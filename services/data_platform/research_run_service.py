from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from .run_contracts import (
    BundleInputClosure,
    BundleInputMode,
    HistoricalAuthorizationEvidence,
    IdempotencyConflictError,
)
from .run_preview_service import ResearchRunPreviewService
from .store import DataPlatformStore, json_dumps, utc_now


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
        return self.get(run_id)  # type: ignore[return-value]

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
        bundle = conn.execute(
            "SELECT lifecycle_status, integrity_status, reuse_status, reuse_reason_code, bundle_hash FROM frozen_research_bundles WHERE bundle_id=?",
            (result["bundle_id"],),
        ).fetchone()
        result["bundle"] = dict(bundle) if bundle else None
        return result


class ResearchRunWorker:
    """Lease only committed QUEUED Runs and consume only their Frozen Bundle."""

    def __init__(self, store: DataPlatformStore, worker_id: str = "formal-research-worker"):
        self.store = store
        self.worker_id = _clean(worker_id) or "formal-research-worker"

    def claim(self, *, lease_seconds: int = 300, project_id: str = "") -> dict[str, Any] | None:
        now_dt = datetime.now(timezone.utc)
        now = now_dt.replace(microsecond=0).isoformat()
        expires = (now_dt + timedelta(seconds=max(30, int(lease_seconds)))).replace(microsecond=0).isoformat()
        with self.store.transaction(immediate=True) as conn:
            project_id = _clean(project_id)
            if project_id:
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

    def run_once(self, *, lease_seconds: int = 300, project_id: str = "") -> dict[str, Any] | None:
        run = self.claim(lease_seconds=lease_seconds, project_id=project_id)
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
        try:
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
                "code": "FORMAL_RESEARCH_EXECUTION_FAILED",
                "message": str(exc),
                "exception_type": type(exc).__name__,
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

    def complete(self, run_id: str, output: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            row = conn.execute(
                "SELECT reservation_id, status, lease_owner FROM research_runs_v2 WHERE run_id=?", (_clean(run_id),)
            ).fetchone()
            if not row or str(row["status"]) != "RUNNING" or str(row["lease_owner"]) != self.worker_id:
                raise ValueError("Run is not leased by this Worker")
            conn.execute(
                "UPDATE research_runs_v2 SET status='SUCCEEDED', output_json=?, finished_at=?, lease_owner=NULL, lease_expires_at=NULL WHERE run_id=?",
                (json_dumps(output), now, _clean(run_id)),
            )
            self._consume_reservation_in_conn(conn, str(row["reservation_id"]), now)
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
            retry = int(row["attempt_count"]) < int(row["max_attempts"])
            status = "QUEUED" if retry else "FAILED"
            conn.execute(
                "UPDATE research_runs_v2 SET status=?, error_json=?, finished_at=?, lease_owner=NULL, lease_expires_at=NULL WHERE run_id=?",
                (status, json_dumps(error), None if retry else now, _clean(run_id)),
            )
            if not retry:
                self._release_reservation_in_conn(conn, str(row["reservation_id"]), now)
        return ResearchRunService(self.store).get(run_id)  # type: ignore[return-value]

    @staticmethod
    def _consume_reservation_in_conn(conn: Any, reservation_id: str, now: str) -> None:
        row = conn.execute("SELECT * FROM approval_budget_reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
        if not row or str(row["status"]) != "RESERVED":
            return
        conn.execute("UPDATE approval_budget_reservations SET status='CONSUMED', consumed_at=? WHERE reservation_id=?", (now, reservation_id))
        conn.execute(
            """UPDATE approval_budget_counters SET
               reserved_runs=reserved_runs-?, consumed_runs=consumed_runs+?,
               reserved_download_bytes=reserved_download_bytes-?, consumed_download_bytes=consumed_download_bytes+?,
               reserved_runtime_seconds=reserved_runtime_seconds-?, consumed_runtime_seconds=consumed_runtime_seconds+?, updated_at=?
               WHERE grant_id=?""",
            (row["runs"], row["runs"], row["download_bytes"], row["download_bytes"], row["runtime_seconds"], row["runtime_seconds"], now, row["grant_id"]),
        )

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
        from .factor_definition_executor import FactorDefinitionExecutor
        from .portfolio import PortfolioEngine, PortfolioSpec
        from .requirement_compiler import RequirementCompiler
        from .research_backtest import ResearchBacktestProvider
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
        bars: dict[str, list[dict[str, Any]]] = {}
        manifest_inputs: list[dict[str, Any]] = []
        for manifest_id in closure["exact_manifest_ids"]:
            frozen_data = FrozenManifestData(self.store, manifest_id)
            grouped_rows = frozen_data.read_bars_by_instrument()
            catalog_entry = frozen_data.catalog.get_catalog(frozen_data.dataset_id)
            if catalog_entry is None:
                raise ValueError(f"Frozen Manifest catalog entry is unavailable: {manifest_id}")
            scoped_rows: dict[str, list[dict[str, Any]]] = {}
            for instrument_id, rows in grouped_rows.items():
                window = input_windows.get(
                    (str(instrument_id), str(catalog_entry.frequency).lower())
                )
                if window is None:
                    continue
                scoped_rows[str(instrument_id)] = _rows_in_window(
                    [dict(row) for row in rows], window[0], window[1]
                )
            manifest_inputs.append({
                "manifest_id": manifest_id,
                "frequency": catalog_entry.frequency,
                "fields": set(catalog_entry.fields),
                "rows": scoped_rows,
            })
            for instrument_id, rows in scoped_rows.items():
                bars.setdefault(instrument_id, []).extend(rows)
        for rows in bars.values():
            rows.sort(key=lambda item: str(item.get("bar_start_time") or item.get("event_time") or ""))
        allowed = set(snapshot.actual_instrument_ids)
        bars = {key: value for key, value in bars.items() if key in allowed}
        if set(bars) != allowed:
            raise ValueError(f"Frozen Manifests do not supply all Universe members: {sorted(allowed - set(bars))}")
        analysis_bars = {
            instrument_id: _rows_in_window(rows, analysis_start, analysis_end)
            for instrument_id, rows in bars.items()
        }
        if any(not rows for rows in analysis_bars.values()):
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
        produced_alpha_ids = [item[2].artifact_id for item in alpha_outputs]
        produced_evaluation_ids: list[str] = []
        produced_portfolio_ids: list[str] = []
        produced_backtest_ids: list[str] = []
        metrics: dict[str, Any] = {}
        if run["run_type"] == "FACTOR_EVALUATION":
            evaluation_spec = EvaluationSpec(**dict(specs.get("evaluation_spec") or {}))
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
        elif run["run_type"] == "ALPHA_EVALUATION":
            evaluation_spec = EvaluationSpec(**dict(specs.get("evaluation_spec") or {}))
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
