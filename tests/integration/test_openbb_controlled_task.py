from __future__ import annotations

import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from requests import ConnectionError as RequestsConnectionError

from services.data_platform import DataPlatformStore, OpenBBResearchTaskExecutor, ResearchControlPlane


class StubAdapter:
    def __init__(self):
        self.calls = []

    def export(self, payload):
        self.calls.append(payload)
        manifest = type("Manifest", (), {"manifest_id": "manifest_openbb_test"})()
        return {
            "dataset_id": "openbb:yfinance:equity:XNAS:AAPL:bars:1d:splits_only",
            "manifest": manifest,
            "instrument_id": "equity:XNAS:AAPL",
            "row_count": 100,
            "gateway": "openbb",
            "upstream_provider": "yfinance",
            "adjustment": "splits_only",
        }


class FallbackStubAdapter(StubAdapter):
    def export(self, payload):
        self.calls.append(payload)
        if payload.get("provider") == "fmp":
            raise RequestsConnectionError("fmp unavailable")
        manifest = type("Manifest", (), {"manifest_id": "manifest_fallback_test"})()
        return {
            "dataset_id": "openbb:yfinance:equity:XNAS:AAPL:bars:1d:splits_only",
            "manifest": manifest, "instrument_id": "equity:XNAS:AAPL", "row_count": 100,
            "gateway": "openbb", "upstream_provider": "yfinance", "adjustment": "splits_only",
        }


class SlowStubAdapter(StubAdapter):
    def export(self, payload):
        time.sleep(1.3)
        return super().export(payload)


class OpenBBControlledTaskTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = DataPlatformStore(Path(self.tmp.name) / "control.db")
        self.control = ResearchControlPlane(self.store)
        self.project = self.control.create_project(title="OpenBB", objective="Controlled equity data export")
        intent = self.control.create_plan(project_id=self.project["project_id"], stage="INTENT", payload={"objective": "AAPL daily"})
        self.plan_version = intent["plan_version"]
        self.control.create_plan(
            project_id=self.project["project_id"], stage="RESOLVED", plan_version=self.plan_version,
            payload={"symbol": "AAPL", "venue": "XNAS", "provider": "yfinance"},
        )
        self.grant = self.control.approve_plan(
            project_id=self.project["project_id"], plan_version=self.plan_version,
            scope={
                "asset_classes": ["equity"], "venues": ["XNAS"], "symbols": ["AAPL"],
                "providers": ["yfinance"], "endpoints": ["equity.price.historical"],
            },
            budgets={"max_backtest_runs": 2, "max_download_bytes": 2_000_000, "max_runtime_seconds": 120},
            approved_by="local_user", actor_type="human",
            expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).replace(microsecond=0).isoformat(),
        )

    def tearDown(self):
        self.tmp.cleanup()

    def compile(self, *, symbol="AAPL"):
        return self.control.compile_tasks(
            project_id=self.project["project_id"], plan_version=self.plan_version,
            workflow_run_id=f"workflow-{symbol.lower()}",
            task_specs=[{
                "task_type": "OPENBB_EQUITY_DAILY_EXPORT", "logical_key": "export",
                "input": {
                    "grant_id": self.grant["grant_id"], "symbol": symbol, "venue": "XNAS",
                    "provider": "yfinance", "adjustment": "splits_only",
                    "budget": {"download_bytes": 1_000_000, "runtime_seconds": 60},
                },
            }],
        )[0]

    def test_approved_task_consumes_budget_and_completes(self):
        adapter = StubAdapter()
        task = self.compile()
        result = OpenBBResearchTaskExecutor(self.store, {}, adapter=adapter).execute(
            task_id=task["task_id"], worker_id="openbb-worker"
        )
        self.assertEqual(result["status"], "SUCCEEDED")
        self.assertEqual(result["output"]["manifest_id"], "manifest_openbb_test")
        reservation = self.control.get_reservation(result["output"]["reservation_id"])
        self.assertEqual(reservation["status"], "CONSUMED")
        self.assertEqual(reservation["runs"], 0)
        with self.store.connection() as conn:
            counter = conn.execute(
                "SELECT consumed_runs FROM approval_budget_counters WHERE grant_id=?",
                (self.grant["grant_id"],),
            ).fetchone()
        self.assertEqual(int(counter["consumed_runs"]), 0)
        self.assertEqual(len(adapter.calls), 1)

    def test_system_maintenance_accepts_matching_bare_equity_ticker(self):
        payload = OpenBBResearchTaskExecutor._validate_maintenance_scope({
            "project_id": "project_system_requirement_maintenance",
            "input": {
                "authorization_mode": "SYSTEM_REQUIREMENT_MAINTENANCE",
                "instrument_id": "AAPL",
                "symbol": "AAPL",
                "venue": "XNAS",
                "interval": "1d",
            },
        })

        self.assertEqual("AAPL", payload["instrument_id"])

    def test_scope_violation_fails_before_adapter_call(self):
        adapter = StubAdapter()
        task = self.compile(symbol="MSFT")
        with self.assertRaisesRegex(PermissionError, "does not allow symbol"):
            OpenBBResearchTaskExecutor(self.store, {}, adapter=adapter).execute(
                task_id=task["task_id"], worker_id="openbb-worker"
            )
        current = self.control.list_tasks(workflow_run_id="workflow-msft")[0]
        self.assertEqual(current["status"], "FAILED")
        self.assertEqual(adapter.calls, [])

    def test_primary_fallback_switches_whole_request(self):
        adapter = FallbackStubAdapter()
        # Expand only this test's human grant by creating a separate project/plan.
        project = self.control.create_project(title="Fallback", objective="Approved fallback")
        intent = self.control.create_plan(project_id=project["project_id"], stage="INTENT", payload={})
        self.control.create_plan(project_id=project["project_id"], stage="RESOLVED", plan_version=intent["plan_version"], payload={})
        grant = self.control.approve_plan(
            project_id=project["project_id"], plan_version=intent["plan_version"],
            scope={"asset_classes": ["equity"], "venues": ["XNAS"], "symbols": ["AAPL"],
                   "providers": ["fmp", "yfinance"], "endpoints": ["equity.price.historical"]},
            budgets={"max_backtest_runs": 1, "max_download_bytes": 2_000_000, "max_runtime_seconds": 120},
            approved_by="local_user", actor_type="human",
        )
        task = self.control.compile_tasks(
            project_id=project["project_id"], plan_version=intent["plan_version"], workflow_run_id="fallback-workflow",
            task_specs=[{"task_type": "OPENBB_EQUITY_DAILY_EXPORT", "logical_key": "export", "input": {
                "grant_id": grant["grant_id"], "symbol": "AAPL", "venue": "XNAS", "adjustment": "splits_only",
                "source_policy": {"mode": "PRIMARY_FALLBACK", "providers": ["fmp", "yfinance"]},
                "budget": {"download_bytes": 1_000_000, "runtime_seconds": 60},
            }}],
        )[0]
        result = OpenBBResearchTaskExecutor(self.store, {}, adapter=adapter).execute(task_id=task["task_id"], worker_id="worker")
        self.assertEqual(result["output"]["upstream_provider"], "yfinance")
        self.assertEqual([item["provider"] for item in result["output"]["source_attempts"]], ["fmp", "yfinance"])
        self.assertEqual(len(adapter.calls), 2)

    def test_long_export_renews_task_lease(self):
        adapter = SlowStubAdapter()
        task = self.compile()
        result = OpenBBResearchTaskExecutor(self.store, {}, adapter=adapter).execute(
            task_id=task["task_id"], worker_id="heartbeat-worker", lease_seconds=3
        )
        self.assertEqual(result["status"], "SUCCEEDED")
        self.assertEqual(result["output"]["manifest_id"], "manifest_openbb_test")


if __name__ == "__main__":
    unittest.main()
