from __future__ import annotations

import ctypes
import os
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping


MIB = 1024 * 1024


def automatic_queue_status(
    *,
    state: str,
    position: int = 0,
    total: int = 0,
    queued_at: Any = None,
    reason: str = "",
) -> dict[str, Any]:
    """Return the stable queue contract shared by Frontend and Agent APIs."""

    public_state = str(state or "WAITING").upper()
    public_reason = str(reason or "").upper()
    messages = {
        "READY": "任务已就绪，系统会自动选择合适的执行资源。",
        "WAITING": "任务正在排队，系统会在不影响前端的前提下自动执行。",
        "WAITING_RESOURCE": "任务正在等待安全执行窗口，无需手动调整资源。",
        "DISPATCHED": "任务已由系统调度，正在受控环境中执行。",
        "RUNNING": "任务正在受控环境中执行。",
        "TERMINAL": "任务已结束。",
    }
    if public_reason in {
        "FRONTEND_MEMORY_RESERVE",
        "HEAVY_WORKLOAD_ACTIVE",
        "HEAVY_WORKLOAD_MUTUAL_EXCLUSION",
        "WORKER_CONCURRENCY_LIMIT",
    }:
        public_state = "WAITING_RESOURCE"
    return {
        "state": public_state,
        "position": max(0, int(position)),
        "total": max(0, int(total)),
        "mode": "AUTOMATIC",
        "action_required": False,
        "message": messages.get(public_state, messages["WAITING"]),
        "queued_at": queued_at,
        "next_update_seconds": 5 if public_state != "TERMINAL" else 0,
    }


@dataclass(frozen=True)
class MemorySnapshot:
    total_mb: int
    available_mb: int
    frontend_reserve_mb: int

    @property
    def dispatchable_mb(self) -> int:
        return max(0, self.available_mb - self.frontend_reserve_mb)

    def to_dict(self) -> dict[str, int]:
        return {**asdict(self), "dispatchable_mb": self.dispatchable_mb}


@dataclass(frozen=True)
class WorkloadPlan:
    resource_class: str
    worker_memory_mb: int
    estimated_working_set_mb: int
    source_rows: int
    source_bytes: int
    hard_limit_exceeded: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RoutingDecision:
    """An internal execution decision made without user or Agent input.

    Resource limits deliberately live only in this control-plane object.  API
    facades must use :meth:`to_public_dict` so callers never need to choose a
    worker size, process model, or retry strategy.
    """

    priority: int
    workload_kind: str
    resource_class: str
    execution_mode: str
    worker_memory_mb: int
    estimated_working_set_mb: int
    checkpoint_enabled: bool
    dispatch_policy: str
    reason_code: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_public_dict(
        self,
        *,
        state: str,
        position: int = 0,
        total: int = 0,
        queued_at: Any = None,
        reason: str = "",
    ) -> dict[str, Any]:
        return automatic_queue_status(
            state=state,
            position=position,
            total=total,
            queued_at=queued_at,
            reason=reason or self.reason_code,
        )


