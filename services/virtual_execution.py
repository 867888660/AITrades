"""
VirtualExecution — 虚拟成交引擎。

接收解析后的 FunctionJson actions，按 leg 路由执行虚拟成交，
写入五张虚拟盘表：virtual_orders / positions / account / events / ticks。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from services.strategy_audit_store import MODE_VIRTUAL, write_action_event_conn
from services.strategy_data_source import connect as ds_connect

# ---------------------------------------------------------------------------
# 手续费模型
# ---------------------------------------------------------------------------

_CATEGORY_FEE_RATE: Dict[str, float] = {
    "crypto":      0.072,
    "sports":      0.03,
    "politics":    0.04,
    "finance":     0.04,
    "tech":        0.04,
    "mentions":    0.04,
    "economics":   0.05,
    "culture":     0.05,
    "weather":     0.05,
    "other":       0.05,
    "geopolitics": 0.0,
}
_DEFAULT_FEE_RATE = 0.05
SET_TARGET_EPSILON = 1e-9
MIN_SET_TARGET_BUY_QTY = 0.01
CASH_EPSILON = 1e-9
OPEN_ORDER_ACTIVE_STATUSES = ("open", "partially_filled")
STOP_LOSS_LOCKED_STATE = "stop_loss_locked"

_GENERIC_FEE_RATE: Dict[str, float] = {
    "crypto_spot": 0.001,
    "equity": 0.0,
    "equity_option": 0.0,
}


def _fee_rate(market_category: Optional[str]) -> float:
    if not market_category:
        return _DEFAULT_FEE_RATE
    return _CATEGORY_FEE_RATE.get(market_category.lower().strip(), _DEFAULT_FEE_RATE)


def _calc_fee(qty: float, price: float, rate: float) -> float:
    """fee = qty × rate × price × (1 - price)"""
    return qty * rate * price * (1.0 - price)


def _buy_total_cost(qty: float, price: float, fee_rate: float) -> float:
    return qty * price + _calc_fee(qty, price, fee_rate)


def _max_buy_qty_for_cash(cash: float, price: float, fee_rate: float) -> float:
    if cash <= 0 or price <= 0:
        return 0.0
    per_share_cost = price * (1.0 + fee_rate * max(0.0, 1.0 - price))
    if per_share_cost <= 0:
        return 0.0
    return max(0.0, cash / per_share_cost)


def _generic_fee_rate(asset_class: str) -> float:
    return _GENERIC_FEE_RATE.get(str(asset_class or "").strip().lower(), 0.0)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or str(value).strip() == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _side_label(side: str) -> str:
    return "Yes" if str(side or "").strip().upper() == "YES" else "No"


def _action_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _order_action(value: Any) -> str:
    raw = str(value or "BUY").strip().upper()
    return "SELL" if raw == "SELL" else "BUY"


def _outcome_side(raw_action: Optional[Dict[str, Any]], default: str = "Yes") -> str:
    raw = str(
        (raw_action or {}).get("outcome")
        or (raw_action or {}).get("asset_side")
        or ""
    ).strip()
    if not raw:
        side_candidate = str((raw_action or {}).get("side") or "").strip()
        if side_candidate.upper() not in {"BUY", "SELL"}:
            raw = side_candidate
    return _side_label(raw or default)


def _client_order_tag(action: Optional[Dict[str, Any]]) -> str:
    return str(
        (action or {}).get("client_order_tag")
        or (action or {}).get("tag")
        or (action or {}).get("order_tag")
        or ""
    ).strip()


def _leg_uid(use_data: Dict[str, Any], leg: int, raw_action: Optional[Dict[str, Any]] = None) -> str:
    explicit = str((raw_action or {}).get("leg_uid") or "").strip()
    if explicit:
        return explicit
    return str(use_data.get(f"L{leg}_LegUid") or f"legacy:{leg}").strip()


def _leg_condition_id(use_data: Dict[str, Any], leg: int) -> str:
    return str(use_data.get(f"L{leg}_ConditionId") or "").strip()


def _leg_token_id(use_data: Dict[str, Any], leg: int, side: str) -> str:
    side_name = _side_label(side)
    return str(use_data.get(f"L{leg}_{side_name}_TokenId") or "").strip()


# ---------------------------------------------------------------------------
# 时间工具
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# 内容哈希（用于事件去重）
# ---------------------------------------------------------------------------

def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def _parse_raw_action(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    try:
        parsed = json.loads(raw or "{}")
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _state_text(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("state")
    if isinstance(value, str):
        raw = value.strip()
        if raw:
            try:
                parsed = json.loads(raw)
                if parsed is not raw:
                    return _state_text(parsed)
            except Exception:
                pass
        return raw.lower()
    return str(value or "").strip().lower()


def _is_stop_loss_locked(
    conn: sqlite3.Connection,
    strategy_id: int,
    use_data: Optional[Dict[str, Any]] = None,
) -> bool:
    state_values: List[Any] = []
    try:
        row = conn.execute(
            """SELECT value_json
               FROM strategy_state
               WHERE strategy_id = ? AND namespace = 'machine' AND key = 'state'""",
            (strategy_id,),
        ).fetchone()
        if row:
            raw = row["value_json"] if isinstance(row, sqlite3.Row) else row[0]
            try:
                state_values.append(json.loads(raw))
            except Exception:
                state_values.append(raw)
    except Exception:
        pass

    if isinstance(use_data, dict):
        state_values.append(use_data.get("MachineState"))
        strategy_state = use_data.get("StrategyState")
        state_values.append(strategy_state)
        if isinstance(strategy_state, dict):
            state_values.append(strategy_state.get("state"))

    return any(_state_text(value) == STOP_LOSS_LOCKED_STATE for value in state_values)


def _write_stop_loss_locked_block(
    conn: sqlite3.Connection,
    strategy_id: int,
    action_type: str,
    *,
    audit_tick_id: Optional[int],
    leg: int,
    side: Optional[str],
    qty: Optional[float] = None,
    price: Optional[float] = None,
    order_ref: Optional[str] = None,
    raw_action: Optional[Dict[str, Any]] = None,
    raw_action_json: Optional[str] = None,
    function_json_hash: Optional[str] = None,
) -> None:
    write_action_event_conn(
        conn,
        strategy_id,
        MODE_VIRTUAL,
        action_type,
        tick_id=audit_tick_id,
        leg_index=leg,
        side=side,
        qty=qty,
        price=price,
        status="blocked",
        reason=STOP_LOSS_LOCKED_STATE,
        order_ref=order_ref,
        raw_action=raw_action,
        raw_action_json=raw_action_json,
        raw_function_json_hash=function_json_hash,
    )


# ---------------------------------------------------------------------------
# 核心写入函数
# ---------------------------------------------------------------------------

def _upsert_account(conn: sqlite3.Connection, strategy_id: int, initial_cash: float) -> None:
    """确保 virtual_account 行存在，不存在则初始化。"""
    row = conn.execute(
        "SELECT initial_cash, cash FROM strategy_virtual_account WHERE strategy_id = ?", (strategy_id,)
    ).fetchone()
    if not row:
        ts = _now()
        conn.execute(
            """INSERT INTO strategy_virtual_account
               (strategy_id, initial_cash, cash, equity, realized_pnl, unrealized_pnl, total_fees_paid, updated_at_utc)
               VALUES (?,?,?,0,0,0,0,?)""",
            (strategy_id, initial_cash, initial_cash, ts),
        )
        if abs(_safe_float(initial_cash, 0.0)) > CASH_EPSILON:
            conn.execute(
                """INSERT INTO strategy_cash_ledger(
                       strategy_id, currency, amount_delta, reason, ref_table, ref_id, created_at_utc
                   ) VALUES (?, 'USD', ?, 'initial_funding', NULL, NULL, ?)""",
                (strategy_id, initial_cash, ts),
            )
        return

    old_initial = _safe_float(row["initial_cash"], 0.0)
    if initial_cash > 0 and abs(initial_cash - old_initial) > CASH_EPSILON:
        cash_delta = initial_cash - old_initial
        ts = _now()
        conn.execute(
            """UPDATE strategy_virtual_account
               SET initial_cash = ?,
                   cash = cash + ?,
                   updated_at_utc = ?
               WHERE strategy_id = ?""",
            (initial_cash, cash_delta, ts, strategy_id),
        )
        conn.execute(
            """INSERT INTO strategy_cash_ledger(
                   strategy_id, currency, amount_delta, reason, ref_table, ref_id, created_at_utc
               ) VALUES (?, 'USD', ?, 'initial_cash_adjustment', NULL, NULL, ?)""",
            (strategy_id, cash_delta, ts),
        )


def _derive_initial_cash(strategy_bankroll: float, use_data: Dict[str, Any]) -> float:
    """Resolve virtual cash from StrategyBankroll, falling back to configured leg budgets."""
    bankroll = _safe_float(strategy_bankroll, 0.0)
    if bankroll > 0:
        return bankroll

    total_configured = 0.0
    leg_count = int(_safe_float(use_data.get("LegCount"), 0.0))
    for idx in range(max(leg_count, 1)):
        configured = _safe_float(use_data.get(f"L{idx}_ConfiguredBudgetCap"), 0.0)
        if configured > 0:
            total_configured += configured
    if total_configured > 0:
        return total_configured

    fallback = _safe_float(use_data.get("ConfiguredBudgetCap"), 0.0)
    if fallback > 0:
        return fallback
    return _safe_float(use_data.get("StrategyBankroll"), 0.0)


def _get_account(conn: sqlite3.Connection, strategy_id: int) -> Dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM strategy_virtual_account WHERE strategy_id = ?", (strategy_id,)
    ).fetchone()
    return dict(row) if row else {}


def _update_account_cash(
    conn: sqlite3.Connection,
    strategy_id: int,
    net_cash_change: float,
    fee: float,
    realized_pnl_delta: float = 0.0,
    reason: str = "trade_cash_change",
) -> None:
    ts = _now()
    conn.execute(
        """UPDATE strategy_virtual_account
           SET cash = cash + ?,
               total_fees_paid = total_fees_paid + ?,
               realized_pnl = realized_pnl + ?,
               updated_at_utc = ?
           WHERE strategy_id = ?""",
        (net_cash_change, fee, realized_pnl_delta, ts, strategy_id),
    )
    if abs(_safe_float(net_cash_change, 0.0)) > CASH_EPSILON:
        conn.execute(
            """INSERT INTO strategy_cash_ledger(
                   strategy_id, currency, amount_delta, reason, ref_table, ref_id, created_at_utc
               ) VALUES (?, 'USD', ?, ?, NULL, NULL, ?)""",
            (strategy_id, net_cash_change, reason, ts),
        )


def _instrument_by_index(use_data: Dict[str, Any], index: int) -> Dict[str, Any]:
    instruments = use_data.get("Instruments") or []
    if not isinstance(instruments, list):
        return {}
    for item in instruments:
        if not isinstance(item, dict):
            continue
        item_index = item.get("index")
        if item_index is None:
            item_index = item.get("leg_index", -1)
        if int(item_index) == int(index):
            return item
    return {}


def _instrument_price(instrument: Dict[str, Any], action: str, override: Any = None) -> Optional[float]:
    if override is not None:
        if not isinstance(override, str):
            try:
                return float(override)
            except (TypeError, ValueError):
                pass
        marker = str(override).strip().lower() if isinstance(override, str) else ""
        if marker not in ("", "none", "null", "nowprice", "market"):
            try:
                return float(override)
            except (TypeError, ValueError):
                pass
    quote = instrument.get("quote") if isinstance(instrument.get("quote"), dict) else {}
    action_norm = str(action or "").upper()
    keys = ("ask", "last", "bid") if action_norm == "BUY" else ("bid", "last", "ask")
    for key in keys:
        price = _safe_float(quote.get(key), 0.0)
        if price > 0:
            return price
    return None


def _get_position_v2(
    conn: sqlite3.Connection,
    strategy_id: int,
    instrument_id: str,
    side: str = "LONG",
) -> Dict[str, Any]:
    row = conn.execute(
        """SELECT * FROM strategy_virtual_positions_v2
           WHERE strategy_id = ? AND instrument_id = ? AND side = ?""",
        (strategy_id, instrument_id, side.upper()),
    ).fetchone()
    return dict(row) if row else {}


def _upsert_position_v2(
    conn: sqlite3.Connection,
    strategy_id: int,
    instrument_id: str,
    asset_class: str,
    side: str,
    qty_delta: float,
    price: float,
) -> float:
    ts = _now()
    side = str(side or "LONG").upper()
    pos = _get_position_v2(conn, strategy_id, instrument_id, side)
    realized_delta = 0.0
    if not pos:
        new_qty = max(0.0, qty_delta)
        new_avg = price if new_qty > 0 else 0.0
        conn.execute(
            """INSERT INTO strategy_virtual_positions_v2(
                   strategy_id, instrument_id, asset_class, side, qty, avg_price,
                   cost, market_value, realized_pnl, unrealized_pnl, updated_at_utc
               ) VALUES (?,?,?,?,?,?,?,?,0,0,?)""",
            (strategy_id, instrument_id, asset_class, side, new_qty, new_avg, new_qty * new_avg, new_qty * price, ts),
        )
        return 0.0

    old_qty = _safe_float(pos.get("qty"), 0.0)
    old_avg = _safe_float(pos.get("avg_price"), 0.0)
    old_realized = _safe_float(pos.get("realized_pnl"), 0.0)
    if qty_delta > 0:
        new_qty = old_qty + qty_delta
        new_avg = (old_qty * old_avg + qty_delta * price) / new_qty if new_qty > 0 else 0.0
        realized = old_realized
    else:
        sell_qty = min(abs(qty_delta), old_qty)
        new_qty = old_qty - sell_qty
        new_avg = old_avg if new_qty > 0 else 0.0
        realized_delta = sell_qty * (price - old_avg)
        realized = old_realized + realized_delta
    market_value = new_qty * price
    cost = new_qty * new_avg
    conn.execute(
        """UPDATE strategy_virtual_positions_v2
           SET qty = ?, avg_price = ?, cost = ?, market_value = ?,
               realized_pnl = ?, unrealized_pnl = ?, updated_at_utc = ?
           WHERE strategy_id = ? AND instrument_id = ? AND side = ?""",
        (
            new_qty,
            new_avg,
            cost,
            market_value,
            realized,
            market_value - cost,
            ts,
            strategy_id,
            instrument_id,
            side,
        ),
    )
    return realized_delta


def _write_order_v2(
    conn: sqlite3.Connection,
    strategy_id: int,
    instrument_id: str,
    asset_class: str,
    action: str,
    side: str,
    qty: float,
    price: float,
    fee: float,
    status: str,
    raw_action: Optional[Dict[str, Any]],
    reason: Optional[str] = None,
) -> int:
    ts = _now()
    cur = conn.execute(
        """INSERT INTO strategy_virtual_orders_v2(
               strategy_id, instrument_id, asset_class, action, side, qty,
               notional, price, fee, status, reason, raw_action_json, created_at_utc
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            strategy_id,
            instrument_id,
            asset_class,
            action.upper(),
            side.upper(),
            qty,
            qty * price,
            price,
            fee,
            status,
            reason,
            json.dumps(raw_action or {}, ensure_ascii=False, sort_keys=True),
            ts,
        ),
    )
    return cur.lastrowid


