from __future__ import annotations

from typing import Any, Dict, List

from services.config_loader import BASE_DIR, load_web_settings
from services.realtime_collector import collector
from services.strategy_registry_service import get_strategy, update_strategy_mode
from services.virtual_context_builder import build_use_data
from services.virtual_execution import execute_actions


def _realtime_db_path() -> str:
    settings = load_web_settings()
    raw = str(settings.get("market_realtime_db_path") or "").strip()
    if not raw:
        return str(BASE_DIR / "Data" / "polymarket_realtime.db")
    path = BASE_DIR / raw if not (":" in raw or raw.startswith("\\\\")) else raw
    return str(path)


def _force_flat_actions(use_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    actions: List[Dict[str, Any]] = []
    instruments = use_data.get("Instruments") or []
    if isinstance(instruments, list) and instruments:
        for item in instruments:
            if not isinstance(item, dict):
                continue
            idx = int(item.get("index", item.get("leg_index", 0)) or 0)
            asset_class = str(item.get("asset_class") or "polymarket_binary").strip()
            if asset_class == "polymarket_binary":
                actions.append({"type": "CLOSE_ALL", "leg": idx, "reason": "force_flat"})
            else:
                actions.append({"type": "SET_TARGET", "instrument": idx, "target": 0, "reason": "force_flat"})
        return actions

    leg_count = int(use_data.get("LegCount") or 0)
    for idx in range(max(leg_count, 1)):
        actions.append({"type": "CLOSE_ALL", "leg": idx, "reason": "force_flat"})
    return actions


def force_flat_strategy(strategy_id: int, *, actor: str = "user") -> Dict[str, Any]:
    strategy = get_strategy(strategy_id)
    if not strategy:
        raise ValueError(f"strategy {strategy_id} not found")

    mode = str(strategy.get("mode") or strategy.get("state") or "Stop")
    if mode == "Real":
        raise ValueError("Real force-flat is blocked until strategy_real_positions and order attribution are reconciled")

    use_data = build_use_data(
        strategy,
        _realtime_db_path(),
        collector.get_state(),
        include_live_orderbook=False,
    )
    actions = _force_flat_actions(use_data)
    orders_placed, errors = execute_actions(
        strategy_id,
        float(strategy.get("strategy_bankroll") or 0.0),
        actions,
        use_data,
        None,
        market_category=None,
        audit_tick_id=None,
        function_json_hash=f"manual_force_flat:{actor}",
    )
    if mode != "Stop":
        update_strategy_mode(strategy_id, "Stop")
    return {
        "strategy_id": strategy_id,
        "previous_mode": mode,
        "mode": "Stop",
        "orders_placed": orders_placed,
        "errors": errors,
        "actions": actions,
    }
