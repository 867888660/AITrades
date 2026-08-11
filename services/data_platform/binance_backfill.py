from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from requests import RequestException

from .binance_history_adapter import BinanceHistoryAdapter
from services.history_storage_service import get_history_workspace_db_path
from .provenance_service import ManifestProvenanceService
from .research_control_plane import (
    ResearchControlPlane,
    SYSTEM_MAINTENANCE_AUTHORIZATION,
    SYSTEM_MAINTENANCE_PROJECT_ID,
)
from .store import BASE_DIR, DataPlatformStore, json_dumps, utc_now


BINANCE_BACKFILL_TASK_TYPE = "BINANCE_BARS_BACKFILL"
SUPPORTED_INTERVALS = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d"}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _parse_time(value: Any) -> datetime:
    parsed = datetime.fromisoformat(_clean(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_from_ms(value: int) -> str:
    return datetime.fromtimestamp(int(value) / 1000.0, timezone.utc).isoformat()


def _ms(value: Any) -> int:
    return int(_parse_time(value).timestamp() * 1000)


def interval_milliseconds(interval: str) -> int:
    interval = _clean(interval).lower()
    if interval not in SUPPORTED_INTERVALS:
        raise ValueError(f"unsupported Binance interval: {interval}")
    unit = interval[-1]
    value = int(interval[:-1])
    return value * {"m": 60_000, "h": 3_600_000, "d": 86_400_000}[unit]


def last_complete_open_ms(interval: str, now: datetime | None = None) -> int:
    interval_ms = interval_milliseconds(interval)
    current_ms = int((now or datetime.now(timezone.utc)).timestamp() * 1000)
    return current_ms // interval_ms * interval_ms - interval_ms


def _hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


class BinanceGapDetector:
    """Detect exact missing open-time ranges; no tolerance or forward fill."""

    @staticmethod
    def detect(
        *,
        start_time: str,
        end_time: str,
        interval: str,
        observed_open_times: Iterable[int | str],
    ) -> dict[str, Any]:
        interval_ms = interval_milliseconds(interval)
        start_ms = _ms(start_time)
        end_ms = _ms(end_time)
        aligned_start = ((start_ms + interval_ms - 1) // interval_ms) * interval_ms
        aligned_end = end_ms // interval_ms * interval_ms
        if aligned_start > aligned_end:
            raise ValueError("gap detection window contains no aligned completed bars")
        normalized: list[int] = []
        for item in observed_open_times:
            value = int(item) if isinstance(item, int) else _ms(item)
            normalized.append(value)
        in_range = [item for item in normalized if aligned_start <= item <= aligned_end]
        unique = set(in_range)
        duplicates = len(in_range) - len(unique)
        misaligned = sorted(item for item in unique if (item - aligned_start) % interval_ms != 0)
        valid = {item for item in unique if not (item - aligned_start) % interval_ms}
        expected_count = (aligned_end - aligned_start) // interval_ms + 1
        missing_ranges: list[dict[str, Any]] = []
        range_start: int | None = None
        previous_missing: int | None = None
        missing_count = 0
        for expected in range(aligned_start, aligned_end + 1, interval_ms):
            if expected not in valid:
                missing_count += 1
                if range_start is None:
                    range_start = expected
                previous_missing = expected
            elif range_start is not None and previous_missing is not None:
                missing_ranges.append({
                    "start_time": _iso_from_ms(range_start),
                    "end_time": _iso_from_ms(previous_missing),
                    "bar_count": (previous_missing - range_start) // interval_ms + 1,
                })
                range_start = None
                previous_missing = None
        if range_start is not None and previous_missing is not None:
            missing_ranges.append({
                "start_time": _iso_from_ms(range_start),
                "end_time": _iso_from_ms(previous_missing),
                "bar_count": (previous_missing - range_start) // interval_ms + 1,
            })
        return {
            "start_time": _iso_from_ms(aligned_start),
            "end_time": _iso_from_ms(aligned_end),
            "interval": interval,
            "expected_count": expected_count,
            "observed_count": len(valid),
            "missing_count": missing_count,
            "duplicate_count": duplicates,
            "misaligned_count": len(misaligned),
            "misaligned_open_times": [_iso_from_ms(item) for item in misaligned[:100]],
            "out_of_range_count": len(normalized) - len(in_range),
            "missing_ranges": missing_ranges,
            "status": "COMPLETE" if missing_count == 0 and duplicates == 0 and not misaligned else "GAPS_FOUND",
        }


class LegacyBinanceHistorySource:
    """Controlled adapter around the existing Binance history downloader/store."""

    def __init__(self, history_db_path: str | Path | None = None, adapter: BinanceHistoryAdapter | None = None):
        self.history_db_path = Path(history_db_path or get_history_workspace_db_path())
        self.adapter = adapter or BinanceHistoryAdapter(history_db_path=self.history_db_path)

    def observed_open_times(self, symbol: str, interval: str, start_time: str, end_time: str) -> list[int]:
        if not self.history_db_path.exists():
            return []
        uri = f"{self.history_db_path.resolve().as_uri()}?mode=ro"
        conn = sqlite3.connect(uri, timeout=20.0, uri=True)
        try:
            rows = conn.execute(
                """
                SELECT open_time_ms FROM binance_klines
                WHERE symbol = ? AND interval = ? AND open_time_ms >= ? AND open_time_ms <= ?
                ORDER BY open_time_ms
                """,
                (_clean(symbol).upper(), _clean(interval).lower(), _ms(start_time), _ms(end_time)),
            ).fetchall()
            return [int(row[0]) for row in rows]
        finally:
            conn.close()

    def download_page(self, payload: dict[str, Any]) -> dict[str, Any]:
        from services.history_data_service import download_binance_klines

        return download_binance_klines(payload)

    def export_manifest(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = self.adapter.export(
            symbol=payload["symbol"],
            interval=payload["interval"],
            start_time=payload["start_time"],
            end_time=payload["effective_end_time"],
        )
        result["provenance"] = ManifestProvenanceService(self.adapter.store).record(
            manifest_id=result["manifest"].manifest_id,
            dataset_id=result["dataset_id"],
            gateway="DATATUBE",
            upstream_provider="binance",
            endpoint="market.klines",
            request={
                "symbol": payload["symbol"], "interval": payload["interval"],
                "start_time": payload["start_time"], "end_time": payload["effective_end_time"],
            },
            gateway_version="binance_backfill.v1",
            provider_version="binance_rest_v3",
            source_policy={"mode": "FIXED", "providers": ["binance"]},
        )
        return result


class BinanceBackfillJobService:
    def __init__(self, store: DataPlatformStore):
        self.store = store

    def create_or_get(self, *, task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        symbol = _clean(payload.get("symbol")).upper()
        interval = _clean(payload.get("interval")).lower()
        start_time = _parse_time(payload.get("start_time") or payload.get("start")).isoformat()
        requested_end = _parse_time(payload.get("end_time") or payload.get("end")).isoformat()
        interval_milliseconds(interval)
        effective_end_ms = min(_ms(requested_end), last_complete_open_ms(interval))
        if _ms(start_time) > effective_end_ms:
            raise ValueError("backfill window has no completed bars")
        effective_end = _iso_from_ms(effective_end_ms)
        request_material = {
            "project_id": task["project_id"],
            "plan_version": int(task["plan_version"]),
            "task_id": task["task_id"],
            "requirement_id": _clean(payload.get("requirement_id")),
            "symbol": symbol,
            "interval": interval,
            "start_time": start_time,
            "end_time": requested_end,
            "effective_end_time": effective_end,
        }
        source_request_hash = _hash(request_material)
        idempotency_key = _clean(payload.get("idempotency_key")) or f"binance-backfill:{source_request_hash}"
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            existing = conn.execute(
                "SELECT * FROM binance_backfill_jobs WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing:
                if str(existing["source_request_hash"]) != source_request_hash:
                    raise ValueError("backfill idempotency_key was reused with a different source request")
                return self._decode(existing)
            task_row = conn.execute("SELECT project_id, plan_version FROM research_tasks WHERE task_id = ?", (task["task_id"],)).fetchone()
            if not task_row or str(task_row[0]) != str(task["project_id"]) or int(task_row[1]) != int(task["plan_version"]):
                raise ValueError("backfill task identity does not match control-plane metadata")
            job_id = f"backfill_{uuid.uuid4().hex}"
            conn.execute(
                """
                INSERT INTO binance_backfill_jobs(
                    job_id, project_id, plan_version, task_id, requirement_id,
                    symbol, interval, start_time, end_time, effective_end_time,
                    status, idempotency_key, source_request_hash, cursor_time,
                    page_limit, max_attempts, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'READY', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id, task["project_id"], int(task["plan_version"]), task["task_id"],
                    request_material["requirement_id"], symbol, interval, start_time, requested_end,
                    effective_end, idempotency_key, source_request_hash, start_time,
                    max(1, min(1000, int(payload.get("page_limit") or 1000))),
                    max(1, int(payload.get("max_attempts") or 5)), now, now,
                ),
            )
        return self.get(job_id)  # type: ignore[return-value]

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self.store.connection() as conn:
            row = conn.execute("SELECT * FROM binance_backfill_jobs WHERE job_id = ?", (_clean(job_id),)).fetchone()
        return self._decode(row) if row else None

    def list(self, *, status: str = "", task_id: str = "", limit: int = 200) -> list[dict[str, Any]]:
        clauses, params = [], []
        if _clean(status):
            clauses.append("status = ?")
            params.append(_clean(status).upper())
        if _clean(task_id):
            clauses.append("task_id = ?")
            params.append(_clean(task_id))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(int(limit), 1000)))
        with self.store.connection() as conn:
            rows = conn.execute(f"SELECT * FROM binance_backfill_jobs{where} ORDER BY updated_at DESC LIMIT ?", params).fetchall()
        return [self._decode(row) for row in rows]

    def claim(self, *, job_id: str, worker_id: str, lease_seconds: int) -> dict[str, Any]:
        worker_id = _clean(worker_id)
        if not worker_id:
            raise ValueError("worker_id is required")
        now = datetime.now(timezone.utc).replace(microsecond=0)
        with self.store.transaction(immediate=True) as conn:
            row = conn.execute("SELECT * FROM binance_backfill_jobs WHERE job_id = ?", (_clean(job_id),)).fetchone()
            if not row:
                raise ValueError(f"backfill job not found: {job_id}")
            status = str(row["status"])
            if status == "SUCCEEDED":
                return self._decode(row)
            if status == "RUNNING" and row["lease_expires_at"] and _parse_time(row["lease_expires_at"]) > now:
                raise ValueError("backfill job is already leased")
            if status == "RETRY_WAIT" and row["next_retry_at"] and _parse_time(row["next_retry_at"]) > now:
                raise ValueError("backfill retry is not due")
            attempt = int(row["attempt_count"]) + 1
            if attempt > int(row["max_attempts"]):
                conn.execute("UPDATE binance_backfill_jobs SET status = 'FAILED', updated_at = ? WHERE job_id = ?", (now.isoformat(), job_id))
                raise ValueError("backfill job exceeded max attempts")
            lease_expires = (now + timedelta(seconds=max(1, int(lease_seconds)))).isoformat()
            conn.execute(
                """
                UPDATE binance_backfill_jobs
                SET status = 'RUNNING', attempt_count = ?, lease_owner = ?, lease_expires_at = ?,
                    heartbeat_at = ?, next_retry_at = NULL, updated_at = ?
                WHERE job_id = ?
                """,
                (attempt, worker_id, lease_expires, now.isoformat(), now.isoformat(), job_id),
            )
        return self.get(job_id)  # type: ignore[return-value]

    def heartbeat(self, *, job_id: str, worker_id: str, lease_seconds: int) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        expires = (now + timedelta(seconds=max(1, int(lease_seconds)))).isoformat()
        with self.store.transaction(immediate=True) as conn:
            affected = conn.execute(
                """
                UPDATE binance_backfill_jobs SET heartbeat_at = ?, lease_expires_at = ?, updated_at = ?
                WHERE job_id = ? AND status = 'RUNNING' AND lease_owner = ? AND lease_expires_at > ?
                """,
                (now.isoformat(), expires, now.isoformat(), _clean(job_id), _clean(worker_id), now.isoformat()),
            ).rowcount
            if not affected:
                raise ValueError("backfill lease is not owned by worker or has expired")

    def adjust_start_for_availability(
        self,
        *,
        job_id: str,
        worker_id: str,
        available_from: str,
    ) -> dict[str, Any]:
        """Move a leased job past a verified pre-listing window.

        The task input remains unchanged, preserving the requested research
        contract. Only the provider preparation window is narrowed.
        """
        now = utc_now()
        available_ms = _ms(available_from)
        with self.store.transaction(immediate=True) as conn:
            job = conn.execute(
                "SELECT * FROM binance_backfill_jobs WHERE job_id = ?",
                (_clean(job_id),),
            ).fetchone()
            if (
                not job or str(job["status"]) != "RUNNING"
                or str(job["lease_owner"]) != _clean(worker_id)
                or not job["lease_expires_at"]
                or _parse_time(job["lease_expires_at"]) <= _parse_time(now)
            ):
                raise ValueError("backfill availability adjustment requires the active lease")
            if available_ms <= _ms(job["start_time"]):
                return self._decode(job)
            if available_ms > _ms(job["effective_end_time"]):
                raise RuntimeError("Binance has no completed bars in the requested window")
            effective_start = _iso_from_ms(available_ms)
            cursor_ms = max(available_ms, _ms(job["cursor_time"] or job["start_time"]))
            conn.execute(
                """
                UPDATE binance_backfill_jobs
                SET start_time = ?, cursor_time = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (effective_start, _iso_from_ms(cursor_ms), now, _clean(job_id)),
            )
        return self.get(job_id)  # type: ignore[return-value]

    def record_page(self, *, job_id: str, worker_id: str, request: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            job = conn.execute("SELECT * FROM binance_backfill_jobs WHERE job_id = ?", (_clean(job_id),)).fetchone()
            if (
                not job or str(job["status"]) != "RUNNING" or str(job["lease_owner"]) != _clean(worker_id)
                or not job["lease_expires_at"] or _parse_time(job["lease_expires_at"]) <= _parse_time(now)
            ):
                raise ValueError("backfill page cannot be recorded without the active lease")
            page_number = int(job["pages_completed"]) + 1
            fetched = max(0, int(response.get("fetched") or 0))
            stored = max(0, int(response.get("stored") or 0))
            batch_to = _clean(response.get("batch_to"))
            next_cursor = _iso_from_ms(_ms(batch_to) + interval_milliseconds(str(job["interval"]))) if batch_to else str(job["cursor_time"])
            response_material = {
                "batch_from": response.get("batch_from"), "batch_to": response.get("batch_to"),
                "fetched": fetched, "stored": stored, "source_url": response.get("source_url"),
            }
            conn.execute(
                """
                INSERT INTO binance_backfill_pages(
                    job_id, page_number, request_start_time, request_end_time,
                    response_start_time, response_end_time, fetched_count, stored_count,
                    response_hash, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'SUCCEEDED', ?)
                """,
                (
                    job_id, page_number, request["start"], request["end"], response.get("batch_from"),
                    response.get("batch_to"), fetched, stored, _hash(response_material), now,
                ),
            )
            conn.execute(
                """
                UPDATE binance_backfill_jobs
                SET pages_completed = ?, rows_fetched = rows_fetched + ?, rows_stored = rows_stored + ?,
                    cursor_time = ?, updated_at = ? WHERE job_id = ?
                """,
                (page_number, fetched, stored, next_cursor, now, job_id),
            )
        return self.get(job_id)  # type: ignore[return-value]

    def mark_retry(
        self,
        *,
        job_id: str,
        worker_id: str,
        error: Exception,
        retryable: bool = True,
        base_delay_seconds: int = 5,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        with self.store.transaction(immediate=True) as conn:
            row = conn.execute("SELECT * FROM binance_backfill_jobs WHERE job_id = ?", (_clean(job_id),)).fetchone()
            if not row or str(row["status"]) != "RUNNING" or str(row["lease_owner"]) != _clean(worker_id):
                raise ValueError("backfill retry requires the active lease")
            exhausted = not retryable or int(row["attempt_count"]) >= int(row["max_attempts"])
            status = "FAILED" if exhausted else "RETRY_WAIT"
            delay = min(3600, max(1, int(base_delay_seconds)) * (2 ** max(0, int(row["attempt_count"]) - 1)))
            next_retry = None if exhausted else (now + timedelta(seconds=delay)).isoformat()
            conn.execute(
                """
                UPDATE binance_backfill_jobs
                SET status = ?, next_retry_at = ?, lease_owner = NULL, lease_expires_at = NULL,
                    heartbeat_at = NULL, last_error_json = ?, updated_at = ? WHERE job_id = ?
                """,
                (status, next_retry, json_dumps({"type": type(error).__name__, "message": str(error)[:1000]}), now.isoformat(), job_id),
            )
        return self.get(job_id)  # type: ignore[return-value]

    def complete(self, *, job_id: str, worker_id: str, export_result: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        manifest = export_result["manifest"]
        with self.store.transaction(immediate=True) as conn:
            affected = conn.execute(
                """
                UPDATE binance_backfill_jobs
                SET status = 'SUCCEEDED', manifest_commit_status = 'COMMITTED', manifest_id = ?, dataset_id = ?,
                    lease_owner = NULL, lease_expires_at = NULL, heartbeat_at = NULL,
                    updated_at = ?, completed_at = ?
                WHERE job_id = ? AND status = 'RUNNING' AND lease_owner = ? AND lease_expires_at > ?
                """,
                (manifest.manifest_id, export_result["dataset_id"], now, now, _clean(job_id), _clean(worker_id), now),
            ).rowcount
            if not affected:
                raise ValueError("backfill completion requires the active lease")
        return self.get(job_id)  # type: ignore[return-value]

    @staticmethod
    def _decode(row: Any) -> dict[str, Any]:
        result = dict(row)
        result["last_error"] = json.loads(result.pop("last_error_json") or "{}")
        return result


class BinanceBackfillTaskExecutor:
    def __init__(self, store: DataPlatformStore, *, source: LegacyBinanceHistorySource | Any | None = None):
        self.store = store
        self.control = ResearchControlPlane(store)
        self.jobs = BinanceBackfillJobService(store)
        self.source = source or LegacyBinanceHistorySource(adapter=BinanceHistoryAdapter(store=store))

    @staticmethod
    def _validate_scope(task: dict[str, Any], grant: dict[str, Any]) -> dict[str, Any]:
        payload = task.get("input") if isinstance(task.get("input"), dict) else {}
        scope = grant.get("scope") if isinstance(grant.get("scope"), dict) else {}
        if grant.get("project_id") != task.get("project_id") or int(grant.get("plan_version") or 0) != int(task.get("plan_version") or 0):
            raise PermissionError("approval grant does not belong to this backfill task")
        if str(grant.get("status")) != "ACTIVE":
            raise PermissionError("approval grant is not active")
        symbol = _clean(payload.get("symbol")).upper()
        interval = _clean(payload.get("interval")).lower()
        if not symbol or interval not in SUPPORTED_INTERVALS:
            raise ValueError("Binance backfill requires a symbol and supported interval")
        asset_classes = {_clean(item).lower() for item in scope.get("asset_classes", [])}
        if asset_classes and "crypto_spot" not in asset_classes:
            raise PermissionError("approval grant does not allow crypto_spot data")
        venues = {_clean(item).upper() for item in scope.get("venues", [])}
        if venues and "BINANCE" not in venues:
            raise PermissionError("approval grant does not allow Binance data")
        symbols = {_clean(item).upper() for item in scope.get("symbols", [])}
        if symbols and symbol not in symbols:
            raise PermissionError(f"approval grant does not allow symbol: {symbol}")
        intervals = {_clean(item).lower() for item in scope.get("intervals", [])}
        if intervals and interval not in intervals:
            raise PermissionError(f"approval grant does not allow interval: {interval}")
        endpoints = {_clean(item).lower() for item in scope.get("endpoints", [])}
        if endpoints and "binance.klines" not in endpoints:
            raise PermissionError("approval grant does not allow binance.klines")
        return payload

    @staticmethod
    def _validate_maintenance_scope(task: dict[str, Any]) -> dict[str, Any]:
        payload = task.get("input") if isinstance(task.get("input"), dict) else {}
        if (
            task.get("project_id") != SYSTEM_MAINTENANCE_PROJECT_ID
            or payload.get("authorization_mode") != SYSTEM_MAINTENANCE_AUTHORIZATION
        ):
            raise PermissionError("invalid system Requirement maintenance task")
        instrument_id = _clean(payload.get("instrument_id"))
        symbol = _clean(payload.get("symbol")).upper()
        interval = _clean(payload.get("interval")).lower()
        if instrument_id.lower() != f"crypto_spot:binance:{symbol}".lower():
            raise PermissionError("maintenance task instrument does not match its Binance symbol")
        if not symbol or interval not in SUPPORTED_INTERVALS:
            raise ValueError("Binance maintenance requires a symbol and supported interval")
        return payload

    @staticmethod
    def _retryable(exc: Exception) -> bool:
        text = str(exc).lower()
        return isinstance(exc, (RequestException, TimeoutError, ConnectionError)) or any(
            marker in text for marker in ("429", "418", "timeout", "temporar", "http 5", "connection")
        )

    def execute(self, *, task_id: str, worker_id: str, lease_seconds: int = 300) -> dict[str, Any]:
        task = self.control.claim_task(task_id=task_id, worker_id=worker_id, lease_seconds=lease_seconds)
        job: dict[str, Any] | None = None
        reservation_id = ""
        heartbeat_stop = threading.Event()
        heartbeat_errors: list[Exception] = []
        heartbeat_thread: threading.Thread | None = None
        try:
            if str(task.get("task_type") or "").upper() != BINANCE_BACKFILL_TASK_TYPE:
                raise ValueError(f"unsupported Binance backfill task type: {task.get('task_type')}")
            task_input = task.get("input") if isinstance(task.get("input"), dict) else {}
            maintenance = (
                task.get("project_id") == SYSTEM_MAINTENANCE_PROJECT_ID
                and task_input.get("authorization_mode") == SYSTEM_MAINTENANCE_AUTHORIZATION
            )
            grant_id = _clean(task_input.get("grant_id"))
            if maintenance:
                payload = self._validate_maintenance_scope(task)
            else:
                if not grant_id:
                    raise ValueError("Binance backfill task requires grant_id")
                grant = self.control.get_grant(grant_id)
                if not grant:
                    raise PermissionError(f"approval grant not found: {grant_id}")
                payload = self._validate_scope(task, grant)
            job = self.jobs.create_or_get(task=task, payload=payload)
            if job["status"] == "SUCCEEDED":
                return self.control.complete_task(
                    task_id=task_id,
                    worker_id=worker_id,
                    output=self._task_output(
                        job,
                        {},
                        requested_start_time=_clean(payload.get("start_time") or payload.get("start")),
                    ),
                )
            job = self.jobs.claim(job_id=job["job_id"], worker_id=worker_id, lease_seconds=lease_seconds)
            budget = payload.get("budget") if isinstance(payload.get("budget"), dict) else {}
            if not maintenance:
                reservation = self.control.reserve_budget(
                    grant_id=grant_id,
                    idempotency_key=f"binance-backfill:{job['job_id']}:attempt:{job['attempt_count']}",
                    runs=1,
                    download_bytes=max(1, int(budget.get("download_bytes") or 20_000_000)),
                    runtime_seconds=max(1, int(budget.get("runtime_seconds") or lease_seconds)),
                )
                reservation_id = str(reservation["reservation_id"])
            heartbeat_interval = max(1, min(60, lease_seconds // 3))

            def maintain_lease() -> None:
                while not heartbeat_stop.wait(heartbeat_interval):
                    try:
                        self.control.heartbeat_task(task_id=task_id, worker_id=worker_id, lease_seconds=lease_seconds)
                        self.jobs.heartbeat(job_id=job["job_id"], worker_id=worker_id, lease_seconds=lease_seconds)
                    except Exception as heartbeat_exc:
                        heartbeat_errors.append(heartbeat_exc)
                        heartbeat_stop.set()

            heartbeat_thread = threading.Thread(target=maintain_lease, name=f"binance-backfill-heartbeat-{task_id}", daemon=True)
            heartbeat_thread.start()
            max_pages = max(1, int(payload.get("max_pages_per_attempt") or 20))
            page_delay = max(0.0, min(5.0, float(payload.get("page_delay_seconds") or 0.05)))
            final_gap: dict[str, Any] = {}
            for _ in range(max_pages):
                observed = self.source.observed_open_times(job["symbol"], job["interval"], job["start_time"], job["effective_end_time"])
                final_gap = BinanceGapDetector.detect(
                    start_time=job["start_time"], end_time=job["effective_end_time"], interval=job["interval"], observed_open_times=observed,
                )
                if final_gap["missing_count"] == 0:
                    break
                gap = final_gap["missing_ranges"][0]
                request_payload = {
                    "symbol": job["symbol"], "interval": job["interval"], "start": gap["start_time"],
                    "end": gap["end_time"], "limit": int(job["page_limit"]),
                }
                response = self.source.download_page(request_payload)
                if int(response.get("fetched") or 0) <= 0 or not response.get("batch_to"):
                    available_from = _clean(response.get("available_from"))
                    starts_at_job_boundary = _ms(gap["start_time"]) == _ms(job["start_time"])
                    availability_follows_gap = (
                        bool(available_from)
                        and _ms(available_from) > _ms(gap["end_time"])
                    )
                    if starts_at_job_boundary and availability_follows_gap:
                        job = self.jobs.adjust_start_for_availability(
                            job_id=job["job_id"],
                            worker_id=worker_id,
                            available_from=available_from,
                        )
                        continue
                    raise RuntimeError("Binance backfill page returned no progress")
                job = self.jobs.record_page(job_id=job["job_id"], worker_id=worker_id, request=request_payload, response=response)
                if page_delay:
                    time.sleep(page_delay)
            observed = self.source.observed_open_times(job["symbol"], job["interval"], job["start_time"], job["effective_end_time"])
            final_gap = BinanceGapDetector.detect(
                start_time=job["start_time"], end_time=job["effective_end_time"], interval=job["interval"], observed_open_times=observed,
            )
            if final_gap["missing_count"]:
                raise RuntimeError(f"Binance backfill remains partial: missing={final_gap['missing_count']}")
            export_result = self.source.export_manifest(job)
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=2.0)
            if heartbeat_errors:
                raise RuntimeError(f"Binance backfill heartbeat failed: {heartbeat_errors[0]}")
            job = self.jobs.complete(job_id=job["job_id"], worker_id=worker_id, export_result=export_result)
            if reservation_id:
                self.control.consume_reservation(reservation_id)
            return self.control.complete_task(
                task_id=task_id,
                worker_id=worker_id,
                output=self._task_output(
                    job,
                    final_gap,
                    reservation_id,
                    requested_start_time=_clean(payload.get("start_time") or payload.get("start")),
                ),
            )
        except Exception as exc:
            heartbeat_stop.set()
            if heartbeat_thread and heartbeat_thread.is_alive():
                heartbeat_thread.join(timeout=2.0)
            retryable = self._retryable(exc) or "remains partial" in str(exc).lower()
            if job and job.get("status") == "RUNNING":
                try:
                    job = self.jobs.mark_retry(
                        job_id=job["job_id"], worker_id=worker_id, error=exc, retryable=retryable
                    )
                except Exception:
                    pass
            if reservation_id:
                try:
                    reservation = self.control.get_reservation(reservation_id)
                    if reservation and reservation.get("status") == "RESERVED":
                        self.control.release_reservation(reservation_id)
                except Exception:
                    pass
            try:
                self.control.fail_task(task_id=task_id, worker_id=worker_id, error=str(exc), retry=retryable)
            except Exception:
                pass
            raise

    @staticmethod
    def _task_output(
        job: dict[str, Any],
        gap_report: dict[str, Any],
        reservation_id: str = "",
        requested_start_time: str = "",
    ) -> dict[str, Any]:
        output = {
            "job_id": job["job_id"], "dataset_id": job.get("dataset_id"), "manifest_id": job.get("manifest_id"),
            "symbol": job["symbol"], "interval": job["interval"], "row_count": gap_report.get("observed_count"),
            "pages_completed": job["pages_completed"], "rows_fetched": job["rows_fetched"], "rows_stored": job["rows_stored"],
            "manifest_commit_status": job["manifest_commit_status"], "reservation_id": reservation_id, "gap_report": gap_report,
        }
        if requested_start_time and _ms(job["start_time"]) > _ms(requested_start_time):
            output["availability_adjustment"] = {
                "code": "INSTRUMENT_LISTED_AFTER_REQUEST_START",
                "requested_start": _parse_time(requested_start_time).isoformat(),
                "available_from": _parse_time(job["start_time"]).isoformat(),
                "message": (
                    f"All available {job['symbol']} history was prepared. "
                    f"Binance data begins at {_parse_time(job['start_time']).isoformat()}."
                ),
            }
        return output


class BinanceBackfillWorker:
    def __init__(self, executor: BinanceBackfillTaskExecutor, worker_id: str):
        self.executor = executor
        self.worker_id = _clean(worker_id)
        if not self.worker_id:
            raise ValueError("worker_id is required")

    def run_once(self, *, lease_seconds: int = 300) -> dict[str, Any]:
        ready = list(self.executor.control.list_tasks(
            status="READY", task_type=BINANCE_BACKFILL_TASK_TYPE, limit=1000
        ))
        ready.sort(key=lambda item: (-int(item.get("priority") or 0), str(item.get("created_at") or ""), str(item.get("task_id") or "")))
        for task in ready:
            jobs = self.executor.jobs.list(task_id=str(task["task_id"]), limit=1)
            if jobs and jobs[0].get("status") == "RETRY_WAIT" and jobs[0].get("next_retry_at"):
                if _parse_time(jobs[0]["next_retry_at"]) > datetime.now(timezone.utc):
                    continue
            try:
                completed = self.executor.execute(task_id=str(task["task_id"]), worker_id=self.worker_id, lease_seconds=lease_seconds)
                return {"status": "EXECUTED", "task": completed}
            except ValueError as exc:
                if "task is not READY" in str(exc):
                    continue
                raise
        return {"status": "IDLE", "task": None}

    def status(self) -> dict[str, Any]:
        tasks = list(self.executor.control.list_tasks(task_type=BINANCE_BACKFILL_TASK_TYPE, limit=2000))
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
            "worker_id": self.worker_id, "task_type": BINANCE_BACKFILL_TASK_TYPE, "counts": counts,
            "queue_depth": len(ready_tasks),
            "oldest_ready_age_seconds": oldest_ready_age_seconds,
            "jobs": self.executor.jobs.list(limit=200), "total": len(tasks),
        }