def _mark_account_equity(
    conn: sqlite3.Connection,
    strategy_id: int,
    use_data: Dict[str, Any],
    fee_rate: float,
) -> None:
    """Refresh account equity using current liquidation value for open positions."""
    acct = _get_account(conn, strategy_id)
    if not acct:
        return
    rows = conn.execute(
        "SELECT * FROM strategy_virtual_positions WHERE strategy_id = ?",
        (strategy_id,),
    ).fetchall()
    liquidation_value = 0.0
    open_cost = 0.0
    estimated_exit_fees = 0.0
    for row in rows:
        pos = dict(row)
        qty = _safe_float(pos.get("qty"), 0.0)
        avg_price = _safe_float(pos.get("avg_price"), 0.0)
        if qty <= 0 or avg_price <= 0:
            continue
        side = str(pos.get("side") or "YES")
        leg = int(pos.get("leg_index") or 0)
        fill = _simulate_taker_fill(side, leg, "SELL", use_data, None, fee_rate, qty_limit=qty)
        mark = _safe_float(fill.get("price"), 0.0)
        if mark > 0 and _safe_float(fill.get("qty"), 0.0) > 0:
            liquidation_value += _safe_float(fill.get("gross"), 0.0)
            estimated_exit_fees += _safe_float(fill.get("fee"), 0.0)
        else:
            mark = _resolve_price(side, leg, "SELL", use_data, None)
            if mark is None or mark <= 0:
                mark = avg_price
            liquidation_value += qty * mark
            estimated_exit_fees += _calc_fee(qty, mark, fee_rate)
        open_cost += qty * avg_price

    instruments = {
        str(item.get("instrument_id") or ""): item
        for item in (use_data.get("Instruments") or [])
        if isinstance(item, dict)
    }
    rows_v2 = conn.execute(
        "SELECT * FROM strategy_virtual_positions_v2 WHERE strategy_id = ?",
        (strategy_id,),
    ).fetchall()
    for row in rows_v2:
        pos = dict(row)
        instrument_id = str(pos.get("instrument_id") or "").strip()
        qty = _safe_float(pos.get("qty"), 0.0)
        avg_price = _safe_float(pos.get("avg_price"), 0.0)
        if not instrument_id or qty <= 0:
            continue
        instrument = instruments.get(instrument_id, {})
        mark = _instrument_price(instrument, "SELL", None)
        if mark is None or mark <= 0:
            mark = avg_price
        asset_class = str(pos.get("asset_class") or "")
        notional = qty * mark
        liquidation_value += notional
        open_cost += qty * avg_price
        estimated_exit_fees += notional * _generic_fee_rate(asset_class)
        conn.execute(
            """UPDATE strategy_virtual_positions_v2
               SET market_value = ?, unrealized_pnl = ?, updated_at_utc = ?
               WHERE id = ?""",
            (notional, notional - qty * avg_price, _now(), pos["id"]),
        )

    cash = _safe_float(acct.get("cash"), 0.0)
    equity = cash + liquidation_value - estimated_exit_fees
    unrealized_pnl = liquidation_value - open_cost - estimated_exit_fees
    conn.execute(
        """UPDATE strategy_virtual_account
           SET equity = ?, unrealized_pnl = ?, updated_at_utc = ?
           WHERE strategy_id = ?""",
        (equity, unrealized_pnl, _now(), strategy_id),
    )


def _get_position(
    conn: sqlite3.Connection, strategy_id: int, leg_index: int, side: str
) -> Dict[str, Any]:
    row = conn.execute(
        """SELECT * FROM strategy_virtual_positions
           WHERE strategy_id = ? AND leg_index = ? AND side = ?""",
        (strategy_id, leg_index, side.upper()),
    ).fetchone()
    return dict(row) if row else {}


def _upsert_position(
    conn: sqlite3.Connection,
    strategy_id: int,
    leg_index: int,
    side: str,
    qty_delta: float,
    price: float,
    realized_pnl_delta: float = 0.0,
) -> None:
    """平均成本法更新持仓。qty_delta > 0 买入，< 0 卖出。"""
    ts = _now()
    side = side.upper()
    pos = _get_position(conn, strategy_id, leg_index, side)

    if not pos:
        # 新建持仓行
        new_qty = max(0.0, qty_delta)
        new_avg = price if new_qty > 0 else 0.0
        new_cost = new_qty * new_avg
        conn.execute(
            """INSERT INTO strategy_virtual_positions
               (strategy_id, leg_index, side, qty, avg_price, cost, realized_pnl, updated_at_utc)
               VALUES (?,?,?,?,?,?,?,?)""",
            (strategy_id, leg_index, side, new_qty, new_avg, new_cost, realized_pnl_delta, ts),
        )
        return

    old_qty = float(pos.get("qty", 0.0))
    old_avg = float(pos.get("avg_price", 0.0))
    old_realized = float(pos.get("realized_pnl", 0.0))

    if qty_delta > 0:
        # 买入：加权平均成本
        new_qty = old_qty + qty_delta
        new_avg = (old_qty * old_avg + qty_delta * price) / new_qty if new_qty > 0 else 0.0
        new_cost = new_qty * new_avg
        new_realized = old_realized
    else:
        # 卖出：平均成本法，计算已实现盈亏
        sell_qty = min(abs(qty_delta), old_qty)
        new_qty = old_qty - sell_qty
        new_avg = old_avg  # 成本不变
        new_cost = new_qty * new_avg
        new_realized = old_realized + sell_qty * (price - old_avg) + realized_pnl_delta

    conn.execute(
        """UPDATE strategy_virtual_positions
           SET qty = ?, avg_price = ?, cost = ?, realized_pnl = ?, updated_at_utc = ?
           WHERE strategy_id = ? AND leg_index = ? AND side = ?""",
        (new_qty, new_avg, new_cost, new_realized, ts, strategy_id, leg_index, side),
    )


def _write_order(
    conn: sqlite3.Connection,
    strategy_id: int,
    leg_index: int,
    side: str,
    action: str,
    qty: float,
    price: float,
    fee_rate: float,
    status: str,
    reason: Optional[str] = None,
    liquidity_role: str = "taker",
    gross_override: Optional[float] = None,
    fee_override: Optional[float] = None,
) -> int:
    """写入虚拟订单，返回 id。"""
    gross = gross_override if gross_override is not None else qty * price
    fee = fee_override if fee_override is not None else _calc_fee(qty, price, fee_rate)
    if action == "BUY":
        net_cash = -(gross + fee)
    else:
        net_cash = gross - fee
    ts = _now()
    cur = conn.execute(
        """INSERT INTO strategy_virtual_orders
           (strategy_id, leg_index, side, action, qty, price,
            gross_notional, fee_rate, fee, net_cash_change, liquidity_role,
            status, reason, created_at_utc)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            strategy_id, leg_index, side.upper(), action.upper(),
            qty, price, gross, fee_rate, fee, net_cash,
            str(liquidity_role or "taker").lower(), status, reason, ts,
        ),
    )
    return cur.lastrowid


def _open_reduce_sell_qty(
    conn: sqlite3.Connection,
    strategy_id: int,
    leg_uid: str,
    leg: int,
    side: str,
    *,
    exclude_tag: str = "",
) -> float:
    params: List[Any] = [strategy_id, side.upper()]
    where = [
        "strategy_id = ?",
        "side = ?",
        "action = 'SELL'",
        "reduce_only = 1",
        "status IN ('open', 'partially_filled')",
    ]
    if leg_uid:
        where.append("leg_uid = ?")
        params.append(leg_uid)
    else:
        where.append("leg_index = ?")
        params.append(leg)
    if exclude_tag:
        where.append("client_order_tag != ?")
        params.append(exclude_tag)
    row = conn.execute(
        f"SELECT COALESCE(SUM(remaining_qty), 0) FROM strategy_virtual_open_orders WHERE {' AND '.join(where)}",
        params,
    ).fetchone()
    return _safe_float(row[0] if row else 0.0, 0.0)


def _write_virtual_open_order(
    conn: sqlite3.Connection,
    strategy_id: int,
    leg: int,
    leg_uid: str,
    condition_id: str,
    token_id: str,
    side: str,
    action: str,
    qty: float,
    price: float,
    order_type: str,
    post_only: bool,
    reduce_only: bool,
    client_order_tag: str,
    raw_action: Optional[Dict[str, Any]],
    status: str = "open",
    reason: Optional[str] = None,
) -> int:
    ts = _now()
    cur = conn.execute(
        """INSERT INTO strategy_virtual_open_orders(
            strategy_id, leg_uid, leg_index, condition_id, token_id,
            side, action, qty, filled_qty, remaining_qty, price,
            order_type, post_only, reduce_only, liquidity_role,
            client_order_tag, status, reason, raw_action_json,
            created_at_utc, updated_at_utc
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            strategy_id,
            leg_uid,
            int(leg or 0),
            condition_id,
            token_id,
            side.upper(),
            action.upper(),
            qty,
            0.0,
            qty if status in OPEN_ORDER_ACTIVE_STATUSES else 0.0,
            price,
            str(order_type or "GTC").upper(),
            1 if post_only else 0,
            1 if reduce_only else 0,
            "maker" if post_only else "limit",
            client_order_tag,
            status,
            reason,
            json.dumps(raw_action or {}, ensure_ascii=False, sort_keys=True),
            ts,
            ts,
        ),
    )
    return cur.lastrowid


def _cancel_open_orders(
    conn: sqlite3.Connection,
    strategy_id: int,
    leg: int,
    side: str,
    *,
    leg_uid: str = "",
    client_order_tag: str = "",
    reason: str = "cancel_requested",
) -> int:
    params: List[Any] = [reason, _now(), strategy_id]
    where = ["strategy_id = ?", "status IN ('open', 'partially_filled')"]
    if side:
        where.append("side = ?")
        params.append(side.upper())
    if leg_uid:
        where.append("leg_uid = ?")
        params.append(leg_uid)
    else:
        where.append("leg_index = ?")
        params.append(leg)
    if client_order_tag:
        where.append("client_order_tag = ?")
        params.append(client_order_tag)
    return conn.execute(
        f"""UPDATE strategy_virtual_open_orders
            SET status = 'canceled', reason = ?, remaining_qty = 0, updated_at_utc = ?
            WHERE {' AND '.join(where)}""",
        params,
    ).rowcount


