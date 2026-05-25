"""
Unified read/write layer for strategy data.

All consumers (polymarket_service, profit_engine, main/WS, chart_service, etc.)
should use this module instead of directly querying old monitoring tables.

Data lives in strategy_registry + strategy_legs (same DB file as before).
Position fields (qty, avg_cost, current_price, pnl) are stored on strategy_legs.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.config_loader import BASE_DIR, load_web_settings

# ---------------------------------------------------------------------------
# Strategy state namespaces
# ---------------------------------------------------------------------------

RUNTIME_STATE_NAMESPACE = "runtime"
USER_STATE_NAMESPACE = "user"
LEGACY_STATE_NAMESPACE = "default"

# ---------------------------------------------------------------------------
# Position columns to add via ALTER (idempotent)
# ---------------------------------------------------------------------------

_POSITION_COLUMNS = [
    ("yes_qty", "REAL NOT NULL DEFAULT 0"),
    ("no_qty", "REAL NOT NULL DEFAULT 0"),
    ("yes_avg_cost", "REAL"),
    ("no_avg_cost", "REAL"),
    ("yes_current_price", "REAL"),
    ("no_current_price", "REAL"),
    ("unrealized_pnl", "REAL NOT NULL DEFAULT 0"),
    ("position_source", "TEXT NOT NULL DEFAULT ''"),
    ("position_updated_at", "TEXT"),
]

_INSTRUMENT_COLUMNS = [
    ("asset_class", "TEXT NOT NULL DEFAULT 'polymarket_binary'"),
    ("venue", "TEXT NOT NULL DEFAULT 'polymarket'"),
    ("symbol", "TEXT NOT NULL DEFAULT ''"),
    ("instrument_id", "TEXT NOT NULL DEFAULT ''"),
    ("instrument_json", "TEXT NOT NULL DEFAULT '{}'"),
]

# ---------------------------------------------------------------------------
# DDL (used for fresh DB init)
# ---------------------------------------------------------------------------

_DDL_REGISTRY = """
CREATE TABLE IF NOT EXISTS strategy_registry (
    strategy_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_uid      TEXT    NOT NULL UNIQUE,
    strategy_name     TEXT    NOT NULL,
    strategy_code     TEXT    NOT NULL DEFAULT '',
    state             TEXT    NOT NULL DEFAULT 'Stop'
                      CHECK (state IN ('Stop', 'Virtual', 'Real')),
    initial_capital   REAL    NOT NULL DEFAULT 0,
    strategy_bankroll REAL    NOT NULL DEFAULT 0,
    profit_roll_ratio REAL    NOT NULL DEFAULT 0,
    realized_profit   REAL    NOT NULL DEFAULT 0,
    input_json        TEXT    NOT NULL DEFAULT '{}',
    created_at_utc    TEXT    NOT NULL,
    updated_at_utc    TEXT    NOT NULL
);
"""

_DDL_LEGS = """
CREATE TABLE IF NOT EXISTS strategy_legs (
    leg_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id       INTEGER NOT NULL,
    leg_uid           TEXT    NOT NULL DEFAULT '',
    leg_index         INTEGER NOT NULL DEFAULT 0,
    condition_id      TEXT    NOT NULL DEFAULT '',
    yes_token         TEXT,
    no_token          TEXT,
    asset_class       TEXT    NOT NULL DEFAULT 'polymarket_binary',
    venue             TEXT    NOT NULL DEFAULT 'polymarket',
    symbol            TEXT    NOT NULL DEFAULT '',
    instrument_id     TEXT    NOT NULL DEFAULT '',
    instrument_json   TEXT    NOT NULL DEFAULT '{}',
    budget_cap        REAL    NOT NULL DEFAULT 0,
    params_json       TEXT    NOT NULL DEFAULT '{}',
    yes_qty           REAL    NOT NULL DEFAULT 0,
    no_qty            REAL    NOT NULL DEFAULT 0,
    yes_avg_cost      REAL,
    no_avg_cost       REAL,
    yes_current_price REAL,
    no_current_price  REAL,
    unrealized_pnl    REAL    NOT NULL DEFAULT 0,
    position_source   TEXT    NOT NULL DEFAULT '',
    position_updated_at TEXT,
    created_at_utc    TEXT    NOT NULL,
    updated_at_utc    TEXT    NOT NULL,
    FOREIGN KEY (strategy_id) REFERENCES strategy_registry(strategy_id) ON DELETE CASCADE,
    UNIQUE(strategy_id, leg_index)
);
"""

_DDL_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_strategy_registry_state ON strategy_registry(state);
CREATE INDEX IF NOT EXISTS idx_strategy_legs_strategy_id ON strategy_legs(strategy_id);
CREATE INDEX IF NOT EXISTS idx_strategy_legs_uid ON strategy_legs(strategy_id, leg_uid);
CREATE INDEX IF NOT EXISTS idx_strategy_legs_condition_id ON strategy_legs(condition_id);
CREATE INDEX IF NOT EXISTS idx_strategy_legs_yes_token ON strategy_legs(yes_token);
CREATE INDEX IF NOT EXISTS idx_strategy_legs_no_token ON strategy_legs(no_token);
CREATE INDEX IF NOT EXISTS idx_strategy_legs_instrument_id ON strategy_legs(instrument_id);
CREATE INDEX IF NOT EXISTS idx_strategy_legs_asset_class ON strategy_legs(asset_class);
"""

