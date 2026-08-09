from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from services.data_platform.backtest_contract import BacktestExecutionSpec
from services.data_platform.portfolio import PortfolioSpec
from services.strategy_signal_source_service import (
    LEGACY_STRATEGY_CODE,
    LIBRARY_ALPHA,
    effective_strategy_signal_source,
    resolve_library_alpha_source,
)


STRATEGY_RUNTIME_SCHEMA_VERSION = "strategy_runtime_spec.v1"
LEGACY_HISTORY_ENGINE = "legacy_strategy_code_history.v1"
LIBRARY_ALPHA_HISTORY_ENGINE = "library_alpha_history.v1"


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    material = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _factor_identity(items: Any) -> list[tuple[str, str, str, str]]:
    result = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, Mapping):
            continue
        result.append((
            str(item.get("library_asset_id") or ""),
            str(item.get("factor_definition_id") or ""),
            str(item.get("factor_version") or ""),
            str(item.get("factor_spec_hash") or ""),
        ))
    return result


@dataclass(frozen=True)
class StrategyRuntimeSpec:
    payload: dict[str, Any]

    @property
    def runtime_hash(self) -> str:
        return str(self.payload["runtime_hash"])

    @property
    def signal_source_type(self) -> str:
        return str(self.payload["signal_source_type"])

    def to_dict(self) -> dict[str, Any]:
        return dict(self.payload)


class StrategyRuntimeCompiler:
    """Compile one persisted Strategy into a frozen Backtest runtime input."""

    def compile(
        self,
        strategy: Mapping[str, Any],
        *,
        params: Mapping[str, Any] | None = None,
        execution_spec: Mapping[str, Any] | None = None,
        portfolio_spec: Mapping[str, Any] | None = None,
    ) -> StrategyRuntimeSpec:
        strategy = dict(strategy or {})
        source = effective_strategy_signal_source(
            strategy.get("signal_source"),
            strategy_code=strategy.get("strategy_code"),
        )
        source_type = str(source.get("type") or "").upper()
        strategy_inputs = _dict(strategy.get("input_json"))
        run_params = {**_dict(strategy_inputs.get("params")), **_dict(params)}

        if source_type == LEGACY_STRATEGY_CODE:
            strategy_code = str(source.get("strategy_code") or strategy.get("strategy_code") or "").strip()
            if not strategy_code:
                raise ValueError("Legacy Strategy runtime requires strategy_code")
            core = {
                "schema_version": STRATEGY_RUNTIME_SCHEMA_VERSION,
                "strategy_id": strategy.get("strategy_id"),
                "strategy_uid": str(strategy.get("strategy_uid") or ""),
                "strategy_name": str(strategy.get("strategy_name") or ""),
                "signal_source_type": LEGACY_STRATEGY_CODE,
                "signal_source": source,
                "engine": LEGACY_HISTORY_ENGINE,
                "strategy_code": strategy_code,
                "params": run_params,
                "backtest_status": "READY",
                "live_execution_status": "CONNECTED",
            }
        elif source_type == LIBRARY_ALPHA:
            current = resolve_library_alpha_source(source.get("library_asset_id"))
            alpha_identity = (
                str(source.get("library_asset_id") or ""),
                str(source.get("alpha_definition_id") or ""),
                str(source.get("alpha_version") or ""),
                str(source.get("alpha_spec_hash") or ""),
            )
            current_identity = (
                str(current.get("library_asset_id") or ""),
                str(current.get("alpha_definition_id") or ""),
                str(current.get("alpha_version") or ""),
                str(current.get("alpha_spec_hash") or ""),
            )
            if alpha_identity != current_identity or _factor_identity(source.get("factor_closure")) != _factor_identity(current.get("factor_closure")):
                raise ValueError("Strategy Library Alpha pin no longer matches the immutable Library closure")

            stored_execution = _dict(strategy_inputs.get("execution_spec"))
            execution_payload = BacktestExecutionSpec.from_payload({
                **stored_execution,
                **_dict(execution_spec),
            }).to_dict()
            stored_portfolio = _dict(strategy_inputs.get("portfolio_spec"))
            portfolio_payload = PortfolioSpec(**{
                **stored_portfolio,
                **_dict(portfolio_spec),
            }).to_dict()
            core = {
                "schema_version": STRATEGY_RUNTIME_SCHEMA_VERSION,
                "strategy_id": strategy.get("strategy_id"),
                "strategy_uid": str(strategy.get("strategy_uid") or ""),
                "strategy_name": str(strategy.get("strategy_name") or ""),
                "signal_source_type": LIBRARY_ALPHA,
                "signal_source": source,
                "engine": LIBRARY_ALPHA_HISTORY_ENGINE,
                "strategy_code": "",
                "params": run_params,
                "execution_spec": execution_payload,
                "portfolio_spec": portfolio_payload,
                "backtest_status": "READY",
                "live_execution_status": "NOT_CONNECTED",
                "data_identity_mode": "HISTORY_CASE_SNAPSHOT",
            }
        else:
            raise ValueError(f"unsupported Strategy signal source: {source_type}")

        runtime_hash = _canonical_hash(core)
        return StrategyRuntimeSpec(payload={
            **core,
            "runtime_hash": runtime_hash,
            "compiled_at": _now(),
        })
