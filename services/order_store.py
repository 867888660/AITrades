"""
Order state machine and persistence layer for Polymarket CLOB orders.

State transitions:
    created -> submitted -> open -> partially_filled -> filled
                                 -> cancelled
                         -> failed
    open -> cancel_requested -> cancelled
    open -> expired
    filled -> reconciled
    partially_filled -> filled
    partially_filled -> cancel_requested -> cancelled

The `orders` table lives in the same DB as the legacy `polyMarket_OrderList`.
Legacy table is preserved as-is for historical decision logs.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.config_loader import BASE_DIR, load_web_settings
from services.strategy_audit_store import MODE_REAL, write_action_event, write_run_event


# ---------------------------------------------------------------------------
# Order status enum
# ---------------------------------------------------------------------------

class OrderStatus(str, Enum):
    CREATED = "created"
    SUBMITTED = "submitted"
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    FAILED = "failed"
    EXPIRED = "expired"
    RECONCILED = "reconciled"


# Valid state transitions: current_state -> set of allowed next states
_TRANSITIONS: Dict[OrderStatus, set] = {
    OrderStatus.CREATED: {OrderStatus.SUBMITTED, OrderStatus.FAILED},
    OrderStatus.SUBMITTED: {OrderStatus.OPEN, OrderStatus.FAILED, OrderStatus.CANCELLED},
    OrderStatus.OPEN: {
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCEL_REQUESTED,
        OrderStatus.CANCELLED,
        OrderStatus.EXPIRED,
    },
    OrderStatus.PARTIALLY_FILLED: {
        OrderStatus.FILLED,
        OrderStatus.CANCEL_REQUESTED,
        OrderStatus.CANCELLED,
    },
    OrderStatus.CANCEL_REQUESTED: {OrderStatus.CANCELLED, OrderStatus.FILLED},
    OrderStatus.FILLED: {OrderStatus.RECONCILED},
    OrderStatus.CANCELLED: set(),
    OrderStatus.FAILED: set(),
    OrderStatus.EXPIRED: set(),
    OrderStatus.RECONCILED: set(),
}


def can_transition(current: OrderStatus, target: OrderStatus) -> bool:
    return target in _TRANSITIONS.get(current, set())


def is_terminal(status: OrderStatus) -> bool:
    return not _TRANSITIONS.get(status)


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_DDL_ORDERS = """
CREATE TABLE IF NOT EXISTS orders (
    order_id          TEXT    PRIMARY KEY,
    client_order_id   TEXT    NOT NULL UNIQUE,
    strategy_id       INTEGER,
    leg_uid           TEXT    NOT NULL DEFAULT '',
    leg_index_snapshot INTEGER NOT NULL DEFAULT 0,
    condition_id      TEXT    NOT NULL DEFAULT '',
    token_id          TEXT    NOT NULL,
    side              TEXT    NOT NULL CHECK (side IN ('BUY', 'SELL')),
    direction         TEXT    NOT NULL DEFAULT 'yes' CHECK (direction IN ('yes', 'no')),
    status            TEXT    NOT NULL DEFAULT 'created',
    price             REAL    NOT NULL,
    qty               REAL    NOT NULL,
    filled_qty        REAL    NOT NULL DEFAULT 0,
    avg_fill_price    REAL,
    remaining_qty     REAL    NOT NULL DEFAULT 0,
    remote_order_id   TEXT,
    client_order_tag  TEXT    NOT NULL DEFAULT '',
    post_only         INTEGER NOT NULL DEFAULT 0,
    reduce_only       INTEGER NOT NULL DEFAULT 0,
    liquidity_role    TEXT    NOT NULL DEFAULT '',
    raw_order_json    TEXT    NOT NULL DEFAULT '{}',
    tx_hash           TEXT,
    error_message     TEXT,
    created_at        TEXT    NOT NULL,
    submitted_at      TEXT,
    opened_at         TEXT,
    filled_at         TEXT,
    cancelled_at      TEXT,
    reconciled_at     TEXT,
    updated_at        TEXT    NOT NULL
);
"""

_DDL_ORDER_EVENTS = """
CREATE TABLE IF NOT EXISTS order_events (
    event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    TEXT    NOT NULL,
    from_status TEXT    NOT NULL,
    to_status   TEXT    NOT NULL,
    filled_qty  REAL,
    message     TEXT,
    created_at  TEXT    NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(order_id)
);
"""

_DDL_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_strategy_id ON orders(strategy_id);
CREATE INDEX IF NOT EXISTS idx_orders_token_id ON orders(token_id);
CREATE INDEX IF NOT EXISTS idx_orders_condition_id ON orders(condition_id);
CREATE INDEX IF NOT EXISTS idx_orders_strategy_leg ON orders(strategy_id, leg_uid);
CREATE INDEX IF NOT EXISTS idx_order_events_order_id ON order_events(order_id);
"""


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