_DDL_STATE = """
CREATE TABLE IF NOT EXISTS strategy_state (
    strategy_id       INTEGER NOT NULL,
    namespace         TEXT    NOT NULL DEFAULT 'default',
    key               TEXT    NOT NULL,
    value_json        TEXT    NOT NULL DEFAULT 'null',
    updated_at_utc    TEXT    NOT NULL,
    PRIMARY KEY(strategy_id, namespace, key),
    FOREIGN KEY (strategy_id) REFERENCES strategy_registry(strategy_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_strategy_state_strategy
ON strategy_state(strategy_id, namespace);

CREATE TABLE IF NOT EXISTS strategy_state_audit (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id      INTEGER NOT NULL,
    namespace        TEXT    NOT NULL DEFAULT 'runtime',
    key              TEXT    NOT NULL DEFAULT '',
    old_value_json   TEXT,
    new_value_json   TEXT,
    action           TEXT    NOT NULL DEFAULT 'UPSERT',
    actor            TEXT    NOT NULL DEFAULT 'system',
    reason           TEXT    NOT NULL DEFAULT '',
    updated_at_utc   TEXT    NOT NULL,
    FOREIGN KEY (strategy_id) REFERENCES strategy_registry(strategy_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_strategy_state_audit_strategy
ON strategy_state_audit(strategy_id, updated_at_utc DESC);
"""

