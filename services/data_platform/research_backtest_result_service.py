from __future__ import annotations

from dataclasses import asdict
from typing import Any, Mapping

from .alpha_run_result_service import (
    _dict,
    _list,
    _produced_artifact_ids,
    alpha_run_section,
    build_alpha_run_contract,
)
from .artifact_service import ArtifactService
from .definition_registry import DefinitionRegistry
from .research_run_service import ResearchRunService
from .universe_service import UniverseService


RESEARCH_BACKTEST_RESULT_SCHEMA_VERSION = "research-backtest-result.v1"
RESEARCH_BACKTEST_STRUCTURED_SECTIONS = {
    "signals",
    "portfolio_targets",
    "positions",
    "trades",
    "equity_curve",
    "performance_metrics",
    "drawdown",
    "diagnostics",
}


def build_research_backtest_contract(
    *,
    run: Mapping[str, Any],
    alpha_definitions: list[Mapping[str, Any]],
    factor_definitions: list[Mapping[str, Any]],
    universe: Mapping[str, Any],
    data_inputs: list[Mapping[str, Any]],
    execution_specs: Mapping[str, Any],
    artifacts: list[Any],
    artifact_dependencies: Mapping[str, Any],
) -> dict[str, Any]:
    if str(run.get("run_type") or "") != "RESEARCH_BACKTEST":
        raise ValueError("Research Backtest result contract requires RESEARCH_BACKTEST")

    # Reuse the immutable Alpha lineage/artifact association logic, then replace
    # the product boundary. The synthetic run type is confined to this read
    # projection and never mutates the persisted Run.
    contract = build_alpha_run_contract(
        run={**dict(run), "run_type": "ALPHA_EVALUATION"},
        alpha_definitions=alpha_definitions,
        factor_definitions=factor_definitions,
        universe=universe,
        data_inputs=data_inputs,
        execution_specs=execution_specs,
        artifacts=artifacts,
        artifact_dependencies=artifact_dependencies,
    )
    benchmark_spec = _dict(execution_specs.get("benchmark_spec"))
    benchmark_rows = []
    for result in _list(contract.get("results")):
        performance = _dict(result.get("performance"))
        if performance.get("benchmark_manifest_id") and performance.get("benchmark_total_return") is not None:
            benchmark_rows.append({
                "alpha_definition_id": result.get("alpha_definition_id"),
                "benchmark_manifest_id": performance.get("benchmark_manifest_id"),
                "benchmark_total_return": performance.get("benchmark_total_return"),
                "excess_total_return": performance.get("excess_total_return"),
            })
    benchmark_materialized = bool(benchmark_rows)
    contract.update({
        "schema_version": RESEARCH_BACKTEST_RESULT_SCHEMA_VERSION,
        "product_run_type": "RESEARCH_BACKTEST",
        "legacy_hybrid": False,
        "migration_notice": "",
        "boundary": {
            "starts_at": "FROZEN_RESEARCH_INPUTS",
            "ends_at": "COST_ADJUSTED_PORTFOLIO_AND_TRADING_PERFORMANCE",
            "includes": [
                "ALPHA_LINEAGE", "PORTFOLIO_CONSTRUCTION", "EXECUTION_ASSUMPTIONS",
                "TARGETS", "POSITIONS", "TRADES", "COSTS", "EQUITY_CURVE",
                "PERFORMANCE_METRICS", "DRAWDOWN", "DIAGNOSTICS", "LOGS",
            ],
            "excludes": ["STRATEGY_DEPLOYMENT", "PAPER_TRADING", "LIVE_TRADING"],
        },
        "benchmark_spec": benchmark_spec,
        "benchmark_status": {
            "configured": bool(benchmark_spec),
            "materialized": benchmark_materialized,
            "comparisons": benchmark_rows,
            "warning": (
                "" if benchmark_materialized else
                "Benchmark comparison is not materialized for this Run; excess return and "
                "information ratio must not be inferred."
            ),
        },
    })
    return contract


class ResearchBacktestResultService:
    """Build a strict read model over immutable Research Backtest artifacts."""

    def __init__(self, store: Any):
        self.store = store

    def build(self, run_or_id: Mapping[str, Any] | str) -> dict[str, Any]:
        run_service = ResearchRunService(self.store)
        run = dict(run_or_id) if isinstance(run_or_id, Mapping) else run_service.get(str(run_or_id))
        if not run:
            raise ValueError("Research Run not found")
        if str(run.get("run_type") or "") != "RESEARCH_BACKTEST":
            raise ValueError("Research Run is not a Research Backtest")

        bundle = run_service.get_bundle(str(run.get("bundle_id") or ""))
        frozen = _dict(_dict(bundle).get("canonical_payload"))
        closure = _dict(frozen.get("input_closure"))
        registry = DefinitionRegistry(self.store)
        factor_definitions = []
        for ref in _list(closure.get("factor_definitions")):
            definition = registry.get(
                str(ref.get("factor_definition_id") or ""), version=str(ref.get("version") or "")
            )
            factor_definitions.append(definition.to_dict() if definition else dict(ref))
        alpha_definitions = []
        for ref in _list(closure.get("alpha_definitions")):
            definition = registry.get(
                str(ref.get("alpha_definition_id") or ""), version=str(ref.get("version") or "")
            )
            alpha_definitions.append(definition.to_dict() if definition else dict(ref))
        snapshot = UniverseService(self.store).get_snapshot(str(closure.get("universe_snapshot_id") or ""))
        universe = asdict(snapshot) if snapshot else {
            "universe_snapshot_id": closure.get("universe_snapshot_id"),
            "actual_instrument_ids": closure.get("resolved_instrument_ids") or [],
        }
        artifact_service = ArtifactService(self.store)
        produced_ids = _produced_artifact_ids(run)
        artifacts = [
            artifact
            for artifact in artifact_service.list(limit=1000)
            if artifact.created_by_run_id == run.get("run_id") or artifact.artifact_id in produced_ids
        ]
        dependencies = artifact_service.dependencies_many([item.artifact_id for item in artifacts])
        return build_research_backtest_contract(
            run=run,
            alpha_definitions=alpha_definitions,
            factor_definitions=factor_definitions,
            universe=universe,
            data_inputs=_list(frozen.get("manifest_descriptors")),
            execution_specs=_dict(frozen.get("execution_specs")),
            artifacts=artifacts,
            artifact_dependencies=dependencies,
        )

    def section(self, run_or_id: Mapping[str, Any] | str, section_key: str) -> dict[str, Any]:
        key = str(section_key or "").strip().lower()
        if key not in RESEARCH_BACKTEST_STRUCTURED_SECTIONS:
            raise ValueError(f"unsupported structured Research Backtest section: {key}")
        data = alpha_run_section(self.build(run_or_id), key)
        data["view_type"] = f"RESEARCH_BACKTEST_{key.upper()}"
        return data
