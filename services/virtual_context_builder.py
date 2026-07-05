"""
VirtualContextBuilder — 组装 UseData 字典，按五层优先级注入。

优先级（高→低）：
  Tier 1  盘口（per-leg，_L{n} 后缀，L0 同时作为无后缀别名）
  Tier 2  预算派生（向后兼容旧策略代码）
  Tier 3  时间（全局）
  Tier 4  外部行情（realtime_collector 注入）
  Tier 5  用户自定义参数（input_json）
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.clob_orderbook_service import fetch_orderbook_quote
from services.config_loader import BASE_DIR, load_web_settings
from services.strategy_data_source import (
    connect as ds_connect,
    derive_instrument_id,
    derive_leg_kind,
    normalize_leg_instrument,
    read_strategy_state_bundle,
)
from services.strategy_schema_service import get_strategy_code_schemas, merge_schema_defaults


_MARKET_META_CACHE: Dict[str, Dict[str, Any]] = {}
_MARKET_META_TTL_SECONDS = 300.0
_DICTIONARY_META_CACHE: Dict[str, Dict[str, Any]] = {}
_RESOLVED_META_CACHE: Dict[str, Dict[str, Any]] = {}
_END_DATE_INPUT_KEYS = ("Enddate", "EndDate", "end_date", "endDate", "EndTime", "L0_EndTime")


# ---------------------------------------------------------------------------
# 市场类别 → taker 费率
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


def get_fee_rate(market_category: Optional[str]) -> float:
    if not market_category:
        return _DEFAULT_FEE_RATE
    return _CATEGORY_FEE_RATE.get(market_category.lower().strip(), _DEFAULT_FEE_RATE)


# ---------------------------------------------------------------------------
# 从 polymarket_realtime.db 读取盘口快照
# ---------------------------------------------------------------------------

def _read_market_snapshot(realtime_db_path: str, token: str) -> Dict[str, Any]:
    """从 market_deltas 表读取最新一条盘口快照，返回字段字典。"""
    if not realtime_db_path or not token:
        return {}
    try:
        conn = sqlite3.connect(realtime_db_path, timeout=3.0)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """SELECT timestamp, now_bid, now_ask, best_bid, best_ask, last_price
               FROM market_deltas
               WHERE clobTokenId = ?
               ORDER BY id DESC LIMIT 1""",
            (token,),
        ).fetchone()
        conn.close()
        if not row:
            return {}
        d = dict(row)
        d["ts"] = d.pop("timestamp", None)
        return d
    except Exception:
        return {}


def _read_live_orderbook_quote(token: str) -> Dict[str, Any]:
    """Read the current CLOB orderbook top of book for one token."""
    try:
        quote = fetch_orderbook_quote(token)
    except Exception:
        return {}
    if not quote:
        return {}
    return {
        "now_bid": quote.get("bid"),
        "now_ask": quote.get("ask"),
        "best_bid": quote.get("bid"),
        "best_ask": quote.get("ask"),
        "best_bid_qty": quote.get("bid_size"),
        "best_ask_qty": quote.get("ask_size"),
        "bid_levels": quote.get("bids") or [],
        "ask_levels": quote.get("asks") or [],
        "bid_depth_qty": quote.get("bid_depth_qty"),
        "ask_depth_qty": quote.get("ask_depth_qty"),
        "bid_depth_notional": quote.get("bid_depth_notional"),
        "ask_depth_notional": quote.get("ask_depth_notional"),
        "ts": quote.get("updated_at"),
        "source": "clob_book",
    }


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        if isinstance(value, str) and not value.strip():
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _valid_binary_price(value: Any) -> Optional[float]:
    price = _safe_float(value, 0.0)
    if 0.0 < price < 1.0:
        return price
    return None


def _normalize_book_levels(value: Any, *, side: str) -> List[Dict[str, float]]:
    if isinstance(value, str):
        try:
            value = json.loads(value) if value.strip() else []
        except Exception:
            value = []
    if not isinstance(value, list):
        return []
    by_price: Dict[float, float] = {}
    for level in value:
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


def _levels_qty(levels: List[Dict[str, float]]) -> float:
    return sum(_safe_float(level.get("qty"), 0.0) for level in levels)


def _levels_notional(levels: List[Dict[str, float]]) -> float:
    return sum(_safe_float(level.get("price"), 0.0) * _safe_float(level.get("qty"), 0.0) for level in levels)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or str(value).strip() == "":
            return int(default)
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _ema_values(values: List[float], period: int) -> List[float]:
    if not values:
        return []
    period = max(1, int(period))
    alpha = 2.0 / (period + 1.0)
    out: List[float] = []
    ema = float(values[0])
    for value in values:
        ema = alpha * float(value) + (1.0 - alpha) * ema
        out.append(ema)
    return out


def _compute_macd(values: List[float], fast: int, slow: int, signal: int) -> Dict[str, Any]:
    values = [float(v) for v in values if _valid_binary_price(v) is not None]
    if len(values) < max(2, slow):
        return {}
    ema_fast = _ema_values(values, fast)
    ema_slow = _ema_values(values, slow)
    macd_values = [f - s for f, s in zip(ema_fast, ema_slow)]
    signal_values = _ema_values(macd_values, signal)
    hist_values = [m - s for m, s in zip(macd_values, signal_values)]
    if not hist_values:
        return {}
    hist_prev = hist_values[-2] if len(hist_values) >= 2 else hist_values[-1]
    return {
        "macd": macd_values[-1],
        "macd_signal": signal_values[-1],
        "macd_hist": hist_values[-1],
        "macd_hist_prev": hist_prev,
        "macd_hist_slope": hist_values[-1] - hist_prev,
        "macd_sample_count": len(values),
    }


def _history_price_from_row(row: sqlite3.Row) -> Optional[float]:
    bid = _valid_binary_price(row["now_bid"] if "now_bid" in row.keys() else None)
    if bid is None:
        bid = _valid_binary_price(row["best_bid"] if "best_bid" in row.keys() else None)
    if bid is not None:
        return bid
    last_price = _valid_binary_price(row["last_price"] if "last_price" in row.keys() else None)
    if last_price is not None:
        return last_price
    return None


def _read_market_macd(
    realtime_db_path: str,
    token: str,
    fast: int,
    slow: int,
    signal: int,
    current_price: Any = None,
    limit: int = 240,
) -> Dict[str, Any]:
    """Compute a lightweight MACD snapshot from recent sellable bid history."""
    if not realtime_db_path or not token:
        return {}
    try:
        conn = sqlite3.connect(realtime_db_path, timeout=3.0)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT timestamp, now_bid, now_ask, best_bid, best_ask, last_price
               FROM market_deltas
               WHERE clobTokenId = ?
               ORDER BY id DESC LIMIT ?""",
            (token, max(20, int(limit))),
        ).fetchall()
        conn.close()
    except Exception:
        return {}

    values: List[float] = []
    for row in reversed(rows):
        price = _history_price_from_row(row)
        if price is not None:
            values.append(price)
    current = _valid_binary_price(current_price)
    if current is not None and (not values or abs(values[-1] - current) > 1e-9):
        values.append(current)
    out = _compute_macd(values, fast, slow, signal)
    if out:
        out["macd_source"] = "market_deltas_bid"
    return out