_DDL_VIRTUAL = """
CREATE TABLE IF NOT EXISTS strategy_virtual_account (
    strategy_id      INTEGER PRIMARY KEY,
    initial_cash     REAL    NOT NULL DEFAULT 0,
    cash             REAL    NOT NULL DEFAULT 0,
    equity           REAL    NOT NULL DEFAULT 0,
    realized_pnl     REAL    NOT NULL DEFAULT 0,
    unrealized_pnl   REAL    NOT NULL DEFAULT 0,
    total_fees_paid  REAL    NOT NULL DEFAULT 0,
    updated_at_utc   TEXT    NOT NULL,
    FOREIGN KEY (strategy_id) REFERENCES strategy_registry(strategy_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS strategy_virtual_positions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id     INTEGER NOT NULL,
    leg_index       INTEGER NOT NULL DEFAULT 0,
    side            TEXT    NOT NULL CHECK(side IN ('YES', 'NO')),
    qty             REAL    NOT NULL DEFAULT 0,
    avg_price       REAL    NOT NULL DEFAULT 0,
    cost            REAL    NOT NULL DEFAULT 0,
    realized_pnl    REAL    NOT NULL DEFAULT 0,
    updated_at_utc  TEXT    NOT NULL,
    UNIQUE(strategy_id, leg_index, side),
    FOREIGN KEY (strategy_id) REFERENCES strategy_registry(strategy_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS strategy_virtual_orders (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id          INTEGER NOT NULL,
    leg_index            INTEGER NOT NULL DEFAULT 0,
    side                 TEXT    NOT NULL CHECK(side IN ('YES', 'NO')),
    action               TEXT    NOT NULL CHECK(action IN ('BUY', 'SELL')),
    qty                  REAL    NOT NULL DEFAULT 0,
    price                REAL    NOT NULL DEFAULT 0,
    gross_notional       REAL    NOT NULL DEFAULT 0,
    fee_rate             REAL    NOT NULL DEFAULT 0,
    fee                  REAL    NOT NULL DEFAULT 0,
    net_cash_change      REAL    NOT NULL DEFAULT 0,
    liquidity_role       TEXT    NOT NULL DEFAULT 'taker',
    status               TEXT    NOT NULL CHECK(status IN ('filled', 'blocked', 'failed')),
    reason               TEXT,
    created_at_utc       TEXT    NOT NULL,
    FOREIGN KEY (strategy_id) REFERENCES strategy_registry(strategy_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS strategy_virtual_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id     INTEGER NOT NULL,
    tick_id         INTEGER,
    mode            TEXT    NOT NULL DEFAULT 'virtual' CHECK(mode IN ('virtual', 'real')),
    event_type      TEXT    NOT NULL CHECK(event_type IN ('action', 'print', 'error', 'settle')),
    content         TEXT    NOT NULL DEFAULT '',
    content_hash    TEXT    NOT NULL DEFAULT '',
    repeat_count    INTEGER NOT NULL DEFAULT 1,
    last_seen_utc   TEXT    NOT NULL,
    created_at_utc  TEXT    NOT NULL,
    FOREIGN KEY (strategy_id) REFERENCES strategy_registry(strategy_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS strategy_virtual_ticks (
    tick_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id     INTEGER NOT NULL,
    run_at_utc      TEXT    NOT NULL,
    duration_ms     REAL    NOT NULL DEFAULT 0,
    function_json   TEXT,
    mode_output     TEXT,
    error           TEXT,
    orders_placed   INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (strategy_id) REFERENCES strategy_registry(strategy_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_virtual_positions_strategy ON strategy_virtual_positions(strategy_id);
CREATE INDEX IF NOT EXISTS idx_virtual_orders_strategy ON strategy_virtual_orders(strategy_id, created_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_virtual_events_strategy ON strategy_virtual_events(strategy_id, created_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_virtual_events_strategy_seen ON strategy_virtual_events(strategy_id, last_seen_utc DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_virtual_events_strategy_type_seen ON strategy_virtual_events(strategy_id, event_type, last_seen_utc DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_virtual_ticks_strategy ON strategy_virtual_ticks(strategy_id, run_at_utc DESC);

CREATE TABLE IF NOT EXISTS strategy_virtual_open_orders (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id          INTEGER NOT NULL,
    leg_uid              TEXT    NOT NULL DEFAULT '',
    leg_index            INTEGER NOT NULL DEFAULT 0,
    condition_id         TEXT    NOT NULL DEFAULT '',
    token_id             TEXT    NOT NULL DEFAULT '',
    side                 TEXT    NOT NULL CHECK(side IN ('YES', 'NO')),
    action               TEXT    NOT NULL CHECK(action IN ('BUY', 'SELL')),
    qty                  REAL    NOT NULL DEFAULT 0,
    filled_qty           REAL    NOT NULL DEFAULT 0,
    remaining_qty        REAL    NOT NULL DEFAULT 0,
    price                REAL    NOT NULL DEFAULT 0,
    order_type           TEXT    NOT NULL DEFAULT 'GTC',
    post_only            INTEGER NOT NULL DEFAULT 0,
    reduce_only          INTEGER NOT NULL DEFAULT 0,
    liquidity_role       TEXT    NOT NULL DEFAULT 'maker',
    client_order_tag     TEXT    NOT NULL DEFAULT '',
    status               TEXT    NOT NULL DEFAULT 'open',
    reason               TEXT,
    raw_action_json      TEXT    NOT NULL DEFAULT '{}',
    created_at_utc       TEXT    NOT NULL,
    updated_at_utc       TEXT    NOT NULL,
    FOREIGN KEY (strategy_id) REFERENCES strategy_registry(strategy_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_virtual_open_orders_strategy
ON strategy_virtual_open_orders(strategy_id, status, updated_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_virtual_open_orders_tag
ON strategy_virtual_open_orders(strategy_id, leg_uid, side, client_order_tag, status);

CREATE TABLE IF NOT EXISTS strategy_virtual_positions_v2 (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id     INTEGER NOT NULL,
    instrument_id   TEXT    NOT NULL,
    asset_class     TEXT    NOT NULL DEFAULT '',
    side            TEXT    NOT NULL DEFAULT 'LONG',
    qty             REAL    NOT NULL DEFAULT 0,
    avg_price       REAL    NOT NULL DEFAULT 0,
    cost            REAL    NOT NULL DEFAULT 0,
    market_value    REAL    NOT NULL DEFAULT 0,
    realized_pnl    REAL    NOT NULL DEFAULT 0,
    unrealized_pnl  REAL    NOT NULL DEFAULT 0,
    updated_at_utc  TEXT    NOT NULL,
    UNIQUE(strategy_id, instrument_id, side),
    FOREIGN KEY (strategy_id) REFERENCES strategy_registry(strategy_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS strategy_virtual_orders_v2 (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id     INTEGER NOT NULL,
    instrument_id   TEXT    NOT NULL,
    asset_class     TEXT    NOT NULL DEFAULT '',
    action          TEXT    NOT NULL DEFAULT '',
    side            TEXT    NOT NULL DEFAULT '',
    qty             REAL    NOT NULL DEFAULT 0,
    notional        REAL    NOT NULL DEFAULT 0,
    price           REAL    NOT NULL DEFAULT 0,
    fee             REAL    NOT NULL DEFAULT 0,
    status          TEXT    NOT NULL DEFAULT '',
    reason          TEXT,
    raw_action_json TEXT    NOT NULL DEFAULT '{}',
    created_at_utc  TEXT    NOT NULL,
    FOREIGN KEY (strategy_id) REFERENCES strategy_registry(strategy_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS strategy_cash_ledger (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id     INTEGER NOT NULL,
    currency        TEXT    NOT NULL DEFAULT 'USD',
    amount_delta    REAL    NOT NULL DEFAULT 0,
    reason          TEXT    NOT NULL DEFAULT '',
    ref_table       TEXT,
    ref_id          INTEGER,
    created_at_utc  TEXT    NOT NULL,
    FOREIGN KEY (strategy_id) REFERENCES strategy_registry(strategy_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_virtual_positions_v2_strategy
ON strategy_virtual_positions_v2(strategy_id, instrument_id);
CREATE INDEX IF NOT EXISTS idx_virtual_orders_v2_strategy
ON strategy_virtual_orders_v2(strategy_id, created_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_strategy_cash_ledger_strategy
ON strategy_cash_ledger(strategy_id, created_at_utc DESC);

CREATE TABLE IF NOT EXISTS strategy_virtual_run_locks (
    strategy_id     INTEGER PRIMARY KEY,
    owner           TEXT    NOT NULL DEFAULT '',
    acquired_at_utc TEXT    NOT NULL,
    expires_at_utc  TEXT    NOT NULL,
    FOREIGN KEY (strategy_id) REFERENCES strategy_registry(strategy_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS strategy_order_intents (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id          INTEGER NOT NULL,
    leg_uid              TEXT    NOT NULL DEFAULT '',
    leg_index            INTEGER NOT NULL DEFAULT 0,
    leg_index_snapshot   INTEGER NOT NULL DEFAULT 0,
    condition_id         TEXT    NOT NULL DEFAULT '',
    token_id             TEXT    NOT NULL DEFAULT '',
    outcome              TEXT    NOT NULL DEFAULT '',
    side                 TEXT    NOT NULL DEFAULT '',
    price                REAL    NOT NULL DEFAULT 0,
    qty                  REAL    NOT NULL DEFAULT 0,
    order_type           TEXT    NOT NULL DEFAULT '',
    post_only            INTEGER NOT NULL DEFAULT 0,
    reduce_only          INTEGER NOT NULL DEFAULT 0,
    client_order_tag     TEXT    NOT NULL DEFAULT '',
    replace_policy       TEXT    NOT NULL DEFAULT '',
    reason               TEXT,
    raw_action_json      TEXT    NOT NULL DEFAULT '{}',
    status               TEXT    NOT NULL DEFAULT '',
    created_at_utc       TEXT    NOT NULL,
    updated_at_utc       TEXT    NOT NULL,
    FOREIGN KEY (strategy_id) REFERENCES strategy_registry(strategy_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS strategy_real_positions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id          INTEGER NOT NULL,
    leg_uid              TEXT    NOT NULL DEFAULT '',
    leg_index_snapshot   INTEGER NOT NULL DEFAULT 0,
    condition_id         TEXT    NOT NULL DEFAULT '',
    token_id             TEXT    NOT NULL DEFAULT '',
    outcome              TEXT    NOT NULL DEFAULT '',
    qty                  REAL    NOT NULL DEFAULT 0,
    avg_price            REAL    NOT NULL DEFAULT 0,
    cost                 REAL    NOT NULL DEFAULT 0,
    realized_pnl         REAL    NOT NULL DEFAULT 0,
    source               TEXT    NOT NULL DEFAULT 'system_order',
    updated_at_utc       TEXT    NOT NULL,
    UNIQUE(strategy_id, leg_uid, token_id, outcome),
    FOREIGN KEY (strategy_id) REFERENCES strategy_registry(strategy_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS unassigned_positions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet_address       TEXT    NOT NULL DEFAULT '',
    condition_id         TEXT    NOT NULL DEFAULT '',
    token_id             TEXT    NOT NULL DEFAULT '',
    outcome              TEXT    NOT NULL DEFAULT '',
    qty                  REAL    NOT NULL DEFAULT 0,
    avg_price            REAL    NOT NULL DEFAULT 0,
    source               TEXT    NOT NULL DEFAULT '',
    reason               TEXT    NOT NULL DEFAULT '',
    updated_at_utc       TEXT    NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

_schema_ensured: bool = False
_db_file_ensured: bool = False


def _db_path() -> Path:
    settings = load_web_settings()
    raw = str(settings.get("strategy_monitoring_db_path", "")).strip()
    if raw:
        p = Path(raw).expanduser()
        if p.is_absolute():
            return p
        return BASE_DIR / p
    return BASE_DIR / "Data" / "PolyMarketMonitoring.db"


def _ensure_db_file(path: Path) -> None:
    global _db_file_ensured
    if _db_file_ensured:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    _db_file_ensured = True


def connect(*, readonly: bool = False) -> sqlite3.Connection:
    """Return a connection to the strategy DB with tables ensured."""
    global _schema_ensured
    path = _db_path()
    _ensure_db_file(path)
    if readonly:
        uri = f"file:{path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    else:
        conn = sqlite3.connect(str(path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.row_factory = sqlite3.Row
    if not readonly and not _schema_ensured:
        _ensure_schema(conn)
        _schema_ensured = True
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create tables if missing, add position columns if missing."""
    existing = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    if "strategy_registry" not in existing:
        conn.executescript(_DDL_REGISTRY)
    if "strategy_legs" not in existing:
        conn.executescript(_DDL_LEGS)
    else:
        _migrate_leg_columns(conn)
    conn.executescript(_DDL_INDEXES)
    conn.executescript(_DDL_STATE)
    _migrate_legacy_strategy_state(conn)
    # virtual tables are always idempotent
    conn.executescript(_DDL_VIRTUAL)
    _migrate_virtual_events_columns(conn)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_virtual_events_strategy_seen "
        "ON strategy_virtual_events(strategy_id, last_seen_utc DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_virtual_events_strategy_type_seen "
        "ON strategy_virtual_events(strategy_id, event_type, last_seen_utc DESC, id DESC)"
    )
    conn.commit()