class IntelligentWorkloadRouter:
    """Convert workload estimates into one automatic execution policy.

    Priority is intentionally inverse to cost for research jobs: small
    interactive work can pass a queued full-market study, while control and
    status endpoints never enter this compute queue at all.
    """

    PRIORITY_LIGHT_RESEARCH = 70
    PRIORITY_HEAVY_RESEARCH = 40
    PRIORITY_BATCH = 20

    def route_research(self, plan: WorkloadPlan) -> RoutingDecision:
        if plan.hard_limit_exceeded:
            return RoutingDecision(
                priority=self.PRIORITY_HEAVY_RESEARCH,
                workload_kind="HEAVY_RESEARCH",
                resource_class="HEAVY",
                execution_mode="PARTITIONED_REQUIRED",
                worker_memory_mb=plan.worker_memory_mb,
                estimated_working_set_mb=plan.estimated_working_set_mb,
                checkpoint_enabled=True,
                # Until the partition executor is available, claim once and
                # fail fast before loading rows.  Leaving this queued forever
                # would be less safe and much less transparent.
                dispatch_policy="PREFLIGHT_ONLY",
                reason_code="PARTITIONED_EXECUTION_REQUIRED",
            )
        if plan.estimated_working_set_mb <= 1024 and plan.source_rows <= 3_000_000:
            # Keep enough headroom for the Python/Arrow runtime itself.  The
            # estimate models research rows, not interpreter and native-library
            # startup, so a 1 GiB process cap is unnecessarily brittle on
            # Windows even for a tiny dataset.
            memory_mb = max(
                2048,
                min(
                    int(os.environ.get("DATATUBE_STANDARD_WORKER_MEMORY_MB", "4096")),
                    int(plan.estimated_working_set_mb * 2.0) + 512,
                ),
            )
            return RoutingDecision(
                priority=self.PRIORITY_LIGHT_RESEARCH,
                workload_kind="LIGHT_RESEARCH",
                resource_class="STANDARD",
                execution_mode="BOUNDED_ISOLATED",
                worker_memory_mb=memory_mb,
                estimated_working_set_mb=plan.estimated_working_set_mb,
                checkpoint_enabled=False,
                dispatch_policy="WHEN_AVAILABLE",
                reason_code="READY",
            )
        return RoutingDecision(
            priority=self.PRIORITY_HEAVY_RESEARCH,
            workload_kind="HEAVY_RESEARCH",
            resource_class="HEAVY",
            execution_mode="BOUNDED_ISOLATED",
            worker_memory_mb=plan.worker_memory_mb,
            estimated_working_set_mb=plan.estimated_working_set_mb,
            checkpoint_enabled=False,
            dispatch_policy="WHEN_AVAILABLE",
            reason_code="READY",
        )

    def route_backtest(self, *, legs: int, estimated_points: int = 0) -> RoutingDecision:
        heavy = int(legs) > 20 or int(estimated_points) > 2_000_000
        memory_mb = max(
            512,
            int(
                os.environ.get(
                    "DATATUBE_BACKTEST_HEAVY_MEMORY_MB" if heavy else "DATATUBE_BACKTEST_WORKER_MEMORY_MB",
                    "4096" if heavy else "2048",
                )
            ),
        )
        return RoutingDecision(
            priority=self.PRIORITY_HEAVY_RESEARCH if heavy else self.PRIORITY_LIGHT_RESEARCH,
            workload_kind="HEAVY_RESEARCH" if heavy else "LIGHT_RESEARCH",
            resource_class="HEAVY" if heavy else "STANDARD",
            execution_mode="BOUNDED_ISOLATED",
            worker_memory_mb=memory_mb,
            estimated_working_set_mb=0,
            checkpoint_enabled=False,
            dispatch_policy="WHEN_AVAILABLE",
            reason_code="READY",
        )


class ResourceAdmissionController:
    """Process-local admission gate shared by all Web-launched workers.

    The durable queue lives in SQLite.  This gate only decides whether the Web
    process may launch the next isolated child while preserving memory for
    interactive requests.  It never runs business computation itself.
    """

    _condition = threading.Condition()
    _active: dict[str, tuple[str, int]] = {}

    def __init__(self, *, frontend_reserve_mb: int | None = None):
        self.frontend_reserve_mb = max(
            1024,
            int(
                frontend_reserve_mb
                or os.environ.get("DATATUBE_FRONTEND_MEMORY_RESERVE_MB", "6144")
            ),
        )

    def memory_snapshot(self) -> MemorySnapshot:
        total, available = _physical_memory_mb()
        return MemorySnapshot(total, available, self.frontend_reserve_mb)

    def can_dispatch(self, resource_class: str, worker_memory_mb: int) -> tuple[bool, str]:
        resource_class = str(resource_class or "STANDARD").upper()
        worker_memory_mb = max(256, int(worker_memory_mb))
        snapshot = self.memory_snapshot()
        with self._condition:
            active = dict(self._active)
        if resource_class == "HEAVY" and active:
            return False, "HEAVY_WORKLOAD_MUTUAL_EXCLUSION"
        if resource_class != "HEAVY" and any(kind == "HEAVY" for kind, _ in active.values()):
            return False, "HEAVY_WORKLOAD_ACTIVE"
        standard_limit = max(
            1, int(os.environ.get("DATATUBE_STANDARD_WORKER_LIMIT", "2"))
        )
        if resource_class != "HEAVY" and len(active) >= standard_limit:
            return False, "WORKER_CONCURRENCY_LIMIT"
        committed_mb = sum(memory for _, memory in active.values())
        required_mb = worker_memory_mb + committed_mb
        if snapshot.dispatchable_mb < required_mb:
            return False, "FRONTEND_MEMORY_RESERVE"
        return True, "READY"

    def acquire(self, token: str, resource_class: str, worker_memory_mb: int) -> bool:
        token = str(token).strip()
        if not token:
            raise ValueError("admission token is required")
        allowed, _ = self.can_dispatch(resource_class, worker_memory_mb)
        if not allowed:
            return False
        with self._condition:
            # Recheck while holding the shared lock to avoid two dispatchers
            # passing the same capacity snapshot concurrently.
            allowed, _ = self.can_dispatch(resource_class, worker_memory_mb)
            if not allowed:
                return False
            self._active[token] = (
                str(resource_class or "STANDARD").upper(),
                max(256, int(worker_memory_mb)),
            )
            return True

    def release(self, token: str) -> None:
        with self._condition:
            self._active.pop(str(token).strip(), None)
            self._condition.notify_all()

    @contextmanager
    def lease(
        self, token: str, resource_class: str, worker_memory_mb: int
    ) -> Iterator[bool]:
        acquired = self.acquire(token, resource_class, worker_memory_mb)
        try:
            yield acquired
        finally:
            if acquired:
                self.release(token)

    @classmethod
    def active_snapshot(cls) -> dict[str, dict[str, Any]]:
        with cls._condition:
            return {
                token: {"resource_class": item[0], "worker_memory_mb": item[1]}
                for token, item in cls._active.items()
            }


