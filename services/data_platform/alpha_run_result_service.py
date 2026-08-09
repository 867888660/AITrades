from __future__ import annotations

from dataclasses import asdict
from typing import Any, Mapping, Sequence

from .artifact_service import ArtifactService
from .definition_registry import DefinitionRegistry
from .research_run_service import ResearchRunService
from .universe_service import UniverseService


ALPHA_RUN_RESULT_SCHEMA_VERSION = "alpha-evaluation-result.v2"
LEGACY_ALPHA_RUN_RESULT_SCHEMA_VERSION = "alpha-run-result.v1"
ALPHA_EVALUATION_STRUCTURED_SECTIONS = {
    "signals",
    "ic_accuracy",
    "decay",
    "turnover",
    "regime_analysis",
    "diagnostics",
}
LEGACY_ALPHA_RUN_STRUCTURED_SECTIONS = {
    "portfolio_targets",
    "positions",
    "trades",
    "equity_curve",
    "performance_metrics",
    "drawdown",
}
ALPHA_RUN_STRUCTURED_SECTIONS = (
    ALPHA_EVALUATION_STRUCTURED_SECTIONS | LEGACY_ALPHA_RUN_STRUCTURED_SECTIONS
)


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _produced_artifact_ids(run: Mapping[str, Any]) -> set[str]:
    output = _dict(run.get("output"))
    return {
        str(artifact_id)
        for key, values in output.items()
        if key.startswith("produced_") and key.endswith("_artifact_ids")
        for artifact_id in _list(values)
        if str(artifact_id or "")
    }