def _migrate_virtual_events_columns(conn: sqlite3.Connection) -> None:
    """Idempotent ALTER to add new columns to existing strategy_virtual_events."""
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(strategy_virtual_events)").fetchall()}
    if "mode" not in cols:
        conn.execute("ALTER TABLE strategy_virtual_events ADD COLUMN mode TEXT NOT NULL DEFAULT 'virtual'")
    if "repeat_count" not in cols:
        conn.execute("ALTER TABLE strategy_virtual_events ADD COLUMN repeat_count INTEGER NOT NULL DEFAULT 1")
    if "last_seen_utc" not in cols:
        conn.execute("ALTER TABLE strategy_virtual_events ADD COLUMN last_seen_utc TEXT NOT NULL DEFAULT ''")


def _migrate_leg_columns(conn: sqlite3.Connection) -> None:
    """Idempotent ALTER to add instrument and position columns to existing strategy_legs."""
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(strategy_legs)").fetchall()}
    if "leg_uid" not in cols:
        conn.execute("ALTER TABLE strategy_legs ADD COLUMN leg_uid TEXT NOT NULL DEFAULT ''")
        cols.add("leg_uid")
    for col_name, col_def in [*_INSTRUMENT_COLUMNS, *_POSITION_COLUMNS]:
        if col_name not in cols:
            conn.execute(f"ALTER TABLE strategy_legs ADD COLUMN {col_name} {col_def}")
    _backfill_leg_uids(conn)


