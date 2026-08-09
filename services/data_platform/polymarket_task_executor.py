from __future__ import annotations

from datetime import datetime, timedelta, timezone
import threading
from typing import Any

from .polymarket_history import PolymarketHistoryPreparer
from .provenance_service import ManifestProvenanceService
from .research_control_plane import (
    ResearchControlPlane,
    SYSTEM_MAINTENANCE_AUTHORIZATION,
    SYSTEM_MAINTENANCE_PROJECT_ID,
    _parse_time,
)
from .store import DataPlatformStore


POLYMARKET_EXPORT_TASK_TYPE = "POLYMARKET_PRICE_HISTORY_EXPORT"


def _clean_set(value: Any, *, upper: bool = False) -> set[str]:
    items = value if isinstance(value, list) else []
    result = {str(item).strip() for item in items if str(item).strip()}
    return {item.upper() for item in result} if upper else {item.lower() for item in result}


class PolymarketResearchTaskExecutor:
    """Prepare outcome-price history only under an approved Research task lease."""

    def __init__(
        self,
        store: DataPlatformStore,
        *,
        preparer: PolymarketHistoryPreparer | None = None,
    ):
        self.store = store
        self.control = ResearchControlPlane(store)
        self.preparer = preparer or PolymarketHistoryPreparer(store)

    @staticmethod
    def _validate_scope(task: dict[str, Any], grant: dict[str, Any]) -> dict[str, Any]:
        payload = task.get("input") if isinstance(task.get("input"), dict) else {}
        scope = grant.get("scope") if isinstance(grant.get("scope"), dict) else {}
        if grant.get("project_id") != task.get("project_id") or int(grant.get("plan_version") or 0) != int(task.get("plan_version") or 0):
            raise PermissionError("approval grant does not belong to this task plan")
        if str(grant.get("status")) != "ACTIVE":
            raise PermissionError("approval grant is not active")

        instrument_id = str(payload.get("instrument_id") or "").strip()
        interval = str(payload.get("interval") or "").strip().lower()
        if not instrument_id.lower().startswith("polymarket_binary:polymarket:"):
            raise ValueError("Polymarket export task requires a Polymarket outcome instrument")
        asset_classes = _clean_set(scope.get("asset_classes"))
        if asset_classes and "polymarket_binary" not in asset_classes:
            raise PermissionError("approval grant does not allow Polymarket binary data")
        venues = _clean_set(scope.get("venues"), upper=True)
        if venues and "POLYMARKET" not in venues:
            raise PermissionError("approval grant does not allow POLYMARKET")
        providers = _clean_set(scope.get("providers"))
        if providers and "polymarket" not in providers:
            raise PermissionError("approval grant does not allow provider: POLYMARKET")
        instruments = {str(item).strip() for item in scope.get("allowed_instrument_ids", []) if str(item).strip()}
        if instruments and instrument_id not in instruments:
            raise PermissionError("approval grant does not allow this Polymarket outcome")
        intervals = _clean_set(scope.get("intervals") or scope.get("allowed_intervals"))
        if intervals and interval not in intervals:
            raise PermissionError(f"approval grant does not allow interval: {interval}")
        endpoints = _clean_set(scope.get("endpoints"))
        if endpoints and "polymarket.price_history" not in endpoints:
            raise PermissionError("approval grant does not allow polymarket.price_history")
        return payload

    @staticmethod
    def _validate_maintenance_scope(task: dict[str, Any]) -> dict[str, Any]:
        payload = task.get("input") if isinstance(task.get("input"), dict) else {}
        if (
            task.get("project_id") != SYSTEM_MAINTENANCE_PROJECT_ID
            or payload.get("authorization_mode") != SYSTEM_MAINTENANCE_AUTHORIZATION
        ):
            raise PermissionError("invalid system Requirement maintenance task")
        instrument_id = str(payload.get("instrument_id") or "").strip()
        if not instrument_id.lower().startswith("polymarket_binary:polymarket:"):
            raise ValueError("Polymarket maintenance requires an outcome instrument")
        if not str(payload.get("interval") or "").strip():
            raise ValueError("Polymarket maintenance requires an interval")
        return payload

    def execute(self, *, task_id: str, worker_id: str, lease_seconds: int = 300) -> dict[str, Any]:
        task = self.control.claim_task(task_id=task_id, worker_id=worker_id, lease_seconds=lease_seconds)
        reservation_id = ""
        heartbeat_stop = threading.Event()
        heartbeat_errors: list[Exception] = []
        heartbeat_thread: threading.Thread | None = None
        try:
            if str(task.get("task_type") or "").upper() != POLYMARKET_EXPORT_TASK_TYPE:
                raise ValueError(f"unsupported Polymarket task type: {task.get('task_type')}")
            task_input = task.get("input") if isinstance(task.get("input"), dict) else {}
            maintenance = (
                task.get("project_id") == SYSTEM_MAINTENANCE_PROJECT_ID
                and task_input.get("authorization_mode") == SYSTEM_MAINTENANCE_AUTHORIZATION
            )
            grant_id = str(task_input.get("grant_id") or "").strip()
            if maintenance:
                payload = self._validate_maintenance_scope(task)
            else:
                if not grant_id:
                    raise ValueError("Polymarket export task requires grant_id")
                grant = self.control.get_grant(grant_id)
                if not grant:
                    raise PermissionError(f"approval grant not found: {grant_id}")
                payload = self._validate_scope(task, grant)
            budget = payload.get("budget") if isinstance(payload.get("budget"), dict) else {}
            if not maintenance:
                reservation = self.control.reserve_budget(
                    grant_id=grant_id,
                    idempotency_key=f"polymarket-export:{task_id}",
                    runs=1,
                    download_bytes=max(1, int(budget.get("download_bytes") or 10_000_000)),
                    runtime_seconds=max(1, int(budget.get("runtime_seconds") or lease_seconds)),
                )
                reservation_id = str(reservation["reservation_id"])
            heartbeat_interval = max(1, min(60, lease_seconds // 3))

            def maintain_lease() -> None:
                while not heartbeat_stop.wait(heartbeat_interval):
                    try:
                        self.control.heartbeat_task(
                            task_id=task_id, worker_id=worker_id, lease_seconds=lease_seconds
                        )
                    except Exception as exc:
                        heartbeat_errors.append(exc)
                        heartbeat_stop.set()
                        return

            heartbeat_thread = threading.Thread(
                target=maintain_lease, name=f"polymarket-heartbeat-{task_id}", daemon=True
            )
            heartbeat_thread.start()
            result = self.preparer.prepare(payload)
            provenance_service = ManifestProvenanceService(self.store)
            manifest_id = result["manifest"]["manifest_id"]
            # The immutable commit layer may deduplicate a request to an
            # existing Manifest.  In that case its original provenance is
            # authoritative; a wider retry request must not try to rewrite it.
            provenance = provenance_service.get(manifest_id) or provenance_service.record(
                manifest_id=manifest_id,
                dataset_id=result["dataset_id"],
                gateway="DATATUBE",
                upstream_provider="polymarket",
                endpoint="polymarket.price_history",
                request={
                    "instrument_id": payload["instrument_id"],
                    "interval": payload["interval"],
                    "start_time": payload.get("start_time"),
                    "end_time": payload.get("end_time"),
                    "latest_available": bool(payload.get("latest_available")),
                },
                gateway_version="polymarket_history.v2",
                provider_version="clob_prices_history",
                source_policy={"mode": "FIXED", "providers": ["polymarket"]},
            )
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=2.0)
            if heartbeat_errors:
                raise RuntimeError(f"Polymarket task lease heartbeat failed: {heartbeat_errors[0]}")
            if reservation_id:
                self.control.consume_reservation(reservation_id)
            availability_adjustment = None
            requested_start = str(payload.get("start_time") or "").strip()
            available_from = str(result.get("start_time") or "").strip()
            interval_seconds = {
                "1m": 60,
                "5m": 300,
                "15m": 900,
                "1h": 3600,
                "4h": 14_400,
                "1d": 86_400,
            }.get(str(payload.get("interval") or "").lower(), 0)
            if requested_start and available_from:
                requested_time = datetime.fromisoformat(
                    requested_start.replace("Z", "+00:00")
                )
                available_time = datetime.fromisoformat(
                    available_from.replace("Z", "+00:00")
                )
                if requested_time.tzinfo is None:
                    requested_time = requested_time.replace(tzinfo=timezone.utc)
                if available_time.tzinfo is None:
                    available_time = available_time.replace(tzinfo=timezone.utc)
                if available_time > requested_time + timedelta(
                    seconds=interval_seconds
                ):
                    availability_adjustment = {
                        "code": "DATA_AVAILABLE_AFTER_REQUEST_START",
                        "requested_start": requested_time.astimezone(
                            timezone.utc
                        ).isoformat(),
                        "available_from": available_time.astimezone(
                            timezone.utc
                        ).isoformat(),
                        "message": (
                            "All available Polymarket price history was prepared. "
                            f"Provider data begins at {available_time.astimezone(timezone.utc).isoformat()}."
                        ),
                    }
            output = {
                "dataset_id": result["dataset_id"],
                "manifest_id": result["manifest"]["manifest_id"],
                "instrument_id": payload["instrument_id"],
                "row_count": result["row_count"],
                "reservation_id": reservation_id,
                "provenance": provenance,
                "availability_adjustment": availability_adjustment,
            }
            return self.control.complete_task(task_id=task_id, worker_id=worker_id, output=output)
        except Exception as exc:
            heartbeat_stop.set()
            if heartbeat_thread and heartbeat_thread.is_alive():
                heartbeat_thread.join(timeout=2.0)
            if reservation_id:
                try:
                    reservation = self.control.get_reservation(reservation_id)
                    if reservation and reservation.get("status") == "RESERVED":
                        self.control.release_reservation(reservation_id)
                except Exception:
                    pass
            try:
                self.control.fail_task(task_id=task_id, worker_id=worker_id, error=str(exc), retry=False)
            except Exception:
                pass
            raise


class PolymarketResearchWorker:
    def __init__(self, executor: PolymarketResearchTaskExecutor, worker_id: str):
        self.executor = executor
        self.worker_id = str(worker_id).strip()
        if not self.worker_id:
            raise ValueError("worker_id is required")

    def run_once(self, *, lease_seconds: int = 300) -> dict[str, Any]:
        ready = list(self.executor.control.list_tasks(
            status="READY", task_type=POLYMARKET_EXPORT_TASK_TYPE, limit=500
        ))
        ready.sort(key=lambda item: (-int(item.get("priority") or 0), str(item.get("created_at") or ""), str(item.get("task_id") or "")))
        for task in ready:
            try:
                completed = self.executor.execute(
                    task_id=str(task["task_id"]), worker_id=self.worker_id, lease_seconds=lease_seconds
                )
                return {"status": "EXECUTED", "task": completed}
            except ValueError as exc:
                if "task is not READY" in str(exc):
                    continue
                raise
        return {"status": "IDLE", "task": None}

    def status(self) -> dict[str, Any]:
        tasks = list(self.executor.control.list_tasks(task_type=POLYMARKET_EXPORT_TASK_TYPE, limit=2000))
        counts: dict[str, int] = {}
        for task in tasks:
            s = str(task.get("status") or "UNKNOWN")
            counts[s] = counts.get(s, 0) + 1
        ready_tasks = [t for t in tasks if t.get("status") == "READY"]
        oldest_ready_age_seconds: float | None = None
        if ready_tasks:
            oldest_ts = min(
                (str(t.get("created_at")) for t in ready_tasks if t.get("created_at")),
                default="",
            )
            if oldest_ts:
                try:
                    oldest_ready_age_seconds = (datetime.now(timezone.utc) - _parse_time(oldest_ts)).total_seconds()
                except ValueError:
                    pass
        return {
            "worker_id": self.worker_id,
            "task_type": POLYMARKET_EXPORT_TASK_TYPE,
            "counts": counts,
            "queue_depth": len(ready_tasks),
            "oldest_ready_age_seconds": oldest_ready_age_seconds,
            "active": [t for t in tasks if t.get("status") in {"READY", "RUNNING", "PENDING"}][:100],
            "recent_failures": [t for t in tasks if t.get("status") == "FAILED"][-50:],
            "total": len(tasks),
        }