class ResearchWorkloadPlanner:
    """Estimate the current Python engine's peak memory before row loading."""

    def __init__(self, store: Any):
        self.store = store

    def plan(self, run: Mapping[str, Any]) -> WorkloadPlan:
        frozen = dict(run.get("frozen_input") or {})
        if not frozen and run.get("bundle_id"):
            from .research_run_service import ResearchRunService

            bundle = ResearchRunService(self.store).get_bundle(str(run["bundle_id"])) or {}
            frozen = dict(bundle.get("canonical_payload") or {})
        closure = dict(frozen.get("input_closure") or {})
        manifest_ids = [
            str(item).strip()
            for item in closure.get("exact_manifest_ids") or []
            if str(item).strip()
        ]
        source_rows = 0
        source_bytes = 0
        universe_size = 0
        snapshot_id = str(closure.get("universe_snapshot_id") or "").strip()
        if snapshot_id:
            from .universe_service import UniverseService

            snapshot = UniverseService(self.store).get_snapshot(snapshot_id)
            universe_size = len(snapshot.actual_instrument_ids) if snapshot else 0
        history_start = ""
        history_end = ""
        requirement_set_id = str(closure.get("requirement_set_id") or "").strip()
        if requirement_set_id:
            from .requirement_compiler import RequirementCompiler

            requirement_set = RequirementCompiler(self.store).get(requirement_set_id)
            if requirement_set:
                starts = [
                    str(item.history_start or "").strip()
                    for item in requirement_set.requirements
                    if str(item.history_start or "").strip()
                ]
                ends = [
                    str(item.history_end or "").strip()
                    for item in requirement_set.requirements
                    if str(item.history_end or "").strip()
                ]
                history_start = min(starts) if starts else ""
                history_end = max(ends) if ends else ""
        if manifest_ids:
            placeholders = ",".join("?" for _ in manifest_ids)
            date_clauses = []
            params: list[Any] = list(manifest_ids)
            if history_start:
                date_clauses.append("(p.end_time IS NULL OR p.end_time >= ?)")
                params.append(history_start)
            if history_end:
                date_clauses.append("(p.start_time IS NULL OR p.start_time <= ?)")
                params.append(history_end)
            date_sql = (" AND " + " AND ".join(date_clauses)) if date_clauses else ""
            with self.store.connection() as conn:
                rows = conn.execute(
                    f"""
                    SELECT p.manifest_id,
                           COALESCE(SUM(p.row_count), 0) AS row_count,
                           COALESCE(SUM(p.file_size), 0) AS file_size,
                           COALESCE(c.instrument_id, '') AS instrument_id,
                           COALESCE(c.start_time, '') AS dataset_start,
                           COALESCE(c.end_time, '') AS dataset_end
                    FROM dataset_partitions AS p
                    JOIN dataset_manifests AS m ON m.manifest_id=p.manifest_id
                    LEFT JOIN dataset_catalog AS c ON c.dataset_id=m.dataset_id
                    WHERE p.manifest_id IN ({placeholders}){date_sql}
                    GROUP BY p.manifest_id, c.instrument_id
                    """,
                    params,
                ).fetchall()
            collection_baseline = max(
                1, int(os.environ.get("DATATUBE_COLLECTION_INSTRUMENT_BASELINE", "5000"))
            )
            for row in rows:
                scale = 1.0
                if str(row["instrument_id"] or "").upper().endswith(":ALL") and universe_size:
                    scale = min(1.0, universe_size / collection_baseline)
                requested_start = _parse_iso(history_start)
                requested_end = _parse_iso(history_end)
                dataset_start = _parse_iso(str(row["dataset_start"] or ""))
                dataset_end = _parse_iso(str(row["dataset_end"] or ""))
                if requested_start and requested_end and dataset_start and dataset_end:
                    requested_days = max(1.0, (requested_end - requested_start).total_seconds() / 86400)
                    dataset_days = max(1.0, (dataset_end - dataset_start).total_seconds() / 86400)
                    # A 1.5 density margin keeps the estimate conservative for
                    # recent periods without treating a century-wide manifest
                    # as fully resident for a one-year study.
                    scale *= min(1.0, (requested_days / dataset_days) * 1.5)
                source_rows += int(int(row["row_count"] or 0) * scale)
                source_bytes += int(int(row["file_size"] or 0) * scale)

        worker_memory_mb = max(
            512, int(os.environ.get("DATATUBE_RESEARCH_WORKER_MEMORY_MB", "8192"))
        )
        # The legacy executor converts Arrow rows into Python dicts, groups
        # them, then retains additional scoped/factor copies.  Empirical safe
        # lower bounds are therefore much larger than compressed Parquet.
        row_model_bytes = source_rows * max(
            96, int(os.environ.get("DATATUBE_RESEARCH_ROW_MEMORY_BYTES", "192"))
        )
        parquet_model_bytes = source_bytes * 4
        estimated_mb = max(256, int(max(row_model_bytes, parquet_model_bytes) / MIB))
        hard_limit = int(worker_memory_mb * 0.82)
        exceeded = estimated_mb > hard_limit
        resource_class = (
            "HEAVY"
            if estimated_mb >= 2048 or source_rows >= 10_000_000
            else "STANDARD"
        )
        reason = (
            "PARTITIONED_EXECUTION_REQUIRED"
            if exceeded
            else "READY"
        )
        return WorkloadPlan(
            resource_class=resource_class,
            worker_memory_mb=worker_memory_mb,
            estimated_working_set_mb=estimated_mb,
            source_rows=source_rows,
            source_bytes=source_bytes,
            hard_limit_exceeded=exceeded,
            reason=reason,
        )


