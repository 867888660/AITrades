from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from services.config_loader import BASE_DIR, get_market_realtime_db_path, load_web_settings
from services.polymarket_service import (
    fetch_strategy_detail,
    get_strategy_chart_capabilities,
    get_strategy_chart_defaults,
    get_strategy_leg_snapshots,
    resolve_market_selection,
)
from services import strategy_data_source
from services.history_data_service import (
    DEFAULT_BINANCE_INTERVAL,
    HISTORY_DB_PATH,
    get_backtest_metric_catalog,
    get_backtest_run,
    get_backtest_workspace_strategy,
)
from services.strategy_event_service import list_strategy_events
from services.strategy_display import preferred_leg_display_name
from services.strategy_stats_store import get_strategy_stats_db_path
from services.strategy_metric_store import load_metric_events
from services.virtual_liquidation import liquidate_position, normalize_book_levels


_SERIES_CONFIG: Dict[str, Dict[str, str]] = {
    "yes_bid": {"label": "Yes Bid", "panel": "main", "render": "line", "unit": "price", "category": "price"},
    "yes_ask": {"label": "Yes Ask", "panel": "main", "render": "line", "unit": "price", "category": "price"},
    "no_bid": {"label": "No Bid", "panel": "main", "render": "line", "unit": "price", "category": "price"},
    "no_ask": {"label": "No Ask", "panel": "main", "render": "line", "unit": "price", "category": "price"},
    "yes_mid": {"label": "Yes Mid", "panel": "main", "render": "line", "unit": "price", "category": "price"},
    "no_mid": {"label": "No Mid", "panel": "main", "render": "line", "unit": "price", "category": "price"},
    "yes_position": {"label": "Yes Position", "panel": "positions", "render": "step", "unit": "ratio", "category": "position"},
    "no_position": {"label": "No Position", "panel": "positions", "render": "step", "unit": "ratio", "category": "position"},
    "position": {"label": "Position", "panel": "positions", "render": "step", "unit": "qty", "category": "position"},
    "yes_qty": {"label": "Yes Qty", "panel": "sizes", "render": "step", "unit": "qty", "category": "size"},
    "no_qty": {"label": "No Qty", "panel": "sizes", "render": "step", "unit": "qty", "category": "size"},
    "qty": {"label": "Qty", "panel": "sizes", "render": "step", "unit": "qty", "category": "size"},
    "yes_avg": {"label": "Yes Avg", "panel": "averages", "render": "line", "unit": "price", "category": "average"},
    "no_avg": {"label": "No Avg", "panel": "averages", "render": "line", "unit": "price", "category": "average"},
    "avg": {"label": "Avg", "panel": "averages", "render": "line", "unit": "price", "category": "average"},
    "strategy_pnl": {"label": "Strategy PnL", "panel": "capital", "render": "line", "unit": "currency", "category": "capital"},
    "strategy_bankroll": {"label": "Strategy Bankroll", "panel": "capital", "render": "line", "unit": "currency", "category": "capital"},
    "initial_capital": {"label": "Initial Capital", "panel": "capital", "render": "line", "unit": "currency", "category": "capital"},
    "profit_roll_ratio": {"label": "Profit Roll Ratio", "panel": "capital", "render": "line", "unit": "ratio", "category": "capital"},
    "realized_profit": {"label": "Realized Profit", "panel": "capital", "render": "line", "unit": "currency", "category": "capital"},
}
_PANEL_TITLES = {
    "main": "Prices",
    "positions": "Positions",
    "sizes": "Sizes",
    "averages": "Average Cost",
    "capital": "Capital",
    "market_price": "Market Price",
    "market_mcap": "Market Cap",
    "market_volume": "Market Volume",
    "market_supply": "Market Supply",
    "indicator_macd": "MACD",
    "metric_values": "Strategy Metrics",
    "leg_positions": "Leg Positions",
    "leg_position_metrics": "Leg-Cap Position Metrics",
    "leg_edges": "Leg Edge / Probability",
    "leg_rank": "Leg Rank",
    "metric_states": "State Lanes",
    "backtest_metrics": "Backtest Metrics",
    "backtest_states": "Backtest State",
    "volume": "Volume",
}
_BACKTEST_RUN_CACHE_TTL_SECONDS = 20.0
_BACKTEST_RUN_CACHE_MAX = 8
_BACKTEST_RUN_CACHE_LOCK = threading.Lock()
_BACKTEST_RUN_CACHE: Dict[int, Tuple[float, Dict[str, Any]]] = {}
_BACKTEST_SERIES_CONFIG = [
    {
        "key": "backtest_position_ratio",
        "label": "Backtest Position",
        "panel": "positions",
        "render": "step",
        "unit": "ratio",
        "category": "backtest_position",
        "source_label": "Backtest Equity",
        "source_detail": "position_ratio from equity meta",
        "removable": True,
    },
    {
        "key": "backtest_equity",
        "label": "Backtest Equity",
        "panel": "capital",
        "render": "line",
        "unit": "currency",
        "category": "backtest_capital",
        "source_label": "Backtest Equity",
        "source_detail": "equity curve",
        "removable": True,
    },
    {
        "key": "backtest_pnl",
        "label": "Backtest PnL",
        "panel": "capital",
        "render": "line",
        "unit": "currency",
        "category": "backtest_capital",
        "source_label": "Backtest Equity",
        "source_detail": "pnl curve",
        "removable": True,
        "style": {"visible": False, "line_type": "solid", "width": 1.8, "show_symbol": False},
    },
    {
        "key": "backtest_return",
        "label": "Backtest Return",
        "panel": "backtest_metrics",
        "render": "line",
        "unit": "ratio",
        "category": "backtest_metric",
        "source_label": "Backtest Metrics",
        "source_detail": "equity / initial equity - 1",
        "removable": True,
    },
    {
        "key": "backtest_drawdown",
        "label": "Backtest Drawdown",
        "panel": "backtest_metrics",
        "render": "line",
        "unit": "ratio",
        "category": "backtest_metric",
        "source_label": "Backtest Metrics",
        "source_detail": "equity / running peak - 1",
        "removable": True,
    },
]
_PRICE_DETAIL_KEYS = {
    "yes_bid",
    "yes_ask",
    "yes_bid_levels",
    "yes_ask_levels",
    "no_bid",
    "no_ask",
    "no_bid_levels",
    "no_ask_levels",
    "yes_last_price",
    "no_last_price",
    "yes_mid",
    "no_mid",
}
_LEG_POSITION_DETAIL_KEYS = {
    "yes_position",
    "no_position",
    "position",
    "yes_qty",
    "no_qty",
    "qty",
    "yes_avg",
    "no_avg",
    "avg",
}
_STATS_DETAIL_KEYS = {
    "yes_qty",
    "no_qty",
    "yes_avg",
    "no_avg",
    "yes_position",
    "no_position",
    "strategy_pnl",
    "strategy_bankroll",
    "initial_capital",
    "profit_roll_ratio",
    "realized_profit",
}
_PRICE_FORWARD_FILL_MAX_SECONDS = 300
_DEFAULT_CHART_METRIC_LIMIT = 16


def _is_price_field(key: str) -> bool:
    text = str(key or "")
    return (
        text in _PRICE_DETAIL_KEYS
        or text.endswith("_open")
        or text.endswith("_high")
        or text.endswith("_low")
        or text.endswith("_close")
        or text.endswith("_ohlc")
        or text.endswith("_yes_bid")
        or text.endswith("_yes_ask")
        or text.endswith("_yes_bid_levels")
        or text.endswith("_yes_ask_levels")
        or text.endswith("_yes_mid")
        or text.endswith("_yes_last_price")
        or text.endswith("_no_bid")
        or text.endswith("_no_ask")
        or text.endswith("_no_bid_levels")
        or text.endswith("_no_ask_levels")
        or text.endswith("_no_mid")
        or text.endswith("_no_last_price")
    )
_OVERLAY_FIELD_SPECS: Dict[str, Dict[str, Dict[str, str]]] = {
    "crypto": {
        "price": {"column": "Price", "label": "Price", "panel": "market_price", "unit": "price"},
        "mcap_usd": {"column": "McapUsd", "label": "McapUsd", "panel": "market_mcap", "unit": "compact_currency"},
        "fdv_usd": {"column": "FdvUsd", "label": "FdvUsd", "panel": "market_mcap", "unit": "compact_currency"},
        "vol_24h_base": {"column": "Vol24hBase", "label": "Vol24hBase", "panel": "market_volume", "unit": "compact_number"},
        "vol_24h_quote": {"column": "Vol24hQuote", "label": "Vol24hQuote", "panel": "market_volume", "unit": "compact_currency"},
        "circ_supply": {"column": "CircSupply", "label": "CircSupply", "panel": "market_supply", "unit": "compact_number"},
        "total_supply": {"column": "TotalSupply", "label": "TotalSupply", "panel": "market_supply", "unit": "compact_number"},
        "max_supply": {"column": "MaxSupply", "label": "MaxSupply", "panel": "market_supply", "unit": "compact_number"},
    },
    "finance": {
        "price": {"column": "Price", "label": "Price", "panel": "market_price", "unit": "price"},
        "mcap_usd": {"column": "McapUsd", "label": "McapUsd", "panel": "market_mcap", "unit": "compact_currency"},
    },
}
_REALTIME_INDEX_LOCK = threading.Lock()
_REALTIME_INDEXED_PATHS: set[str] = set()


def _ensure_realtime_chart_indexes(conn: sqlite3.Connection, db_path: Path) -> None:
    cache_key = str(db_path)
    if cache_key in _REALTIME_INDEXED_PATHS:
        return
    with _REALTIME_INDEX_LOCK:
        if cache_key in _REALTIME_INDEXED_PATHS:
            return
        try:
            conn.execute('CREATE INDEX IF NOT EXISTS idx_deltas_cond_ts ON market_deltas(condition_id, timestamp)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_deltas_token_ts ON market_deltas(clobTokenId, timestamp)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_state_token_time ON markets_state(clobTokenId, updated_at_utc)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_state_condition ON markets_state(condition_id)')
            conn.commit()
        except sqlite3.Error:
            pass
        _REALTIME_INDEXED_PATHS.add(cache_key)


