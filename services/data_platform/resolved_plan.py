from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

from .artifact_service import ArtifactService
from .requirement_compiler import RequirementCompiler
from .research_control_plane import ResearchControlPlane
from .store import BASE_DIR, DataPlatformStore, json_dumps
from services.history_storage_service import get_data_platform_storage_root


def _values(value: Any) -> set[str]:
    if value in (None, ""): return set()
    if isinstance(value, (list, tuple, set)): return {str(item).strip().lower() for item in value if str(item).strip()}
    return {str(value).strip().lower()}


class ResolvedDataPlanService:
    """Freeze a concrete data route and prove that it is inside one Grant Scope."""

    def __init__(self, store: DataPlatformStore, output_root: str | Path | None = None):
        self.store = store
        self.artifacts = ArtifactService(store)
        self.requirements = RequirementCompiler(store)
        self.control = ResearchControlPlane(store)
        self.output_root = Path(output_root or get_data_platform_storage_root() / "research_artifacts" / "resolved_plans")

    def create(self, *, project_id: str, logical_name: str, requirement_set_id: str,
               route: dict[str, Any], source_policy: dict[str, Any], canonical: dict[str, Any],
               estimates: dict[str, Any] | None = None):
        requirement_set = self.requirements.get(requirement_set_id)
        if not requirement_set or requirement_set.project_id != project_id:
            raise ValueError("RequirementSet does not belong to project")
        payload = {"project_id": project_id, "requirement_set_id": requirement_set_id,
                   "requirement_set_fingerprint": requirement_set.fingerprint, "route": route,
                   "source_policy": source_policy, "canonical": canonical, "estimates": estimates or {}}
        digest = hashlib.sha256(json_dumps(payload).encode()).hexdigest()
        self.output_root.mkdir(parents=True, exist_ok=True)
        path = self.output_root / f"sha256-{digest}.json"
        if not path.exists():
            tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
            os.replace(tmp, path)
        return self.artifacts.create(artifact_type="RESOLVED_DATA_PLAN", logical_name=logical_name,
            content_uri=str(path), content_hash=digest, schema_version="resolved_data_plan.v1",
            project_id=project_id, spec_hash=digest, metadata=payload,
            dependencies=[{"parent_id": requirement_set_id, "parent_type": "REQUIREMENT_SET", "dependency_type": "RESOLVES"}])

    def validate_grant(self, plan_artifact_id: str, grant_id: str) -> dict[str, Any]:
        plan = self.artifacts.get(plan_artifact_id)
        grant = self.control.get_grant(grant_id)
        if not plan or plan.artifact_type != "RESOLVED_DATA_PLAN": raise ValueError("Resolved Data Plan not found")
        if not grant or grant["status"] != "ACTIVE": raise PermissionError("active Grant is required")
        if grant["project_id"] != plan.project_id: raise PermissionError("Grant project does not match plan")
        data, scope = plan.metadata, grant.get("scope") or {}
        requested = {
            "gateways": _values((data.get("route") or {}).get("gateway")),
            "endpoints": _values((data.get("route") or {}).get("endpoint")),
            "providers": _values((data.get("source_policy") or {}).get("providers")),
            "adjustment_modes": _values((data.get("canonical") or {}).get("adjustment")),
        }
        violations = []
        for key, values in requested.items():
            allowed = _values(scope.get(f"allowed_{key}") or scope.get(key))
            if values and allowed and not values <= allowed:
                violations.append({"field": key, "requested": sorted(values), "allowed": sorted(allowed)})
        estimates, budgets = data.get("estimates") or {}, grant.get("budgets") or {}
        for field, budget_key in (("download_bytes", "max_download_bytes"), ("runtime_seconds", "max_runtime_seconds")):
            if int(estimates.get(field) or 0) > int(budgets.get(budget_key) or 0):
                violations.append({"field": field, "requested": int(estimates.get(field) or 0), "allowed": int(budgets.get(budget_key) or 0)})
        return {"plan_artifact_id": plan_artifact_id, "grant_id": grant_id,
                "within_scope": not violations, "violations": violations,
                "resolved_plan_hash": plan.content_hash, "approval_required": bool(violations)}
