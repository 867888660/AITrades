from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.data_platform.workload_scheduler import (
    IntelligentWorkloadRouter,
    ResourceAdmissionController,
    WorkloadPlan,
    worker_log_path,
)


class WorkloadSchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        ResourceAdmissionController._active.clear()

    def tearDown(self) -> None:
        ResourceAdmissionController._active.clear()

    def test_frontend_memory_reserve_blocks_compute_dispatch(self) -> None:
        controller = ResourceAdmissionController(frontend_reserve_mb=6144)
        with patch(
            "services.data_platform.workload_scheduler._physical_memory_mb",
            return_value=(32768, 7000),
        ):
            allowed, reason = controller.can_dispatch("STANDARD", 2048)
        self.assertFalse(allowed)
        self.assertEqual("FRONTEND_MEMORY_RESERVE", reason)

    def test_heavy_worker_is_mutually_exclusive(self) -> None:
        controller = ResourceAdmissionController(frontend_reserve_mb=4096)
        with patch(
            "services.data_platform.workload_scheduler._physical_memory_mb",
            return_value=(32768, 24000),
        ):
            self.assertTrue(controller.acquire("heavy-1", "HEAVY", 8192))
            allowed, reason = controller.can_dispatch("STANDARD", 2048)
            self.assertFalse(allowed)
            self.assertEqual("HEAVY_WORKLOAD_ACTIVE", reason)
            controller.release("heavy-1")
            self.assertTrue(controller.acquire("standard-1", "STANDARD", 2048))
            allowed, reason = controller.can_dispatch("HEAVY", 8192)
            self.assertFalse(allowed)
            self.assertEqual("HEAVY_WORKLOAD_MUTUAL_EXCLUSION", reason)

    def test_worker_logs_are_durable_and_scoped_to_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = worker_log_path(
                Path(temp) / "metadata" / "data_platform.db",
                "research_runs",
                "run_123",
                "attempt-1.log",
            )
            self.assertEqual("attempt-1.log", path.name)
            self.assertEqual("run_123", path.parent.name)
            self.assertIn("worker_logs", path.parts)

    def test_router_prioritizes_small_research_without_exposing_resources(self) -> None:
        decision = IntelligentWorkloadRouter().route_research(
            WorkloadPlan(
                resource_class="STANDARD",
                worker_memory_mb=8192,
                estimated_working_set_mb=600,
                source_rows=2_000_000,
                source_bytes=100_000_000,
                hard_limit_exceeded=False,
                reason="READY",
            )
        )
        self.assertEqual("LIGHT_RESEARCH", decision.workload_kind)
        self.assertEqual("STANDARD", decision.resource_class)
        self.assertEqual("BOUNDED_ISOLATED", decision.execution_mode)
        public = decision.to_public_dict(state="WAITING", position=2, total=4)
        self.assertEqual("AUTOMATIC", public["mode"])
        self.assertFalse(public["action_required"])
        self.assertNotIn("worker_memory_mb", public)
        self.assertNotIn("resource_class", public)

    def test_router_fails_oversized_legacy_execution_at_preflight(self) -> None:
        decision = IntelligentWorkloadRouter().route_research(
            WorkloadPlan(
                resource_class="HEAVY",
                worker_memory_mb=8192,
                estimated_working_set_mb=12000,
                source_rows=60_000_000,
                source_bytes=4_000_000_000,
                hard_limit_exceeded=True,
                reason="PARTITIONED_EXECUTION_REQUIRED",
            )
        )
        self.assertEqual("PARTITIONED_REQUIRED", decision.execution_mode)
        self.assertEqual("PREFLIGHT_ONLY", decision.dispatch_policy)
        self.assertTrue(decision.checkpoint_enabled)

    def test_resource_wait_reason_is_abstracted_for_callers(self) -> None:
        decision = IntelligentWorkloadRouter().route_backtest(legs=2)
        public = decision.to_public_dict(
            state="WAITING", reason="FRONTEND_MEMORY_RESERVE"
        )
        self.assertEqual("WAITING_RESOURCE", public["state"])
        self.assertFalse(public["action_required"])
        self.assertNotIn("reason", public)


if __name__ == "__main__":
    unittest.main()
