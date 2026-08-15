from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .definition_registry import DefinitionRegistry
from .factor_pack import FactorPackRegistry
from .manifest_resolver import DeterministicManifestResolver
from .run_contracts import (
    READINESS_RULE_VERSION,
    ReadinessCheck,
    ReadinessDimension,
    ReadinessReport,
    ReadinessStatus,
    RemediationCode,
    ResearchReasonCode,
    build_preview_fingerprint,
)
from .store import DataPlatformStore, json_dumps, utc_now


SUPPORTED_RESEARCH_RUN_TYPES = {
    "FACTOR_EVALUATION", "ALPHA_EVALUATION", "RESEARCH_BACKTEST",
}
FORMAL_RESEARCH_WORKER_VERSION = "formal-research-worker.v2"
FORMAL_RESEARCH_WORKER_CODE_HASH = hashlib.sha256(
    Path(__file__).with_name("research_run_service.py").read_bytes()
).hexdigest()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _hash_spec(value: Any) -> str:
    if isinstance(value, str) and len(value.strip()) == 64:
        return value.strip().lower()
    return hashlib.sha256(json_dumps(value if value is not None else {}).encode("utf-8")).hexdigest()


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class ResearchRunPreviewService:
    """Server-owned resolution and layered Readiness report for a proposed Run."""

    def __init__(self, store: DataPlatformStore):
        self.store = store
        self.registry = DefinitionRegistry(store)
        self.resolver = DeterministicManifestResolver(store)

    def create(self, project_id: str, payload: dict[str, Any], *, created_by: str = "local_user") -> dict[str, Any]:
        request = dict(payload)
        run_type = _clean(request.get("run_type")).upper()
        if not run_type:
            raise ValueError("run_type is required")
        project, definition_closure, definition_checks = self._definition_closure(project_id, run_type, request)
        resolution = self.resolver.resolve(
            _clean(request.get("requirement_set_id")),
            source_selection_policy=request.get("source_selection_policy") or {},
            verify_physical=bool(request.get("verify_physical", True)),
        )
        execution_closure, execution_checks = self._execution_closure(run_type, definition_closure, request)
        authorization_closure, authorization_checks = self._authorization_closure(project_id, run_type, request)
        checks = [*definition_checks, *resolution.checks, *authorization_checks, *execution_checks]
        report = ReadinessReport.build(checks)
        data_closure = {
            "resolved_manifest_ids": list(resolution.exact_manifest_ids),
            "resolver_version": resolution.resolver_version,
            "source_selection_policy_version": resolution.source_selection_policy_version,
        }
        fingerprint = build_preview_fingerprint(
            definition_closure=definition_closure,
            data_resolution_closure=data_closure,
            execution_closure=execution_closure,
            authorization_closure=authorization_closure,
        )
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            existing = conn.execute(
                "SELECT preview_id FROM research_run_previews WHERE project_id=? AND preview_fingerprint=?",
                (_clean(project_id), fingerprint.value),
            ).fetchone()
            if existing:
                preview_id = str(existing[0])
                conn.execute(
                    "UPDATE research_run_previews SET status=?, readiness_json=?, resolver_output_json=?, request_json=?, updated_at=? WHERE preview_id=?",
                    (
                        report.overall.value, json_dumps(report.to_dict()), json_dumps(resolution.to_dict()),
                        json_dumps(request), now, preview_id,
                    ),
                )
            else:
                preview_id = f"preview_{uuid.uuid4().hex}"
                conn.execute(
                    """
                    INSERT INTO research_run_previews(
                        preview_id, project_id, run_type, status, preview_fingerprint,
                        definition_closure_json, data_resolution_closure_json,
                        execution_closure_json, authorization_closure_json,
                        readiness_json, resolver_output_json, request_json,
                        created_by, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        preview_id, _clean(project_id), run_type, report.overall.value, fingerprint.value,
                        json_dumps(definition_closure), json_dumps(data_closure), json_dumps(execution_closure),
                        json_dumps(authorization_closure), json_dumps(report.to_dict()),
                        json_dumps(resolution.to_dict()), json_dumps(request), _clean(created_by) or "local_user", now, now,
                    ),
                )
        return self.get(preview_id)  # type: ignore[return-value]

    def refresh(self, preview_id: str) -> dict[str, Any]:
        existing = self.get(preview_id)
        if existing is None:
            raise ValueError("preview not found")
        refreshed = self.create(existing["project_id"], existing["request"], created_by=existing["created_by"])
        refreshed["supersedes_preview_id"] = existing["preview_id"]
        refreshed["previous_fingerprint"] = existing["preview_fingerprint"]
        refreshed["changed"] = refreshed["preview_fingerprint"] != existing["preview_fingerprint"]
        return refreshed

    def get(self, preview_id: str) -> dict[str, Any] | None:
        with self.store.connection() as conn:
            row = conn.execute("SELECT * FROM research_run_previews WHERE preview_id=?", (_clean(preview_id),)).fetchone()
        return self._row(row) if row else None

    def list(self, *, project_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = ""
        if _clean(project_id):
            where = " WHERE project_id=?"
            params.append(_clean(project_id))
        params.append(max(1, min(int(limit), 500)))
        with self.store.connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM research_run_previews{where} ORDER BY created_at DESC LIMIT ?", params
            ).fetchall()
        return [self._row(row) for row in rows]

    def _definition_closure(
        self, project_id: str, run_type: str, request: dict[str, Any]
    ) -> tuple[Any, dict[str, Any], list[ReadinessCheck]]:
        checks: list[ReadinessCheck] = []
        with self.store.connection() as conn:
            project = conn.execute("SELECT * FROM research_projects WHERE project_id=?", (_clean(project_id),)).fetchone()
            requirement_set = conn.execute(
                "SELECT * FROM requirement_sets WHERE requirement_set_id=?", (_clean(request.get("requirement_set_id")),)
            ).fetchone()
            snapshot = conn.execute(
                """
                SELECT s.*, d.version AS definition_version,
                       r.universe_id AS shared_universe_id,
                       r.revision_id AS shared_universe_revision_id,
                        r.resolution_id AS shared_universe_resolution_id,
                        r.instrument_tuples_json AS shared_instrument_tuples_json,
                        r.instrument_weights_json AS shared_instrument_weights_json,
                        r.resolution_metadata_json AS shared_resolution_metadata_json
                FROM universe_snapshots s JOIN universe_definitions d
                  ON d.universe_definition_id=s.universe_definition_id
                LEFT JOIN shared_universe_resolutions r
                  ON r.legacy_snapshot_id=s.universe_snapshot_id
                WHERE s.universe_snapshot_id=?
                """,
                (_clean(request.get("universe_snapshot_id")),),
            ).fetchone()
        if project is None:
            raise ValueError("research project not found")
        if requirement_set is None or str(requirement_set["project_id"]) != _clean(project_id):
            checks.append(self._check(
                ResearchReasonCode.DEFINITION_CLOSURE_INVALID, ReadinessDimension.DEFINITION, ReadinessStatus.BLOCKED,
                _clean(request.get("requirement_set_id")), {"project_id": project_id},
                {"project_id": requirement_set["project_id"] if requirement_set else None},
                RemediationCode.RECOMPILE_REQUIREMENTS, "Effective RequirementSet is missing or belongs to another project",
            ))
        if snapshot is None:
            checks.append(self._check(
                ResearchReasonCode.DEFINITION_CLOSURE_INVALID, ReadinessDimension.DEFINITION, ReadinessStatus.BLOCKED,
                _clean(request.get("universe_snapshot_id")), {"universe_snapshot": "existing"}, {},
                RemediationCode.OPEN_DEFINITION, "Universe Snapshot is required",
            ))
        refs = self.registry.list_project_refs(project_id)
        factor_refs: list[dict[str, Any]] = []
        alpha_refs: list[dict[str, Any]] = []
        for ref in refs.values():
            normalized = {
                f"{ref['definition_type'].lower()}_definition_id": ref["definition_id"],
                "version": ref["definition_version"],
                "spec_hash": ref["spec_hash"],
                "name": ref["name"],
            }
            (factor_refs if ref["definition_type"] == "FACTOR" else alpha_refs).append(normalized)
            if ref["reference_mode"] != "PINNED":
                checks.append(self._check(
                    ResearchReasonCode.TRACK_DRAFT_PRESENT, ReadinessDimension.DEFINITION, ReadinessStatus.BLOCKED,
                    ref["slot_key"], {"reference_mode": "PINNED"}, {"reference_mode": ref["reference_mode"]},
                    RemediationCode.VALIDATE_AND_PIN, "TRACK_DRAFT references are editing-only and cannot run",
                ))
            elif ref["state"] != "VALIDATED":
                checks.append(self._check(
                    ResearchReasonCode.REFERENCE_NOT_PINNED, ReadinessDimension.DEFINITION, ReadinessStatus.BLOCKED,
                    ref["slot_key"], {"state": "VALIDATED"}, {"state": ref["state"]},
                    RemediationCode.VALIDATE_AND_PIN, "Pinned definition is not validated",
                ))
        factor_refs.sort(key=lambda item: (item["factor_definition_id"], item["version"]))
        alpha_refs.sort(key=lambda item: (item["alpha_definition_id"], item["version"]))
        factor_pack_definitions: list[dict[str, Any]] = []
        requested_pack = dict(request.get("factor_pack_definition") or {})
        if requested_pack:
            try:
                pack = FactorPackRegistry.require(_clean(requested_pack.get("pack_id")))
                if requested_pack.get("spec_hash") != pack.spec_hash:
                    raise ValueError("Factor Pack spec_hash does not match the native registry")
                if run_type != "FACTOR_EVALUATION":
                    raise ValueError("Factor Pack execution currently requires FACTOR_EVALUATION")
                factor_pack_definitions.append(pack.to_dict())
            except ValueError as exc:
                checks.append(self._check(
                    ResearchReasonCode.DEFINITION_CLOSURE_INVALID,
                    ReadinessDimension.DEFINITION,
                    ReadinessStatus.BLOCKED,
                    _clean(requested_pack.get("pack_id")),
                    {"factor_pack": "registered immutable definition"},
                    {"error": str(exc)},
                    RemediationCode.OPEN_DEFINITION,
                    "Factor Pack identity is invalid",
                ))
        if run_type == "FACTOR_EVALUATION" and not factor_refs and not factor_pack_definitions:
            checks.append(self._missing_definition("FACTOR"))
        if run_type in {"ALPHA_EVALUATION", "RESEARCH_BACKTEST"} and not alpha_refs:
            checks.append(self._missing_definition("ALPHA"))
        if not any(item.status == ReadinessStatus.BLOCKED for item in checks):
            checks.append(self._check(
                ResearchReasonCode.DEFINITION_VALID, ReadinessDimension.DEFINITION, ReadinessStatus.READY,
                _clean(project_id), {"references": "PINNED+VALIDATED"},
                {"factor_count": len(factor_refs), "alpha_count": len(alpha_refs)},
                RemediationCode.NONE, "Project definition closure is runnable",
            ))
        closure = {
            "project_version": int(project["revision"]),
            "universe_definition_id": str(snapshot["universe_definition_id"]) if snapshot else "",
            "universe_definition_version": str(snapshot["definition_version"]) if snapshot else "",
            "universe_snapshot_id": str(snapshot["universe_snapshot_id"]) if snapshot else "",
            "actual_instrument_ids": json.loads(snapshot["actual_instrument_ids_json"] or "[]") if snapshot else [],
            "universe_id": str(snapshot["shared_universe_id"] or "") if snapshot else "",
            "universe_revision_id": str(snapshot["shared_universe_revision_id"] or "") if snapshot else "",
            "universe_resolution_id": str(snapshot["shared_universe_resolution_id"] or "") if snapshot else "",
            "resolved_instrument_tuples": json.loads(snapshot["shared_instrument_tuples_json"] or "[]") if snapshot else [],
            "resolved_instrument_weights": json.loads(snapshot["shared_instrument_weights_json"] or "{}") if snapshot else {},
            "universe_resolution_metadata": json.loads(snapshot["shared_resolution_metadata_json"] or "{}") if snapshot else {},
            "factor_definitions": factor_refs,
            "factor_pack_definitions": factor_pack_definitions,
            "alpha_definitions": alpha_refs,
            "requirement_set_id": str(requirement_set["requirement_set_id"]) if requirement_set else _clean(request.get("requirement_set_id")),
        }
        return project, closure, checks

    def _execution_closure(
        self, run_type: str, definitions: dict[str, Any], request: dict[str, Any]
    ) -> tuple[dict[str, Any], list[ReadinessCheck]]:
        from .evaluation import EVALUATION_CODE_HASH, EVALUATION_ENGINE_VERSION, EvaluationSpec
        from .portfolio import PORTFOLIO_CODE_HASH, PORTFOLIO_ENGINE_VERSION, PortfolioSpec
        from .research_backtest import RESEARCH_BACKTEST_CODE_HASH, RESEARCH_BACKTEST_ENGINE_VERSION, ResearchBacktestProvider
        from .equity_monthly_research import (
            EQUITY_MONTHLY_RESEARCH_CODE_HASH,
            EQUITY_MONTHLY_RESEARCH_ENGINE_VERSION,
        )

        checks: list[ReadinessCheck] = []
        if run_type not in SUPPORTED_RESEARCH_RUN_TYPES:
            checks.append(self._check(
                ResearchReasonCode.EXECUTION_SEMANTICS_UNSUPPORTED, ReadinessDimension.EXECUTION, ReadinessStatus.BLOCKED,
                run_type, {"run_types": sorted(SUPPORTED_RESEARCH_RUN_TYPES)}, {"run_type": run_type},
                RemediationCode.REVIEW_EXECUTION_SPEC, "Run type is not supported by the formal Research worker",
            ))
        if definitions.get("resolved_instrument_tuples"):
            checks.append(self._check(
                ResearchReasonCode.UNIVERSE_GROUP_EXECUTION_UNSUPPORTED,
                ReadinessDimension.EXECUTION,
                ReadinessStatus.BLOCKED,
                str(definitions.get("universe_resolution_id") or definitions.get("universe_snapshot_id") or ""),
                {"universe_member_semantics": "INDIVIDUAL_INSTRUMENTS"},
                {
                    "universe_member_semantics": "INSTRUMENT_GROUPS",
                    "combination_count": len(definitions["resolved_instrument_tuples"]),
                },
                RemediationCode.REVIEW_EXECUTION_SPEC,
                "This Run type does not yet define group-level Factor, Alpha, or portfolio semantics; execution is blocked instead of flattening the Universe",
            ))
        collection_ids = [
            str(item) for item in definitions.get("actual_instrument_ids", [])
            if str(item).upper().endswith(":ALL")
        ]
        if collection_ids:
            checks.append(self._check(
                ResearchReasonCode.COLLECTION_UNIVERSE_NOT_EXPANDED,
                ReadinessDimension.EXECUTION,
                ReadinessStatus.BLOCKED,
                str(definitions.get("universe_snapshot_id") or ""),
                {"universe_member_semantics": "ROW_LEVEL_INSTRUMENTS"},
                {"collection_catalog_ids": collection_ids},
                RemediationCode.REVIEW_EXECUTION_SPEC,
                "Collection Catalog ids describe datasets, not tradable securities; resolve a row-level historical Universe before execution",
            ))
        if request.get("input_factor_artifact_ids") or request.get("input_alpha_artifact_ids"):
            checks.append(self._check(
                ResearchReasonCode.EXECUTION_SEMANTICS_UNSUPPORTED,
                ReadinessDimension.EXECUTION,
                ReadinessStatus.BLOCKED,
                run_type,
                {"worker_input_mode": "DEFINITIONS"},
                {"requested_input_mode": "PRECOMPUTED_ARTIFACTS"},
                RemediationCode.REVIEW_EXECUTION_SPEC,
                "Frozen Bundle supports precomputed Artifact identities, but formal worker v2 only executes definition inputs",
            ))
        evaluation = request.get("evaluation_spec") or {}
        portfolio = request.get("portfolio_spec") or {}
        execution = request.get("execution_spec") or {}
        benchmark = request.get("benchmark_spec") or {}
        missing: list[str] = []
        if run_type in {"FACTOR_EVALUATION", "ALPHA_EVALUATION"} and not evaluation:
            missing.append("evaluation_spec")
        if run_type == "RESEARCH_BACKTEST":
            if not portfolio:
                missing.append("portfolio_spec")
            if not execution:
                missing.append("execution_spec")
        if missing:
            checks.append(self._check(
                ResearchReasonCode.EXECUTION_SEMANTICS_UNSUPPORTED, ReadinessDimension.EXECUTION, ReadinessStatus.BLOCKED,
                run_type, {"required_specs": missing}, {"missing": missing},
                RemediationCode.REVIEW_EXECUTION_SPEC, "Required execution contracts are missing",
            ))
        if run_type == "RESEARCH_BACKTEST" and not benchmark:
            checks.append(self._check(
                ResearchReasonCode.BENCHMARK_NOT_CONFIGURED,
                ReadinessDimension.EXECUTION,
                ReadinessStatus.WARNING,
                run_type,
                {"benchmark_spec": "recommended"},
                {"benchmark_spec": {}},
                RemediationCode.REVIEW_EXECUTION_SPEC,
                "Benchmark is not configured; excess return and information ratio will be unavailable",
            ))
        if not missing and run_type in SUPPORTED_RESEARCH_RUN_TYPES:
            try:
                if run_type in {"FACTOR_EVALUATION", "ALPHA_EVALUATION"}:
                    EvaluationSpec(**dict(evaluation))
                else:
                    PortfolioSpec(**dict(portfolio))
                    validation = ResearchBacktestProvider().validate(execution)
                    if not validation["ok"]:
                        raise ValueError(json.dumps(validation["issues"], ensure_ascii=False))
            except (TypeError, ValueError) as exc:
                checks.append(self._check(
                    ResearchReasonCode.EXECUTION_SEMANTICS_UNSUPPORTED,
                    ReadinessDimension.EXECUTION,
                    ReadinessStatus.BLOCKED,
                    run_type,
                    {"contract": "supported by current engine"},
                    {"error": str(exc)},
                    RemediationCode.REVIEW_EXECUTION_SPEC,
                    "Execution contract does not match current engine capabilities",
                ))
        versions = sorted({
            item.get("spec_hash", "") for item in [*definitions["factor_definitions"], *definitions["alpha_definitions"]]
        })
        versions.extend(
            str(item.get("spec_hash") or "")
            for item in definitions.get("factor_pack_definitions") or []
        )
        versions = sorted(set(versions))
        registry_rows = []
        for item in [*definitions["factor_definitions"], *definitions["alpha_definitions"]]:
            definition_id = item.get("factor_definition_id") or item.get("alpha_definition_id")
            found = self.registry.get(str(definition_id), version=str(item["version"]))
            if found:
                registry_rows.append(found)
        engine_versions = {item.engine_version for item in registry_rows}
        code_hashes = {item.code_hash for item in registry_rows}
        for pack in definitions.get("factor_pack_definitions") or []:
            engine_versions.add(f"factor-pack:{pack.get('engine')}:{pack.get('version')}")
            code_hashes.add(str(pack.get("code_hash") or pack.get("spec_hash") or ""))
        engine_versions.add(FORMAL_RESEARCH_WORKER_VERSION)
        code_hashes.add(FORMAL_RESEARCH_WORKER_CODE_HASH)
        if run_type in {"FACTOR_EVALUATION", "ALPHA_EVALUATION"}:
            engine_versions.add(EVALUATION_ENGINE_VERSION)
            code_hashes.add(EVALUATION_CODE_HASH)
        if run_type == "RESEARCH_BACKTEST":
            engine_versions.update({
                PORTFOLIO_ENGINE_VERSION,
                RESEARCH_BACKTEST_ENGINE_VERSION,
                EQUITY_MONTHLY_RESEARCH_ENGINE_VERSION,
            })
            code_hashes.update({
                PORTFOLIO_CODE_HASH,
                RESEARCH_BACKTEST_CODE_HASH,
                EQUITY_MONTHLY_RESEARCH_CODE_HASH,
            })
        engine_version = "+".join(sorted(engine_versions)) or "research-engine.v2"
        code_hash = _hash_spec(sorted(code_hashes))
        if not any(item.status == ReadinessStatus.BLOCKED for item in checks):
            checks.append(self._check(
                ResearchReasonCode.EXECUTION_VALID, ReadinessDimension.EXECUTION, ReadinessStatus.READY,
                run_type, {"worker": FORMAL_RESEARCH_WORKER_VERSION},
                {"engine_version": engine_version, "definition_spec_hashes": versions},
                RemediationCode.NONE, "Execution contracts match the built-in formal Research worker",
            ))
        return {
            "evaluation_spec_hash": _hash_spec(evaluation),
            "portfolio_spec_hash": _hash_spec(portfolio),
            "execution_spec_hash": _hash_spec(execution),
            "benchmark_spec_hash": _hash_spec(benchmark),
            "research_semantics_hash": _hash_spec(request.get("research_semantics") or {}),
            "engine_version": engine_version,
            "code_hash": code_hash,
            "readiness_rule_version": READINESS_RULE_VERSION,
        }, checks

    def _authorization_closure(
        self, project_id: str, run_type: str, request: dict[str, Any]
    ) -> tuple[dict[str, Any], list[ReadinessCheck]]:
        checks: list[ReadinessCheck] = []
        grant_id = _clean(request.get("grant_id"))
        actor_id = _clean(request.get("actor_id") or "local_user")
        actor_type = _clean(request.get("actor_type") or "HUMAN").upper()
        with self.store.connection() as conn:
            if grant_id:
                grant = conn.execute("SELECT * FROM approval_grants WHERE grant_id=?", (grant_id,)).fetchone()
            else:
                grant = conn.execute(
                    "SELECT * FROM approval_grants WHERE project_id=? AND status='ACTIVE' ORDER BY approved_at DESC LIMIT 1",
                    (_clean(project_id),),
                ).fetchone()
            counter = conn.execute(
                "SELECT * FROM approval_budget_counters WHERE grant_id=?", (str(grant["grant_id"]),)
            ).fetchone() if grant else None
        if grant is None:
            checks.append(self._check(
                ResearchReasonCode.GRANT_REQUIRED, ReadinessDimension.AUTHORIZATION, ReadinessStatus.BLOCKED,
                project_id, {"grant": "ACTIVE"}, {}, RemediationCode.REQUEST_SCOPE_EXPANSION,
                "An active approval Grant is required",
            ))
            return {"grant_id": "", "grant_version": "", "policy_version": ""}, checks
        grant_id = str(grant["grant_id"])
        if str(grant["project_id"]) != _clean(project_id):
            checks.append(self._check(
                ResearchReasonCode.GRANT_SCOPE_VIOLATION, ReadinessDimension.AUTHORIZATION, ReadinessStatus.BLOCKED,
                grant_id, {"project_id": project_id}, {"project_id": grant["project_id"]},
                RemediationCode.REQUEST_SCOPE_EXPANSION, "Grant belongs to another project",
            ))
        if str(grant["status"]) != "ACTIVE":
            checks.append(self._check(
                ResearchReasonCode.GRANT_REVOKED, ReadinessDimension.AUTHORIZATION, ReadinessStatus.BLOCKED,
                grant_id, {"status": "ACTIVE"}, {"status": grant["status"]},
                RemediationCode.RENEW_GRANT, "Grant is not active",
            ))
        if grant["expires_at"] and _parse_time(grant["expires_at"]) <= datetime.now(timezone.utc):
            checks.append(self._check(
                ResearchReasonCode.GRANT_EXPIRED, ReadinessDimension.AUTHORIZATION, ReadinessStatus.BLOCKED,
                grant_id, {"expires_after": utc_now()}, {"expires_at": grant["expires_at"]},
                RemediationCode.RENEW_GRANT, "Grant has expired",
            ))
        scope = json.loads(grant["scope_json"] or "{}")
        allowed_types = {str(item).upper() for item in scope.get("allowed_run_types", [])}
        if allowed_types and run_type not in allowed_types:
            checks.append(self._check(
                ResearchReasonCode.GRANT_SCOPE_VIOLATION, ReadinessDimension.AUTHORIZATION, ReadinessStatus.BLOCKED,
                grant_id, {"allowed_run_types": sorted(allowed_types)}, {"run_type": run_type},
                RemediationCode.REQUEST_SCOPE_EXPANSION, "Run type is outside Grant scope",
            ))
        for scope_key, request_key in (
            ("requirement_set_id", "requirement_set_id"),
            ("universe_snapshot_id", "universe_snapshot_id"),
        ):
            scoped = _clean(scope.get(scope_key))
            requested_identity = _clean(request.get(request_key))
            if scoped and scoped != requested_identity:
                checks.append(self._check(
                    ResearchReasonCode.GRANT_SCOPE_VIOLATION, ReadinessDimension.AUTHORIZATION, ReadinessStatus.BLOCKED,
                    grant_id, {scope_key: scoped}, {scope_key: requested_identity},
                    RemediationCode.REQUEST_SCOPE_EXPANSION, f"{scope_key} is outside Grant scope",
                ))
        if actor_type == "AGENT" and actor_id and actor_id == str(grant["approved_by"]):
            checks.append(self._check(
                ResearchReasonCode.CAPABILITY_DENIED, ReadinessDimension.AUTHORIZATION, ReadinessStatus.BLOCKED,
                actor_id, {"self_approval": False}, {"approved_by": grant["approved_by"]},
                RemediationCode.OPEN_AUDIT, "An Agent cannot execute from its own approval",
            ))
        budgets = json.loads(grant["budgets_json"] or "{}")
        budget_request = dict(request.get("budget") or {})
        requested = {
            "runs": int(budget_request.get("runs", 1)),
            "download_bytes": int(budget_request.get("download_bytes", 0)),
            "runtime_seconds": int(budget_request.get("runtime_seconds", 0)),
        }
        if counter is None:
            checks.append(self._check(
                ResearchReasonCode.BUDGET_INSUFFICIENT, ReadinessDimension.AUTHORIZATION, ReadinessStatus.BLOCKED,
                grant_id, {"budget_counter": "existing"}, {}, RemediationCode.REQUEST_BUDGET_INCREASE,
                "Grant budget counter is missing",
            ))
        else:
            triples = [
                ("runs", "max_backtest_runs", "reserved_runs", "consumed_runs"),
                ("download_bytes", "max_download_bytes", "reserved_download_bytes", "consumed_download_bytes"),
                ("runtime_seconds", "max_runtime_seconds", "reserved_runtime_seconds", "consumed_runtime_seconds"),
            ]
            for key, maximum_key, reserved_key, consumed_key in triples:
                maximum = int(budgets.get(maximum_key, 0))
                remaining = maximum - int(counter[reserved_key]) - int(counter[consumed_key])
                if requested[key] > remaining:
                    checks.append(self._check(
                        ResearchReasonCode.BUDGET_INSUFFICIENT, ReadinessDimension.AUTHORIZATION, ReadinessStatus.BLOCKED,
                        grant_id, {key: requested[key]}, {"remaining": max(0, remaining), "maximum": maximum},
                        RemediationCode.REQUEST_BUDGET_INCREASE, f"Insufficient {key} budget",
                    ))
        if not any(item.status == ReadinessStatus.BLOCKED for item in checks):
            checks.append(self._check(
                ResearchReasonCode.AUTHORIZATION_VALID, ReadinessDimension.AUTHORIZATION, ReadinessStatus.READY,
                grant_id, {"scope": "contains run", "budget": requested},
                {"grant_version": int(grant["grant_version"]), "policy_version": str(grant["policy_version"])},
                RemediationCode.NONE, "Current Grant, scope, policy, and budget permit Run creation",
            ))
        return {
            "grant_id": grant_id,
            "grant_version": str(grant["grant_version"]),
            "policy_version": str(grant["policy_version"]),
        }, checks

    @staticmethod
    def _row(row: Any) -> dict[str, Any]:
        result = dict(row)
        for key in (
            "definition_closure_json", "data_resolution_closure_json", "execution_closure_json",
            "authorization_closure_json", "readiness_json", "resolver_output_json", "request_json",
        ):
            result[key.removesuffix("_json")] = json.loads(result.pop(key) or "{}")
        return result

    @staticmethod
    def _missing_definition(kind: str) -> ReadinessCheck:
        return ResearchRunPreviewService._check(
            ResearchReasonCode.DEFINITION_CLOSURE_INVALID, ReadinessDimension.DEFINITION, ReadinessStatus.BLOCKED,
            kind, {"definition": f"PINNED {kind}"}, {}, RemediationCode.VALIDATE_AND_PIN,
            f"Run requires at least one pinned {kind} definition",
        )

    @staticmethod
    def _check(
        code: ResearchReasonCode,
        dimension: ReadinessDimension,
        status: ReadinessStatus,
        object_ref: str,
        required: dict[str, Any],
        actual: dict[str, Any],
        remediation: RemediationCode,
        message: str,
    ) -> ReadinessCheck:
        return ReadinessCheck(
            code=code, dimension=dimension, status=status, object_ref=object_ref,
            required=required, actual=actual, remediation_code=remediation, message=message,
        )
