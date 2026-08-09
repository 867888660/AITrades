from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from services.data_platform import get_default_store
from services.data_platform.backtest_contract import BacktestExecutionSpec
from services.data_platform.definition_registry import DefinitionRegistry
from services.data_platform.factor_alpha import AlphaComponent, AlphaEngine, AlphaSpec
from services.data_platform.factor_definition_executor import FactorDefinitionExecutor
from services.data_platform.models import UniverseSnapshot
from services.data_platform.portfolio import PortfolioEngine, PortfolioSpec
from services.data_platform.research_backtest import ResearchBacktestProvider, ResearchBacktestResult
from services.data_platform.universe_service import UniverseService


LIBRARY_ALPHA_ADAPTER_VERSION = "library-alpha-history-adapter.v1"


def _snapshot_fingerprint(instruments: Sequence[str], runtime_hash: str) -> str:
    return hashlib.sha256(("|".join(sorted(instruments)) + "|" + runtime_hash).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LibraryAlphaBacktestOutput:
    result: ResearchBacktestResult
    alpha_signals: tuple[dict[str, Any], ...]
    portfolio_targets: tuple[dict[str, Any], ...]
    lineage: dict[str, Any]


class LibraryAlphaHistoryBacktestAdapter:
    """Execute a pinned Library Alpha against aligned History-case bars."""

    def __init__(self, store: Any = None):
        self.store = store or get_default_store()

    def execute(
        self,
        runtime_spec: Mapping[str, Any],
        *,
        bars_by_instrument: Mapping[str, Sequence[Mapping[str, Any]]],
        frequency: str,
        case_id: int | None = None,
        initial_cash: float = 10_000.0,
    ) -> LibraryAlphaBacktestOutput:
        runtime = dict(runtime_spec or {})
        if str(runtime.get("signal_source_type") or "").upper() != "LIBRARY_ALPHA":
            raise ValueError("Library Alpha adapter requires a LIBRARY_ALPHA Strategy runtime")
        source = dict(runtime.get("signal_source") or {})
        instruments = tuple(sorted(str(item) for item in bars_by_instrument))
        if not instruments:
            raise ValueError("Library Alpha backtest requires at least one instrument")

        registry = DefinitionRegistry(self.store)
        factor_executor = FactorDefinitionExecutor()
        factor_outputs: dict[str, dict[str, list[dict[str, Any]]]] = {}
        allowed = set(instruments)
        fields = {str(key).lower() for rows in bars_by_instrument.values() for row in rows[:1] for key in row}
        manifest_inputs = [{
            "manifest_id": f"history_case:{case_id or 'transient'}:{frequency}",
            "frequency": str(frequency or "").lower(),
            "fields": fields,
            "rows": {key: [dict(row) for row in rows] for key, rows in bars_by_instrument.items()},
        }]
        for ref in source.get("factor_closure") or []:
            definition = registry.get(str(ref.get("factor_definition_id") or ""), version=str(ref.get("factor_version") or ""))
            if definition is None or definition.spec_hash != str(ref.get("factor_spec_hash") or ""):
                raise ValueError("Pinned Factor definition identity is unavailable")
            spec, values = factor_executor.execute(
                definition,
                manifest_inputs=manifest_inputs,
                bars_by_instrument=bars_by_instrument,
                allowed_instruments=allowed,
            )
            factor_outputs[spec.name] = values

        alpha_definition = registry.get(
            str(source.get("alpha_definition_id") or ""),
            version=str(source.get("alpha_version") or ""),
        )
        if alpha_definition is None or alpha_definition.spec_hash != str(source.get("alpha_spec_hash") or ""):
            raise ValueError("Pinned Alpha definition identity is unavailable")

        pinned_snapshot_id = str(alpha_definition.spec.get("universe_snapshot_id") or "")
        stored_snapshot = UniverseService(self.store).get_snapshot(pinned_snapshot_id) if pinned_snapshot_id else None
        if stored_snapshot is not None and set(stored_snapshot.actual_instrument_ids) != allowed:
            raise ValueError("Backtest case instruments do not match the Alpha's pinned Universe Snapshot")
        snapshot_id = pinned_snapshot_id or f"history_case_{case_id or 'transient'}_{runtime.get('runtime_hash', '')[:16]}"
        snapshot = stored_snapshot or UniverseSnapshot(
            universe_snapshot_id=snapshot_id,
            universe_definition_id="history_case_runtime",
            as_of_time="",
            actual_instrument_ids=instruments,
            selection_inputs={"source": "history_case", "case_id": case_id},
            selection_rule_version=LIBRARY_ALPHA_ADAPTER_VERSION,
            dataset_manifest_ids=(),
            fingerprint=_snapshot_fingerprint(instruments, str(runtime.get("runtime_hash") or "")),
        )

        components = tuple(
            AlphaComponent(
                factor_name=str(item["factor_name"]),
                weight=float(item["weight"]),
                transform=str(item.get("transform") or "CS_RANK"),
                ascending=bool(item.get("ascending", True)),
            )
            for item in alpha_definition.spec.get("components") or []
        )
        alpha_spec = AlphaSpec(
            name=alpha_definition.name,
            version=alpha_definition.version,
            components=components,
            minimum_coverage=float(alpha_definition.spec.get("minimum_coverage", 1.0)),
            universe_snapshot_id=snapshot.universe_snapshot_id,
            minimum_cross_section_size=int(alpha_definition.spec.get("minimum_cross_section_size", 1)),
            missing_policy=str(alpha_definition.spec.get("missing_policy") or "EXCLUDE"),
            rank_method=str(alpha_definition.spec.get("rank_method") or "AVERAGE"),
            output_scale=str(alpha_definition.spec.get("output_scale") or "PERCENTILE"),
        )
        alpha_signals = AlphaEngine().build_signals(alpha_spec, factor_outputs, universe_snapshot=snapshot)
        portfolio_payload = dict(runtime.get("portfolio_spec") or {})
        portfolio_payload["universe_snapshot_id"] = snapshot.universe_snapshot_id
        portfolio_spec = PortfolioSpec(**portfolio_payload)
        targets = PortfolioEngine().build_targets(alpha_signals, portfolio_spec)
        if not targets:
            raise ValueError("Library Alpha produced no executable portfolio targets for this case window")

        execution_spec = BacktestExecutionSpec.from_payload(dict(runtime.get("execution_spec") or {}))
        result = ResearchBacktestProvider().simulate(
            bars_by_instrument=bars_by_instrument,
            alpha_signals=targets,
            initial_cash=float(initial_cash),
            execution_spec=execution_spec,
            portfolio_spec=portfolio_spec,
            universe_snapshot_ids=[snapshot.universe_snapshot_id],
        )
        lineage = {
            "adapter_version": LIBRARY_ALPHA_ADAPTER_VERSION,
            "strategy_runtime_hash": str(runtime.get("runtime_hash") or ""),
            "library_asset_id": str(source.get("library_asset_id") or ""),
            "library_asset_version": source.get("library_asset_version"),
            "alpha_definition_id": alpha_definition.definition_id,
            "alpha_version": alpha_definition.version,
            "alpha_spec_hash": alpha_definition.spec_hash,
            "factor_closure": [dict(item) for item in source.get("factor_closure") or []],
            "universe_snapshot_id": snapshot.universe_snapshot_id,
            "history_case_id": case_id,
            "data_identity_mode": "HISTORY_CASE_SNAPSHOT",
            "dataset_manifest_ids": [],
        }
        return LibraryAlphaBacktestOutput(
            result=result,
            alpha_signals=tuple(dict(item) for item in alpha_signals),
            portfolio_targets=tuple(dict(item) for item in targets),
            lineage=lineage,
        )