def _parse_input_json(raw_input: Any) -> Dict[str, Any]:
    if isinstance(raw_input, dict):
        return dict(raw_input)
    if isinstance(raw_input, str):
        try:
            parsed = json.loads(raw_input) if raw_input.strip() else {}
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _market_status(state_snap: Dict[str, Any]) -> str:
    for key in ("market_status", "status", "state"):
        value = str(state_snap.get(key) or "").strip().lower()
        if value:
            if value in ("open", "active", "live", "trading"):
                return "open"
            if value in ("closed", "resolved", "settled", "finalized"):
                return "resolved" if value in ("resolved", "settled", "finalized") else "closed"
            return value
    if state_snap:
        return "open"
    return "unknown"


def _snapshot_age_seconds(snapshot: Dict[str, Any], now_utc: datetime) -> float:
    raw_ts = snapshot.get("ts") or snapshot.get("timestamp") or snapshot.get("updated_at")
    if raw_ts is None or raw_ts == "":
        return 0.0
    try:
        if isinstance(raw_ts, (int, float)):
            ts_value = float(raw_ts)
            if ts_value > 1_000_000_000_000:
                ts_value = ts_value / 1000.0
            sample_dt = datetime.fromtimestamp(ts_value, tz=timezone.utc)
        else:
            sample_dt = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
            if sample_dt.tzinfo is None:
                sample_dt = sample_dt.replace(tzinfo=timezone.utc)
        return max(0.0, (now_utc - sample_dt.astimezone(timezone.utc)).total_seconds())
    except Exception:
        return 0.0


def _data_status(ask_price: float, bid_price: float, snapshot: Dict[str, Any]) -> str:
    if not snapshot:
        return "missing"
    if ask_price <= 0 and bid_price <= 0:
        return "missing"
    return "ok"


def _read_markets_state(realtime_db_path: str, token: str) -> Dict[str, Any]:
    """从 markets_state 表读取最新状态（含 end_date_iso 等）。"""
    if not realtime_db_path or not token:
        return {}
    try:
        conn = sqlite3.connect(realtime_db_path, timeout=3.0)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM markets_state WHERE clobTokenId = ? LIMIT 1",
            (token,),
        ).fetchone()
        conn.close()
        return dict(row) if row else {}
    except Exception:
        return {}


def _resolve_tokens_from_condition_id(realtime_db_path: str, condition_id: str) -> Dict[str, str]:
    """从 markets_state 通过 condition_id 反查 yes/no token。返回 {"yes": token, "no": token}。"""
    if not realtime_db_path or not condition_id:
        return {}
    try:
        conn = sqlite3.connect(realtime_db_path, timeout=3.0)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT clobTokenId, outcome_side FROM markets_state WHERE condition_id = ?",
            (condition_id,),
        ).fetchall()
        conn.close()
        result = {}
        for r in rows:
            side = str(r["outcome_side"] or "").strip().lower()
            token = str(r["clobTokenId"] or "").strip()
            if side in ("yes", "no") and token:
                result[side] = token
        return result
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# 从虚拟盘持仓表读取当前仓位
# ---------------------------------------------------------------------------

def _read_virtual_positions(strategy_id: int) -> Dict[tuple, Dict[str, Any]]:
    """返回 {(leg_index, side): row_dict}"""
    result: Dict[tuple, Dict[str, Any]] = {}
    try:
        conn = ds_connect(readonly=True)
        rows = conn.execute(
            "SELECT * FROM strategy_virtual_positions WHERE strategy_id = ?",
            (strategy_id,),
        ).fetchall()
        conn.close()
        for r in rows:
            d = dict(r)
            result[(d["leg_index"], d["side"].upper())] = d
    except Exception:
        pass
    return result


def _read_virtual_open_orders(strategy_id: int) -> Dict[tuple, Dict[str, Any]]:
    """Return open order aggregates keyed by (leg_uid, leg_index, side)."""
    result: Dict[tuple, Dict[str, Any]] = {}
    try:
        conn = ds_connect(readonly=True)
        rows = conn.execute(
            """SELECT * FROM strategy_virtual_open_orders
               WHERE strategy_id = ? AND status IN ('open', 'partially_filled')""",
            (strategy_id,),
        ).fetchall()
        conn.close()
        for row in rows:
            d = dict(row)
            leg_uid = str(d.get("leg_uid") or "").strip()
            leg_index = int(d.get("leg_index") or 0)
            side = str(d.get("side") or "").upper()
            key = (leg_uid, leg_index, side)
            agg = result.setdefault(
                key,
                {
                    "open_buy_qty": 0.0,
                    "open_sell_qty": 0.0,
                    "open_reduce_sell_qty": 0.0,
                    "take_profit_order_price": None,
                    "take_profit_order_qty": 0.0,
                    "take_profit_order_status": "",
                },
            )
            remaining = _safe_float(d.get("remaining_qty"), 0.0)
            action = str(d.get("action") or "").upper()
            if action == "BUY":
                agg["open_buy_qty"] += remaining
            elif action == "SELL":
                agg["open_sell_qty"] += remaining
                if int(d.get("reduce_only") or 0):
                    agg["open_reduce_sell_qty"] += remaining
            if str(d.get("client_order_tag") or "").strip() == "take_profit":
                agg["take_profit_order_price"] = _safe_float(d.get("price"), 0.0)
                agg["take_profit_order_qty"] += remaining
                agg["take_profit_order_status"] = str(d.get("status") or "")
    except Exception:
        pass
    return result