def _cancel_reduce_only_before_exit(
    conn: sqlite3.Connection,
    strategy_id: int,
    leg: int,
    side: str,
    use_data: Dict[str, Any],
    raw_action: Optional[Dict[str, Any]],
) -> int:
    reason = str((raw_action or {}).get("reason") or (raw_action or {}).get("signal") or "").strip().lower()
    atype = str((raw_action or {}).get("type") or "").strip().upper()
    target = _safe_float((raw_action or {}).get("target_pct", (raw_action or {}).get("pct")), 1.0)
    risk_exit = (
        atype in {"CLOSE", "CLOSE_ALL"}
        or target <= 0
        or reason in {"stop_loss", "force_flat", "edge_exit", "close", "take_profit"}
    )
    if not risk_exit:
        return 0
    leg_uid = _leg_uid(use_data, leg, raw_action)
    canceled = _cancel_open_orders(
        conn,
        strategy_id,
        leg,
        "",
        leg_uid=leg_uid,
        client_order_tag="take_profit",
        reason=f"cancel_take_profit_before_{reason or atype.lower()}",
    )
    canceled += _cancel_open_orders(
        conn,
        strategy_id,
        leg,
        side,
        leg_uid=leg_uid,
        reason=f"cancel_before_{reason or atype.lower()}",
    )
    return canceled


def _write_event(
    conn: sqlite3.Connection,
    strategy_id: int,
    tick_id: Optional[int],
    event_type: str,
    content: str,
    mode: str = "virtual",
) -> None:
    """写入事件流。print 类型相同内容聚合计数；其他类型相邻相同则跳过。"""
    ch = _content_hash(content)
    ts = _now()
    last = conn.execute(
        """SELECT id, content_hash FROM strategy_virtual_events
           WHERE strategy_id = ? AND event_type = ? AND mode = ?
           ORDER BY id DESC LIMIT 1""",
        (strategy_id, event_type, mode),
    ).fetchone()
    if last and last[1] == ch:
        if event_type == "print":
            conn.execute(
                "UPDATE strategy_virtual_events SET repeat_count = repeat_count + 1, last_seen_utc = ? WHERE id = ?",
                (ts, last[0]),
            )
        return
    conn.execute(
        """INSERT INTO strategy_virtual_events
           (strategy_id, tick_id, mode, event_type, content, content_hash, repeat_count, last_seen_utc, created_at_utc)
           VALUES (?,?,?,?,?,?,1,?,?)""",
        (strategy_id, tick_id, mode, event_type, content, ch, ts, ts),
    )


def create_tick(conn: sqlite3.Connection, strategy_id: int, run_at_utc: str) -> int:
    """预写 tick 行，返回 tick_id；后续用 update_tick 回填结果。"""
    cur = conn.execute(
        """INSERT INTO strategy_virtual_ticks
           (strategy_id, run_at_utc, duration_ms, function_json, mode_output, error, orders_placed)
           VALUES (?,?,0,NULL,NULL,NULL,0)""",
        (strategy_id, run_at_utc),
    )
    conn.commit()
    return cur.lastrowid


def update_tick(
    conn: sqlite3.Connection,
    tick_id: int,
    duration_ms: float,
    function_json: Optional[str],
    mode_output: Optional[str],
    error: Optional[str],
    orders_placed: int,
) -> None:
    conn.execute(
        """UPDATE strategy_virtual_ticks
           SET duration_ms=?, function_json=?, mode_output=?, error=?, orders_placed=?
           WHERE tick_id=?""",
        (duration_ms, function_json, mode_output, error, orders_placed, tick_id),
    )
    conn.commit()


def write_tick(
    conn: sqlite3.Connection,
    strategy_id: int,
    run_at_utc: str,
    duration_ms: float,
    function_json: Optional[str],
    mode_output: Optional[str],
    error: Optional[str],
    orders_placed: int,
) -> int:
    """写入 tick 日志，返回 tick_id。保留兼容。"""
    cur = conn.execute(
        """INSERT INTO strategy_virtual_ticks
           (strategy_id, run_at_utc, duration_ms, function_json, mode_output, error, orders_placed)
           VALUES (?,?,?,?,?,?,?)""",
        (strategy_id, run_at_utc, duration_ms, function_json, mode_output, error, orders_placed),
    )
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# 主入口：执行一批 actions
# ---------------------------------------------------------------------------

def _execute_generic_order(
    conn: sqlite3.Connection,
    strategy_id: int,
    instrument_index: int,
    action_side: str,
    qty: float,
    price_override: Any,
    use_data: Dict[str, Any],
    tick_id: Optional[int],
    audit_tick_id: Optional[int] = None,
    function_json_hash: Optional[str] = None,
    raw_action: Optional[Dict[str, Any]] = None,
) -> Tuple[int, Optional[str]]:
    instrument = _instrument_by_index(use_data, instrument_index)
    if not instrument:
        write_action_event_conn(
            conn, strategy_id, MODE_VIRTUAL, "ORDER",
            tick_id=audit_tick_id, leg_index=instrument_index, side=action_side,
            status="failed", reason="instrument_not_found", raw_action=raw_action,
            raw_function_json_hash=function_json_hash,
        )
        return 0, f"ORDER I{instrument_index}: instrument not found"

    asset_class = str(instrument.get("asset_class") or "").strip()
    if asset_class == "polymarket_binary":
        raw_side = str((raw_action or {}).get("side") or "").strip()
        outcome = str((raw_action or {}).get("outcome") or ("" if raw_side.upper() in {"BUY", "SELL"} else raw_side) or "Yes")
        order_action = str(action_side or "").upper()
        if order_action == "BUY":
            return _execute_buy(
                conn, strategy_id, instrument_index, outcome, qty, price_override, _fee_rate(None),
                use_data, tick_id, audit_tick_id=audit_tick_id,
                function_json_hash=function_json_hash, raw_action=raw_action,
            )
        if order_action == "SELL":
            return _execute_sell(
                conn, strategy_id, instrument_index, outcome, qty, price_override, _fee_rate(None),
                use_data, tick_id, audit_tick_id=audit_tick_id,
                function_json_hash=function_json_hash, raw_action=raw_action,
            )

    instrument_id = str(instrument.get("instrument_id") or "").strip()
    side = "LONG"
    order_action = str(action_side or "BUY").upper()
    price = _instrument_price(instrument, order_action, price_override)
    if not instrument_id or price is None or price <= 0:
        order_id = _write_order_v2(
            conn, strategy_id, instrument_id or f"I{instrument_index}", asset_class,
            order_action, side, 0.0, 0.0, 0.0, "failed", raw_action, "no_price_or_instrument_id",
        )
        write_action_event_conn(
            conn, strategy_id, MODE_VIRTUAL, "ORDER",
            tick_id=audit_tick_id, leg_index=instrument_index, side=side, qty=qty,
            price=0.0, status="failed", reason="no_price_or_instrument_id",
            order_ref=f"v2:{order_id}", raw_action=raw_action,
            raw_function_json_hash=function_json_hash,
        )
        return 1, f"ORDER I{instrument_index}: no valid price or instrument_id"

    if qty <= 0:
        notional = _safe_float((raw_action or {}).get("notional"), 0.0)
        qty = notional / price if notional > 0 and price > 0 else 0.0
    if qty <= 0:
        return 0, None

    notional = qty * price
    fee = notional * _generic_fee_rate(asset_class)
    acct = _get_account(conn, strategy_id)
    cash = _safe_float(acct.get("cash"), 0.0)

    if order_action == "BUY":
        if _is_stop_loss_locked(conn, strategy_id, use_data):
            _write_stop_loss_locked_block(
                conn,
                strategy_id,
                "ORDER",
                audit_tick_id=audit_tick_id,
                leg=instrument_index,
                side=side,
                qty=qty,
                price=price,
                raw_action=raw_action,
                function_json_hash=function_json_hash,
            )
            return 0, None
        total_cost = notional + fee
        if cash < total_cost:
            order_id = _write_order_v2(
                conn, strategy_id, instrument_id, asset_class, order_action, side,
                qty, price, fee, "blocked", raw_action, "insufficient_cash",
            )
            write_action_event_conn(
                conn, strategy_id, MODE_VIRTUAL, "ORDER",
                tick_id=audit_tick_id, leg_index=instrument_index, side=side, qty=qty,
                price=price, status="blocked", reason="insufficient_cash",
                order_ref=f"v2:{order_id}", raw_action=raw_action,
                raw_function_json_hash=function_json_hash,
            )
            return 1, None
        order_id = _write_order_v2(
            conn, strategy_id, instrument_id, asset_class, order_action, side,
            qty, price, fee, "filled", raw_action,
        )
        _upsert_position_v2(conn, strategy_id, instrument_id, asset_class, side, qty, price)
        _update_account_cash(conn, strategy_id, -(notional + fee), fee)
    elif order_action == "SELL":
        pos = _get_position_v2(conn, strategy_id, instrument_id, side)
        held = _safe_float(pos.get("qty"), 0.0)
        sell_qty = min(qty, held)
        if sell_qty <= 0:
            order_id = _write_order_v2(
                conn, strategy_id, instrument_id, asset_class, order_action, side,
                qty, price, fee, "blocked", raw_action, "no_position",
            )
            write_action_event_conn(
                conn, strategy_id, MODE_VIRTUAL, "ORDER",
                tick_id=audit_tick_id, leg_index=instrument_index, side=side, qty=qty,
                price=price, status="blocked", reason="no_position",
                order_ref=f"v2:{order_id}", raw_action=raw_action,
                raw_function_json_hash=function_json_hash,
            )
            return 1, None
        qty = sell_qty
        notional = qty * price
        fee = notional * _generic_fee_rate(asset_class)
        realized = _upsert_position_v2(conn, strategy_id, instrument_id, asset_class, side, -qty, price)
        order_id = _write_order_v2(
            conn, strategy_id, instrument_id, asset_class, order_action, side,
            qty, price, fee, "filled", raw_action,
        )
        _update_account_cash(conn, strategy_id, notional - fee, fee, realized)
    else:
        write_action_event_conn(
            conn, strategy_id, MODE_VIRTUAL, "ORDER",
            tick_id=audit_tick_id, leg_index=instrument_index, side=side,
            status="failed", reason="unknown_order_side", raw_action=raw_action,
            raw_function_json_hash=function_json_hash,
        )
        return 0, f"ORDER I{instrument_index}: unknown side {action_side}"

    write_action_event_conn(
        conn, strategy_id, MODE_VIRTUAL, "ORDER",
        tick_id=audit_tick_id, leg_index=instrument_index, side=side, qty=qty,
        price=price, status="filled", order_ref=f"v2:{order_id}",
        raw_action=raw_action, raw_function_json_hash=function_json_hash,
    )
    return 1, None


def _execute_set_target(
    conn: sqlite3.Connection,
    strategy_id: int,
    instrument_index: int,
    target: float,
    use_data: Dict[str, Any],
    tick_id: Optional[int],
    audit_tick_id: Optional[int] = None,
    function_json_hash: Optional[str] = None,
    raw_action: Optional[Dict[str, Any]] = None,
) -> Tuple[int, Optional[str]]:
    instrument = _instrument_by_index(use_data, instrument_index)
    if not instrument:
        return 0, f"SET_TARGET I{instrument_index}: instrument not found"
    asset_class = str(instrument.get("asset_class") or "").strip()
    if asset_class == "polymarket_binary":
        raw_side = str((raw_action or {}).get("side") or "").strip()
        outcome = str((raw_action or {}).get("outcome") or ("" if raw_side.upper() in {"BUY", "SELL"} else raw_side) or "Yes")
        return _execute_setpos(
            conn, strategy_id, instrument_index, outcome, target, _fee_rate(None), use_data, tick_id,
            audit_tick_id=audit_tick_id, function_json_hash=function_json_hash, raw_action=raw_action,
        )
    budget = _safe_float(instrument.get("budget_cap"), 0.0)
    if budget <= 0:
        budget = _safe_float(use_data.get("StrategyBankroll"), 0.0)
    target_notional = max(0.0, min(1.0, _safe_float(target, 0.0))) * budget
    instrument_id = str(instrument.get("instrument_id") or "").strip()
    pos = _get_position_v2(conn, strategy_id, instrument_id, "LONG") if instrument_id else {}
    current_notional = _safe_float(pos.get("qty"), 0.0) * _safe_float(pos.get("avg_price"), 0.0)
    delta = target_notional - current_notional
    price = _instrument_price(instrument, "BUY" if delta > 0 else "SELL", None)
    if price is None or price <= 0:
        return 0, f"SET_TARGET I{instrument_index}: no valid price"
    if abs(delta) <= SET_TARGET_EPSILON:
        write_action_event_conn(
            conn, strategy_id, MODE_VIRTUAL, "SET_TARGET",
            tick_id=audit_tick_id, leg_index=instrument_index, side="LONG",
            status="skipped", reason="already_at_target", raw_action=raw_action,
            raw_function_json_hash=function_json_hash,
        )
        return 0, None
    qty = abs(delta) / price
    side = "BUY" if delta > 0 else "SELL"
    order_action = dict(raw_action or {})
    order_action.update({"type": "ORDER", "side": side, "qty": qty, "price": price})
    return _execute_generic_order(
        conn, strategy_id, instrument_index, side, qty, price, use_data, tick_id,
        audit_tick_id=audit_tick_id, function_json_hash=function_json_hash, raw_action=order_action,
    )


