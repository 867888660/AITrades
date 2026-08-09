"""Verify project planning, human grant approval, budget reservation, and task DAG."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.data_platform import DataPlatformStore, ResearchControlPlane


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="datatube-control-plane-") as temp_dir:
        service = ResearchControlPlane(DataPlatformStore(Path(temp_dir) / "control.db"))
        project = service.create_project(
            title="Crypto Momentum Smoke",
            objective="Validate a controlled Binance momentum research run.",
        )
        project_id = project["project_id"]
        intent = service.create_plan(
            project_id=project_id,
            stage="INTENT",
            payload={"objective": "research momentum", "market": "Binance Spot"},
        )
        resolved = service.create_plan(
            project_id=project_id,
            stage="RESOLVED",
            plan_version=intent["plan_version"],
            payload={
                "instruments": ["crypto_spot:BINANCE:BTCUSDT"],
                "frequency": "1m",
                "factors": ["momentum_20", "volatility_20"],
                "max_backtest_runs": 5,
            },
        )
        assert resolved["plan_stage"] == "RESOLVED"
        grant = service.approve_plan(
            project_id=project_id,
            plan_version=intent["plan_version"],
            scope={"asset_classes": ["crypto_spot"], "max_instruments": 1},
            budgets={
                "max_backtest_runs": 5,
                "max_download_bytes": 10_000_000,
                "max_runtime_seconds": 600,
            },
            approved_by="local_user",
            actor_type="human",
        )
        assert grant["status"] == "ACTIVE"
        try:
            service.approve_plan(
                project_id=project_id,
                plan_version=intent["plan_version"],
                scope={},
                budgets={},
                approved_by="agent_strategy_assistant",
                actor_type="agent",
            )
        except PermissionError:
            pass
        else:
            raise AssertionError("agent approval must be rejected")

        reservation = service.reserve_budget(
            grant_id=grant["grant_id"],
            idempotency_key="run:001",
            runs=2,
            download_bytes=2_000_000,
            runtime_seconds=120,
        )
        repeat = service.reserve_budget(
            grant_id=grant["grant_id"],
            idempotency_key="run:001",
            runs=2,
            download_bytes=2_000_000,
            runtime_seconds=120,
        )
        assert reservation["reservation_id"] == repeat["reservation_id"]
        consumed = service.consume_reservation(reservation["reservation_id"])
        assert consumed["status"] == "CONSUMED"

        tasks = service.compile_tasks(
            project_id=project_id,
            plan_version=intent["plan_version"],
            workflow_run_id="workflow_001",
            task_specs=[
                {"task_type": "CHECK_AVAILABILITY", "logical_key": "availability"},
                {"task_type": "COMPUTE_FACTOR", "logical_key": "factor", "depends_on": ["availability"]},
                {"task_type": "BUILD_ALPHA", "logical_key": "alpha", "depends_on": ["factor"]},
            ],
        )
        availability = next(item for item in tasks if item["logical_key"] == "availability")
        factor = next(item for item in tasks if item["logical_key"] == "factor")
        alpha = next(item for item in tasks if item["logical_key"] == "alpha")
        assert availability["status"] == "READY"
        assert factor["status"] == "PENDING"
        assert alpha["status"] == "PENDING"
        service.claim_task(task_id=availability["task_id"], worker_id="worker-1")
        service.heartbeat_task(task_id=availability["task_id"], worker_id="worker-1")
        service.complete_task(task_id=availability["task_id"], worker_id="worker-1", output={"coverage": 1.0})
        factor_after = next(item for item in service.list_tasks(workflow_run_id="workflow_001") if item["task_id"] == factor["task_id"])
        assert factor_after["status"] == "READY"
        service.claim_task(task_id=factor["task_id"], worker_id="worker-1")
        service.complete_task(task_id=factor["task_id"], worker_id="worker-1", output={"artifact_id": "factor_demo"})
        alpha_after = next(item for item in service.list_tasks(workflow_run_id="workflow_001") if item["task_id"] == alpha["task_id"])
        assert alpha_after["status"] == "READY"
        print("Research control plane smoke test passed")
        print({
            "project_id": project_id,
            "plan_version": intent["plan_version"],
            "grant_id": grant["grant_id"],
            "reservation_id": reservation["reservation_id"],
            "task_statuses": {item["logical_key"]: item["status"] for item in service.list_tasks(workflow_run_id="workflow_001")},
        })


if __name__ == "__main__":
    main()
