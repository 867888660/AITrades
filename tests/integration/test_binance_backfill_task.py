from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from requests import ConnectionError as RequestsConnectionError

from services.data_platform import BinanceBackfillTaskExecutor, BinanceBackfillJobService, DataPlatformStore, ResearchControlPlane


def ms(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def iso(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()


class StubBackfillSource:
    def __init__(self, observed=(), *, fail_on_call: int = 0):
        self.observed = {int(item) for item in observed}
        self.fail_on_call = fail_on_call
        self.download_calls = []
        self.export_calls = []

    def observed_open_times(self, symbol, interval, start_time, end_time):
        return sorted(item for item in self.observed if ms(start_time) <= item <= ms(end_time))

    def download_page(self, payload):
        self.download_calls.append(dict(payload))
        if self.fail_on_call and len(self.download_calls) == self.fail_on_call:
            raise RequestsConnectionError("temporary Binance connection failure")
        interval_ms = 3_600_000
        start = ms(payload["start"])
        end = ms(payload["end"])
        values = list(range(start, end + 1, interval_ms))[: int(payload["limit"])]
        self.observed.update(values)
        return {
            "fetched": len(values),
            "stored": len(values),
            "batch_from": iso(values[0]) if values else None,
            "batch_to": iso(values[-1]) if values else None,
            "source_url": "stub://binance",
        }

    def export_manifest(self, payload):
        self.export_calls.append(dict(payload))
        manifest = type("Manifest", (), {"manifest_id": "manifest_binance_backfill_test"})()
        return {"manifest": manifest, "dataset_id": "binance:AAAUSDT:1h"}


class ListedLaterBackfillSource(StubBackfillSource):
    def __init__(self, available_from: str):
        first = ms(available_from)
        super().__init__(observed=range(first, ms("2026-01-01T05:00:00+00:00") + 1, 3_600_000))
        self.available_from = available_from

    def download_page(self, payload):
        if ms(payload["start"]) < ms(self.available_from):
            self.download_calls.append(dict(payload))
            return {
                "fetched": 0,
                "stored": 0,
                "batch_from": None,
                "batch_to": None,
                "available_from": self.available_from,
                "source_url": "stub://binance-fallback",
            }
        return super().download_page(payload)


class BinanceBackfillTaskTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = DataPlatformStore(Path(self.temp.name) / "control.db")
        self.control = ResearchControlPlane(self.store)
        project = self.control.create_project(title="Binance Backfill", objective="Controlled research data backfill")
        self.project_id = project["project_id"]
        intent = self.control.create_plan(project_id=self.project_id, stage="INTENT", payload={"symbol": "AAAUSDT"})
        self.plan_version = intent["plan_version"]
        self.control.create_plan(
            project_id=self.project_id, stage="RESOLVED", plan_version=self.plan_version,
            payload={"symbol": "AAAUSDT", "interval": "1h"},
        )
        self.grant = self.control.approve_plan(
            project_id=self.project_id,
            plan_version=self.plan_version,
            scope={
                "asset_classes": ["crypto_spot"], "venues": ["BINANCE"], "symbols": ["AAAUSDT"],
                "intervals": ["1h"], "endpoints": ["binance.klines"],
            },
            budgets={"max_backtest_runs": 10, "max_download_bytes": 100_000_000, "max_runtime_seconds": 1000},
            actor_type="human",
            expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).replace(microsecond=0).isoformat(),
        )

    def tearDown(self):
        self.temp.cleanup()

    def compile(self, *, symbol="AAAUSDT", workflow="backfill", page_limit=1000, max_pages=20):
        return self.control.compile_tasks(
            project_id=self.project_id,
            plan_version=self.plan_version,
            workflow_run_id=workflow,
            task_specs=[{
                "task_type": "BINANCE_BARS_BACKFILL",
                "logical_key": "backfill",
                "max_attempts": 5,
                "input": {
                    "grant_id": self.grant["grant_id"], "symbol": symbol, "interval": "1h",
                    "start_time": "2026-01-01T00:00:00+00:00", "end_time": "2026-01-01T05:00:00+00:00",
                    "page_limit": page_limit, "max_pages_per_attempt": max_pages, "page_delay_seconds": 0,
                    "budget": {"download_bytes": 1_000_000, "runtime_seconds": 60},
                },
            }],
        )[0]

    def test_only_missing_range_is_downloaded_and_manifest_committed(self):
        source = StubBackfillSource(observed=[ms("2026-01-01T00:00:00+00:00"), ms("2026-01-01T05:00:00+00:00")])
        task = self.compile()
        result = BinanceBackfillTaskExecutor(self.store, source=source).execute(task_id=task["task_id"], worker_id="worker")
        self.assertEqual("SUCCEEDED", result["status"])
        self.assertEqual("manifest_binance_backfill_test", result["output"]["manifest_id"])
        self.assertEqual(0, result["output"]["gap_report"]["missing_count"])
        self.assertEqual("2026-01-01T01:00:00+00:00", source.download_calls[0]["start"])
        self.assertEqual("2026-01-01T04:00:00+00:00", source.download_calls[0]["end"])
        job = BinanceBackfillTaskExecutor(self.store, source=source).jobs.get(result["output"]["job_id"])
        self.assertEqual("COMMITTED", job["manifest_commit_status"])

    def test_partial_progress_resumes_after_transient_failure(self):
        source = StubBackfillSource(fail_on_call=2)
        task = self.compile(workflow="resume", page_limit=2, max_pages=10)
        executor = BinanceBackfillTaskExecutor(self.store, source=source)
        with self.assertRaises(RequestsConnectionError):
            executor.execute(task_id=task["task_id"], worker_id="worker")
        jobs = executor.jobs.list(task_id=task["task_id"])
        self.assertEqual("RETRY_WAIT", jobs[0]["status"])
        self.assertEqual(1, jobs[0]["pages_completed"])
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                "UPDATE binance_backfill_jobs SET next_retry_at = ? WHERE job_id = ?",
                ("2000-01-01T00:00:00+00:00", jobs[0]["job_id"]),
            )
        source.fail_on_call = 0
        completed = executor.execute(task_id=task["task_id"], worker_id="worker")
        self.assertEqual("SUCCEEDED", completed["status"])
        final_job = executor.jobs.get(jobs[0]["job_id"])
        self.assertGreater(final_job["pages_completed"], 1)
        self.assertEqual("COMMITTED", final_job["manifest_commit_status"])

    def test_scope_violation_fails_before_download(self):
        source = StubBackfillSource()
        task = self.compile(symbol="BBBUSD", workflow="scope")
        with self.assertRaisesRegex(PermissionError, "does not allow symbol"):
            BinanceBackfillTaskExecutor(self.store, source=source).execute(task_id=task["task_id"], worker_id="worker")
        self.assertEqual([], source.download_calls)

    def test_prelisting_window_is_auto_reviewed_and_available_history_is_exported(self):
        source = ListedLaterBackfillSource("2026-01-01T02:00:00+00:00")
        task = self.compile(workflow="listed-later")
        result = BinanceBackfillTaskExecutor(self.store, source=source).execute(
            task_id=task["task_id"],
            worker_id="worker",
        )
        self.assertEqual("SUCCEEDED", result["status"])
        review = result["output"]["availability_adjustment"]
        self.assertEqual("INSTRUMENT_LISTED_AFTER_REQUEST_START", review["code"])
        self.assertEqual("2026-01-01T00:00:00+00:00", review["requested_start"])
        self.assertEqual("2026-01-01T02:00:00+00:00", review["available_from"])
        self.assertEqual("2026-01-01T02:00:00+00:00", source.export_calls[0]["start_time"])
        self.assertEqual(0, result["output"]["gap_report"]["missing_count"])

    def test_system_requirement_maintenance_executes_without_research_grant(self):
        control = ResearchControlPlane(self.store)
        task = control.compile_maintenance_task({
            "task_type": "BINANCE_BARS_BACKFILL",
            "workflow_run_id": "requirement-maintenance:test",
            "idempotency_key": "requirement-maintenance:test",
            "logical_key": "AAAUSDT:1h",
            "input": {
                "instrument_id": "crypto_spot:BINANCE:AAAUSDT",
                "symbol": "AAAUSDT",
                "interval": "1h",
                "start_time": "2026-01-01T00:00:00+00:00",
                "end_time": "2026-01-01T02:00:00+00:00",
                "page_limit": 1000,
                "max_pages_per_attempt": 20,
                "budget": {"download_bytes": 1_000_000, "runtime_seconds": 60},
            },
        })
        source = StubBackfillSource()

        completed = BinanceBackfillTaskExecutor(self.store, source=source).execute(
            task_id=task["task_id"], worker_id="maintenance-test", lease_seconds=60
        )

        self.assertEqual("SUCCEEDED", completed["status"])
        self.assertEqual("", completed["output"]["reservation_id"])

    def test_only_one_worker_can_claim_same_backfill_job(self):
        task = self.compile(workflow="claim")
        jobs = BinanceBackfillJobService(self.store)
        job = jobs.create_or_get(task=task, payload=task["input"])

        def claim(worker_id):
            try:
                return "ok", jobs.claim(job_id=job["job_id"], worker_id=worker_id, lease_seconds=60)["lease_owner"]
            except ValueError as exc:
                return "error", str(exc)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, ["worker-a", "worker-b"]))
        self.assertEqual(1, sum(status == "ok" for status, _ in results))
        self.assertEqual(1, sum(status == "error" for status, _ in results))


if __name__ == "__main__":
    unittest.main()
