from __future__ import annotations

from typing import Any, Mapping

from .artifact_service import ArtifactService
from .research_run_service import ResearchRunService


FACTOR_PACK_RESULT_SCHEMA_VERSION = "factor-pack-evaluation-result.v1"


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


class FactorPackRunResultService:
    """Read one immutable Factor Pack matrix and its aggregated evaluations."""

    def __init__(self, store: Any):
        self.store = store

    def build(self, run_or_id: Mapping[str, Any] | str) -> dict[str, Any]:
        run_service = ResearchRunService(self.store)
        run = dict(run_or_id) if isinstance(run_or_id, Mapping) else run_service.get(str(run_or_id))
        if not run or str(run.get("run_type") or "") != "FACTOR_EVALUATION":
            raise ValueError("Factor Pack result requires a Factor Evaluation Run")
        bundle = run_service.get_bundle(str(run.get("bundle_id") or "")) or {}
        frozen = _dict(bundle.get("canonical_payload"))
        closure = _dict(frozen.get("input_closure"))
        definitions = list(closure.get("factor_pack_definitions") or [])
        if len(definitions) != 1:
            raise ValueError("Factor Pack Run requires exactly one frozen FactorPackDefinition")
        artifacts = [
            item for item in ArtifactService(self.store).list(limit=1000)
            if item.created_by_run_id == run.get("run_id")
        ]
        values = next((item for item in artifacts if item.artifact_type == "FACTOR_PACK_VALUES"), None)
        evaluation = next(
            (item for item in artifacts if item.artifact_type == "FACTOR_PACK_EVALUATION"), None
        )
        if values is None or evaluation is None:
            raise ValueError("Factor Pack Run artifacts are incomplete")
        summary = _dict(evaluation.metadata.get("summary"))
        return {
            "schema_version": FACTOR_PACK_RESULT_SCHEMA_VERSION,
            "product_run_type": "FACTOR_PACK_RUN",
            "status": run.get("status"),
            "factor_pack": dict(definitions[0]),
            "evaluation_spec": _dict(evaluation.metadata.get("evaluation_spec")),
            "summary": summary,
            "results": list(evaluation.metadata.get("results") or []),
            "artifacts": {
                "values": {
                    "artifact_id": values.artifact_id,
                    "row_count": values.metadata.get("row_count"),
                    "factor_count": values.metadata.get("factor_count"),
                },
                "evaluation": {
                    "artifact_id": evaluation.artifact_id,
                    "row_count": evaluation.metadata.get("row_count"),
                },
            },
        }


__all__ = ["FACTOR_PACK_RESULT_SCHEMA_VERSION", "FactorPackRunResultService"]
