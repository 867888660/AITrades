from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from services.data_platform import (
    DataPlatformStore,
    PolymarketResearchTaskExecutor,
    ResearchControlPlane,
)


class _StubPreparer:
    def __init__(self):
        self.calls = []

    def prepare(self, payload):
        self.calls.append(payload)
        return {
            "dataset_id": "polymarket-price-test-1h",
            "manifest": {"manifest_id": "manifest_polymarket_test"},
            "row_count": 42,
        }


class PolymarketControlledTaskTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = DataPlatformStore(Path(self.temp.name) / "control.db")
        self.control = ResearchControlPlane(self.store)
        self.project = self.control.create_project(title="Polymarket", objective="Controlled price history")
        intent = self.control.create_plan(project_id=self.project["project_id"], stage="INTENT", payload={})
        self.plan_version = intent["plan_version"]
        self.control.create_plan(
            project_id=self.project["project_id"], stage="RESOLVED",
            plan_version=self.plan_version, payload={},
        )
        self.instrument_id = "polymarket_binary:POLYMARKET:token-1"
        self.grant = self.control.approve_plan(
            project_id=self.project["project_id"], plan_version=self.plan_version,
            scope={
                "asset_classes": ["polymarket_binary"],
                "venues": ["POLYMARKET"],
                "providers": ["polymarket"],
                "allowed_instrument_ids": [self.instrument_id],
                "intervals": ["1h"],
                "endpoints": ["polymarket.price_history"],
            },
            budgets={"max_backtest_runs": 2, "max_download_bytes": 2_000_000, "max_runtime_seconds": 120},
            approved_by="local_user", actor_type="human",
            expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).replace(microsecond=0).isoformat(),
        )

    def tearDown(self):
        self.temp.cleanup()

    def _task(self, instrument_id=None):
        return self.control.compile_tasks(
            project_id=self.project["project_id"], plan_version=self.plan_version,
            workflow_run_id="polymarket-workflow",
            task_specs=[{
                "task_type": "POLYMARKET_PRICE_HISTORY_EXPORT",
                "logical_key": instrument_id or self.instrument_id,
                "input": {
                    "grant_id": self.grant["grant_id"],
                    "instrument_id": instrument_id or self.instrument_id,
                    "interval": "1h",
                    "start_time": "2026-01-01T00:00:00+00:00",
                    "end_time": "2026-01-31T00:00:00+00:00",
                    "budget": {"download_bytes": 1_000_000, "runtime_seconds": 60},
                },
            }],
        )[0]

    def test_approved_task_consumes_budget_and_completes(self):
        preparer = _StubPreparer()
        with patch(
            "services.data_platform.polymarket_task_executor.ManifestProvenanceService.record",
            return_value={"manifest_id": "manifest_polymarket_test"},
        ):
            result = PolymarketResearchTaskExecutor(
                self.store, preparer=preparer
            ).execute(task_id=self._task()["task_id"], worker_id="polymarket-worker")
        self.assertEqual("SUCCEEDED", result["status"])
        self.assertEqual("manifest_polymarket_test", result["output"]["manifest_id"])
        self.assertEqual("CONSUMED", self.control.get_reservation(result["output"]["reservation_id"])["status"])
        self.assertEqual(1, len(preparer.calls))

    def test_scope_violation_stops_before_download(self):
        preparer = _StubPreparer()
        with self.assertRaisesRegex(PermissionError, "does not allow this Polymarket outcome"):
            PolymarketResearchTaskExecutor(
                self.store, preparer=preparer
            ).execute(
                task_id=self._task("polymarket_binary:POLYMARKET:token-2")["task_id"],
                worker_id="polymarket-worker",
            )
        self.assertEqual([], preparer.calls)


if __name__ == "__main__":
    unittest.main()
