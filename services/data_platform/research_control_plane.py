from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from .store import DataPlatformStore, json_dumps


PLAN_STAGES = {"INTENT", "RESOLVED", "APPROVED", "EXECUTION"}
TASK_STATUSES = {"PENDING", "READY", "RUNNING", "RETRY_WAIT", "SUCCEEDED", "FAILED", "BLOCKED", "CANCELLED"}
SYSTEM_MAINTENANCE_PROJECT_ID = "project_system_requirement_maintenance"
SYSTEM_MAINTENANCE_AUTHORIZATION = "SYSTEM_REQUIREMENT_MAINTENANCE"
SYSTEM_MAINTENANCE_TASK_TYPES = {
    "BINANCE_BARS_BACKFILL",
    "OPENBB_EQUITY_DAILY_EXPORT",
    "POLYMARKET_PRICE_HISTORY_EXPORT",
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _future(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=max(1, int(seconds)))).replace(microsecond=0).isoformat()


def _parse_time(value: Any) -> datetime:
    parsed = datetime.fromisoformat(_clean(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _loads(value: Any, fallback: Any) -> Any:
    try:
        parsed = json.loads(value or "")
        return parsed
    except Exception:
        return fallback


def _hash_payload(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json_dumps(payload).encode("utf-8")).hexdigest()


def _nonnegative_int(value: Any, name: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if result < 0:
        raise ValueError(f"{name} cannot be negative")
    return result


class ResearchControlPlane:
    """Durable research planning, approval, budget, and task coordination."""

    def __init__(self, store: DataPlatformStore):
        self.store = store

    def create_project(self, *, title: str, objective: str, created_by: str = "local_user") -> dict[str, Any]:
        title = _clean(title)
        objective = _clean(objective)
        if not title or not objective:
            raise ValueError("title and objective are required")
        project_id = f"project_{uuid.uuid4().hex}"
        now = _now()
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                """
                INSERT INTO research_projects(
                    project_id, title, objective, summary_state,
                    current_plan_version, created_by, created_at, updated_at, revision
                ) VALUES (?, ?, ?, 'PLANNING', 0, ?, ?, ?, 0)
                """,
                (project_id, title, objective, _clean(created_by) or "local_user", now, now),
            )
        return self.get_project(project_id)  # type: ignore[return-value]

    def get_project(self, project_id: str) -> Optional[dict[str, Any]]:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM research_projects WHERE project_id = ?",
                (_clean(project_id),),
            ).fetchone()
        return dict(row) if row else None

    def list_projects(self, *, summary_state: str = "", limit: int = 100, include_archived: bool = False) -> list[dict[str, Any]]:
        clauses = [] if include_archived else ["archived_at IS NULL"]
        params: list[Any] = []
        if _clean(summary_state):
            clauses.append("summary_state = ?")
            params.append(_clean(summary_state).upper())
        params.append(max(1, min(int(limit), 500)))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.store.connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM research_projects{where} ORDER BY updated_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def create_plan(
        self,
        *,
        project_id: str,
        stage: str,
        payload: dict[str, Any],
        created_by: str = "local_user",
        plan_version: int | None = None,
    ) -> dict[str, Any]:
        stage = _clean(stage).upper()
        if stage not in PLAN_STAGES:
            raise ValueError(f"unsupported plan stage: {stage}")
        if not isinstance(payload, dict):
            raise ValueError("plan payload must be an object")
        now = _now()
        with self.store.transaction(immediate=True) as conn:
            project = conn.execute(
                "SELECT current_plan_version FROM research_projects WHERE project_id = ?",
                (_clean(project_id),),
            ).fetchone()
            if not project:
                raise ValueError(f"research project not found: {project_id}")
            if plan_version is None:
                if stage != "INTENT":
                    raise ValueError("plan_version is required for non-INTENT plan stages")
                plan_version = int(project[0]) + 1
            plan_version = _nonnegative_int(plan_version, "plan_version")
            if plan_version <= 0:
                raise ValueError("plan_version must be positive")
            plan_hash = _hash_payload(payload)
            existing = conn.execute(
                "SELECT plan_id, plan_hash FROM research_plans WHERE project_id = ? AND plan_version = ? AND plan_stage = ?",
                (_clean(project_id), plan_version, stage),
            ).fetchone()
            if existing:
                if str(existing[1]) != plan_hash:
                    raise ValueError("plan version and stage are immutable; create a new plan version")
                plan_id = str(existing[0])
            else:
                plan_id = f"plan_{uuid.uuid4().hex}"
                conn.execute(
                    """
                    INSERT INTO research_plans(
                        plan_id, project_id, plan_version, plan_stage, status,
                        plan_json, plan_hash, created_by, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'DRAFT', ?, ?, ?, ?, ?)
                    """,
                    (
                        plan_id,
                        _clean(project_id),
                        plan_version,
                        stage,
                        json_dumps(payload),
                        plan_hash,
                        _clean(created_by) or "local_user",
                        now,
                        now,
                    ),
                )
            conn.execute(
                """
                UPDATE research_projects
                SET current_plan_version = MAX(current_plan_version, ?),
                    summary_state = CASE WHEN ? = 'RESOLVED' THEN 'WAITING_APPROVAL' ELSE summary_state END,
                    updated_at = ?, revision = revision + 1
                WHERE project_id = ?
                """,
                (plan_version, stage, now, _clean(project_id)),
            )
        return self.get_plan(plan_id)  # type: ignore[return-value]

    def get_plan(self, plan_id: str) -> Optional[dict[str, Any]]:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM research_plans WHERE plan_id = ?",
                (_clean(plan_id),),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["plan"] = _loads(result.pop("plan_json"), {})
        return result

    def list_plans(self, project_id: str) -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM research_plans WHERE project_id = ? ORDER BY plan_version DESC, plan_stage",
                (_clean(project_id),),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["plan"] = _loads(item.pop("plan_json"), {})
            result.append(item)
        return result

    def approve_plan(
        self,
        *,
        project_id: str,
        plan_version: int,
        scope: dict[str, Any],
        budgets: dict[str, Any],
        approved_by: str = "local_user",
        actor_type: str = "human",
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        if _clean(actor_type).lower() != "human":
            raise PermissionError("only a human actor can approve a research plan")
        plan_version = _nonnegative_int(plan_version, "plan_version")
        if plan_version <= 0:
            raise ValueError("plan_version must be positive")
        if not isinstance(scope, dict) or not isinstance(budgets, dict):
            raise ValueError("scope and budgets must be objects")
        now = _now()
        if expires_at and _parse_time(expires_at) <= _parse_time(now):
            raise ValueError("approval grant expires_at must be in the future")
        with self.store.transaction(immediate=True) as conn:
            project = conn.execute(
                "SELECT 1 FROM research_projects WHERE project_id = ?",
                (_clean(project_id),),
            ).fetchone()
            if not project:
                raise ValueError(f"research project not found: {project_id}")
            resolved = conn.execute(
                """
                SELECT plan_id, plan_json, plan_hash
                FROM research_plans
                WHERE project_id = ? AND plan_version = ? AND plan_stage = 'RESOLVED'
                """,
                (_clean(project_id), plan_version),
            ).fetchone()
            if not resolved:
                raise ValueError("a RESOLVED plan is required before approval")
            approved = conn.execute(
                """
                SELECT plan_id FROM research_plans
                WHERE project_id = ? AND plan_version = ? AND plan_stage = 'APPROVED'
                """,
                (_clean(project_id), plan_version),
            ).fetchone()
            if not approved:
                approved_plan_id = f"plan_{uuid.uuid4().hex}"
                conn.execute(
                    """
                    INSERT INTO research_plans(
                        plan_id, project_id, plan_version, plan_stage, status,
                        plan_json, plan_hash, created_by, created_at, updated_at
                    ) VALUES (?, ?, ?, 'APPROVED', 'APPROVED', ?, ?, ?, ?, ?)
                    """,
                    (
                        approved_plan_id,
                        _clean(project_id),
                        plan_version,
                        resolved[1],
                        resolved[2],
                        _clean(approved_by) or "local_user",
                        now,
                        now,
                    ),
                )
            grant_row = conn.execute(
                """
                SELECT grant_id, scope_json, budgets_json, expires_at
                FROM approval_grants
                WHERE project_id = ? AND plan_version = ? AND status = 'ACTIVE'
                """,
                (_clean(project_id), plan_version),
            ).fetchone()
            if grant_row:
                if _loads(grant_row[1], {}) != scope or _loads(grant_row[2], {}) != budgets or (grant_row[3] or None) != (expires_at or None):
                    raise ValueError("active approval grant is immutable; revoke it before changing scope or budgets")
                grant_id = str(grant_row[0])
            else:
                grant_id = f"grant_{uuid.uuid4().hex}"
                conn.execute(
                    """
                    INSERT INTO approval_grants(
                        grant_id, project_id, plan_version, status, scope_json,
                        budgets_json, approved_by, created_at, approved_at, expires_at
                    ) VALUES (?, ?, ?, 'ACTIVE', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        grant_id,
                        _clean(project_id),
                        plan_version,
                        json_dumps(scope),
                        json_dumps(budgets),
                        _clean(approved_by) or "local_user",
                        now,
                        now,
                        expires_at,
                    ),
                )
                conn.execute(
                    "INSERT INTO approval_budget_counters(grant_id, updated_at) VALUES (?, ?)",
                    (grant_id, now),
                )
            conn.execute(
                "UPDATE research_projects SET summary_state = 'APPROVED', updated_at = ?, revision = revision + 1 WHERE project_id = ?",
                (now, _clean(project_id)),
            )
        return self.get_grant(grant_id)  # type: ignore[return-value]

    def get_grant(self, grant_id: str) -> Optional[dict[str, Any]]:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM approval_grants WHERE grant_id = ?",
                (_clean(grant_id),),
            ).fetchone()
            if not row:
                return None
            counter = conn.execute(
                "SELECT * FROM approval_budget_counters WHERE grant_id = ?",
                (_clean(grant_id),),
            ).fetchone()
        result = dict(row)
        result["scope"] = _loads(result.pop("scope_json"), {})
        result["budgets"] = _loads(result.pop("budgets_json"), {})
        result["counters"] = dict(counter) if counter else {}
        return result

    def list_grants(self, *, project_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if _clean(project_id):
            clauses.append("project_id = ?")
            params.append(_clean(project_id))
        params.append(max(1, min(int(limit), 500)))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.store.connection() as conn:
            rows = conn.execute(
                f"SELECT grant_id FROM approval_grants{where} ORDER BY approved_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [self.get_grant(str(row[0])) for row in rows]  # type: ignore[list-item]

    def set_grant_agent_state(
        self,
        grant_id: str,
        *,
        paused: bool,
        actor_type: str = "human",
    ) -> dict[str, Any]:
        if _clean(actor_type).lower() != "human":
            raise PermissionError("only a human actor can pause or resume a Research Agent")
        now = _now()
        with self.store.transaction(immediate=True) as conn:
            row = conn.execute(
                "SELECT status, expires_at, project_id FROM approval_grants WHERE grant_id=?",
                (_clean(grant_id),),
            ).fetchone()
            if row is None:
                raise ValueError("approval grant not found")
            current = _clean(row["status"]).upper()
            if current not in {"ACTIVE", "PAUSED"}:
                raise ValueError(f"grant cannot be paused or resumed from status {current}")
            if not paused and row["expires_at"] and _parse_time(row["expires_at"]) <= _parse_time(now):
                conn.execute("UPDATE approval_grants SET status='EXPIRED' WHERE grant_id=?", (_clean(grant_id),))
                raise ValueError("expired grant cannot be resumed")
            status = "PAUSED" if paused else "ACTIVE"
            conn.execute("UPDATE approval_grants SET status=? WHERE grant_id=?", (status, _clean(grant_id)))
            conn.execute(
                "UPDATE research_projects SET summary_state=?, updated_at=?, revision=revision+1 WHERE project_id=?",
                ("PAUSED" if paused else "APPROVED", now, str(row["project_id"])),
            )
        return self.get_grant(grant_id)  # type: ignore[return-value]

    def reserve_budget(
        self,
        *,
        grant_id: str,
        idempotency_key: str,
        runs: int = 0,
        download_bytes: int = 0,
        runtime_seconds: int = 0,
    ) -> dict[str, Any]:
        idempotency_key = _clean(idempotency_key)
        if not idempotency_key:
            raise ValueError("idempotency_key is required")
        requested = {
            "runs": _nonnegative_int(runs, "runs"),
            "download_bytes": _nonnegative_int(download_bytes, "download_bytes"),
            "runtime_seconds": _nonnegative_int(runtime_seconds, "runtime_seconds"),
        }
        now = _now()
        with self.store.transaction(immediate=True) as conn:
            existing = conn.execute(
                "SELECT * FROM approval_budget_reservations WHERE grant_id = ? AND idempotency_key = ?",
                (_clean(grant_id), idempotency_key),
            ).fetchone()
            if existing:
                if any(
                    int(existing[column]) != requested[name]
                    for column, name in (
                        ("runs", "runs"),
                        ("download_bytes", "download_bytes"),
                        ("runtime_seconds", "runtime_seconds"),
                    )
                ):
                    raise ValueError("idempotency_key was already used with different budget values")
                return dict(existing)
            grant = conn.execute(
                "SELECT status, budgets_json, expires_at FROM approval_grants WHERE grant_id = ?",
                (_clean(grant_id),),
            ).fetchone()
            if not grant:
                raise ValueError(f"approval grant not found: {grant_id}")
            if str(grant[0]) != "ACTIVE":
                raise ValueError(f"approval grant is not active: {grant_id}")
            if grant[2] and _parse_time(grant[2]) <= _parse_time(now):
                conn.execute("UPDATE approval_grants SET status = 'EXPIRED' WHERE grant_id = ?", (_clean(grant_id),))
                raise ValueError(f"approval grant expired: {grant_id}")
            budgets = _loads(grant[1], {})
            counter = conn.execute(
                "SELECT reserved_runs, consumed_runs, reserved_download_bytes, consumed_download_bytes, reserved_runtime_seconds, consumed_runtime_seconds FROM approval_budget_counters WHERE grant_id = ?",
                (_clean(grant_id),),
            ).fetchone()
            if not counter:
                raise ValueError(f"approval budget counter not found: {grant_id}")
            checks = [
                ("max_backtest_runs", requested["runs"], int(counter[0]), int(counter[1]), "runs"),
                ("max_download_bytes", requested["download_bytes"], int(counter[2]), int(counter[3]), "download_bytes"),
                ("max_runtime_seconds", requested["runtime_seconds"], int(counter[4]), int(counter[5]), "runtime_seconds"),
            ]
            for budget_key, amount, reserved, consumed, label in checks:
                maximum = _nonnegative_int(budgets.get(budget_key, 0), budget_key)
                if consumed + reserved + amount > maximum:
                    raise ValueError(
                        f"approval budget exceeded for {label}: requested={amount}, remaining={max(0, maximum - consumed - reserved)}"
                    )
            reservation_id = f"reservation_{uuid.uuid4().hex}"
            conn.execute(
                """
                INSERT INTO approval_budget_reservations(
                    reservation_id, grant_id, idempotency_key, runs,
                    download_bytes, runtime_seconds, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'RESERVED', ?)
                """,
                (reservation_id, _clean(grant_id), idempotency_key, requested["runs"], requested["download_bytes"], requested["runtime_seconds"], now),
            )
            conn.execute(
                """
                UPDATE approval_budget_counters
                SET reserved_runs = reserved_runs + ?,
                    reserved_download_bytes = reserved_download_bytes + ?,
                    reserved_runtime_seconds = reserved_runtime_seconds + ?,
                    updated_at = ?
                WHERE grant_id = ?
                """,
                (requested["runs"], requested["download_bytes"], requested["runtime_seconds"], now, _clean(grant_id)),
            )
        return self.get_reservation(reservation_id)  # type: ignore[return-value]

    def get_reservation(self, reservation_id: str) -> Optional[dict[str, Any]]:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM approval_budget_reservations WHERE reservation_id = ?",
                (_clean(reservation_id),),
            ).fetchone()
        return dict(row) if row else None

    def release_reservation(self, reservation_id: str) -> dict[str, Any]:
        now = _now()
        with self.store.transaction(immediate=True) as conn:
            row = conn.execute(
                "SELECT * FROM approval_budget_reservations WHERE reservation_id = ?",
                (_clean(reservation_id),),
            ).fetchone()
            if not row:
                raise ValueError(f"reservation not found: {reservation_id}")
            if str(row["status"]) != "RESERVED":
                return dict(row)
            conn.execute(
                "UPDATE approval_budget_reservations SET status = 'RELEASED', released_at = ? WHERE reservation_id = ?",
                (now, _clean(reservation_id)),
            )
            conn.execute(
                """
                UPDATE approval_budget_counters
                SET reserved_runs = reserved_runs - ?,
                    reserved_download_bytes = reserved_download_bytes - ?,
                    reserved_runtime_seconds = reserved_runtime_seconds - ?,
                    updated_at = ?
                WHERE grant_id = ?
                """,
                (row["runs"], row["download_bytes"], row["runtime_seconds"], now, row["grant_id"]),
            )
        return self.get_reservation(reservation_id)  # type: ignore[return-value]

    def consume_reservation(self, reservation_id: str) -> dict[str, Any]:
        now = _now()
        with self.store.transaction(immediate=True) as conn:
            row = conn.execute(
                "SELECT * FROM approval_budget_reservations WHERE reservation_id = ?",
                (_clean(reservation_id),),
            ).fetchone()
            if not row:
                raise ValueError(f"reservation not found: {reservation_id}")
            if str(row["status"]) != "RESERVED":
                return dict(row)
            conn.execute(
                "UPDATE approval_budget_reservations SET status = 'CONSUMED', consumed_at = ? WHERE reservation_id = ?",
                (now, _clean(reservation_id)),
            )
            conn.execute(
                """
                UPDATE approval_budget_counters
                SET reserved_runs = reserved_runs - ?,
                    consumed_runs = consumed_runs + ?,
                    reserved_download_bytes = reserved_download_bytes - ?,
                    consumed_download_bytes = consumed_download_bytes + ?,
                    reserved_runtime_seconds = reserved_runtime_seconds - ?,
                    consumed_runtime_seconds = consumed_runtime_seconds + ?,
                    updated_at = ?
                WHERE grant_id = ?
                """,
                (
                    row["runs"], row["runs"],
                    row["download_bytes"], row["download_bytes"],
                    row["runtime_seconds"], row["runtime_seconds"],
                    now, row["grant_id"],
                ),
            )
        return self.get_reservation(reservation_id)  # type: ignore[return-value]

    def compile_tasks(
        self,
        *,
        project_id: str,
        plan_version: int,
        workflow_run_id: str,
        task_specs: Iterable[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        specs = [dict(item) for item in task_specs]
        if not specs:
            raise ValueError("task_specs cannot be empty")
        workflow_run_id = _clean(workflow_run_id)
        if not workflow_run_id:
            raise ValueError("workflow_run_id is required")
        logical_keys = [_clean(item.get("logical_key")) for item in specs]
        if len(set(logical_keys)) != len(logical_keys):
            raise ValueError("task logical_key values must be unique within a workflow")
        now = _now()
        with self.store.transaction(immediate=True) as conn:
            project = conn.execute(
                "SELECT 1 FROM research_projects WHERE project_id = ?",
                (_clean(project_id),),
            ).fetchone()
            grant = conn.execute(
                "SELECT expires_at FROM approval_grants WHERE project_id = ? AND plan_version = ? AND status = 'ACTIVE'",
                (_clean(project_id), int(plan_version)),
            ).fetchone()
            if not project or not grant:
                raise ValueError("an active approval grant is required before compiling tasks")
            if grant[0] and _parse_time(grant[0]) <= _parse_time(now):
                raise ValueError("approval grant expired before task compilation")
            key_to_id: dict[str, str] = {}
            existing_task_ids: set[str] = set()
            for spec in specs:
                logical_key = _clean(spec.get("logical_key"))
                task_type = _clean(spec.get("task_type")).upper()
                if not logical_key or not task_type:
                    raise ValueError("each task requires task_type and logical_key")
                idempotency_key = _clean(spec.get("idempotency_key")) or f"{workflow_run_id}:{logical_key}"
                normalized_input = spec.get("input") if isinstance(spec.get("input"), dict) else {}
                normalized_outputs = spec.get("expected_output_types") if isinstance(spec.get("expected_output_types"), list) else []
                max_attempts = max(1, int(spec.get("max_attempts", 3)))
                timeout_seconds = max(1, int(spec.get("timeout_seconds", 3600)))
                priority = int(spec.get("priority", 50))
                existing = conn.execute(
                    "SELECT * FROM research_tasks WHERE workflow_run_id = ? AND idempotency_key = ?",
                    (workflow_run_id, idempotency_key),
                ).fetchone()
                task_id = str(existing[0]) if existing else f"task_{uuid.uuid4().hex}"
                key_to_id[logical_key] = task_id
                if existing:
                    existing_task_ids.add(task_id)
                    expected = {
                        "project_id": _clean(project_id),
                        "plan_version": int(plan_version),
                        "task_type": task_type,
                        "logical_key": logical_key,
                        "priority": priority,
                        "input_json": json_dumps(normalized_input),
                        "expected_output_types_json": json_dumps(normalized_outputs),
                        "max_attempts": max_attempts,
                        "timeout_seconds": timeout_seconds,
                    }
                    mismatched = [key for key, value in expected.items() if existing[key] != value]
                    if mismatched:
                        raise ValueError(f"task idempotency_key was reused with different fields: {', '.join(mismatched)}")
                else:
                    conn.execute(
                        """
                        INSERT INTO research_tasks(
                            task_id, project_id, plan_version, workflow_run_id,
                            task_type, logical_key, status, priority, idempotency_key,
                            input_json, expected_output_types_json, max_attempts,
                            timeout_seconds, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            task_id,
                            _clean(project_id),
                            int(plan_version),
                            workflow_run_id,
                            task_type,
                            logical_key,
                            priority,
                            idempotency_key,
                            json_dumps(normalized_input),
                            json_dumps(normalized_outputs),
                            max_attempts,
                            timeout_seconds,
                            now,
                        ),
                    )
            for spec in specs:
                task_id = key_to_id[_clean(spec.get("logical_key"))]
                expected_dependencies: set[str] = set()
                for dependency in spec.get("depends_on", []) if isinstance(spec.get("depends_on"), list) else []:
                    depends_id = key_to_id.get(_clean(dependency), _clean(dependency))
                    if not depends_id:
                        raise ValueError(f"unknown task dependency: {dependency}")
                    exists = conn.execute(
                        "SELECT 1 FROM research_tasks WHERE task_id = ?",
                        (depends_id,),
                    ).fetchone()
                    if not exists:
                        raise ValueError(f"unknown task dependency: {dependency}")
                    dependency_task = conn.execute(
                        "SELECT workflow_run_id FROM research_tasks WHERE task_id = ?",
                        (depends_id,),
                    ).fetchone()
                    if not dependency_task or str(dependency_task[0]) != workflow_run_id:
                        raise ValueError("task dependencies must belong to the same workflow")
                    expected_dependencies.add(depends_id)
                current_dependencies = {
                    str(row[0]) for row in conn.execute(
                        "SELECT depends_on_task_id FROM research_task_dependencies WHERE task_id = ?",
                        (task_id,),
                    ).fetchall()
                }
                if task_id in existing_task_ids and current_dependencies != expected_dependencies:
                    raise ValueError("existing task dependencies do not match the idempotent request")
                for depends_id in expected_dependencies:
                    conn.execute(
                        "INSERT OR IGNORE INTO research_task_dependencies(task_id, depends_on_task_id) VALUES (?, ?)",
                        (task_id, depends_id),
                    )
            self._assert_acyclic_locked(conn, workflow_run_id)
            conn.execute(
                """
                UPDATE research_tasks
                SET status = CASE WHEN NOT EXISTS(
                    SELECT 1 FROM research_task_dependencies d WHERE d.task_id = research_tasks.task_id
                ) THEN 'READY' ELSE 'PENDING' END
                WHERE workflow_run_id = ? AND status IN ('PENDING', 'READY')
                """,
                (workflow_run_id,),
            )
        return self.list_tasks(workflow_run_id=workflow_run_id)

    def compile_maintenance_task(self, task_spec: dict[str, Any]) -> dict[str, Any]:
        """Compile a tightly bounded backend-owned data-maintenance task.

        Requirement maintenance is infrastructure work, not an autonomous
        Research action.  It therefore does not borrow a user's Research
        approval grant.  The internal compiler only accepts the three
        historical-data preparation task types and stamps an authorization
        mode that public API task builders cannot set.
        """
        spec = dict(task_spec or {})
        task_type = _clean(spec.get("task_type")).upper()
        logical_key = _clean(spec.get("logical_key"))
        workflow_run_id = _clean(spec.get("workflow_run_id"))
        idempotency_key = _clean(spec.get("idempotency_key"))
        payload = dict(spec.get("input") or {})
        if task_type not in SYSTEM_MAINTENANCE_TASK_TYPES:
            raise ValueError(f"unsupported system maintenance task type: {task_type}")
        if not logical_key or not workflow_run_id or not idempotency_key:
            raise ValueError("maintenance task requires logical_key, workflow_run_id, and idempotency_key")
        if not _clean(payload.get("instrument_id")):
            raise ValueError("maintenance task requires instrument_id")
        if not _clean(payload.get("interval")):
            raise ValueError("maintenance task requires interval")
        if not _clean(payload.get("start_time")) or not _clean(payload.get("end_time")):
            raise ValueError("maintenance task requires a bounded start_time and end_time")
        budget = dict(payload.get("budget") or {})
        download_bytes = max(1, int(budget.get("download_bytes") or 20_000_000))
        runtime_seconds = max(1, int(budget.get("runtime_seconds") or 300))
        if download_bytes > 100_000_000 or runtime_seconds > 900:
            raise ValueError("maintenance task exceeds the system data-preparation budget")
        payload["budget"] = {
            "download_bytes": download_bytes,
            "runtime_seconds": runtime_seconds,
        }
        payload["authorization_mode"] = SYSTEM_MAINTENANCE_AUTHORIZATION
        now = _now()
        with self.store.transaction(immediate=True) as conn:
            project_columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(research_projects)").fetchall()
            }
            if "archived_at" in project_columns:
                conn.execute(
                    """INSERT OR IGNORE INTO research_projects(
                           project_id, title, objective, summary_state,
                           current_plan_version, created_by, created_at, updated_at,
                           revision, archived_at
                       ) VALUES (?, ?, ?, 'SYSTEM_MAINTENANCE', 0, ?, ?, ?, 0, ?)""",
                    (
                        SYSTEM_MAINTENANCE_PROJECT_ID,
                        "System Requirement Maintenance",
                        "Backend-owned historical data maintenance for Requirement Library.",
                        "system_requirement_maintenance",
                        now,
                        now,
                        now,
                    ),
                )
            else:
                conn.execute(
                    """INSERT OR IGNORE INTO research_projects(
                           project_id, title, objective, summary_state,
                           current_plan_version, created_by, created_at, updated_at, revision
                       ) VALUES (?, ?, ?, 'SYSTEM_MAINTENANCE', 0, ?, ?, ?, 0)""",
                    (
                        SYSTEM_MAINTENANCE_PROJECT_ID,
                        "System Requirement Maintenance",
                        "Backend-owned historical data maintenance for Requirement Library.",
                        "system_requirement_maintenance",
                        now,
                        now,
                    ),
                )
            existing = conn.execute(
                "SELECT * FROM research_tasks WHERE workflow_run_id=? AND idempotency_key=?",
                (workflow_run_id, idempotency_key),
            ).fetchone()
            if existing:
                expected = {
                    "project_id": SYSTEM_MAINTENANCE_PROJECT_ID,
                    "plan_version": 0,
                    "task_type": task_type,
                    "logical_key": logical_key,
                    "input_json": json_dumps(payload),
                }
                mismatched = [key for key, value in expected.items() if existing[key] != value]
                if mismatched:
                    raise ValueError(
                        "maintenance idempotency_key was reused with different fields: "
                        + ", ".join(mismatched)
                    )
                return self._get_task(str(existing["task_id"]))  # type: ignore[return-value]
            task_id = f"task_{uuid.uuid4().hex}"
            conn.execute(
                """INSERT INTO research_tasks(
                       task_id, project_id, plan_version, workflow_run_id,
                       task_type, logical_key, status, priority, idempotency_key,
                       input_json, expected_output_types_json, max_attempts,
                       timeout_seconds, created_at
                   ) VALUES (?, ?, 0, ?, ?, ?, 'READY', ?, ?, ?, '[]', ?, ?, ?)""",
                (
                    task_id,
                    SYSTEM_MAINTENANCE_PROJECT_ID,
                    workflow_run_id,
                    task_type,
                    logical_key,
                    max(1, min(100, int(spec.get("priority") or 60))),
                    idempotency_key,
                    json_dumps(payload),
                    max(1, min(5, int(spec.get("max_attempts") or 3))),
                    max(1, min(3600, int(spec.get("timeout_seconds") or 900))),
                    now,
                ),
            )
        return self._get_task(task_id)  # type: ignore[return-value]

    def list_tasks(
        self,
        *,
        workflow_run_id: str = "",
        project_id: str = "",
        status: str = "",
        task_type: str = "",
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """List tasks with optional server-side filtering.

        Pass ``status`` and/or ``task_type`` to push those filters into SQL so
        the LIMIT applies *after* filtering — without them every worker had to
        fetch 500 historical rows before Python-side filtering could find READY
        tasks, starving new work when the backlog exceeded the limit.
        """
        clauses = []
        params: list[Any] = []
        if _clean(workflow_run_id):
            clauses.append("workflow_run_id = ?")
            params.append(_clean(workflow_run_id))
        if _clean(project_id):
            clauses.append("project_id = ?")
            params.append(_clean(project_id))
        if _clean(status):
            clauses.append("status = ?")
            params.append(_clean(status).upper())
        if _clean(task_type):
            clauses.append("task_type = ?")
            params.append(_clean(task_type))
        params.append(max(1, min(int(limit), 2000)))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.store.connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM research_tasks{where} ORDER BY priority DESC, created_at, task_id LIMIT ?",
                params,
            ).fetchall()
            task_ids = [str(row["task_id"]) for row in rows]
            dependency_map = {task_id: [] for task_id in task_ids}
            for start in range(0, len(task_ids), 400):
                chunk = task_ids[start:start + 400]
                placeholders = ",".join("?" for _ in chunk)
                dependencies = conn.execute(
                    f"""
                    SELECT task_id, depends_on_task_id
                    FROM research_task_dependencies
                    WHERE task_id IN ({placeholders})
                    ORDER BY task_id, depends_on_task_id
                    """,
                    chunk,
                ).fetchall()
                for dependency in dependencies:
                    dependency_map[str(dependency["task_id"])].append(str(dependency["depends_on_task_id"]))
        result = []
        for row in rows:
            item = self._task_dict(row)
            item["depends_on"] = dependency_map.get(str(row["task_id"]), [])
            result.append(item)
        return result

    def claim_task(self, *, task_id: str, worker_id: str, lease_seconds: int = 300) -> dict[str, Any]:
        worker_id = _clean(worker_id)
        if not worker_id:
            raise ValueError("worker_id is required")
        now = _now()
        with self.store.transaction(immediate=True) as conn:
            self._recover_expired_locked(conn, now)
            row = conn.execute("SELECT * FROM research_tasks WHERE task_id = ?", (_clean(task_id),)).fetchone()
            if not row:
                raise ValueError(f"task not found: {task_id}")
            if str(row["status"]) != "READY":
                raise ValueError(f"task is not READY: {task_id} ({row['status']})")
            next_attempt = int(conn.execute("SELECT COALESCE(MAX(attempt_number), 0) + 1 FROM research_task_attempts WHERE task_id = ?", (_clean(task_id),)).fetchone()[0])
            if next_attempt > int(row["max_attempts"]):
                conn.execute("UPDATE research_tasks SET status = 'FAILED', finished_at = ? WHERE task_id = ?", (now, _clean(task_id)))
                raise ValueError(f"task exceeded max attempts: {task_id}")
            lease_expires = _future(lease_seconds)
            conn.execute(
                """
                UPDATE research_tasks
                SET status = 'RUNNING', lease_owner = ?, lease_expires_at = ?, heartbeat_at = ?, started_at = COALESCE(started_at, ?)
                WHERE task_id = ?
                """,
                (worker_id, lease_expires, now, now, _clean(task_id)),
            )
            conn.execute(
                """
                INSERT INTO research_task_attempts(
                    attempt_id, task_id, attempt_number, status, worker_id, started_at
                ) VALUES (?, ?, ?, 'RUNNING', ?, ?)
                """,
                (f"attempt_{uuid.uuid4().hex}", _clean(task_id), next_attempt, worker_id, now),
            )
        return self._get_task(task_id)  # type: ignore[return-value]

    def heartbeat_task(self, *, task_id: str, worker_id: str, lease_seconds: int = 300) -> dict[str, Any]:
        now = _now()
        with self.store.transaction(immediate=True) as conn:
            affected = conn.execute(
                """
                UPDATE research_tasks
                SET heartbeat_at = ?, lease_expires_at = ?
                WHERE task_id = ? AND status = 'RUNNING' AND lease_owner = ?
                  AND lease_expires_at > ?
                """,
                (now, _future(lease_seconds), _clean(task_id), _clean(worker_id), now),
            ).rowcount
            if not affected:
                raise ValueError("task lease is not owned by worker")
        return self._get_task(task_id)  # type: ignore[return-value]

    def complete_task(self, *, task_id: str, worker_id: str, output: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        now = _now()
        expired = False
        with self.store.transaction(immediate=True) as conn:
            row = conn.execute("SELECT * FROM research_tasks WHERE task_id = ?", (_clean(task_id),)).fetchone()
            if not row or str(row["status"]) != "RUNNING" or str(row["lease_owner"]) != _clean(worker_id):
                raise ValueError("task is not running under this worker")
            if row["lease_expires_at"] and _parse_time(row["lease_expires_at"]) <= _parse_time(now):
                self._expire_task_locked(conn, _clean(task_id), now)
                expired = True
            else:
                conn.execute(
                    """
                    UPDATE research_tasks
                    SET status = 'SUCCEEDED', finished_at = ?, lease_owner = NULL,
                        lease_expires_at = NULL, heartbeat_at = NULL, output_json = ?
                    WHERE task_id = ?
                    """,
                    (now, json_dumps(output or {}), _clean(task_id)),
                )
                conn.execute(
                    """
                    UPDATE research_task_attempts
                    SET status = 'SUCCEEDED', finished_at = ?, output_json = ?
                    WHERE task_id = ? AND status = 'RUNNING'
                    """,
                    (now, json_dumps(output or {}), _clean(task_id)),
                )
                dependents = conn.execute(
                    "SELECT task_id FROM research_task_dependencies WHERE depends_on_task_id = ?",
                    (_clean(task_id),),
                ).fetchall()
                for dependent in dependents:
                    dependent_id = str(dependent[0])
                    pending = conn.execute(
                        """
                        SELECT COUNT(*) FROM research_task_dependencies d
                        JOIN research_tasks t ON t.task_id = d.depends_on_task_id
                        WHERE d.task_id = ? AND t.status != 'SUCCEEDED'
                        """,
                        (dependent_id,),
                    ).fetchone()[0]
                    if int(pending) == 0:
                        conn.execute(
                            "UPDATE research_tasks SET status = 'READY' WHERE task_id = ? AND status = 'PENDING'",
                            (dependent_id,),
                        )
        if expired:
            raise ValueError("task lease expired before completion")
        return self._get_task(task_id)  # type: ignore[return-value]

    def fail_task(self, *, task_id: str, worker_id: str, error: str, retry: bool = True) -> dict[str, Any]:
        now = _now()
        expired = False
        with self.store.transaction(immediate=True) as conn:
            row = conn.execute("SELECT * FROM research_tasks WHERE task_id = ?", (_clean(task_id),)).fetchone()
            if not row or str(row["status"]) != "RUNNING" or str(row["lease_owner"]) != _clean(worker_id):
                raise ValueError("task is not running under this worker")
            if row["lease_expires_at"] and _parse_time(row["lease_expires_at"]) <= _parse_time(now):
                self._expire_task_locked(conn, _clean(task_id), now)
                expired = True
            else:
                attempt = int(conn.execute("SELECT COALESCE(MAX(attempt_number), 0) FROM research_task_attempts WHERE task_id = ?", (_clean(task_id),)).fetchone()[0])
                next_status = "READY" if retry and attempt < int(row["max_attempts"]) else "FAILED"
                error_json = json_dumps({"message": _clean(error)})
                conn.execute(
                    """
                    UPDATE research_tasks
                    SET status = ?, finished_at = CASE WHEN ? = 'FAILED' THEN ? ELSE NULL END,
                        lease_owner = NULL, lease_expires_at = NULL, heartbeat_at = NULL, error_json = ?
                    WHERE task_id = ?
                    """,
                    (next_status, next_status, now, error_json, _clean(task_id)),
                )
                conn.execute(
                    "UPDATE research_task_attempts SET status = 'FAILED', finished_at = ?, error_json = ? WHERE task_id = ? AND status = 'RUNNING'",
                    (now, error_json, _clean(task_id)),
                )
        if expired:
            raise ValueError("task lease expired before failure report")
        return self._get_task(task_id)  # type: ignore[return-value]

    @staticmethod
    def _assert_acyclic_locked(conn: Any, workflow_run_id: str) -> None:
        task_ids = {
            str(row[0]) for row in conn.execute(
                "SELECT task_id FROM research_tasks WHERE workflow_run_id = ?",
                (workflow_run_id,),
            ).fetchall()
        }
        graph = {task_id: set() for task_id in task_ids}
        for row in conn.execute(
            """
            SELECT d.task_id, d.depends_on_task_id
            FROM research_task_dependencies d
            JOIN research_tasks t ON t.task_id = d.task_id
            WHERE t.workflow_run_id = ?
            """,
            (workflow_run_id,),
        ).fetchall():
            graph[str(row[0])].add(str(row[1]))
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise ValueError("task dependency graph contains a cycle")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in graph.get(task_id, set()):
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in sorted(graph):
            visit(task_id)

    @staticmethod
    def _expire_task_locked(conn: Any, task_id: str, now: str) -> None:
        conn.execute(
            "UPDATE research_tasks SET status = 'READY', lease_owner = NULL, lease_expires_at = NULL, heartbeat_at = NULL WHERE task_id = ?",
            (task_id,),
        )
        conn.execute(
            "UPDATE research_task_attempts SET status = 'FAILED', finished_at = ?, error_json = ? WHERE task_id = ? AND status = 'RUNNING'",
            (now, json_dumps({"message": "task lease expired"}), task_id),
        )

    def _get_task(self, task_id: str) -> Optional[dict[str, Any]]:
        with self.store.connection() as conn:
            row = conn.execute("SELECT * FROM research_tasks WHERE task_id = ?", (_clean(task_id),)).fetchone()
            if not row:
                return None
            dependencies = conn.execute(
                "SELECT depends_on_task_id FROM research_task_dependencies WHERE task_id = ? ORDER BY depends_on_task_id",
                (_clean(task_id),),
            ).fetchall()
            attempts = conn.execute(
                "SELECT * FROM research_task_attempts WHERE task_id = ? ORDER BY attempt_number",
                (_clean(task_id),),
            ).fetchall()
        result = self._task_dict(row)
        result["depends_on"] = [str(item[0]) for item in dependencies]
        result["attempts"] = [dict(item) for item in attempts]
        return result

    @staticmethod
    def _task_dict(row: Any) -> dict[str, Any]:
        result = dict(row)
        result["input"] = _loads(result.pop("input_json"), {})
        result["expected_output_types"] = _loads(result.pop("expected_output_types_json"), [])
        result["error"] = _loads(result.pop("error_json"), {})
        result["output"] = _loads(result.pop("output_json"), {})
        return result

    @staticmethod
    def _recover_expired_locked(conn: Any, now: str) -> None:
        rows = conn.execute(
            "SELECT task_id FROM research_tasks WHERE status = 'RUNNING' AND lease_expires_at IS NOT NULL AND lease_expires_at < ?",
            (now,),
        ).fetchall()
        for row in rows:
            ResearchControlPlane._expire_task_locked(conn, str(row[0]), now)