def _execute_place_order(
    conn: sqlite3.Connection,
    strategy_id: int,
    leg: int,
    raw_action: Dict[str, Any],
    fee_rate: float,
    use_data: Dict[str, Any],
    tick_id: Optional[int],
    audit_tick_id: Optional[int] = None,
    function_json_hash: Optional[str] = None,
    replace_existing: bool = False,
) -> Tuple[int, Optional[str]]:
    order_side = _order_action(
        raw_action.get("order_side") or raw_action.get("trade_side") or raw_action.get("side")
    )
    side = _outcome_side(raw_action)
    qty = _safe_float(raw_action.get("qty", raw_action.get("contracts", 0.0)), 0.0)
    price = _safe_float(raw_action.get("limit_price", raw_action.get("price")), 0.0)
    order_type = str(raw_action.get("order_type") or raw_action.get("time_in_force") or "GTC").strip().upper()
    post_only = _action_bool(raw_action.get("post_only", raw_action.get("postOnly")), False)
    reduce_only = _action_bool(raw_action.get("reduce_only", raw_action.get("reduceOnly")), False)
    tag = _client_order_tag(raw_action)
    replace_policy = str(raw_action.get("replace_policy") or "").strip().lower()
    leg_uid = _leg_uid(use_data, leg, raw_action)
    token_id = _leg_token_id(use_data, leg, side)
    condition_id = _leg_condition_id(use_data, leg)

    if price <= 0:
        order_id = _write_order(conn, strategy_id, leg, side, order_side, qty, 0.0, fee_rate, "failed", "no_price")
        write_action_event_conn(
            conn, strategy_id, MODE_VIRTUAL, "PLACE_ORDER",
            tick_id=audit_tick_id, leg_index=leg, side=side, qty=qty, price=0.0,
            status="failed", reason="no_price", order_ref=order_id,
            raw_action=raw_action, raw_function_json_hash=function_json_hash,
        )
        return 1, f"PLACE_ORDER L{leg} {side}: no valid price"

    if qty <= 0:
        notional = _safe_float(raw_action.get("notional"), 0.0)
        qty = notional / price if notional > 0 else 0.0
    if qty <= 0:
        write_action_event_conn(
            conn, strategy_id, MODE_VIRTUAL, "PLACE_ORDER",
            tick_id=audit_tick_id, leg_index=leg, side=side, qty=0.0, price=price,
            status="skipped", reason="qty_le_zero",
            raw_action=raw_action, raw_function_json_hash=function_json_hash,
        )
        return 0, None

    if order_side == "BUY" and _is_stop_loss_locked(conn, strategy_id, use_data):
        _write_stop_loss_locked_block(
            conn,
            strategy_id,
            str(raw_action.get("type") or "PLACE_ORDER").upper(),
            audit_tick_id=audit_tick_id,
            leg=leg,
            side=side,
            qty=qty,
            price=price,
            raw_action=raw_action,
            function_json_hash=function_json_hash,
        )
        return 0, None

    if post_only and order_type not in {"GTC", "GTD"}:
        open_id = _write_virtual_open_order(
            conn, strategy_id, leg, leg_uid, condition_id, token_id, side, order_side,
            0.0, price, order_type, post_only, reduce_only, tag, raw_action,
            status="blocked", reason="post_only_requires_gtc_or_gtd",
        )
        write_action_event_conn(
            conn, strategy_id, MODE_VIRTUAL, "PLACE_ORDER",
            tick_id=audit_tick_id, leg_index=leg, side=side, qty=qty, price=price,
            status="blocked", reason="post_only_requires_gtc_or_gtd", order_ref=f"open:{open_id}",
            raw_action=raw_action, raw_function_json_hash=function_json_hash,
        )
        return 1, None

    if tag and (replace_existing or replace_policy in {"same_tag", "cancel_then_place"}):
        _cancel_open_orders(
            conn, strategy_id, leg, side, leg_uid=leg_uid, client_order_tag=tag,
            reason="replace_same_tag",
        )
    elif tag and replace_policy == "keep_existing":
        existing = conn.execute(
            """SELECT id FROM strategy_virtual_open_orders
               WHERE strategy_id = ? AND leg_uid = ? AND side = ? AND client_order_tag = ?
                 AND status IN ('open', 'partially_filled')
               LIMIT 1""",
            (strategy_id, leg_uid, side.upper(), tag),
        ).fetchone()
        if existing:
            write_action_event_conn(
                conn, strategy_id, MODE_VIRTUAL, "PLACE_ORDER",
                tick_id=audit_tick_id, leg_index=leg, side=side, qty=qty, price=price,
                status="skipped", reason="existing_order_kept", order_ref=f"open:{existing['id']}",
                raw_action=raw_action, raw_function_json_hash=function_json_hash,
            )
            return 0, None

    if reduce_only:
        if order_side != "SELL":
            open_id = _write_virtual_open_order(
                conn, strategy_id, leg, leg_uid, condition_id, token_id, side, order_side,
                0.0, price, order_type, post_only, reduce_only, tag, raw_action,
                status="blocked", reason="reduce_only_buy_unsupported",
            )
            write_action_event_conn(
                conn, strategy_id, MODE_VIRTUAL, "PLACE_ORDER",
                tick_id=audit_tick_id, leg_index=leg, side=side, qty=qty, price=price,
                status="blocked", reason="reduce_only_buy_unsupported", order_ref=f"open:{open_id}",
                raw_action=raw_action, raw_function_json_hash=function_json_hash,
            )
            return 1, None
        pos = _get_position(conn, strategy_id, leg, side)
        held = _safe_float(pos.get("qty"), 0.0)
        already_open = _open_reduce_sell_qty(conn, strategy_id, leg_uid, leg, side, exclude_tag=tag)
        available = max(0.0, held - already_open)
        qty = min(qty, available)
        if qty <= 0:
            open_id = _write_virtual_open_order(
                conn, strategy_id, leg, leg_uid, condition_id, token_id, side, order_side,
                0.0, price, order_type, post_only, reduce_only, tag, raw_action,
                status="blocked", reason="no_available_position",
            )
            write_action_event_conn(
                conn, strategy_id, MODE_VIRTUAL, "PLACE_ORDER",
                tick_id=audit_tick_id, leg_index=leg, side=side, qty=0.0, price=price,
                status="blocked", reason="no_available_position", order_ref=f"open:{open_id}",
                raw_action=raw_action, raw_function_json_hash=function_json_hash,
            )
            return 1, None

    best_opposite = _resolve_price(side, leg, "SELL" if order_side == "SELL" else "BUY", use_data, None)
    would_cross = False
    if order_side == "SELL" and best_opposite is not None and best_opposite > 0:
        would_cross = price <= best_opposite
    elif order_side == "BUY" and best_opposite is not None and best_opposite > 0:
        would_cross = price >= best_opposite

    if post_only and would_cross:
        open_id = _write_virtual_open_order(
            conn, strategy_id, leg, leg_uid, condition_id, token_id, side, order_side,
            0.0, price, order_type, post_only, reduce_only, tag, raw_action,
            status="blocked", reason="post_only_would_cross",
        )
        write_action_event_conn(
            conn, strategy_id, MODE_VIRTUAL, "PLACE_ORDER",
            tick_id=audit_tick_id, leg_index=leg, side=side, qty=qty, price=price,
            status="blocked", reason="post_only_would_cross", order_ref=f"open:{open_id}",
            raw_action=raw_action, raw_function_json_hash=function_json_hash,
        )
        return 1, None

    if not post_only and would_cross:
        if order_side == "SELL":
            return _execute_sell(
                conn, strategy_id, leg, side, qty, best_opposite, fee_rate, use_data, tick_id,
                audit_tick_id=audit_tick_id, function_json_hash=function_json_hash, raw_action=raw_action,
            )
        return _execute_buy(
            conn, strategy_id, leg, side, qty, best_opposite, fee_rate, use_data, tick_id,
            audit_tick_id=audit_tick_id, function_json_hash=function_json_hash, raw_action=raw_action,
        )

    open_id = _write_virtual_open_order(
        conn, strategy_id, leg, leg_uid, condition_id, token_id, side, order_side,
        qty, price, order_type, post_only, reduce_only, tag, raw_action,
    )
    write_action_event_conn(
        conn, strategy_id, MODE_VIRTUAL, "PLACE_ORDER",
        tick_id=audit_tick_id, leg_index=leg, side=side, qty=qty, price=price,
        status="open", reason=str(raw_action.get("reason") or "") or None,
        order_ref=f"open:{open_id}", raw_action=raw_action,
        raw_function_json_hash=function_json_hash,
    )
    return 1, None


def _execute_cancel_order(
    conn: sqlite3.Connection,
    strategy_id: int,
    leg: int,
    raw_action: Dict[str, Any],
    use_data: Dict[str, Any],
    audit_tick_id: Optional[int] = None,
    function_json_hash: Optional[str] = None,
) -> Tuple[int, Optional[str]]:
    outcome = str(raw_action.get("outcome") or raw_action.get("asset_side") or "").strip()
    if not outcome:
        side_candidate = str(raw_action.get("side") or "").strip()
        if side_candidate.upper() not in {"BUY", "SELL"}:
            outcome = side_candidate
    side = _outcome_side(raw_action) if outcome else ""
    tag = _client_order_tag(raw_action)
    leg_uid = _leg_uid(use_data, leg, raw_action)
    count = _cancel_open_orders(
        conn, strategy_id, leg, side, leg_uid=leg_uid, client_order_tag=tag,
        reason=str(raw_action.get("reason") or "cancel_requested"),
    )
    write_action_event_conn(
        conn, strategy_id, MODE_VIRTUAL, "CANCEL_ORDER",
        tick_id=audit_tick_id, leg_index=leg, side=side or None, qty=float(count),
        status="canceled" if count else "skipped",
        reason="canceled_open_orders" if count else "no_matching_open_order",
        raw_action=raw_action, raw_function_json_hash=function_json_hash,
    )
    return count, None