def _artifact_item(artifact: Any, dependencies: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    if isinstance(artifact, Mapping):
        item = dict(artifact)
        item.setdefault("dependencies", [dict(value) for value in dependencies])
        return item
    metadata = dict(artifact.metadata)
    row_count = metadata.get("row_count")
    if row_count is None:
        row_count = metadata.get("order_count")
    if row_count is None and artifact.artifact_type == "BACKTEST_RESULT":
        row_count = 1
    return {
        "artifact_id": artifact.artifact_id,
        "artifact_type": artifact.artifact_type,
        "logical_name": artifact.logical_name,
        "version": artifact.version,
        "status": artifact.status,
        "schema_version": artifact.schema_version,
        "engine_version": artifact.engine_version,
        "row_count": int(row_count or 0),
        "metadata": metadata,
        "dependencies": [dict(value) for value in dependencies],
    }


def _artifact_ref(artifact: Mapping[str, Any] | None) -> dict[str, Any]:
    item = dict(artifact or {})
    return {
        "artifact_id": item.get("artifact_id"),
        "artifact_type": item.get("artifact_type"),
        "logical_name": item.get("logical_name"),
        "row_count": item.get("row_count"),
        "schema_version": item.get("schema_version"),
        "status": item.get("status"),
    }


def _depends_on(artifact: Mapping[str, Any], parent_id: str) -> bool:
    return any(
        str(item.get("parent_id") or "") == str(parent_id or "")
        for item in _list(artifact.get("dependencies"))
        if isinstance(item, Mapping)
    )


def _first_child(
    artifacts: Sequence[Mapping[str, Any]],
    artifact_type: str,
    parent_id: str,
    *,
    logical_prefix: str = "",
) -> dict[str, Any]:
    candidates = [dict(item) for item in artifacts if item.get("artifact_type") == artifact_type]
    linked = next((item for item in candidates if _depends_on(item, parent_id)), None)
    if linked:
        return linked
    if logical_prefix:
        named = next(
            (item for item in candidates if str(item.get("logical_name") or "").startswith(logical_prefix)),
            None,
        )
        if named:
            return named
    return candidates[0] if len(candidates) == 1 else {}


def _alpha_identity(definition: Mapping[str, Any], name: str) -> dict[str, Any]:
    definition = dict(definition or {})
    spec = _dict(definition.get("spec"))
    return {
        "definition_id": str(definition.get("definition_id") or ""),
        "name": str(definition.get("name") or spec.get("name") or name),
        "version": str(definition.get("version") or spec.get("version") or ""),
        "spec_hash": str(definition.get("spec_hash") or ""),
        "engine_version": str(definition.get("engine_version") or spec.get("engine_version") or ""),
        "output_scale": str(spec.get("output_scale") or ""),
        "minimum_coverage": spec.get("minimum_coverage"),
        "minimum_cross_section_size": spec.get("minimum_cross_section_size"),
    }


def _factor_inputs(
    definition: Mapping[str, Any],
    factor_definitions: Sequence[Mapping[str, Any]],
    factor_artifacts: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    spec = _dict(definition.get("spec"))
    definitions_by_id = {
        str(item.get("definition_id") or ""): dict(item)
        for item in factor_definitions
    }
    definitions_by_name = {
        str(item.get("name") or _dict(item.get("spec")).get("name") or ""): dict(item)
        for item in factor_definitions
    }
    artifacts_by_name = {
        str(_dict(item.get("metadata")).get("factor_name") or item.get("logical_name") or ""): dict(item)
        for item in factor_artifacts
    }
    result = []
    for component in _list(spec.get("components")):
        if not isinstance(component, Mapping):
            continue
        component = dict(component)
        definition_id = str(component.get("factor_definition_id") or "")
        name = str(component.get("factor_name") or "")
        factor_definition = definitions_by_id.get(definition_id) or definitions_by_name.get(name) or {}
        factor_artifact = artifacts_by_name.get(name) or {}
        result.append({
            "definition_id": definition_id or factor_definition.get("definition_id"),
            "name": name or factor_definition.get("name"),
            "version": component.get("factor_version") or factor_definition.get("version"),
            "spec_hash": component.get("factor_spec_hash") or factor_definition.get("spec_hash"),
            "weight": component.get("weight"),
            "transform": component.get("transform"),
            "ascending": component.get("ascending"),
            "artifact": _artifact_ref(factor_artifact),
        })
    return result


def _alpha_result(
    *,
    definition: Mapping[str, Any],
    factor_definitions: Sequence[Mapping[str, Any]],
    artifacts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    spec = _dict(definition.get("spec"))
    name = str(definition.get("name") or spec.get("name") or "Alpha")
    alpha_artifacts = [item for item in artifacts if item.get("artifact_type") == "ALPHA_VALUES"]
    alpha_artifact = next(
        (
            dict(item) for item in alpha_artifacts
            if str(_dict(item.get("metadata")).get("alpha_name") or item.get("logical_name") or "") == name
        ),
        {},
    )
    if not alpha_artifact and len(alpha_artifacts) == 1:
        alpha_artifact = dict(alpha_artifacts[0])
    alpha_artifact_id = str(alpha_artifact.get("artifact_id") or "")
    evaluation_artifact = _first_child(
        artifacts, "ALPHA_EVALUATION", alpha_artifact_id, logical_prefix=name,
    )
    portfolio_artifact = _first_child(
        artifacts, "PORTFOLIO_TARGETS", alpha_artifact_id,
    )
    portfolio_artifact_id = str(portfolio_artifact.get("artifact_id") or "")
    artifact_by_role = {
        "alpha": alpha_artifact,
        "evaluation": evaluation_artifact,
        "portfolio_targets": portfolio_artifact,
        "positions": _first_child(artifacts, "POSITION_SERIES", portfolio_artifact_id, logical_prefix=name),
        "trades": _first_child(artifacts, "BACKTEST_ORDERS", portfolio_artifact_id, logical_prefix=name),
        "equity_curve": _first_child(artifacts, "EQUITY_SERIES", portfolio_artifact_id, logical_prefix=name),
        "drawdown": _first_child(artifacts, "DRAWDOWN_SERIES", portfolio_artifact_id, logical_prefix=name),
        "performance": _first_child(artifacts, "BACKTEST_RESULT", portfolio_artifact_id, logical_prefix=name),
    }
    evaluation_summary = _dict(_dict(evaluation_artifact.get("metadata")).get("summary"))
    performance = _dict(_dict(artifact_by_role["performance"].get("metadata")).get("metrics"))
    diagnostics = [
        dict(item)
        for item in _list(evaluation_summary.get("diagnostics"))
        if isinstance(item, Mapping)
    ]
    return {
        "alpha": _alpha_identity(definition, name),
        "definition_spec": spec,
        "factor_inputs": _factor_inputs(
            definition,
            factor_definitions,
            [item for item in artifacts if item.get("artifact_type") == "FACTOR_VALUES"],
        ),
        "artifacts": {key: _artifact_ref(value) for key, value in artifact_by_role.items()},
        "signal_summary": {
            "row_count": alpha_artifact.get("row_count"),
            "score_count": evaluation_summary.get("score_count"),
            "score_mean": evaluation_summary.get("score_mean"),
            "score_std": evaluation_summary.get("score_std"),
            "score_quantiles": _dict(evaluation_summary.get("score_quantiles")),
            "average_rank_stability": evaluation_summary.get("average_rank_stability"),
            "average_membership_turnover": evaluation_summary.get("average_membership_turnover"),
        },
        "ic": _dict(evaluation_summary.get("ic")),
        "rank_ic": _dict(evaluation_summary.get("rank_ic")),
        "holding_period_decay": _dict(evaluation_summary.get("holding_period_decay")),
        "regime_performance": _dict(evaluation_summary.get("regime_performance")),
        "performance": performance,
        "costs": {
            "fees": performance.get("fees"),
            "slippage_cost": performance.get("slippage_cost"),
            "turnover": performance.get("turnover"),
        },
        "diagnostics": diagnostics,
    }


def build_alpha_run_contract(
    *,
    run: Mapping[str, Any],
    alpha_definitions: Sequence[Mapping[str, Any]],
    factor_definitions: Sequence[Mapping[str, Any]],
    universe: Mapping[str, Any],
    data_inputs: Sequence[Mapping[str, Any]],
    execution_specs: Mapping[str, Any],
    artifacts: Sequence[Any],
    artifact_dependencies: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> dict[str, Any]:
    run = dict(run or {})
    if str(run.get("run_type") or "") != "ALPHA_EVALUATION":
        raise ValueError("Alpha Run result contract requires ALPHA_EVALUATION")
    dependency_map = dict(artifact_dependencies or {})
    artifact_items = [
        _artifact_item(item, dependency_map.get(str(getattr(item, "artifact_id", "") or _dict(item).get("artifact_id") or ""), ()))
        for item in artifacts
    ]
    definitions = [dict(item) for item in alpha_definitions]
    if not definitions:
        definitions = [
            {
                "name": _dict(item.get("metadata")).get("alpha_name") or item.get("logical_name"),
                "version": _dict(item.get("metadata")).get("alpha_version"),
                "spec": _dict(_dict(item.get("metadata")).get("alpha_spec")),
            }
            for item in artifact_items
            if item.get("artifact_type") == "ALPHA_VALUES"
        ]
    results = [
        _alpha_result(
            definition=definition,
            factor_definitions=factor_definitions,
            artifacts=artifact_items,
        )
        for definition in definitions
    ]
    severity_counts: dict[str, int] = {}
    for result in results:
        for item in result["diagnostics"]:
            severity = str(item.get("severity") or "INFO").upper()
            severity_counts[severity] = severity_counts.get(severity, 0) + 1
    execution_specs = dict(execution_specs or {})
    legacy_hybrid = any(
        _dict(_dict(result.get("artifacts")).get("performance")).get("artifact_id")
        for result in results
    )
    if not legacy_hybrid:
        for result in results:
            result["artifacts"] = {
                role: artifact
                for role, artifact in _dict(result.get("artifacts")).items()
                if role in {"alpha", "evaluation"}
            }
            result.pop("performance", None)
            result.pop("costs", None)
    return {
        "schema_version": (
            LEGACY_ALPHA_RUN_RESULT_SCHEMA_VERSION
            if legacy_hybrid else ALPHA_RUN_RESULT_SCHEMA_VERSION
        ),
        "product_run_type": "LEGACY_HYBRID_RUN" if legacy_hybrid else "ALPHA_RUN",
        "legacy_hybrid": legacy_hybrid,
        "migration_notice": (
            "This immutable historical Alpha Run contains embedded portfolio and backtest artifacts. "
            "New Alpha Evaluations stop at predictive signal evaluation."
            if legacy_hybrid else ""
        ),
        "run_id": run.get("run_id"),
        "project_id": run.get("project_id"),
        "status": run.get("status"),
        "bundle_id": run.get("bundle_id"),
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "boundary": ({
            "starts_at": "SIGNAL_CONSTRUCTION",
            "ends_at": "COST_ADJUSTED_PORTFOLIO_AND_TRADING_PERFORMANCE",
            "includes": [
                "ALPHA_DEFINITION", "FACTOR_INPUTS", "UNIVERSE", "SIGNAL_RULES",
                "PORTFOLIO_RULES", "EXECUTION_ASSUMPTIONS", "SIGNALS", "POSITIONS",
                "TRADES", "EQUITY_CURVE", "PERFORMANCE_METRICS", "DRAWDOWN",
                "DIAGNOSTICS", "LOGS",
            ],
            "excludes": ["STRATEGY_DEPLOYMENT", "PAPER_TRADING", "LIVE_TRADING"],
        } if legacy_hybrid else {
            "starts_at": "SIGNAL_CONSTRUCTION",
            "ends_at": "SIGNAL_PREDICTIVE_EVALUATION",
            "includes": [
                "ALPHA_DEFINITION", "FACTOR_INPUTS", "UNIVERSE", "SIGNAL_RULES",
                "SIGNALS", "IC_ACCURACY", "DECAY", "TURNOVER", "REGIME_ANALYSIS",
                "DIAGNOSTICS", "LOGS",
            ],
            "excludes": [
                "PORTFOLIO_CONSTRUCTION", "EXECUTION", "POSITIONS", "TRADES",
                "EQUITY_CURVE", "BACKTEST_PERFORMANCE", "STRATEGY_DEPLOYMENT",
                "PAPER_TRADING", "LIVE_TRADING",
            ],
        }),
        "universe": dict(universe or {}),
        "data_inputs": [dict(item) for item in data_inputs],
        "portfolio_rules": _dict(execution_specs.get("portfolio_spec")) if legacy_hybrid else {},
        "execution_assumptions": _dict(execution_specs.get("execution_spec")) if legacy_hybrid else {},
        "evaluation_spec": _dict(execution_specs.get("evaluation_spec")),
        "results": results,
        "diagnostic_summary": severity_counts,
    }


def alpha_run_section(contract: Mapping[str, Any], section_key: str) -> dict[str, Any]:
    key = str(section_key or "").strip().lower()
    if key not in ALPHA_RUN_STRUCTURED_SECTIONS:
        raise ValueError(f"unsupported structured Alpha Run section: {key}")
    if (
        str(contract.get("product_run_type") or "") == "ALPHA_RUN"
        and not bool(contract.get("legacy_hybrid"))
        and key not in ALPHA_EVALUATION_STRUCTURED_SECTIONS
    ):
        raise ValueError(f"unsupported structured Alpha Evaluation section: {key}")
    items = []
    artifact_ids = []
    artifact_type = {
        "signals": "ALPHA_VALUES",
        "ic_accuracy": "ALPHA_EVALUATION",
        "decay": "ALPHA_EVALUATION",
        "turnover": "ALPHA_EVALUATION",
        "regime_analysis": "ALPHA_EVALUATION",
        "portfolio_targets": "PORTFOLIO_TARGETS",
        "positions": "POSITION_SERIES",
        "trades": "BACKTEST_ORDERS",
        "equity_curve": "EQUITY_SERIES",
        "performance_metrics": "BACKTEST_RESULT",
        "drawdown": "DRAWDOWN_SERIES",
        "diagnostics": "ALPHA_EVALUATION",
    }[key]
    role = {
        "signals": "alpha",
        "ic_accuracy": "evaluation",
        "decay": "evaluation",
        "turnover": "evaluation",
        "regime_analysis": "evaluation",
        "portfolio_targets": "portfolio_targets",
        "positions": "positions",
        "trades": "trades",
        "equity_curve": "equity_curve",
        "performance_metrics": "performance",
        "drawdown": "drawdown",
        "diagnostics": "evaluation",
    }[key]
    for result in _list(contract.get("results")):
        if not isinstance(result, Mapping):
            continue
        result = dict(result)
        artifact = _dict(_dict(result.get("artifacts")).get(role))
        if artifact.get("artifact_id"):
            artifact_ids.append(str(artifact["artifact_id"]))
        base = {"alpha": _dict(result.get("alpha")), "artifact": artifact}
        if key == "signals":
            item = {
                **base,
                "signal_summary": _dict(result.get("signal_summary")),
            }
        elif key == "ic_accuracy":
            item = {
                **base,
                "ic": _dict(result.get("ic")),
                "rank_ic": _dict(result.get("rank_ic")),
            }
        elif key == "decay":
            item = {**base, "holding_period_decay": _dict(result.get("holding_period_decay"))}
        elif key == "turnover":
            summary = _dict(result.get("signal_summary"))
            item = {**base, "turnover_summary": {
                "average_membership_turnover": summary.get("average_membership_turnover"),
                "average_rank_stability": summary.get("average_rank_stability"),
                "score_count": summary.get("score_count"),
            }}
        elif key == "regime_analysis":
            item = {**base, "regime_performance": _dict(result.get("regime_performance"))}
        elif key == "portfolio_targets":
            item = {**base, "portfolio_rules": _dict(contract.get("portfolio_rules"))}
        elif key == "positions":
            item = {**base, "exposure_summary": {name: _dict(result.get("performance")).get(name) for name in ("average_exposure", "average_cash_ratio", "bar_count", "instrument_count")}}
        elif key == "trades":
            item = {**base, "trade_summary": {name: _dict(result.get("performance")).get(name) for name in ("trade_count", "turnover", "fees", "slippage_cost", "rebalance_count", "invested_rebalance_count", "flat_rebalance_count")}}
        elif key == "equity_curve":
            item = {**base, "equity_summary": {name: _dict(result.get("performance")).get(name) for name in ("initial_cash", "final_equity", "total_return", "annualized_return", "volatility", "sharpe")}}
        elif key == "performance_metrics":
            item = {**base, "performance": _dict(result.get("performance")), "costs": _dict(result.get("costs"))}
        elif key == "drawdown":
            item = {**base, "drawdown_summary": {name: _dict(result.get("performance")).get(name) for name in ("max_drawdown", "max_drawdown_at", "max_drawdown_peak_at", "max_underwater_bars")}}
        else:
            item = {**base, "diagnostics": [dict(value) for value in _list(result.get("diagnostics")) if isinstance(value, Mapping)]}
        items.append(item)
    return {
        "schema_version": str(contract.get("schema_version") or ALPHA_RUN_RESULT_SCHEMA_VERSION),
        "section": key,
        "view_type": f"ALPHA_RUN_{key.upper()}",
        "artifact_type": artifact_type,
        "artifact_ids": sorted(set(artifact_ids)),
        "items": items,
    }


class AlphaRunResultService:
    """Build strict Alpha Evaluation views while preserving legacy hybrid Run reads."""

    def __init__(self, store: Any):
        self.store = store

    def build(self, run_or_id: Mapping[str, Any] | str) -> dict[str, Any]:
        run_service = ResearchRunService(self.store)
        run = dict(run_or_id) if isinstance(run_or_id, Mapping) else run_service.get(str(run_or_id))
        if not run:
            raise ValueError("Research Run not found")
        if str(run.get("run_type") or "") != "ALPHA_EVALUATION":
            raise ValueError("Research Run is not an Alpha Run")
        bundle = run_service.get_bundle(str(run.get("bundle_id") or ""))
        frozen = _dict(_dict(bundle).get("canonical_payload"))
        closure = _dict(frozen.get("input_closure"))
        registry = DefinitionRegistry(self.store)
        factor_definitions = []
        for ref in _list(closure.get("factor_definitions")):
            definition = registry.get(str(ref.get("factor_definition_id") or ""), version=str(ref.get("version") or ""))
            factor_definitions.append(definition.to_dict() if definition else dict(ref))
        alpha_definitions = []
        for ref in _list(closure.get("alpha_definitions")):
            definition = registry.get(str(ref.get("alpha_definition_id") or ""), version=str(ref.get("version") or ""))
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
            if artifact.created_by_run_id == run.get("run_id")
            or artifact.artifact_id in produced_ids
        ]
        dependencies = artifact_service.dependencies_many([item.artifact_id for item in artifacts])
        return build_alpha_run_contract(
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
        return alpha_run_section(self.build(run_or_id), section_key)
