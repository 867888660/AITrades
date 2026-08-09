from __future__ import annotations

from dataclasses import asdict
from typing import Any, Mapping, Sequence

from .artifact_service import ArtifactService
from .definition_registry import DefinitionRegistry
from .research_run_service import ResearchRunService
from .universe_service import UniverseService


FACTOR_RUN_RESULT_SCHEMA_VERSION = "factor-run-result.v1"
FACTOR_RUN_STRUCTURED_SECTIONS = {
    "coverage",
    "distribution",
    "ic_rank_ic",
    "quantile_return",
    "diagnostics",
}


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


def _artifact_item(artifact: Any) -> dict[str, Any]:
    if isinstance(artifact, Mapping):
        return dict(artifact)
    return {
        "artifact_id": artifact.artifact_id,
        "artifact_type": artifact.artifact_type,
        "logical_name": artifact.logical_name,
        "version": artifact.version,
        "status": artifact.status,
        "schema_version": artifact.schema_version,
        "engine_version": artifact.engine_version,
        "row_count": int(artifact.metadata.get("row_count") or 0),
        "metadata": dict(artifact.metadata),
    }


def _horizon_sort(value: Any) -> tuple[int, str]:
    text = str(value)
    try:
        return int(text), text
    except (TypeError, ValueError):
        return 10**9, text


def _factor_identity(definition: Mapping[str, Any], name: str) -> dict[str, Any]:
    definition = dict(definition or {})
    spec = _dict(definition.get("spec"))
    return {
        "definition_id": str(definition.get("definition_id") or ""),
        "name": str(definition.get("name") or spec.get("name") or name),
        "version": str(definition.get("version") or spec.get("version") or ""),
        "spec_hash": str(definition.get("spec_hash") or ""),
        "engine_version": str(definition.get("engine_version") or spec.get("engine_version") or ""),
        "dimension": str(spec.get("dimension") or ""),
        "frequency": str(spec.get("frequency") or ""),
        "output_unit": str(spec.get("output_unit") or ""),
    }


def _evaluation_result(
    *,
    definition: Mapping[str, Any],
    factor_artifact: Mapping[str, Any] | None,
    evaluation_artifact: Mapping[str, Any] | None,
) -> dict[str, Any]:
    factor_artifact = dict(factor_artifact or {})
    factor_metadata = _dict(factor_artifact.get("metadata"))
    evaluation_artifact = dict(evaluation_artifact or {})
    evaluation_metadata = _dict(evaluation_artifact.get("metadata"))
    summary = _dict(evaluation_metadata.get("summary"))
    evaluation_spec = _dict(summary.get("evaluation_spec") or evaluation_metadata.get("evaluation_spec"))
    factor_name = str(
        definition.get("name")
        or factor_metadata.get("factor_name")
        or factor_artifact.get("logical_name")
        or evaluation_artifact.get("logical_name")
        or "Factor"
    )
    coverage_by_instrument = _dict(summary.get("coverage_by_instrument"))
    coverage = {
        "overall": summary.get("coverage"),
        "missing_rate": summary.get("missing_rate"),
        "valid_rows": summary.get("valid_rows"),
        "total_rows": summary.get("total_rows"),
        "by_instrument": coverage_by_instrument,
        "cross_section_count": summary.get("cross_section_count"),
        "eligible_cross_section_count": summary.get("eligible_cross_section_count"),
    }
    distribution = {
        "mean": summary.get("mean"),
        "std": summary.get("std"),
        "quantiles": _dict(summary.get("quantiles")),
        "outlier_ratio_5sigma": summary.get("outlier_ratio_5sigma"),
        "time_stability": _dict(summary.get("time_stability")),
        "average_rank_turnover": summary.get("average_rank_turnover"),
    }
    ic = _dict(summary.get("ic"))
    rank_ic = _dict(summary.get("rank_ic"))
    horizon_keys = sorted(
        {str(item) for item in (*evaluation_spec.get("horizons", []), *ic.keys(), *rank_ic.keys())},
        key=_horizon_sort,
    )
    predictive_power = [
        {
            "horizon_bars": int(key) if key.isdigit() else key,
            "ic": _dict(ic.get(key)),
            "rank_ic": _dict(rank_ic.get(key)),
        }
        for key in horizon_keys
    ]
    quantile_returns = []
    for key in sorted(_dict(summary.get("quantile_returns")), key=_horizon_sort):
        item = _dict(_dict(summary.get("quantile_returns")).get(key))
        groups = [
            {"quantile": int(group) if str(group).isdigit() else group, "mean_return": value}
            for group, value in sorted(_dict(item.get("mean_returns")).items(), key=lambda pair: _horizon_sort(pair[0]))
        ]
        quantile_returns.append({
            "horizon_bars": int(key) if str(key).isdigit() else key,
            "groups": groups,
            "high_minus_low": item.get("high_minus_low"),
            "monotonicity": item.get("monotonicity"),
        })
    diagnostics = [dict(item) for item in _list(summary.get("diagnostics")) if isinstance(item, Mapping)]
    return {
        "factor": _factor_identity(definition, factor_name),
        "factor_artifact": {
            "artifact_id": factor_artifact.get("artifact_id"),
            "row_count": factor_artifact.get("row_count") or factor_metadata.get("row_count"),
            "schema_version": factor_artifact.get("schema_version"),
            "status": factor_artifact.get("status"),
        },
        "evaluation_artifact": {
            "artifact_id": evaluation_artifact.get("artifact_id"),
            "schema_version": evaluation_artifact.get("schema_version"),
            "status": evaluation_artifact.get("status"),
            "evaluation_spec_hash": summary.get("evaluation_spec_hash") or evaluation_metadata.get("evaluation_spec_hash"),
        },
        "evaluation_spec": evaluation_spec,
        "coverage": coverage,
        "distribution": distribution,
        "predictive_power": predictive_power,
        "quantile_returns": quantile_returns,
        "diagnostics": diagnostics,
    }


