from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict

from services.backtest_service import get_backtest_placeholder
from services.config_loader import get_market_realtime_db_path, load_web_settings
from services.realtime_collector import collector
from services.virtual_context_builder import build_use_data
from services.history_data_service import get_backtest_metric_catalog, get_backtest_workspace_strategy
from services.polymarket_service import (
    fetch_strategy_detail,
    get_strategy_leg_snapshots,
    get_strategy_chart_capabilities,
    get_strategy_chart_defaults,
    resolve_market_selection,
)
from services import strategy_data_source
from services.strategy_event_service import list_strategy_events
from services.strategy_settings_service import build_strategy_settings_schema
from services.strategy_stats_store import get_strategy_stats_db_path, strategy_metrics_db_directory
from services.strategy_chart_service import default_strategy_metric_keys
from services.strategy_display import preferred_leg_display_name
from services.workspace_preset_service import list_workspace_presets


_BACKTEST_STATE_LANE_DEFAULTS = (
    "decision",
    "position_state",
    "reason",
    "risk_state",
    "trend_state",
    "machine_state",
)
_DEFAULT_CHART_METRIC_LIMIT = 16


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _apply_multi_leg_summary_pnl(strategy: Dict[str, Any], legs_data: list[Dict[str, Any]]) -> None:
    """Use the per-leg liquidation PnL already computed from each market's book."""
    if len(legs_data) <= 1 or str(strategy.get("mode") or "").strip().lower() != "virtual":
        return
    total = 0.0
    missing_active_leg = False
    for leg in legs_data:
        qty = (float(leg.get("yes_qty") or 0.0) + float(leg.get("no_qty") or 0.0))
        pnl = leg.get("pnl")
        if pnl is None:
            missing_active_leg = missing_active_leg or qty > 0
            continue
        total += float(pnl)
    strategy["strategy_pnl"] = total
    strategy["virtual_total_pnl"] = total
    strategy["pnl_source"] = (
        "multi_leg_liquidation_sum_partial" if missing_active_leg else "multi_leg_liquidation_sum"
    )


