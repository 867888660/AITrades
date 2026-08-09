from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from services.data_platform import DataPlatformStore, ResearchControlPlane


class ResearchControlPlaneSafetyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = DataPlatformStore(Path(self.temp.name) / "control.db")
        self.service = ResearchControlPlane(self.store)
        project = self.service.create_project(title="Safety", objective="Audit control plane invariants")
        self.project_id = project["project_id"]
        intent = self.service.create_plan(
            project_id=self.project_id,
            stage="INTENT",
            payload={"objective": "test"},
        )
        self.plan_version = intent["plan_version"]
        self.service.create_plan(
            project_id=self.project_id,
            stage="RESOLVED",
            plan_version=self.plan_version,
            payload={"tasks": ["CHECK_DATA"]},
        )
        self.grant = self.service.approve_plan(
            project_id=self.project_id,
            plan_version=self.plan_version,
            scope={"max_instruments": 5},
            budgets={
                "max_backtest_runs": 2,
                "max_download_bytes": 1000,
                "max_runtime_seconds": 100,
            },
            actor_type="human",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_plan_and_grant_cannot_mutate_under_same_identity(self) -> None:
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.service.create_plan(
                project_id=self.project_id,
                stage="RESOLVED",
                plan_version=self.plan_version,
                payload={"tasks": ["DIFFERENT"]},
            )
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.service.approve_plan(
                project_id=self.project_id,
                plan_version=self.plan_version,
                scope={"max_instruments": 100},
                budgets={"max_backtest_runs": 100},
                actor_type="human",
            )

    def test_task_dag_rejects_cycles_and_idempotency_mismatch(self) -> None:
        with self.assertRaisesRegex(ValueError, "cycle"):
            self.service.compile_tasks(
                project_id=self.project_id,
                plan_version=self.plan_version,
                workflow_run_id="cycle-workflow",
                task_specs=[
                    {"task_type": "A", "logical_key": "a", "depends_on": ["b"]},
                    {"task_type": "B", "logical_key": "b", "depends_on": ["a"]},
                ],
            )
        self.assertEqual([], self.service.list_tasks(workflow_run_id="cycle-workflow"))

        self.service.compile_tasks(
            project_id=self.project_id,
            plan_version=self.plan_version,
            workflow_run_id="idempotent-workflow",
            task_specs=[{"task_type": "CHECK_DATA", "logical_key": "check", "input": {"days": 90}}],
        )
        with self.assertRaisesRegex(ValueError, "different fields"):
            self.service.compile_tasks(
                project_id=self.project_id,
                plan_version=self.plan_version,
                workflow_run_id="idempotent-workflow",
                task_specs=[{"task_type": "CHECK_DATA", "logical_key": "check", "input": {"days": 365}}],
            )

    def test_expired_worker_lease_cannot_complete_task(self) -> None:
        tasks = self.service.compile_tasks(
            project_id=self.project_id,
            plan_version=self.plan_version,
            workflow_run_id="lease-workflow",
            task_specs=[{"task_type": "CHECK_DATA", "logical_key": "check"}],
        )
        task_id = tasks[0]["task_id"]
        self.service.claim_task(task_id=task_id, worker_id="worker")
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                "UPDATE research_tasks SET lease_expires_at = ? WHERE task_id = ?",
                ("2000-01-01T00:00:00+00:00", task_id),
            )
        with self.assertRaisesRegex(ValueError, "lease expired"):
            self.service.complete_task(task_id=task_id, worker_id="worker", output={"ok": True})
        current = next(item for item in self.service.list_tasks(workflow_run_id="lease-workflow") if item["task_id"] == task_id)
        self.assertEqual("READY", current["status"])

    def test_last_budget_slot_is_reserved_atomically(self) -> None:
        def reserve(key: str) -> tuple[str, str]:
            try:
                result = self.service.reserve_budget(
                    grant_id=self.grant["grant_id"],
                    idempotency_key=key,
                    runs=2,
                )
                return "ok", result["reservation_id"]
            except ValueError as exc:
                return "error", str(exc)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reserve, ["budget-a", "budget-b"]))
        self.assertEqual(1, sum(status == "ok" for status, _ in results))
        self.assertEqual(1, sum(status == "error" for status, _ in results))


if __name__ == "__main__":
    unittest.main()