_order_schema_ensured: bool = False
_order_db_file_ensured: bool = False


def _order_db_path() -> Path:
    settings = load_web_settings()
    raw = str(settings.get("order_list_db_path", "")).strip()
    if raw:
        p = Path(raw).expanduser()
        if p.is_absolute():
            return p
        return BASE_DIR / p
    return BASE_DIR / "PolyMarketOrderList.db"


def _connect() -> sqlite3.Connection:
    global _order_schema_ensured, _order_db_file_ensured
    path = _order_db_path()
    if not _order_db_file_ensured:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        _order_db_file_ensured = True
    conn = sqlite3.connect(str(path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.row_factory = sqlite3.Row
    if not _order_schema_ensured:
        _ensure_schema(conn)
        _order_schema_ensured = True
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    existing = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    if "orders" not in existing:
        conn.executescript(_DDL_ORDERS)
    else:
        _migrate_order_columns(conn)
    if "order_events" not in existing:
        conn.executescript(_DDL_ORDER_EVENTS)
    conn.executescript(_DDL_INDEXES)
    conn.commit()


def _migrate_order_columns(conn: sqlite3.Connection) -> None:
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(orders)").fetchall()}
    additions = [
        ("leg_uid", "TEXT NOT NULL DEFAULT ''"),
        ("leg_index_snapshot", "INTEGER NOT NULL DEFAULT 0"),
        ("client_order_tag", "TEXT NOT NULL DEFAULT ''"),
        ("post_only", "INTEGER NOT NULL DEFAULT 0"),
        ("reduce_only", "INTEGER NOT NULL DEFAULT 0"),
        ("liquidity_role", "TEXT NOT NULL DEFAULT ''"),
        ("raw_order_json", "TEXT NOT NULL DEFAULT '{}'"),
    ]
    for col, ddl in additions:
        if col not in cols:
            conn.execute(f"ALTER TABLE orders ADD COLUMN {col} {ddl}")


def get_db_path() -> Path:
    return _order_db_path()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _gen_client_id() -> str:
    return uuid.uuid4().hex


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return dict(row)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def create_order(
    *,
    token_id: str,
    side: str,
    price: float,
    qty: float,
    direction: str = "yes",
    condition_id: str = "",
    strategy_id: Optional[int] = None,
    leg_uid: str = "",
    leg_index_snapshot: int = 0,
    client_order_id: Optional[str] = None,
    client_order_tag: str = "",
    post_only: bool = False,
    reduce_only: bool = False,
    liquidity_role: str = "",
    raw_order_json: str = "{}",
    audit_tick_id: Optional[int] = None,
    raw_action_json: Optional[str] = None,
    raw_function_json_hash: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new order in CREATED state. Returns the order dict."""
    now = _now_iso()
    oid = uuid.uuid4().hex
    cid = client_order_id or _gen_client_id()
    conn = _connect()
    try:
        conn.execute(
            """INSERT INTO orders
               (order_id, client_order_id, strategy_id, leg_uid, leg_index_snapshot,
                condition_id, token_id, side, direction, status, price, qty, remaining_qty,
                client_order_tag, post_only, reduce_only, liquidity_role, raw_order_json,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (oid, cid, strategy_id, str(leg_uid or ""), int(leg_index_snapshot or 0),
             condition_id, token_id,
             side.upper(), direction.lower(), OrderStatus.CREATED.value,
             price, qty, qty, str(client_order_tag or ""), 1 if post_only else 0,
             1 if reduce_only else 0, str(liquidity_role or ""), raw_order_json or "{}",
             now, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM orders WHERE order_id = ?", (oid,)).fetchone()
        result = _row_to_dict(row)
    finally:
        conn.close()
    if strategy_id is not None:
        write_action_event(
            int(strategy_id),
            MODE_REAL,
            side,
            tick_id=audit_tick_id,
            side=direction,
            qty=qty,
            price=price,
            status=OrderStatus.CREATED.value,
            reason="real_order_created",
            order_ref=oid,
            raw_action_json=raw_action_json,
            raw_function_json_hash=raw_function_json_hash,
        )
    return result


def transition_order(
    order_id: str,
    new_status: OrderStatus,
    *,
    filled_qty: Optional[float] = None,
    avg_fill_price: Optional[float] = None,
    remote_order_id: Optional[str] = None,
    tx_hash: Optional[str] = None,
    error_message: Optional[str] = None,
    message: Optional[str] = None,
) -> Dict[str, Any]:
    """Transition an order to a new status. Raises ValueError on invalid transition."""
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        if not row:
            raise ValueError(f"Order {order_id} not found")
        current = OrderStatus(row["status"])
        if not can_transition(current, new_status):
            raise ValueError(
                f"Invalid transition: {current.value} -> {new_status.value} "
                f"(allowed: {[s.value for s in _TRANSITIONS.get(current, set())]})"
            )

        now = _now_iso()
        sets: List[str] = ["status = ?", "updated_at = ?"]
        vals: List[Any] = [new_status.value, now]

        if filled_qty is not None:
            new_filled = float(row["filled_qty"]) + filled_qty
            new_remaining = max(0.0, float(row["qty"]) - new_filled)
            sets.extend(["filled_qty = ?", "remaining_qty = ?"])
            vals.extend([new_filled, new_remaining])
        if avg_fill_price is not None:
            sets.append("avg_fill_price = ?"); vals.append(avg_fill_price)
        if remote_order_id is not None:
            sets.append("remote_order_id = ?"); vals.append(remote_order_id)
        if tx_hash is not None:
            sets.append("tx_hash = ?"); vals.append(tx_hash)
        if error_message is not None:
            sets.append("error_message = ?"); vals.append(error_message)

        # Timestamp columns per status
        ts_map = {
            OrderStatus.SUBMITTED: "submitted_at",
            OrderStatus.OPEN: "opened_at",
            OrderStatus.FILLED: "filled_at",
            OrderStatus.CANCELLED: "cancelled_at",
            OrderStatus.RECONCILED: "reconciled_at",
        }
        ts_col = ts_map.get(new_status)
        if ts_col:
            sets.append(f"{ts_col} = ?"); vals.append(now)

        vals.append(order_id)
        conn.execute(f"UPDATE orders SET {', '.join(sets)} WHERE order_id = ?", vals)

        # Record event
        conn.execute(
            """INSERT INTO order_events (order_id, from_status, to_status, filled_qty, message, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (order_id, current.value, new_status.value, filled_qty, message, now),
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        result = _row_to_dict(updated)
    finally:
        conn.close()
    strategy_id = result.get("strategy_id")
    if strategy_id is not None:
        write_run_event(
            int(strategy_id),
            MODE_REAL,
            "order_update",
            (
                f"order {order_id} {current.value}->{new_status.value}"
                + (f" filled_qty={filled_qty}" if filled_qty is not None else "")
                + (f" message={message}" if message else "")
                + (f" error={error_message}" if error_message else "")
            ),
            aggregate=False,
        )
        write_action_event(
            int(strategy_id),
            MODE_REAL,
            str(result.get("side") or ""),
            side=str(result.get("direction") or ""),
            qty=result.get("qty"),
            price=result.get("avg_fill_price") or result.get("price"),
            status=new_status.value,
            reason=message or error_message,
            order_ref=order_id,
        )
    return result


def get_order(order_id: str) -> Optional[Dict[str, Any]]:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def get_order_by_client_id(client_order_id: str) -> Optional[Dict[str, Any]]:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM orders WHERE client_order_id = ?", (client_order_id,)
        ).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def list_orders(
    *,
    strategy_id: Optional[int] = None,
    token_id: Optional[str] = None,
    status: Optional[OrderStatus] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    conn = _connect()
    try:
        clauses: List[str] = []
        vals: List[Any] = []
        if strategy_id is not None:
            clauses.append("strategy_id = ?"); vals.append(strategy_id)
        if token_id is not None:
            clauses.append("token_id = ?"); vals.append(token_id)
        if status is not None:
            clauses.append("status = ?"); vals.append(status.value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        vals.append(limit)
        rows = conn.execute(
            f"SELECT * FROM orders {where} ORDER BY created_at DESC LIMIT ?", vals
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def list_open_orders(strategy_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Return orders in non-terminal states."""
    conn = _connect()
    try:
        active = [s.value for s in OrderStatus if not is_terminal(s)]
        placeholders = ",".join("?" * len(active))
        vals: List[Any] = list(active)
        clause = f"status IN ({placeholders})"
        if strategy_id is not None:
            clause += " AND strategy_id = ?"
            vals.append(strategy_id)
        rows = conn.execute(
            f"SELECT * FROM orders WHERE {clause} ORDER BY created_at DESC", vals
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_order_events(order_id: str) -> List[Dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM order_events WHERE order_id = ? ORDER BY event_id", (order_id,)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Aggregation for profit engine compatibility
# ---------------------------------------------------------------------------

def aggregate_filled_by_token() -> Dict[str, Dict[str, float]]:
    """Aggregate filled orders by token_id for position estimation.
    Returns {token_id: {buy_qty, sell_qty, buy_cost, sell_cost, trades}}
    Only considers orders in filled/reconciled/partially_filled states.
    """
    conn = _connect()
    try:
        rows = conn.execute(
            """SELECT token_id, side, SUM(filled_qty) as total_qty,
                      SUM(filled_qty * COALESCE(avg_fill_price, price)) as total_cost,
                      COUNT(*) as cnt
               FROM orders
               WHERE status IN ('filled', 'reconciled', 'partially_filled')
               GROUP BY token_id, side"""
        ).fetchall()
        result: Dict[str, Dict[str, float]] = {}
        for r in rows:
            token = r["token_id"]
            bucket = result.setdefault(token, {
                "buy_qty": 0.0, "sell_qty": 0.0,
                "buy_cost": 0.0, "sell_cost": 0.0, "trades": 0,
            })
            if r["side"] == "BUY":
                bucket["buy_qty"] += float(r["total_qty"] or 0)
                bucket["buy_cost"] += float(r["total_cost"] or 0)
            else:
                bucket["sell_qty"] += float(r["total_qty"] or 0)
                bucket["sell_cost"] += float(r["total_cost"] or 0)
            bucket["trades"] += int(r["cnt"] or 0)
        return result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Migration: import legacy polyMarket_OrderList rows into new orders table
# ---------------------------------------------------------------------------

def migrate_legacy_orders() -> Dict[str, Any]:
    """One-time migration: read legacy table, insert into orders as 'filled' or 'failed'.
    Skips rows already migrated (by checking client_order_id prefix 'legacy_').
    Returns stats dict.
    """
    conn = _connect()
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if "polyMarket_OrderList" not in tables:
            return {"migrated": 0, "skipped": 0, "error": None}

        cols = [r[1] for r in conn.execute('PRAGMA table_info("polyMarket_OrderList")').fetchall()]
        rows = conn.execute('SELECT * FROM "polyMarket_OrderList"').fetchall()

        migrated = 0
        skipped = 0
        now = _now_iso()

        for raw in rows:
            item = {cols[idx]: raw[idx] for idx in range(len(cols))}
            token = str(item.get("Token") or "").strip()
            if not token:
                skipped += 1
                continue

            # Derive a stable client_order_id from legacy data
            nowtime = str(item.get("nowtime") or "").strip()
            legacy_id = f"legacy_{token[:16]}_{nowtime}"

            # Check if already migrated
            existing = conn.execute(
                "SELECT 1 FROM orders WHERE client_order_id = ?", (legacy_id,)
            ).fetchone()
            if existing:
                skipped += 1
                continue

            is_success = str(item.get("IsSuccess") or "").strip().lower()
            has_tx = bool(str(item.get("tx_hash_hint") or "").strip())
            if is_success in ("true", "1", "yes", "ok", "filled", "success") or has_tx:
                status = OrderStatus.FILLED.value
            else:
                status = OrderStatus.FAILED.value

            side_raw = str(item.get("BUY/SELL") or "").strip().upper()
            side = "SELL" if side_raw == "SELL" else "BUY"

            price = 0.0
            try:
                price = float(item.get("Buy_Price") or 0)
            except (TypeError, ValueError):
                pass

            qty = 0.0
            try:
                qty = float(item.get("Qty") or 0)
            except (TypeError, ValueError):
                pass

            name_field = str(item.get("name") or "").strip().lower()
            direction = "no" if name_field == "no" else "yes"

            oid = uuid.uuid4().hex
            filled_qty = qty if status == OrderStatus.FILLED.value else 0.0

            conn.execute(
                """INSERT INTO orders
                   (order_id, client_order_id, strategy_id, condition_id, token_id,
                    side, direction, status, price, qty, filled_qty, remaining_qty,
                    avg_fill_price, tx_hash, created_at, updated_at, filled_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (oid, legacy_id, None, "", token,
                 side, direction, status, price, qty, filled_qty,
                 0.0 if status == OrderStatus.FILLED.value else qty,
                 price if filled_qty > 0 else None,
                 str(item.get("tx_hash_hint") or "").strip() or None,
                 nowtime or now, now,
                 nowtime if status == OrderStatus.FILLED.value else None),
            )
            migrated += 1

        conn.commit()
        return {"migrated": migrated, "skipped": skipped, "error": None}
    except Exception as exc:
        return {"migrated": 0, "skipped": 0, "error": str(exc)}
    finally:
        conn.close()
