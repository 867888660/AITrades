from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from services.history_data_service import (
    create_backtest_case as create_history_backtest_case,
    create_backtest_run as create_history_backtest_run,
    get_backtest_workspace_strategy as get_history_backtest_workspace_strategy,
    get_backtest_run as get_history_backtest_run,
    list_backtest_cases as list_history_backtest_cases,
    list_backtest_runs as list_history_backtest_runs,
)
from services.strategy_data_source import normalize_leg_instrument
from services.strategy_registry_service import get_strategy


DEFAULT_BACKTEST_CASH = 10_000
DEFAULT_BACKTEST_FEE_BPS = 2
GENERIC_BACKTEST_COLLECTIONS = {"", "strategy picks"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or str(value).strip() == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _strategy_code_name(value: Any) -> str:
    text = str(value or "").strip()
    if text.lower().endswith(".py"):
        text = text[:-3]
    return text


def _parse_input_json(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            import json

            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _is_binance_leg(leg: Dict[str, Any]) -> bool:
    venue = str(leg.get("venue") or "").strip().lower()
    source = str(leg.get("source") or "").strip().lower()
    asset_class = str(leg.get("asset_class") or "").strip().lower()
    instrument_id = str(leg.get("instrument_id") or "").strip().lower()
    return (
        venue == "binance"
        or source == "binance"
        or instrument_id.startswith("crypto:binance:")
        or (asset_class.startswith("crypto") and venue in {"", "binance"})
    )


def _history_leg_from_strategy_leg(leg: Dict[str, Any], index: int) -> Dict[str, Any]:
    item = normalize_leg_instrument(leg)
    asset_class = str(item.get("asset_class") or "").strip() or "polymarket_binary"
    symbol = str(item.get("symbol") or "").strip().upper()
    interval = str(
        item.get("interval")
        or (item.get("instrument_json") or {}).get("interval")
        or (item.get("params_json") or {}).get("interval")
        or "1m"
    ).strip() or "1m"

    if _is_binance_leg(item):
        source = "binance"
        venue = "binance"
    else:
        source = "polymarket"
        venue = "polymarket"

    return {
        "id": item.get("leg_uid") or f"strategy:{item.get('strategy_id') or ''}:leg:{index}",
        "source": source,
        "venue": venue,
        "asset_class": asset_class,
        "leg_kind": item.get("leg_kind") or "",
        "leg_index": int(item.get("leg_index") or index),
        "instrument_id": item.get("instrument_id") or "",
        "display_name": symbol or item.get("condition_id") or item.get("instrument_id") or f"Leg {index + 1}",
        "symbol": symbol,
        "interval": interval,
        "condition_id": item.get("condition_id") or "",
        "token_id": item.get("yes_token") or "",
        "yes_token": item.get("yes_token") or "",
        "no_token": item.get("no_token") or "",
        "side": item.get("direction") or "",
        "role": item.get("role") or item.get("purpose") or "",
        "budget_cap": _safe_float(item.get("budget_cap"), 0.0),
        "meta": {
            "source_strategy_leg": item,
        },
    }


def _history_legs_from_strategy(strategy: Dict[str, Any]) -> List[Dict[str, Any]]:
    legs = strategy.get("legs") if isinstance(strategy.get("legs"), list) else []
    return [
        _history_leg_from_strategy_leg(leg, index)
        for index, leg in enumerate(legs)
        if isinstance(leg, dict)
    ]


def _is_executable_legs(legs: List[Dict[str, Any]]) -> bool:
    if not legs:
        return False
    sources = {str(leg.get("source") or "").lower() for leg in legs}
    return sources in ({"binance"}, {"polymarket"})


def _title_token(value: Any) -> str:
    text = str(value or "").strip().replace("_", " ").replace("-", " ")
    return " ".join(part.capitalize() for part in text.split()) or "Unknown"


def _default_collection_name_for_legs(legs: List[Dict[str, Any]]) -> str:
    if not legs:
        return "Strategy Picks / Unknown / 0 legs"
    sources = {str(leg.get("source") or "").strip().lower() or "unknown" for leg in legs}
    asset_classes = {str(leg.get("asset_class") or "").strip().lower() or "unknown" for leg in legs}
    if sources == {"binance"} and asset_classes <= {"crypto_spot", "crypto", "unknown"}:
        family = "Binance Crypto"
    elif sources == {"polymarket"} and asset_classes <= {"polymarket_binary", "binary", "unknown"}:
        family = "Polymarket Binary"
    elif len(sources) == 1 and len(asset_classes) == 1:
        family = f"{_title_token(next(iter(sources)))} {_title_token(next(iter(asset_classes)))}"
    else:
        source_label = "+".join(_title_token(item) for item in sorted(sources))
        asset_label = "+".join(_title_token(item) for item in sorted(asset_classes))
        family = f"Mixed {source_label} {asset_label}"
    leg_count = len(legs)
    return f"Strategy Picks / {family} / {leg_count} leg{'s' if leg_count != 1 else ''}"


def _should_retry_with_auto_collection(error: Exception, collection_name: str, auto_collection_name: str, payload: Dict[str, Any]) -> bool:
    if bool(payload.get("strict_collection")):
        return False
    if collection_name == auto_collection_name:
        return False
    if "collection schema" not in str(error).lower():
        return False
    normalized = collection_name.strip().lower()
    return normalized in GENERIC_BACKTEST_COLLECTIONS or normalized.startswith("strategy picks /")


def _strategy_cases(row_id: int) -> List[Dict[str, Any]]:
    cases = []
    for item in list_history_backtest_cases():
        if int(item.get("strategy_id") or 0) == int(row_id):
            cases.append(item)
            continue
        snapshot = (item.get("execution_config") or {}).get("strategy_snapshot") or {}
        if int(snapshot.get("strategy_id") or 0) == int(row_id):
            cases.append(item)
    return cases


def _strategy_runs(row_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    runs = []
    for item in list_history_backtest_runs():
        snapshot = item.get("case_snapshot") if isinstance(item.get("case_snapshot"), dict) else {}
        run_strategy_id = snapshot.get("run_strategy_id")
        if int(item.get("strategy_id") or 0) == int(row_id) or int(run_strategy_id or 0) == int(row_id):
            runs.append(item)
    return runs[: max(1, int(limit or 20))]


def _status_tone(status: str) -> str:
    text = str(status or "").lower()
    if text == "completed":
        return "good"
    if text == "failed":
        return "error"
    return "pending"


def _latest_run_summary(run: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not run:
        return None
    metrics = run.get("metrics") if isinstance(run.get("metrics"), dict) else {}
    snapshot = run.get("case_snapshot") if isinstance(run.get("case_snapshot"), dict) else {}
    runtime = snapshot.get("run_strategy_runtime") if isinstance(snapshot.get("run_strategy_runtime"), dict) else {}
    window = snapshot.get("data_window") if isinstance(snapshot.get("data_window"), dict) else {}
    return {
        "run_id": run.get("run_id"),
        "case_id": run.get("case_id"),
        "status": run.get("status"),
        "tone": _status_tone(str(run.get("status") or "")),
        "created_at_utc": run.get("created_at_utc"),
        "updated_at_utc": run.get("updated_at_utc"),
        "strategy_code": metrics.get("strategy_code") or snapshot.get("run_strategy_code"),
        "signal_source_type": metrics.get("signal_source_type") or runtime.get("signal_source_type"),
        "engine": metrics.get("engine") or metrics.get("backtest_engine") or runtime.get("engine"),
        "strategy_runtime_hash": metrics.get("strategy_runtime_hash") or runtime.get("runtime_hash"),
        "period_start": metrics.get("period_start") or metrics.get("requested_start") or window.get("start"),
        "period_end": metrics.get("period_end") or metrics.get("requested_end") or window.get("end"),
        "total_return": metrics.get("total_return"),
        "max_drawdown": metrics.get("max_drawdown"),
        "sharpe": metrics.get("sharpe"),
        "progress_percent": metrics.get("progress_percent"),
        "progress_stage": metrics.get("progress_stage"),
        "report_url": f"/backtests/{run.get('run_id')}",
        "workspace_url": f"/strategies/{snapshot.get('run_strategy_id') or run.get('strategy_id')}/workspace?source=backtest&run_id={run.get('run_id')}",
    }


def _build_backtest_overview_payload(
    row_id: int,
    strategy: Dict[str, Any],
    legs: List[Dict[str, Any]],
    runs: List[Dict[str, Any]],
    *,
    cases: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    executable_now = _is_executable_legs(legs)
    return {
        "strategy_row_id": row_id,
        "enabled": True,
        "status": "ready" if executable_now else "metadata_ready",
        "title": "Backtest",
        "summary": (
            "This strategy can be backtested directly when all legs come from the same supported venue."
            if executable_now
            else "This strategy can save backtest cases, but mixed-source replay is still limited."
        ),
        "defaults": {
            "start_cash": DEFAULT_BACKTEST_CASH,
            "slippage_bps": 10,
            "fee_bps": DEFAULT_BACKTEST_FEE_BPS,
            "benchmark": "NONE",
        },
        "engine_capabilities": {
            "binance_single_leg": True,
            "binance_multi_leg": True,
            "polymarket_binary_replay": True,
            "polymarket_metadata_cases": True,
            "polymarket_tick_replay": False,
            "mixed_source_replay": False,
            "multi_leg_alignment": True,
            "library_alpha_history": True,
            "library_alpha_execution": "FACTOR_ALPHA_PORTFOLIO_NEXT_BAR_OPEN",
            "library_alpha_data_identity": "HISTORY_CASE_SNAPSHOT",
        },
        "strategy": {
            "strategy_id": strategy.get("strategy_id"),
            "strategy_name": strategy.get("strategy_name"),
            "strategy_code": _strategy_code_name(strategy.get("strategy_code")),
            "signal_source": strategy.get("signal_source") or {
                "type": "LEGACY_STRATEGY_CODE",
                "strategy_code": _strategy_code_name(strategy.get("strategy_code")),
            },
            "mode": strategy.get("mode") or strategy.get("state"),
        },
        "legs": legs,
        "cases": list(cases or [])[:10],
        "recent_runs": [_latest_run_summary(run) for run in runs],
        "latest_run": _latest_run_summary(runs[0] if runs else None),
        "supported_inputs": [
            "time_window",
            "market_context",
            "workspace_preset",
            "indicator_config",
            "execution_costs",
        ],
        "next_endpoints": {
            "create": f"/api/polymarket/strategies/{row_id}/backtest",
            "status": f"/api/polymarket/strategies/{row_id}/backtest",
            "results": f"/api/polymarket/strategies/{row_id}/backtest/results",
        },
        "updated_at": _now_iso(),
    }


def get_strategy_backtest(row_id: int) -> Dict[str, Any]:
    strategy = get_strategy(int(row_id)) or get_history_backtest_workspace_strategy(int(row_id))
    if not strategy:
        raise ValueError(f"strategy {row_id} not found")

    legs = _history_legs_from_strategy(strategy)
    runs = _strategy_runs(row_id, limit=10)
    cases = _strategy_cases(row_id)
    return _build_backtest_overview_payload(row_id, strategy, legs, runs, cases=cases)
    executable_now = _is_executable_legs(legs)
    return {
        "strategy_row_id": row_id,
        "enabled": True,
        "status": "ready" if executable_now else "metadata_ready",
        "title": "回测系统",
        "summary": (
            "当前策略可以创建并运行回测：支持全 Binance legs 和全 Polymarket binary legs。"
            if executable_now
            else "当前策略可以保存回测样例；真实执行器暂不支持混合 Binance/Polymarket replay。"
        ),
        "defaults": {
            "start_cash": DEFAULT_BACKTEST_CASH,
            "slippage_bps": 10,
            "fee_bps": DEFAULT_BACKTEST_FEE_BPS,
            "benchmark": "NONE",
        },
        "engine_capabilities": {
            "binance_single_leg": True,
            "binance_multi_leg": True,
            "polymarket_binary_replay": True,
            "polymarket_metadata_cases": True,
            "polymarket_tick_replay": False,
            "mixed_source_replay": False,
            "multi_leg_alignment": True,
        },
        "strategy": {
            "strategy_id": strategy.get("strategy_id"),
            "strategy_name": strategy.get("strategy_name"),
            "strategy_code": _strategy_code_name(strategy.get("strategy_code")),
            "mode": strategy.get("mode") or strategy.get("state"),
        },
        "legs": legs,
        "cases": cases[:10],
        "recent_runs": [_latest_run_summary(run) for run in runs],
        "latest_run": _latest_run_summary(runs[0] if runs else None),
        "supported_inputs": [
            "time_window",
            "market_context",
            "workspace_preset",
            "indicator_config",
            "execution_costs",
        ],
        "next_endpoints": {
            "create": f"/api/polymarket/strategies/{row_id}/backtest",
            "status": f"/api/polymarket/strategies/{row_id}/backtest",
            "results": f"/api/polymarket/strategies/{row_id}/backtest/results",
        },
        "updated_at": _now_iso(),
    }


def create_strategy_backtest(row_id: int, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    strategy = get_strategy(int(row_id)) or get_history_backtest_workspace_strategy(int(row_id))
    if not strategy:
        raise ValueError(f"strategy {row_id} not found")

    case_id = payload.get("case_id")
    strategy_code = _strategy_code_name(payload.get("strategy_code") or strategy.get("strategy_code"))
    params = payload.get("params") if isinstance(payload.get("params"), dict) else _parse_input_json(strategy.get("input_json"))
    if "initial_cash" not in params:
        params["initial_cash"] = _safe_float(
            payload.get("initial_cash") or strategy.get("strategy_bankroll") or strategy.get("initial_capital"),
            DEFAULT_BACKTEST_CASH,
        )
    if "fee_bps" not in params:
        params["fee_bps"] = _safe_float(payload.get("fee_bps"), DEFAULT_BACKTEST_FEE_BPS)

    if case_id:
        case = None
        executable_now = True
    else:
        legs = payload.get("legs") if isinstance(payload.get("legs"), list) else _history_legs_from_strategy(strategy)
        if not legs:
            raise ValueError("strategy has no legs to backtest")
        executable_now = _is_executable_legs(legs)
        data_window = payload.get("data_window") if isinstance(payload.get("data_window"), dict) else {}
        if data_window.get("from") and not data_window.get("start"):
            data_window["start"] = data_window.get("from")
        if data_window.get("to") and not data_window.get("end"):
            data_window["end"] = data_window.get("to")
        if data_window.get("start") or data_window.get("end"):
            data_window["strict"] = bool(payload.get("strict_window", True))
        auto_collection_name = _default_collection_name_for_legs(legs)
        collection_name = str(payload.get("collection_name") or auto_collection_name).strip()
        case_name = str(payload.get("case_name") or "").strip()
        if not case_name:
            case_name = f"{strategy.get('strategy_name') or ('Strategy ' + str(row_id))} backtest {datetime.now(timezone.utc).strftime('%Y%m%d %H%M%S')}"
        case_payload = {
            "case_name": case_name,
            "collection_name": collection_name,
            "strategy_id": row_id,
            "legs": legs,
            "params": params,
            "data_window": data_window,
            "execution_config": {
                "origin": "strategy_workspace",
                "strategy_code": strategy_code,
                "execution_spec": payload.get("execution_spec") if isinstance(payload.get("execution_spec"), dict) else {},
                "portfolio_spec": payload.get("portfolio_spec") if isinstance(payload.get("portfolio_spec"), dict) else {},
            },
        }
        try:
            case = create_history_backtest_case(case_payload)
        except ValueError as exc:
            if not _should_retry_with_auto_collection(exc, collection_name, auto_collection_name, payload):
                raise
            case_payload["collection_name"] = auto_collection_name
            case = create_history_backtest_case(case_payload)
        case_id = case.get("case_id")

    if payload.get("case_only") or payload.get("save_case_only"):
        return {
            "ok": True,
            "strategy_row_id": row_id,
            "case": case,
            "run": None,
            "message": "Backtest case saved. Run it from History when ready.",
            "report_url": None,
            "history_url": "/history",
            "workspace_url": f"/strategies/{row_id}/workspace",
        }

    if payload.get("metadata_only") and not executable_now:
        return {
            "ok": True,
            "strategy_row_id": row_id,
            "case": case,
            "run": None,
            "message": "Backtest case saved. The executable engine currently supports all-Binance and all-Polymarket cases; mixed-source replay is planned.",
            "report_url": None,
            "workspace_url": f"/strategies/{row_id}/workspace",
        }

    run = create_history_backtest_run(
        int(case_id),
        {
            "strategy_id": row_id,
            "strategy_code": strategy_code,
            "params": params,
            "execution_spec": payload.get("execution_spec") if isinstance(payload.get("execution_spec"), dict) else {},
            "portfolio_spec": payload.get("portfolio_spec") if isinstance(payload.get("portfolio_spec"), dict) else {},
            "auto_download": bool(payload.get("auto_download") or payload.get("auto_download_missing")),
            "run_mode": payload.get("run_mode") or "async",
        },
    )
    return {
        "ok": True,
        "strategy_row_id": row_id,
        "case": case if not payload.get("case_id") else None,
        "run": run,
        "report_url": f"/backtests/{run.get('run_id')}",
        "workspace_url": f"/strategies/{row_id}/workspace?source=backtest&run_id={run.get('run_id')}",
    }


def get_strategy_backtest_results(row_id: int, run_id: int | None = None) -> Dict[str, Any]:
    runs = _strategy_runs(row_id, limit=50)
    selected = None
    if run_id:
        candidate = get_history_backtest_run(int(run_id))
        snapshot = candidate.get("case_snapshot") if isinstance(candidate.get("case_snapshot"), dict) else {}
        if candidate and (
            int(candidate.get("strategy_id") or 0) == int(row_id)
            or int(snapshot.get("run_strategy_id") or 0) == int(row_id)
        ):
            selected = candidate
        else:
            raise ValueError("backtest run not found for this strategy")
    elif runs:
        selected = get_history_backtest_run(int(runs[0]["run_id"]))
    return {
        "strategy_row_id": row_id,
        "runs": [_latest_run_summary(run) for run in runs],
        "selected_run": selected,
        "selected_summary": _latest_run_summary(selected),
        "updated_at": _now_iso(),
    }


def get_backtest_placeholder(row_id: int) -> Dict[str, Any]:
    strategy = get_strategy(int(row_id)) or get_history_backtest_workspace_strategy(int(row_id))
    if not strategy:
        raise ValueError(f"strategy {row_id} not found")
    legs = _history_legs_from_strategy(strategy)
    runs = _strategy_runs(row_id, limit=10)
    return _build_backtest_overview_payload(row_id, strategy, legs, runs, cases=[])


def create_backtest_placeholder(row_id: int, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return create_strategy_backtest(row_id, payload)
