from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from services.data_platform import OpenBBResearchWorker


def _control_plane_list_tasks(rows: list[dict]):
    """Mirror ResearchControlPlane.list_tasks server-side filtering.

    The real implementation pushes ``task_type``/``status`` into SQL, so a mock
    using a plain return_value hands back rows the worker would never see.
    """

    def _list_tasks(*, task_type: str = "", status: str = "", limit: int = 500, **_: object) -> list[dict]:
        result = list(rows)
        if task_type:
            result = [item for item in result if str(item.get("task_type") or "") == task_type]
        if status:
            result = [item for item in result if str(item.get("status") or "").upper() == status.upper()]
        return result[:limit]

    return _list_tasks


class OpenBBWorkerTest(unittest.TestCase):
    def test_idle_and_execute(self):
        executor = Mock()
        executor.control.list_tasks.side_effect = _control_plane_list_tasks([])
        worker = OpenBBResearchWorker(executor, "worker-1")
        self.assertEqual(worker.run_once()["status"], "IDLE")

        executor.control.list_tasks.side_effect = _control_plane_list_tasks([
            {"task_id": "task-1", "task_type": "OPENBB_EQUITY_DAILY_EXPORT", "status": "READY", "priority": 10, "created_at": "2026-01-01"}
        ])
        executor.execute.return_value = {"task_id": "task-1", "status": "SUCCEEDED"}
        result = worker.run_once()
        self.assertEqual(result["status"], "EXECUTED")
        executor.execute.assert_called_once()

    def test_status_summarizes_existing_control_plane_tasks(self):
        executor = Mock()
        executor.control.list_tasks.side_effect = _control_plane_list_tasks([
            {"task_id": "ready", "task_type": "OPENBB_EQUITY_DAILY_EXPORT", "status": "READY"},
            {"task_id": "failed", "task_type": "OPENBB_EQUITY_DAILY_EXPORT", "status": "FAILED"},
            {"task_id": "other", "task_type": "COMPUTE_FACTOR", "status": "READY"},
        ])
        status = OpenBBResearchWorker(executor, "worker-1").status()
        self.assertEqual(status["total"], 2)
        self.assertEqual(status["counts"], {"READY": 1, "FAILED": 1})
        self.assertEqual(status["active"][0]["task_id"], "ready")

    def test_status_reports_oldest_ready_age_for_stall_diagnostics(self):
        created = (datetime.now(timezone.utc) - timedelta(seconds=900)).isoformat()
        executor = Mock()
        executor.control.list_tasks.side_effect = _control_plane_list_tasks([
            {"task_id": "old", "task_type": "OPENBB_EQUITY_DAILY_EXPORT", "status": "READY", "created_at": created},
        ])
        status = OpenBBResearchWorker(executor, "worker-1").status()
        self.assertEqual(status["queue_depth"], 1)
        self.assertIsNotNone(status["oldest_ready_age_seconds"])
        self.assertAlmostEqual(900.0, status["oldest_ready_age_seconds"], delta=30.0)

    def test_status_survives_ready_tasks_without_usable_created_at(self):
        executor = Mock()
        executor.control.list_tasks.side_effect = _control_plane_list_tasks([
            {"task_id": "no-timestamp", "task_type": "OPENBB_EQUITY_DAILY_EXPORT", "status": "READY"},
            {"task_id": "bad-timestamp", "task_type": "OPENBB_EQUITY_DAILY_EXPORT", "status": "READY", "created_at": "not-a-date"},
        ])
        status = OpenBBResearchWorker(executor, "worker-1").status()
        self.assertEqual(status["queue_depth"], 2)
        self.assertIsNone(status["oldest_ready_age_seconds"])


if __name__ == "__main__":
    unittest.main()
