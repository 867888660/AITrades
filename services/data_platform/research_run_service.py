from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from .run_contracts import (
    BundleInputClosure,
    BundleInputMode,
    HistoricalAuthorizationEvidence,
    IdempotencyConflictError,
)
from .run_preview_service import ResearchRunPreviewService
from .process_guard import GuardedProcessError, run_guarded_process
from .store import DataPlatformStore, json_dumps, utc_now
from .workload_scheduler import (
    IntelligentWorkloadRouter,
    ResearchWorkloadPlanner,
    automatic_queue_status,
    worker_log_path,
)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _emit_inspection_safely(**kwargs: Any) -> Any:
    # Lazy import avoids making the Data Platform package depend on the
    # strategy workspace DB during module initialization.
    from services.inspection_service import emit_event_safely

    return emit_event_safely(**kwargs)


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _row_event_time(row: dict[str, Any]) -> datetime:
    value = str(
        row.get("bar_start_time")
        or row.get("event_time")
        or row.get("available_time")
        or ""
    ).strip()
    parsed = _parse_time(value)
    if parsed is None:
        raise ValueError("research input row is missing an event timestamp")
    return parsed


def _rows_in_window(
    rows: list[dict[str, Any]],
    start: datetime | None,
    end: datetime | None,
) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if (start is None or _row_event_time(row) >= start)
        and (end is None or _row_event_time(row) <= end)
    ]


class PreviewStaleError(ValueError):
    code = "PREVIEW_STALE"


class ReadinessBlockedError(ValueError):
    code = "READINESS_NOT_READY"