def execute_actions(
    strategy_id: int,
    strategy_bankroll: float,
    actions: List[Dict[str, Any]],
    use_data: Dict[str, Any],
    tick_id: Optional[int],
    market_category: Optional[str] = None,
    audit_tick_id: Optional[int] = None,
    function_json_hash: Optional[str] = None,
) -> Tuple[int, List[str]]:
    """
    执行 FunctionJson actions 列表。

    返回 (orders_placed, error_messages)。
    """
    rate = _fee_rate(market_category)
    orders_placed = 0
    errors: List[str] = []

    conn = ds_connect()
    try:
        _upsert_account(conn, strategy_id, _derive_initial_cash(strategy_bankroll, use_data))
        conn.commit()

        for action in actions:
            atype = str(action.get("type") or "").upper()
            side = str(action.get("side") or "Yes").strip()
            leg = int(action.get("leg", action.get("instrument", 0)))

            if atype == "BUY":
                qty = _safe_float(action.get("qty"), 0.0)
                price = action.get("price")
                n, err = _execute_buy(
                    conn, strategy_id, leg, side, qty, price, rate, use_data, tick_id,
                    audit_tick_id=audit_tick_id,
                    function_json_hash=function_json_hash,
                    raw_action=action,
                )
            elif atype == "SELL":
                qty = _safe_float(action.get("qty"), 0.0)
                price = action.get("price")
                _cancel_reduce_only_before_exit(conn, strategy_id, leg, side, use_data, action)
                n, err = _execute_sell(
                    conn, strategy_id, leg, side, qty, price, rate, use_data, tick_id,
                    audit_tick_id=audit_tick_id,
                    function_json_hash=function_json_hash,
                    raw_action=action,
                )
            elif atype == "SETPOS":
                pct = _safe_float(action.get("target_pct", action.get("pct", 0.0)), 0.0)
                _cancel_reduce_only_before_exit(conn, strategy_id, leg, side, use_data, action)
                n, err = _execute_setpos(
                    conn, strategy_id, leg, side, pct, rate, use_data, tick_id,
                    audit_tick_id=audit_tick_id,
                    function_json_hash=function_json_hash,
                    raw_action=action,
                )
            elif atype == "SET_BINARY_TARGET":
                pct = _safe_float(action.get("target_pct", action.get("pct", 0.0)), 0.0)
                outcome = str(action.get("outcome") or side or "Yes").strip()
                n, err = _execute_setpos(
                    conn, strategy_id, leg, outcome, pct, rate, use_data, tick_id,
                    audit_tick_id=audit_tick_id,
                    function_json_hash=function_json_hash,
                    raw_action=action,
                )
            elif atype == "SET_TARGET":
                target = _safe_float(action.get("target", action.get("target_pct", 0.0)), 0.0)
                n, err = _execute_set_target(
                    conn, strategy_id, leg, target, use_data, tick_id,
                    audit_tick_id=audit_tick_id,
                    function_json_hash=function_json_hash,
                    raw_action=action,
                )
            elif atype == "ORDER":
                qty = _safe_float(action.get("qty", action.get("contracts", 0.0)), 0.0)
                order_side = str(action.get("order_side") or action.get("trade_side") or action.get("side") or "BUY").strip()
                price = action.get("limit_price", action.get("price"))
                n, err = _execute_generic_order(
                    conn, strategy_id, leg, order_side, qty, price, use_data, tick_id,
                    audit_tick_id=audit_tick_id,
                    function_json_hash=function_json_hash,
                    raw_action=action,
                )
            elif atype == "PLACE_ORDER":
                n, err = _execute_place_order(
                    conn, strategy_id, leg, action, rate, use_data, tick_id,
                    audit_tick_id=audit_tick_id,
                    function_json_hash=function_json_hash,
                )
            elif atype == "REPLACE_ORDER":
                n, err = _execute_place_order(
                    conn, strategy_id, leg, action, rate, use_data, tick_id,
                    audit_tick_id=audit_tick_id,
                    function_json_hash=function_json_hash,
                    replace_existing=True,
                )
            elif atype == "CANCEL_ORDER":
                n, err = _execute_cancel_order(
                    conn, strategy_id, leg, action, use_data,
                    audit_tick_id=audit_tick_id,
                    function_json_hash=function_json_hash,
                )
            elif atype == "BUY_NOTIONAL":
                notional = _safe_float(action.get("notional"), 0.0)
                price = action.get("price")
                n, err = _execute_buy_notional(
                    conn, strategy_id, leg, side, notional, price, rate, use_data, tick_id,
                    audit_tick_id=audit_tick_id,
                    function_json_hash=function_json_hash,
                    raw_action=action,
                )
            elif atype == "SELL_NOTIONAL":
                notional = _safe_float(action.get("notional"), 0.0)
                price = action.get("price")
                n, err = _execute_sell_notional(
                    conn, strategy_id, leg, side, notional, price, rate, use_data, tick_id,
                    audit_tick_id=audit_tick_id,
                    function_json_hash=function_json_hash,
                    raw_action=action,
                )
            elif atype == "CLOSE":
                price = action.get("price")
                _cancel_reduce_only_before_exit(conn, strategy_id, leg, side, use_data, action)
                n, err = _execute_close(
                    conn, strategy_id, leg, side, price, rate, use_data, tick_id,
                    audit_tick_id=audit_tick_id,
                    function_json_hash=function_json_hash,
                    raw_action=action,
                )
            elif atype == "CLOSE_ALL":
                price = action.get("price")
                _cancel_reduce_only_before_exit(conn, strategy_id, leg, "Yes", use_data, action)
                _cancel_reduce_only_before_exit(conn, strategy_id, leg, "No", use_data, action)
                n_yes, err_yes = _execute_close(
                    conn, strategy_id, leg, "Yes", price, rate, use_data, tick_id,
                    audit_tick_id=audit_tick_id,
                    function_json_hash=function_json_hash,
                    raw_action=action,
                )
                n_no, err_no = _execute_close(
                    conn, strategy_id, leg, "No", price, rate, use_data, tick_id,
                    audit_tick_id=audit_tick_id,
                    function_json_hash=function_json_hash,
                    raw_action=action,
                )
                n = n_yes + n_no
                err = "; ".join(e for e in (err_yes, err_no) if e) or None
            elif atype in ("CANCEL", "WAKE"):
                write_action_event_conn(
                    conn,
                    strategy_id,
                    MODE_VIRTUAL,
                    atype,
                    tick_id=audit_tick_id,
                    leg_index=leg,
                    side=side,
                    status="skipped",
                    reason="recorded_no_virtual_order_effect",
                    raw_action=action,
                    raw_function_json_hash=function_json_hash,
                )
                n, err = 0, None
            else:
                errors.append(f"unknown action type: {atype}")
                write_action_event_conn(
                    conn,
                    strategy_id,
                    MODE_VIRTUAL,
                    atype or "UNKNOWN",
                    tick_id=audit_tick_id,
                    leg_index=leg,
                    side=side,
                    status="failed",
                    reason="unknown_action_type",
                    raw_action=action,
                    raw_function_json_hash=function_json_hash,
                )
                continue

            orders_placed += n
            if err:
                errors.append(err)

        _mark_account_equity(conn, strategy_id, use_data, rate)
        conn.commit()
    finally:
        conn.close()

    return orders_placed, errors


def sync_virtual_open_orders(
    strategy_id: int,
    strategy_bankroll: float,
    use_data: Dict[str, Any],
    *,
    tick_id: Optional[int] = None,
    market_category: Optional[str] = None,
    audit_tick_id: Optional[int] = None,
) -> Tuple[int, List[str]]:
    """Mark virtual open orders filled/canceled based on the latest UseData book."""
    fills = 0
    errors: List[str] = []
    rate = _fee_rate(market_category)
    conn = ds_connect()
    try:
        _upsert_account(conn, strategy_id, _derive_initial_cash(strategy_bankroll, use_data))
        rows = conn.execute(
            """SELECT * FROM strategy_virtual_open_orders
               WHERE strategy_id = ? AND status IN ('open', 'partially_filled')
               ORDER BY id""",
            (strategy_id,),
        ).fetchall()
        stop_loss_locked = _is_stop_loss_locked(conn, strategy_id, use_data)
        for row in rows:
            order = dict(row)
            leg = int(order.get("leg_index") or 0)
            side = str(order.get("side") or "YES")
            action = str(order.get("action") or "SELL").upper()
            price = _safe_float(order.get("price"), 0.0)
            remaining = _safe_float(order.get("remaining_qty"), 0.0)
            raw_action_json = str(order.get("raw_action_json") or "")
            if stop_loss_locked and action == "BUY":
                conn.execute(
                    """UPDATE strategy_virtual_open_orders
                       SET status = 'canceled', reason = ?,
                           remaining_qty = 0, updated_at_utc = ?
                       WHERE id = ?""",
                    (STOP_LOSS_LOCKED_STATE, _now(), order["id"]),
                )
                _write_stop_loss_locked_block(
                    conn,
                    strategy_id,
                    "OPEN_ORDER_FILL",
                    audit_tick_id=audit_tick_id,
                    leg=leg,
                    side=side,
                    qty=remaining,
                    price=price,
                    order_ref=f"open:{order['id']}",
                    raw_action_json=raw_action_json,
                )
                continue
            if price <= 0 or remaining <= 0:
                conn.execute(
                    """UPDATE strategy_virtual_open_orders
                       SET status = 'canceled', reason = 'invalid_open_order',
                           remaining_qty = 0, updated_at_utc = ?
                       WHERE id = ?""",
                    (_now(), order["id"]),
                )
                continue
            book_price = _resolve_price(side, leg, "SELL" if action == "SELL" else "BUY", use_data, None)
            if book_price is None or book_price <= 0:
                continue
            should_fill = (action == "SELL" and book_price >= price) or (action == "BUY" and book_price <= price)
            if not should_fill:
                continue

            fill_qty = remaining
            fill_reason = "maker_fill"
            if action == "SELL":
                pos = _get_position(conn, strategy_id, leg, side)
                held = _safe_float(pos.get("qty"), 0.0)
                fill_qty = min(fill_qty, held)
                if fill_qty <= 0:
                    conn.execute(
                        """UPDATE strategy_virtual_open_orders
                           SET status = 'canceled', reason = 'no_position',
                               remaining_qty = 0, updated_at_utc = ?
                           WHERE id = ?""",
                        (_now(), order["id"]),
                    )
                    continue
                avg_cost = _safe_float(pos.get("avg_price"), 0.0)
                realized = fill_qty * (price - avg_cost)
                gross = fill_qty * price
                order_id = _write_order(
                    conn, strategy_id, leg, side, "SELL", fill_qty, price, 0.0,
                    "filled", fill_reason, liquidity_role="maker",
                )
                _upsert_position(conn, strategy_id, leg, side, -fill_qty, price)
                _update_account_cash(conn, strategy_id, gross, 0.0, realized)
            else:
                acct = _get_account(conn, strategy_id)
                cash = _safe_float(acct.get("cash"), 0.0)
                affordable = cash / price if price > 0 else 0.0
                fill_qty = min(fill_qty, affordable)
                if fill_qty <= 0:
                    conn.execute(
                        """UPDATE strategy_virtual_open_orders
                           SET status = 'canceled', reason = 'insufficient_cash',
                               remaining_qty = 0, updated_at_utc = ?
                           WHERE id = ?""",
                        (_now(), order["id"]),
                    )
                    continue
                gross = fill_qty * price
                order_id = _write_order(
                    conn, strategy_id, leg, side, "BUY", fill_qty, price, 0.0,
                    "filled", fill_reason, liquidity_role="maker",
                )
                _upsert_position(conn, strategy_id, leg, side, fill_qty, price)
                _update_account_cash(conn, strategy_id, -gross, 0.0)

            new_filled = _safe_float(order.get("filled_qty"), 0.0) + fill_qty
            new_remaining = max(0.0, remaining - fill_qty)
            new_status = "filled" if new_remaining <= CASH_EPSILON else "partially_filled"
            conn.execute(
                """UPDATE strategy_virtual_open_orders
                   SET filled_qty = ?, remaining_qty = ?, status = ?,
                       reason = ?, updated_at_utc = ?
                   WHERE id = ?""",
                (new_filled, new_remaining, new_status, fill_reason, _now(), order["id"]),
            )
            _write_event(
                conn,
                strategy_id,
                tick_id,
                "action",
                json.dumps(
                    {
                        "type": "OPEN_ORDER_FILL",
                        "open_order_id": order["id"],
                        "order_id": order_id,
                        "leg": leg,
                        "side": _side_label(side),
                        "action": action,
                        "qty": fill_qty,
                        "price": price,
                        "client_order_tag": order.get("client_order_tag") or "",
                        "status": new_status,
                    },
                    ensure_ascii=False,
                ),
            )
            write_action_event_conn(
                conn,
                strategy_id,
                MODE_VIRTUAL,
                "OPEN_ORDER_FILL",
                tick_id=audit_tick_id,
                leg_index=leg,
                side=_side_label(side),
                qty=fill_qty,
                price=price,
                status="filled",
                reason=order.get("client_order_tag") or fill_reason,
                order_ref=f"open:{order['id']}/order:{order_id}",
                raw_action=_parse_raw_action(order.get("raw_action_json")),
            )
            fills += 1

        _mark_account_equity(conn, strategy_id, use_data, rate)
        conn.commit()
    except Exception as exc:
        conn.rollback()
        errors.append(f"sync_virtual_open_orders: {type(exc).__name__}: {exc}")
    finally:
        conn.close()
    return fills, errors


# ---------------------------------------------------------------------------
# 具体动作实现
# ---------------------------------------------------------------------------

def _resolve_price(side: str, leg: int, action: str, use_data: Dict[str, Any], override: Any) -> Optional[float]:
    """解析成交价：优先用 override，否则从 UseData 取盘口价。"""
    if override is not None:
        if not isinstance(override, str):
            try:
                return float(override)
            except (TypeError, ValueError):
                pass
        marker = str(override).strip().lower() if isinstance(override, str) else ""
        if marker not in ("", "none", "null", "nowprice", "market"):
            try:
                return float(override)
            except (TypeError, ValueError):
                pass
    side_name = _side_label(side)
    price_field = "AskPrice" if action == "BUY" else "BidPrice"
    old_suffix = "ask" if action == "BUY" else "bid"
    candidates = [
        f"L{leg}_{side_name}_{price_field}",
        f"{side_name}_now_{old_suffix}_L{leg}",
    ]
    if leg == 0:
        candidates.extend([
            f"{side_name}_{price_field}",
            f"{side_name}_now_{old_suffix}",
        ])
    else:
        candidates.append(f"{side_name}_now_{old_suffix}")
    for key in candidates:
        if key not in use_data:
            continue
        price = _safe_float(use_data.get(key), 0.0)
        if price > 0:
            return price
    return None