def build_factor_run_contract(
    *,
    run: Mapping[str, Any],
    factor_definitions: Sequence[Mapping[str, Any]],
    universe: Mapping[str, Any],
    data_inputs: Sequence[Mapping[str, Any]],
    artifacts: Sequence[Any],
) -> dict[str, Any]:
    run = dict(run or {})
    if str(run.get("run_type") or "") != "FACTOR_EVALUATION":
        raise ValueError("Factor Run result contract requires FACTOR_EVALUATION")
    artifact_items = [_artifact_item(item) for item in artifacts]
    factor_artifacts = [item for item in artifact_items if item.get("artifact_type") == "FACTOR_VALUES"]
    evaluation_artifacts = [item for item in artifact_items if item.get("artifact_type") == "FACTOR_EVALUATION"]
    factor_by_name = {
        str(_dict(item.get("metadata")).get("factor_name") or item.get("logical_name") or ""): item
        for item in factor_artifacts
    }
    evaluation_by_input = {
        str(_dict(item.get("metadata")).get("input_artifact_id") or ""): item
        for item in evaluation_artifacts
    }
    definitions = [dict(item) for item in factor_definitions]
    if not definitions:
        definitions = [{"name": name, "spec": _dict(_dict(item.get("metadata")).get("factor_spec"))} for name, item in factor_by_name.items()]
    results = []
    for definition in definitions:
        name = str(definition.get("name") or _dict(definition.get("spec")).get("name") or "")
        factor_artifact = factor_by_name.get(name)
        evaluation_artifact = evaluation_by_input.get(str((factor_artifact or {}).get("artifact_id") or ""))
        if evaluation_artifact is None:
            evaluation_artifact = next(
                (item for item in evaluation_artifacts if str(item.get("logical_name") or "").startswith(f"{name}-")),
                None,
            )
        results.append(_evaluation_result(
            definition=definition,
            factor_artifact=factor_artifact,
            evaluation_artifact=evaluation_artifact,
        ))
    severity_counts: dict[str, int] = {}
    for result in results:
        for item in result["diagnostics"]:
            severity = str(item.get("severity") or "INFO").upper()
            severity_counts[severity] = severity_counts.get(severity, 0) + 1
    return {
        "schema_version": FACTOR_RUN_RESULT_SCHEMA_VERSION,
        "product_run_type": "FACTOR_RUN",
        "run_id": run.get("run_id"),
        "project_id": run.get("project_id"),
        "status": run.get("status"),
        "bundle_id": run.get("bundle_id"),
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "boundary": {
            "ends_at": "FACTOR_PREDICTIVE_POWER_AND_GROUP_PERFORMANCE",
            "includes": ["FACTOR_OUTPUT", "COVERAGE", "DISTRIBUTION", "IC", "RANK_IC", "QUANTILE_RETURN", "DIAGNOSTICS", "LOGS"],
            "excludes": ["SIGNAL_RULES", "POSITIONS", "TRADES", "EXECUTION_COSTS", "EQUITY_CURVE", "DRAWDOWN"],
        },
        "universe": dict(universe or {}),
        "data_inputs": [dict(item) for item in data_inputs],
        "results": results,
        "diagnostic_summary": severity_counts,
    }