class ResearchRunService:
    """Atomic Preview -> Frozen Bundle -> QUEUED Run lifecycle."""

    def __init__(self, store: DataPlatformStore):
        self.store = store
        self.previews = ResearchRunPreviewService(store)

    def create(
        self,
        *,
        preview_id: str,
        preview_fingerprint: str,
        idempotency_key: str,
        actor_id: str = "local_user",
        actor_type: str = "HUMAN",
    ) -> dict[str, Any]:
        preview_id = _clean(preview_id)
        expected_fingerprint = _clean(preview_fingerprint)
        idempotency_key = _clean(idempotency_key)
        if not preview_id or not expected_fingerprint or not idempotency_key:
            raise ValueError("preview_id, preview_fingerprint, and idempotency_key are required")

        # Resolve immediately before acquiring the write lock. The transaction
        # below then rechecks every frozen identity, current authorization, and
        # budget from the same database snapshot before it writes anything.
        existing_preview = self.previews.get(preview_id)
        if existing_preview is None:
            raise ValueError("preview not found")
        if existing_preview["preview_fingerprint"] != expected_fingerprint:
            raise PreviewStaleError("PREVIEW_STALE: submitted fingerprint does not match Preview")

        with self.store.connection() as read_conn:
            prior = read_conn.execute(
                "SELECT * FROM research_runs_v2 WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
        if prior:
            if str(prior["preview_fingerprint"]) != expected_fingerprint:
                raise IdempotencyConflictError(
                    idempotency_key, str(prior["preview_fingerprint"]), expected_fingerprint
                )
            return self.get(str(prior["run_id"]))  # type: ignore[return-value]

        refreshed = self.previews.refresh(preview_id)
        if refreshed["preview_fingerprint"] != expected_fingerprint:
            raise PreviewStaleError(
                "PREVIEW_STALE: definitions, data resolution, execution, policy, Grant, or Readiness rules changed"
            )
        preview = refreshed
        readiness = preview["readiness"]
        if readiness.get("overall", {}).get("status") != "READY":
            raise ReadinessBlockedError("READINESS_NOT_READY: all four Readiness dimensions must be READY")

        request = preview["request"]
        budget = dict(request.get("budget") or {})
        requested_budget = {
            "runs": int(budget.get("runs", 1)),
            "download_bytes": int(budget.get("download_bytes", 0)),
            "runtime_seconds": int(budget.get("runtime_seconds", 0)),
        }
        now = utc_now()
        run_id = f"run_{uuid.uuid4().hex}"
        bundle_id = f"bundle_{uuid.uuid4().hex}"
        reservation_id = f"reservation_{uuid.uuid4().hex}"
        event_id = f"event_{uuid.uuid4().hex}"
        artifact_id = f"artifact_{uuid.uuid4().hex}"

        with self.store.transaction(immediate=True) as conn:
            prior = conn.execute(
                "SELECT * FROM research_runs_v2 WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            if prior:
                if str(prior["preview_fingerprint"]) != expected_fingerprint:
                    raise IdempotencyConflictError(
                        idempotency_key, str(prior["preview_fingerprint"]), expected_fingerprint
                    )
                return self._get_in_conn(conn, str(prior["run_id"]))

            stored = conn.execute(
                "SELECT * FROM research_run_previews WHERE preview_id=?", (preview["preview_id"],)
            ).fetchone()
            if stored is None or str(stored["preview_fingerprint"]) != expected_fingerprint:
                raise PreviewStaleError("PREVIEW_STALE: Preview changed before Run transaction")

            definition = json.loads(stored["definition_closure_json"])
            data = json.loads(stored["data_resolution_closure_json"])
            execution = json.loads(stored["execution_closure_json"])
            authorization = json.loads(stored["authorization_closure_json"])
            resolver_output = json.loads(stored["resolver_output_json"])
            self._revalidate_closure_in_conn(conn, stored, definition, data, execution, authorization, resolver_output)
            grant = self._authorize_in_conn(
                conn,
                project_id=str(stored["project_id"]),
                run_type=str(stored["run_type"]),
                grant_id=str(authorization["grant_id"]),
                grant_version=str(authorization["grant_version"]),
                policy_version=str(authorization["policy_version"]),
                requirement_set_id=str(definition["requirement_set_id"]),
                universe_snapshot_id=str(definition["universe_snapshot_id"]),
                actor_id=_clean(actor_id) or "local_user",
                actor_type=_clean(actor_type).upper() or "HUMAN",
            )
            self._reserve_budget_in_conn(
                conn,
                reservation_id=reservation_id,
                grant=grant,
                idempotency_key=f"research-run:{idempotency_key}",
                requested=requested_budget,
                now=now,
            )
            historical_auth = HistoricalAuthorizationEvidence(
                grant_id=str(grant["grant_id"]),
                grant_version=str(grant["grant_version"]),
                scope_snapshot=json.loads(grant["scope_json"] or "{}"),
                policy_version=str(grant["policy_version"]),
                authorization_check_result={
                    "status": "READY",
                    "checked_at": now,
                    "actor_id": _clean(actor_id) or "local_user",
                    "actor_type": _clean(actor_type).upper() or "HUMAN",
                    "budget": requested_budget,
                },
            )
            input_mode = BundleInputMode.PRECOMPUTED_ARTIFACTS if (
                request.get("input_factor_artifact_ids") or request.get("input_alpha_artifact_ids")
            ) else BundleInputMode.DEFINITIONS
            input_closure = BundleInputClosure(
                run_type=str(stored["run_type"]),
                input_mode=input_mode,
                exact_manifest_ids=tuple(data["resolved_manifest_ids"]),
                universe_snapshot_id=str(definition["universe_snapshot_id"]),
                requirement_set_id=str(definition["requirement_set_id"]),
                universe_id=str(definition.get("universe_id") or ""),
                universe_revision_id=str(definition.get("universe_revision_id") or ""),
                universe_resolution_id=str(definition.get("universe_resolution_id") or ""),
                resolved_instrument_tuples=tuple(
                    tuple(str(value) for value in row)
                    for row in definition.get("resolved_instrument_tuples") or ()
                ),
                resolved_instrument_weights=dict(
                    definition.get("resolved_instrument_weights") or {}
                ),
                universe_resolution_metadata=dict(
                    definition.get("universe_resolution_metadata") or {}
                ),
                factor_definitions=tuple(definition["factor_definitions"]),
                factor_pack_definitions=tuple(definition.get("factor_pack_definitions") or ()),
                alpha_definitions=tuple(definition["alpha_definitions"]),
                input_factor_artifact_ids=tuple(request.get("input_factor_artifact_ids") or ()),
                input_alpha_artifact_ids=tuple(request.get("input_alpha_artifact_ids") or ()),
                evaluation_spec_hash=str(execution["evaluation_spec_hash"]),
                portfolio_spec_hash=str(execution["portfolio_spec_hash"]),
                execution_spec_hash=str(execution["execution_spec_hash"]),
                engine_version=str(execution["engine_version"]),
                code_hash=str(execution["code_hash"]),
                resolver_version=str(data["resolver_version"]),
                source_selection_policy_version=str(data["source_selection_policy_version"]),
                readiness_rule_version=str(execution["readiness_rule_version"]),
            )
            manifest_descriptors = [
                {
                    "manifest_id": item["manifest_id"],
                    "manifest_hash": item["manifest_hash"],
                    "dataset_id": item["dataset_id"],
                    "schema_version": item["schema_version"],
                }
                for item in resolver_output.get("bindings", [])
            ]
            # Preserve one descriptor per exact Manifest even if several
            # Requirement bindings share it.
            manifest_descriptors = list({item["manifest_id"]: item for item in manifest_descriptors}.values())
            manifest_descriptors.sort(key=lambda item: item["manifest_id"])
            bundle_payload = {
                "schema_version": "research_run_input_bundle.v2",
                "bundle_id": bundle_id,
                "run_id": run_id,
                "project_id": str(stored["project_id"]),
                "source_preview_id": str(stored["preview_id"]),
                "source_preview_fingerprint": expected_fingerprint,
                "input_closure": input_closure.to_dict(),
                "execution_specs": {
                    "evaluation_spec": request.get("evaluation_spec") or {},
                    "portfolio_spec": request.get("portfolio_spec") or {},
                    "execution_spec": request.get("execution_spec") or {},
                    "benchmark_spec": request.get("benchmark_spec") or {},
                },
                "research_semantics": dict(request.get("research_semantics") or {}),
                "manifest_descriptors": manifest_descriptors,
                "historical_authorization": historical_auth.to_dict(),
                "budget_reservation_id": reservation_id,
                "created_at": now,
            }
            bundle_hash = hashlib.sha256(json_dumps(bundle_payload).encode("utf-8")).hexdigest()
            conn.execute(
                """
                INSERT INTO frozen_research_bundles(
                    bundle_id, run_id, project_id, source_preview_id,
                    source_preview_fingerprint, lifecycle_status, integrity_status,
                    reuse_status, reuse_reason_code, canonical_payload_json,
                    bundle_hash, historical_authorization_json, created_at
                ) VALUES (?, ?, ?, ?, ?, 'FROZEN', 'VERIFIED', 'ALLOWED', '', ?, ?, ?, ?)
                """,
                (
                    bundle_id, run_id, str(stored["project_id"]), str(stored["preview_id"]),
                    expected_fingerprint, json_dumps(bundle_payload), bundle_hash,
                    json_dumps(historical_auth.to_dict()), now,
                ),
            )
            self._create_bundle_artifact_in_conn(
                conn, artifact_id=artifact_id, bundle_id=bundle_id, project_id=str(stored["project_id"]),
                run_id=run_id, bundle_hash=bundle_hash, bundle_payload=bundle_payload, now=now,
            )
            conn.execute(
                """
                INSERT INTO research_runs_v2(
                    run_id, project_id, run_type, status, idempotency_key,
                    preview_id, preview_fingerprint, bundle_id, reservation_id,
                    actor_id, actor_type, priority, max_attempts, input_json,
                    created_at, queued_at
                ) VALUES (?, ?, ?, 'QUEUED', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id, str(stored["project_id"]), str(stored["run_type"]), idempotency_key,
                    str(stored["preview_id"]), expected_fingerprint, bundle_id, reservation_id,
                    _clean(actor_id) or "local_user", _clean(actor_type).upper() or "HUMAN",
                    int(request.get("priority", 50)), max(1, int(request.get("max_attempts", 3))),
                    json_dumps({"bundle_id": bundle_id, "bundle_hash": bundle_hash}), now, now,
                ),
            )
            conn.execute(
                "INSERT INTO research_run_outbox(event_id, run_id, event_type, payload_json, status, created_at) VALUES (?, ?, 'RESEARCH_RUN_QUEUED', ?, 'PENDING', ?)",
                (event_id, run_id, json_dumps({"run_id": run_id, "bundle_id": bundle_id}), now),
            )
        self.apply_automatic_routing(run_id)
        return self.get(run_id)  # type: ignore[return-value]

    def apply_automatic_routing(self, run_id: str) -> dict[str, Any]:
        """Estimate and persist queue priority; keep resource details internal."""

        run = self.get(_clean(run_id))
        if run is None:
            raise ValueError("research run not found")
        plan = ResearchWorkloadPlanner(self.store).plan(run)
        decision = IntelligentWorkloadRouter().route_research(plan)
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                "UPDATE research_runs_v2 SET priority=? WHERE run_id=? AND status='QUEUED'",
                (decision.priority, _clean(run_id)),
            )
        return {"plan": plan.to_dict(), "decision": decision.to_dict()}

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self.store.connection() as conn:
            return self._get_in_conn(conn, _clean(run_id))

    def list(self, *, project_id: str = "", run_type: str = "", status: str = "", limit: int = 200) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        for field, value in (("project_id", project_id), ("run_type", run_type), ("status", status)):
            if _clean(value):
                clauses.append(f"{field}=?")
                params.append(_clean(value).upper() if field != "project_id" else _clean(value))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(int(limit), 1000)))
        with self.store.connection() as conn:
            rows = conn.execute(
                f"SELECT run_id FROM research_runs_v2{where} ORDER BY created_at DESC LIMIT ?", params
            ).fetchall()
        return [self.get(str(row[0])) for row in rows]  # type: ignore[list-item]

    def get_bundle(self, bundle_id: str, *, check_current_authorization: bool = False) -> dict[str, Any] | None:
        with self.store.connection() as conn:
            row = conn.execute("SELECT * FROM frozen_research_bundles WHERE bundle_id=?", (_clean(bundle_id),)).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["canonical_payload"] = json.loads(result.pop("canonical_payload_json"))
            result["historical_authorization"] = json.loads(result.pop("historical_authorization_json"))
            if check_current_authorization:
                auth = result["historical_authorization"]
                grant = conn.execute("SELECT * FROM approval_grants WHERE grant_id=?", (auth["grant_id"],)).fetchone()
                closure = result["canonical_payload"]["input_closure"]
                scope = json.loads(grant["scope_json"] or "{}") if grant else {}
                allowed_types = {str(item).upper() for item in scope.get("allowed_run_types", [])}
                reasons: list[str] = []
                if not grant or str(grant["status"]) != "ACTIVE":
                    reasons.append("GRANT_REVOKED")
                elif grant["expires_at"] and _parse_time(grant["expires_at"]) <= datetime.now(timezone.utc):
                    reasons.append("GRANT_EXPIRED")
                if grant and str(grant["policy_version"]) != str(auth["policy_version"]):
                    reasons.append("POLICY_VERSION_MISMATCH")
                if allowed_types and closure["run_type"] not in allowed_types:
                    reasons.append("GRANT_SCOPE_VIOLATION")
                if _clean(scope.get("requirement_set_id")) and _clean(scope.get("requirement_set_id")) != closure["requirement_set_id"]:
                    reasons.append("GRANT_SCOPE_VIOLATION")
                if _clean(scope.get("universe_snapshot_id")) and _clean(scope.get("universe_snapshot_id")) != closure["universe_snapshot_id"]:
                    reasons.append("GRANT_SCOPE_VIOLATION")
                allowed = not reasons
                result["current_reuse_authorization"] = {
                    "status": "ALLOWED" if allowed else "PROHIBITED",
                    "checked_at": utc_now(),
                    "historical_authorization_inherited": False,
                    "reason_codes": sorted(set(reasons)),
                }
            return result

    def verify_bundle(self, bundle_id: str) -> dict[str, Any]:
        from .data_client import FrozenManifestData

        bundle = self.get_bundle(bundle_id)
        if bundle is None:
            raise ValueError("Frozen Bundle not found")
        payload = bundle["canonical_payload"]
        actual = hashlib.sha256(json_dumps(payload).encode("utf-8")).hexdigest()
        try:
            if actual != bundle["bundle_hash"]:
                raise ValueError("Frozen Bundle hash mismatch")
            for manifest_id in payload["input_closure"]["exact_manifest_ids"]:
                FrozenManifestData(self.store, manifest_id).verify()
        except Exception as exc:
            with self.store.transaction(immediate=True) as conn:
                conn.execute(
                    "UPDATE frozen_research_bundles SET integrity_status='DAMAGED', reuse_status='PROHIBITED', reuse_reason_code='MANIFEST_DAMAGED' WHERE bundle_id=?",
                    (_clean(bundle_id),),
                )
            raise ValueError(f"Frozen Bundle verification failed: {exc}") from exc
        return {"bundle_id": bundle_id, "bundle_hash": actual, "integrity_status": "VERIFIED"}

    @staticmethod
    def _revalidate_closure_in_conn(
        conn: Any, preview: Any, definition: dict[str, Any], data: dict[str, Any],
        execution: dict[str, Any], authorization: dict[str, Any], resolver_output: dict[str, Any],
    ) -> None:
        project = conn.execute("SELECT revision FROM research_projects WHERE project_id=?", (preview["project_id"],)).fetchone()
        if not project or int(project[0]) != int(definition["project_version"]):
            raise PreviewStaleError("PREVIEW_STALE: project revision changed")
        req = conn.execute(
            "SELECT superseded_by_id FROM requirement_sets WHERE requirement_set_id=? AND project_id=?",
            (definition["requirement_set_id"], preview["project_id"]),
        ).fetchone()
        if not req or req[0]:
            raise PreviewStaleError("PREVIEW_STALE: RequirementSet changed or was superseded")
        snapshot = conn.execute(
            "SELECT universe_definition_id FROM universe_snapshots WHERE universe_snapshot_id=?",
            (definition["universe_snapshot_id"],),
        ).fetchone()
        if not snapshot or str(snapshot[0]) != str(definition["universe_definition_id"]):
            raise PreviewStaleError("PREVIEW_STALE: Universe Snapshot changed")
        current_refs = conn.execute(
            """
            SELECT r.definition_type, r.definition_id, r.definition_version, r.reference_mode,
                   d.state, d.spec_hash
            FROM project_definition_refs r JOIN research_definitions d ON d.definition_id=r.definition_id
            WHERE r.project_id=? ORDER BY r.definition_type, r.definition_id, r.definition_version
            """,
            (preview["project_id"],),
        ).fetchall()
        expected_refs = sorted(
            [
                ("FACTOR", item["factor_definition_id"], item["version"], "PINNED", "VALIDATED", item["spec_hash"])
                for item in definition["factor_definitions"]
            ] + [
                ("ALPHA", item["alpha_definition_id"], item["version"], "PINNED", "VALIDATED", item["spec_hash"])
                for item in definition["alpha_definitions"]
            ]
        )
        actual_refs = sorted(tuple(str(row[index]) for index in range(6)) for row in current_refs)
        if actual_refs != expected_refs:
            raise PreviewStaleError("PREVIEW_STALE: project definition references changed")
        descriptor_by_id = {item["manifest_id"]: item for item in resolver_output.get("bindings", [])}
        for manifest_id in data["resolved_manifest_ids"]:
            row = conn.execute(
                "SELECT status, manifest_hash FROM dataset_manifests WHERE manifest_id=?", (manifest_id,)
            ).fetchone()
            expected = descriptor_by_id.get(manifest_id, {})
            if not row or str(row[0]) != "READY" or str(row[1]) != str(expected.get("manifest_hash")):
                raise PreviewStaleError(f"PREVIEW_STALE: Manifest identity changed: {manifest_id}")
        if not execution.get("engine_version") or not execution.get("code_hash"):
            raise PreviewStaleError("PREVIEW_STALE: execution identity is incomplete")
        if not authorization.get("grant_id"):
            raise PreviewStaleError("PREVIEW_STALE: authorization identity is incomplete")

    @staticmethod
    def _authorize_in_conn(
        conn: Any, *, project_id: str, run_type: str, grant_id: str,
        grant_version: str, policy_version: str, requirement_set_id: str,
        universe_snapshot_id: str, actor_id: str, actor_type: str,
    ) -> Any:
        grant = conn.execute("SELECT * FROM approval_grants WHERE grant_id=?", (grant_id,)).fetchone()
        if not grant or str(grant["project_id"]) != project_id:
            raise PermissionError("GRANT_SCOPE_VIOLATION: Grant does not authorize this project")
        if str(grant["status"]) != "ACTIVE":
            raise PermissionError("GRANT_REVOKED: current authorization is not active")
        if str(grant["grant_version"]) != grant_version or str(grant["policy_version"]) != policy_version:
            raise PermissionError("POLICY_VERSION_MISMATCH: Grant or Policy version changed")
        if grant["expires_at"] and _parse_time(grant["expires_at"]) <= datetime.now(timezone.utc):
            raise PermissionError("GRANT_EXPIRED: current authorization expired")
        scope = json.loads(grant["scope_json"] or "{}")
        allowed = {str(item).upper() for item in scope.get("allowed_run_types", [])}
        if allowed and run_type not in allowed:
            raise PermissionError("GRANT_SCOPE_VIOLATION: Run type is outside Grant scope")
        if _clean(scope.get("requirement_set_id")) and _clean(scope.get("requirement_set_id")) != requirement_set_id:
            raise PermissionError("GRANT_SCOPE_VIOLATION: RequirementSet is outside Grant scope")
        if _clean(scope.get("universe_snapshot_id")) and _clean(scope.get("universe_snapshot_id")) != universe_snapshot_id:
            raise PermissionError("GRANT_SCOPE_VIOLATION: Universe Snapshot is outside Grant scope")
        if actor_type == "AGENT" and actor_id == str(grant["approved_by"]):
            raise PermissionError("CAPABILITY_DENIED: Agent self-approval is forbidden")
        return grant

    @staticmethod
    def _reserve_budget_in_conn(
        conn: Any, *, reservation_id: str, grant: Any, idempotency_key: str,
        requested: dict[str, int], now: str,
    ) -> None:
        counter = conn.execute(
            "SELECT * FROM approval_budget_counters WHERE grant_id=?", (grant["grant_id"],)
        ).fetchone()
        if not counter:
            raise ValueError("BUDGET_RESERVATION_FAILED: Grant budget counter missing")
        budgets = json.loads(grant["budgets_json"] or "{}")
        triples = [
            ("runs", "max_backtest_runs", "reserved_runs", "consumed_runs"),
            ("download_bytes", "max_download_bytes", "reserved_download_bytes", "consumed_download_bytes"),
            ("runtime_seconds", "max_runtime_seconds", "reserved_runtime_seconds", "consumed_runtime_seconds"),
        ]
        for key, max_key, reserved_key, consumed_key in triples:
            amount = max(0, int(requested[key]))
            remaining = int(budgets.get(max_key, 0)) - int(counter[reserved_key]) - int(counter[consumed_key])
            if amount > remaining:
                raise ValueError(f"BUDGET_RESERVATION_FAILED: insufficient {key} budget")
        conn.execute(
            """
            INSERT INTO approval_budget_reservations(
                reservation_id, grant_id, idempotency_key, runs, download_bytes,
                runtime_seconds, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'RESERVED', ?)
            """,
            (
                reservation_id, grant["grant_id"], idempotency_key, requested["runs"],
                requested["download_bytes"], requested["runtime_seconds"], now,
            ),
        )
        conn.execute(
            """
            UPDATE approval_budget_counters SET
                reserved_runs=reserved_runs+?,
                reserved_download_bytes=reserved_download_bytes+?,
                reserved_runtime_seconds=reserved_runtime_seconds+?, updated_at=?
            WHERE grant_id=?
            """,
            (
                requested["runs"], requested["download_bytes"], requested["runtime_seconds"],
                now, grant["grant_id"],
            ),
        )

    @staticmethod
    def _create_bundle_artifact_in_conn(
        conn: Any, *, artifact_id: str, bundle_id: str, project_id: str, run_id: str,
        bundle_hash: str, bundle_payload: dict[str, Any], now: str,
    ) -> None:
        logical_name = f"run-inputs-{run_id}"
        version = int(conn.execute(
            "SELECT COALESCE(MAX(artifact_version),0)+1 FROM research_artifacts WHERE artifact_type='RESEARCH_INPUT_BUNDLE' AND logical_name=?",
            (logical_name,),
        ).fetchone()[0])
        conn.execute(
            """
            INSERT INTO research_artifacts(
                artifact_id, project_id, artifact_type, logical_name, artifact_version,
                status, content_uri, content_hash, schema_version, created_by_run_id,
                created_by_task_id, spec_hash, engine_version, code_hash,
                metadata_json, created_at
            ) VALUES (?, ?, 'RESEARCH_INPUT_BUNDLE', ?, ?, 'READY', ?, ?,
                      'research_run_input_bundle.v2', ?, '', ?, ?, ?, ?, ?)
            """,
            (
                artifact_id, project_id, logical_name, version, f"db://frozen-research-bundles/{bundle_id}",
                bundle_hash, run_id, bundle_hash,
                bundle_payload["input_closure"]["engine_version"],
                bundle_payload["input_closure"]["code_hash"], json_dumps(bundle_payload), now,
            ),
        )
        dependencies = [
            (item, "DATASET_MANIFEST", "INPUT_DATA")
            for item in bundle_payload["input_closure"]["exact_manifest_ids"]
        ]
        dependencies.extend([
            (bundle_payload["input_closure"]["universe_snapshot_id"], "UNIVERSE_SNAPSHOT", "INPUT_UNIVERSE"),
            (bundle_payload["input_closure"]["requirement_set_id"], "REQUIREMENT_SET", "INPUT_REQUIREMENTS"),
        ])
        for parent_id, parent_type, dependency_type in dependencies:
            conn.execute(
                "INSERT INTO artifact_dependencies(child_artifact_id,parent_id,parent_type,dependency_type) VALUES (?,?,?,?)",
                (artifact_id, parent_id, parent_type, dependency_type),
            )
        conn.execute(
            "INSERT INTO artifact_pins(artifact_id,pin_owner_type,pin_owner_id,reason,created_at) VALUES (?, 'RESEARCH_RUN', ?, 'immutable Run input', ?)",
            (artifact_id, run_id, now),
        )

    @staticmethod
    def _get_in_conn(conn: Any, run_id: str) -> dict[str, Any] | None:
        row = conn.execute("SELECT * FROM research_runs_v2 WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        for key in ("input_json", "output_json", "error_json"):
            result[key.removesuffix("_json")] = json.loads(result.pop(key) or "{}")
        if _clean(result.get("status")).upper() == "SUCCEEDED":
            # A successful retry supersedes diagnostics from earlier attempts.
            result["error"] = {}
        bundle = conn.execute(
            "SELECT lifecycle_status, integrity_status, reuse_status, reuse_reason_code, bundle_hash FROM frozen_research_bundles WHERE bundle_id=?",
            (result["bundle_id"],),
        ).fetchone()
        result["bundle"] = dict(bundle) if bundle else None
        status = _clean(result.get("status")).upper()
        if status == "QUEUED":
            total = int(
                conn.execute(
                    "SELECT COUNT(*) FROM research_runs_v2 WHERE status='QUEUED'"
                ).fetchone()[0]
                or 0
            )
            position = int(
                conn.execute(
                    """SELECT COUNT(*) FROM research_runs_v2 WHERE status='QUEUED'
                       AND (priority > ? OR (priority = ? AND queued_at < ?)
                            OR (priority = ? AND queued_at = ? AND run_id <= ?))""",
                    (
                        int(result.get("priority") or 0),
                        int(result.get("priority") or 0),
                        result.get("queued_at"),
                        int(result.get("priority") or 0),
                        result.get("queued_at"),
                        run_id,
                    ),
                ).fetchone()[0]
                or 0
            )
            queue_state = "WAITING"
        else:
            total = 0
            position = 0
            queue_state = "RUNNING" if status == "RUNNING" else "TERMINAL"
        result["queue"] = automatic_queue_status(
            state=queue_state,
            position=max(1, position) if status == "QUEUED" else 0,
            total=total,
            queued_at=result.get("queued_at"),
        )
        result["progress"] = {
            "phase": status,
            "percent": 50 if status == "RUNNING" else (5 if status == "QUEUED" else 100),
            "message": (
                "研究任务正在受控环境中执行。"
                if status == "RUNNING"
                else "研究任务已进入自动调度队列。"
                if status == "QUEUED"
                else "研究任务已结束。"
            ),
            "heartbeat_at": result.get("heartbeat_at") or result.get("queued_at"),
            "attempt": int(result.get("attempt_count") or 0),
            "action_required": False,
            "next_update_seconds": 5 if status in {"QUEUED", "RUNNING"} else 0,
        }
        return result


class ResearchRunWorker:
    """Lease only committed QUEUED Runs and consume only their Frozen Bundle."""

    def __init__(self, store: DataPlatformStore, worker_id: str = "formal-research-worker"):
        self.store = store
        self.worker_id = _clean(worker_id) or "formal-research-worker"

    def claim(
        self,
        *,
        lease_seconds: int = 300,
        project_id: str = "",
        run_id: str = "",
    ) -> dict[str, Any] | None:
        now_dt = datetime.now(timezone.utc)
        now = now_dt.replace(microsecond=0).isoformat()
        expires = (now_dt + timedelta(seconds=max(30, int(lease_seconds)))).replace(microsecond=0).isoformat()
        with self.store.transaction(immediate=True) as conn:
            project_id = _clean(project_id)
            run_id = _clean(run_id)
            if run_id:
                row = conn.execute(
                    """
                    SELECT run_id FROM research_runs_v2
                    WHERE run_id=? AND (status='QUEUED' OR (status='RUNNING' AND lease_expires_at < ?))
                    """,
                    (run_id, now),
                ).fetchone()
            elif project_id:
                row = conn.execute(
                    """
                    SELECT run_id FROM research_runs_v2
                    WHERE project_id=? AND (status='QUEUED' OR (status='RUNNING' AND lease_expires_at < ?))
                    ORDER BY priority DESC, queued_at LIMIT 1
                    """,
                    (project_id, now),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT run_id FROM research_runs_v2
                    WHERE status='QUEUED' OR (status='RUNNING' AND lease_expires_at < ?)
                    ORDER BY priority DESC, queued_at LIMIT 1
                    """,
                    (now,),
                ).fetchone()
            if row is None:
                return None
            run_id = str(row[0])
            conn.execute(
                """
                UPDATE research_runs_v2 SET status='RUNNING', lease_owner=?, lease_expires_at=?,
                    heartbeat_at=?, started_at=COALESCE(started_at,?), attempt_count=attempt_count+1
                WHERE run_id=?
                """,
                (self.worker_id, expires, now, now, run_id),
            )
            run = ResearchRunService._get_in_conn(conn, run_id)
            bundle = conn.execute(
                "SELECT * FROM frozen_research_bundles WHERE bundle_id=?", (run["bundle_id"],)
            ).fetchone()
            if not bundle or str(bundle["integrity_status"]) == "DAMAGED":
                raise ValueError("Worker cannot consume a missing or DAMAGED Frozen Bundle")
            run["frozen_input"] = json.loads(bundle["canonical_payload_json"])
            return run

    def run_once(
        self,
        *,
        lease_seconds: int = 300,
        project_id: str = "",
        run_id: str = "",
        isolate_execution: bool = True,
    ) -> dict[str, Any] | None:
        run = self.claim(
            lease_seconds=lease_seconds,
            project_id=project_id,
            run_id=run_id,
        )
        if run is None:
            return None
        attempt = max(1, int(run.get("attempt_count") or 1))
        trace_id = f"trace_research_{run['run_id']}_attempt_{attempt}"
        claim_event_id = f"evt_research_{run['run_id']}_attempt_{attempt}_claimed"
        common = {
            "trace_id": trace_id,
            "subject_type": "research_run",
            "subject_id": str(run["run_id"]),
            "project_id": str(run.get("project_id") or ""),
            "trace_title": f"{run.get('run_type') or 'Research Run'} execution",
            "actor_type": "system",
            "actor_id": self.worker_id,
            "target_type": "research_run",
            "target_id": str(run["run_id"]),
        }
        _emit_inspection_safely(
            **common,
            trace_status="running",
            event_id=claim_event_id,
            event_kind="state_change",
            title="Research Run claimed",
            status="succeeded",
            operation="research.run.claim",
            output_data={
                "status": "RUNNING",
                "attempt": attempt,
                "lease_owner": self.worker_id,
            },
            refs=[
                {"ref_type": "research_run", "ref_id": str(run["run_id"]), "ref_role": "target"},
                {"ref_type": "input_bundle", "ref_id": str(run.get("bundle_id") or ""), "ref_role": "input"},
            ],
            idempotency_key=f"research-run:{run['run_id']}:attempt:{attempt}:claimed",
        )
        heartbeat_stop = threading.Event()

        def maintain_heartbeat() -> None:
            interval = max(
                5,
                int(os.environ.get("DATATUBE_RESEARCH_HEARTBEAT_SECONDS", "15")),
            )
            while not heartbeat_stop.wait(interval):
                try:
                    self._extend_lease(run["run_id"], max(lease_seconds, interval * 4))
                except Exception:
                    # Execution remains isolated and bounded.  The final state
                    # transition will surface a lost lease instead of killing
                    # the heartbeat thread or the Web process.
                    return

        heartbeat_thread = threading.Thread(
            target=maintain_heartbeat,
            name=f"research-heartbeat-{str(run['run_id'])[-8:]}",
            daemon=True,
        )
        heartbeat_thread.start()
        try:
            workload_plan = (
                ResearchWorkloadPlanner(self.store).plan(run)
                if isinstance(self.store, DataPlatformStore)
                else None
            )
            route = (
                IntelligentWorkloadRouter().route_research(workload_plan)
                if workload_plan is not None
                else None
            )
            if route is not None:
                _emit_inspection_safely(
                    **common,
                    trace_status="running",
                    event_id=f"evt_research_{run['run_id']}_attempt_{attempt}_routed",
                    parent_event_id=claim_event_id,
                    dependency_event_ids=[claim_event_id],
                    event_kind="agent_step",
                    title="Research Run automatically routed",
                    status="succeeded",
                    operation="research.run.route",
                    input_data={"run_type": run.get("run_type")},
                    output_data={
                        "workload_plan": workload_plan.to_dict(),
                        "routing_decision": route.to_dict(),
                    },
                    idempotency_key=f"research-run:{run['run_id']}:attempt:{attempt}:routed",
                )
            if workload_plan is not None and workload_plan.hard_limit_exceeded:
                # 切换到分区执行模式
                _emit_inspection_safely(
                    **common,
                    trace_status="running",
                    event_id=f"evt_research_{run['run_id']}_attempt_{attempt}_partition_mode",
                    parent_event_id=claim_event_id,
                    dependency_event_ids=[claim_event_id],
                    event_kind="agent_step",
                    title="Switching to partitioned execution",
                    status="succeeded",
                    operation="research.run.partition_mode",
                    output_data={
                        "estimated_mb": workload_plan.estimated_working_set_mb,
                        "worker_limit_mb": workload_plan.worker_memory_mb,
                        "reason": "Estimated memory exceeds worker capacity",
                    },
                    idempotency_key=f"research-run:{run['run_id']}:attempt:{attempt}:partition_mode",
                )
                # 执行分区研究
                from .partition_planner import ResearchPartitionPlanner
                from .checkpoint_manager import CheckpointManager
                from .partition_executor import PartitionedResearchExecutor

                output = self._execute_partitioned(
                    run=run,
                    timeout_seconds=self._runtime_timeout_seconds(run) * 3,  # 分区执行更慢
                )
            elif isolate_execution and isinstance(self.store, DataPlatformStore):
                timeout_seconds = self._runtime_timeout_seconds(run)
                self._extend_lease(run["run_id"], timeout_seconds + 60)
                output = self._execute_isolated(
                    run,
                    timeout_seconds=timeout_seconds,
                    memory_limit_mb=route.worker_memory_mb if route is not None else None,
                )
            else:
                output = FormalResearchRunExecutor(self.store).execute(run)
            completed = self.complete(run["run_id"], output)
            artifact_refs = []
            for key, values in output.items():
                if not key.startswith("produced_") or not key.endswith("_artifact_ids"):
                    continue
                artifact_type = key.removeprefix("produced_").removesuffix("_artifact_ids")
                artifact_refs.extend(
                    {"ref_type": artifact_type, "ref_id": str(artifact_id), "ref_role": "output"}
                    for artifact_id in (values or [])
                    if str(artifact_id or "")
                )
            _emit_inspection_safely(
                **common,
                trace_status="succeeded",
                event_id=f"evt_research_{run['run_id']}_attempt_{attempt}_completed",
                parent_event_id=claim_event_id,
                dependency_event_ids=[claim_event_id],
                event_kind="agent_step",
                title=f"{run.get('run_type') or 'Research Run'} completed",
                status="succeeded",
                operation="research.run.execute",
                input_data={
                    "run_type": run.get("run_type"),
                    "bundle_id": run.get("bundle_id"),
                },
                output_data={
                    "status": "SUCCEEDED",
                    "product_run_type": output.get("product_run_type"),
                    "artifact_counts": {
                        key: len(values or [])
                        for key, values in output.items()
                        if key.startswith("produced_") and key.endswith("_artifact_ids")
                    },
                    "metric_groups": sorted((output.get("metrics") or {}).keys()),
                },
                refs=artifact_refs,
                idempotency_key=f"research-run:{run['run_id']}:attempt:{attempt}:completed",
            )
            return completed
        except Exception as exc:
            error = {
                "code": (
                    "FORMAL_RESEARCH_EXECUTION_TIMEOUT"
                    if isinstance(exc, subprocess.TimeoutExpired)
                    or (
                        isinstance(exc, GuardedProcessError)
                        and exc.code == "PROCESS_TIMEOUT"
                    )
                    else "FORMAL_RESEARCH_RESOURCE_LIMIT"
                    if (
                        isinstance(exc, MemoryError)
                        or (
                            isinstance(exc, GuardedProcessError)
                            and exc.code in {"PROCESS_RESOURCE_LIMIT", "PROCESS_LOG_LIMIT"}
                        )
                    )
                    else "FORMAL_RESEARCH_EXECUTION_FAILED"
                ),
                "message": str(exc),
                "exception_type": type(exc).__name__,
            }
            error["retryable"] = error["code"] not in {
                "FORMAL_RESEARCH_EXECUTION_TIMEOUT",
                "FORMAL_RESEARCH_RESOURCE_LIMIT",
            }
            _emit_inspection_safely(
                **common,
                trace_status="failed",
                event_id=f"evt_research_{run['run_id']}_attempt_{attempt}_failed",
                parent_event_id=claim_event_id,
                dependency_event_ids=[claim_event_id],
                event_kind="agent_step",
                title=f"{run.get('run_type') or 'Research Run'} failed",
                status="failed",
                severity="error",
                operation="research.run.execute",
                input_data={"run_type": run.get("run_type"), "bundle_id": run.get("bundle_id")},
                error_data=error,
                idempotency_key=f"research-run:{run['run_id']}:attempt:{attempt}:failed",
            )
            return self.fail(run["run_id"], error)
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=2.0)

    def _runtime_timeout_seconds(self, run: Mapping[str, Any]) -> int:
        with self.store.connection() as conn:
            row = conn.execute(
                """
                SELECT r.runtime_seconds AS reserved_runtime_seconds,
                       g.budgets_json,
                       c.reserved_runtime_seconds AS total_reserved_runtime_seconds,
                       c.consumed_runtime_seconds
                FROM approval_budget_reservations r
                JOIN approval_grants g ON g.grant_id=r.grant_id
                LEFT JOIN approval_budget_counters c ON c.grant_id=r.grant_id
                WHERE r.reservation_id=?
                """,
                (_clean(run.get("reservation_id")),),
            ).fetchone()
        if row is None:
            return 300
        budgets = json.loads(row["budgets_json"] or "{}")
        maximum = max(
            1,
            int(
                budgets.get("max_runtime_seconds")
                or row["reserved_runtime_seconds"]
                or 300
            ),
        )
        own_reserved = max(0, int(row["reserved_runtime_seconds"] or 0))
        other_reserved = max(
            0, int(row["total_reserved_runtime_seconds"] or 0) - own_reserved
        )
        consumed = max(0, int(row["consumed_runtime_seconds"] or 0))
        return max(1, maximum - consumed - other_reserved)

    def _extend_lease(self, run_id: str, lease_seconds: int) -> None:
        now_dt = datetime.now(timezone.utc)
        now = now_dt.replace(microsecond=0).isoformat()
        expires = (now_dt + timedelta(seconds=max(30, int(lease_seconds)))).replace(
            microsecond=0
        ).isoformat()
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                """
                UPDATE research_runs_v2 SET lease_expires_at=?, heartbeat_at=?
                WHERE run_id=? AND status='RUNNING' AND lease_owner=?
                """,
                (expires, now, _clean(run_id), self.worker_id),
            )

    def _execute_partitioned(
        self,
        run: Mapping[str, Any],
        *,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        """
        分区执行研究任务

        流程：
        1. 生成分区计划
        2. 逐个执行分区（带恢复机制）
        3. 聚合所有分区结果
        4. 返回最终输出

        Args:
            run: Research Run 记录
            timeout_seconds: 总超时时间

        Returns:
            研究结果字典
        """
        from pathlib import Path
        from .partition_planner import ResearchPartitionPlanner
        from .checkpoint_manager import CheckpointManager
        from .partition_executor import PartitionedResearchExecutor

        frozen_input = run.get("frozen_input", {})
        bundle_id = run.get("bundle_id", "")

        # 获取 bundle_hash
        with self.store.connection() as conn:
            bundle_row = conn.execute(
                "SELECT bundle_hash FROM frozen_research_bundles WHERE bundle_id=?",
                (bundle_id,),
            ).fetchone()
            if not bundle_row:
                raise ValueError(f"Bundle {bundle_id} not found")
            bundle_hash = bundle_row["bundle_hash"]

        # 确保 frozen_input 包含 bundle_hash（用于 checkpoint 路径）
        frozen_input["_bundle_hash"] = bundle_hash

        # 初始化组件
        checkpoint_root = Path(self.store.db_path).parent / "research_checkpoints"
        checkpoint_manager = CheckpointManager(self.store, checkpoint_root)
        executor = PartitionedResearchExecutor(self.store, checkpoint_manager)
        planner = ResearchPartitionPlanner(checkpoint_root)

        # 生成分区策略
        strategy = planner.plan(frozen_input, bundle_hash)

        if strategy.execution_mode != "PARTITIONED":
            # 不需要分区，回退到普通执行
            return FormalResearchRunExecutor(self.store).execute(run)

        # 执行分区研究
        partitions = strategy.partitions
        completed_checkpoints = []

        # 获取 manifest_id
        manifest_id = frozen_input.get("manifest_id", "")
        if not manifest_id:
            raise ValueError("分区执行需要 manifest_id")

        for i, partition in enumerate(partitions):
            # 更新进度
            progress_percent = int((i / len(partitions)) * 100)
            self._update_partition_progress(
                run_id=run["run_id"],
                partition_id=partition.partition_id,
                partition_index=i + 1,
                total_partitions=len(partitions),
                progress_percent=progress_percent,
            )

            # 检查是否已有 Checkpoint（恢复场景）
            existing_checkpoint = checkpoint_manager.load(
                partition_id=partition.partition_id,
                bundle_hash=bundle_hash,
            )

            if existing_checkpoint:
                _emit_inspection_safely(
                    subject_type="research_run",
                    subject_id=str(run["run_id"]),
                    event_kind="agent_step",
                    title=f"Reusing checkpoint for {partition.partition_id}",
                    status="succeeded",
                    operation="research.partition.checkpoint_reused",
                    output_data={
                        "partition_id": partition.partition_id,
                        "row_count": existing_checkpoint.row_count,
                    },
                )
                completed_checkpoints.append(existing_checkpoint)
                continue

            # 执行分区
            try:
                checkpoint = executor.execute_partition(
                    partition=partition,
                    frozen_input=frozen_input,
                    manifest_id=manifest_id,
                )
                completed_checkpoints.append(checkpoint)

                _emit_inspection_safely(
                    subject_type="research_run",
                    subject_id=str(run["run_id"]),
                    event_kind="agent_step",
                    title=f"Completed {partition.partition_id}",
                    status="succeeded",
                    operation="research.partition.execute",
                    output_data={
                        "partition_id": partition.partition_id,
                        "row_count": checkpoint.row_count,
                        "estimated_mb": partition.estimated_mb,
                    },
                )

            except Exception as e:
                _emit_inspection_safely(
                    subject_type="research_run",
                    subject_id=str(run["run_id"]),
                    event_kind="agent_step",
                    title=f"Failed {partition.partition_id}",
                    status="failed",
                    operation="research.partition.execute",
                    output_data={
                        "partition_id": partition.partition_id,
                        "error": str(e),
                    },
                )
                raise RuntimeError(f"分区 {partition.partition_id} 执行失败: {e}") from e

        # 更新进度：聚合阶段
        self._update_partition_progress(
            run_id=run["run_id"],
            partition_id="AGGREGATING",
            partition_index=len(partitions),
            total_partitions=len(partitions),
            progress_percent=95,
        )

        # 聚合所有分区结果
        final_result = executor.aggregate_partitions(
            checkpoints=completed_checkpoints,
            frozen_input=frozen_input,
        )

        # 清理旧 Checkpoint（可选，保留 7 天）
        # checkpoint_manager.cleanup(bundle_hash, keep_days=7)

        return final_result

    def _update_partition_progress(
        self,
        run_id: str,
        partition_id: str,
        partition_index: int,
        total_partitions: int,
        progress_percent: int,
    ) -> None:
        """更新分区执行进度"""
        progress_data = {
            "phase": partition_id,
            "partition_index": partition_index,
            "total_partitions": total_partitions,
            "progress_percent": progress_percent,
        }

        # 持久化到数据库（可选）
        # 目前只发送 event

        _emit_inspection_safely(
            subject_type="research_run",
            subject_id=str(run_id),
            event_kind="progress",
            title=f"Partition {partition_index}/{total_partitions}",
            status="running",
            operation="research.partition.progress",
            output_data=progress_data,
        )

    def _execute_isolated(
        self,
        run: Mapping[str, Any],
        *,
        timeout_seconds: int,
        memory_limit_mb: int | None = None,
    ) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="datatube-research-run-") as temp_dir:
            output_path = Path(temp_dir) / "output.json"
            attempt = max(1, int(run.get("attempt_count") or 1))
            log_path = worker_log_path(
                self.store.db_path,
                "research_runs",
                _clean(run.get("run_id")),
                f"attempt-{attempt}.log",
            )
            command = [
                sys.executable,
                "-m",
                "services.data_platform.research_run_child",
                "--db-path",
                str(self.store.db_path),
                "--run-id",
                _clean(run.get("run_id")),
                "--output",
                str(output_path),
            ]
            run_guarded_process(
                command,
                cwd=str(Path(__file__).resolve().parents[2]),
                log_path=log_path,
                timeout_seconds=max(1, int(timeout_seconds)),
                memory_limit_mb=max(
                    512,
                    int(
                        memory_limit_mb
                        or os.environ.get("DATATUBE_RESEARCH_WORKER_MEMORY_MB", "8192")
                    ),
                ),
            )
            if not output_path.is_file():
                raise RuntimeError("isolated formal Research Run produced no result envelope")
            return dict(json.loads(output_path.read_text(encoding="utf-8")))

    def complete(self, run_id: str, output: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            row = conn.execute(
                "SELECT reservation_id, status, lease_owner, started_at FROM research_runs_v2 WHERE run_id=?", (_clean(run_id),)
            ).fetchone()
            if not row or str(row["status"]) != "RUNNING" or str(row["lease_owner"]) != self.worker_id:
                raise ValueError("Run is not leased by this Worker")
            conn.execute(
                """UPDATE research_runs_v2 SET status='SUCCEEDED', output_json=?,
                   error_json='{}', heartbeat_at=?, finished_at=?, lease_owner=NULL,
                   lease_expires_at=NULL WHERE run_id=?""",
                (json_dumps(output), now, now, _clean(run_id)),
            )
            started_at = _parse_time(str(row["started_at"] or ""))
            finished_at = _parse_time(now)
            actual_runtime_seconds = max(
                1,
                int(math.ceil((finished_at - started_at).total_seconds()))
                if started_at and finished_at else 1,
            )
            self._consume_reservation_in_conn(
                conn,
                str(row["reservation_id"]),
                now,
                actual_runtime_seconds=actual_runtime_seconds,
            )
        return ResearchRunService(self.store).get(run_id)  # type: ignore[return-value]

    def fail(self, run_id: str, error: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            row = conn.execute(
                "SELECT reservation_id, status, lease_owner, attempt_count, max_attempts FROM research_runs_v2 WHERE run_id=?",
                (_clean(run_id),),
            ).fetchone()
            if not row or str(row["status"]) != "RUNNING" or str(row["lease_owner"]) != self.worker_id:
                raise ValueError("Run is not leased by this Worker")
            retry = (
                int(row["attempt_count"]) < int(row["max_attempts"])
                and bool(error.get("retryable", True))
                and _clean(error.get("code")) not in {
                    "FORMAL_RESEARCH_EXECUTION_TIMEOUT",
                    "FORMAL_RESEARCH_RESOURCE_LIMIT",
                    "FORMAL_RESEARCH_PROCESS_INTERRUPTED",
                }
                and _clean(error.get("exception_type")) != "MemoryError"
            )
            status = "QUEUED" if retry else "FAILED"
            conn.execute(
                "UPDATE research_runs_v2 SET status=?, error_json=?, finished_at=?, lease_owner=NULL, lease_expires_at=NULL WHERE run_id=?",
                (status, json_dumps(error), None if retry else now, _clean(run_id)),
            )
            if not retry:
                self._release_reservation_in_conn(conn, str(row["reservation_id"]), now)
        return ResearchRunService(self.store).get(run_id)  # type: ignore[return-value]

    def fail_interrupted(self, run_id: str) -> dict[str, Any] | None:
        """Close a RUNNING lease left behind by a terminated server process.

        This is intentionally not a retry: the experiment layer quarantines
        the interrupted attempt so a restart cannot silently repeat a large
        computation and exhaust the machine again.
        """

        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            row = conn.execute(
                "SELECT reservation_id, status FROM research_runs_v2 WHERE run_id=?",
                (_clean(run_id),),
            ).fetchone()
            if row is None:
                return None
            if str(row["status"]) != "RUNNING":
                return ResearchRunService._get_in_conn(conn, _clean(run_id))
            error = {
                "code": "FORMAL_RESEARCH_PROCESS_INTERRUPTED",
                "message": "The server process ended while this Research Run was active.",
                "exception_type": "ProcessInterrupted",
            }
            conn.execute(
                """
                UPDATE research_runs_v2
                SET status='FAILED', error_json=?, finished_at=?,
                    lease_owner=NULL, lease_expires_at=NULL
                WHERE run_id=? AND status='RUNNING'
                """,
                (json_dumps(error), now, _clean(run_id)),
            )
            self._release_reservation_in_conn(
                conn, str(row["reservation_id"] or ""), now
            )
            return ResearchRunService._get_in_conn(conn, _clean(run_id))

    @staticmethod
    def _consume_reservation_in_conn(
        conn: Any,
        reservation_id: str,
        now: str,
        *,
        actual_runtime_seconds: int | None = None,
    ) -> None:
        row = conn.execute("SELECT * FROM approval_budget_reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
        if not row or str(row["status"]) != "RESERVED":
            return
        consumed_runtime = (
            max(1, int(actual_runtime_seconds))
            if actual_runtime_seconds is not None
            else int(row["runtime_seconds"])
        )
        conn.execute(
            "UPDATE approval_budget_reservations SET status='CONSUMED', consumed_at=?, runtime_seconds=? WHERE reservation_id=?",
            (now, consumed_runtime, reservation_id),
        )
        conn.execute(
            """UPDATE approval_budget_counters SET
               reserved_runs=reserved_runs-?, consumed_runs=consumed_runs+?,
               reserved_download_bytes=reserved_download_bytes-?, consumed_download_bytes=consumed_download_bytes+?,
               reserved_runtime_seconds=reserved_runtime_seconds-?, consumed_runtime_seconds=consumed_runtime_seconds+?, updated_at=?
               WHERE grant_id=?""",
            (row["runs"], row["runs"], row["download_bytes"], row["download_bytes"], row["runtime_seconds"], consumed_runtime, now, row["grant_id"]),
        )

    @staticmethod
    def reconcile_project_runtime_budget(
        store: DataPlatformStore,
        project_id: str,
    ) -> dict[str, Any]:
        """Reconcile consumed runtime to observed Run wall-clock seconds."""
        now = utc_now()
        with store.transaction(immediate=True) as conn:
            grants = conn.execute(
                "SELECT grant_id FROM approval_grants WHERE project_id=?",
                (_clean(project_id),),
            ).fetchall()
            grant_ids = [str(row[0]) for row in grants]
            adjusted = 0
            for grant_id in grant_ids:
                rows = conn.execute(
                    """
                    SELECT r.reservation_id, r.runtime_seconds, run.started_at, run.finished_at
                    FROM approval_budget_reservations r
                    JOIN research_runs_v2 run ON run.reservation_id=r.reservation_id
                    WHERE r.grant_id=? AND r.status='CONSUMED'
                      AND run.status='SUCCEEDED' AND run.started_at IS NOT NULL
                      AND run.finished_at IS NOT NULL
                    """,
                    (grant_id,),
                ).fetchall()
                for row in rows:
                    started = _parse_time(str(row["started_at"]))
                    finished = _parse_time(str(row["finished_at"]))
                    actual = max(1, int(math.ceil((finished - started).total_seconds())))
                    if actual != int(row["runtime_seconds"]):
                        conn.execute(
                            "UPDATE approval_budget_reservations SET runtime_seconds=? WHERE reservation_id=?",
                            (actual, str(row["reservation_id"])),
                        )
                        adjusted += 1
                totals = conn.execute(
                    """
                    SELECT
                      COALESCE(sum(CASE WHEN status='RESERVED' THEN runs ELSE 0 END),0),
                      COALESCE(sum(CASE WHEN status='CONSUMED' THEN runs ELSE 0 END),0),
                      COALESCE(sum(CASE WHEN status='RESERVED' THEN download_bytes ELSE 0 END),0),
                      COALESCE(sum(CASE WHEN status='CONSUMED' THEN download_bytes ELSE 0 END),0),
                      COALESCE(sum(CASE WHEN status='RESERVED' THEN runtime_seconds ELSE 0 END),0),
                      COALESCE(sum(CASE WHEN status='CONSUMED' THEN runtime_seconds ELSE 0 END),0)
                    FROM approval_budget_reservations WHERE grant_id=?
                    """,
                    (grant_id,),
                ).fetchone()
                conn.execute(
                    """
                    UPDATE approval_budget_counters SET
                      reserved_runs=?, consumed_runs=?, reserved_download_bytes=?,
                      consumed_download_bytes=?, reserved_runtime_seconds=?,
                      consumed_runtime_seconds=?, updated_at=? WHERE grant_id=?
                    """,
                    (*totals, now, grant_id),
                )
        return {
            "project_id": _clean(project_id),
            "grant_ids": grant_ids,
            "adjusted_reservations": adjusted,
            "reconciled_at": now,
        }

    @staticmethod
    def _release_reservation_in_conn(conn: Any, reservation_id: str, now: str) -> None:
        row = conn.execute("SELECT * FROM approval_budget_reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
        if not row or str(row["status"]) != "RESERVED":
            return
        conn.execute("UPDATE approval_budget_reservations SET status='RELEASED', released_at=? WHERE reservation_id=?", (now, reservation_id))
        conn.execute(
            """UPDATE approval_budget_counters SET reserved_runs=reserved_runs-?,
               reserved_download_bytes=reserved_download_bytes-?, reserved_runtime_seconds=reserved_runtime_seconds-?, updated_at=?
               WHERE grant_id=?""",
            (row["runs"], row["download_bytes"], row["runtime_seconds"], now, row["grant_id"]),
        )


class FormalResearchRunExecutor:
    """Execute the engine contracts fixed inside one Frozen Bundle."""

    def __init__(self, store: DataPlatformStore, *, artifact_root: str | None = None):
        self.store = store
        self.artifact_root = artifact_root

    def execute(self, run: dict[str, Any]) -> dict[str, Any]:
        from .artifact_service import ResearchArtifactMaterializer
        from .backtest_contract import BacktestExecutionSpec
        from .data_client import FrozenManifestData
        from .definition_registry import DefinitionRegistry
        from .evaluation import AlphaEvaluator, EvaluationSpec, FactorEvaluator
        from .factor_alpha import AlphaComponent, AlphaEngine, AlphaSpec
        from .factor_pack import FactorPackMemberSpec, FactorPackRegistry
        from .factor_definition_executor import FactorDefinitionExecutor
        from .portfolio import PortfolioEngine, PortfolioSpec
        from .provenance_service import ManifestProvenanceService
        from .requirement_compiler import RequirementCompiler
        from .research_backtest import ResearchBacktestProvider
        from .equity_monthly_research import EquityMonthlyResearchBacktester
        from .universe_service import UniverseService

        frozen = run["frozen_input"]
        closure = frozen["input_closure"]
        if closure["input_mode"] != "DEFINITIONS":
            raise ValueError("formal worker v2 currently requires DEFINITIONS input mode")
        ResearchRunService(self.store).verify_bundle(frozen["bundle_id"])
        snapshot = UniverseService(self.store).get_snapshot(closure["universe_snapshot_id"])
        if snapshot is None:
            raise ValueError("Frozen Universe Snapshot not found")
        requirement_set = RequirementCompiler(self.store).get(
            closure["requirement_set_id"]
        )
        if requirement_set is None:
            raise ValueError("Frozen Requirement Set not found")
        analysis_start = _parse_time(
            str(requirement_set.context.get("history_start") or "")
        )
        analysis_end = _parse_time(
            str(requirement_set.context.get("history_end") or "")
        )
        input_windows: dict[tuple[str, str], tuple[datetime | None, datetime | None]] = {}
        for requirement in requirement_set.requirements:
            required_start = _parse_time(requirement.history_start)
            required_end = _parse_time(requirement.history_end)
            for instrument_id in requirement.instrument_ids:
                key = (str(instrument_id), str(requirement.frequency).lower())
                current = input_windows.get(key)
                if current is None:
                    input_windows[key] = (required_start, required_end)
                    continue
                starts = [value for value in (current[0], required_start) if value is not None]
                merged_start = min(starts) if starts else None
                merged_end = (
                    None
                    if current[1] is None or required_end is None
                    else max(current[1], required_end)
                )
                input_windows[key] = (merged_start, merged_end)
        allowed = set(snapshot.actual_instrument_ids)
        if any(instrument_id.upper().endswith(":ALL") for instrument_id in allowed):
            raise ValueError(
                "Collection Catalog ids must be expanded to row-level Universe members before formal execution"
            )
        bars: dict[str, list[dict[str, Any]]] = {}
        manifest_inputs: list[dict[str, Any]] = []
        equity_monthly_metadata: dict[str, Any] | None = None
        for manifest_id in closure["exact_manifest_ids"]:
            frozen_data = FrozenManifestData(self.store, manifest_id)
            catalog_entry = frozen_data.catalog.get_catalog(frozen_data.dataset_id)
            if catalog_entry is None:
                raise ValueError(f"Frozen Manifest catalog entry is unavailable: {manifest_id}")
            if str(catalog_entry.data_type).lower() == "equity_research_monthly":
                provenance = ManifestProvenanceService(self.store).get(manifest_id)
                frozen_metadata = dict(
                    ((provenance or {}).get("request") or {}).get("metadata") or {}
                )
                if not frozen_metadata:
                    if catalog_entry.latest_manifest_id != manifest_id:
                        raise ValueError(
                            "Historical equity panel lacks immutable provenance; refusing mutable Catalog lineage"
                        )
                    frozen_metadata = dict(catalog_entry.metadata or {})
                equity_monthly_metadata = {**frozen_metadata, "panel_manifest_id": manifest_id}
            is_collection = bool(catalog_entry.metadata.get("full_import")) or str(
                catalog_entry.instrument_id
            ).upper().endswith(":ALL")
            candidate_windows = [
                window for (instrument_id, frequency), window in input_windows.items()
                if frequency == str(catalog_entry.frequency).lower()
                and (instrument_id in allowed or instrument_id == catalog_entry.instrument_id)
            ]
            read_starts = [window[0] for window in candidate_windows if window[0] is not None]
            read_ends = [window[1] for window in candidate_windows if window[1] is not None]
            read_columns = None
            if (
                str(catalog_entry.data_type).lower() == "bars"
                and str(run["run_type"]).upper() in {"FACTOR_EVALUATION", "ALPHA_EVALUATION"}
                and not closure.get("factor_pack_definitions")
            ):
                required_fields = {
                    str(field).lower()
                    for requirement in requirement_set.requirements
                    for field in requirement.fields
                }
                read_columns = sorted({
                    "instrument_id",
                    "bar_start_time",
                    "bar_end_time",
                    "available_time",
                    "open",
                    "close",
                    *required_fields,
                })
            grouped_rows = frozen_data.read_bars_by_instrument(
                columns=read_columns,
                start_time=min(read_starts).isoformat() if read_starts else None,
                end_time=max(read_ends).isoformat() if read_ends else None,
                instrument_ids=allowed if is_collection else None,
            )
            scoped_rows: dict[str, list[dict[str, Any]]] = {}
            for instrument_id, rows in grouped_rows.items():
                window = input_windows.get(
                    (str(instrument_id), str(catalog_entry.frequency).lower())
                )
                if window is None and is_collection:
                    window = input_windows.get(
                        (str(catalog_entry.instrument_id), str(catalog_entry.frequency).lower())
                    )
                if window is None:
                    continue
                scoped_rows[str(instrument_id)] = _rows_in_window(
                    rows, window[0], window[1]
                )
            manifest_inputs.append({
                "manifest_id": manifest_id,
                "data_type": catalog_entry.data_type,
                "frequency": catalog_entry.frequency,
                "fields": set(catalog_entry.fields),
                "rows": scoped_rows,
            })
            if str(catalog_entry.data_type).lower() in {"bars", "price_history"} or str(
                frozen_data.manifest.schema_version
            ).startswith("bars"):
                for instrument_id, rows in scoped_rows.items():
                    bars.setdefault(instrument_id, []).extend(rows)
        for rows in bars.values():
            rows.sort(key=lambda item: str(item.get("bar_start_time") or item.get("event_time") or ""))
        bars = {key: value for key, value in bars.items() if key in allowed}
        if equity_monthly_metadata is None and set(bars) != allowed:
            raise ValueError(f"Frozen Manifests do not supply all Universe members: {sorted(allowed - set(bars))}")
        analysis_bars = {
            instrument_id: _rows_in_window(rows, analysis_start, analysis_end)
            for instrument_id, rows in bars.items()
        }
        snapshot = UniverseService.materialize_dynamic_membership(
            snapshot,
            manifest_inputs,
        )
        if equity_monthly_metadata is None and any(not rows for rows in analysis_bars.values()):
            missing = sorted(
                instrument_id
                for instrument_id, rows in analysis_bars.items()
                if not rows
            )
            raise ValueError(
                f"Frozen Manifests contain no rows in the Research period for: {missing}"
            )

        registry = DefinitionRegistry(self.store)
        materializer = ResearchArtifactMaterializer(self.store, root=self.artifact_root)
        factor_outputs: dict[str, dict[str, list[dict[str, Any]]]] = {}
        factor_artifacts: dict[str, Any] = {}
        factor_pack_outputs: list[dict[str, Any]] = []
        factor_executor = FactorDefinitionExecutor()
        for ref in closure["factor_definitions"]:
            definition = registry.get(ref["factor_definition_id"], version=ref["version"])
            if definition is None or definition.spec_hash != ref["spec_hash"]:
                raise ValueError("Frozen Factor definition identity is unavailable")
            spec, computed_values = factor_executor.execute(
                definition,
                manifest_inputs=manifest_inputs,
                bars_by_instrument=bars,
                allowed_instruments=allowed,
            )
            values = {
                instrument_id: _rows_in_window(
                    rows, analysis_start, analysis_end
                )
                for instrument_id, rows in computed_values.items()
            }
            artifact = materializer.materialize_factor(
                spec=spec, values_by_instrument=values,
                dataset_manifest_ids=closure["exact_manifest_ids"],
                universe_snapshot_id=snapshot.universe_snapshot_id,
                project_id=run["project_id"], created_by_run_id=run["run_id"],
            )
            factor_outputs[spec.name] = values
            factor_artifacts[definition.definition_id] = artifact

        for raw_pack in closure.get("factor_pack_definitions") or []:
            from integrations.qlib import Alpha158ImportService

            pack = FactorPackRegistry.require(str(raw_pack.get("pack_id") or ""))
            if raw_pack.get("spec_hash") != pack.spec_hash:
                raise ValueError("Frozen FactorPackDefinition identity is unavailable")
            imported = Alpha158ImportService(self.store).run(
                manifest_ids=closure["exact_manifest_ids"],
                input_bundle_id=frozen["bundle_id"],
                instrument_ids=sorted(allowed),
                start_time=analysis_start.isoformat() if analysis_start else "",
                end_time=analysis_end.isoformat() if analysis_end else "",
            )
            manifest = dict(imported.get("manifest") or {})
            names = list(manifest.get("factor_names") or [])
            if (
                manifest.get("pack_id") != pack.pack_id
                or int(manifest.get("factor_count") or 0) != pack.factor_count
                or len(names) != pack.factor_count
                or set(pack.excluded_factors).intersection(names)
                or bool(manifest.get("is_standard_alpha158")) != pack.is_standard_alpha158
            ):
                raise ValueError("GOAL_CONFORMANCE_FAILED: computed Factor Pack identity differs from Contract")
            values_artifact = materializer.artifacts.create(
                artifact_type="FACTOR_PACK_VALUES",
                logical_name=f"{pack.pack_id}-{run['run_id']}",
                content_uri=str(imported["factor_path"]),
                content_hash=str(dict(manifest.get("output") or {}).get("sha256") or imported["cache_id"]),
                schema_version=str(manifest.get("factor_frame_schema_version") or "factor-frame.wide.v1"),
                project_id=run["project_id"],
                created_by_run_id=run["run_id"],
                spec_hash=pack.spec_hash,
                engine_version=f"qlib:{dict(manifest.get('engine') or {}).get('qlib_version') or 'unknown'}",
                code_hash=pack.code_hash,
                metadata={
                    "pack_id": pack.pack_id,
                    "pack_version": pack.version,
                    "factor_count": pack.factor_count,
                    "factor_names": names,
                    "row_count": dict(manifest.get("output") or {}).get("row_count"),
                    "instrument_count": dict(manifest.get("output") or {}).get("instrument_count"),
                    "cache_id": imported.get("cache_id"),
                    "goal_conformance": "PASS",
                },
                dependencies=[
                    *[
                        {"parent_id": manifest_id, "parent_type": "DATASET_MANIFEST", "dependency_type": "INPUT_DATASET"}
                        for manifest_id in closure["exact_manifest_ids"]
                    ],
                    {
                        "parent_id": snapshot.universe_snapshot_id,
                        "parent_type": "UNIVERSE_SNAPSHOT",
                        "dependency_type": "INPUT_UNIVERSE",
                    },
                ],
            )
            factor_pack_outputs.append({
                "definition": pack,
                "imported": imported,
                "manifest": manifest,
                "factor_names": names,
                "values_artifact": values_artifact,
            })

        alpha_outputs: list[tuple[Any, list[dict[str, Any]], Any]] = []
        for ref in closure["alpha_definitions"]:
            definition = registry.get(ref["alpha_definition_id"], version=ref["version"])
            if definition is None or definition.spec_hash != ref["spec_hash"]:
                raise ValueError("Frozen Alpha definition identity is unavailable")
            components = []
            used_artifacts = []
            for item in definition.spec["components"]:
                components.append(AlphaComponent(
                    factor_name=item["factor_name"], weight=float(item["weight"]),
                    transform=item.get("transform", "CS_RANK"), ascending=bool(item.get("ascending", True)),
                ))
                artifact = factor_artifacts.get(item["factor_definition_id"])
                if artifact is None:
                    raise ValueError("Alpha's pinned Factor is absent from the Frozen definition closure")
                used_artifacts.append(artifact.artifact_id)
            spec = AlphaSpec(
                name=definition.name, version=definition.version, components=tuple(components),
                minimum_coverage=float(definition.spec.get("minimum_coverage", 1.0)),
                universe_snapshot_id=snapshot.universe_snapshot_id,
                minimum_cross_section_size=int(definition.spec.get("minimum_cross_section_size", 1)),
                missing_policy=definition.spec.get("missing_policy", "EXCLUDE"),
                rank_method=definition.spec.get("rank_method", "AVERAGE"),
                output_scale=definition.spec.get("output_scale", "PERCENTILE"),
            )
            signals = AlphaEngine().build_signals(spec, factor_outputs, universe_snapshot=snapshot)
            artifact = materializer.materialize_alpha(
                spec=spec, signals=signals, factor_artifact_ids=used_artifacts,
                dataset_manifest_ids=closure["exact_manifest_ids"],
                universe_snapshot_id=snapshot.universe_snapshot_id,
                project_id=run["project_id"], created_by_run_id=run["run_id"],
            )
            alpha_outputs.append((spec, signals, artifact))

        specs = frozen.get("execution_specs") or {}
        produced_factor_ids = [item.artifact_id for item in factor_artifacts.values()]
        produced_factor_pack_ids = [item["values_artifact"].artifact_id for item in factor_pack_outputs]
        produced_factor_pack_evaluation_ids: list[str] = []
        produced_alpha_ids = [item[2].artifact_id for item in alpha_outputs]
        produced_evaluation_ids: list[str] = []
        produced_portfolio_ids: list[str] = []
        produced_backtest_ids: list[str] = []
        metrics: dict[str, Any] = {}
        if run["run_type"] == "FACTOR_EVALUATION":
            evaluation_payload = dict(specs.get("evaluation_spec") or {})
            if len(allowed) > 500 and "retain_observations" not in evaluation_payload:
                # Legacy Frozen Bundles predate the explicit retention flag.
                # Their decision metrics never depended on persisting every
                # instrument/horizon observation, so resume them with the
                # bounded aggregate-only representation instead of OOMing.
                evaluation_payload["retain_observations"] = False
            evaluation_spec = EvaluationSpec(**evaluation_payload)
            for ref in closure["factor_definitions"]:
                definition = registry.get(ref["factor_definition_id"], version=ref["version"])
                result = FactorEvaluator().evaluate(
                    spec=evaluation_spec,
                    factor_values_by_instrument=factor_outputs[definition.name],
                    bars_by_instrument=analysis_bars, universe_snapshot=snapshot,
                )
                artifact = materializer.materialize_evaluation(
                    logical_name=f"{definition.name}-{run['run_id']}", result=result, spec=evaluation_spec,
                    input_artifact_id=factor_artifacts[definition.definition_id].artifact_id,
                    dataset_manifest_ids=closure["exact_manifest_ids"], universe_snapshot_id=snapshot.universe_snapshot_id,
                    project_id=run["project_id"], created_by_run_id=run["run_id"],
                )
                produced_evaluation_ids.append(artifact.artifact_id)
                metrics[definition.name] = result.summary
            for pack_output in factor_pack_outputs:
                try:
                    import pandas as pd
                except ImportError as exc:
                    raise RuntimeError("Factor Pack evaluation requires pandas") from exc
                pack = pack_output["definition"]
                frame = pd.read_parquet(pack_output["imported"]["factor_path"])
                factor_results: list[dict[str, Any]] = []
                summary_rows: list[dict[str, Any]] = []
                for index, factor_name in enumerate(pack_output["factor_names"]):
                    values_by_instrument: dict[str, list[dict[str, Any]]] = {}
                    for row in frame[["datetime", "instrument", factor_name]].itertuples(index=False, name=None):
                        event_time = row[0].isoformat() if hasattr(row[0], "isoformat") else str(row[0])
                        raw_value = row[2]
                        value = None if pd.isna(raw_value) else float(raw_value)
                        values_by_instrument.setdefault(str(row[1]), []).append({
                            "instrument_id": str(row[1]),
                            "event_time": event_time,
                            "bar_start_time": event_time,
                            "bar_end_time": event_time,
                            "factor_as_of_time": event_time,
                            "available_time": event_time,
                            "factor_name": factor_name,
                            "factor_version": f"{pack.version}.{index:03d}",
                            "value": value,
                            "quality_status": "PASS" if value is not None else "WARMUP",
                        })
                    member_spec = FactorPackMemberSpec(
                        pack_id=pack.pack_id,
                        pack_version=pack.version,
                        name=factor_name,
                        member_index=index,
                        engine_version=str(dict(pack_output["manifest"].get("engine") or {}).get("qlib_version") or "qlib"),
                        code_hash=pack.code_hash,
                        frequency=pack.frequency,
                    )
                    evaluation_result = FactorEvaluator().evaluate(
                        spec=evaluation_spec,
                        factor_values_by_instrument=values_by_instrument,
                        bars_by_instrument=analysis_bars,
                        universe_snapshot=snapshot,
                    )
                    summary = dict(evaluation_result.summary)
                    factor_results.append({
                        "factor": {
                            "name": factor_name,
                            "member_index": index,
                            "spec_hash": member_spec.spec_hash,
                        },
                        "summary": summary,
                    })
                    first_horizon = str(evaluation_spec.horizons[0])
                    summary_rows.append({
                        "factor_name": factor_name,
                        "member_index": index,
                        "coverage": summary.get("coverage"),
                        "ic": dict(summary.get("ic") or {}).get(first_horizon, {}).get("mean"),
                        "rank_ic": dict(summary.get("rank_ic") or {}).get(first_horizon, {}).get("mean"),
                        "horizon_bars": int(first_horizon),
                    })
                evaluation_artifact = materializer.materialize_rows(
                    artifact_type="FACTOR_PACK_EVALUATION",
                    logical_name=f"{pack.pack_id}-{run['run_id']}",
                    rows=summary_rows,
                    schema_version="factor-pack-evaluation.v1",
                    output_folder="factor_pack_evaluations",
                    dependencies=[{
                        "parent_id": pack_output["values_artifact"].artifact_id,
                        "parent_type": "RESEARCH_ARTIFACT",
                        "dependency_type": "INPUT_FACTOR_PACK",
                    }],
                    project_id=run["project_id"],
                    created_by_run_id=run["run_id"],
                    spec_hash=pack.spec_hash,
                    engine_version=f"factor-pack-evaluator:{pack.version}",
                    code_hash=pack.code_hash,
                    identity_context={
                        "pack_spec_hash": pack.spec_hash,
                        "evaluation_spec_hash": evaluation_spec.spec_hash,
                        "input_artifact_id": pack_output["values_artifact"].artifact_id,
                    },
                    metadata={
                        "pack_id": pack.pack_id,
                        "factor_count": pack.factor_count,
                        "row_count": len(summary_rows),
                        "evaluation_spec": evaluation_spec.to_dict(),
                        "summary": {
                            "evaluated_factor_count": len(factor_results),
                            "goal_conformance": "PASS",
                        },
                        "results": factor_results,
                    },
                )
                produced_factor_pack_evaluation_ids.append(evaluation_artifact.artifact_id)
                metrics[pack.pack_id] = {
                    "factor_count": pack.factor_count,
                    "evaluated_factor_count": len(factor_results),
                    "goal_conformance": "PASS",
                }
        elif run["run_type"] == "ALPHA_EVALUATION":
            evaluation_payload = dict(specs.get("evaluation_spec") or {})
            if len(allowed) > 500 and "retain_observations" not in evaluation_payload:
                evaluation_payload["retain_observations"] = False
            evaluation_spec = EvaluationSpec(**evaluation_payload)
            for alpha_spec, signals, alpha_artifact in alpha_outputs:
                evaluation_result = AlphaEvaluator().evaluate(
                    spec=evaluation_spec, alpha_signals=signals,
                    bars_by_instrument=analysis_bars, universe_snapshot=snapshot,
                )
                artifact = materializer.materialize_evaluation(
                    logical_name=f"{alpha_spec.name}-{run['run_id']}", result=evaluation_result, spec=evaluation_spec,
                    input_artifact_id=alpha_artifact.artifact_id,
                    dataset_manifest_ids=closure["exact_manifest_ids"], universe_snapshot_id=snapshot.universe_snapshot_id,
                    project_id=run["project_id"], created_by_run_id=run["run_id"],
                )
                produced_evaluation_ids.append(artifact.artifact_id)
                metrics[alpha_spec.name] = evaluation_result.summary
        elif run["run_type"] == "RESEARCH_BACKTEST":
            if len(alpha_outputs) != 1:
                raise ValueError("Research Backtest v1 requires exactly one Alpha definition")
            alpha_spec, signals, alpha_artifact = alpha_outputs[0]
            portfolio_payload = dict(specs.get("portfolio_spec") or {})
            portfolio_payload["universe_snapshot_id"] = snapshot.universe_snapshot_id
            portfolio_spec = PortfolioSpec(**portfolio_payload)
            targets = PortfolioEngine().build_targets(signals, portfolio_spec)
            target_artifact = materializer.materialize_portfolio_targets(
                spec=portfolio_spec, targets=targets, alpha_artifact_id=alpha_artifact.artifact_id,
                universe_snapshot_id=snapshot.universe_snapshot_id,
                project_id=run["project_id"], created_by_run_id=run["run_id"],
            )
            produced_portfolio_ids.append(target_artifact.artifact_id)
            execution_spec = BacktestExecutionSpec.from_payload(specs.get("execution_spec") or {})
            if equity_monthly_metadata is not None:
                source_manifests = dict(equity_monthly_metadata.get("source_manifests") or {})
                bars_manifest_id = str(source_manifests.get("bars") or "")
                benchmark_manifest_id = str(equity_monthly_metadata.get("benchmark_manifest_id") or "")
                if not bars_manifest_id or not benchmark_manifest_id:
                    raise ValueError("Equity monthly panel is missing pinned execution lineage")
                lineage_manifest_ids = list(dict.fromkeys([
                    *closure["exact_manifest_ids"],
                    *list(equity_monthly_metadata.get("source_manifest_ids") or []),
                    benchmark_manifest_id,
                ]))
                result = EquityMonthlyResearchBacktester(self.store).simulate(
                    targets=targets,
                    bars_manifest_id=bars_manifest_id,
                    benchmark_manifest_id=benchmark_manifest_id,
                    initial_cash=float((specs.get("execution_spec") or {}).get("initial_cash", 10_000.0)),
                    execution_spec=execution_spec,
                    portfolio_spec=portfolio_spec,
                    dataset_manifest_ids=lineage_manifest_ids,
                    universe_snapshot_ids=[snapshot.universe_snapshot_id],
                    factor_artifact_ids=produced_factor_ids,
                    alpha_artifact_ids=produced_alpha_ids,
                    input_bundle_id=frozen["bundle_id"],
                )
            else:
                result = ResearchBacktestProvider().simulate(
                    bars_by_instrument=analysis_bars, alpha_signals=targets,
                    initial_cash=float((specs.get("execution_spec") or {}).get("initial_cash", 10_000.0)),
                    execution_spec=execution_spec, portfolio_spec=portfolio_spec,
                    dataset_manifest_ids=closure["exact_manifest_ids"],
                    universe_snapshot_ids=[snapshot.universe_snapshot_id],
                    factor_artifact_ids=produced_factor_ids, alpha_artifact_ids=produced_alpha_ids,
                    input_bundle_id=frozen["bundle_id"],
                )
            artifacts = materializer.materialize_backtest(
                logical_name=f"{alpha_spec.name}-{run['run_id']}", result=result,
                portfolio_target_artifact_id=target_artifact.artifact_id,
                project_id=run["project_id"], created_by_run_id=run["run_id"],
            )
            produced_backtest_ids = [item.artifact_id for item in artifacts.values()]
            metrics = result.metrics
        else:
            raise ValueError(f"unsupported formal Run type: {run['run_type']}")
        return {
            "product_run_type": {
                "FACTOR_EVALUATION": "FACTOR_RUN",
                "ALPHA_EVALUATION": "ALPHA_RUN",
                "RESEARCH_BACKTEST": "RESEARCH_BACKTEST",
            }[run["run_type"]],
            "produced_factor_artifact_ids": produced_factor_ids,
            "produced_factor_pack_artifact_ids": produced_factor_pack_ids,
            "produced_factor_pack_evaluation_artifact_ids": produced_factor_pack_evaluation_ids,
            "produced_alpha_artifact_ids": produced_alpha_ids,
            "produced_evaluation_artifact_ids": produced_evaluation_ids,
            "produced_portfolio_artifact_ids": produced_portfolio_ids,
            "produced_backtest_artifact_ids": produced_backtest_ids,
            "metrics": metrics,
            "input_bundle_id": frozen["bundle_id"],
        }

    @staticmethod
    def _bind_factor_inputs(
        spec: Any,
        manifest_inputs: list[dict[str, Any]],
        allowed_instruments: set[str],
    ) -> dict[str, dict[str, list[dict[str, Any]]]]:
        from .factor_definition_executor import FactorDefinitionExecutor

        return FactorDefinitionExecutor.bind_factor_inputs(
            spec,
            manifest_inputs,
            allowed_instruments,
        )