def _numeric_price_override(override: Any) -> Optional[float]:
    if override is None:
        return None
    if isinstance(override, str):
        marker = override.strip().lower()
        if marker in ("", "none", "null", "nowprice", "market"):
            return None
    try:
        value = float(override)
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


def _normalize_book_levels(raw: Any, *, side: str) -> List[Dict[str, float]]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else []
        except Exception:
            raw = []
    if not isinstance(raw, list):
        return []
    by_price: Dict[float, float] = {}
    for level in raw:
        if not isinstance(level, dict):
            continue
        price = _safe_float(level.get("price"), 0.0)
        qty = _safe_float(level.get("qty", level.get("size")), 0.0)
        if price <= 0 or qty <= 0:
            continue
        by_price[price] = by_price.get(price, 0.0) + qty
    return [
        {"price": price, "qty": qty}
        for price, qty in sorted(by_price.items(), key=lambda item: item[0], reverse=(side == "bid"))
    ]


def _orderbook_levels(side: str, leg: int, action: str, use_data: Dict[str, Any]) -> List[Dict[str, float]]:
    side_name = _side_label(side)
    field = "AskLevels" if str(action or "").upper() == "BUY" else "BidLevels"
    candidates = [
        f"L{leg}_{side_name}_{field}",
        f"{side_name}_{field}_L{leg}",
    ]
    if leg == 0:
        candidates.append(f"{side_name}_{field}")
    else:
        candidates.append(f"{side_name}_{field}")
    for key in candidates:
        if key not in use_data:
            continue
        levels = _normalize_book_levels(
            use_data.get(key),
            side="ask" if field == "AskLevels" else "bid",
        )
        if levels:
            return levels
    return []


def _simulate_taker_fill(
    side: str,
    leg: int,
    action: str,
    use_data: Dict[str, Any],
    price_override: Any,
    fee_rate: float,
    *,
    qty_limit: Optional[float] = None,
    gross_limit: Optional[float] = None,
    cash_limit: Optional[float] = None,
) -> Dict[str, Any]:
    """Simulate an immediate taker fill by walking visible orderbook levels."""
    action = str(action or "").upper()
    limit_price = _numeric_price_override(price_override)
    levels = _orderbook_levels(side, leg, action, use_data)
    requested_qty = max(0.0, _safe_float(qty_limit, 0.0)) if qty_limit is not None else None
    remaining_qty = requested_qty
    remaining_gross = max(0.0, _safe_float(gross_limit, 0.0)) if gross_limit is not None else None
    remaining_cash = max(0.0, _safe_float(cash_limit, 0.0)) if cash_limit is not None else None
    fills: List[Dict[str, float]] = []
    gross = 0.0
    fee = 0.0
    filled_qty = 0.0
    stopped_by_cash = False
    stopped_by_gross = False

    def add_fill(level_price: float, level_qty: float) -> None:
        nonlocal gross, fee, filled_qty, remaining_qty, remaining_gross, remaining_cash
        level_fee = _calc_fee(level_qty, level_price, fee_rate)
        fills.append({"price": level_price, "qty": level_qty})
        gross += level_qty * level_price
        fee += level_fee
        filled_qty += level_qty
        if remaining_qty is not None:
            remaining_qty = max(0.0, remaining_qty - level_qty)
        if remaining_gross is not None:
            remaining_gross = max(0.0, remaining_gross - level_qty * level_price)
        if remaining_cash is not None and action == "BUY":
            remaining_cash = max(0.0, remaining_cash - level_qty * level_price - level_fee)

    for level in levels:
        price = _safe_float(level.get("price"), 0.0)
        available = _safe_float(level.get("qty"), 0.0)
        if price <= 0 or available <= 0:
            continue
        if limit_price is not None:
            if action == "BUY" and price > limit_price + CASH_EPSILON:
                break
            if action == "SELL" and price + CASH_EPSILON < limit_price:
                break
        take_qty = available
        if remaining_qty is not None:
            take_qty = min(take_qty, remaining_qty)
        if remaining_gross is not None:
            if remaining_gross <= CASH_EPSILON:
                stopped_by_gross = True
                break
            take_qty = min(take_qty, remaining_gross / price)
        if remaining_cash is not None and action == "BUY":
            per_share_cost = price + _calc_fee(1.0, price, fee_rate)
            if remaining_cash <= CASH_EPSILON or per_share_cost <= 0:
                stopped_by_cash = True
                break
            take_qty = min(take_qty, remaining_cash / per_share_cost)
        if take_qty <= CASH_EPSILON:
            break
        add_fill(price, take_qty)
        if remaining_qty is not None and remaining_qty <= CASH_EPSILON:
            break
        if remaining_gross is not None and remaining_gross <= CASH_EPSILON:
            stopped_by_gross = True
            break
        if remaining_cash is not None and action == "BUY" and remaining_cash <= CASH_EPSILON:
            stopped_by_cash = True
            break

    used_orderbook = bool(levels)
    if not fills and not used_orderbook:
        fallback_price = _resolve_price(side, leg, action, use_data, price_override)
        if fallback_price and fallback_price > 0:
            take_qty = requested_qty if requested_qty is not None else 0.0
            if remaining_gross is not None:
                take_qty = remaining_gross / fallback_price if take_qty <= 0 else min(take_qty, remaining_gross / fallback_price)
            if remaining_cash is not None and action == "BUY":
                if remaining_cash <= CASH_EPSILON:
                    stopped_by_cash = True
                    take_qty = 0.0
                else:
                    per_share_cost = fallback_price + _calc_fee(1.0, fallback_price, fee_rate)
                    affordable = remaining_cash / per_share_cost if per_share_cost > 0 else 0.0
                    take_qty = affordable if take_qty <= 0 else min(take_qty, affordable)
            if take_qty > CASH_EPSILON:
                add_fill(fallback_price, take_qty)

    vwap = gross / filled_qty if filled_qty > CASH_EPSILON else 0.0
    depth_limited = (
        used_orderbook
        and requested_qty is not None
        and filled_qty + CASH_EPSILON < requested_qty
        and not stopped_by_cash
        and not stopped_by_gross
    )
    gross_depth_limited = (
        used_orderbook
        and gross_limit is not None
        and gross + CASH_EPSILON < _safe_float(gross_limit, 0.0)
        and not stopped_by_cash
    )
    gross_capped = gross_limit is not None and (remaining_gross is not None and remaining_gross <= CASH_EPSILON)
    cash_capped = cash_limit is not None and action == "BUY" and stopped_by_cash
    return {
        "qty": filled_qty,
        "requested_qty": requested_qty,
        "price": vwap,
        "gross": gross,
        "fee": fee,
        "fills": fills,
        "levels_used": len(fills),
        "used_orderbook": used_orderbook,
        "depth_limited": depth_limited,
        "gross_depth_limited": gross_depth_limited,
        "gross_capped": gross_capped,
        "cash_capped": cash_capped,
        "limit_price": limit_price,
    }


def _budget_cap(use_data: Dict[str, Any], leg: int) -> float:
    candidates = [
        f"L{leg}_BudgetCap",
        f"BudgetCap_L{leg}",
    ]
    if leg == 0:
        candidates.append("BudgetCap")
    for key in candidates:
        if key in use_data:
            value = _safe_float(use_data.get(key), 0.0)
            if value > 0:
                return value
    return 0.0


def _dynamic_account_budget(conn: sqlite3.Connection, strategy_id: int, fallback: float) -> float:
    acct = _get_account(conn, strategy_id)
    if not acct:
        return max(0.0, fallback)
    equity = _safe_float(acct.get("equity"), 0.0)
    if equity > 0:
        return equity
    cash = _safe_float(acct.get("cash"), 0.0)
    if cash > 0:
        return cash
    return 0.0


def _position_notional(pos: Dict[str, Any]) -> float:
    qty = _safe_float(pos.get("qty"), 0.0)
    avg_price = _safe_float(pos.get("avg_price"), 0.0)
    return max(0.0, qty * avg_price)


def _usedata_side_position(use_data: Dict[str, Any], leg: int, side: str) -> tuple[float, float]:
    side_title = _side_label(side)
    candidates_qty = [
        f"L{leg}_{side_title}_PositionQty",
        f"{side_title}_PositionQty_L{leg}",
    ]
    candidates_avg = [
        f"L{leg}_{side_title}_PositionAvgPrice",
        f"{side_title}_PositionAvgPrice_L{leg}",
    ]
    if leg == 0:
        candidates_qty.extend([f"{side_title}_PositionQty", f"{side_title}_now_Qty"])
        candidates_avg.extend([f"{side_title}_PositionAvgPrice", f"{side_title}_now_avgPrice"])
    qty = 0.0
    avg = 0.0
    for key in candidates_qty:
        if key in use_data:
            qty = _safe_float(use_data.get(key), 0.0)
            break
    for key in candidates_avg:
        if key in use_data:
            avg = _safe_float(use_data.get(key), 0.0)
            break
    return max(0.0, qty), max(0.0, avg)


def _execute_buy(
    conn: sqlite3.Connection,
    strategy_id: int,
    leg: int,
    side: str,
    qty: float,
    price_override: Any,
    fee_rate: float,
    use_data: Dict[str, Any],
    tick_id: Optional[int],
    audit_tick_id: Optional[int] = None,
    function_json_hash: Optional[str] = None,
    raw_action: Optional[Dict[str, Any]] = None,
    reason: Optional[str] = None,
) -> Tuple[int, Optional[str]]:
    if qty <= 0:
        return 0, None
    if _is_stop_loss_locked(conn, strategy_id, use_data):
        _write_stop_loss_locked_block(
            conn,
            strategy_id,
            "BUY",
            audit_tick_id=audit_tick_id,
            leg=leg,
            side=side,
            qty=qty,
            raw_action=raw_action,
            function_json_hash=function_json_hash,
        )
        return 0, None
    fill = _simulate_taker_fill(
        side,
        leg,
        "BUY",
        use_data,
        price_override,
        fee_rate,
        qty_limit=qty,
    )
    price = _safe_float(fill.get("price"), 0.0)
    fill_qty = _safe_float(fill.get("qty"), 0.0)
    if price <= 0 or fill_qty <= 0:
        fail_reason = "no_book_liquidity" if fill.get("used_orderbook") else "no_price"
        order_id = _write_order(conn, strategy_id, leg, side, "BUY", qty, 0.0, fee_rate, "failed", fail_reason)
        write_action_event_conn(
            conn,
            strategy_id,
            MODE_VIRTUAL,
            "BUY",
            tick_id=audit_tick_id,
            leg_index=leg,
            side=side,
            qty=qty,
            price=0.0,
            status="failed",
            reason=fail_reason,
            order_ref=order_id,
            raw_action=raw_action,
            raw_function_json_hash=function_json_hash,
        )
        return 1, f"BUY L{leg} {side}: {fail_reason}"

    gross = _safe_float(fill.get("gross"), 0.0)
    fee = _safe_float(fill.get("fee"), 0.0)
    total_cost = gross + fee

    acct = _get_account(conn, strategy_id)
    cash = float(acct.get("cash", 0.0))
    if cash + CASH_EPSILON < total_cost:
        order_id = _write_order(conn, strategy_id, leg, side, "BUY", qty, price, fee_rate, "blocked", "insufficient_cash")
        _write_event(conn, strategy_id, tick_id, "action",
                     json.dumps({"type": "BUY", "leg": leg, "side": side, "qty": qty,
                                 "price": price, "status": "blocked", "reason": "insufficient_cash"}))
        write_action_event_conn(
            conn,
            strategy_id,
            MODE_VIRTUAL,
            "BUY",
            tick_id=audit_tick_id,
            leg_index=leg,
            side=side,
            qty=qty,
            price=price,
            status="blocked",
            reason="insufficient_cash",
            order_ref=order_id,
            raw_action=raw_action,
            raw_function_json_hash=function_json_hash,
        )
        return 1, None

    fill_reason = reason
    if fill.get("depth_limited"):
        fill_reason = "partial_fill_book_depth"
    elif _safe_float(fill.get("levels_used"), 0.0) > 1:
        fill_reason = reason or "orderbook_sweep"

    order_id = _write_order(
        conn,
        strategy_id,
        leg,
        side,
        "BUY",
        fill_qty,
        price,
        fee_rate,
        "filled",
        fill_reason,
        gross_override=gross,
        fee_override=fee,
    )
    _upsert_position(conn, strategy_id, leg, side, fill_qty, price)
    _update_account_cash(conn, strategy_id, -(gross + fee), fee)
    event_payload = {
        "type": "BUY",
        "leg": leg,
        "side": side,
        "qty": fill_qty,
        "requested_qty": qty,
        "price": price,
        "gross": gross,
        "fee": fee,
        "levels_used": fill.get("levels_used"),
        "fills": fill.get("fills"),
        "status": "filled",
    }
    if fill_reason:
        event_payload["reason"] = fill_reason
    _write_event(conn, strategy_id, tick_id, "action", json.dumps(event_payload))
    write_action_event_conn(
        conn,
        strategy_id,
        MODE_VIRTUAL,
        "BUY",
        tick_id=audit_tick_id,
        leg_index=leg,
        side=side,
        qty=fill_qty,
        price=price,
        status="filled",
        reason=fill_reason,
        order_ref=order_id,
        raw_action=raw_action,
        raw_function_json_hash=function_json_hash,
    )
    return 1, None