def _backfill_leg_uids(conn: sqlite3.Connection) -> None:
    """Fill missing immutable leg ids for older rows."""
    ts_rows = conn.execute(
        "SELECT leg_id, strategy_id, leg_index FROM strategy_legs WHERE COALESCE(leg_uid, '') = ''"
    ).fetchall()
    for row in ts_rows:
        leg_id = int(row["leg_id"])
        strategy_id = int(row["strategy_id"])
        leg_index = int(row["leg_index"] or 0)
        leg_uid = f"leg_{strategy_id}_{leg_id or leg_index}_{uuid.uuid4().hex[:8]}"
        conn.execute("UPDATE strategy_legs SET leg_uid = ? WHERE leg_id = ?", (leg_uid, leg_id))


def _migrate_legacy_strategy_state(conn: sqlite3.Connection) -> None:
    """Move pre-namespace runtime state from `default` to `runtime`."""
    conn.execute(
        """INSERT OR IGNORE INTO strategy_state(strategy_id, namespace, key, value_json, updated_at_utc)
           SELECT strategy_id, ?, key, value_json, updated_at_utc
           FROM strategy_state
           WHERE namespace = ?""",
        (RUNTIME_STATE_NAMESPACE, LEGACY_STATE_NAMESPACE),
    )
    conn.execute(
        "DELETE FROM strategy_state WHERE namespace = ?",
        (LEGACY_STATE_NAMESPACE,),
    )


def db_path() -> Path:
    """Public accessor for the resolved DB path."""
    return _db_path()


# ---------------------------------------------------------------------------
# Read: list / get strategies
# ---------------------------------------------------------------------------


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or str(v).strip() == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return dict(row)