def factor_run_section(contract: Mapping[str, Any], section_key: str) -> dict[str, Any]:
    key = str(section_key or "").strip().lower()
    if key not in FACTOR_RUN_STRUCTURED_SECTIONS:
        raise ValueError(f"unsupported structured Factor Run section: {key}")
    results = [dict(item) for item in _list(contract.get("results"))]
    rows: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    artifact_ids: list[str] = []
    for result in results:
        factor = _dict(result.get("factor"))
        name = str(factor.get("name") or "Factor")
        artifact_id = str(_dict(result.get("evaluation_artifact")).get("artifact_id") or "")
        if artifact_id:
            artifact_ids.append(artifact_id)
        if key == "coverage":
            value = _dict(result.get("coverage"))
            items.append({"factor": factor, "coverage": value})
            rows.append({"factor_name": name, "scope": "OVERALL", **{k: value.get(k) for k in ("overall", "missing_rate", "valid_rows", "total_rows", "cross_section_count", "eligible_cross_section_count")}})
            for instrument_id, instrument in sorted(_dict(value.get("by_instrument")).items()):
                rows.append({"factor_name": name, "scope": "INSTRUMENT", "instrument_id": instrument_id, **_dict(instrument)})
        elif key == "distribution":
            value = _dict(result.get("distribution"))
            items.append({"factor": factor, "distribution": value})
            rows.append({"factor_name": name, "statistic": "SUMMARY", **{k: value.get(k) for k in ("mean", "std", "outlier_ratio_5sigma", "average_rank_turnover")}})
            rows.extend({"factor_name": name, "statistic": "QUANTILE", "quantile": quantile, "value": number} for quantile, number in sorted(_dict(value.get("quantiles")).items(), key=lambda pair: _horizon_sort(pair[0])))
        elif key == "ic_rank_ic":
            value = _list(result.get("predictive_power"))
            items.append({"factor": factor, "predictive_power": value})
            for horizon in value:
                ic = _dict(horizon.get("ic"))
                rank_ic = _dict(horizon.get("rank_ic"))
                rows.append({
                    "factor_name": name,
                    "horizon_bars": horizon.get("horizon_bars"),
                    "ic_mean": ic.get("mean"),
                    "ic_std": ic.get("std"),
                    "ic_ir": ic.get("icir"),
                    "ic_t_stat": ic.get("t_stat"),
                    "ic_positive_rate": ic.get("positive_rate"),
                    "ic_count": ic.get("count"),
                    "rank_ic_mean": rank_ic.get("mean"),
                    "rank_ic_std": rank_ic.get("std"),
                    "rank_ic_ir": rank_ic.get("icir"),
                    "rank_ic_t_stat": rank_ic.get("t_stat"),
                    "rank_ic_positive_rate": rank_ic.get("positive_rate"),
                    "rank_ic_count": rank_ic.get("count"),
                })
        elif key == "quantile_return":
            value = _list(result.get("quantile_returns"))
            items.append({"factor": factor, "quantile_returns": value})
            for horizon in value:
                for group in _list(horizon.get("groups")):
                    rows.append({
                        "factor_name": name,
                        "horizon_bars": horizon.get("horizon_bars"),
                        "quantile": group.get("quantile"),
                        "mean_return": group.get("mean_return"),
                        "high_minus_low": horizon.get("high_minus_low"),
                        "monotonicity": horizon.get("monotonicity"),
                    })
        elif key == "diagnostics":
            value = _list(result.get("diagnostics"))
            items.append({"factor": factor, "diagnostics": value})
            rows.extend({"factor_name": name, **dict(item)} for item in value if isinstance(item, Mapping))
    return {
        "schema_version": str(contract.get("schema_version") or FACTOR_RUN_RESULT_SCHEMA_VERSION),
        "section": key,
        "view_type": f"FACTOR_RUN_{key.upper()}",
        "artifact_type": "FACTOR_EVALUATION",
        "artifact_ids": sorted(set(artifact_ids)),
        "items": items,
        "rows": rows,
        "total_rows": len(rows),
    }


class FactorRunResultService:
    """Build a stable read model over immutable Factor Run artifacts."""

    def __init__(self, store: Any):
        self.store = store

    def build(self, run_or_id: Mapping[str, Any] | str) -> dict[str, Any]:
        run_service = ResearchRunService(self.store)
        run = dict(run_or_id) if isinstance(run_or_id, Mapping) else run_service.get(str(run_or_id))
        if not run:
            raise ValueError("Research Run not found")
        if str(run.get("run_type") or "") != "FACTOR_EVALUATION":
            raise ValueError("Research Run is not a Factor Run")
        bundle = run_service.get_bundle(str(run.get("bundle_id") or ""))
        frozen = _dict(_dict(bundle).get("canonical_payload"))
        closure = _dict(frozen.get("input_closure"))
        registry = DefinitionRegistry(self.store)
        definitions = []
        for ref in _list(closure.get("factor_definitions")):
            definition = registry.get(str(ref.get("factor_definition_id") or ""), version=str(ref.get("version") or ""))
            definitions.append(definition.to_dict() if definition else dict(ref))
        snapshot = UniverseService(self.store).get_snapshot(str(closure.get("universe_snapshot_id") or ""))
        universe = asdict(snapshot) if snapshot else {
            "universe_snapshot_id": closure.get("universe_snapshot_id"),
            "actual_instrument_ids": closure.get("resolved_instrument_ids") or [],
        }
        produced_ids = _produced_artifact_ids(run)
        artifacts = [
            artifact
            for artifact in ArtifactService(self.store).list(limit=1000)
            if artifact.created_by_run_id == run.get("run_id")
            or artifact.artifact_id in produced_ids
        ]
        return build_factor_run_contract(
            run=run,
            factor_definitions=definitions,
            universe=universe,
            data_inputs=_list(frozen.get("manifest_descriptors")),
            artifacts=artifacts,
        )

    def section(self, run_or_id: Mapping[str, Any] | str, section_key: str) -> dict[str, Any]:
        return factor_run_section(self.build(run_or_id), section_key)
