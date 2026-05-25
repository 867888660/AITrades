from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict

from services.backtest_service import get_backtest_placeholder
from services.config_loader import get_market_realtime_db_path, load_web_settings
from services.realtime_collector import collector
from services.virtual_context_builder import build_use_data
from services.polymarket_service import (
    fetch_strategy_detail,
    get_strategy_chart_capabilities,
    get_strategy_chart_defaults,
    resolve_market_selection,
)
from services import strategy_data_source
from services.strategy_event_service import list_strategy_events
from services.strategy_settings_service import build_strategy_settings_schema
from services.strategy_stats_store import get_strategy_stats_db_path, strategy_metrics_db_directory
from services.workspace_preset_service import list_workspace_presets


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


def get_strategy_workspace(row_id: int, include_events: bool = False) -> Dict[str, Any]:
    t0 = time.perf_counter()
    print(f"[SV][workspace] start row_id={row_id} include_events={include_events}")

    t_detail0 = time.perf_counter()
    detail = fetch_strategy_detail(row_id, allow_remote_positions=False)
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
    recent_events = list_strategy_events(row_id, {"limit": 20}) if include_events else {"data": []}
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
    live_leg_snapshots = {
        int(snap.get("leg_index") or 0): snap
        for snap in (detail.get("legs_snapshot") or [])
        if isinstance(snap, dict)
    }
    market_legs = []
    legs_data = []
    for leg in sorted(raw_strategy.get("legs") or [], key=lambda item: int(item.get("leg_index") or 0)):
        leg_index = int(leg.get("leg_index") or 0)
        live_snap = live_leg_snapshots.get(leg_index) or {}
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
        market_legs.append(
            {
                "type": "market",
                "leg_index": leg_index,
                "label": f"Leg {leg_index + 1}",
                "condition_id": leg.get("condition_id") or "",
                "yes_token": leg.get("yes_token") or "",
                "no_token": leg.get("no_token") or "",
                "asset_class": leg.get("asset_class") or "polymarket_binary",
                "venue": leg.get("venue") or "",
                "symbol": leg.get("symbol") or "",
                "instrument_id": leg.get("instrument_id") or "",
                "instrument_json": leg.get("instrument_json") or {},
                "budget_cap": leg.get("budget_cap"),
                "params_json": leg.get("params_json"),
                "direction": leg.get("direction") or "Observe",
                "weight": leg.get("weight"),
            }
        )
        # Per-leg price/position snapshot from the same live detail path used by the workspace summary.
        legs_data.append(
            {
                "leg_index": leg_index,
                "direction": leg.get("direction") or "Observe",
                "asset_class": leg.get("asset_class") or "polymarket_binary",
                "venue": leg.get("venue") or "",
                "symbol": leg.get("symbol") or "",
                "instrument_id": leg.get("instrument_id") or "",
                "yes_bid": detail.get("yes_bid") if leg_index == 0 else None,
                "yes_ask": live_snap.get("yes_mark") if live_snap else (detail.get("yes_ask") if leg_index == 0 else None),
                "no_bid": detail.get("no_bid") if leg_index == 0 else None,
                "no_ask": live_snap.get("no_mark") if live_snap else (detail.get("no_ask") if leg_index == 0 else None),
                "yes_qty": fallback_yes_qty,
                "no_qty": fallback_no_qty,
                "yes_avg": fallback_yes_avg,
                "no_avg": fallback_no_avg,
                "unrealized_pnl": fallback_pnl,
                "budget_cap": leg.get("budget_cap"),
            }
        )

    total_ms = (time.perf_counter() - t0) * 1000
    print(f"[SV][workspace] total {total_ms:.1f}ms row_id={row_id}")
    return {
        "strategy": detail,
        "settings_schema": build_strategy_settings_schema(detail),
        "chart_defaults": get_strategy_chart_defaults(detail),
        "chart_capabilities": get_strategy_chart_capabilities(detail),
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
        "backtest": get_backtest_placeholder(row_id),
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


def get_strategy_usedata_snapshot(row_id: int) -> Dict[str, Any]:
    strategy = strategy_data_source.get_strategy(row_id)
    if not strategy:
        raise ValueError(f"strategy {row_id} not found")
    settings = load_web_settings()
    realtime_db_path = get_market_realtime_db_path(settings)
    use_data = build_use_data(strategy, realtime_db_path, collector.get_state())
    return {
        "strategy_id": row_id,
        "generated_at_utc": use_data.get("NowTime"),
        "data": use_data,
    }


def get_strategy_usedata_draft(payload: Dict[str, Any]) -> Dict[str, Any]:
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
    market = {}
    if condition_id:
        try:
            resolved = resolve_market_selection(condition_id=condition_id, limit=1)
            market = resolved.get("selected") or {}
        except Exception:
            market = {}
    yes_token = payload.get("yes_token") or market.get("yes_token") or ""
    no_token = payload.get("no_token") or market.get("no_token") or ""
    end_date = market.get("end_date") or (market.get("raw") or {}).get("endDate") or ""
    strategy = {
        "strategy_id": 0,
        "strategy_name": str(payload.get("strategy_name") or "Draft Strategy").strip(),
        "strategy_code": str(payload.get("strategy_code") or "").strip(),
        "state": str(payload.get("state") or "Virtual").strip(),
        "strategy_bankroll": payload.get("strategy_bankroll") or payload.get("budget_cap") or 0,
        "input_json": json.dumps(input_params or {}, ensure_ascii=False),
        "end_date": end_date,
        "legs": [
            {
                "leg_index": 0,
                "condition_id": condition_id,
                "yes_token": yes_token,
                "no_token": no_token,
                "asset_class": payload.get("asset_class") or "polymarket_binary",
                "venue": payload.get("venue") or "polymarket",
                "symbol": payload.get("symbol") or "",
                "instrument_id": payload.get("instrument_id") or "",
                "instrument_json": payload.get("instrument_json") or {},
                "end_date": end_date,
                "budget_cap": payload.get("budget_cap") or payload.get("strategy_bankroll") or 0,
                "params_json": "{}",
            }
        ],
    }
    settings = load_web_settings()
    realtime_db_path = get_market_realtime_db_path(settings)
    use_data = build_use_data(strategy, realtime_db_path, collector.get_state())
    return {
        "strategy_id": None,
        "generated_at_utc": use_data.get("NowTime"),
        "data": use_data,
    }