def _path_status(path_text: str | None) -> Dict[str, Any]:
    text = str(path_text or "").strip()
    if not text:
        return {"status": "pending", "path": "", "exists": False}
    path = Path(text).expanduser()
    return {
        "status": "good" if path.exists() else "pending",
        "path": str(path),
        "exists": path.exists(),
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


def _is_backtest_derived_catalog_item(item: Dict[str, Any]) -> bool:
    meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
    key = str(item.get("key") or "")
    return meta.get("source") == "backtest_derived" or key.startswith("backtest_")


def _merge_chart_default_metric_tokens(
    chart_defaults: Dict[str, Any],
    chart_capabilities: Dict[str, Any],
    detail: Dict[str, Any],
) -> list[str]:
    merged = list(chart_defaults.get("sub_series") or [])
    for key in default_strategy_metric_keys(chart_capabilities, detail)[:_DEFAULT_CHART_METRIC_LIMIT]:
        token = f"metric:{key}"
        if token not in merged:
            merged.append(token)
    return merged


def get_strategy_workspace(row_id: int, include_events: bool = False, backtest_run_id: int | str | None = None) -> Dict[str, Any]:
    t0 = time.perf_counter()
    print(f"[SV][workspace] start row_id={row_id} include_events={include_events}")

    t_detail0 = time.perf_counter()
    # The workspace must not block its first paint on one remote CLOB request per
    # outcome. Cached realtime prices are enough for the compact header; the
    # historical chart and live stream update independently.
    detail = fetch_strategy_detail(row_id, allow_remote_positions=False, allow_clob_book=False)
    t_detail1 = time.perf_counter()
    print(f"[SV][workspace] fetch_strategy_detail {(t_detail1 - t_detail0) * 1000:.1f}ms")

    t_settings0 = time.perf_counter()
    settings = load_web_settings()
    t_settings1 = time.perf_counter()
    print(f"[SV][workspace] load_web_settings {(t_settings1 - t_settings0) * 1000:.1f}ms")

    t_stats0 = time.perf_counter()
    stats_db_path = get_strategy_stats_db_path(detail)
    t_stats1 = time.perf_counter()
    print(f"[SV][workspace] get_strategy_stats_db_path {(t_stats1 - t_stats0) * 1000:.1f}ms path={stats_db_path}")

    t_events0 = time.perf_counter()
    recent_events = list_strategy_events(row_id, {"limit": 20}, detail=detail) if include_events else {"data": []}
    t_events1 = time.perf_counter()
    print(
        f"[SV][workspace] list_strategy_events {(t_events1 - t_events0) * 1000:.1f}ms "
        f"enabled={include_events} count={len(recent_events.get('data') or [])}"
    )

    t_presets0 = time.perf_counter()
    workspace_presets = list_workspace_presets(row_id)
    t_presets1 = time.perf_counter()
    print(f"[SV][workspace] list_workspace_presets {(t_presets1 - t_presets0) * 1000:.1f}ms count={len(workspace_presets)}")

    try:
        raw_strategy = strategy_data_source.get_strategy(row_id) or {}
    except Exception:
        raw_strategy = {}
    if not raw_strategy:
        try:
            raw_strategy = get_backtest_workspace_strategy(row_id) or {}
        except Exception:
            raw_strategy = {}
    live_leg_snapshots = {
        int(snap.get("leg_index") or 0): snap
        for snap in (detail.get("legs_snapshot") or [])
        if isinstance(snap, dict)
    }
    if not live_leg_snapshots:
        for snap in get_strategy_leg_snapshots(
            row_id,
            raw_strategy,
            include_realtime_prices=True,
            allow_clob_book=False,
        ):
            if isinstance(snap, dict):
                live_leg_snapshots[int(snap.get("leg_index") or 0)] = snap
    market_legs = []
    legs_data = []
    for leg in sorted(raw_strategy.get("legs") or [], key=lambda item: int(item.get("leg_index") or 0)):
        leg_index = int(leg.get("leg_index") or 0)
        live_snap = live_leg_snapshots.get(leg_index) or {}
        instrument_json = leg.get("instrument_json") if isinstance(leg.get("instrument_json"), dict) else {}
        is_binary_leg = not (
            str(leg.get("venue") or "").strip().lower() == "binance"
            or str(leg.get("asset_class") or "").strip().lower() in {"crypto_spot", "crypto"}
        )
        leg_name = preferred_leg_display_name(
            leg,
            live_snap,
            fallback=f"Leg {leg_index + 1}",
        )
        if live_snap:
            fallback_yes_qty = live_snap.get("yes_qty")
            fallback_no_qty = live_snap.get("no_qty")
            fallback_yes_avg = live_snap.get("yes_avg")
            fallback_no_avg = live_snap.get("no_avg")
            fallback_pnl = live_snap.get("pnl")
        elif leg_index == 0:
            fallback_yes_qty = detail.get("yes_qty")
            fallback_no_qty = detail.get("no_qty")
            fallback_yes_avg = detail.get("yes_avg")
            fallback_no_avg = detail.get("no_avg")
            fallback_pnl = detail.get("strategy_pnl")
        else:
            fallback_yes_qty = leg.get("yes_qty")
            fallback_no_qty = leg.get("no_qty")
            fallback_yes_avg = leg.get("yes_avg_cost")
            fallback_no_avg = leg.get("no_avg_cost")
            fallback_pnl = leg.get("unrealized_pnl")
        yes_bid = _first_present(live_snap.get("yes_bid"), detail.get("yes_bid") if leg_index == 0 else None)
        yes_ask = _first_present(live_snap.get("yes_ask"), live_snap.get("yes_mark"), detail.get("yes_ask") if leg_index == 0 else None)
        no_bid = _first_present(live_snap.get("no_bid"), detail.get("no_bid") if leg_index == 0 else None)
        no_ask = _first_present(live_snap.get("no_ask"), live_snap.get("no_mark"), detail.get("no_ask") if leg_index == 0 else None)
        yes_position = _first_present(live_snap.get("yes_position"), detail.get("yes_position") if leg_index == 0 else None)
        no_position = _first_present(live_snap.get("no_position"), detail.get("no_position") if leg_index == 0 else None)
        market_legs.append(
            {
                "type": "binance" if str(leg.get("venue") or "").strip().lower() == "binance" or str(leg.get("asset_class") or "").strip().lower() in {"crypto_spot", "crypto"} else "market",
                "leg_index": leg_index,
                "label": f"Leg {leg_index + 1}",
                "name": leg_name,
                "display_name": leg_name,
                "question": leg_name,
                "leg_type": "polymarket_binary" if is_binary_leg else "position",
                "position_kind": "yes_no" if is_binary_leg else "position",
                "condition_id": leg.get("condition_id") or "",
                "yes_token": leg.get("yes_token") or "",
                "no_token": leg.get("no_token") or "",
                "leg_kind": leg.get("leg_kind") or "",
                "asset_class": leg.get("asset_class") or "polymarket_binary",
                "venue": leg.get("venue") or "",
                "symbol": leg.get("symbol") or "",
                "interval": (leg.get("instrument_json") or {}).get("interval") if isinstance(leg.get("instrument_json"), dict) else "",
                "instrument_id": leg.get("instrument_id") or "",
                "instrument_json": instrument_json,
                "budget_cap": leg.get("budget_cap"),
                "params_json": leg.get("params_json"),
                "direction": leg.get("direction") or "Observe",
                "weight": leg.get("weight"),
                "yes_bid": yes_bid,
                "yes_ask": yes_ask,
                "no_bid": no_bid,
                "no_ask": no_ask,
                "yes_qty": fallback_yes_qty,
                "no_qty": fallback_no_qty,
                "yes_avg": fallback_yes_avg,
                "no_avg": fallback_no_avg,
                "yes_position": yes_position,
                "no_position": no_position,
                "price_source": live_snap.get("price_source"),
                "market_updated_at": live_snap.get("market_updated_at") or live_snap.get("updated_at"),
            }
        )
        # Per-leg price/position snapshot from the same live detail path used by the workspace summary.
        legs_data.append(
            {
                "leg_index": leg_index,
                "direction": leg.get("direction") or "Observe",
                "leg_kind": leg.get("leg_kind") or "",
                "leg_type": "polymarket_binary" if is_binary_leg else "position",
                "position_kind": "yes_no" if is_binary_leg else "position",
                "name": leg_name,
                "display_name": leg_name,
                "question": leg_name,
                "asset_class": leg.get("asset_class") or "polymarket_binary",
                "venue": leg.get("venue") or "",
                "symbol": leg.get("symbol") or "",
                "instrument_id": leg.get("instrument_id") or "",
                "yes_bid": yes_bid,
                "yes_ask": yes_ask,
                "no_bid": no_bid,
                "no_ask": no_ask,
                "yes_qty": fallback_yes_qty,
                "no_qty": fallback_no_qty,
                "yes_avg": fallback_yes_avg,
                "no_avg": fallback_no_avg,
                "yes_position": yes_position,
                "no_position": no_position,
                "unrealized_pnl": fallback_pnl,
                "pnl": fallback_pnl,
                "position": leg.get("position") or leg.get("position_qty") or leg.get("qty") or leg.get("yes_qty"),
                "qty": leg.get("qty") or leg.get("position_qty") or leg.get("yes_qty"),
                "avg": leg.get("avg") or leg.get("avg_price") or leg.get("yes_avg_cost"),
                "budget_cap": leg.get("budget_cap"),
                "price_source": live_snap.get("price_source"),
                "market_updated_at": live_snap.get("market_updated_at") or live_snap.get("updated_at"),
            }
        )

    _apply_multi_leg_summary_pnl(detail, legs_data)

    has_crypto_legs = any(
        str(leg.get("venue") or "").strip().lower() == "binance"
        or str(leg.get("asset_class") or "").strip().lower() in {"crypto_spot", "crypto"}
        for leg in (raw_strategy.get("legs") or [])
    )
    chart_defaults = get_strategy_chart_defaults(detail)
    chart_capabilities = get_strategy_chart_capabilities(detail)
    if has_crypto_legs:
        chart_defaults = {
            **chart_defaults,
            "interval": "15m",
            "range": "1w",
            "main_side": "all",
            "main_series": [],
            "sub_series": ["position", "strategy_pnl"],
            "template": "crypto_spot",
        }
        chart_capabilities = {
            **chart_capabilities,
            "main_allowed": [],
            "sub_allowed": ["position", "qty", "avg", "strategy_pnl", "strategy_bankroll", "initial_capital", "realized_profit"],
        }
    # Live first load intentionally contains only leg price, every leg's
    # position, and strategy PnL. Strategy metrics remain available in the
    # picker, but are not silently appended here.
    chart_defaults = {
        **chart_defaults,
        "sub_series": list(dict.fromkeys(chart_defaults.get("sub_series") or [])),
    }
    parsed_backtest_run_id = 0
    try:
        parsed_backtest_run_id = int(str(backtest_run_id or "").strip() or "0")
    except (TypeError, ValueError):
        parsed_backtest_run_id = 0
    if parsed_backtest_run_id:
        backtest_catalog = get_backtest_metric_catalog(parsed_backtest_run_id)
        chart_capabilities = {
            **chart_capabilities,
            "metric_catalog": _merge_metric_catalog(chart_capabilities.get("metric_catalog") or {}, backtest_catalog),
        }
        backtest_defaults = [
            "metric:backtest_return",
            "metric:backtest_drawdown",
            "metric_state:backtest_position_state",
        ]
        strategy_metric_defaults = _merge_chart_default_metric_tokens(chart_defaults, chart_capabilities, detail)
        available_catalog_keys = {
            f"metric:{item.get('key')}"
            for item in chart_capabilities.get("metric_catalog", {}).get("numeric", [])
            if item.get("key")
        } | {
            f"metric_state:{item.get('key')}"
            for item in chart_capabilities.get("metric_catalog", {}).get("state", [])
            if item.get("key")
        }
        merged_sub = list(strategy_metric_defaults)
        for key in backtest_defaults:
            if key in available_catalog_keys and key not in merged_sub:
                merged_sub.append(key)
        if not any(key.startswith("metric_state:") for key in merged_sub):
            state_items = {
                str(item.get("key") or ""): item
                for item in (chart_capabilities.get("metric_catalog", {}).get("state") or [])
                if item.get("key") and not _is_backtest_derived_catalog_item(item)
            }
            for key in _BACKTEST_STATE_LANE_DEFAULTS:
                if key in state_items and f"metric_state:{key}" not in merged_sub:
                    merged_sub.append(f"metric_state:{key}")
            if not any(key.startswith("metric_state:") for key in merged_sub):
                for key in list(state_items)[:4]:
                    merged_sub.append(f"metric_state:{key}")
        chart_defaults = {
            **chart_defaults,
            "sub_series": merged_sub,
            "template": "backtest_crypto" if has_crypto_legs else chart_defaults.get("template", "backtest"),
        }

    backtest_placeholder = get_backtest_placeholder(row_id)
    payload = {
        "strategy": detail,
        "settings_schema": build_strategy_settings_schema(detail),
        "chart_defaults": chart_defaults,
        "chart_capabilities": chart_capabilities,
        "market_context": {
            "type": "strategy",
            "row_id": row_id,
            "condition_id": detail.get("condition_id"),
            "yes_token": detail.get("yes_token"),
            "no_token": detail.get("no_token"),
            "question": detail.get("question"),
            "display_name": detail.get("display_name"),
            "slug": (detail.get("matched_market_raw") or {}).get("slug"),
            "legs": market_legs,
        },
        "legs_data": legs_data,
        "workspace_presets": workspace_presets,
        "backtest": backtest_placeholder,
        "source_statuses": {
            "strategy_monitoring_db": _path_status(settings.get("strategy_monitoring_db_path")),
            "market_realtime_db": _path_status(
                detail.get("realtime_snapshot_db_path")
                or settings.get("market_realtime_db_path")
            ),
            "strategy_metrics_db_dir": _path_status(str(strategy_metrics_db_directory())),
            "strategy_metrics_db": _path_status(str(stats_db_path) if stats_db_path else ""),
            "price_source": {
                "status": "good" if detail.get("price_source") else "pending",
                "value": detail.get("price_source") or "unknown",
                "updated_at": detail.get("market_updated_at"),
            },
            "position_source": {
                "status": "good" if detail.get("position_source") else "pending",
                "value": detail.get("position_source") or "unknown",
            },
        },
        "recent_events": recent_events.get("data") or [],
    }
    total_ms = (time.perf_counter() - t0) * 1000
    print(f"[SV][workspace] total {total_ms:.1f}ms row_id={row_id}")
    return payload


def get_strategy_usedata_snapshot(row_id: int, *, include_live_orderbook: bool = True) -> Dict[str, Any]:
    strategy = strategy_data_source.get_strategy(row_id)
    if not strategy:
        raise ValueError(f"strategy {row_id} not found")
    settings = load_web_settings()
    realtime_db_path = get_market_realtime_db_path(settings)
    use_data = build_use_data(
        strategy,
        realtime_db_path,
        collector.get_state(),
        include_live_orderbook=include_live_orderbook,
    )
    return {
        "strategy_id": row_id,
        "generated_at_utc": use_data.get("NowTime"),
        "data": use_data,
    }


def get_strategy_usedata_draft(
    payload: Dict[str, Any],
    *,
    include_live_orderbook: bool = True,
) -> Dict[str, Any]:
    input_raw = payload.get("input_json")
    if isinstance(input_raw, str):
        try:
            input_params = json.loads(input_raw) if input_raw.strip() else {}
        except Exception:
            input_params = {}
    elif isinstance(input_raw, dict):
        input_params = input_raw
    else:
        input_params = {}

    condition_id = str(payload.get("condition_id") or "").strip()
    raw_legs = payload.get("legs") if isinstance(payload.get("legs"), list) else []
    if not raw_legs:
        raw_legs = [
            {
                "leg_index": 0,
                "condition_id": condition_id,
                "yes_token": payload.get("yes_token") or "",
                "no_token": payload.get("no_token") or "",
                "leg_kind": payload.get("leg_kind") or payload.get("kind") or "",
                "asset_class": payload.get("asset_class") or "polymarket_binary",
                "venue": payload.get("venue") or "polymarket",
                "symbol": payload.get("symbol") or "",
                "instrument_id": payload.get("instrument_id") or "",
                "instrument_json": payload.get("instrument_json") or {},
                "budget_cap": payload.get("budget_cap") or payload.get("strategy_bankroll") or 0,
                "params_json": "{}",
            }
        ]

    draft_legs = []
    primary_end_date = ""
    for index, raw_leg in enumerate(raw_legs):
        if not isinstance(raw_leg, dict):
            continue
        leg_condition_id = str(raw_leg.get("condition_id") or (condition_id if index == 0 else "")).strip()
        market = {}
        if leg_condition_id:
            try:
                resolved = resolve_market_selection(condition_id=leg_condition_id, limit=1)
                market = resolved.get("selected") or {}
            except Exception:
                market = {}
        end_date = market.get("end_date") or (market.get("raw") or {}).get("endDate") or ""
        if not primary_end_date and end_date:
            primary_end_date = end_date
        asset_class = raw_leg.get("asset_class") or ("polymarket_binary" if leg_condition_id else payload.get("asset_class") or "polymarket_binary")
        leg_budget = raw_leg.get("budget_cap")
        if leg_budget in (None, ""):
            leg_budget = (payload.get("budget_cap") or payload.get("strategy_bankroll") or 0) if index == 0 else 0
        leg = {
            "leg_index": index,
            "condition_id": leg_condition_id,
            "yes_token": raw_leg.get("yes_token") or market.get("yes_token") or "",
            "no_token": raw_leg.get("no_token") or market.get("no_token") or "",
            "leg_kind": raw_leg.get("leg_kind") or raw_leg.get("kind") or payload.get("leg_kind") or "",
            "asset_class": asset_class,
            "venue": raw_leg.get("venue") or ("polymarket" if asset_class == "polymarket_binary" else payload.get("venue") or ""),
            "symbol": raw_leg.get("symbol") or payload.get("symbol") or "",
            "instrument_id": raw_leg.get("instrument_id") or "",
            "instrument_json": raw_leg.get("instrument_json") or {},
            "end_date": end_date,
            "budget_cap": leg_budget,
            "params_json": raw_leg.get("params_json") or "{}",
        }
        normalized = strategy_data_source.normalize_leg_instrument(leg)
        leg["leg_kind"] = normalized.get("leg_kind") or leg["leg_kind"]
        leg["instrument_id"] = normalized.get("instrument_id") or leg["instrument_id"]
        draft_legs.append(leg)

    mode = str(payload.get("mode") or payload.get("state") or "Virtual").strip()
    strategy = {
        "strategy_id": 0,
        "strategy_name": str(payload.get("strategy_name") or "Draft Strategy").strip(),
        "strategy_code": str(payload.get("strategy_code") or "").strip(),
        "mode": mode,
        "state": "auto",
        "machine_state": "auto",
        "strategy_bankroll": payload.get("strategy_bankroll") or payload.get("budget_cap") or 0,
        "input_json": json.dumps(input_params or {}, ensure_ascii=False),
        "end_date": primary_end_date,
        "legs": draft_legs,
    }
    settings = load_web_settings()
    realtime_db_path = get_market_realtime_db_path(settings)
    use_data = build_use_data(
        strategy,
        realtime_db_path,
        collector.get_state(),
        include_live_orderbook=include_live_orderbook,
    )
    return {
        "strategy_id": None,
        "generated_at_utc": use_data.get("NowTime"),
        "data": use_data,
    }