def _safe_json_dict(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def derive_instrument_id(leg: Dict[str, Any]) -> str:
    """Return a stable instrument id for a strategy leg.

    Polymarket legs keep their old token fields, while non-Polymarket legs can
    identify themselves by asset_class + venue + symbol.
    """
    explicit = str(leg.get("instrument_id") or "").strip()
    if explicit:
        return explicit
    asset_class = str(leg.get("asset_class") or "polymarket_binary").strip() or "polymarket_binary"
    venue = str(leg.get("venue") or "").strip()
    symbol = str(leg.get("symbol") or "").strip().upper()
    if asset_class == "polymarket_binary":
        condition_id = str(leg.get("condition_id") or "").strip()
        if condition_id:
            return f"poly:condition:{condition_id}"
        yes_token = str(leg.get("yes_token") or "").strip()
        no_token = str(leg.get("no_token") or "").strip()
        if yes_token or no_token:
            return f"poly:tokens:{yes_token}:{no_token}"
    if asset_class == "crypto_spot" and symbol:
        return f"crypto:{venue or 'unknown'}:{symbol}"
    if asset_class == "equity" and symbol:
        return f"equity:{venue or 'US'}:{symbol}"
    if asset_class == "equity_option" and symbol:
        return f"option:{venue or 'US'}:{symbol}"
    leg_index = leg.get("leg_index", 0)
    return f"{asset_class}:{venue or 'local'}:{symbol or ('leg' + str(leg_index))}"


def normalize_leg_instrument(leg: Dict[str, Any]) -> Dict[str, Any]:
    """Return leg with generic instrument fields populated for callers."""
    result = dict(leg)
    result["leg_uid"] = str(result.get("leg_uid") or "").strip()
    result["asset_class"] = str(result.get("asset_class") or "polymarket_binary").strip() or "polymarket_binary"
    result["venue"] = str(result.get("venue") or ("polymarket" if result["asset_class"] == "polymarket_binary" else "")).strip()
    result["symbol"] = str(result.get("symbol") or "").strip()
    result["instrument_json"] = _safe_json_dict(result.get("instrument_json"))
    if not result.get("instrument_id"):
        result["instrument_id"] = derive_instrument_id(result)
    return result


def list_strategies(*, state_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return all strategies with their legs (primary leg = leg_index 0)."""
    conn = connect(readonly=True)
    try:
        if state_filter:
            strats = [_row_to_dict(r) for r in conn.execute(
                "SELECT * FROM strategy_registry WHERE state = ? ORDER BY strategy_id",
                (state_filter,),
            ).fetchall()]
        else:
            strats = [_row_to_dict(r) for r in conn.execute(
                "SELECT * FROM strategy_registry ORDER BY strategy_id"
            ).fetchall()]
        all_legs = [normalize_leg_instrument(_row_to_dict(r)) for r in conn.execute(
            "SELECT * FROM strategy_legs ORDER BY strategy_id, leg_index"
        ).fetchall()]
    finally:
        conn.close()
    legs_by_sid: Dict[int, List[Dict[str, Any]]] = {}
    for lg in all_legs:
        legs_by_sid.setdefault(lg["strategy_id"], []).append(lg)
    results = []
    for s in strats:
        sid = s["strategy_id"]
        legs = legs_by_sid.get(sid, [])
        s["legs"] = legs
        results.append(s)
    return results


def get_strategy(strategy_id: int) -> Optional[Dict[str, Any]]:
    """Return a single strategy with all legs, or None."""
    conn = connect(readonly=True)
    try:
        row = conn.execute(
            "SELECT * FROM strategy_registry WHERE strategy_id = ?", (strategy_id,)
        ).fetchone()
        if not row:
            return None
        result = _row_to_dict(row)
        legs = [normalize_leg_instrument(_row_to_dict(r)) for r in conn.execute(
            "SELECT * FROM strategy_legs WHERE strategy_id = ? ORDER BY leg_index",
            (strategy_id,),
        ).fetchall()]
        result["legs"] = legs
        return result
    finally:
        conn.close()


def get_strategy_primary_leg(strategy_id: int) -> Optional[Dict[str, Any]]:
    """Return leg_index=0 for a strategy, or None."""
    conn = connect(readonly=True)
    try:
        row = conn.execute(
            "SELECT * FROM strategy_legs WHERE strategy_id = ? AND leg_index = 0",
            (strategy_id,),
        ).fetchone()
        return normalize_leg_instrument(_row_to_dict(row)) if row else None
    finally:
        conn.close()


def get_all_tokens() -> List[Dict[str, Any]]:
    """Return token info for all active strategy legs.
    Used by WS subscription logic to know which tokens to monitor."""
    conn = connect(readonly=True)
    try:
        rows = conn.execute(
            """SELECT l.strategy_id, l.leg_uid, l.leg_index, l.yes_token, l.no_token, l.condition_id,
                      l.asset_class, l.venue, l.symbol, l.instrument_id, l.instrument_json,
                      r.state, r.strategy_name
               FROM strategy_legs l
               JOIN strategy_registry r ON r.strategy_id = l.strategy_id
               WHERE r.state IN ('Virtual', 'Real')
               ORDER BY l.strategy_id, l.leg_index"""
        ).fetchall()
        return [normalize_leg_instrument(_row_to_dict(r)) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Write: update position data on legs
# ---------------------------------------------------------------------------


def update_leg_positions(
    strategy_id: int,
    leg_index: int,
    *,
    yes_qty: Optional[float] = None,
    no_qty: Optional[float] = None,
    yes_avg_cost: Optional[float] = None,
    no_avg_cost: Optional[float] = None,
    yes_current_price: Optional[float] = None,
    no_current_price: Optional[float] = None,
    unrealized_pnl: Optional[float] = None,
    position_source: Optional[str] = None,
) -> bool:
    """Update position fields on a specific leg. Returns True if row was found."""
    sets: List[str] = []
    vals: List[Any] = []
    if yes_qty is not None:
        sets.append("yes_qty = ?"); vals.append(yes_qty)
    if no_qty is not None:
        sets.append("no_qty = ?"); vals.append(no_qty)
    if yes_avg_cost is not None:
        sets.append("yes_avg_cost = ?"); vals.append(yes_avg_cost)
    if no_avg_cost is not None:
        sets.append("no_avg_cost = ?"); vals.append(no_avg_cost)
    if yes_current_price is not None:
        sets.append("yes_current_price = ?"); vals.append(yes_current_price)
    if no_current_price is not None:
        sets.append("no_current_price = ?"); vals.append(no_current_price)
    if unrealized_pnl is not None:
        sets.append("unrealized_pnl = ?"); vals.append(unrealized_pnl)
    if position_source is not None:
        sets.append("position_source = ?"); vals.append(position_source)
    if not sets:
        return False
    ts = datetime.now(timezone.utc).isoformat()
    sets.append("position_updated_at = ?")
    vals.append(ts)
    vals.extend([strategy_id, leg_index])
    conn = connect()
    try:
        affected = conn.execute(
            f"UPDATE strategy_legs SET {', '.join(sets)} WHERE strategy_id = ? AND leg_index = ?",
            vals,
        ).rowcount
        conn.commit()
        return affected > 0
    finally:
        conn.close()


def batch_update_positions(updates: List[Dict[str, Any]]) -> int:
    """Batch update positions for multiple legs.
    Each dict: {strategy_id, leg_index, yes_qty, no_qty, yes_avg_cost, ...}
    Returns count of rows updated.
    """
    if not updates:
        return 0
    conn = connect()
    ts = datetime.now(timezone.utc).isoformat()
    count = 0
    try:
        for u in updates:
            sid = u.get("strategy_id")
            li = u.get("leg_index", 0)
            sets: List[str] = []
            vals: List[Any] = []
            for field in ("yes_qty", "no_qty", "yes_avg_cost", "no_avg_cost",
                          "yes_current_price", "no_current_price",
                          "unrealized_pnl", "position_source"):
                if field in u:
                    sets.append(f"{field} = ?")
                    vals.append(u[field])
            if not sets:
                continue
            sets.append("position_updated_at = ?")
            vals.append(ts)
            vals.extend([sid, li])
            affected = conn.execute(
                f"UPDATE strategy_legs SET {', '.join(sets)} WHERE strategy_id = ? AND leg_index = ?",
                vals,
            ).rowcount
            count += affected
        conn.commit()
    finally:
        conn.close()
    return count


def update_strategy_profit(strategy_id: int, realized_profit: float) -> None:
    """Write realized_profit back to strategy_registry."""
    conn = connect()
    try:
        ts = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE strategy_registry SET realized_profit = ?, updated_at_utc = ? WHERE strategy_id = ?",
            (realized_profit, ts, strategy_id),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Strategy state store: explicit persistent state for strategy code
# ---------------------------------------------------------------------------


def _normalize_state_namespace(namespace: str = RUNTIME_STATE_NAMESPACE) -> str:
    ns = str(namespace or RUNTIME_STATE_NAMESPACE).strip().lower()
    if ns in {"default", "legacy"}:
        return LEGACY_STATE_NAMESPACE
    if ns in {"runtime", "strategy"}:
        return RUNTIME_STATE_NAMESPACE
    if ns in {"user", "manual"}:
        return USER_STATE_NAMESPACE
    if ns in {"system"}:
        return "system"
    return ns or RUNTIME_STATE_NAMESPACE


def _json_value_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _read_strategy_state_with_conn(
    conn: sqlite3.Connection,
    strategy_id: int,
    namespace: str,
) -> Dict[str, Any]:
    rows = conn.execute(
        """SELECT key, value_json
           FROM strategy_state
           WHERE strategy_id = ? AND namespace = ?
           ORDER BY key""",
        (int(strategy_id), namespace),
    ).fetchall()
    result: Dict[str, Any] = {}
    for row in rows:
        key = str(row["key"] or "").strip()
        if not key:
            continue
        try:
            result[key] = json.loads(row["value_json"])
        except (TypeError, ValueError):
            result[key] = row["value_json"]
    return result


def _read_strategy_state_json_with_conn(
    conn: sqlite3.Connection,
    strategy_id: int,
    namespace: str,
) -> Dict[str, str]:
    rows = conn.execute(
        """SELECT key, value_json
           FROM strategy_state
           WHERE strategy_id = ? AND namespace = ?""",
        (int(strategy_id), namespace),
    ).fetchall()
    return {str(row["key"]): row["value_json"] for row in rows if str(row["key"] or "").strip()}


def read_strategy_state(strategy_id: int, namespace: str = RUNTIME_STATE_NAMESPACE) -> Dict[str, Any]:
    """Return persisted strategy state for one namespace.

    Runtime state reads merge legacy `default` rows first, then `runtime` rows.
    This keeps strategies created before the namespace split compatible.
    """
    ns = _normalize_state_namespace(namespace)
    try:
        conn = connect(readonly=True)
    except Exception:
        return {}
    try:
        if ns == RUNTIME_STATE_NAMESPACE:
            result = _read_strategy_state_with_conn(conn, strategy_id, LEGACY_STATE_NAMESPACE)
            result.update(_read_strategy_state_with_conn(conn, strategy_id, RUNTIME_STATE_NAMESPACE))
            return result
        return _read_strategy_state_with_conn(conn, strategy_id, ns)
    except Exception:
        return {}
    finally:
        conn.close()


def read_strategy_state_bundle(strategy_id: int) -> Dict[str, Any]:
    """Return state grouped by ownership for API/UI/UseData builders."""
    try:
        conn = connect(readonly=True)
    except Exception:
        return {
            "runtime": {},
            "user": {},
            "system": {},
            "legacy": {},
        }
    try:
        legacy = _read_strategy_state_with_conn(conn, strategy_id, LEGACY_STATE_NAMESPACE)
        runtime = dict(legacy)
        runtime.update(_read_strategy_state_with_conn(conn, strategy_id, RUNTIME_STATE_NAMESPACE))
        return {
            "runtime": runtime,
            "user": _read_strategy_state_with_conn(conn, strategy_id, USER_STATE_NAMESPACE),
            "system": _read_strategy_state_with_conn(conn, strategy_id, "system"),
            "legacy": legacy,
        }
    except Exception:
        return {
            "runtime": {},
            "user": {},
            "system": {},
            "legacy": {},
        }
    finally:
        conn.close()


def write_strategy_state_values(
    strategy_id: int,
    values: Dict[str, Any],
    namespace: str = USER_STATE_NAMESPACE,
    *,
    replace: bool = False,
    actor: str = "user",
    reason: str = "",
    conn: Optional[sqlite3.Connection] = None,
) -> int:
    """Upsert or replace one state namespace. Returns affected key count."""
    if not isinstance(values, dict):
        raise ValueError("state values must be a JSON object")
    ns = _normalize_state_namespace(namespace)
    own_conn = conn is None
    if own_conn:
        conn = connect()
    assert conn is not None
    ts = datetime.now(timezone.utc).isoformat()
    actor_text = str(actor or "system").strip() or "system"
    reason_text = str(reason or "").strip()
    count = 0
    try:
        old_rows = _read_strategy_state_json_with_conn(conn, strategy_id, ns)
        normalized_values: Dict[str, Any] = {}
        for key, value in values.items():
            key_text = str(key or "").strip()
            if key_text:
                normalized_values[key_text] = value

        if replace:
            for key_text, old_json in old_rows.items():
                if key_text in normalized_values:
                    continue
                conn.execute(
                    "DELETE FROM strategy_state WHERE strategy_id = ? AND namespace = ? AND key = ?",
                    (int(strategy_id), ns, key_text),
                )
                conn.execute(
                    """INSERT INTO strategy_state_audit(
                           strategy_id, namespace, key, old_value_json, new_value_json,
                           action, actor, reason, updated_at_utc
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (int(strategy_id), ns, key_text, old_json, None, "DELETE", actor_text, reason_text, ts),
                )
                count += 1

        for key_text, value in normalized_values.items():
            new_json = _json_value_text(value)
            old_json = old_rows.get(key_text)
            if old_json == new_json:
                continue
            conn.execute(
                """INSERT INTO strategy_state(strategy_id, namespace, key, value_json, updated_at_utc)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(strategy_id, namespace, key)
                   DO UPDATE SET value_json = excluded.value_json,
                                 updated_at_utc = excluded.updated_at_utc""",
                (int(strategy_id), ns, key_text, new_json, ts),
            )
            conn.execute(
                """INSERT INTO strategy_state_audit(
                       strategy_id, namespace, key, old_value_json, new_value_json,
                       action, actor, reason, updated_at_utc
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    int(strategy_id),
                    ns,
                    key_text,
                    old_json,
                    new_json,
                    "UPDATE" if key_text in old_rows else "INSERT",
                    actor_text,
                    reason_text,
                    ts,
                ),
            )
            count += 1

        if own_conn:
            conn.commit()
        return count
    finally:
        if own_conn:
            conn.close()


def reset_strategy_state_namespace(
    strategy_id: int,
    namespace: str,
    *,
    actor: str = "user",
    reason: str = "",
    conn: Optional[sqlite3.Connection] = None,
) -> int:
    """Delete all keys in one namespace and audit the reset."""
    ns = _normalize_state_namespace(namespace)
    own_conn = conn is None
    if own_conn:
        conn = connect()
    assert conn is not None
    ts = datetime.now(timezone.utc).isoformat()
    actor_text = str(actor or "system").strip() or "system"
    reason_text = str(reason or "").strip()
    try:
        old_rows = _read_strategy_state_json_with_conn(conn, strategy_id, ns)
        for key_text, old_json in old_rows.items():
            conn.execute(
                """INSERT INTO strategy_state_audit(
                       strategy_id, namespace, key, old_value_json, new_value_json,
                       action, actor, reason, updated_at_utc
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (int(strategy_id), ns, key_text, old_json, None, "DELETE", actor_text, reason_text, ts),
            )
        conn.execute(
            "DELETE FROM strategy_state WHERE strategy_id = ? AND namespace = ?",
            (int(strategy_id), ns),
        )
        if own_conn:
            conn.commit()
        return len(old_rows)
    finally:
        if own_conn:
            conn.close()


def write_strategy_state_updates(
    strategy_id: int,
    updates: Dict[str, Any],
    namespace: str = RUNTIME_STATE_NAMESPACE,
    *,
    conn: Optional[sqlite3.Connection] = None,
) -> int:
    """Upsert runtime state updates emitted by strategy code. Returns row count."""
    if not isinstance(updates, dict) or not updates:
        return 0
    return write_strategy_state_values(
        strategy_id,
        updates,
        namespace=namespace,
        replace=False,
        actor="strategy",
        reason="state_updates",
        conn=conn,
    )


# ---------------------------------------------------------------------------
# Compatibility: flat dict matching old monitoring table format
# ---------------------------------------------------------------------------


def strategy_to_flat_dict(strategy: Dict[str, Any], leg_index: int = 0) -> Dict[str, Any]:
    """Convert a strategy+legs dict to a flat dict compatible with old code.
    Maps new column names to the field names that _build_strategy_item() expects."""
    legs = strategy.get("legs") or []
    leg = None
    for lg in legs:
        if lg.get("leg_index") == leg_index:
            leg = lg
            break
    if leg is None and legs:
        leg = legs[0]
    flat: Dict[str, Any] = {
        "row_id": strategy["strategy_id"],
        "Strategy": strategy.get("strategy_name", ""),
        "Code": strategy.get("strategy_code", ""),
        "state": strategy.get("state", "Stop"),
        "initial_capital": strategy.get("initial_capital", 0),
        "strategy_bankroll": strategy.get("strategy_bankroll", 0),
        "profit_roll_ratio": strategy.get("profit_roll_ratio", 0),
        "realized_profit": strategy.get("realized_profit", 0),
        "input_json": strategy.get("input_json", "{}"),
    }
    # Expand input_json into Inputs1~13 keys for backward compatibility
    try:
        import json as _json
        parsed_inputs = _json.loads(strategy.get("input_json") or "{}")
        for k, v in (parsed_inputs or {}).items():
            flat[k] = v
    except (ValueError, TypeError):
        pass
    if leg:
        leg = normalize_leg_instrument(leg)
        flat["condition_id"] = leg.get("condition_id", "")
        flat["yes_token"] = leg.get("yes_token") or ""
        flat["no_token"] = leg.get("no_token") or ""
        flat["asset_class"] = leg.get("asset_class") or "polymarket_binary"
        flat["venue"] = leg.get("venue") or ""
        flat["symbol"] = leg.get("symbol") or ""
        flat["instrument_id"] = leg.get("instrument_id") or ""
        flat["instrument_json"] = json.dumps(leg.get("instrument_json") or {}, ensure_ascii=False)
        flat["budget_cap"] = leg.get("budget_cap", 0)
        flat["Yes_now_qty"] = _safe_float(leg.get("yes_qty"))
        flat["No_now_qty"] = _safe_float(leg.get("no_qty"))
        flat["Yes_avg_cost"] = leg.get("yes_avg_cost")
        flat["No_avg_cost"] = leg.get("no_avg_cost")
        flat["Yes_ask"] = leg.get("yes_current_price")
        flat["No_ask"] = leg.get("no_current_price")
    else:
        flat["condition_id"] = ""
        flat["yes_token"] = ""
        flat["no_token"] = ""
        flat["asset_class"] = "polymarket_binary"
        flat["venue"] = "polymarket"
        flat["symbol"] = ""
        flat["instrument_id"] = ""
        flat["instrument_json"] = "{}"
        flat["budget_cap"] = 0
        flat["Yes_now_qty"] = 0.0
        flat["No_now_qty"] = 0.0
        flat["Yes_avg_cost"] = None
        flat["No_avg_cost"] = None
        flat["Yes_ask"] = None
        flat["No_ask"] = None
    return flat


def list_strategies_flat(*, state_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return strategies as flat dicts compatible with old _build_strategy_item()."""
    strategies = list_strategies(state_filter=state_filter)
    return [strategy_to_flat_dict(s) for s in strategies]


# ---------------------------------------------------------------------------
# Helpers for callers that need the raw DB path
# ---------------------------------------------------------------------------


def get_db_path() -> Path:
    return _db_path()