def _execute_sell(
    conn: sqlite3.Connection,
    strategy_id: int,
    leg: int,
    side: str,
    qty: float,
    price_override: Any,
    fee_rate: float,
    use_data: Dict[str, Any],
    tick_id: Optional[int],
    audit_tick_id: Optional[int] = None,
    function_json_hash: Optional[str] = None,
    raw_action: Optional[Dict[str, Any]] = None,
) -> Tuple[int, Optional[str]]:
    if qty <= 0:
        return 0, None

    pos = _get_position(conn, strategy_id, leg, side)
    held = float(pos.get("qty", 0.0))
    sell_qty = min(qty, held)
    if sell_qty <= 0:
        display_price = _resolve_price(side, leg, "SELL", use_data, price_override) or 0.0
        order_id = _write_order(conn, strategy_id, leg, side, "SELL", qty, display_price, fee_rate, "blocked", "no_position")
        write_action_event_conn(
            conn,
            strategy_id,
            MODE_VIRTUAL,
            "SELL",
            tick_id=audit_tick_id,
            leg_index=leg,
            side=side,
            qty=qty,
            price=display_price,
            status="blocked",
            reason="no_position",
            order_ref=order_id,
            raw_action=raw_action,
            raw_function_json_hash=function_json_hash,
        )
        return 1, None

    fill = _simulate_taker_fill(
        side,
        leg,
        "SELL",
        use_data,
        price_override,
        fee_rate,
        qty_limit=sell_qty,
    )
    price = _safe_float(fill.get("price"), 0.0)
    fill_qty = _safe_float(fill.get("qty"), 0.0)
    if price <= 0 or fill_qty <= 0:
        fail_reason = "no_book_liquidity" if fill.get("used_orderbook") else "no_price"
        order_id = _write_order(conn, strategy_id, leg, side, "SELL", sell_qty, 0.0, fee_rate, "failed", fail_reason)
        write_action_event_conn(
            conn,
            strategy_id,
            MODE_VIRTUAL,
            "SELL",
            tick_id=audit_tick_id,
            leg_index=leg,
            side=side,
            qty=sell_qty,
            price=0.0,
            status="failed",
            reason=fail_reason,
            order_ref=order_id,
            raw_action=raw_action,
            raw_function_json_hash=function_json_hash,
        )
        return 1, f"SELL L{leg} {side}: {fail_reason}"

    gross = _safe_float(fill.get("gross"), 0.0)
    fee = _safe_float(fill.get("fee"), 0.0)
    avg_cost = float(pos.get("avg_price", 0.0))
    realized = fill_qty * (price - avg_cost)
    fill_reason = None
    if fill.get("depth_limited"):
        fill_reason = "partial_fill_book_depth"
    elif _safe_float(fill.get("levels_used"), 0.0) > 1:
        fill_reason = "orderbook_sweep"

    order_id = _write_order(
        conn,
        strategy_id,
        leg,
        side,
        "SELL",
        fill_qty,
        price,
        fee_rate,
        "filled",
        fill_reason,
        gross_override=gross,
        fee_override=fee,
    )
    _upsert_position(conn, strategy_id, leg, side, -fill_qty, price)
    _update_account_cash(conn, strategy_id, gross - fee, fee, realized)
    _write_event(conn, strategy_id, tick_id, "action",
                 json.dumps({
                     "type": "SELL",
                     "leg": leg,
                     "side": side,
                     "qty": fill_qty,
                     "requested_qty": qty,
                     "price": price,
                     "gross": gross,
                     "fee": fee,
                     "levels_used": fill.get("levels_used"),
                     "fills": fill.get("fills"),
                     "status": "filled",
                     **({"reason": fill_reason} if fill_reason else {}),
                 }))
    write_action_event_conn(
        conn,
        strategy_id,
        MODE_VIRTUAL,
        "SELL",
        tick_id=audit_tick_id,
        leg_index=leg,
        side=side,
        qty=fill_qty,
        price=price,
        status="filled",
        reason=fill_reason,
        order_ref=order_id,
        raw_action=raw_action,
        raw_function_json_hash=function_json_hash,
    )
    return 1, None


def _execute_buy_notional(
    conn: sqlite3.Connection,
    strategy_id: int,
    leg: int,
    side: str,
    notional: float,
    price_override: Any,
    fee_rate: float,
    use_data: Dict[str, Any],
    tick_id: Optional[int],
    audit_tick_id: Optional[int] = None,
    function_json_hash: Optional[str] = None,
    raw_action: Optional[Dict[str, Any]] = None,
) -> Tuple[int, Optional[str]]:
    if notional <= 0:
        return 0, None
    if _is_stop_loss_locked(conn, strategy_id, use_data):
        _write_stop_loss_locked_block(
            conn,
            strategy_id,
            "BUY_NOTIONAL",
            audit_tick_id=audit_tick_id,
            leg=leg,
            side=side,
            qty=0.0,
            raw_action=raw_action,
            function_json_hash=function_json_hash,
        )
        return 0, None
    acct = _get_account(conn, strategy_id)
    cash = _safe_float(acct.get("cash"), 0.0)
    fill = _simulate_taker_fill(
        side,
        leg,
        "BUY",
        use_data,
        price_override,
        fee_rate,
        gross_limit=notional,
        cash_limit=cash,
    )
    price = _safe_float(fill.get("price"), 0.0)
    qty = _safe_float(fill.get("qty"), 0.0)
    if price <= 0 or qty <= 0:
        fail_reason = "no_book_liquidity" if fill.get("used_orderbook") else "no_price"
        order_id = _write_order(conn, strategy_id, leg, side, "BUY", 0.0, 0.0, fee_rate, "failed", fail_reason)
        write_action_event_conn(
            conn,
            strategy_id,
            MODE_VIRTUAL,
            "BUY_NOTIONAL",
            tick_id=audit_tick_id,
            leg_index=leg,
            side=side,
            qty=0.0,
            price=0.0,
            status="failed",
            reason=fail_reason,
            order_ref=order_id,
            raw_action=raw_action,
            raw_function_json_hash=function_json_hash,
        )
        return 1, f"BUY_NOTIONAL L{leg} {side}: {fail_reason}"
    reason = None
    if fill.get("cash_capped"):
        reason = "cash_capped"
    elif fill.get("gross_depth_limited"):
        reason = "partial_fill_book_depth"
    elif _safe_float(fill.get("levels_used"), 0.0) > 1:
        reason = "orderbook_sweep"
    if qty < MIN_SET_TARGET_BUY_QTY:
        write_action_event_conn(
            conn,
            strategy_id,
            MODE_VIRTUAL,
            "BUY_NOTIONAL",
            tick_id=audit_tick_id,
            leg_index=leg,
            side=side,
            qty=0.0,
            price=price,
            status="skipped",
            reason="insufficient_cash",
            raw_action=raw_action,
            raw_function_json_hash=function_json_hash,
        )
        return 0, None

    gross = _safe_float(fill.get("gross"), 0.0)
    fee = _safe_float(fill.get("fee"), 0.0)
    order_id = _write_order(
        conn,
        strategy_id,
        leg,
        side,
        "BUY",
        qty,
        price,
        fee_rate,
        "filled",
        reason,
        gross_override=gross,
        fee_override=fee,
    )
    _upsert_position(conn, strategy_id, leg, side, qty, price)
    _update_account_cash(conn, strategy_id, -(gross + fee), fee)
    _write_event(
        conn,
        strategy_id,
        tick_id,
        "action",
        json.dumps(
            {
                "type": "BUY_NOTIONAL",
                "leg": leg,
                "side": side,
                "notional": notional,
                "qty": qty,
                "price": price,
                "gross": gross,
                "fee": fee,
                "levels_used": fill.get("levels_used"),
                "fills": fill.get("fills"),
                "status": "filled",
                **({"reason": reason} if reason else {}),
            }
        ),
    )
    write_action_event_conn(
        conn,
        strategy_id,
        MODE_VIRTUAL,
        "BUY_NOTIONAL",
        tick_id=audit_tick_id,
        leg_index=leg,
        side=side,
        qty=qty,
        price=price,
        status="filled",
        reason=reason,
        order_ref=order_id,
        raw_action=raw_action,
        raw_function_json_hash=function_json_hash,
    )
    return 1, None


def _execute_sell_notional(
    conn: sqlite3.Connection,
    strategy_id: int,
    leg: int,
    side: str,
    notional: float,
    price_override: Any,
    fee_rate: float,
    use_data: Dict[str, Any],
    tick_id: Optional[int],
    audit_tick_id: Optional[int] = None,
    function_json_hash: Optional[str] = None,
    raw_action: Optional[Dict[str, Any]] = None,
) -> Tuple[int, Optional[str]]:
    if notional <= 0:
        return 0, None
    pos = _get_position(conn, strategy_id, leg, side)
    held = _safe_float(pos.get("qty"), 0.0)
    if held <= 0:
        display_price = _resolve_price(side, leg, "SELL", use_data, price_override) or 0.0
        order_id = _write_order(conn, strategy_id, leg, side, "SELL", 0.0, display_price, fee_rate, "blocked", "no_position")
        write_action_event_conn(
            conn,
            strategy_id,
            MODE_VIRTUAL,
            "SELL_NOTIONAL",
            tick_id=audit_tick_id,
            leg_index=leg,
            side=side,
            qty=0.0,
            price=display_price,
            status="blocked",
            reason="no_position",
            order_ref=order_id,
            raw_action=raw_action,
            raw_function_json_hash=function_json_hash,
        )
        return 1, None
    fill = _simulate_taker_fill(
        side,
        leg,
        "SELL",
        use_data,
        price_override,
        fee_rate,
        qty_limit=held,
        gross_limit=notional,
    )
    price = _safe_float(fill.get("price"), 0.0)
    qty = _safe_float(fill.get("qty"), 0.0)
    if price <= 0 or qty <= 0:
        fail_reason = "no_book_liquidity" if fill.get("used_orderbook") else "no_price"
        order_id = _write_order(conn, strategy_id, leg, side, "SELL", 0.0, 0.0, fee_rate, "failed", fail_reason)
        write_action_event_conn(
            conn,
            strategy_id,
            MODE_VIRTUAL,
            "SELL_NOTIONAL",
            tick_id=audit_tick_id,
            leg_index=leg,
            side=side,
            qty=0.0,
            price=0.0,
            status="failed",
            reason=fail_reason,
            order_ref=order_id,
            raw_action=raw_action,
            raw_function_json_hash=function_json_hash,
        )
        return 1, f"SELL_NOTIONAL L{leg} {side}: {fail_reason}"
    reason = None
    if fill.get("gross_depth_limited") or fill.get("depth_limited"):
        reason = "partial_fill_book_depth"
    elif _safe_float(fill.get("levels_used"), 0.0) > 1:
        reason = "orderbook_sweep"
    gross = _safe_float(fill.get("gross"), 0.0)
    fee = _safe_float(fill.get("fee"), 0.0)
    avg_cost = _safe_float(pos.get("avg_price"), 0.0)
    realized = qty * (price - avg_cost)
    order_id = _write_order(
        conn,
        strategy_id,
        leg,
        side,
        "SELL",
        qty,
        price,
        fee_rate,
        "filled",
        reason,
        gross_override=gross,
        fee_override=fee,
    )
    _upsert_position(conn, strategy_id, leg, side, -qty, price)
    _update_account_cash(conn, strategy_id, gross - fee, fee, realized)
    _write_event(
        conn,
        strategy_id,
        tick_id,
        "action",
        json.dumps(
            {
                "type": "SELL_NOTIONAL",
                "leg": leg,
                "side": side,
                "notional": notional,
                "qty": qty,
                "price": price,
                "gross": gross,
                "fee": fee,
                "levels_used": fill.get("levels_used"),
                "fills": fill.get("fills"),
                "status": "filled",
                **({"reason": reason} if reason else {}),
            }
        ),
    )
    write_action_event_conn(
        conn,
        strategy_id,
        MODE_VIRTUAL,
        "SELL_NOTIONAL",
        tick_id=audit_tick_id,
        leg_index=leg,
        side=side,
        qty=qty,
        price=price,
        status="filled",
        reason=reason,
        order_ref=order_id,
        raw_action=raw_action,
        raw_function_json_hash=function_json_hash,
    )
    return 1, None