def _read_virtual_account(strategy_id: int) -> Dict[str, Any]:
    try:
        conn = ds_connect(readonly=True)
        row = conn.execute(
            "SELECT * FROM strategy_virtual_account WHERE strategy_id = ?",
            (strategy_id,),
        ).fetchone()
        conn.close()
        return dict(row) if row else {}
    except Exception:
        return {}


def _read_virtual_positions_v2(strategy_id: int) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    try:
        conn = ds_connect(readonly=True)
        rows = conn.execute(
            "SELECT * FROM strategy_virtual_positions_v2 WHERE strategy_id = ?",
            (strategy_id,),
        ).fetchall()
        conn.close()
        for row in rows:
            data = dict(row)
            key = str(data.get("instrument_id") or "").strip()
            if key:
                result[key] = data
    except Exception:
        pass
    return result


def _parse_json_dict(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    try:
        parsed = json.loads(raw or "{}")
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _collector_quote(collector_state: Optional[Dict[str, Any]], symbol: str) -> Dict[str, Any]:
    target = str(symbol or "").strip().upper()
    if not collector_state or not target:
        return {}
    for asset_type in ("crypto", "finance"):
        rows = (collector_state.get(asset_type, {}) or {}).get("data") or []
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_symbol = str(row.get("symbol") or "").strip().upper()
            if row_symbol == target:
                return dict(row)
    return {}


# ---------------------------------------------------------------------------
# 时间工具
# ---------------------------------------------------------------------------

def _days_hours_to_end(end_date_iso: Optional[str]) -> tuple[float, float]:
    if not end_date_iso:
        return 0.0, 0.0
    try:
        from datetime import datetime, timezone
        end = datetime.fromisoformat(end_date_iso.replace("Z", "+00:00"))
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = (end - now).total_seconds()
        if delta < 0:
            return 0.0, 0.0
        return delta / 86400.0, delta / 3600.0
    except Exception:
        return 0.0, 0.0


def _normalize_end_date_iso(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return text


def _manual_end_date_from_inputs(input_params: Dict[str, Any]) -> str:
    for key in _END_DATE_INPUT_KEYS:
        value = input_params.get(key)
        if value is not None and str(value).strip():
            return _normalize_end_date_iso(value)
    return ""


def _resolve_market_meta(strategy: Dict[str, Any]) -> Dict[str, Any]:
    strategy_id = int(strategy.get("strategy_id") or 0)
    data = {
        "end_date": strategy.get("end_date") or "",
        "question": strategy.get("question") or strategy.get("display_name") or strategy.get("strategy_name") or "",
        "market_updated_at": strategy.get("market_updated_at") or "",
    }
    if strategy_id <= 0:
        return data
    cache_key = f"{strategy_id}:{strategy.get('updated_at_utc') or ''}"
    now = time.monotonic()
    cached = _MARKET_META_CACHE.get(cache_key)
    if cached and (now - float(cached.get("ts") or 0.0)) < _MARKET_META_TTL_SECONDS:
        return dict(cached.get("data") or {})
    _MARKET_META_CACHE[cache_key] = {"ts": now, "data": data}
    return data


def _dictionary_db_path() -> Path:
    try:
        settings = load_web_settings()
        raw = str(settings.get("polymarket_dictionary_db_path") or "").strip()
        if raw:
            path = Path(raw).expanduser()
            return path if path.is_absolute() else BASE_DIR / path
    except Exception:
        pass
    return BASE_DIR / "Data" / "PolyMarketDictionary.db"


def _read_dictionary_market_meta(condition_id: str) -> Dict[str, Any]:
    condition_id = str(condition_id or "").strip()
    if not condition_id:
        return {}
    now = time.monotonic()
    cached = _DICTIONARY_META_CACHE.get(condition_id)
    if cached and (now - float(cached.get("ts") or 0.0)) < _MARKET_META_TTL_SECONDS:
        return dict(cached.get("data") or {})

    data: Dict[str, Any] = {}
    path = _dictionary_db_path()
    if path.exists():
        try:
            conn = sqlite3.connect(str(path), timeout=3.0)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """SELECT question, Subject, endDate, url, yes_token, no_token
                   FROM polyMarket_Dictionary
                   WHERE condition_id = ?
                   LIMIT 1""",
                (condition_id,),
            ).fetchone()
            conn.close()
            if row:
                d = dict(row)
                data = {
                    "question": d.get("question") or "",
                    "category": d.get("Subject") or "",
                    "end_date": d.get("endDate") or "",
                    "url": d.get("url") or "",
                    "yes_token": d.get("yes_token") or "",
                    "no_token": d.get("no_token") or "",
                }
        except Exception:
            data = {}

    _DICTIONARY_META_CACHE[condition_id] = {"ts": now, "data": data}
    return dict(data)


def _read_resolved_market_meta(condition_id: str) -> Dict[str, Any]:
    condition_id = str(condition_id or "").strip()
    if not condition_id:
        return {}
    now = time.monotonic()
    cached = _RESOLVED_META_CACHE.get(condition_id)
    if cached and (now - float(cached.get("ts") or 0.0)) < _MARKET_META_TTL_SECONDS:
        return dict(cached.get("data") or {})
    data: Dict[str, Any] = {}
    try:
        from services.polymarket_service import resolve_market_selection

        resolved = resolve_market_selection(condition_id=condition_id, limit=1)
        selected = resolved.get("selected") if isinstance(resolved, dict) else {}
        if isinstance(selected, dict) and selected:
            raw = selected.get("raw") if isinstance(selected.get("raw"), dict) else {}
            data = {
                "condition_id": selected.get("condition_id") or condition_id,
                "question": selected.get("question") or raw.get("question") or "",
                "category": selected.get("category") or "",
                "end_date": selected.get("end_date") or raw.get("endDate") or raw.get("umaEndDate") or "",
                "url": selected.get("url") or raw.get("url") or "",
                "yes_token": selected.get("yes_token") or "",
                "no_token": selected.get("no_token") or "",
            }
    except Exception:
        data = {}
    _RESOLVED_META_CACHE[condition_id] = {"ts": now, "data": data}
    return dict(data)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def build_use_data(
    strategy: Dict[str, Any],
    realtime_db_path: str,
    collector_state: Optional[Dict[str, Any]] = None,
    include_live_orderbook: bool = True,
) -> Dict[str, Any]:
    """
    组装 UseData 字典。

    strategy: strategy_registry 行 + legs 列表（来自 strategy_data_source.get_strategy）
    realtime_db_path: polymarket_realtime.db 路径
    collector_state: realtime_collector.get_state() 返回值（含 crypto/finance 数据）
    """
    use_data: Dict[str, Any] = {}
    legs: List[Dict[str, Any]] = strategy.get("legs") or []
    strategy_id: int = strategy["strategy_id"]
    input_params = _parse_input_json(strategy.get("input_json") or "{}")
    state_bundle = read_strategy_state_bundle(strategy_id) if strategy_id else {}
    schemas = get_strategy_code_schemas(strategy.get("strategy_code") or "")
    input_params = merge_schema_defaults(schemas.get("params") or {}, input_params)
    manual_end_date = _manual_end_date_from_inputs(input_params)
    macd_fast = min(60, max(2, _safe_int(input_params.get("macd_fast"), 6)))
    macd_slow = min(120, max(macd_fast + 1, _safe_int(input_params.get("macd_slow"), 13)))
    macd_signal = min(60, max(2, _safe_int(input_params.get("macd_signal"), 5)))
    runtime_state = merge_schema_defaults(schemas.get("runtime") or {}, state_bundle.get("runtime") or {})
    user_state = merge_schema_defaults(schemas.get("controls") or {}, state_bundle.get("user") or {})
    machine_state = str(
        (state_bundle.get("machine") or {}).get("state")
        or (schemas.get("state_machine") or {}).get("default")
        or "auto"
    ).strip() or "auto"
    system_state = state_bundle.get("system") or {}

    # 读取虚拟持仓
    vpos = _read_virtual_positions(strategy_id)
    open_orders = _read_virtual_open_orders(strategy_id)
    vpos_v2 = _read_virtual_positions_v2(strategy_id)
    virtual_account = _read_virtual_account(strategy_id)
    account_cash = _safe_float(virtual_account.get("cash"), 0.0)
    account_equity = _safe_float(virtual_account.get("equity"), 0.0)
    strategy_bankroll = _safe_float(strategy.get("strategy_bankroll"), 0.0)
    dynamic_budget_cap = account_equity if account_equity > 0 else account_cash
    if dynamic_budget_cap <= 0 and not virtual_account:
        dynamic_budget_cap = strategy_bankroll

    now_dt = datetime.now(timezone.utc)
    now_utc = now_dt.isoformat()
    instruments: List[Dict[str, Any]] = []

    # -----------------------------------------------------------------------
    # Tier 3 — 时间（全局，先写，后面 Tier 1 会覆盖 L0 的 Enddate）
    # -----------------------------------------------------------------------
    use_data["SchemaVersion"] = "2.0"
    use_data["LegacySchemaVersion"] = "1.0"
    use_data["NowTime"] = now_utc
    use_data["RunMode"] = str(strategy.get("mode") or strategy.get("state") or "Virtual")
    use_data["StrategyId"] = strategy_id
    use_data["StrategyName"] = strategy.get("strategy_name") or ""
    use_data["StrategyBankroll"] = strategy_bankroll
    use_data["LegCount"] = len(legs)
    use_data["Params"] = input_params
    use_data["Controls"] = user_state
    use_data["RuntimeState"] = runtime_state
    use_data["UserState"] = user_state
    use_data["StrategyState"] = {"state": machine_state}
    use_data["MachineState"] = machine_state
    use_data["SystemState"] = system_state
    # Compatibility alias for older strategies. New strategies should read
    # RuntimeState or StrategyState explicitly.
    use_data["State"] = runtime_state
    use_data["Portfolio"] = {
        "cash": account_cash,
        "equity": account_equity,
        "realized_pnl": _safe_float(virtual_account.get("realized_pnl"), 0.0),
        "unrealized_pnl": _safe_float(virtual_account.get("unrealized_pnl"), 0.0),
        "total_fees_paid": _safe_float(virtual_account.get("total_fees_paid"), 0.0),
    }
    market_meta = _resolve_market_meta(strategy)

    # -----------------------------------------------------------------------
    # Tier 1 — 盘口（per-leg）
    # -----------------------------------------------------------------------
    for leg in legs:
        leg = normalize_leg_instrument(leg)
        n = leg.get("leg_index", 0)
        leg_index_num = int(_safe_float(n, 0.0))
        leg_uid = str(leg.get("leg_uid") or f"legacy:{n}").strip()
        yes_token = leg.get("yes_token") or ""
        no_token = leg.get("no_token") or ""
        condition_id = leg.get("condition_id") or ""
        asset_class = str(leg.get("asset_class") or "polymarket_binary").strip() or "polymarket_binary"
        leg_kind = derive_leg_kind(leg)
        venue = str(leg.get("venue") or "").strip()
        symbol = str(leg.get("symbol") or "").strip().upper()
        instrument_id = derive_instrument_id(leg)
        instrument_params = _parse_json_dict(leg.get("params_json"))
        instrument_meta = _parse_json_dict(leg.get("instrument_json"))
        dictionary_meta = _read_dictionary_market_meta(condition_id)
        resolved_meta = _read_resolved_market_meta(condition_id)

        # 自动补全：有 condition_id 但缺 token 时，从 markets_state 反查
        if condition_id and (not yes_token or not no_token):
            resolved = _resolve_tokens_from_condition_id(realtime_db_path, condition_id)
            if not yes_token and "yes" in resolved:
                yes_token = resolved["yes"]
            if not no_token and "no" in resolved:
                no_token = resolved["no"]
        if not yes_token:
            yes_token = str(dictionary_meta.get("yes_token") or "").strip()
        if not no_token:
            no_token = str(dictionary_meta.get("no_token") or "").strip()
        if not yes_token:
            yes_token = str(resolved_meta.get("yes_token") or "").strip()
        if not no_token:
            no_token = str(resolved_meta.get("no_token") or "").strip()

        yes_snap = _read_market_snapshot(realtime_db_path, yes_token)
        no_snap = _read_market_snapshot(realtime_db_path, no_token)
        yes_live = _read_live_orderbook_quote(yes_token) if include_live_orderbook else {}
        no_live = _read_live_orderbook_quote(no_token) if include_live_orderbook else {}
        if yes_live:
            yes_snap = {**yes_snap, **yes_live}
        if no_live:
            no_snap = {**no_snap, **no_live}
        state_snap = _read_markets_state(realtime_db_path, yes_token)

        yes_ask = _safe_float(yes_snap.get("now_ask") or yes_snap.get("best_ask"), 0.0)
        yes_bid = _safe_float(yes_snap.get("now_bid") or yes_snap.get("best_bid"), 0.0)
        no_ask  = _safe_float(no_snap.get("now_ask")  or no_snap.get("best_ask"), 0.0)
        no_bid  = _safe_float(no_snap.get("now_bid")  or no_snap.get("best_bid"), 0.0)
        yes_last = _safe_float(yes_snap.get("last_price"), 0.0)
        no_last = _safe_float(no_snap.get("last_price"), 0.0)
        yes_macd = _read_market_macd(realtime_db_path, yes_token, macd_fast, macd_slow, macd_signal, yes_bid)
        no_macd = _read_market_macd(realtime_db_path, no_token, macd_fast, macd_slow, macd_signal, no_bid)

        # 虚拟持仓
        yes_pos = vpos.get((n, "YES"), {})
        no_pos  = vpos.get((n, "NO"),  {})
        yes_qty = _safe_float(yes_pos.get("qty"), 0.0)
        no_qty  = _safe_float(no_pos.get("qty"), 0.0)
        yes_avg = _safe_float(yes_pos.get("avg_price"), 0.0)
        no_avg  = _safe_float(no_pos.get("avg_price"), 0.0)

        end_date_iso = (
            manual_end_date
            or state_snap.get("end_date_iso")
            or state_snap.get("end_date")
            or leg.get("end_date")
            or strategy.get("end_date")
            or market_meta.get("end_date")
            or dictionary_meta.get("end_date")
            or resolved_meta.get("end_date")
            or ""
        )
        day_to_end, hour_to_end = _days_hours_to_end(end_date_iso)
        budget_cap = _safe_float(leg.get("budget_cap"), 0.0)
        configured_budget_cap = budget_cap
        # Preserve legacy dynamic sizing for single-leg strategies and L0.
        # In multi-leg strategies, non-primary budget_cap=0 means no budget.
        if budget_cap <= 0:
            budget_cap = dynamic_budget_cap if len(legs) <= 1 or leg_index_num == 0 else 0.0
        market_status = _market_status(state_snap)
        market_title = (
            state_snap.get("market_title")
            or state_snap.get("question")
            or state_snap.get("title")
            or dictionary_meta.get("question")
            or resolved_meta.get("question")
            or market_meta.get("question")
            or symbol
            or ""
        )
        generic_quote = _collector_quote(collector_state, symbol) if asset_class != "polymarket_binary" else {}
        generic_last = _safe_float(generic_quote.get("price"), 0.0)
        generic_bid = _safe_float(generic_quote.get("bid") or generic_quote.get("price"), generic_last)
        generic_ask = _safe_float(generic_quote.get("ask") or generic_quote.get("price"), generic_last)
        generic_pos = vpos_v2.get(instrument_id, {})
        generic_qty = _safe_float(generic_pos.get("qty"), 0.0)
        generic_avg = _safe_float(generic_pos.get("avg_price"), 0.0)
        yes_bid_levels = _normalize_book_levels(yes_snap.get("bid_levels") or yes_snap.get("bids"), side="bid")
        yes_ask_levels = _normalize_book_levels(yes_snap.get("ask_levels") or yes_snap.get("asks"), side="ask")
        no_bid_levels = _normalize_book_levels(no_snap.get("bid_levels") or no_snap.get("bids"), side="bid")
        no_ask_levels = _normalize_book_levels(no_snap.get("ask_levels") or no_snap.get("asks"), side="ask")
        side_rows = {
            "Yes": {
                "token": yes_token,
                "snap": yes_snap,
                "macd": yes_macd,
                "ask": yes_ask,
                "bid": yes_bid,
                "last": yes_last,
                "qty": yes_qty,
                "avg": yes_avg,
                "bid_levels": yes_bid_levels,
                "ask_levels": yes_ask_levels,
            },
            "No": {
                "token": no_token,
                "snap": no_snap,
                "macd": no_macd,
                "ask": no_ask,
                "bid": no_bid,
                "last": no_last,
                "qty": no_qty,
                "avg": no_avg,
                "bid_levels": no_bid_levels,
                "ask_levels": no_ask_levels,
            },
        }

        use_data[f"L{n}_ConditionId"] = leg.get("condition_id") or ""
        use_data[f"L{n}_LegUid"] = leg_uid
        use_data[f"L{n}_LegKind"] = leg_kind
        use_data[f"L{n}_AssetClass"] = asset_class
        use_data[f"L{n}_Venue"] = venue
        use_data[f"L{n}_Symbol"] = symbol
        use_data[f"L{n}_InstrumentId"] = instrument_id
        use_data[f"L{n}_MarketTitle"] = market_title
        use_data[f"L{n}_MarketStatus"] = market_status
        use_data[f"L{n}_MarketCategory"] = str(state_snap.get("category") or "").strip()
        use_data[f"L{n}_BudgetCap"] = budget_cap
        use_data[f"L{n}_ConfiguredBudgetCap"] = configured_budget_cap
        use_data[f"L{n}_BudgetMin"] = 0.0
        use_data[f"L{n}_EndTime"] = end_date_iso
        use_data[f"L{n}_DayToEnd"] = day_to_end
        use_data[f"L{n}_HourToEnd"] = hour_to_end
        use_data[f"L{n}_AskPrice"] = generic_ask
        use_data[f"L{n}_BidPrice"] = generic_bid
        use_data[f"L{n}_LastPrice"] = generic_last
        use_data[f"L{n}_PositionQty"] = generic_qty
        use_data[f"L{n}_PositionAvgPrice"] = generic_avg
        use_data[f"L{n}_PositionCost"] = generic_qty * generic_avg

        for side_name, side_data in side_rows.items():
            prefix = f"L{n}_{side_name}"
            side_qty = _safe_float(side_data["qty"], 0.0)
            side_avg = _safe_float(side_data["avg"], 0.0)
            side_bid = _safe_float(side_data["bid"], 0.0)
            side_ask = _safe_float(side_data["ask"], 0.0)
            side_snap = side_data["snap"]
            bid_levels = side_data.get("bid_levels") if isinstance(side_data.get("bid_levels"), list) else []
            ask_levels = side_data.get("ask_levels") if isinstance(side_data.get("ask_levels"), list) else []
            bid_depth_qty = _safe_float(side_snap.get("bid_depth_qty"), _levels_qty(bid_levels))
            ask_depth_qty = _safe_float(side_snap.get("ask_depth_qty"), _levels_qty(ask_levels))
            bid_depth_notional = _safe_float(side_snap.get("bid_depth_notional"), _levels_notional(bid_levels))
            ask_depth_notional = _safe_float(side_snap.get("ask_depth_notional"), _levels_notional(ask_levels))
            side_macd = side_data["macd"] if isinstance(side_data.get("macd"), dict) else {}
            position_cost = side_qty * side_avg
            open_info = open_orders.get((leg_uid, int(n), side_name.upper()), {})
            open_buy_qty = _safe_float(open_info.get("open_buy_qty"), 0.0)
            open_sell_qty = _safe_float(open_info.get("open_sell_qty"), 0.0)
            open_reduce_sell_qty = _safe_float(open_info.get("open_reduce_sell_qty"), 0.0)
            use_data[f"{prefix}_TokenId"] = side_data["token"]
            use_data[f"{prefix}_AskPrice"] = side_ask
            use_data[f"{prefix}_BidPrice"] = side_bid
            use_data[f"{prefix}_LastPrice"] = _safe_float(side_data["last"], 0.0)
            use_data[f"{prefix}_BestAskQty"] = _safe_float(side_snap.get("best_ask_qty"), 0.0)
            use_data[f"{prefix}_BestBidQty"] = _safe_float(side_snap.get("best_bid_qty"), 0.0)
            use_data[f"{prefix}_AskLevels"] = ask_levels
            use_data[f"{prefix}_BidLevels"] = bid_levels
            use_data[f"{prefix}_AskDepthQty"] = ask_depth_qty
            use_data[f"{prefix}_BidDepthQty"] = bid_depth_qty
            use_data[f"{prefix}_AskDepthNotional"] = ask_depth_notional
            use_data[f"{prefix}_BidDepthNotional"] = bid_depth_notional
            use_data[f"{prefix}_PositionQty"] = side_qty
            use_data[f"{prefix}_PositionAvgPrice"] = side_avg
            use_data[f"{prefix}_PositionCost"] = position_cost
            use_data[f"{prefix}_PositionValueBid"] = side_qty * side_bid
            use_data[f"{prefix}_OpenBuyQty"] = open_buy_qty
            use_data[f"{prefix}_OpenSellQty"] = open_sell_qty
            use_data[f"{prefix}_OpenReduceSellQty"] = open_reduce_sell_qty
            use_data[f"{prefix}_AvailableSellQty"] = max(0.0, side_qty - open_reduce_sell_qty)
            use_data[f"{prefix}_TakeProfitOrderPrice"] = open_info.get("take_profit_order_price")
            use_data[f"{prefix}_TakeProfitOrderQty"] = _safe_float(open_info.get("take_profit_order_qty"), 0.0)
            use_data[f"{prefix}_TakeProfitOrderStatus"] = str(open_info.get("take_profit_order_status") or "")
            use_data[f"{prefix}_DataStatus"] = _data_status(side_ask, side_bid, side_snap)
            use_data[f"{prefix}_LastUpdateAgeSec"] = _snapshot_age_seconds(side_snap, now_dt)
            use_data[f"{prefix}_MACD"] = side_macd.get("macd")
            use_data[f"{prefix}_MACDSignal"] = side_macd.get("macd_signal")
            use_data[f"{prefix}_MACDHist"] = side_macd.get("macd_hist")
            use_data[f"{prefix}_MACDHistPrev"] = side_macd.get("macd_hist_prev")
            use_data[f"{prefix}_MACDHistSlope"] = side_macd.get("macd_hist_slope")
            use_data[f"{prefix}_MACDSampleCount"] = side_macd.get("macd_sample_count", 0)

        suffix = f"_L{n}"
        use_data[f"Yes_now_ask{suffix}"]           = yes_ask
        use_data[f"Yes_now_bid{suffix}"]           = yes_bid
        use_data[f"No_now_ask{suffix}"]            = no_ask
        use_data[f"No_now_bid{suffix}"]            = no_bid
        use_data[f"Yes_now_Qty{suffix}"]           = yes_qty
        use_data[f"No_now_Qty{suffix}"]            = no_qty
        use_data[f"Yes_now_avgPrice{suffix}"]      = yes_avg
        use_data[f"No_now_avgPrice{suffix}"]       = no_avg
        yes_open = open_orders.get((leg_uid, int(n), "YES"), {})
        no_open = open_orders.get((leg_uid, int(n), "NO"), {})
        use_data[f"Yes_OpenBuyOrdersQty{suffix}"]  = _safe_float(yes_open.get("open_buy_qty"), 0.0)
        use_data[f"Yes_OpenSellOrdersQty{suffix}"] = _safe_float(yes_open.get("open_sell_qty"), 0.0)
        use_data[f"No_OpenBuyOrdersQty{suffix}"]   = _safe_float(no_open.get("open_buy_qty"), 0.0)
        use_data[f"No_OpenSellOrdersQty{suffix}"]  = _safe_float(no_open.get("open_sell_qty"), 0.0)
        use_data[f"Yes_AskLevels{suffix}"]          = yes_ask_levels
        use_data[f"Yes_BidLevels{suffix}"]          = yes_bid_levels
        use_data[f"No_AskLevels{suffix}"]           = no_ask_levels
        use_data[f"No_BidLevels{suffix}"]           = no_bid_levels
        use_data[f"Yes_AskDepthQty{suffix}"]        = _levels_qty(yes_ask_levels)
        use_data[f"Yes_BidDepthQty{suffix}"]        = _levels_qty(yes_bid_levels)
        use_data[f"No_AskDepthQty{suffix}"]         = _levels_qty(no_ask_levels)
        use_data[f"No_BidDepthQty{suffix}"]         = _levels_qty(no_bid_levels)
        use_data[f"Yes_depth_ask_1c_usd{suffix}"]  = 0.0
        use_data[f"Yes_depth_bid_1c_usd{suffix}"]  = 0.0
        use_data[f"No_depth_ask_1c_usd{suffix}"]   = 0.0
        use_data[f"No_depth_bid_1c_usd{suffix}"]   = 0.0
        # Yes_Now_Pos / No_Now_Pos: 仓位成本占 BudgetCap 的比例（0~1），与 SETPOS pct 语义一致
        yes_pos_pct = (yes_qty * yes_avg / budget_cap) if budget_cap > 0 else 0.0
        no_pos_pct = (no_qty * no_avg / budget_cap) if budget_cap > 0 else 0.0
        use_data[f"Yes_Now_Pos{suffix}"]           = yes_pos_pct
        use_data[f"No_Now_Pos{suffix}"]            = no_pos_pct
        use_data[f"Yes_Now_CostPos{suffix}"]       = yes_qty * yes_avg
        use_data[f"No_Now_CostPos{suffix}"]        = no_qty * no_avg
        use_data[f"BudgetCap{suffix}"]             = budget_cap
        use_data[f"ConfiguredBudgetCap{suffix}"]   = configured_budget_cap
        use_data[f"Enddate{suffix}"]               = end_date_iso
        use_data[f"day_to_end{suffix}"]            = day_to_end
        use_data[f"hour_to_end{suffix}"]           = hour_to_end
        use_data[f"Yes_MACD{suffix}"]              = yes_macd.get("macd")
        use_data[f"Yes_MACDSignal{suffix}"]        = yes_macd.get("macd_signal")
        use_data[f"Yes_MACDHist{suffix}"]          = yes_macd.get("macd_hist")
        use_data[f"Yes_MACDHistPrev{suffix}"]      = yes_macd.get("macd_hist_prev")
        use_data[f"Yes_MACDHistSlope{suffix}"]     = yes_macd.get("macd_hist_slope")
        use_data[f"No_MACD{suffix}"]               = no_macd.get("macd")
        use_data[f"No_MACDSignal{suffix}"]         = no_macd.get("macd_signal")
        use_data[f"No_MACDHist{suffix}"]           = no_macd.get("macd_hist")
        use_data[f"No_MACDHistPrev{suffix}"]       = no_macd.get("macd_hist_prev")
        use_data[f"No_MACDHistSlope{suffix}"]      = no_macd.get("macd_hist_slope")

        instruments.append({
            "index": int(n),
            "leg_index": int(n),
            "leg_uid": leg_uid,
            "instrument_id": instrument_id,
            "leg_kind": leg_kind,
            "asset_class": asset_class,
            "venue": venue,
            "symbol": symbol,
            "budget_cap": budget_cap,
            "configured_budget_cap": configured_budget_cap,
            "params": instrument_params,
            "instrument": instrument_meta,
            "market": {
                "condition_id": condition_id,
                "title": market_title,
                "status": market_status,
                "category": str(state_snap.get("category") or "").strip(),
                "end_time": end_date_iso,
                "day_to_end": day_to_end,
                "hour_to_end": hour_to_end,
            },
            "quote": {
                "bid": generic_bid,
                "ask": generic_ask,
                "last": generic_last,
                "yes_bid": yes_bid,
                "yes_ask": yes_ask,
                "yes_last": yes_last,
                "no_bid": no_bid,
                "no_ask": no_ask,
                "no_last": no_last,
            },
            "orderbook": {
                "yes": {
                    "bids": yes_bid_levels,
                    "asks": yes_ask_levels,
                    "bid_depth_qty": _levels_qty(yes_bid_levels),
                    "ask_depth_qty": _levels_qty(yes_ask_levels),
                    "bid_depth_notional": _levels_notional(yes_bid_levels),
                    "ask_depth_notional": _levels_notional(yes_ask_levels),
                },
                "no": {
                    "bids": no_bid_levels,
                    "asks": no_ask_levels,
                    "bid_depth_qty": _levels_qty(no_bid_levels),
                    "ask_depth_qty": _levels_qty(no_ask_levels),
                    "bid_depth_notional": _levels_notional(no_bid_levels),
                    "ask_depth_notional": _levels_notional(no_ask_levels),
                },
            },
            "position": {
                "qty": generic_qty,
                "avg_price": generic_avg,
                "cost": generic_qty * generic_avg,
                "yes_qty": yes_qty,
                "yes_avg_price": yes_avg,
                "no_qty": no_qty,
                "no_avg_price": no_avg,
            },
            "tokens": {
                "yes": yes_token,
                "no": no_token,
            },
        })

        # L0 同时作为无后缀别名（向后兼容单腿策略）
        if n == 0:
            use_data["LegUid"] = leg_uid
            use_data["LegKind"] = leg_kind
            use_data["Yes_AskPrice"]        = yes_ask
            use_data["Yes_BidPrice"]        = yes_bid
            use_data["No_AskPrice"]         = no_ask
            use_data["No_BidPrice"]         = no_bid
            use_data["Yes_PositionQty"]     = yes_qty
            use_data["No_PositionQty"]      = no_qty
            use_data["Yes_PositionAvgPrice"] = yes_avg
            use_data["No_PositionAvgPrice"]  = no_avg
            use_data["Yes_now_ask"]           = yes_ask
            use_data["Yes_now_bid"]           = yes_bid
            use_data["No_now_ask"]            = no_ask
            use_data["No_now_bid"]            = no_bid
            use_data["Yes_now_Qty"]           = yes_qty
            use_data["No_now_Qty"]            = no_qty
            use_data["Yes_now_avgPrice"]      = yes_avg
            use_data["No_now_avgPrice"]       = no_avg
            use_data["Yes_OpenBuyOrdersQty"]  = _safe_float(yes_open.get("open_buy_qty"), 0.0)
            use_data["Yes_OpenSellOrdersQty"] = _safe_float(yes_open.get("open_sell_qty"), 0.0)
            use_data["No_OpenBuyOrdersQty"]   = _safe_float(no_open.get("open_buy_qty"), 0.0)
            use_data["No_OpenSellOrdersQty"]  = _safe_float(no_open.get("open_sell_qty"), 0.0)
            use_data["Yes_AskLevels"]          = yes_ask_levels
            use_data["Yes_BidLevels"]          = yes_bid_levels
            use_data["No_AskLevels"]           = no_ask_levels
            use_data["No_BidLevels"]           = no_bid_levels
            use_data["Yes_AskDepthQty"]        = _levels_qty(yes_ask_levels)
            use_data["Yes_BidDepthQty"]        = _levels_qty(yes_bid_levels)
            use_data["No_AskDepthQty"]         = _levels_qty(no_ask_levels)
            use_data["No_BidDepthQty"]         = _levels_qty(no_bid_levels)
            use_data["Yes_Now_Pos"]           = yes_pos_pct
            use_data["No_Now_Pos"]            = no_pos_pct
            use_data["Yes_Now_CostPos"]       = yes_qty * yes_avg
            use_data["No_Now_CostPos"]        = no_qty * no_avg
            use_data["BudgetCap"]             = budget_cap
            use_data["ConfiguredBudgetCap"]   = configured_budget_cap
            use_data["Enddate"]               = end_date_iso
            use_data["day_to_end"]            = day_to_end
            use_data["hour_to_end"]           = hour_to_end
            use_data["Yes_MACD"]              = yes_macd.get("macd")
            use_data["Yes_MACDSignal"]        = yes_macd.get("macd_signal")
            use_data["Yes_MACDHist"]          = yes_macd.get("macd_hist")
            use_data["Yes_MACDHistPrev"]      = yes_macd.get("macd_hist_prev")
            use_data["Yes_MACDHistSlope"]     = yes_macd.get("macd_hist_slope")
            use_data["No_MACD"]               = no_macd.get("macd")
            use_data["No_MACDSignal"]         = no_macd.get("macd_signal")
            use_data["No_MACDHist"]           = no_macd.get("macd_hist")
            use_data["No_MACDHistPrev"]       = no_macd.get("macd_hist_prev")
            use_data["No_MACDHistSlope"]      = no_macd.get("macd_hist_slope")

    # -----------------------------------------------------------------------
    # Tier 2 — 预算派生（向后兼容旧策略代码，仅 L0）
    # -----------------------------------------------------------------------
    use_data["Instruments"] = instruments

    primary_leg = next((lg for lg in legs if lg.get("leg_index", 0) == 0), legs[0] if legs else {})
    primary_cap = primary_leg.get("budget_cap", 0.0) if primary_leg else 0.0
    use_data["Yes_Max_BudgetCap"] = primary_cap
    use_data["No_Max_BudgetCap"]  = primary_cap
    use_data["Yes_Min_BudgetCap"] = 0.0
    use_data["No_Min_BudgetCap"]  = 0.0

    saved_start_day = input_params.get("start_day")
    if saved_start_day not in (None, ""):
        start_day = saved_start_day
    else:
        start_day = use_data.get("day_to_end")
    if start_day not in (None, ""):
        use_data["start_day"] = start_day
        use_data["StartDay"] = start_day

    # -----------------------------------------------------------------------
    # Tier 4 — 外部行情（realtime_collector 注入）
    # -----------------------------------------------------------------------
    if collector_state:
        for asset_type in ("crypto", "finance"):
            asset_data = collector_state.get(asset_type, {})
            rows = asset_data.get("data") or []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                symbol = str(row.get("symbol") or "").strip().upper()
                if not symbol:
                    continue
                if row.get("price") is not None:
                    use_data[f"Price_{symbol}"] = row["price"]
                if row.get("market_cap_usd") is not None:
                    use_data[f"McapUsd_{symbol}"] = row["market_cap_usd"]
                if row.get("vol_24h_quote") is not None:
                    use_data[f"Vol24hUsd_{symbol}"] = row["vol_24h_quote"]
                if row.get("change_percent") is not None:
                    use_data[f"Change24h_{symbol}"] = row["change_percent"]
                if row.get("fdv_usd") is not None:
                    use_data[f"FdvUsd_{symbol}"] = row["fdv_usd"]

    # -----------------------------------------------------------------------
    # Tier 5 — 用户自定义参数（input_json，最低优先级，不覆盖已有键）
    # -----------------------------------------------------------------------
    for k, v in input_params.items():
        if k not in use_data:
            use_data[k] = v

    return use_data
