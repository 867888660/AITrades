from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def get_backtest_placeholder(row_id: int) -> Dict[str, Any]:
    return {
        "strategy_row_id": row_id,
        "enabled": False,
        "status": "planned",
        "title": "回测系统",
        "summary": "已预留回测入口与接口契约，执行引擎后续接入。",
        "defaults": {
            "start_cash": 1000,
            "slippage_bps": 10,
            "fee_bps": 0,
            "benchmark": "NONE",
        },
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


def create_backtest_placeholder(row_id: int, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    request_payload = payload if isinstance(payload, dict) else {}
    return {
        "ok": False,
        "implemented": False,
        "strategy_row_id": row_id,
        "status": "planned",
        "message": "Backtest execution is not implemented yet. The UI and API contract are reserved for a later upgrade.",
        "request": request_payload,
        "placeholder": get_backtest_placeholder(row_id),
    }