def worker_log_path(db_path: str | Path, kind: str, job_id: str, name: str) -> Path:
    safe_kind = "".join(ch for ch in str(kind) if ch.isalnum() or ch in "-_") or "jobs"
    safe_id = "".join(ch for ch in str(job_id) if ch.isalnum() or ch in "-_") or "unknown"
    safe_name = "".join(ch for ch in str(name) if ch.isalnum() or ch in "-_.") or "worker.log"
    return Path(db_path).resolve().parent / "worker_logs" / safe_kind / safe_id / safe_name


def _physical_memory_mb() -> tuple[int, int]:
    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullTotalPhys / MIB), int(status.ullAvailPhys / MIB)
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        total = int(os.sysconf("SC_PHYS_PAGES")) * page_size
        available = int(os.sysconf("SC_AVPHYS_PAGES")) * page_size
        return int(total / MIB), int(available / MIB)
    except (AttributeError, OSError, ValueError):
        # Conservative fallback: keep admission closed rather than risk the UI.
        return 0, 0


def _parse_iso(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


__all__ = [
    "automatic_queue_status",
    "IntelligentWorkloadRouter",
    "MemorySnapshot",
    "ResearchWorkloadPlanner",
    "ResourceAdmissionController",
    "RoutingDecision",
    "WorkloadPlan",
    "worker_log_path",
]