def _parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _strategy_end_at(detail: Dict[str, Any]) -> datetime | None:
    raw = detail.get("raw") if isinstance(detail.get("raw"), dict) else {}
    editable = detail.get("editable") if isinstance(detail.get("editable"), dict) else {}
    return _parse_iso(
        detail.get("end_date")
        or raw.get("Enddate")
        or raw.get("end_date")
        or editable.get("Enddate")
        or editable.get("end_date")
    )


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _iso_utc_exact(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_binary_quote(value: Any) -> float | None:
    """Treat zero/invalid binary-market quotes as missing chart data."""
    num = _safe_float(value)
    if num is None or not math.isfinite(num) or num <= 0 or num > 1:
        return None
    return num


def _orderbook_edge_price(levels: Any, *, side: str) -> float | None:
    if not isinstance(levels, list) or not levels:
        return None
    prices: List[float] = []
    for level in levels:
        price = None
        if isinstance(level, dict):
            for key in ("price", "px", "rate", "value"):
                price = _safe_float(level.get(key))
                if price is not None:
                    break
        elif isinstance(level, (list, tuple)) and level:
            price = _safe_float(level[0])
        else:
            price = _safe_float(level)
        if price is not None:
            prices.append(price)
    if not prices:
        return None
    return max(prices) if side == "bid" else min(prices)


def _clamp_binary_price(value: float | None) -> float | None:
    if value is None:
        return None
    return max(0.0, min(1.0, value))


def _fill_binary_complements(sample: Dict[str, Any], *, infer_bid_ask: bool = False) -> None:
    yes_bid = _safe_float(sample.get("yes_bid"))
    yes_ask = _safe_float(sample.get("yes_ask"))
    no_bid = _safe_float(sample.get("no_bid"))
    no_ask = _safe_float(sample.get("no_ask"))
    yes_last = _safe_float(sample.get("yes_last_price"))
    no_last = _safe_float(sample.get("no_last_price"))

    if infer_bid_ask:
        if yes_bid is not None and no_ask is None:
            no_ask = _clamp_binary_price(1.0 - yes_bid)
            sample["no_ask"] = no_ask
        if yes_ask is not None and no_bid is None:
            no_bid = _clamp_binary_price(1.0 - yes_ask)
            sample["no_bid"] = no_bid
        if no_bid is not None and yes_ask is None:
            yes_ask = _clamp_binary_price(1.0 - no_bid)
            sample["yes_ask"] = yes_ask
        if no_ask is not None and yes_bid is None:
            yes_bid = _clamp_binary_price(1.0 - no_ask)
            sample["yes_bid"] = yes_bid

    if yes_last is not None and no_last is None:
        sample["no_last_price"] = _clamp_binary_price(1.0 - yes_last)
    if no_last is not None and yes_last is None:
        sample["yes_last_price"] = _clamp_binary_price(1.0 - no_last)


def _mid_from_bid_ask_or_last(bid: float | None, ask: float | None, last_price: float | None) -> float | None:
    if bid is not None and ask is not None:
        return (bid + ask) / 2.0
    if last_price is not None:
        return last_price
    if bid is not None:
        return bid
    if ask is not None:
        return ask
    return None


def _attach_binary_mid_prices(sample: Dict[str, Any]) -> None:
    yes_mid = _mid_from_bid_ask_or_last(
        _safe_float(sample.get("yes_bid")),
        _safe_float(sample.get("yes_ask")),
        _safe_float(sample.get("yes_last_price")),
    )
    no_mid = _mid_from_bid_ask_or_last(
        _safe_float(sample.get("no_bid")),
        _safe_float(sample.get("no_ask")),
        _safe_float(sample.get("no_last_price")),
    )
    if yes_mid is not None:
        sample["yes_mid"] = _clamp_binary_price(yes_mid)
    if no_mid is not None:
        sample["no_mid"] = _clamp_binary_price(no_mid)


def _drop_crossed_bid_ask(sample: Dict[str, Any]) -> None:
    """Discard invalid bid/ask pairs before they can affect chart PNL."""
    for prefix in ("yes", "no"):
        bid_key = f"{prefix}_bid"
        ask_key = f"{prefix}_ask"
        bid = _safe_float(sample.get(bid_key))
        ask = _safe_float(sample.get(ask_key))
        if bid is not None and ask is not None and bid > ask:
            sample.pop(bid_key, None)
            sample.pop(ask_key, None)


def _overlay_sample_maps(*sample_maps: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    output: Dict[str, Dict[str, Any]] = {}
    for sample_map in sample_maps:
        for ts, payload in sample_map.items():
            target = output.setdefault(ts, {"ts": ts})
            target.update({key: value for key, value in payload.items() if key != "ts"})
    return output


def _row_keys(row: sqlite3.Row | Dict[str, Any]) -> set[str]:
    try:
        return set(row.keys())  # type: ignore[union-attr]
    except Exception:
        return set()


def _delta_after_from_row(row: sqlite3.Row) -> Dict[str, Any]:
    """优先使用 market_deltas 表内展开列，否则解析 payload_json。"""
    keys = _row_keys(row)
    if keys & {"now_bid", "now_ask", "best_bid", "best_ask", "last_price"}:
        nb = _safe_float(row["now_bid"]) if "now_bid" in keys else None
        na = _safe_float(row["now_ask"]) if "now_ask" in keys else None
        bb = _safe_float(row["best_bid"]) if "best_bid" in keys else None
        ba = _safe_float(row["best_ask"]) if "best_ask" in keys else None
        lp = _safe_float(row["last_price"]) if "last_price" in keys else None
        if any(v is not None for v in (nb, na, bb, ba, lp)):
            return {
                "now_bid": nb if nb is not None else bb,
                "now_ask": na if na is not None else ba,
                "best_bid": bb if bb is not None else nb,
                "best_ask": ba if ba is not None else na,
                "last_price": lp,
            }
    payload = _parse_json(row["payload_json"])
    after = payload.get("after") if isinstance(payload.get("after"), dict) else {}
    if not after:
        after = payload
    if isinstance(after, dict):
        best_bid = _safe_float(after.get("best_bid"))
        best_ask = _safe_float(after.get("best_ask"))
        now_bid = _safe_float(after.get("now_bid"))
        now_ask = _safe_float(after.get("now_ask"))
        if best_bid is None and now_bid is None:
            best_bid = _orderbook_edge_price(after.get("bids"), side="bid")
        if best_ask is None and now_ask is None:
            best_ask = _orderbook_edge_price(after.get("asks"), side="ask")
        if any(value is not None for value in (best_bid, best_ask, now_bid, now_ask)):
            return {
                **after,
                "now_bid": now_bid if now_bid is not None else best_bid,
                "now_ask": now_ask if now_ask is not None else best_ask,
                "best_bid": best_bid if best_bid is not None else now_bid,
                "best_ask": best_ask if best_ask is not None else now_ask,
            }
    return after if isinstance(after, dict) else {}


def _markets_state_flat_payload(row: sqlite3.Row, state_columns: set[str]) -> Dict[str, Any] | None:
    """若 markets_state 已有展开列且非空，则直接用于报价；否则返回 None 走 JSON。"""
    need = {"now_bid", "now_ask", "best_bid", "best_ask", "last_price"} & state_columns
    if not need:
        return None
    nb = _safe_float(row["now_bid"]) if "now_bid" in state_columns else None
    na = _safe_float(row["now_ask"]) if "now_ask" in state_columns else None
    bb = _safe_float(row["best_bid"]) if "best_bid" in state_columns else None
    ba = _safe_float(row["best_ask"]) if "best_ask" in state_columns else None
    lp = _safe_float(row["last_price"]) if "last_price" in state_columns else None
    if not any(v is not None for v in (nb, na, bb, ba, lp)):
        return None
    return {
        "now_bid": nb if nb is not None else bb,
        "now_ask": na if na is not None else ba,
        "best_bid": bb if bb is not None else nb,
        "best_ask": ba if ba is not None else na,
        "last_price": lp,
    }


def _parse_json(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_json_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _parse_seconds(text: str) -> int:
    value = str(text or "").strip().lower()
    if not value:
        return 5
    if value.endswith("ms"):
        return max(1, int(float(value[:-2]) / 1000))
    suffix = value[-1]
    number_part = value[:-1] if suffix.isalpha() else value
    try:
        number = max(1, int(float(number_part)))
    except ValueError:
        return 5
    if suffix == "m":
        return number * 60
    if suffix == "h":
        return number * 3600
    if suffix == "d":
        return number * 86400
    return number


def _resolve_time_bounds(args: Dict[str, Any], defaults: Dict[str, Any]) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    to_dt = _parse_iso(args.get("to")) or now
    from_dt = _parse_iso(args.get("from"))
    if from_dt is None:
        range_text = str(args.get("range") or defaults.get("range") or "1d").strip().lower()
        if range_text == "ytd":
            from_dt = datetime(to_dt.year, 1, 1, tzinfo=timezone.utc)
        elif range_text == "all":
            from_dt = datetime(2015, 1, 1, tzinfo=timezone.utc)
        elif range_text.endswith("mo"):
            from_dt = to_dt - timedelta(days=30 * max(1, int(range_text[:-2] or 1)))
        elif range_text.endswith("w"):
            from_dt = to_dt - timedelta(days=7 * max(1, int(range_text[:-1] or 1)))
        elif range_text.endswith("d"):
            from_dt = to_dt - timedelta(days=max(1, int(range_text[:-1] or 1)))
        elif range_text.endswith("h"):
            from_dt = to_dt - timedelta(hours=max(1, int(range_text[:-1] or 24)))
        elif range_text.endswith("m"):
            from_dt = to_dt - timedelta(minutes=max(1, int(range_text[:-1] or 15)))
        else:
            from_dt = to_dt - timedelta(hours=24)
    if from_dt > to_dt:
        from_dt, to_dt = to_dt, from_dt
    return _iso_utc(from_dt), _iso_utc(to_dt)


def _bucket_ts(ts: str, interval_seconds: int) -> str:
    parsed = _parse_iso(ts)
    if parsed is None:
        parsed = datetime.now(timezone.utc)
    epoch = int(parsed.timestamp())
    bucket = epoch - (epoch % max(1, interval_seconds))
    return _iso_utc(datetime.fromtimestamp(bucket, tz=timezone.utc))


def _monitoring_db_path() -> Path:
    settings = load_web_settings()
    text = get_market_realtime_db_path(settings)
    return Path(text).expanduser() if text else (BASE_DIR / "market_data.db")


def _market_overlay_db_path() -> Path:
    settings = load_web_settings()
    text = str(settings.get("sqlite_db_path", "")).strip()
    return Path(text).expanduser() if text else (BASE_DIR / "market_data.db")


def _clean_symbol_list(raw: Any) -> List[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    seen: set[str] = set()
    items: List[str] = []
    for item in text.split(","):
        symbol = str(item or "").strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        items.append(symbol)
    return items


def _allowed_overlay_symbols(args: Dict[str, Any], capabilities: Dict[str, Any], key: str) -> List[str]:
    requested = _clean_symbol_list(args.get(key))
    allowed = {str(item).strip().upper() for item in ((capabilities.get("overlay_allowed") or {}).get(key.removeprefix("overlay_")) or [])}
    return [item for item in requested if item in allowed]


def _clean_overlay_fields(raw: Any) -> List[str]:
    text = str(raw or "").strip().lower()
    if not text:
        return []
    seen: set[str] = set()
    items: List[str] = []
    for item in text.split(","):
        field_key = str(item or "").strip().lower()
        if not field_key or field_key in seen:
            continue
        seen.add(field_key)
        items.append(field_key)
    return items


def _selected_overlay_fields(args: Dict[str, Any], defaults: Dict[str, Any], capabilities: Dict[str, Any], source_kind: str) -> List[str]:
    raw = args.get(f"overlay_{source_kind}_fields")
    requested = _clean_overlay_fields(raw)
    if not requested:
        requested = _clean_overlay_fields(defaults.get(f"overlay_{source_kind}_fields"))
    allowed = {
        str(item).strip().lower()
        for item in ((capabilities.get("overlay_field_allowed") or {}).get(source_kind) or [])
    }
    return [item for item in requested if item in allowed]


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()}


def _overlay_series_key(source_kind: str, symbol: str, field_key: str) -> str:
    return f"{source_kind}_{symbol}_{field_key}"


def _overlay_json_value(item: Dict[str, Any], source_kind: str, field_key: str) -> Any:
    if field_key == "price":
        return item.get("price")
    if field_key == "mcap_usd":
        return item.get("market_cap_usd") or item.get("mcap_usd")
    if source_kind == "crypto":
        aliases = {
            "fdv_usd": ("fdv_usd", "fully_diluted_valuation"),
            "vol_24h_base": ("volume_24h_base", "vol_24h_base"),
            "vol_24h_quote": ("volume_24h_quote", "vol_24h_quote", "total_volume"),
            "circ_supply": ("circulating_supply", "circ_supply"),
            "total_supply": ("total_supply",),
            "max_supply": ("max_supply",),
        }
        for key in aliases.get(field_key, ()):
            value = item.get(key)
            if value is not None:
                return value
    return None


def _overlay_json_items_by_symbol(raw_json: Any) -> Dict[str, Dict[str, Any]]:
    try:
        parsed = json.loads(str(raw_json or "[]"))
    except (TypeError, ValueError):
        return {}
    if isinstance(parsed, dict):
        rows = parsed.get("data") or parsed.get("items") or parsed.get("rows") or []
    else:
        rows = parsed
    if not isinstance(rows, list):
        return {}
    output: Dict[str, Dict[str, Any]] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").strip().upper()
        if symbol:
            output[symbol] = item
    return output


def _overlay_series_items(source_kind: str, symbols: List[str], field_keys: List[str]) -> List[Dict[str, str]]:
    specs = _OVERLAY_FIELD_SPECS.get(source_kind) or {}
    items: List[Dict[str, str]] = []
    for field_key in field_keys:
        spec = specs.get(field_key)
        if not spec:
            continue
        for symbol in symbols:
            items.append(
                {
                    "key": _overlay_series_key(source_kind, symbol, field_key),
                    "label": f"{symbol} {spec['label']}",
                    "panel": spec["panel"],
                    "render": "line",
                    "unit": spec["unit"],
                    "category": f"overlay_{source_kind}",
                    "source_label": f"{source_kind.title()} Overlay",
                    "source_detail": f"{symbol} · {spec['label']}",
                    "removable": True,
                }
            )
    return items


def _load_overlay_samples(
    table_name: str,
    source_kind: str,
    from_ts: str,
    to_ts: str,
    interval_seconds: int,
    symbols: List[str],
    field_keys: List[str],
) -> Dict[str, Dict[str, Any]]:
    if not symbols or not field_keys:
        return {}
    db_path = _market_overlay_db_path()
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    samples: Dict[str, Dict[str, Any]] = {}
    try:
        _ensure_realtime_chart_indexes(conn, db_path)
        tables = {str(row[0]) for row in conn.execute("select name from sqlite_master where type='table'").fetchall()}
        if table_name not in tables:
            return {}
        columns = _table_columns(conn, table_name)
        # Use saved_at_utc directly to allow index usage; fall back to ts_utc for legacy tables.
        time_col = "saved_at_utc" if "saved_at_utc" in columns else "ts_utc"
        specs = _OVERLAY_FIELD_SPECS.get(source_kind) or {}
        selected_columns: list[tuple[str, str, str]] = []
        for field_key in field_keys:
            spec = specs.get(field_key)
            if not spec:
                continue
            column_prefix = spec["column"]
            for symbol in symbols:
                column_name = f"{column_prefix}_{symbol}"
                if column_name in columns:
                    selected_columns.append((symbol, field_key, column_name))
        has_data_json = "data" in columns
        if not selected_columns and not has_data_json:
            return {}
        select_parts = [f'"{column_name}"' for _, _, column_name in selected_columns]
        if has_data_json:
            select_parts.append('"data"')
        select_fields = ", ".join(select_parts)
        rows = conn.execute(
            f"""
            SELECT "{time_col}" AS sample_ts, {select_fields}
            FROM "{table_name}"
            WHERE "{time_col}" >= ? AND "{time_col}" <= ?
            ORDER BY "{time_col}" ASC
            """,
            (from_ts, to_ts),
        ).fetchall()
        for row in rows:
            sample_ts = str(row["sample_ts"] or "").strip()
            if not sample_ts:
                continue
            bucket = _bucket_ts(sample_ts, interval_seconds)
            sample = samples.setdefault(bucket, {"ts": bucket})
            json_items = _overlay_json_items_by_symbol(row["data"]) if has_data_json else {}
            for symbol, field_key, column_name in selected_columns:
                value = _safe_float(row[column_name])
                if value is None and json_items:
                    value = _safe_float(_overlay_json_value(json_items.get(symbol) or {}, source_kind, field_key))
                if value is not None:
                    sample[_overlay_series_key(source_kind, symbol, field_key)] = value
            if json_items:
                for symbol in symbols:
                    item = json_items.get(symbol)
                    if not item:
                        continue
                    for field_key in field_keys:
                        key = _overlay_series_key(source_kind, symbol, field_key)
                        if sample.get(key) is not None:
                            continue
                        value = _safe_float(_overlay_json_value(item, source_kind, field_key))
                        if value is not None:
                            sample[key] = value
    finally:
        conn.close()
    return samples


def _load_crypto_overlay_samples(
    from_ts: str,
    to_ts: str,
    interval_seconds: int,
    symbols: List[str],
    field_keys: List[str],
) -> Dict[str, Dict[str, Any]]:
    return _load_overlay_samples("Crypto", "crypto", from_ts, to_ts, interval_seconds, symbols, field_keys)


def _load_finance_overlay_samples(
    from_ts: str,
    to_ts: str,
    interval_seconds: int,
    symbols: List[str],
    field_keys: List[str],
) -> Dict[str, Dict[str, Any]]:
    return _load_overlay_samples("Stock", "finance", from_ts, to_ts, interval_seconds, symbols, field_keys)


def _load_price_samples(detail: Dict[str, Any], from_ts: str, to_ts: str, interval_seconds: int) -> Dict[str, Dict[str, Any]]:
    db_path = _monitoring_db_path()
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    samples: Dict[str, Dict[str, Any]] = {}
    try:
        tables = {str(row[0]) for row in conn.execute("select name from sqlite_master where type='table'").fetchall()}
        condition_id = str(detail.get("condition_id") or "").strip()
        yes_token = str(detail.get("yes_token") or "").strip()
        no_token = str(detail.get("no_token") or "").strip()
        tokens = [token for token in [yes_token, no_token] if token]
        token_to_side = {yes_token: "yes", no_token: "no"}
        clauses: list[str] = []
        params: list[Any] = []
        if condition_id:
            clauses.append("condition_id = ?")
            params.append(condition_id)
        if tokens:
            placeholders = ", ".join(["?"] * len(tokens))
            clauses.append(f"clobTokenId IN ({placeholders})")
            params.extend(tokens)
        if not clauses:
            return {}

        def apply_side_sample(sample: Dict[str, Any], side: str, payload: Dict[str, Any]) -> None:
            bid = _safe_binary_quote(payload.get("now_bid", payload.get("best_bid")))
            ask = _safe_binary_quote(payload.get("now_ask", payload.get("best_ask")))
            last_price = _safe_binary_quote(payload.get("last_price"))
            prefix = "yes" if side == "yes" else "no"
            if bid is not None:
                sample[f"{prefix}_bid"] = bid
            if ask is not None:
                sample[f"{prefix}_ask"] = ask
            if last_price is not None:
                sample[f"{prefix}_last_price"] = last_price

        if "market_deltas" in tables and (tokens or condition_id):
            delta_cols = _table_columns(conn, "market_deltas")
            base_fields = [c for c in ["timestamp", "clobTokenId", "payload_json", "condition_id"] if c in delta_cols]
            extra_fields = [
                c
                for c in (
                    "outcome_side",
                    "now_bid",
                    "now_ask",
                    "best_bid",
                    "best_ask",
                    "last_price",
                    "spread_c",
                )
                if c in delta_cols
            ]
            select_delta = ", ".join(f'"{c}"' for c in base_fields + extra_fields)
            where_parts: list[str] = []
            query_params: list[Any] = []
            if tokens:
                placeholders = ", ".join(["?"] * len(tokens))
                where_parts.append(f"clobTokenId IN ({placeholders})")
                query_params.extend(tokens)
            if condition_id and "condition_id" in delta_cols:
                where_parts.append("condition_id = ?")
                query_params.append(condition_id)
            if not where_parts:
                ws_rows = []
            else:
                ws_rows = conn.execute(
                    f"""
                    SELECT {select_delta}
                    FROM "market_deltas"
                    WHERE ({' OR '.join(where_parts)}) AND timestamp >= ? AND timestamp <= ?
                    ORDER BY timestamp ASC
                    """,
                    [*query_params, from_ts, to_ts],
                ).fetchall()

            def apply_delta_row(row: sqlite3.Row, *, bucket_override: str | None = None) -> str:
                after = _delta_after_from_row(row)
                side = str(row["outcome_side"] if "outcome_side" in row.keys() else after.get("outcome_side") or after.get("side") or "").strip().lower()
                if side not in {"yes", "no"}:
                    side = token_to_side.get(str(row["clobTokenId"] or "").strip())
                if side not in {"yes", "no"}:
                    return ""
                bucket = bucket_override or _bucket_ts(row["timestamp"], interval_seconds)
                sample = samples.setdefault(bucket, {"ts": bucket})
                apply_side_sample(sample, side, after)
                return side

            if where_parts:
                seed_from = from_ts
                from_dt = _parse_iso(from_ts)
                if from_dt is not None:
                    seed_from = _iso_utc(from_dt - timedelta(seconds=_PRICE_FORWARD_FILL_MAX_SECONDS))
                seed_rows = conn.execute(
                    f"""
                    SELECT {select_delta}
                    FROM "market_deltas"
                    WHERE ({' OR '.join(where_parts)}) AND timestamp >= ? AND timestamp < ?
                    ORDER BY timestamp DESC
                    LIMIT 40
                    """,
                    [*query_params, seed_from, from_ts],
                ).fetchall()
                seeded_sides: set[str] = set()
                for row in seed_rows:
                    side = apply_delta_row(row, bucket_override=from_ts)
                    if side:
                        seeded_sides.add(side)
                    if seeded_sides >= {"yes", "no"}:
                        break

            for row in ws_rows:
                apply_delta_row(row)

        if "markets_state" in tables and tokens:
            state_columns = _table_columns(conn, "markets_state")
            if "clobTokenId" in state_columns:
                state_select_fields = [
                    "clobTokenId",
                    "target_price",
                    "target_option_json",
                    "depth_metrics_json",
                    "raw_clob_json",
                    "updated_at_utc",
                ]
                for c in ("outcome_side", "now_bid", "now_ask", "best_bid", "best_ask", "last_price"):
                    if c in state_columns and c not in state_select_fields:
                        state_select_fields.append(c)
                select_state = ", ".join(f'"{c}"' for c in state_select_fields if c in state_columns)
                placeholders = ", ".join(["?"] * len(tokens))
                state_rows = conn.execute(
                    f"""
                    SELECT {select_state}
                    FROM "markets_state"
                    WHERE clobTokenId IN ({placeholders}) AND updated_at_utc >= ? AND updated_at_utc <= ?
                    ORDER BY updated_at_utc ASC
                    """,
                    [*tokens, from_ts, to_ts],
                ).fetchall()
            else:
                state_rows = conn.execute(
                    """
                    SELECT target_price, target_option_json, depth_metrics_json, raw_clob_json, updated_at_utc
                    FROM "markets_state"
                    WHERE updated_at_utc >= ? AND updated_at_utc <= ?
                    ORDER BY updated_at_utc ASC
                    """,
                    (from_ts, to_ts),
                ).fetchall()
            for row in state_rows:
                target_option = _parse_json(row["target_option_json"])
                token_id = str(
                    row["clobTokenId"] if "clobTokenId" in row.keys() else (target_option.get("clobTokenId") or "")
                ).strip()
                side = str(row["outcome_side"] if "outcome_side" in row.keys() else target_option.get("outcome_side") or target_option.get("side") or "").strip().lower()
                if side not in {"yes", "no"}:
                    side = token_to_side.get(token_id, "")
                if side not in {"yes", "no"}:
                    continue
                bucket = _bucket_ts(row["updated_at_utc"], interval_seconds)
                sample = samples.setdefault(bucket, {"ts": bucket})
                flat_payload = _markets_state_flat_payload(row, state_columns)
                if flat_payload is not None:
                    raw_clob_json = _parse_json(row["raw_clob_json"])
                    lp = _safe_float(flat_payload.get("last_price"))
                    if lp is None:
                        lp = _safe_float(
                            raw_clob_json.get("price")
                            or raw_clob_json.get("last_trade_price")
                            or raw_clob_json.get("lastPrice")
                            or row["target_price"]
                        )
                    merged = {**flat_payload, "last_price": lp}
                    apply_side_sample(sample, side, merged)
                else:
                    depth_metrics = _parse_json(row["depth_metrics_json"])
                    raw_clob_json = _parse_json(row["raw_clob_json"])
                    apply_side_sample(
                        sample,
                        side,
                        {
                            "now_bid": depth_metrics.get("now_bid", depth_metrics.get("best_bid")),
                            "now_ask": depth_metrics.get("now_ask", depth_metrics.get("best_ask")),
                            "last_price": raw_clob_json.get("price")
                            or raw_clob_json.get("last_trade_price")
                            or raw_clob_json.get("lastPrice")
                            or row["target_price"],
                            "best_bid": depth_metrics.get("best_bid"),
                            "best_ask": depth_metrics.get("best_ask"),
                        },
                    )

    finally:
        conn.close()
    for sample in samples.values():
        _drop_crossed_bid_ask(sample)
        _fill_binary_complements(sample, infer_bid_ask=False)
        _attach_binary_mid_prices(sample)
    return samples


def _load_strategy_tick_price_samples(
    detail: Dict[str, Any],
    from_ts: str,
    to_ts: str,
    interval_seconds: int,
) -> Dict[str, Dict[str, Any]]:
    row_id = detail.get("row_id")
    if not row_id:
        return {}
    try:
        conn = strategy_data_source.connect(readonly=True)
        conn.row_factory = sqlite3.Row
    except Exception:
        return {}
    samples: Dict[str, Dict[str, Any]] = {}
    try:
        rows = conn.execute(
            """
            SELECT run_at_utc, function_json, mode_output
            FROM strategy_virtual_ticks
            WHERE strategy_id = ? AND run_at_utc >= ? AND run_at_utc <= ?
            ORDER BY run_at_utc ASC
            """,
            (int(row_id), from_ts, to_ts),
        ).fetchall()
        bid_re = re.compile(r"Yes_bid=([0-9.]+)\s+No_bid=([0-9.]+)", re.IGNORECASE)
        for row in rows:
            bucket = _bucket_ts(row["run_at_utc"], interval_seconds)
            sample = samples.setdefault(bucket, {"ts": bucket})
            try:
                mode_payload = json.loads(row["mode_output"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                mode_payload = {}
            price_snapshot = mode_payload.get("price_snapshot") if isinstance(mode_payload, dict) else None
            if isinstance(price_snapshot, dict):
                for key in ("yes_bid", "yes_ask", "no_bid", "no_ask"):
                    value = _safe_binary_quote(price_snapshot.get(key))
                    if value is not None:
                        sample[key] = value
            try:
                payload = json.loads(row["function_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = {}
            prints = payload.get("print") if isinstance(payload, dict) else None
            if not isinstance(prints, list):
                continue
            yes_bid = no_bid = None
            for line in prints:
                match = bid_re.search(str(line or ""))
                if match:
                    yes_bid = _safe_float(match.group(1))
                    no_bid = _safe_float(match.group(2))
                    break
            if yes_bid is None and no_bid is None:
                continue
            yes_bid = _safe_binary_quote(yes_bid)
            no_bid = _safe_binary_quote(no_bid)
            if yes_bid is not None:
                sample["yes_bid"] = yes_bid
            if no_bid is not None:
                sample["no_bid"] = no_bid
            _attach_binary_mid_prices(sample)
    finally:
        conn.close()
    return {ts: sample for ts, sample in samples.items() if len(sample) > 1}


def _load_binance_kline_samples(
    detail: Dict[str, Any],
    from_ts: str,
    to_ts: str,
    interval_seconds: int,
) -> Dict[str, Dict[str, Any]]:
    symbol = str(detail.get("symbol") or "").strip().upper()
    if not symbol:
        return {}
    stored_interval = str(detail.get("interval") or DEFAULT_BINANCE_INTERVAL).strip() or DEFAULT_BINANCE_INTERVAL
    if not HISTORY_DB_PATH.exists():
        return {}
    conn = sqlite3.connect(str(HISTORY_DB_PATH), timeout=5.0)
    conn.row_factory = sqlite3.Row
    buckets: Dict[str, Dict[str, Any]] = {}
    try:
        rows = conn.execute(
            """SELECT open_time_utc, open, high, low, close, volume
               FROM binance_klines
               WHERE symbol = ? AND interval = ? AND open_time_utc >= ? AND open_time_utc <= ?
               ORDER BY open_time_ms ASC""",
            (symbol, stored_interval, from_ts, to_ts),
        ).fetchall()
        if not rows and stored_interval != DEFAULT_BINANCE_INTERVAL:
            rows = conn.execute(
                """SELECT open_time_utc, open, high, low, close, volume
                   FROM binance_klines
                   WHERE symbol = ? AND interval = ? AND open_time_utc >= ? AND open_time_utc <= ?
                   ORDER BY open_time_ms ASC""",
                (symbol, DEFAULT_BINANCE_INTERVAL, from_ts, to_ts),
            ).fetchall()
    finally:
        conn.close()
    for row in rows:
        bucket = _bucket_ts(row["open_time_utc"], interval_seconds)
        open_value = _safe_float(row["open"])
        high_value = _safe_float(row["high"])
        low_value = _safe_float(row["low"])
        close_value = _safe_float(row["close"])
        volume_value = _safe_float(row["volume"]) or 0.0
        if open_value is None or high_value is None or low_value is None or close_value is None:
            continue
        sample = buckets.setdefault(bucket, {
            "ts": bucket,
            "open": open_value,
            "high": high_value,
            "low": low_value,
            "close": close_value,
            "volume": 0.0,
        })
        sample["high"] = max(_safe_float(sample.get("high")) or high_value, high_value)
        sample["low"] = min(_safe_float(sample.get("low")) or low_value, low_value)
        sample["close"] = close_value
        sample["volume"] = (_safe_float(sample.get("volume")) or 0.0) + volume_value
    for sample in buckets.values():
        sample["ohlc"] = [
            sample.get("open"),
            sample.get("close"),
            sample.get("low"),
            sample.get("high"),
        ]
    return buckets


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _in_time_window(ts: Any, from_dt: datetime | None, to_dt: datetime | None) -> bool:
    parsed = _parse_iso(ts)
    if parsed is None:
        return False
    if from_dt and parsed < from_dt:
        return False
    if to_dt and parsed > to_dt:
        return False
    return True


def _compact_float(value: Any, digits: int = 6) -> str:
    num = _safe_float(value)
    if num is None or not math.isfinite(num):
        return "-"
    text = f"{num:.{digits}f}".rstrip("0").rstrip(".")
    return text or "0"


def _backtest_order_symbol(order: Dict[str, Any]) -> str:
    instrument_id = str(order.get("instrument_id") or "").strip()
    if instrument_id:
        return instrument_id.split(":")[-1] or instrument_id
    return str(order.get("leg_id") or "asset").strip() or "asset"


def _cached_backtest_run(run_id: int) -> Dict[str, Any]:
    parsed_run_id = _safe_int(run_id)
    if parsed_run_id <= 0:
        return {}
    now = time.time()
    with _BACKTEST_RUN_CACHE_LOCK:
        cached = _BACKTEST_RUN_CACHE.get(parsed_run_id)
        if cached and now - cached[0] <= _BACKTEST_RUN_CACHE_TTL_SECONDS:
            return cached[1]
    run = get_backtest_run(parsed_run_id, equity_limit=0, orders_limit=0, events_limit=0) or {}
    with _BACKTEST_RUN_CACHE_LOCK:
        stale_keys = [
            key
            for key, (cached_at, _) in _BACKTEST_RUN_CACHE.items()
            if now - cached_at > _BACKTEST_RUN_CACHE_TTL_SECONDS
        ]
        for key in stale_keys:
            _BACKTEST_RUN_CACHE.pop(key, None)
        if len(_BACKTEST_RUN_CACHE) >= _BACKTEST_RUN_CACHE_MAX:
            oldest_key = min(_BACKTEST_RUN_CACHE, key=lambda key: _BACKTEST_RUN_CACHE[key][0])
            _BACKTEST_RUN_CACHE.pop(oldest_key, None)
        _BACKTEST_RUN_CACHE[parsed_run_id] = (now, run)
    return run


def _load_backtest_overlay_samples(
    run_id: Any,
    from_ts: str,
    to_ts: str,
    interval_seconds: int,
) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    parsed_run_id = _safe_int(run_id)
    if parsed_run_id <= 0:
        return {}, [], {}
    run = _cached_backtest_run(parsed_run_id)
    from_dt = _parse_iso(from_ts)
    to_dt = _parse_iso(to_ts)
    samples: Dict[str, Dict[str, Any]] = {}
    initial_equity: float | None = None
    running_peak: float | None = None
    for point in sorted(run.get("equity") or [], key=lambda item: str(item.get("ts_utc") or "")):
        ts = point.get("ts_utc")
        if not _in_time_window(ts, from_dt, to_dt):
            continue
        bucket = _bucket_ts(str(ts), interval_seconds)
        meta = point.get("meta") if isinstance(point.get("meta"), dict) else {}
        equity_value = _safe_float(point.get("equity"))
        if equity_value is not None:
            if initial_equity is None and equity_value > 0:
                initial_equity = equity_value
            running_peak = equity_value if running_peak is None else max(running_peak, equity_value)
        sample = samples.setdefault(bucket, {"ts": bucket})
        sample["backtest_equity"] = equity_value
        sample["backtest_cash"] = _safe_float(point.get("cash"))
        sample["backtest_exposure"] = _safe_float(point.get("exposure"))
        sample["backtest_pnl"] = _safe_float(point.get("pnl"))
        sample["backtest_position_ratio"] = _safe_float(meta.get("position_ratio"))
        sample["backtest_position_qty"] = _safe_float(meta.get("position_qty"))
        sample["backtest_mark_price"] = _safe_float(meta.get("close"))
        if equity_value is not None and initial_equity:
            sample["backtest_return"] = (equity_value / initial_equity) - 1.0
        if equity_value is not None and running_peak:
            sample["backtest_drawdown"] = (equity_value / running_peak) - 1.0
    events: List[Dict[str, Any]] = []
    for order in run.get("orders") or []:
        ts = order.get("ts_utc")
        if not _in_time_window(ts, from_dt, to_dt):
            continue
        side = str(order.get("side") or "").upper() or "ORDER"
        symbol = _backtest_order_symbol(order)
        qty = _compact_float(order.get("quantity"), 8)
        price = _compact_float(order.get("price"), 2)
        reason = str((order.get("meta") or {}).get("reason") or order.get("reason") or "").strip()
        label = f"{side} {symbol} qty={qty} price={price}"
        if reason:
            label = f"{label} reason={reason}"
        events.append({
            "ts": ts,
            "type": "trade",
            "label": label,
            "severity": "info",
            "source": "backtest_order",
            "run_id": parsed_run_id,
            "side": side,
            "symbol": symbol,
            "quantity": _safe_float(order.get("quantity")),
            "price": _safe_float(order.get("price")),
            "fee": _safe_float(order.get("fee")),
            "reason": reason,
            "instrument_id": order.get("instrument_id"),
        })
    return samples, events, run


def _backtest_position_state_lanes(
    samples: Dict[str, Dict[str, Any]],
    from_ts: str,
    to_ts: str,
) -> List[Dict[str, Any]]:
    points = sorted(
        [
            (ts, _safe_float(sample.get("backtest_position_ratio")))
            for ts, sample in (samples or {}).items()
            if sample.get("backtest_position_ratio") is not None
        ],
        key=lambda item: item[0],
    )
    if not points:
        return []

    def state_for(value: float | None) -> str:
        if value is None or abs(value) < 1e-9:
            return "Flat"
        return "Long" if value > 0 else "Short"

    def color_for(state: str) -> str:
        return {"Long": "#22c55e", "Short": "#ef4444"}.get(state, "#64748b")

    segments: List[Dict[str, Any]] = []
    current_state = state_for(points[0][1])
    start = points[0][0] or from_ts
    for ts, value in points[1:]:
        next_state = state_for(value)
        if next_state == current_state:
            continue
        if start and ts and start < ts:
            segments.append({
                "from": start,
                "to": ts,
                "value": current_state,
                "label": current_state,
                "color": color_for(current_state),
                "lane": 0,
            })
        start = ts
        current_state = next_state
    end = to_ts or points[-1][0]
    if start and end and start < end:
        segments.append({
            "from": start,
            "to": end,
            "value": current_state,
            "label": current_state,
            "color": color_for(current_state),
            "lane": 0,
        })
    return [{
        "key": "backtest_position_state",
        "label": "Backtest Position State",
        "lane": 0,
        "segments": segments,
    }] if segments else []


def _prefix_sample_map(sample_map: Dict[str, Dict[str, Any]], prefix: str) -> Dict[str, Dict[str, Any]]:
    if not prefix:
        return sample_map
    output: Dict[str, Dict[str, Any]] = {}
    for ts, payload in sample_map.items():
        sample = {"ts": ts}
        for key, value in payload.items():
            if key == "ts":
                continue
            sample[f"{prefix}{key}"] = value
        output[ts] = sample
    return output


def _select_sample_keys(sample_map: Dict[str, Dict[str, Any]], allowed_keys: set[str]) -> Dict[str, Dict[str, Any]]:
    output: Dict[str, Dict[str, Any]] = {}
    for ts, payload in (sample_map or {}).items():
        sample = {"ts": ts}
        for key, value in payload.items():
            if key == "ts" or key not in allowed_keys or value is None:
                continue
            sample[key] = value
        if len(sample) > 1:
            output[ts] = sample
    return output


def _watch_market_key_set(main_side: str) -> set[str]:
    if main_side == "no":
        return {"no_mid"}
    if main_side == "yes":
        return {"yes_mid"}
    return {"yes_mid", "no_mid"}


def _watch_market_sample_map(sample_map: Dict[str, Dict[str, Any]], prefix: str, main_side: str) -> Dict[str, Dict[str, Any]]:
    watched_keys = _watch_market_key_set(main_side)
    output: Dict[str, Dict[str, Any]] = {}
    for ts, payload in (sample_map or {}).items():
        sample = dict(payload)
        _fill_binary_complements(sample, infer_bid_ask=False)
        _attach_binary_mid_prices(sample)
        projected = {"ts": ts}
        for key in watched_keys:
            value = sample.get(key)
            if value is not None:
                projected[f"{prefix}{key}"] = value
        if len(projected) > 1:
            output[ts] = projected
    return output


def _stats_row_to_sample(row: sqlite3.Row | Dict[str, Any], ts: str) -> Dict[str, Any]:
    return {
        "ts": ts,
        "yes_qty": _safe_float(row["yes_qty"]),
        "no_qty": _safe_float(row["no_qty"]),
        "yes_avg": _safe_float(row["yes_avg"]),
        "no_avg": _safe_float(row["no_avg"]),
        "yes_position": _safe_float(row["yes_pic"]),
        "no_position": _safe_float(row["no_pic"]),
        "strategy_pnl": _safe_float(row["strategy_pnl"]),
        "strategy_bankroll": _safe_float(row["strategy_bankroll"]),
        "initial_capital": _safe_float(row["initial_capital"]),
        "profit_roll_ratio": _safe_float(row["profit_roll_ratio"]),
        "realized_profit": _safe_float(row["realized_profit"]),
    }


def _load_stats_samples(detail: Dict[str, Any], from_ts: str, to_ts: str, interval_seconds: int) -> Dict[str, Dict[str, Any]]:
    if not detail.get("row_id"):
        return {}
    db_path = get_strategy_stats_db_path(detail)
    if db_path is None or not db_path.exists():
        return {}
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    samples: Dict[str, Dict[str, Any]] = {}
    try:
        tables = {str(row[0]) for row in conn.execute("select name from sqlite_master where type='table'").fetchall()}
        table_name = "strategy_stats_history" if "strategy_stats_history" in tables else "strategy_stats"
        rows = conn.execute(
            f"""
            SELECT *
            FROM "{table_name}"
            WHERE monitoring_row_id = ? AND updated_at_utc >= ? AND updated_at_utc <= ?
            ORDER BY updated_at_utc ASC
            """,
            (int(detail.get("row_id") or 0), from_ts, to_ts),
        ).fetchall()
        for row in rows:
            bucket = _bucket_ts(row["updated_at_utc"], interval_seconds)
            samples[bucket] = _stats_row_to_sample(row, bucket)
    finally:
        conn.close()
    return samples


def _derive_stats_from_price_samples(
    detail: Dict[str, Any],
    price_sample_map: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Fallback chart stats when the per-strategy stats history DB is empty."""
    yes_qty = _safe_float(detail.get("yes_qty")) or 0.0
    no_qty = _safe_float(detail.get("no_qty")) or 0.0
    yes_avg = _safe_float(detail.get("yes_avg"))
    no_avg = _safe_float(detail.get("no_avg"))
    bankroll = _safe_float(detail.get("strategy_bankroll")) or 0.0
    if yes_qty <= 0 and no_qty <= 0:
        return {}

    output: Dict[str, Dict[str, Any]] = {}
    for ts, sample in sorted(price_sample_map.items()):
        yes_ask = _safe_float(sample.get("market_0_yes_ask") or sample.get("yes_ask"))
        no_ask = _safe_float(sample.get("market_0_no_ask") or sample.get("no_ask"))
        pnl = 0.0
        has_pnl = False
        if yes_qty and yes_avg is not None and yes_ask is not None:
            pnl += (yes_ask - yes_avg) * yes_qty
            has_pnl = True
        if no_qty and no_avg is not None and no_ask is not None:
            pnl += (no_ask - no_avg) * no_qty
            has_pnl = True
        if not has_pnl:
            continue
        row = {
            "ts": ts,
            "yes_qty": yes_qty,
            "no_qty": no_qty,
            "yes_avg": yes_avg,
            "no_avg": no_avg,
            "yes_position": _safe_float(detail.get("yes_position")),
            "no_position": _safe_float(detail.get("no_position")),
            "strategy_pnl": pnl,
            "strategy_bankroll": bankroll,
        }
        output[ts] = row
    return output


def _detail_sample(detail: Dict[str, Any], interval_seconds: int, from_ts: str, to_ts: str) -> Dict[str, Dict[str, Any]]:
    updated_at = _parse_iso(detail.get("market_updated_at"))
    from_dt = _parse_iso(from_ts)
    to_dt = _parse_iso(to_ts)
    editable = detail.get("editable") or {}
    sample = {
        "yes_bid": _safe_binary_quote(detail.get("yes_bid")),
        "yes_ask": _safe_binary_quote(detail.get("yes_ask")),
        "yes_bid_levels": normalize_book_levels(detail.get("yes_bid_levels") or [], side="bid"),
        "yes_ask_levels": normalize_book_levels(detail.get("yes_ask_levels") or [], side="ask"),
        "yes_last_price": _safe_binary_quote(detail.get("yes_last_price")),
        "no_bid": _safe_binary_quote(detail.get("no_bid")),
        "no_ask": _safe_binary_quote(detail.get("no_ask")),
        "no_bid_levels": normalize_book_levels(detail.get("no_bid_levels") or [], side="bid"),
        "no_ask_levels": normalize_book_levels(detail.get("no_ask_levels") or [], side="ask"),
        "no_last_price": _safe_binary_quote(detail.get("no_last_price")),
        "yes_qty": _safe_float(detail.get("yes_qty")),
        "no_qty": _safe_float(detail.get("no_qty")),
        "yes_avg": _safe_float(detail.get("yes_avg")),
        "no_avg": _safe_float(detail.get("no_avg")),
        "yes_position": _safe_float(detail.get("yes_position")),
        "no_position": _safe_float(detail.get("no_position")),
        "position": _safe_float(detail.get("position") or detail.get("position_qty") or detail.get("qty")),
        "qty": _safe_float(detail.get("qty") or detail.get("position_qty") or detail.get("yes_qty")),
        "avg": _safe_float(detail.get("avg") or detail.get("avg_price") or detail.get("yes_avg")),
        "strategy_pnl": _safe_float(detail.get("strategy_pnl")),
        "strategy_bankroll": _safe_float(detail.get("strategy_bankroll")),
        "initial_capital": _safe_float(editable.get("initial_capital")),
        "profit_roll_ratio": _safe_float(editable.get("profit_roll_ratio")),
        "realized_profit": _safe_float(editable.get("realized_profit")),
    }
    if from_dt is None or to_dt is None:
        return {}
    if not any(value is not None for key, value in sample.items() if key != "ts"):
        return {}
    if updated_at is None:
        updated_at = to_dt
    if updated_at < from_dt or updated_at > to_dt:
        return {}
    ts = _bucket_ts(updated_at.isoformat(), interval_seconds)
    sample["ts"] = ts
    _fill_binary_complements(sample, infer_bid_ask=False)
    return {ts: sample}


def _leg_position_sample(
    detail: Dict[str, Any],
    interval_seconds: int,
    from_ts: str,
    to_ts: str,
) -> Dict[str, Dict[str, Any]]:
    """Return the latest leg position snapshot without requiring a remote quote."""
    sample = {
        key: _safe_float(detail.get(key))
        for key in _LEG_POSITION_DETAIL_KEYS
    }
    if not any(value is not None for value in sample.values()):
        return {}
    updated_at = detail.get("position_updated_at") or detail.get("market_updated_at") or to_ts
    updated_dt = _parse_iso(updated_at) or _parse_iso(to_ts)
    from_dt = _parse_iso(from_ts)
    to_dt = _parse_iso(to_ts)
    if updated_dt is None:
        return {}
    # A current snapshot is still useful for a historical chart; place it at the
    # right edge instead of dropping the leg entirely when its last update is old.
    if from_dt is not None and to_dt is not None and not (from_dt <= updated_dt <= to_dt):
        updated_dt = to_dt
    bucket = _bucket_ts(updated_dt.isoformat(), interval_seconds)
    return {bucket: {"ts": bucket, **{key: value for key, value in sample.items() if value is not None}}}


def _merge_samples(*sample_maps: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for sample_map in sample_maps:
        for ts, payload in sample_map.items():
            target = merged.setdefault(ts, {"ts": ts})
            target.update({key: value for key, value in payload.items() if key != "ts"})
    ordered_ts = sorted(merged.keys(), key=lambda item: _parse_iso(item) or datetime.min.replace(tzinfo=timezone.utc))
    rows: List[Dict[str, Any]] = []
    current: Dict[str, Any] = {}
    current_ts_by_key: Dict[str, datetime] = {}
    for ts in ordered_ts:
        row_dt = _parse_iso(ts)
        for key, value in merged[ts].items():
            if key == "ts":
                continue
            if value is None:
                current.pop(key, None)
                current_ts_by_key.pop(key, None)
            else:
                current[key] = value
                if row_dt is not None:
                    current_ts_by_key[key] = row_dt
        row = {"ts": ts}
        for key, value in current.items():
            if _is_price_field(key):
                seen_at = current_ts_by_key.get(key)
                if row_dt is not None and seen_at is not None:
                    age = (row_dt - seen_at).total_seconds()
                    if age > _PRICE_FORWARD_FILL_MAX_SECONDS:
                        continue
            row[key] = value
        rows.append(row)
    if not rows:
        return []
    max_points = 1200
    if len(rows) <= max_points:
        return rows
    stride = max(1, math.ceil(len(rows) / max_points))
    sampled = rows[::stride]
    if sampled[-1]["ts"] != rows[-1]["ts"]:
        sampled.append(rows[-1])
    return sampled


def _sync_row_pnl_to_visible_prices(rows: List[Dict[str, Any]]) -> None:
    """Keep chart PNL consistent with the ask prices shown in the same row."""
    for row in rows:
        yes_qty = _safe_float(row.get("yes_qty")) or 0.0
        no_qty = _safe_float(row.get("no_qty")) or 0.0
        yes_avg = _safe_float(row.get("yes_avg"))
        no_avg = _safe_float(row.get("no_avg"))
        yes_ask = _safe_float(row.get("market_0_yes_ask") or row.get("yes_ask"))
        no_ask = _safe_float(row.get("market_0_no_ask") or row.get("no_ask"))
        pnl = 0.0
        has_pnl = False
        needs_price = False
        if yes_qty > 0 and yes_avg is not None:
            needs_price = True
            if yes_ask is not None:
                pnl += (yes_ask - yes_avg) * yes_qty
                has_pnl = True
        if no_qty > 0 and no_avg is not None:
            needs_price = True
            if no_ask is not None:
                pnl += (no_ask - no_avg) * no_qty
                has_pnl = True
        if has_pnl:
            row["strategy_pnl"] = pnl
        elif needs_price:
            row.pop("strategy_pnl", None)


def _position_mark(row: Dict[str, Any], side: str, avg_price: float, leg_index: int = 0) -> float:
    prefix = "yes" if side.upper() == "YES" else "no"
    bid = _safe_float(row.get(f"market_{leg_index}_{prefix}_bid"))
    ask = _safe_float(row.get(f"market_{leg_index}_{prefix}_ask"))
    if leg_index == 0:
        bid = bid if bid is not None else _safe_float(row.get(f"{prefix}_bid"))
        ask = ask if ask is not None else _safe_float(row.get(f"{prefix}_ask"))
    return bid if bid is not None and bid > 0 else (ask if ask is not None and ask > 0 else avg_price)


def _can_overlay_detail_pnl(detail: Dict[str, Any]) -> bool:
    """Return whether the current detail PnL is based on a live position mark."""
    source = str(detail.get("pnl_source") or "").strip().lower()
    return source != "virtual_account_partial_cost_fallback"


def _position_liquidation(
    row: Dict[str, Any],
    side: str,
    qty: float,
    fee_rate: float,
    detail: Dict[str, Any] | None = None,
    leg_index: int = 0,
) -> Dict[str, Any]:
    prefix = "yes" if side.upper() == "YES" else "no"
    bid = _safe_float(row.get(f"market_{leg_index}_{prefix}_bid"))
    ask = _safe_float(row.get(f"market_{leg_index}_{prefix}_ask"))
    levels = row.get(f"market_{leg_index}_{prefix}_bid_levels") or []
    if leg_index == 0:
        bid = bid if bid is not None else _safe_float(row.get(f"{prefix}_bid"))
        ask = ask if ask is not None else _safe_float(row.get(f"{prefix}_ask"))
        levels = levels or row.get(f"{prefix}_bid_levels") or []
    if not levels and detail:
        leg_detail = next(
            (
                item for item in (detail.get("legs_snapshot") or [])
                if int(item.get("leg_index") or 0) == int(leg_index)
            ),
            detail if leg_index == 0 else {},
        )
        levels = leg_detail.get(f"{prefix}_bid_levels") or []
        bid = bid if bid is not None else _safe_float(leg_detail.get(f"{prefix}_bid"))
        ask = ask if ask is not None else _safe_float(leg_detail.get(f"{prefix}_ask") or leg_detail.get(f"{prefix}_mark"))
    fallback = bid if bid is not None and bid > 0 else ask
    return liquidate_position(qty, fallback, fee_rate, bid_levels=levels)


def _position_budget(detail: Dict[str, Any], account: sqlite3.Row | Dict[str, Any] | None = None) -> float:
    budget = _safe_float(detail.get("budget_cap"))
    if budget is None or budget <= 0:
        budget = _safe_float(detail.get("strategy_bankroll"))
    if (budget is None or budget <= 0) and account is not None:
        try:
            budget = _safe_float(account["initial_cash"])
        except Exception:
            budget = None
    return budget if budget is not None and budget > 0 else 0.0


def _append_current_multileg_snapshot(
    rows: List[Dict[str, Any]],
    detail: Dict[str, Any],
    from_ts: str,
    to_ts: str,
) -> str | None:
    snapshots = detail.get("legs_snapshot") or []
    if len(snapshots) <= 1:
        return None
    timestamps = [
        _parse_iso(item.get("updated_at") or item.get("market_updated_at"))
        for item in snapshots
    ]
    timestamps = [item for item in timestamps if item is not None]
    if not timestamps:
        return None
    snapshot_dt = max(timestamps)
    from_dt = _parse_iso(from_ts)
    to_dt = _parse_iso(to_ts)
    if (from_dt and snapshot_dt < from_dt) or (to_dt and snapshot_dt > to_dt):
        return None
    ts = snapshot_dt.isoformat()
    current = dict(rows[-1]) if rows else {}
    current["ts"] = ts
    for item in snapshots:
        leg_index = int(item.get("leg_index") or 0)
        for side in ("yes", "no"):
            for field in ("bid", "ask", "bid_levels"):
                value = item.get(f"{side}_{field}")
                if value is not None:
                    current[f"market_{leg_index}_{side}_{field}"] = value
                    if leg_index == 0:
                        current[f"{side}_{field}"] = value
    rows.append(current)
    rows.sort(key=lambda item: _parse_iso(item.get("ts")) or datetime.min.replace(tzinfo=timezone.utc))
    return ts


def _infer_row_interval_seconds(rows: List[Dict[str, Any]]) -> int:
    timestamps = [
        parsed
        for parsed in (_parse_iso(row.get("ts")) for row in (rows or [])[:20])
        if parsed is not None
    ]
    deltas: List[int] = []
    for index in range(1, len(timestamps)):
        delta = int((timestamps[index] - timestamps[index - 1]).total_seconds())
        if delta > 0:
            deltas.append(delta)
    return max(1, min(deltas)) if deltas else 1


def _normalize_rows_with_required_buckets(
    rows: List[Dict[str, Any]],
    required_ts: set[str],
) -> None:
    if not rows:
        return
    merged: Dict[str, Dict[str, Any]] = {
        str(row.get("ts") or ""): dict(row)
        for row in rows
        if str(row.get("ts") or "").strip()
    }
    row_times = [_parse_iso(ts) for ts in merged.keys()]
    row_times = [item for item in row_times if item is not None]
    if not row_times:
        return
    min_dt = min(row_times)
    max_dt = max(row_times)
    for ts in required_ts:
        parsed = _parse_iso(ts)
        if parsed is not None and min_dt <= parsed <= max_dt:
            merged.setdefault(ts, {"ts": ts})

    ordered = [
        merged[ts]
        for ts in sorted(merged.keys(), key=lambda item: _parse_iso(item) or datetime.min.replace(tzinfo=timezone.utc))
    ]
    current: Dict[str, Any] = {}
    normalized: List[Dict[str, Any]] = []
    for row in ordered:
        ts = str(row.get("ts") or "")
        current.update({key: value for key, value in row.items() if key != "ts"})
        normalized.append({"ts": ts, **current})
    rows[:] = normalized


def _apply_virtual_account_pnl_to_rows(detail: Dict[str, Any], rows: List[Dict[str, Any]], chart_interval_seconds: int = 0) -> List[Dict[str, Any]]:
    """For Virtual charts, replay filled orders into PnL and position columns.
    Returns aggregated trade events per bucket for chart tooltip alignment."""
    if str(detail.get("mode") or detail.get("state") or "").strip().lower() != "virtual" or not detail.get("row_id") or not rows:
        return []
    try:
        conn = strategy_data_source.connect(readonly=True)
        conn.row_factory = sqlite3.Row
        account = conn.execute(
            "SELECT * FROM strategy_virtual_account WHERE strategy_id = ?",
            (int(detail.get("row_id") or 0),),
        ).fetchone()
        orders = conn.execute(
            """
            SELECT id, leg_index, action, side, qty, price, gross_notional, fee_rate, fee,
                   net_cash_change, liquidity_role, status, reason, created_at_utc
            FROM strategy_virtual_orders
            WHERE strategy_id = ? AND status = 'filled'
            ORDER BY created_at_utc ASC, id ASC
            """,
            (int(detail.get("row_id") or 0),),
        ).fetchall()
        conn.close()
    except Exception:
        return []
    if not account:
        return []

    initial_cash = _safe_float(account["initial_cash"]) or 0.0
    position_budget = _position_budget(detail, account) or initial_cash
    cash = initial_cash
    positions = {
        "YES": {"qty": 0.0, "avg": 0.0, "fee_rate": 0.0},
        "NO": {"qty": 0.0, "avg": 0.0, "fee_rate": 0.0},
    }
    leg_positions: Dict[int, Dict[str, Dict[str, float]]] = {}
    order_items = []
    end_at = _strategy_end_at(detail)
    for order in orders:
        ts = _parse_iso(order["created_at_utc"])
        if ts is None:
            continue
        if end_at is not None and ts > end_at:
            continue
        order_items.append((ts, dict(order)))

    idx = 0
    last_valid_pnl = None
    interval_seconds = chart_interval_seconds if chart_interval_seconds > 0 else _infer_row_interval_seconds(rows)
    order_points = {
        _iso_utc_exact(order_ts)
        for order_ts, _order in order_items
    }
    _normalize_rows_with_required_buckets(rows, order_points)

    # Collect per-bucket aggregated trades for chart events
    bucket_trades: Dict[str, List[Dict[str, Any]]] = {}

    for row_index, row in enumerate(rows):
        row_dt = _parse_iso(row.get("ts"))
        if row_dt is None:
            continue
        row_ts_str = str(row.get("ts") or "")
        while idx < len(order_items):
            order_ts, order = order_items[idx]
            if order_ts > row_dt:
                break
            idx += 1
            order_point_ts = _iso_utc_exact(order_ts)
            side = str(order.get("side") or "").upper()
            if side not in positions:
                continue
            leg_index = int(order.get("leg_index") or 0)
            leg_state = leg_positions.setdefault(
                leg_index,
                {
                    "YES": {"qty": 0.0, "avg": 0.0, "fee_rate": 0.0},
                    "NO": {"qty": 0.0, "avg": 0.0, "fee_rate": 0.0},
                },
            )
            qty = _safe_float(order.get("qty")) or 0.0
            price = _safe_float(order.get("price")) or 0.0
            fee_rate = _safe_float(order.get("fee_rate")) or 0.0
            cash += _safe_float(order.get("net_cash_change")) or 0.0
            action = str(order.get("action") or "BUY").upper()
            pos = positions[side]
            leg_pos = leg_state[side]
            leg_qty_before = float(leg_pos["qty"] or 0.0)
            if action == "BUY":
                new_qty = pos["qty"] + qty
                pos["avg"] = ((pos["qty"] * pos["avg"]) + (qty * price)) / new_qty if new_qty > 0 else 0.0
                pos["qty"] = new_qty
                pos["fee_rate"] = fee_rate
                leg_new_qty = leg_pos["qty"] + qty
                leg_pos["avg"] = ((leg_pos["qty"] * leg_pos["avg"]) + (qty * price)) / leg_new_qty if leg_new_qty > 0 else 0.0
                leg_pos["qty"] = leg_new_qty
                leg_pos["fee_rate"] = fee_rate
            else:
                pos["qty"] = max(0.0, pos["qty"] - qty)
                pos["fee_rate"] = fee_rate or pos["fee_rate"]
                leg_pos["qty"] = max(0.0, leg_pos["qty"] - qty)
                leg_pos["fee_rate"] = fee_rate or leg_pos["fee_rate"]
            leg_qty_after = float(leg_pos["qty"] or 0.0)
            if order_point_ts == row_ts_str:
                bucket_trades.setdefault(row_ts_str, []).append({
                    "order_ref": str(order.get("id") or ""),
                    "leg_index": leg_index,
                    "action": action,
                    "side": side,
                    "qty": qty,
                    "price": price,
                    "gross_notional": _safe_float(order.get("gross_notional")),
                    "fee": _safe_float(order.get("fee")),
                    "net_cash_change": _safe_float(order.get("net_cash_change")),
                    "liquidity_role": str(order.get("liquidity_role") or ""),
                    "status": str(order.get("status") or "filled").lower(),
                    "reason": str(order.get("reason") or ""),
                    "position_qty_before": leg_qty_before,
                    "position_qty_after": leg_qty_after,
                })

        row["yes_qty"] = float(positions["YES"]["qty"] or 0.0)
        row["no_qty"] = float(positions["NO"]["qty"] or 0.0)
        row["yes_avg"] = float(positions["YES"]["avg"] or 0.0) if row["yes_qty"] > 0 else 0.0
        row["no_avg"] = float(positions["NO"]["avg"] or 0.0) if row["no_qty"] > 0 else 0.0
        yes_cost = row["yes_qty"] * row["yes_avg"]
        no_cost = row["no_qty"] * row["no_avg"]
        allocation_base = max(0.0, cash) + yes_cost + no_cost
        row["yes_position"] = (yes_cost / allocation_base) if allocation_base > 0 else 0.0
        row["no_position"] = (no_cost / allocation_base) if allocation_base > 0 else 0.0
        for leg_index, leg_state in leg_positions.items():
            yes_leg = leg_state["YES"]
            no_leg = leg_state["NO"]
            yes_leg_cost = float(yes_leg["qty"] or 0.0) * float(yes_leg["avg"] or 0.0)
            no_leg_cost = float(no_leg["qty"] or 0.0) * float(no_leg["avg"] or 0.0)
            row[f"market_{leg_index}_yes_position"] = yes_leg_cost / allocation_base if allocation_base > 0 else 0.0
            row[f"market_{leg_index}_no_position"] = no_leg_cost / allocation_base if allocation_base > 0 else 0.0
            row[f"market_{leg_index}_yes_qty"] = float(yes_leg["qty"] or 0.0)
            row[f"market_{leg_index}_no_qty"] = float(no_leg["qty"] or 0.0)
            row[f"market_{leg_index}_yes_avg"] = float(yes_leg["avg"] or 0.0) if yes_leg["qty"] > 0 else 0.0
            row[f"market_{leg_index}_no_avg"] = float(no_leg["avg"] or 0.0) if no_leg["qty"] > 0 else 0.0

        previous_row = rows[row_index - 1] if row_index > 0 else {}
        for trade in bucket_trades.get(row_ts_str, []):
            position_key = f"market_{int(trade.get('leg_index') or 0)}_{str(trade.get('side') or '').lower()}_position"
            trade["position_before"] = _safe_float(previous_row.get(position_key)) or 0.0
            trade["position_after"] = _safe_float(row.get(position_key)) or 0.0

        if end_at is not None and row_dt > end_at:
            if last_valid_pnl is not None:
                row["strategy_pnl"] = last_valid_pnl
                row["pnl_source"] = "virtual_order_replay_expired_frozen"
            else:
                open_cost = sum(
                    float(pos.get("qty") or 0.0) * float(pos.get("avg") or 0.0)
                    for leg_state in leg_positions.values()
                    for pos in leg_state.values()
                )
                row["strategy_pnl"] = cash + open_cost - initial_cash
                row["pnl_source"] = "virtual_order_replay_expired_cost_fallback"
            continue

        liquidation_value = 0.0
        estimated_exit_fees = 0.0
        missing_open_mark = False
        # Each leg is a distinct market. Never net or mark positions from
        # different legs with the primary market's quote.
        for leg_index, leg_state in leg_positions.items():
            for side, pos in leg_state.items():
                qty = float(pos["qty"] or 0.0)
                avg = float(pos["avg"] or 0.0)
                if qty <= 0 or avg <= 0:
                    continue
                liquidation = _position_liquidation(
                    row,
                    side,
                    qty,
                    float(pos["fee_rate"] or 0.0),
                    detail if row_index == len(rows) - 1 else None,
                    leg_index=leg_index,
                )
                if liquidation.get("filled_qty", 0.0) <= 0:
                    mark = _position_mark(row, side, 0.0, leg_index=leg_index)
                    if mark <= 0:
                        missing_open_mark = True
                        continue
                    liquidation_value += qty * mark
                    estimated_exit_fees += qty * float(pos["fee_rate"] or 0.0) * mark * (1.0 - mark)
                else:
                    liquidation_value += float(liquidation.get("gross") or 0.0)
                    estimated_exit_fees += float(liquidation.get("fee") or 0.0)
                    key = f"market_{leg_index}_{side.lower()}"
                    row[f"{key}_liquidation_vwap"] = liquidation.get("vwap")
                    row[f"{key}_liquidation_filled_qty"] = liquidation.get("filled_qty")
                    row[f"{key}_liquidation_depth_limited"] = liquidation.get("depth_limited")
        if missing_open_mark:
            if last_valid_pnl is not None:
                row["strategy_pnl"] = last_valid_pnl
                row["pnl_source"] = "virtual_order_replay_last_valid_mark"
            else:
                row.pop("strategy_pnl", None)
                row["pnl_source"] = "virtual_order_replay_missing_mark"
            continue
        row["strategy_pnl"] = cash + liquidation_value - estimated_exit_fees - initial_cash
        row["pnl_source"] = "virtual_order_replay_depth"
        last_valid_pnl = row["strategy_pnl"]

    # Build aggregated trade events aligned to bucket timestamps
    aggregated_events: List[Dict[str, Any]] = []
    for bucket_ts, trades in sorted(bucket_trades.items(), key=lambda x: x[0]):
        # A price can be shared by multiple legs. Keep leg identity in the
        # grouping key so the chart never makes a multi-leg fill look like one
        # anonymous order.
        groups: Dict[tuple, Dict[str, Any]] = {}
        net_by_side: Dict[str, float] = {}
        for t in trades:
            key = (t["leg_index"], t["action"], t["side"], t["price"])
            grouped = groups.setdefault(
                key,
                {
                    **t,
                    "qty": 0.0,
                    "gross_notional": 0.0,
                    "fee": 0.0,
                    "net_cash_change": 0.0,
                    "order_refs": [],
                },
            )
            grouped["qty"] += float(t.get("qty") or 0.0)
            grouped["gross_notional"] += float(t.get("gross_notional") or 0.0)
            grouped["fee"] += float(t.get("fee") or 0.0)
            grouped["net_cash_change"] += float(t.get("net_cash_change") or 0.0)
            if t.get("order_ref"):
                grouped["order_refs"].append(str(t["order_ref"]))
            grouped["position_qty_after"] = t.get("position_qty_after")
            sign = 1.0 if t["action"] == "BUY" else -1.0
            net_by_side[t["side"]] = net_by_side.get(t["side"], 0.0) + sign * t["qty"]
        fills = list(groups.values())
        parts = [
            f"Leg {int(fill['leg_index']) + 1} {fill['action']} {fill['side']} "
            f"qty={float(fill['qty']):.2f} @{float(fill['price']):.4f}"
            for fill in fills
        ]
        net_parts = [
            f"net {side} {qty:+.2f}"
            for side, qty in sorted(net_by_side.items())
            if abs(qty) > 1e-9
        ]
        label = " | ".join(parts)
        if len(trades) > 1:
            label = f"Bucket Trades ({len(trades)} fills): {label}"
        if net_parts:
            label = f"{label} ({', '.join(net_parts)})"
        unique_actions = {str(fill.get("action") or "") for fill in fills}
        unique_sides = {str(fill.get("side") or "") for fill in fills}
        unique_legs = {int(fill.get("leg_index") or 0) for fill in fills}
        unique_prices = {float(fill.get("price") or 0.0) for fill in fills}
        total_qty = sum(float(fill.get("qty") or 0.0) for fill in fills)
        payload = {
            "status": "filled",
            "fills": fills,
            "fill_count": len(trades),
            "action": next(iter(unique_actions)) if len(unique_actions) == 1 else "MULTI",
            "side": next(iter(unique_sides)) if len(unique_sides) == 1 else "MULTI",
            "leg": next(iter(unique_legs)) if len(unique_legs) == 1 else None,
            "price": next(iter(unique_prices)) if len(unique_prices) == 1 else None,
            "qty": total_qty,
        }
        aggregated_events.append({
            "ts": bucket_ts,
            "type": "trade",
            "subtype": "filled",
            "label": label,
            "severity": "info",
            "source": "virtual_order_replay",
            "status": "filled",
            "action": payload["action"],
            "side": payload["side"],
            "leg": payload["leg"],
            "price": payload["price"],
            "quantity": payload["qty"],
            "payload": payload,
        })
    return aggregated_events


_CHART_EVENT_PAYLOAD_KEYS = {
    "action",
    "action_type",
    "fee",
    "gross_notional",
    "instrument_id",
    "leg",
    "leg_index",
    "liquidity_role",
    "net_cash_change",
    "order_ref",
    "price",
    "qty",
    "quantity",
    "reason",
    "side",
    "status",
}


def _compact_chart_event_label(item: Dict[str, Any]) -> str:
    raw = str(item.get("summary") or item.get("label") or item.get("event_type") or item.get("type") or "-")
    text = re.split(r"===DB_JSON_BEGIN===", raw, maxsplit=1, flags=re.IGNORECASE)[0]
    text = " ".join(text.split())
    event_type = str(item.get("event_type") or item.get("type") or "").lower()
    if "print" in event_type:
        fields = []
        for key in ("decision", "actions", "candidates", "selected", "machine_state"):
            match = re.search(rf"(?:^|\s){re.escape(key)}=([^\s]+)", text, flags=re.IGNORECASE)
            if match:
                fields.append(f"{key}={match.group(1)}")
        if fields:
            return " · ".join(fields)
    return f"{text[:277]}…" if len(text) > 280 else text


def _chart_event_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """Project an event into the small, explicit chart tooltip contract."""
    raw_payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    payload = {
        key: raw_payload.get(key)
        for key in _CHART_EVENT_PAYLOAD_KEYS
        if raw_payload.get(key) is not None
    }
    status = str(payload.get("status") or item.get("event_subtype") or "").strip().lower()
    action = str(payload.get("action") or payload.get("action_type") or "").strip().upper()
    side = str(payload.get("side") or "").strip().upper()
    leg = payload.get("leg", payload.get("leg_index"))
    quantity = payload.get("qty", payload.get("quantity"))
    projected = {
        "id": item.get("id"),
        "ts": item.get("ts"),
        "type": item.get("event_type") or item.get("type"),
        "subtype": item.get("event_subtype") or item.get("subtype"),
        "label": _compact_chart_event_label(item),
        "severity": item.get("severity"),
        "source": item.get("source"),
        "status": status,
        "action": action,
        "side": side,
        "leg": leg,
        "price": payload.get("price"),
        "quantity": quantity,
        "order_ref": payload.get("order_ref"),
        "reason": payload.get("reason"),
        "payload": payload,
    }
    return {key: value for key, value in projected.items() if value not in (None, "")}


_LEG_SUB_METRIC_PATTERN = re.compile(
    r"^leg:(\d+):(yes_position|no_position|position|yes_qty|no_qty|qty|yes_avg|no_avg|avg)$"
)


def _leg_sub_metric_parts(token: str) -> tuple[int, str] | None:
    match = _LEG_SUB_METRIC_PATTERN.fullmatch(str(token or "").strip())
    if not match:
        return None
    return int(match.group(1)), match.group(2)


def _is_allowed_sub_series_token(token: str, allowed: set[str]) -> bool:
    if token in allowed:
        return True
    leg_parts = _leg_sub_metric_parts(token)
    return bool(leg_parts and leg_parts[1] in allowed)


def _requested_sub_series(args: Dict[str, Any], defaults: Dict[str, Any], capabilities: Dict[str, Any]) -> List[str]:
    items = _raw_requested_sub_series(args, defaults)
    allowed = _allowed_sub_series_keys(capabilities)
    return [item for item in items if _is_allowed_sub_series_token(item, allowed)]


def _raw_requested_sub_series(args: Dict[str, Any], defaults: Dict[str, Any]) -> List[str]:
    raw = str(args.get("sub_metrics") or "").strip()
    if raw:
        return [item.strip() for item in raw.split(",") if item.strip()]
    return list(defaults.get("sub_series") or [])


def _allowed_sub_series_keys(capabilities: Dict[str, Any]) -> set[str]:
    allowed = set(capabilities.get("sub_allowed") or [])
    metric_catalog = capabilities.get("metric_catalog") or {}
    numeric_keys = {f"metric:{item.get('key')}" for item in metric_catalog.get("numeric") or [] if item.get("key")}
    state_keys = {f"metric_state:{item.get('key')}" for item in metric_catalog.get("state") or [] if item.get("key")}
    return allowed | numeric_keys | state_keys


def _sub_series_debug(args: Dict[str, Any], defaults: Dict[str, Any], capabilities: Dict[str, Any]) -> Dict[str, Any]:
    requested = _raw_requested_sub_series(args, defaults)
    allowed = _allowed_sub_series_keys(capabilities)
    selected = [item for item in requested if _is_allowed_sub_series_token(item, allowed)]
    rejected = [item for item in requested if not _is_allowed_sub_series_token(item, allowed)]
    metric_catalog = capabilities.get("metric_catalog") or {}
    return {
        "requested_sub_metrics": requested,
        "selected_sub_metrics": selected,
        "rejected_sub_metrics": rejected,
        "metric_catalog_items": len(metric_catalog.get("items") or []),
        "metric_catalog_numeric": len(metric_catalog.get("numeric") or []),
        "metric_catalog_state": len(metric_catalog.get("state") or []),
    }


def _metric_key(token: str, prefix: str) -> str:
    text = str(token or "").strip()
    return text[len(prefix):] if text.startswith(prefix) else ""


def _selected_metric_keys(selected_series: List[str]) -> Tuple[List[str], List[str]]:
    numeric: List[str] = []
    state: List[str] = []
    for item in selected_series:
        if item.startswith("metric:"):
            key = _metric_key(item, "metric:")
            if key and key not in numeric:
                numeric.append(key)
        elif item.startswith("metric_state:"):
            key = _metric_key(item, "metric_state:")
            if key and key not in state:
                state.append(key)
    return numeric, state


def _main_series(main_side: str, capabilities: Dict[str, Any]) -> List[str]:
    allowed = capabilities.get("main_allowed") or []
    if main_side == "yes":
        return [item for item in allowed if item.startswith("yes_")]
    if main_side == "no":
        return [item for item in allowed if item.startswith("no_")]
    return list(allowed)


def _static_series_items(series_keys: List[str], detail: Dict[str, Any]) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    strategy_name = str(detail.get("display_name") or detail.get("strategy") or f"Row {detail.get('row_id') or '-'}").strip()
    source_detail = f"{strategy_name} · Row {detail.get('row_id') or '-'}"
    for key in series_keys:
        config = _SERIES_CONFIG.get(key)
        if config:
            items.append(
                {
                    "key": key,
                    **config,
                    "source_label": "策略监控数据",
                    "source_detail": source_detail,
                    "removable": False,
                }
            )
    return items


def _metric_catalog_by_key(capabilities: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    catalog = capabilities.get("metric_catalog") or {}
    return {
        str(item.get("key") or ""): item
        for item in (catalog.get("items") or [])
        if str(item.get("key") or "").strip()
    }


def _merge_metric_catalog(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Dict[str, Any]] = {}
    for item in (base.get("items") if isinstance(base, dict) else []) or []:
        key = str(item.get("key") or "").strip()
        if key:
            merged[key] = item
    for item in (extra.get("items") if isinstance(extra, dict) else []) or []:
        key = str(item.get("key") or "").strip()
        if key:
            merged[key] = item
    items = list(merged.values())
    return {
        "items": items,
        "numeric": [item for item in items if item.get("metric_type") == "number" and item.get("value_state") == "value"],
        "state": [item for item in items if item.get("kind") == "state" and item.get("value_state") == "value"],
    }


def _metric_series_items(metric_keys: List[str], capabilities: Dict[str, Any]) -> List[Dict[str, Any]]:
    catalog = _metric_catalog_by_key(capabilities)
    items: List[Dict[str, Any]] = []
    for key in metric_keys:
        meta = catalog.get(key) or {"key": key, "label": key, "unit": ""}
        meta_payload = meta.get("meta") if isinstance(meta.get("meta"), dict) else {}
        is_backtest_derived = str(meta_payload.get("source") or "") == "backtest_derived" or str(key).startswith("backtest_")
        is_leg_position_metric = bool(re.fullmatch(r"L\d+_(?:yes_|no_)?position", str(key), flags=re.IGNORECASE))
        default_panel = "backtest_metrics" if is_backtest_derived else "metric_values"
        panel = "leg_position_metrics" if is_leg_position_metric else (str(meta.get("panel") or "") or default_panel)
        if key in {"gap_up", "gap_down", "hold_buffer_yes", "recover_buffer_no"} and str(meta.get("unit") or "") == "compact_currency":
            panel = "market_mcap"
        label = str(meta.get("label") or key)
        if is_leg_position_metric:
            label = f"Leg-cap metric · {label}"
        items.append(
            {
                "key": f"metric:{key}",
                "label": label,
                "full_label": label,
                "panel": panel,
                "render": "line",
                "unit": str(meta.get("unit") or ""),
                "category": "backtest_metric" if is_backtest_derived else "strategy_metric",
                "metric_key": key,
                "source_label": "Backtest Metrics" if is_backtest_derived else "策略 Metrics",
                "source_detail": f"metrics.{key}",
                "removable": False,
            }
        )
    return items


def _metric_type_for_value(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
        return "number"
    if value is None:
        return "null"
    if isinstance(value, str):
        return "text"
    return "json"


def _catalog_item_from_metric_value(key: str, value: Any, meta: Dict[str, Any], ts: str, count: int) -> Dict[str, Any]:
    metric_type = _metric_type_for_value(value)
    kind = str(meta.get("kind") or "").strip().lower()
    if not kind:
        kind = "continuous" if metric_type == "number" else ("state" if metric_type in {"bool", "text"} else "event")
    return {
        "key": key,
        "label": str(meta.get("label") or key),
        "kind": kind,
        "metric_type": metric_type,
        "unit": str(meta.get("unit") or ""),
        "panel": str(meta.get("panel") or ""),
        "value_state": "null" if value is None else "value",
        "latest_value": value,
        "latest_ts": ts,
        "count": count,
        "meta": meta,
    }


def _virtual_tick_metric_catalog(detail: Dict[str, Any], limit: int = 50) -> Dict[str, Any]:
    row_id = detail.get("row_id")
    if not row_id:
        return {"items": [], "numeric": [], "state": []}
    try:
        conn = strategy_data_source.connect(readonly=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT run_at_utc, function_json
            FROM strategy_virtual_ticks
            WHERE strategy_id = ? AND function_json IS NOT NULL AND function_json != ''
            ORDER BY run_at_utc DESC
            LIMIT ?
            """,
            (int(row_id), int(limit)),
        ).fetchall()
        conn.close()
    except Exception:
        return {"items": [], "numeric": [], "state": []}
    latest: Dict[str, Dict[str, Any]] = {}
    counts: Dict[str, int] = {}
    for row in rows:
        try:
            payload = json.loads(row["function_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
        metrics_meta = payload.get("metrics_meta") if isinstance(payload.get("metrics_meta"), dict) else {}
        for raw_key, value in metrics.items():
            key = str(raw_key or "").strip()
            if not key:
                continue
            counts[key] = counts.get(key, 0) + 1
            if key in latest:
                continue
            item_meta = metrics_meta.get(key) if isinstance(metrics_meta.get(key), dict) else {}
            latest[key] = _catalog_item_from_metric_value(key, value, item_meta, str(row["run_at_utc"] or ""), 1)
    for key, count in counts.items():
        if key in latest:
            latest[key]["count"] = count
    items = sorted(latest.values(), key=lambda item: str(item.get("key") or ""))
    return {
        "items": items,
        "numeric": [item for item in items if item.get("metric_type") == "number" and item.get("value_state") == "value"],
        "state": [item for item in items if item.get("kind") == "state" and item.get("value_state") == "value"],
    }


def _natural_metric_sort_key(key: str) -> List[Any]:
    parts = re.split(r"(\d+)", str(key or ""))
    return [int(part) if part.isdigit() else part.lower() for part in parts]


def _normalize_strategy_key(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").strip().lower())


def _is_backtest_derived_catalog_item(item: Dict[str, Any]) -> bool:
    meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
    key = str(item.get("key") or "")
    return meta.get("source") == "backtest_derived" or key.startswith("backtest_")


def _strategy_param_keys(detail: Dict[str, Any] | None) -> set[str]:
    if not isinstance(detail, dict):
        return set()
    raw = detail.get("raw") if isinstance(detail.get("raw"), dict) else {}
    params: Dict[str, Any] = {}
    for candidate in (detail.get("input_json"), raw.get("input_json"), raw.get("InputJson")):
        if isinstance(candidate, dict):
            params.update(candidate)
            continue
        text = str(candidate or "").strip()
        if not text:
            continue
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict):
            params.update(parsed)
    return {_normalize_strategy_key(key) for key in params.keys() if str(key or "").strip()}


def default_strategy_metric_keys(capabilities: Dict[str, Any], detail: Dict[str, Any] | None = None) -> List[str]:
    catalog = {
        key: item
        for key, item in _metric_catalog_by_key(capabilities).items()
        if not _is_backtest_derived_catalog_item(item)
    }
    keys = set(catalog.keys())
    selected: List[str] = []
    param_keys = _strategy_param_keys(detail)

    def add(key: str) -> None:
        if key in keys and key not in selected:
            selected.append(key)

    normalized_key_map: Dict[str, List[str]] = {}
    for key in sorted(keys, key=_natural_metric_sort_key):
        normalized_key_map.setdefault(_normalize_strategy_key(key), []).append(key)
    for param_key in sorted(param_keys):
        for key in normalized_key_map.get(param_key, []):
            add(key)

    for key in (
        "trend_ratio",
        "pnl_pct",
        "drawdown_pct",
        "drawdown_from_peak",
        "target_position",
        "target",
        "target_delta",
        "position_cap_pct",
        "fair_price",
        "entry_edge",
        "full_entry_edge",
        "exit_edge",
        "gap_up",
        "gap_down",
        "day_to_end",
        "risk_scale",
    ):
        add(key)
    for key in ("mcap_count", "candidate_count", "selected_count", "best_edge", "best_yes_edge", "best_no_edge"):
        add(key)
    for pattern in (
        r"^L\d+_(yes|no)_position$",
        r"^L\d+_position$",
        r"^L\d+_(model_prob|yes_edge|no_edge)$",
        r"^L\d+_(target_rank|current_rank)$",
        r"^L\d+_mcap_usd$",
        r"^mcap_rank_[A-Z0-9]+$",
        r"^mcap_usd_[A-Z0-9]+$",
    ):
        matched = sorted((key for key in keys if re.match(pattern, key)), key=_natural_metric_sort_key)
        for key in matched:
            add(key)
    if not selected:
        fallback = sorted(
            (
                key
                for key, item in catalog.items()
                if str(item.get("metric_type") or "") == "number" and str(item.get("value_state") or "") == "value"
            ),
            key=_natural_metric_sort_key,
        )
        for key in fallback[:6]:
            add(key)
    return selected[:120]


def _metric_value_from_event(event: Dict[str, Any]) -> Any:
    return None if event.get("value_state") == "null" else event.get("value")


def _load_metric_numeric_samples(
    detail: Dict[str, Any],
    metric_keys: List[str],
    from_ts: str,
    to_ts: str,
    interval_seconds: int,
    *,
    include_prior: bool = True,
) -> Dict[str, Dict[str, Any]]:
    row_id = detail.get("row_id")
    if not row_id or not metric_keys:
        return {}
    events_by_key = load_metric_events(
        int(row_id),
        metric_keys,
        from_ts,
        to_ts,
        include_prior=include_prior,
    )
    output: Dict[str, Dict[str, Any]] = {}
    from_dt = _parse_iso(from_ts)
    for key, events in events_by_key.items():
        for event in events:
            ts = str(event.get("ts") or "")
            event_dt = _parse_iso(ts)
            bucket_source = from_ts if from_dt is not None and event_dt is not None and event_dt < from_dt else ts
            bucket = _bucket_ts(bucket_source, interval_seconds)
            value = _metric_value_from_event(event)
            output.setdefault(bucket, {"ts": bucket})[f"metric:{key}"] = value if isinstance(value, (int, float)) else None
    return output


def _load_virtual_tick_metric_samples(
    detail: Dict[str, Any],
    metric_keys: List[str],
    from_ts: str,
    to_ts: str,
    interval_seconds: int,
) -> Dict[str, Dict[str, Any]]:
    row_id = detail.get("row_id")
    if not row_id or not metric_keys:
        return {}
    wanted = set(metric_keys)
    try:
        conn = strategy_data_source.connect(readonly=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT run_at_utc, function_json
            FROM strategy_virtual_ticks
            WHERE strategy_id = ? AND run_at_utc >= ? AND run_at_utc <= ?
            ORDER BY run_at_utc ASC
            """,
            (int(row_id), from_ts, to_ts),
        ).fetchall()
        conn.close()
    except Exception:
        return {}
    output: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        try:
            payload = json.loads(row["function_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
        if not metrics:
            continue
        bucket = _bucket_ts(str(row["run_at_utc"] or ""), interval_seconds)
        sample = output.setdefault(bucket, {"ts": bucket})
        for key in wanted:
            if key not in metrics:
                continue
            value = metrics.get(key)
            sample[f"metric:{key}"] = value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    return output


_STATE_COLORS = [
    "#60a5fa",
    "#f59e0b",
    "#22c55e",
    "#ef4444",
    "#a78bfa",
    "#14b8a6",
    "#f97316",
    "#94a3b8",
]


_BOOL_STATE_LABELS: Dict[str, Dict[bool, str]] = {
    "cooldown_active": {True: "Cooldown active", False: "Cooldown inactive"},
    "shock_cooldown_active": {True: "Shock cooldown active", False: "Shock cooldown inactive"},
    "shock_block_active": {True: "Shock block active", False: "Shock block inactive"},
    "manual_pause_open": {True: "Manual pause open", False: "Manual pause closed"},
    "stop_loss_locked": {True: "Stop loss locked", False: "Stop loss unlocked"},
    "force_flat": {True: "Force flat", False: "Force flat off"},
    "rank_ok": {True: "Rank OK", False: "Rank not OK"},
}

_BOOL_STATE_COLORS: Dict[str, Dict[bool, str]] = {
    "cooldown_active": {True: "#f59e0b", False: "#64748b"},
    "shock_cooldown_active": {True: "#f97316", False: "#64748b"},
    "shock_block_active": {True: "#ef4444", False: "#64748b"},
    "manual_pause_open": {True: "#f97316", False: "#64748b"},
    "stop_loss_locked": {True: "#ef4444", False: "#64748b"},
    "force_flat": {True: "#f97316", False: "#64748b"},
    "rank_ok": {True: "#22c55e", False: "#f59e0b"},
}


def _state_meta_for_value(value: Any, meta: Dict[str, Any]) -> Dict[str, Any]:
    states = meta.get("states") if isinstance(meta.get("states"), dict) else {}
    state_meta = None
    if isinstance(states, dict):
        for candidate in (str(value), str(value).lower(), str(value).upper()):
            state_meta = states.get(candidate)
            if state_meta is not None:
                break
    return state_meta if isinstance(state_meta, dict) else {}


def _bool_state_label(key: str, value: bool) -> str:
    mapped = _BOOL_STATE_LABELS.get(key)
    if mapped:
        return mapped[value]
    words = key.replace("_", " ").strip()
    if words.endswith(" active"):
        base = words[: -len(" active")].strip()
        return f"{base.title()} {'active' if value else 'inactive'}"
    if words.endswith(" open"):
        base = words[: -len(" open")].strip()
        return f"{base.title()} {'open' if value else 'closed'}"
    if words.endswith(" locked"):
        base = words[: -len(" locked")].strip()
        return f"{base.title()} {'locked' if value else 'unlocked'}"
    return "True" if value else "False"


def _state_label(key: str, value: Any, meta: Dict[str, Any]) -> str:
    state_meta = _state_meta_for_value(value, meta)
    if state_meta.get("label"):
        return str(state_meta.get("label"))
    if isinstance(value, bool):
        return _bool_state_label(str(key or ""), value)
    return str(value)


def _state_color(key: str, value: Any, meta: Dict[str, Any], index: int) -> str:
    state_meta = _state_meta_for_value(value, meta)
    if isinstance(state_meta, dict) and state_meta.get("color"):
        return str(state_meta.get("color"))
    if isinstance(value, bool):
        color_map = _BOOL_STATE_COLORS.get(str(key or ""))
        if color_map and value in color_map:
            return color_map[value]
    return _STATE_COLORS[index % len(_STATE_COLORS)]


def _state_value_key(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return str(value)


def _metric_state_lanes(
    detail: Dict[str, Any],
    metric_keys: List[str],
    from_ts: str,
    to_ts: str,
    capabilities: Dict[str, Any],
) -> List[Dict[str, Any]]:
    row_id = detail.get("row_id")
    if not row_id or not metric_keys:
        return []
    catalog = _metric_catalog_by_key(capabilities)
    events_by_key = load_metric_events(int(row_id), metric_keys, from_ts, to_ts)
    lanes: List[Dict[str, Any]] = []
    for lane_index, key in enumerate(metric_keys):
        raw_events = sorted(events_by_key.get(key) or [], key=lambda item: _parse_iso(item.get("ts")) or datetime.min.replace(tzinfo=timezone.utc))
        events = []
        previous_value_key = ""
        for event in raw_events:
            value = _metric_value_from_event(event)
            if value is None:
                continue
            value_key = _state_value_key(value)
            if events and value_key == previous_value_key:
                continue
            events.append({**event, "_metric_value": value})
            previous_value_key = value_key
        if not events:
            continue
        meta = catalog.get(key) or {"key": key, "label": key, "meta": {}}
        segments = []
        for idx, event in enumerate(events):
            value = event.get("_metric_value")
            start = max(str(event.get("ts") or from_ts), from_ts)
            end = str(events[idx + 1].get("ts") or to_ts) if idx + 1 < len(events) else to_ts
            if end <= from_ts or start >= to_ts:
                continue
            segments.append(
                {
                    "from": start,
                    "to": min(end, to_ts),
                    "value": str(value),
                    "label": _state_label(key, value, meta.get("meta") or {}),
                    "color": _state_color(key, value, meta.get("meta") or {}, idx),
                    "lane": lane_index,
                }
            )
        if segments:
            lanes.append(
                {
                    "key": key,
                    "label": str(meta.get("label") or key),
                    "lane": lane_index,
                    "segments": segments,
                }
            )
    return lanes


def _point_strategy_metrics(point: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    meta = point.get("meta") if isinstance(point.get("meta"), dict) else {}
    metrics = meta.get("strategy_metrics") if isinstance(meta.get("strategy_metrics"), dict) else {}
    metrics_meta = meta.get("strategy_metrics_meta") if isinstance(meta.get("strategy_metrics_meta"), dict) else {}
    return metrics, metrics_meta


def _backtest_derived_metric_value(
    key: str,
    point: Dict[str, Any],
    initial_equity: float | None,
    running_peak: float | None,
) -> Any:
    if key == "backtest_return":
        equity = _safe_float(point.get("equity"))
        return (equity / initial_equity - 1.0) if equity is not None and initial_equity else None
    if key == "backtest_drawdown":
        equity = _safe_float(point.get("equity"))
        return (equity / running_peak - 1.0) if equity is not None and running_peak else None
    return None


def _load_backtest_metric_numeric_samples(
    run: Dict[str, Any],
    metric_keys: List[str],
    from_ts: str,
    to_ts: str,
    interval_seconds: int,
) -> Dict[str, Dict[str, Any]]:
    if not run or not metric_keys:
        return {}
    from_dt = _parse_iso(from_ts)
    to_dt = _parse_iso(to_ts)
    output: Dict[str, Dict[str, Any]] = {}
    initial_equity: float | None = None
    running_peak: float | None = None
    for point in sorted(run.get("equity") or [], key=lambda item: str(item.get("ts_utc") or "")):
        equity = _safe_float(point.get("equity"))
        if equity is not None and equity > 0:
            if initial_equity is None:
                initial_equity = equity
            running_peak = equity if running_peak is None else max(running_peak, equity)
        ts = point.get("ts_utc")
        if not _in_time_window(ts, from_dt, to_dt):
            continue
        bucket = _bucket_ts(str(ts), interval_seconds)
        sample = output.setdefault(bucket, {"ts": bucket})
        metrics, _ = _point_strategy_metrics(point)
        for key in metric_keys:
            value = _backtest_derived_metric_value(key, point, initial_equity, running_peak)
            if value is None:
                value = metrics.get(key)
            number = _safe_float(value)
            if number is not None and math.isfinite(number):
                sample[f"metric:{key}"] = number
    return output


def _load_backtest_metric_state_lanes(
    run: Dict[str, Any],
    metric_keys: List[str],
    from_ts: str,
    to_ts: str,
    capabilities: Dict[str, Any],
) -> List[Dict[str, Any]]:
    if not run or not metric_keys:
        return []
    metric_keys = [key for key in metric_keys if key != "backtest_position_state"]
    if not metric_keys:
        return []
    from_dt = _parse_iso(from_ts)
    to_dt = _parse_iso(to_ts)
    catalog = _metric_catalog_by_key(capabilities)
    lanes: List[Dict[str, Any]] = []
    points = sorted(run.get("equity") or [], key=lambda item: str(item.get("ts_utc") or ""))
    for lane_index, key in enumerate(metric_keys):
        events: List[Dict[str, Any]] = []
        previous_value_key = ""
        for point in points:
            ts = point.get("ts_utc")
            if not _in_time_window(ts, from_dt, to_dt):
                continue
            metrics, metrics_meta = _point_strategy_metrics(point)
            if key not in metrics:
                continue
            value = metrics.get(key)
            if value is None:
                continue
            item_meta = metrics_meta.get(key) if isinstance(metrics_meta.get(key), dict) else {}
            value_key = _state_value_key(value)
            if events and value_key == previous_value_key:
                continue
            events.append({"ts": str(ts), "value": value, "meta": item_meta})
            previous_value_key = value_key
        if not events:
            continue
        meta = catalog.get(key) or {"key": key, "label": key, "meta": events[-1].get("meta") or {}}
        segments = []
        for idx, event in enumerate(events):
            value = event.get("value")
            start = max(str(event.get("ts") or from_ts), from_ts)
            end = str(events[idx + 1].get("ts") or to_ts) if idx + 1 < len(events) else to_ts
            if end <= from_ts or start >= to_ts:
                continue
            meta_payload = meta.get("meta") or event.get("meta") or {}
            segments.append(
                {
                    "from": start,
                    "to": min(end, to_ts),
                    "value": str(value),
                    "label": _state_label(key, value, meta_payload),
                    "color": _state_color(key, value, meta_payload, idx),
                    "lane": lane_index,
                }
            )
        if segments:
            lanes.append(
                {
                    "key": key,
                    "label": str(meta.get("label") or key),
                    "lane": lane_index,
                    "segments": segments,
                }
            )
    return lanes


def _short_market_label(detail: Dict[str, Any], fallback: str) -> str:
    text = str(detail.get("question") or detail.get("display_name") or fallback).strip()
    if len(text) <= 18:
        return text
    return text[:15].rstrip() + "..."


def _market_series_items(targets: List[Dict[str, Any]], main_side: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for index, target in enumerate(targets):
        detail = target["detail"]
        prefix = f"market_{index}_"
        label_prefix = _short_market_label(detail, f"Market {index + 1}")
        full_market_label = str(
            detail.get("question") or detail.get("display_name") or label_prefix
        ).strip()
        market_short_label = str(detail.get("group_item_title") or label_prefix).strip()
        if _is_binance_detail(detail):
            symbol = str(detail.get("symbol") or label_prefix).strip().upper()
            return_label = symbol or label_prefix
            items.append({
                "key": f"{prefix}ohlc",
                "label": f"{return_label} K线",
                "panel": "main",
                "render": "candlestick",
                "unit": "price",
                "category": "market_target",
                "market_index": index,
                "market_label": return_label,
                "base_key": "ohlc",
                "source_label": "Binance OHLCV",
                "source_detail": f"{symbol} {detail.get('interval') or DEFAULT_BINANCE_INTERVAL}",
                "removable": True,
            })
            items.append({
                "key": f"{prefix}close",
                "label": f"{return_label} Close",
                "panel": "main",
                "render": "line",
                "unit": "price",
                "category": "market_target",
                "market_index": index,
                "market_label": return_label,
                "base_key": "close",
                "source_label": "Binance Close",
                "source_detail": f"{symbol} {detail.get('interval') or DEFAULT_BINANCE_INTERVAL}",
                "removable": True,
                "style": {"visible": False, "line_type": "solid", "width": 1.6, "smooth": False, "show_symbol": False},
            })
            items.append({
                "key": f"{prefix}volume",
                "label": f"{return_label} Volume",
                "panel": "volume",
                "render": "bar",
                "unit": "compact_number",
                "category": "market_volume",
                "market_index": index,
                "market_label": return_label,
                "base_key": "volume",
                "source_label": "Binance Volume",
                "source_detail": f"{symbol} {detail.get('interval') or DEFAULT_BINANCE_INTERVAL}",
                "removable": True,
            })
            continue
        if index == 0:
            side_keys = _main_series(main_side, {"main_allowed": ["yes_bid", "yes_ask", "no_bid", "no_ask"]})
        else:
            side_keys = sorted(_watch_market_key_set(main_side))
        for key in side_keys:
            config = _SERIES_CONFIG.get(key)
            if not config:
                continue
            items.append(
                {
                    "key": f"{prefix}{key}",
                    "label": f"{label_prefix} {config['label']}",
                    "panel": "main",
                    "render": "line",
                    "unit": config["unit"],
                    "category": "market_target",
                    "market_index": index,
                    "market_label": label_prefix,
                    "market_short_label": market_short_label,
                    "market_full_label": full_market_label,
                    "full_label": f"{full_market_label} · {config['label']}",
                    "base_key": key,
                    "source_label": "市场价格线",
                    "source_detail": f"{detail.get('question') or detail.get('display_name') or label_prefix} · Condition {detail.get('condition_id') or '-'}",
                    "removable": True,
                }
            )
    return items


def _is_binary_leg_detail(detail: Dict[str, Any]) -> bool:
    if _is_binance_detail(detail):
        return False
    asset_class = str(detail.get("asset_class") or "").strip().lower()
    venue = str(detail.get("venue") or "").strip().lower()
    return bool(
        detail.get("condition_id")
        or detail.get("yes_token")
        or detail.get("no_token")
        or asset_class in {"polymarket_binary", "binary", "binary_market"}
        or venue == "polymarket"
    )


def _leg_position_series_items(
    targets: List[Dict[str, Any]],
    selected_series: List[str],
) -> List[Dict[str, Any]]:
    """Build position/size/average series per leg instead of one aggregate pair."""
    selected = set(selected_series or [])
    position_keys = {"yes_position", "no_position", "position"}
    qty_keys = {"yes_qty", "no_qty", "qty"}
    avg_keys = {"yes_avg", "no_avg", "avg"}
    items: List[Dict[str, Any]] = []
    for index, target in enumerate(targets):
        detail = target.get("detail") or {}
        leg_index = int(target.get("leg_index", detail.get("leg_index", index)) or index)
        leg_name = _short_market_label(detail, f"Leg {leg_index + 1}")
        full_leg_name = str(detail.get("question") or detail.get("display_name") or leg_name).strip()
        short_leg_name = str(detail.get("group_item_title") or leg_name).strip()
        binary = _is_binary_leg_detail(detail)
        selected_for_leg = {
            base_key
            for token in selected
            for parsed_index, base_key in [_leg_sub_metric_parts(token) or (-1, "")]
            if parsed_index == leg_index and base_key
        }
        selected_for_leg.update(selected & (position_keys | qty_keys | avg_keys))
        if binary:
            fields = (
                [("yes_position", "Yes Position", "leg_positions", "ratio"), ("no_position", "No Position", "leg_positions", "ratio")]
                if selected_for_leg & position_keys
                else []
            )
            if "yes_position" not in selected_for_leg:
                fields = [item for item in fields if item[0] != "yes_position"]
            if "no_position" not in selected_for_leg:
                fields = [item for item in fields if item[0] != "no_position"]
            if selected_for_leg & qty_keys:
                fields.extend(
                    item for item in (
                        ("yes_qty", "Yes Qty", "leg_sizes", "qty"),
                        ("no_qty", "No Qty", "leg_sizes", "qty"),
                    ) if item[0] in selected_for_leg
                )
            if selected_for_leg & avg_keys:
                fields.extend(
                    item for item in (
                        ("yes_avg", "Yes Avg", "leg_averages", "price"),
                        ("no_avg", "No Avg", "leg_averages", "price"),
                    ) if item[0] in selected_for_leg
                )
        else:
            fields = []
            if "position" in selected_for_leg:
                fields.append(("position", "Position", "leg_positions", "qty"))
            if "qty" in selected_for_leg:
                fields.append(("qty", "Qty", "leg_sizes", "qty"))
            if "avg" in selected_for_leg:
                fields.append(("avg", "Avg", "leg_averages", "price"))
        for base_key, label, panel, unit in fields:
            selector_token = f"leg:{leg_index}:{base_key}"
            items.append(
                {
                    "key": f"market_{index}_{base_key}",
                    "label": f"Leg {leg_index + 1} {leg_name} {label}",
                    "panel": panel,
                    "render": "step" if panel != "leg_averages" else "line",
                    "unit": unit,
                    "category": "position" if panel == "leg_positions" else ("size" if panel == "leg_sizes" else "average"),
                    "market_index": index,
                    "leg_index": leg_index,
                    "market_label": leg_name,
                    "market_short_label": short_leg_name,
                    "market_full_label": full_leg_name,
                    "full_label": f"Leg {leg_index + 1} · {full_leg_name} · {label}",
                    "base_key": base_key,
                    "sub_metric": selector_token,
                    "sub_metric_base": base_key,
                    "source_label": "Strategy Leg",
                    "source_detail": str(detail.get("question") or detail.get("display_name") or leg_name),
                    "removable": False,
                }
            )
    return items


def _all_leg_sub_metric_tokens(targets: List[Dict[str, Any]]) -> List[str]:
    tokens: List[str] = []
    for index, target in enumerate(targets):
        detail = target.get("detail") or {}
        leg_index = int(target.get("leg_index", detail.get("leg_index", index)) or index)
        base_keys = (
            ("yes_position", "no_position", "yes_qty", "no_qty", "yes_avg", "no_avg")
            if _is_binary_leg_detail(detail)
            else ("position", "qty", "avg")
        )
        tokens.extend(f"leg:{leg_index}:{base_key}" for base_key in base_keys)
    return tokens


def _panel_list(series_items: List[Dict[str, str]]) -> List[Dict[str, str]]:
    panel_ids = ["main"]
    for item in series_items:
        panel_id = str(item.get("panel") or "").strip()
        if panel_id and panel_id not in panel_ids:
            panel_ids.append(panel_id)
    return [{"id": panel_id, "title": _PANEL_TITLES.get(panel_id, panel_id.title())} for panel_id in panel_ids]


def _parse_json_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _leg_identity(detail: Dict[str, Any]) -> str:
    return "|".join(
        [
            str(detail.get("condition_id") or "").strip(),
            str(detail.get("yes_token") or "").strip(),
            str(detail.get("no_token") or "").strip(),
        ]
    )


def _is_binance_detail(detail: Dict[str, Any]) -> bool:
    source = str(detail.get("source") or "").strip().lower()
    venue = str(detail.get("venue") or "").strip().lower()
    asset_class = str(detail.get("asset_class") or "").strip().lower()
    instrument_id = str(detail.get("instrument_id") or "").strip().lower()
    return source == "binance" or venue == "binance" or asset_class in {"crypto_spot", "crypto"} or instrument_id.startswith("crypto_spot:binance:")


def _target_identity(detail: Dict[str, Any]) -> str:
    if _is_binance_detail(detail):
        symbol = str(detail.get("symbol") or "").strip().upper()
        interval = str(detail.get("interval") or DEFAULT_BINANCE_INTERVAL).strip()
        instrument_id = str(detail.get("instrument_id") or "").strip()
        return f"binance|{symbol}|{interval}|{instrument_id}"
    return _leg_identity(detail)


def _binance_target_detail_from_leg(leg: Dict[str, Any], leg_index: int) -> Dict[str, Any] | None:
    source = str(leg.get("source") or leg.get("venue") or "").strip().lower()
    asset_class = str(leg.get("asset_class") or "").strip().lower()
    instrument_id = str(leg.get("instrument_id") or "").strip()
    symbol = str(leg.get("symbol") or "").strip().upper()
    if not symbol and instrument_id.lower().startswith("crypto_spot:binance:"):
        symbol = instrument_id.split(":")[-1].upper()
    if not (symbol and (source == "binance" or asset_class in {"crypto_spot", "crypto"} or instrument_id.lower().startswith("crypto_spot:binance:"))):
        return None
    interval = str(leg.get("interval") or DEFAULT_BINANCE_INTERVAL).strip() or DEFAULT_BINANCE_INTERVAL
    display = str(leg.get("display_name") or leg.get("label") or symbol).strip()
    return {
        "source": "binance",
        "venue": "binance",
        "asset_class": "crypto_spot",
        "symbol": symbol,
        "interval": interval,
        "instrument_id": instrument_id or f"crypto_spot:binance:{symbol}",
        "display_name": display,
        "question": display,
        "leg_index": leg_index,
        "budget_cap": leg.get("budget_cap"),
        "params_json": leg.get("params_json"),
    }


def _chart_market_detail_from_market(
    market: Dict[str, Any] | None,
    *,
    condition_id: str = "",
    token_id: str = "",
) -> Dict[str, Any]:
    matched_market = market or {}
    raw = matched_market.get("raw") if isinstance(matched_market.get("raw"), dict) else {}
    resolved_condition_id = str(matched_market.get("condition_id") or condition_id or raw.get("conditionId") or "").strip()
    yes_token = str(matched_market.get("yes_token") or raw.get("yes_token") or raw.get("yesToken") or "").strip()
    no_token = str(matched_market.get("no_token") or raw.get("no_token") or raw.get("noToken") or "").strip()
    token_text = str(token_id or "").strip()
    if token_text and token_text not in {yes_token, no_token}:
        if not yes_token:
            yes_token = token_text
        elif not no_token:
            no_token = token_text
    question = str(
        matched_market.get("question")
        or matched_market.get("display_name")
        or matched_market.get("slug")
        or raw.get("question")
        or raw.get("slug")
        or resolved_condition_id
        or token_text
        or "Manual Market"
    ).strip()
    yes_bid = _safe_float(matched_market.get("best_bid"))
    yes_ask = _safe_float(matched_market.get("best_ask"))
    yes_last = _safe_float(matched_market.get("yes_price"))
    no_last = _safe_float(matched_market.get("no_price"))
    no_bid = _safe_float(matched_market.get("no_bid"))
    no_ask = _safe_float(matched_market.get("no_ask"))
    yes_qty = _safe_float(matched_market.get("yes_qty") or raw.get("yes_qty")) or 0.0
    no_qty = _safe_float(matched_market.get("no_qty") or raw.get("no_qty")) or 0.0
    yes_avg = _safe_float(matched_market.get("yes_avg") or matched_market.get("yes_avg_cost") or raw.get("yes_avg"))
    no_avg = _safe_float(matched_market.get("no_avg") or matched_market.get("no_avg_cost") or raw.get("no_avg"))
    yes_position = _safe_float(matched_market.get("yes_position") or raw.get("yes_position"))
    no_position = _safe_float(matched_market.get("no_position") or raw.get("no_position"))
    position = _safe_float(
        matched_market.get("position")
        or matched_market.get("position_qty")
        or matched_market.get("qty")
        or raw.get("position")
        or raw.get("position_qty")
        or raw.get("qty")
    )
    avg = _safe_float(matched_market.get("avg") or matched_market.get("avg_price") or raw.get("avg"))
    return {
        "row_id": None,
        "leg_index": matched_market.get("leg_index"),
        "strategy": "Manual Market",
        "question": question,
        "subject": None,
        "display_name": question,
        "condition_id": resolved_condition_id,
        "yes_token": yes_token,
        "no_token": no_token,
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "yes_last_price": yes_last if yes_last is not None else yes_ask,
        "no_bid": no_bid,
        "no_ask": no_ask,
        "no_last_price": no_last if no_last is not None else no_ask,
        "yes_qty": yes_qty,
        "no_qty": no_qty,
        "yes_avg": yes_avg,
        "no_avg": no_avg,
        "yes_position": yes_position if yes_position is not None else 0.0,
        "no_position": no_position if no_position is not None else 0.0,
        "position": position,
        "qty": _safe_float(matched_market.get("qty") or matched_market.get("position_qty") or raw.get("qty")) or position,
        "avg": avg,
        "yes_current_pct": 0.0,
        "no_current_pct": 0.0,
        "strategy_bankroll": 0.0,
        "strategy_pnl": 0.0,
        "price_source": "market_cache" if matched_market else "manual_selection",
        "position_source": "manual_selection",
        "market_updated_at": raw.get("updatedAt") or matched_market.get("updated_at"),
        "realtime_snapshot_db_path": "",
        "end_date": matched_market.get("end_date") or raw.get("endDate"),
        "market_category": matched_market.get("category"),
        "slug": matched_market.get("slug") or raw.get("slug"),
        "event_slug": matched_market.get("event_slug") or raw.get("eventSlug") or raw.get("event_slug"),
        "group_item_title": matched_market.get("group_item_title") or raw.get("groupItemTitle"),
        "url": matched_market.get("url") or raw.get("url"),
        "matched_market_raw": raw or matched_market,
        "editable": {},
        "binary_identity_status": "ok" if yes_token or no_token or resolved_condition_id else "missing",
        "leg_kind": matched_market.get("leg_kind") or raw.get("leg_kind") or "binary_market",
        "asset_class": matched_market.get("asset_class") or raw.get("asset_class") or "polymarket_binary",
        "venue": matched_market.get("venue") or raw.get("venue") or "polymarket",
        "symbol": matched_market.get("symbol") or raw.get("symbol") or "",
        "instrument_id": matched_market.get("instrument_id") or raw.get("instrument_id") or "",
        "market_selected_manually": True,
        "raw": raw or matched_market,
    }


def _market_target_detail_from_payload(target: Dict[str, Any]) -> Dict[str, Any] | None:
    binance_detail = _binance_target_detail_from_leg(target, int(target.get("leg_index") or 0))
    if binance_detail is not None:
        return binance_detail
    condition_id = str(target.get("condition_id") or "").strip()
    yes_token = str(target.get("yes_token") or "").strip()
    no_token = str(target.get("no_token") or "").strip()
    token_id = str(target.get("token_id") or yes_token or no_token or "").strip()
    if not (condition_id or yes_token or no_token or token_id):
        return None
    market = {
        "condition_id": condition_id,
        "yes_token": yes_token,
        "no_token": no_token,
        "question": target.get("question") or target.get("label") or condition_id or token_id,
        "slug": target.get("slug") or "",
        "event_slug": target.get("event_slug") or target.get("eventSlug") or "",
        "group_item_title": target.get("group_item_title") or target.get("groupItemTitle") or "",
        "url": target.get("url") or "",
        "category": target.get("category") or "Workspace",
        "display_name": target.get("display_name") or target.get("name") or "",
        "leg_kind": target.get("leg_kind") or target.get("kind") or "",
        "asset_class": target.get("asset_class") or "",
        "venue": target.get("venue") or target.get("source") or "",
        "symbol": target.get("symbol") or "",
        "instrument_id": target.get("instrument_id") or "",
        "leg_index": target.get("leg_index"),
        "updated_at": target.get("market_updated_at") or target.get("updated_at"),
        "best_bid": target.get("yes_bid"),
        "best_ask": target.get("yes_ask") or target.get("yes_mark"),
        "yes_price": target.get("yes_ask") or target.get("yes_mark"),
        "no_bid": target.get("no_bid"),
        "no_ask": target.get("no_ask") or target.get("no_mark"),
        "no_price": target.get("no_ask") or target.get("no_mark"),
        "yes_qty": target.get("yes_qty"),
        "no_qty": target.get("no_qty"),
        "yes_avg": target.get("yes_avg") or target.get("yes_avg_cost"),
        "no_avg": target.get("no_avg") or target.get("no_avg_cost"),
        "yes_position": target.get("yes_position"),
        "no_position": target.get("no_position"),
        "position": target.get("position") or target.get("position_qty"),
        "qty": target.get("qty") or target.get("position_qty"),
        "avg": target.get("avg") or target.get("avg_price"),
        "raw": target.get("raw") or target,
    }
    return _chart_market_detail_from_market(market, condition_id=condition_id, token_id=token_id)


def _strategy_leg_market_targets(
    row_id: int,
    base_detail: Dict[str, Any] | None = None,
    *,
    allow_clob_book: bool = False,
) -> List[Dict[str, Any]]:
    """Expand a strategy into one chart target per leg/market."""
    try:
        strategy = strategy_data_source.get_strategy(row_id)
    except Exception:
        strategy = None
    if not strategy:
        try:
            strategy = get_backtest_workspace_strategy(row_id)
        except Exception:
            strategy = None
    primary_detail = base_detail or fetch_strategy_detail(
        row_id,
        allow_remote_positions=False,
        allow_clob_book=allow_clob_book,
    )
    if not strategy:
        return [{"detail": primary_detail, "origin": "strategy", "is_primary": True}]

    legs = sorted(strategy.get("legs") or [], key=lambda item: int(item.get("leg_index") or 0))
    if not legs:
        return [{"detail": primary_detail, "origin": "strategy", "is_primary": True}]

    output: List[Dict[str, Any]] = []
    seen: set[str] = set()
    primary_identity = _leg_identity(primary_detail)
    snapshot_by_index = {
        int(snapshot.get("leg_index") or 0): snapshot
        for snapshot in (primary_detail.get("legs_snapshot") or [])
        if isinstance(snapshot, dict)
    }
    if len(snapshot_by_index) < len(legs):
        for snapshot in get_strategy_leg_snapshots(
            row_id,
            strategy,
            include_realtime_prices=True,
            allow_clob_book=allow_clob_book,
        ):
            if isinstance(snapshot, dict):
                snapshot_by_index[int(snapshot.get("leg_index") or 0)] = snapshot
    for index, leg in enumerate(legs):
        leg_index = int(leg.get("leg_index") or index)
        snapshot = snapshot_by_index.get(leg_index) or {}
        instrument = leg.get("instrument_json") if isinstance(leg.get("instrument_json"), dict) else {}
        leg_name = preferred_leg_display_name(
            leg,
            snapshot,
            fallback=f"Leg {leg_index + 1}",
        )
        binance_detail = _binance_target_detail_from_leg(leg, leg_index)
        if binance_detail is not None:
            detail = binance_detail
            detail.update(
                {
                    "display_name": leg_name,
                    "question": leg_name,
                    "leg_kind": leg.get("leg_kind") or "position",
                    "position": leg.get("position") or leg.get("position_qty") or leg.get("qty") or leg.get("yes_qty"),
                    "qty": leg.get("qty") or leg.get("position_qty") or leg.get("yes_qty"),
                    "avg": leg.get("avg") or leg.get("avg_price") or leg.get("yes_avg_cost"),
                }
            )
        else:
            condition_id = str(leg.get("condition_id") or "").strip()
            yes_token = str(leg.get("yes_token") or "").strip()
            no_token = str(leg.get("no_token") or "").strip()
            token_id = yes_token or no_token
            if index == 0 and primary_identity:
                detail = dict(primary_detail)
                detail.update(
                    {
                        "question": leg_name,
                        "display_name": leg_name,
                        "leg_index": leg_index,
                        "yes_qty": snapshot.get("yes_qty", leg.get("yes_qty")),
                        "no_qty": snapshot.get("no_qty", leg.get("no_qty")),
                        "yes_avg": snapshot.get("yes_avg", leg.get("yes_avg_cost")),
                        "no_avg": snapshot.get("no_avg", leg.get("no_avg_cost")),
                        "yes_position": snapshot.get("yes_position"),
                        "no_position": snapshot.get("no_position"),
                        "yes_bid": snapshot.get("yes_bid"),
                        "yes_ask": snapshot.get("yes_ask") or snapshot.get("yes_mark"),
                        "no_bid": snapshot.get("no_bid"),
                        "no_ask": snapshot.get("no_ask") or snapshot.get("no_mark"),
                        "market_updated_at": snapshot.get("market_updated_at") or snapshot.get("updated_at"),
                        "leg_kind": leg.get("leg_kind") or detail.get("leg_kind") or "binary_market",
                        "asset_class": leg.get("asset_class") or detail.get("asset_class") or "polymarket_binary",
                        "venue": leg.get("venue") or detail.get("venue") or "polymarket",
                    }
                )
            else:
                detail = _market_target_detail_from_payload(
                    {
                        "condition_id": condition_id,
                        "yes_token": yes_token,
                        "no_token": no_token,
                        "token_id": token_id,
                        "label": leg_name,
                        "question": leg_name,
                        "display_name": leg_name,
                        "leg_kind": leg.get("leg_kind") or "binary_market",
                        "asset_class": leg.get("asset_class") or "polymarket_binary",
                        "venue": leg.get("venue") or "polymarket",
                        "yes_qty": snapshot.get("yes_qty", leg.get("yes_qty")),
                        "no_qty": snapshot.get("no_qty", leg.get("no_qty")),
                        "yes_avg": snapshot.get("yes_avg", leg.get("yes_avg_cost")),
                        "no_avg": snapshot.get("no_avg", leg.get("no_avg_cost")),
                        "yes_position": snapshot.get("yes_position"),
                        "no_position": snapshot.get("no_position"),
                        "yes_bid": snapshot.get("yes_bid"),
                        "yes_ask": snapshot.get("yes_ask") or snapshot.get("yes_mark"),
                        "no_bid": snapshot.get("no_bid"),
                        "no_ask": snapshot.get("no_ask") or snapshot.get("no_mark"),
                        "market_updated_at": snapshot.get("market_updated_at") or snapshot.get("updated_at"),
                    }
                )
                if detail is None:
                    detail = _chart_market_detail_from_market(
                        {
                            "condition_id": condition_id,
                            "yes_token": yes_token,
                            "no_token": no_token,
                            "question": leg_name,
                            "display_name": leg_name,
                            "yes_qty": snapshot.get("yes_qty", leg.get("yes_qty")),
                            "no_qty": snapshot.get("no_qty", leg.get("no_qty")),
                            "yes_avg": snapshot.get("yes_avg", leg.get("yes_avg_cost")),
                            "no_avg": snapshot.get("no_avg", leg.get("no_avg_cost")),
                            "yes_position": snapshot.get("yes_position"),
                            "no_position": snapshot.get("no_position"),
                            "best_bid": snapshot.get("yes_bid"),
                            "best_ask": snapshot.get("yes_ask") or snapshot.get("yes_mark"),
                            "no_bid": snapshot.get("no_bid"),
                            "no_ask": snapshot.get("no_ask") or snapshot.get("no_mark"),
                            "leg_kind": leg.get("leg_kind") or "binary_market",
                            "asset_class": leg.get("asset_class") or "polymarket_binary",
                            "venue": leg.get("venue") or "polymarket",
                            "category": "Workspace",
                            "raw": leg,
                        },
                        condition_id=condition_id,
                        token_id=token_id,
                    )
        detail["leg_index"] = leg_index
        detail["budget_cap"] = leg.get("budget_cap")
        detail["params_json"] = leg.get("params_json")
        identity = _target_identity(detail)
        if not identity.strip("|") or identity in seen:
            continue
        seen.add(identity)
        output.append(
            {
                "detail": detail,
                "origin": "strategy_leg",
                "is_primary": not output,
                "leg_index": leg_index,
            }
        )
    return output or [{"detail": primary_detail, "origin": "strategy", "is_primary": True}]


def _resolve_compare_details(
    row_id: int,
    args: Dict[str, Any],
    base_detail: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    raw_targets = _parse_json_list(args.get("market_targets_json"))
    if not raw_targets:
        return _strategy_leg_market_targets(row_id, base_detail=base_detail, allow_clob_book=False)
    output: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for target in raw_targets:
        if not isinstance(target, dict):
            continue
        target_type = str(target.get("type") or "market").strip().lower()
        if target_type == "strategy":
            for expanded in _strategy_leg_market_targets(
                int(target.get("row_id") or row_id),
                allow_clob_book=False,
            ):
                detail = expanded["detail"]
                identity = _target_identity(detail)
                if not identity.strip("|") or identity in seen:
                    continue
                seen.add(identity)
                output.append(expanded)
            continue
        else:
            detail = _market_target_detail_from_payload(target)
            if detail is None:
                condition_id = str(target.get("condition_id") or "").strip()
                token_id = str(target.get("yes_token") or target.get("no_token") or target.get("token_id") or "").strip()
                query = str(target.get("question") or target.get("label") or "").strip()
                if condition_id or token_id:
                    detail = _chart_market_detail_from_market(target, condition_id=condition_id, token_id=token_id)
                else:
                    resolved = resolve_market_selection(query=query, condition_id=condition_id, token_id=token_id, limit=20)
                    detail = _chart_market_detail_from_market(
                        resolved.get("selected"),
                        condition_id=condition_id,
                        token_id=token_id,
                    )
        identity = _target_identity(detail)
        if not identity.strip("|") or identity in seen:
            continue
        seen.add(identity)
        output.append(
            {
                "detail": detail,
                "origin": target_type,
                "is_primary": bool(target.get("is_primary", not output)),
                "leg_index": target.get("leg_index", detail.get("leg_index", len(output))),
            }
        )
    if not output:
        base_detail = base_detail or fetch_strategy_detail(
            row_id,
            allow_remote_positions=False,
            allow_clob_book=False,
        )
        return [{"detail": base_detail, "origin": "strategy", "is_primary": True}]
    return output


def _indicator_config(args: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Any]:
    base = defaults.get("indicator_config") if isinstance(defaults.get("indicator_config"), dict) else {}
    raw = _parse_json_object(args.get("indicator_config_json"))
    ma_raw = raw.get("ma") if isinstance(raw.get("ma"), dict) else (base.get("ma") if isinstance(base.get("ma"), dict) else {})
    macd_raw = raw.get("macd") if isinstance(raw.get("macd"), dict) else (base.get("macd") if isinstance(base.get("macd"), dict) else {})

    def _num(value: Any, fallback: int, low: int, high: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = fallback
        return max(low, min(high, parsed))

    return {
        "ma": {
            "enabled": bool(ma_raw.get("enabled", False)),
            "window": _num(ma_raw.get("window"), 9, 2, 240),
        },
        "macd": {
            "enabled": bool(macd_raw.get("enabled", False)),
            "fast": _num(macd_raw.get("fast"), 12, 2, 60),
            "slow": _num(macd_raw.get("slow"), 26, 3, 120),
            "signal": _num(macd_raw.get("signal"), 9, 2, 60),
        },
    }


def _series_style_overrides(args: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    raw = _parse_json_object(args.get("series_style_json"))
    result: Dict[str, Dict[str, Any]] = {}
    for key, payload in raw.items():
        if not isinstance(payload, dict):
            continue
        line_type = str(payload.get("line_type") or "solid").strip().lower()
        if line_type not in {"solid", "dashed", "dotted"}:
            line_type = "solid"
        color = str(payload.get("color") or "").strip()
        try:
            width = float(payload.get("width", 2))
        except (TypeError, ValueError):
            width = 2.0
        macd_payload = payload.get("macd") if isinstance(payload.get("macd"), dict) else {}
        result[str(key)] = {
            "color": color,
            "width": max(1.0, min(6.0, width)),
            "line_type": line_type,
            "smooth": bool(payload.get("smooth", False)),
            "show_symbol": bool(payload.get("show_symbol", False)),
            "visible": payload.get("visible", True) is not False,
            "macd": {
                "enabled": bool(macd_payload.get("enabled", False)),
                "fast": max(2, min(60, int(macd_payload.get("fast", 12) or 12))),
                "slow": max(3, min(120, int(macd_payload.get("slow", 26) or 26))),
                "signal": max(2, min(60, int(macd_payload.get("signal", 9) or 9))),
            },
        }
    return result


def _resolve_chart_detail(row_id: int, args: Dict[str, Any]) -> tuple[Dict[str, Any], List[Dict[str, Any]], Dict[str, Any]]:
    strategy_detail = fetch_strategy_detail(
        row_id,
        allow_remote_positions=False,
        allow_clob_book=False,
    )
    targets = _resolve_compare_details(row_id, args, base_detail=strategy_detail)
    primary = next((item["detail"] for item in targets if item.get("is_primary")), targets[0]["detail"])
    return strategy_detail, targets, {
        "type": "market_collection",
        "label": primary.get("question") or primary.get("display_name") or f"Strategy {row_id}",
        "row_id": row_id,
        "items": [
            {
                "label": target["detail"].get("question") or target["detail"].get("display_name"),
                "name": target["detail"].get("display_name") or target["detail"].get("question"),
                "type": target["detail"].get("source") or target.get("origin"),
                "source": target["detail"].get("source") or "",
                "venue": target["detail"].get("venue") or "",
                "asset_class": target["detail"].get("asset_class") or "",
                "symbol": target["detail"].get("symbol") or "",
                "interval": target["detail"].get("interval") or "",
                "instrument_id": target["detail"].get("instrument_id") or "",
                "condition_id": target["detail"].get("condition_id"),
                "yes_token": target["detail"].get("yes_token"),
                "no_token": target["detail"].get("no_token"),
                "leg_kind": target["detail"].get("leg_kind") or "",
                "leg_type": "polymarket_binary" if _is_binary_leg_detail(target["detail"]) else "position",
                "origin": target.get("origin"),
                "is_primary": bool(target.get("is_primary")),
                "leg_index": target.get("leg_index", target["detail"].get("leg_index")),
            }
            for target in targets
        ],
    }


def _indicator_source_key(detail: Dict[str, Any], main_side: str) -> str:
    if _is_binance_detail(detail):
        return "market_0_close"
    if main_side == "no" and detail.get("no_token"):
        return "no_ask"
    if main_side == "yes" and detail.get("yes_token"):
        return "yes_ask"
    return "yes_ask" if detail.get("yes_token") else "no_ask"


def _ema(values: List[float | None], period: int) -> List[float | None]:
    alpha = 2.0 / (period + 1.0)
    result: List[float | None] = []
    previous: float | None = None
    for value in values:
        if value is None:
            result.append(previous)
            continue
        if previous is None:
            previous = value
        else:
            previous = (value * alpha) + (previous * (1.0 - alpha))
        result.append(previous)
    return result


def _apply_indicator_series(
    rows: List[Dict[str, Any]],
    series_items: List[Dict[str, Any]],
    detail: Dict[str, Any],
    main_side: str,
    indicator_cfg: Dict[str, Any],
    style_overrides: Dict[str, Dict[str, Any]],
) -> None:
    if not rows:
        return
    source_key = _indicator_source_key(detail, main_side)
    base_values = [_safe_float(row.get(source_key)) for row in rows]

    ma_cfg = indicator_cfg.get("ma") or {}
    if ma_cfg.get("enabled"):
        window = max(2, int(ma_cfg.get("window") or 9))
        ma_key = f"indicator_ma_{window}"
        for index, row in enumerate(rows):
            history = [value for value in base_values[max(0, index - window + 1): index + 1] if value is not None]
            if len(history) == window:
                row[ma_key] = sum(history) / len(history)
        series_items.append(
            {
                "key": ma_key,
                "label": f"MA({window})",
                "panel": "main",
                "render": "line",
                "unit": "price",
                "category": "indicator_ma",
                "source_label": "主图均线",
                "source_detail": f"基于 {source_key} 计算 · 窗口 {window}",
                "removable": True,
                "style": style_overrides.get(ma_key, {"line_type": "dashed", "width": 2, "smooth": True, "show_symbol": False}),
            }
        )


def _apply_series_macd_overlays(
    rows: List[Dict[str, Any]],
    series_items: List[Dict[str, Any]],
    style_overrides: Dict[str, Dict[str, Any]],
) -> None:
    if not rows:
        return
    generated: List[Dict[str, Any]] = []
    for item in list(series_items):
        base_key = str(item.get("key") or "")
        if not base_key or item.get("render") == "bar" or "__macd" in base_key:
            continue
        style_cfg = style_overrides.get(base_key) or {}
        macd_cfg = style_cfg.get("macd") if isinstance(style_cfg.get("macd"), dict) else {}
        if not macd_cfg.get("enabled"):
            continue
        try:
            fast = max(2, int(macd_cfg.get("fast") or 12))
            slow = max(fast + 1, int(macd_cfg.get("slow") or 26))
            signal = max(2, int(macd_cfg.get("signal") or 9))
        except (TypeError, ValueError):
            fast, slow, signal = 12, 26, 9
        base_values = [_safe_float(row.get(base_key)) for row in rows]
        ema_fast = _ema(base_values, fast)
        ema_slow = _ema(base_values, slow)
        macd_values: List[float | None] = []
        for fast_value, slow_value in zip(ema_fast, ema_slow):
            if fast_value is None or slow_value is None:
                macd_values.append(None)
            else:
                macd_values.append(fast_value - slow_value)
        signal_values = _ema(macd_values, signal)
        macd_key = f"{base_key}__macd"
        signal_key = f"{base_key}__macd_signal"
        for row, macd_value, signal_value in zip(rows, macd_values, signal_values):
            row[macd_key] = macd_value
            row[signal_key] = signal_value
        generated.extend(
            [
                {
                    "key": macd_key,
                    "label": f"{item['label']} MACD",
                    "panel": "indicator_macd",
                    "render": "line",
                    "unit": "number",
                    "category": "macd_overlay",
                    "source_label": "MACD 衍生线",
                    "source_detail": f"基于 {item['label']} 计算 · 快线 {fast} / 慢线 {slow}",
                    "removable": True,
                    "style": style_overrides.get(macd_key, {"width": 2, "line_type": "solid", "smooth": True, "show_symbol": False}),
                },
                {
                    "key": signal_key,
                    "label": f"{item['label']} 信号线",
                    "panel": "indicator_macd",
                    "render": "line",
                    "unit": "number",
                    "category": "macd_overlay",
                    "source_label": "MACD 信号线",
                    "source_detail": f"基于 {item['label']} 计算 · 信号线周期 {signal}",
                    "removable": True,
                    "style": style_overrides.get(signal_key, {"width": 2, "line_type": "dashed", "smooth": True, "show_symbol": False}),
                },
            ]
        )
    series_items.extend(generated)

def _attach_style_metadata(series_items: List[Dict[str, Any]], style_overrides: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    for item in series_items:
        merged = dict(item)
        if "style" not in merged and item.get("key") in style_overrides:
            merged["style"] = style_overrides.get(str(item.get("key"))) or {}
        output.append(merged)
    return output


def get_strategy_chart(row_id: int, args: Dict[str, Any]) -> Dict[str, Any]:
    t0 = time.perf_counter()
    print(f"[SV][chart] start row_id={row_id}")
    t_detail0 = time.perf_counter()
    detail, market_targets, target_meta = _resolve_chart_detail(row_id, args)
    t_detail1 = time.perf_counter()
    print(
        f"[SV][chart] resolve_chart_detail {(t_detail1 - t_detail0) * 1000:.1f}ms targets={len(market_targets)}"
    )
    defaults = get_strategy_chart_defaults(detail)
    capabilities = get_strategy_chart_capabilities(detail)
    virtual_metric_catalog = _virtual_tick_metric_catalog(detail)
    if virtual_metric_catalog.get("items"):
        capabilities = {
            **capabilities,
            "metric_catalog": _merge_metric_catalog(capabilities.get("metric_catalog") or {}, virtual_metric_catalog),
        }
    backtest_run_id = _safe_int(args.get("backtest_run_id"))
    if backtest_run_id:
        capabilities = {
            **capabilities,
            "metric_catalog": _merge_metric_catalog(
                capabilities.get("metric_catalog") or {},
                get_backtest_metric_catalog(backtest_run_id),
            ),
        }
    interval = str(args.get("interval") or defaults.get("interval") or "5s")
    interval_seconds = _parse_seconds(interval)
    from_ts, to_ts = _resolve_time_bounds(args, defaults)
    main_side = str(args.get("main_side") or defaults.get("main_side") or "all").strip().lower()
    indicator_cfg = _indicator_config(args, defaults)
    style_overrides = _series_style_overrides(args)
    explicit_sub_metrics = bool(str(args.get("sub_metrics") or "").strip())
    selected_series = []
    selected_series.extend(_requested_sub_series(args, defaults, capabilities))
    has_binance_targets = any(_is_binance_detail(target.get("detail") or {}) for target in market_targets)
    if has_binance_targets:
        selected_series = [
            item for item in selected_series
            if item not in {"yes_position", "no_position", "yes_qty", "no_qty", "yes_avg", "no_avg"}
        ]
    # `_requested_sub_series` already supplies the workspace defaults. Do not
    # append strategy metrics behind the user's back: optional metrics are
    # explicitly selected from the chart drawer.
    sub_metrics_debug = _sub_series_debug(args, defaults, capabilities)
    selected_metric_keys, selected_state_metric_keys = _selected_metric_keys(selected_series)
    static_selected_series = [item for item in selected_series if not item.startswith("metric:") and not item.startswith("metric_state:")]
    overlay_crypto = _allowed_overlay_symbols(args, capabilities, "overlay_crypto")
    overlay_finance = _allowed_overlay_symbols(args, capabilities, "overlay_finance")
    overlay_crypto_fields = _selected_overlay_fields(args, defaults, capabilities, "crypto")
    overlay_finance_fields = _selected_overlay_fields(args, defaults, capabilities, "finance")
    if backtest_run_id:
        static_selected_series = [
            item for item in static_selected_series
            if item not in {"strategy_pnl", "strategy_bankroll", "initial_capital", "realized_profit", "profit_roll_ratio"}
        ]
    dynamic_position_tokens = {
        "yes_position",
        "no_position",
        "position",
        "yes_qty",
        "no_qty",
        "qty",
        "yes_avg",
        "no_avg",
        "avg",
    }
    static_selected_series = [
        item
        for item in static_selected_series
        if item not in dynamic_position_tokens and _leg_sub_metric_parts(item) is None
    ]
    series_items = _market_series_items(market_targets, main_side)
    series_items.extend(_leg_position_series_items(market_targets, selected_series))
    series_items.extend(_static_series_items(static_selected_series, detail))
    series_items.extend(_metric_series_items(selected_metric_keys, capabilities))
    series_items.extend(_overlay_series_items("crypto", overlay_crypto, overlay_crypto_fields))
    series_items.extend(_overlay_series_items("finance", overlay_finance, overlay_finance_fields))
    t_samples0 = time.perf_counter()
    market_price_maps = []
    market_detail_samples = []
    leg_position_samples = []
    for index, target in enumerate(market_targets):
        prefix = f"market_{index}_"
        is_binance_target = _is_binance_detail(target["detail"])
        price_sample_map = (
            _load_binance_kline_samples(target["detail"], from_ts, to_ts, interval_seconds)
            if is_binance_target
            else _load_price_samples(target["detail"], from_ts, to_ts, interval_seconds)
        )
        if index == 0 and not is_binance_target:
            tick_price_sample_map = _load_strategy_tick_price_samples(detail, from_ts, to_ts, interval_seconds)
            price_sample_map = _overlay_sample_maps(tick_price_sample_map, price_sample_map)
        detail_sample_map = {} if is_binance_target else _detail_sample(target["detail"], interval_seconds, from_ts, to_ts)
        leg_position_samples.append(
            _prefix_sample_map(
                _leg_position_sample(target["detail"], interval_seconds, from_ts, to_ts),
                prefix,
            )
        )
        if index == 0 or is_binance_target:
            market_price_maps.append(_prefix_sample_map(price_sample_map, prefix))
            market_detail_samples.append(
                _prefix_sample_map(_select_sample_keys(detail_sample_map, _PRICE_DETAIL_KEYS), prefix)
            )
        else:
            market_price_maps.append(_watch_market_sample_map(price_sample_map, prefix, main_side))
            market_detail_samples.append(_watch_market_sample_map(detail_sample_map, prefix, main_side))
    needs_stats = bool(set(static_selected_series) & _STATS_DETAIL_KEYS)
    stats_detail_sample = (
        _select_sample_keys(_detail_sample(detail, interval_seconds, from_ts, to_ts), _STATS_DETAIL_KEYS)
        if needs_stats
        else {}
    )
    price_samples = market_price_maps[0] if market_price_maps else {}
    stats_samples = _load_stats_samples(detail, from_ts, to_ts, interval_seconds) if needs_stats else {}
    metric_samples = _overlay_sample_maps(
        _load_metric_numeric_samples(detail, selected_metric_keys, from_ts, to_ts, interval_seconds),
        _load_virtual_tick_metric_samples(detail, selected_metric_keys, from_ts, to_ts, interval_seconds),
    )
    metric_state_lanes = _metric_state_lanes(detail, selected_state_metric_keys, from_ts, to_ts, capabilities)
    crypto_overlay_samples = _load_crypto_overlay_samples(
        from_ts, to_ts, interval_seconds, overlay_crypto, overlay_crypto_fields
    )
    finance_overlay_samples = _load_finance_overlay_samples(
        from_ts, to_ts, interval_seconds, overlay_finance, overlay_finance_fields
    )
    backtest_samples, backtest_events, backtest_run = _load_backtest_overlay_samples(
        backtest_run_id, from_ts, to_ts, interval_seconds
    )
    backtest_metric_samples = _load_backtest_metric_numeric_samples(
        backtest_run, selected_metric_keys, from_ts, to_ts, interval_seconds
    )
    backtest_state_lanes = (
        _backtest_position_state_lanes(backtest_samples, from_ts, to_ts)
        if "backtest_position_state" in selected_state_metric_keys
        else []
    )
    backtest_metric_state_lanes = _load_backtest_metric_state_lanes(
        backtest_run, selected_state_metric_keys, from_ts, to_ts, capabilities
    )
    if backtest_samples:
        selected_backtest_metric_keys = set(selected_metric_keys)
        series_items.extend(
            dict(item)
            for item in _BACKTEST_SERIES_CONFIG
            if item.get("category") != "backtest_metric" and item.get("key") not in selected_backtest_metric_keys
        )
    if backtest_state_lanes:
        metric_state_lanes.extend(backtest_state_lanes)
    if backtest_metric_state_lanes:
        metric_state_lanes.extend(backtest_metric_state_lanes)
    t_samples1 = time.perf_counter()
    print(
        f"[SV][chart] load_samples {(t_samples1 - t_samples0) * 1000:.1f}ms price_maps={len(market_price_maps)} stats={len(stats_samples)} backtest={len(backtest_samples)}"
    )
    stats_db_path = get_strategy_stats_db_path(detail)
    t_merge0 = time.perf_counter()
    if needs_stats and not stats_samples:
        stats_samples = _derive_stats_from_price_samples(detail, price_samples)
    rows = _merge_samples(
        *market_price_maps,
        stats_samples,
        metric_samples,
        stats_detail_sample,
        *leg_position_samples,
        crypto_overlay_samples,
        finance_overlay_samples,
        backtest_samples,
        backtest_metric_samples,
        *market_detail_samples,
    )
    current_snapshot_ts = _append_current_multileg_snapshot(rows, detail, from_ts, to_ts)
    _sync_row_pnl_to_visible_prices(rows)
    virtual_trade_events = _apply_virtual_account_pnl_to_rows(detail, rows, chart_interval_seconds=interval_seconds)
    if current_snapshot_ts and detail.get("strategy_pnl") is not None and _can_overlay_detail_pnl(detail):
        current_row = next((row for row in reversed(rows) if row.get("ts") == current_snapshot_ts), None)
        if current_row is not None:
            current_row["strategy_pnl"] = _safe_float(detail.get("strategy_pnl"))
            current_row["pnl_source"] = detail.get("pnl_source") or "multi_leg_liquidation_sum"
    _apply_indicator_series(rows, series_items, detail, main_side, indicator_cfg, style_overrides)
    _apply_series_macd_overlays(rows, series_items, style_overrides)
    series_items = _attach_style_metadata(series_items, style_overrides)
    sub_series_catalog = _leg_position_series_items(
        market_targets,
        _all_leg_sub_metric_tokens(market_targets),
    )
    if not backtest_run_id:
        sub_series_catalog.extend(_static_series_items(["strategy_pnl"], detail))
    sub_series_catalog = _attach_style_metadata(sub_series_catalog, style_overrides)
    t_merge1 = time.perf_counter()
    print(
        f"[SV][chart] merge_and_indicator {(t_merge1 - t_merge0) * 1000:.1f}ms rows={len(rows)} series={len(series_items)}"
    )
    if not rows:
        rows = [{"ts": to_ts}]

    include_events = str(args.get("include_events") or "0").strip().lower() in {"1", "true", "yes", "on"}
    events = []
    if include_events and detail.get("row_id"):
        t_event0 = time.perf_counter()
        events_payload = list_strategy_events(
            row_id,
            {"limit": 80, "from": from_ts, "to": to_ts},
            detail=detail,
        )
        events = [_chart_event_item(item) for item in events_payload.get("data") or []]
        t_event1 = time.perf_counter()
        print(f"[SV][chart] list_strategy_events {(t_event1 - t_event0) * 1000:.1f}ms events={len(events)}")

    # For virtual strategies, replace raw per-order trade events with bucket-aggregated ones
    # so that tooltip Trades align exactly with Position/PnL at the same bucket timestamp.
    if include_events and virtual_trade_events:
        events = [ev for ev in events if str(ev.get("type") or "").lower() not in ("trade", "fill", "order")]
        events.extend(virtual_trade_events)
    if include_events and backtest_events:
        events.extend(backtest_events)
        events.sort(key=lambda item: str(item.get("ts") or ""))

    state_lane_panel_title = _PANEL_TITLES["metric_states"]
    if metric_state_lanes and all(str(lane.get("key") or "").startswith("backtest_") for lane in metric_state_lanes):
        state_lane_panel_title = _PANEL_TITLES["backtest_states"]
    elif metric_state_lanes and any(str(lane.get("key") or "").startswith("backtest_") for lane in metric_state_lanes):
        state_lane_panel_title = "State / Backtest Lanes"

    total_ms = (time.perf_counter() - t0) * 1000
    print(f"[SV][chart] total {total_ms:.1f}ms row_id={row_id} rows={len(rows)} series={len(series_items)}")
    return {
        "meta": {
            "strategy_row_id": row_id,
            "from": from_ts,
            "to": to_ts,
            "interval": interval,
            "main_side": main_side,
            "row_count": len(rows),
            "overlay_crypto": overlay_crypto,
            "overlay_finance": overlay_finance,
            "overlay_crypto_fields": overlay_crypto_fields,
            "overlay_finance_fields": overlay_finance_fields,
            "backtest_run_id": backtest_run_id or None,
            "target": target_meta,
            "indicator_config": indicator_cfg,
            "series_style_overrides": style_overrides,
            "debug": {
                **sub_metrics_debug,
                "strategy_detail_row_id": detail.get("row_id"),
                "market_target_count": len(market_targets),
                "series_count": len(series_items),
                "sub_series_catalog_count": len(sub_series_catalog),
                "metric_series_count": len([item for item in series_items if item.get("category") == "strategy_metric"]),
                "state_lane_count": len(metric_state_lanes),
                "backtest_state_lane_count": len(backtest_state_lanes),
            },
            "sources": {
                "price_history_db": str(_monitoring_db_path()),
                "binance_history_db": str(HISTORY_DB_PATH),
                "strategy_stats_db": str(stats_db_path) if stats_db_path else "",
                "current_price_source": str(detail.get("price_source") or "unknown"),
                "current_price_source_path": str(detail.get("realtime_snapshot_db_path") or ""),
                "history_price_points": len(price_samples),
                "history_stats_points": len(stats_samples),
                "history_metric_points": len(metric_samples),
                "history_backtest_points": len(backtest_samples),
                "history_backtest_metric_points": len(backtest_metric_samples),
                "history_backtest_orders": len(backtest_events),
                "history_backtest_status": str(backtest_run.get("status") or ""),
                "history_backtest_derived_metrics": len(backtest_samples) if backtest_samples else 0,
                "history_backtest_state_lanes": len(backtest_state_lanes),
                "history_backtest_metric_state_lanes": len(backtest_metric_state_lanes),
            },
        },
        "panels": [
            *_panel_list(series_items),
            *([{"id": "metric_states", "title": state_lane_panel_title}] if metric_state_lanes else []),
        ],
        "panel_catalog": [
            *_panel_list([*series_items, *sub_series_catalog]),
            *([{"id": "metric_states", "title": state_lane_panel_title}] if metric_state_lanes else []),
        ],
        "series": series_items,
        "sub_series_catalog": sub_series_catalog,
        "events": events,
        "metric_state_lanes": metric_state_lanes,
        "rows": rows,
    }