def _execute_close(
    conn: sqlite3.Connection,
    strategy_id: int,
    leg: int,
    side: str,
    price_override: Any,
    fee_rate: float,
    use_data: Dict[str, Any],
    tick_id: Optional[int],
    audit_tick_id: Optional[int] = None,
    function_json_hash: Optional[str] = None,
    raw_action: Optional[Dict[str, Any]] = None,
) -> Tuple[int, Optional[str]]:
    pos = _get_position(conn, strategy_id, leg, side)
    qty = _safe_float(pos.get("qty"), 0.0)
    if qty <= 0:
        write_action_event_conn(
            conn,
            strategy_id,
            MODE_VIRTUAL,
            "CLOSE",
            tick_id=audit_tick_id,
            leg_index=leg,
            side=side,
            qty=0.0,
            status="skipped",
            reason="no_position",
            raw_action=raw_action,
            raw_function_json_hash=function_json_hash,
        )
        return 0, None
    return _execute_sell(
        conn, strategy_id, leg, side, qty, price_override, fee_rate, use_data, tick_id,
        audit_tick_id=audit_tick_id,
        function_json_hash=function_json_hash,
        raw_action=raw_action,
    )


def _execute_setpos(
    conn: sqlite3.Connection,
    strategy_id: int,
    leg: int,
    side: str,
    pct: float,
    fee_rate: float,
    use_data: Dict[str, Any],
    tick_id: Optional[int],
    audit_tick_id: Optional[int] = None,
    function_json_hash: Optional[str] = None,
    raw_action: Optional[Dict[str, Any]] = None,
) -> Tuple[int, Optional[str]]:
    """将仓位调整到目标比例（相对于 BudgetCap）。"""
    budget = _budget_cap(use_data, leg)
    configured_leg_budget = _safe_float(use_data.get(f"L{leg}_ConfiguredBudgetCap"), 0.0)
    if leg == 0 and configured_leg_budget <= 0:
        configured_leg_budget = _safe_float(use_data.get("ConfiguredBudgetCap"), 0.0)
    leg_count = int(_safe_float(use_data.get("LegCount"), 1.0))
    if configured_leg_budget <= 0 and (leg_count <= 1 or leg == 0):
        budget = _dynamic_account_budget(conn, strategy_id, budget)
    target_pct = max(0.0, min(1.0, _safe_float(pct, 0.0)))
    target_cost = budget * target_pct

    pos = _get_position(conn, strategy_id, leg, side)
    db_qty = _safe_float(pos.get("qty"), 0.0)
    db_avg = _safe_float(pos.get("avg_price"), 0.0)
    actual_qty, actual_avg = _usedata_side_position(use_data, leg, side)
    current_qty = actual_qty if actual_qty > 0 or db_qty <= 0 else db_qty
    current_avg = actual_avg if actual_avg > 0 or db_avg <= 0 else db_avg
    current_cost = max(0.0, current_qty * current_avg)
    delta_cost = target_cost - current_cost

    if budget <= 0:
        write_action_event_conn(
            conn,
            strategy_id,
            MODE_VIRTUAL,
            "SETPOS",
            tick_id=audit_tick_id,
            leg_index=leg,
            side=side,
            qty=current_qty,
            status="skipped",
            reason="budget_cap_le_zero",
            raw_action=raw_action,
            raw_function_json_hash=function_json_hash,
        )
        return 0, None

    if delta_cost > SET_TARGET_EPSILON and _is_stop_loss_locked(conn, strategy_id, use_data):
        _write_stop_loss_locked_block(
            conn,
            strategy_id,
            "SETPOS",
            audit_tick_id=audit_tick_id,
            leg=leg,
            side=side,
            qty=current_qty,
            raw_action=raw_action,
            function_json_hash=function_json_hash,
        )
        return 0, None

    if abs(delta_cost) <= SET_TARGET_EPSILON:
        write_action_event_conn(
            conn,
            strategy_id,
            MODE_VIRTUAL,
            "SETPOS",
            tick_id=audit_tick_id,
            leg_index=leg,
            side=side,
            qty=current_qty,
            status="skipped",
            reason="already_at_target",
            raw_action=raw_action,
            raw_function_json_hash=function_json_hash,
        )
        return 0, None

    if delta_cost > 0:
        acct = _get_account(conn, strategy_id)
        cash = float(acct.get("cash", 0.0))
        fill = _simulate_taker_fill(
            side,
            leg,
            "BUY",
            use_data,
            None,
            fee_rate,
            gross_limit=delta_cost,
            cash_limit=cash,
        )
        price = _safe_float(fill.get("price"), 0.0)
        qty = _safe_float(fill.get("qty"), 0.0)
        if price <= 0 or qty <= 0:
            fail_reason = "insufficient_cash_for_setpos" if fill.get("cash_capped") else (
                "no_book_liquidity" if fill.get("used_orderbook") else "no_price"
            )
            status = "skipped" if fail_reason == "insufficient_cash_for_setpos" else "failed"
            order_id = None
            if status == "failed":
                order_id = _write_order(conn, strategy_id, leg, side, "BUY", 0.0, 0.0, fee_rate, "failed", fail_reason)
            write_action_event_conn(
                conn,
                strategy_id,
                MODE_VIRTUAL,
                "SETPOS",
                tick_id=audit_tick_id,
                leg_index=leg,
                side=side,
                qty=0.0,
                price=0.0,
                status=status,
                reason=fail_reason,
                order_ref=order_id,
                raw_action=raw_action,
                raw_function_json_hash=function_json_hash,
            )
            return (1 if order_id else 0), (None if status == "skipped" else f"SETPOS L{leg} {side}: {fail_reason}")
        reason = None
        if fill.get("cash_capped"):
            reason = "cash_capped"
        elif fill.get("gross_depth_limited"):
            reason = "partial_fill_book_depth"
        elif _safe_float(fill.get("levels_used"), 0.0) > 1:
            reason = "orderbook_sweep"
        if qty < MIN_SET_TARGET_BUY_QTY:
            write_action_event_conn(
                conn,
                strategy_id,
                MODE_VIRTUAL,
                "SETPOS",
                tick_id=audit_tick_id,
                leg_index=leg,
                side=side,
                qty=0.0,
                price=price,
                status="skipped",
                reason="insufficient_cash_for_setpos",
                raw_action=raw_action,
                raw_function_json_hash=function_json_hash,
            )
            return 0, None
        gross = _safe_float(fill.get("gross"), 0.0)
        fee = _safe_float(fill.get("fee"), 0.0)
        order_id = _write_order(
            conn,
            strategy_id,
            leg,
            side,
            "BUY",
            qty,
            price,
            fee_rate,
            "filled",
            reason,
            gross_override=gross,
            fee_override=fee,
        )
        _upsert_position(conn, strategy_id, leg, side, qty, price)
        _update_account_cash(conn, strategy_id, -(gross + fee), fee)
        _write_event(
            conn,
            strategy_id,
            tick_id,
            "action",
            json.dumps(
                {
                    "type": "BUY",
                    "source_action": "SETPOS",
                    "leg": leg,
                    "side": side,
                    "qty": qty,
                    "target_cost_delta": delta_cost,
                    "price": price,
                    "gross": gross,
                    "fee": fee,
                    "levels_used": fill.get("levels_used"),
                    "fills": fill.get("fills"),
                    "status": "filled",
                    **({"reason": reason} if reason else {}),
                }
            ),
        )
        write_action_event_conn(
            conn,
            strategy_id,
            MODE_VIRTUAL,
            "BUY",
            tick_id=audit_tick_id,
            leg_index=leg,
            side=side,
            qty=qty,
            price=price,
            status="filled",
            reason=reason,
            order_ref=order_id,
            raw_action=raw_action,
            raw_function_json_hash=function_json_hash,
        )
        return 1, None

    price = _resolve_price(side, leg, "SELL", use_data, None)
    if price is None or price <= 0:
        order_id = _write_order(conn, strategy_id, leg, side, "SELL", 0.0, 0.0, fee_rate, "failed", "no_price")
        write_action_event_conn(
            conn,
            strategy_id,
            MODE_VIRTUAL,
            "SETPOS",
            tick_id=audit_tick_id,
            leg_index=leg,
            side=side,
            qty=0.0,
            price=0.0,
            status="failed",
            reason="no_price",
            order_ref=order_id,
            raw_action=raw_action,
            raw_function_json_hash=function_json_hash,
        )
        return 1, f"SETPOS L{leg} {side}: no valid sell price"
    target_qty = (target_cost / current_avg) if current_avg > 0 else 0.0
    qty = min(current_qty, max(0.0, current_qty - target_qty))
    return _execute_sell(
        conn, strategy_id, leg, side, qty, price, fee_rate, use_data, tick_id,
        audit_tick_id=audit_tick_id,
        function_json_hash=function_json_hash,
        raw_action=raw_action,
    )


# ---------------------------------------------------------------------------
# 写 print / error 事件（供 runner 调用）
# ---------------------------------------------------------------------------

def write_print_events(strategy_id: int, tick_id: Optional[int], messages: List[str]) -> None:
    if not messages:
        return
    conn = ds_connect()
    try:
        for msg in messages:
            _write_event(conn, strategy_id, tick_id, "print", str(msg))
        conn.commit()
    finally:
        conn.close()


def write_error_event(strategy_id: int, tick_id: Optional[int], error: str) -> None:
    conn = ds_connect()
    try:
        _write_event(conn, strategy_id, tick_id, "error", error)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 重置虚拟账户
# ---------------------------------------------------------------------------

def reset_virtual_account(strategy_id: int, initial_cash: float) -> None:
    """清空持仓、订单、账户，保留策略配置。"""
    conn = ds_connect()
    try:
        conn.execute("DELETE FROM strategy_virtual_positions WHERE strategy_id = ?", (strategy_id,))
        conn.execute("DELETE FROM strategy_virtual_orders WHERE strategy_id = ?", (strategy_id,))
        conn.execute("DELETE FROM strategy_virtual_open_orders WHERE strategy_id = ?", (strategy_id,))
        conn.execute("DELETE FROM strategy_virtual_positions_v2 WHERE strategy_id = ?", (strategy_id,))
        conn.execute("DELETE FROM strategy_virtual_orders_v2 WHERE strategy_id = ?", (strategy_id,))
        conn.execute("DELETE FROM strategy_order_intents WHERE strategy_id = ?", (strategy_id,))
        conn.execute("DELETE FROM strategy_cash_ledger WHERE strategy_id = ?", (strategy_id,))
        conn.execute("DELETE FROM strategy_virtual_ticks WHERE strategy_id = ?", (strategy_id,))
        conn.execute("DELETE FROM strategy_virtual_account WHERE strategy_id = ?", (strategy_id,))
        ts = _now()
        conn.execute(
            """INSERT INTO strategy_virtual_account
               (strategy_id, initial_cash, cash, equity, realized_pnl, unrealized_pnl, total_fees_paid, updated_at_utc)
               VALUES (?,?,?,0,0,0,0,?)""",
            (strategy_id, initial_cash, initial_cash, ts),
        )
        _write_event(conn, strategy_id, None, "print", "Virtual account reset; historical prints retained.")
        conn.commit()
    finally:
        conn.close()


def sync_virtual_account_bankroll(strategy_id: int, initial_cash: float) -> Dict[str, Any]:
    """Sync virtual account cash to the saved strategy bankroll without reset."""
    next_initial = max(0.0, _safe_float(initial_cash, 0.0))
    conn = ds_connect()
    try:
        row = conn.execute(
            "SELECT initial_cash, cash FROM strategy_virtual_account WHERE strategy_id = ?",
            (strategy_id,),
        ).fetchone()
        ts = _now()
        if not row:
            conn.execute(
                """INSERT INTO strategy_virtual_account
                   (strategy_id, initial_cash, cash, equity, realized_pnl, unrealized_pnl, total_fees_paid, updated_at_utc)
                   VALUES (?,?,?,0,0,0,0,?)""",
                (strategy_id, next_initial, next_initial, ts),
            )
            conn.commit()
            return {"status": "created", "initial_cash": next_initial, "cash_delta": next_initial}

        old_initial = _safe_float(row["initial_cash"], 0.0)
        cash_delta = next_initial - old_initial
        if abs(cash_delta) < 0.0000001:
            return {"status": "unchanged", "initial_cash": next_initial, "cash_delta": 0.0}

        conn.execute(
            """UPDATE strategy_virtual_account
               SET initial_cash = ?,
                   cash = cash + ?,
                   updated_at_utc = ?
               WHERE strategy_id = ?""",
            (next_initial, cash_delta, ts, strategy_id),
        )
        conn.commit()
        return {"status": "synced", "initial_cash": next_initial, "cash_delta": cash_delta}
    finally:
        conn.close()
