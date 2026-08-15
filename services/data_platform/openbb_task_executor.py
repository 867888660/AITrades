from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import threading

from requests import RequestException

from .openbb_history_adapter import OpenBBEquityHistoryAdapter
from .research_control_plane import (
    ResearchControlPlane,
    SYSTEM_MAINTENANCE_AUTHORIZATION,
    SYSTEM_MAINTENANCE_PROJECT_ID,
    _parse_time,
)
from .store import DataPlatformStore


OPENBB_EXPORT_TASK_TYPE = "OPENBB_EQUITY_DAILY_EXPORT"


def _clean_list(value: Any, *, upper: bool = False) -> set[str]:
    items = value if isinstance(value, list) else []
    result = {str(item).strip() for item in items if str(item).strip()}
    return {item.upper() for item in result} if upper else {item.lower() for item in result}


class OpenBBResearchTaskExecutor:
    """Execute OpenBB exports only under an approved Research task lease."""

    def __init__(
        self,
        store: DataPlatformStore,
        settings: dict[str, Any],
        *,
        adapter: OpenBBEquityHistoryAdapter | None = None,
    ):
        self.store = store
        self.control = ResearchControlPlane(store)
        self.adapter = adapter or OpenBBEquityHistoryAdapter(settings, store=store)

    @staticmethod
    def _validate_scope(task: dict[str, Any], grant: dict[str, Any]) -> dict[str, Any]:
        payload = task.get("input") if isinstance(task.get("input"), dict) else {}
        grant_scope = grant.get("scope") if isinstance(grant.get("scope"), dict) else {}
        if grant.get("project_id") != task.get("project_id") or int(grant.get("plan_version") or 0) != int(task.get("plan_version") or 0):
            raise PermissionError("approval grant does not belong to this task plan")
        if str(grant.get("status")) != "ACTIVE":
            raise PermissionError("approval grant is not active")

        symbol = str(payload.get("symbol") or "").strip().upper()
        venue = str(payload.get("venue") or "").strip().upper()
        provider = str(payload.get("provider") or "").strip().lower()
        source_policy = payload.get("source_policy") if isinstance(payload.get("source_policy"), dict) else {}
        policy_providers = [str(item).strip().lower() for item in source_policy.get("providers", []) if str(item).strip()]
        requested_providers = set(policy_providers or ([provider] if provider else []))
        if not symbol or not venue:
            raise ValueError("OpenBB export task requires symbol and venue")
        asset_classes = _clean_list(grant_scope.get("asset_classes"))
        if asset_classes and "equity" not in asset_classes:
            raise PermissionError("approval grant does not allow equity data")
        venues = _clean_list(grant_scope.get("venues"), upper=True)
        if venues and venue not in venues:
            raise PermissionError(f"approval grant does not allow venue: {venue}")
        symbols = _clean_list(grant_scope.get("symbols"), upper=True)
        if symbols and symbol not in symbols:
            raise PermissionError(f"approval grant does not allow symbol: {symbol}")
        providers = _clean_list(grant_scope.get("providers"))
        disallowed = sorted(requested_providers - providers) if providers else []
        if disallowed:
            raise PermissionError(f"approval grant does not allow provider: {', '.join(disallowed)}")
        endpoints = _clean_list(grant_scope.get("endpoints"))
        if endpoints and "equity.price.historical" not in endpoints:
            raise PermissionError("approval grant does not allow equity.price.historical")
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
        parts = instrument_id.split(":", 2)
        symbol = str(payload.get("symbol") or "").strip().upper()
        venue = str(payload.get("venue") or "").strip().upper()
        interval = str(payload.get("interval") or "").strip().lower()
        qualified_equity = (
            len(parts) == 3
            and parts[0].lower() == "equity"
            and parts[1].upper() == venue
            and parts[2].upper() == symbol
        )
        bare_equity = len(parts) == 1 and parts[0].upper() == symbol
        if not qualified_equity and not bare_equity:
            raise ValueError("OpenBB maintenance requires a matching equity instrument")
        if interval != "1d":
            raise PermissionError("maintenance task does not match its equity instrument")
        return payload

    @staticmethod
    def _provider_sequence(payload: dict[str, Any]) -> list[str]:
        source_policy = payload.get("source_policy") if isinstance(payload.get("source_policy"), dict) else {}
        mode = str(source_policy.get("mode") or "FIXED").strip().upper()
        if mode not in {"FIXED", "PRIMARY_FALLBACK"}:
            raise ValueError("OpenBB export source policy must be FIXED or PRIMARY_FALLBACK")
        providers = [str(item).strip().lower() for item in source_policy.get("providers", []) if str(item).strip()]
        default_provider = str(payload.get("provider") or "").strip().lower()
        if not providers and default_provider:
            providers = [default_provider]
        providers = list(dict.fromkeys(providers))
        if not providers:
            raise ValueError("OpenBB export requires at least one provider")
        if mode == "FIXED" and len(providers) != 1:
            raise ValueError("FIXED OpenBB export requires exactly one provider")
        return providers

    @staticmethod
    def _fallback_eligible(exc: Exception) -> bool:
        if isinstance(exc, RequestException):
            return True
        text = str(exc).lower()
        return isinstance(exc, ValueError) and (
            "no completed openbb daily bars" in text
            or "no openbb" in text and "found" in text
            or "returned an invalid historical results payload" in text
        )

    def execute(self, *, task_id: str, worker_id: str, lease_seconds: int = 300) -> dict[str, Any]:
        task = self.control.claim_task(task_id=task_id, worker_id=worker_id, lease_seconds=lease_seconds)
        reservation_id = ""
        heartbeat_stop = threading.Event()
        heartbeat_errors: list[Exception] = []
        heartbeat_thread: threading.Thread | None = None
        try:
            if str(task.get("task_type") or "").upper() != OPENBB_EXPORT_TASK_TYPE:
                raise ValueError(f"unsupported OpenBB task type: {task.get('task_type')}")
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
                    raise ValueError("OpenBB export task requires grant_id")
                grant = self.control.get_grant(grant_id)
                if not grant:
                    raise PermissionError(f"approval grant not found: {grant_id}")
                payload = self._validate_scope(task, grant)
            budget = payload.get("budget") if isinstance(payload.get("budget"), dict) else {}
            if not maintenance:
                reservation = self.control.reserve_budget(
                    grant_id=grant_id,
                    idempotency_key=f"openbb-export:{task_id}",
                    # Data preparation is bounded by download/runtime budgets. It
                    # must not consume the separate formal backtest-run budget.
                    runs=0,
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
                    except Exception as heartbeat_exc:
                        heartbeat_errors.append(heartbeat_exc)
                        heartbeat_stop.set()
                        return

            heartbeat_thread = threading.Thread(
                target=maintain_lease, name=f"openbb-heartbeat-{task_id}", daemon=True
            )
            heartbeat_thread.start()
            providers = self._provider_sequence(payload)
            attempts: list[dict[str, str]] = []
            result = None
            for index, provider in enumerate(providers):
                try:
                    result = self.adapter.export({**payload, "provider": provider})
                    attempts.append({"provider": provider, "status": "SUCCEEDED"})
                    break
                except Exception as provider_exc:
                    attempts.append({"provider": provider, "status": "FAILED", "error": str(provider_exc)[:500]})
                    if index == len(providers) - 1 or not self._fallback_eligible(provider_exc):
                        raise
            if result is None:
                raise RuntimeError("all OpenBB providers failed")
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=2.0)
            if heartbeat_errors:
                raise RuntimeError(f"OpenBB task lease heartbeat failed: {heartbeat_errors[0]}")
            if reservation_id:
                self.control.consume_reservation(reservation_id)
            output = {
                "dataset_id": result["dataset_id"],
                "manifest_id": result["manifest"].manifest_id,
                "instrument_id": result["instrument_id"],
                "row_count": result["row_count"],
                "gateway": result["gateway"],
                "upstream_provider": result["upstream_provider"],
                "adjustment": result["adjustment"],
                "reservation_id": reservation_id,
                "source_attempts": attempts,
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
                # The lease may already have expired or been recovered.  Keep
                # the original provider/control error as the worker outcome.
                pass
            raise


class OpenBBResearchWorker:
    """Small worker facade that reuses ResearchControlPlane task claiming."""

    def __init__(self, executor: OpenBBResearchTaskExecutor, worker_id: str):
        self.executor = executor
        self.worker_id = str(worker_id).strip()
        if not self.worker_id:
            raise ValueError("worker_id is required")

    def run_once(self, *, lease_seconds: int = 300) -> dict[str, Any]:
        # Filter by status and task_type in SQL so the LIMIT applies after
        # filtering; previously a Python-side filter on a 500-row unfiltered
        # result could silently starve new READY tasks behind a large backlog.
        ready = list(self.executor.control.list_tasks(
            status="READY", task_type=OPENBB_EXPORT_TASK_TYPE, limit=500
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
        tasks = list(self.executor.control.list_tasks(task_type=OPENBB_EXPORT_TASK_TYPE, limit=2000))
        counts: dict[str, int] = {}
        for task in tasks:
            s = str(task.get("status") or "UNKNOWN")
            counts[s] = counts.get(s, 0) + 1
        ready_tasks = [t for t in tasks if t.get("status") == "READY"]
        active = [t for t in tasks if t.get("status") in {"READY", "RUNNING", "PENDING"}]
        failures = [t for t in tasks if t.get("status") == "FAILED"]
        # Stall diagnostics: queue_depth and age of the oldest READY task let
        # downstream monitoring detect worker starvation without requiring direct
        # DB access or agent-invented workarounds.
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
            "task_type": OPENBB_EXPORT_TASK_TYPE,
            "counts": counts,
            "queue_depth": len(ready_tasks),
            "oldest_ready_age_seconds": oldest_ready_age_seconds,
            "active": active[:100],
            "recent_failures": failures[-50:],
            "total": len(tasks),
        }
