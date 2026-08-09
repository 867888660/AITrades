from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services import agent_interface_service, inspection_service
from services.data_platform.research_run_service import FormalResearchRunExecutor, ResearchRunWorker


class InspectionServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "inspection.sqlite3"
        self.patchers = [
            patch.object(inspection_service, "_strategy_db_path", return_value=self.db_path),
            patch.object(agent_interface_service, "_strategy_db_path", return_value=self.db_path),
        ]
        for item in self.patchers:
            item.start()

    def tearDown(self):
        for item in reversed(self.patchers):
            item.stop()
        self.temp_dir.cleanup()

    def test_progressive_trace_read_relations_and_redaction(self):
        first = inspection_service.emit_event(
            trace_id="trace_factor_eval",
            subject_type="research_run",
            subject_id="run_factor_001",
            trace_title="Factor Evaluation",
            trace_status="running",
            event_id="evt_prepare",
            event_kind="tool_call",
            title="Prepare factor data",
            status="succeeded",
            actor_type="agent",
            actor_id="factor_agent",
            operation="research.data.prepare",
            input_data={"dataset_id": "dataset_1", "api_key": "must-not-be-stored"},
            output_data={"rows": 100},
            refs=[{"ref_type": "dataset", "ref_id": "dataset_1", "ref_role": "input"}],
        )
        second = inspection_service.emit_event(
            trace_id="trace_factor_eval",
            subject_type="research_run",
            subject_id="run_factor_001",
            trace_title="Factor Evaluation",
            trace_status="succeeded",
            event_id="evt_ic",
            parent_event_id="evt_prepare",
            dependency_event_ids=["evt_prepare"],
            event_kind="validation",
            title="Evaluate IC",
            status="succeeded",
            severity="warning",
            operation="factor.evaluate_ic",
            input_data={"method": "pearson"},
            output_data={"ic_mean": 0.0344},
            metadata={"warning_code": "LOW_IC"},
        )

        self.assertEqual(first["sequence_no"], 1)
        self.assertEqual(second["sequence_no"], 2)

        traces = inspection_service.list_traces(subject_type="research_run", subject_id="run_factor_001")
        self.assertFalse(traces["has_more"])
        self.assertEqual(len(traces["items"]), 1)
        self.assertEqual(traces["items"][0]["event_count"], 2)
        self.assertEqual(traces["items"][0]["warning_count"], 1)
        self.assertEqual(traces["items"][0]["status"], "succeeded")

        index = inspection_service.list_events("trace_factor_eval")
        self.assertEqual([item["event_id"] for item in index["items"]], ["evt_prepare", "evt_ic"])
        self.assertNotIn("input", index["items"][0])

        prepared = inspection_service.get_event("evt_prepare")
        self.assertEqual(prepared["input"]["api_key"], "[REDACTED]")
        self.assertEqual(prepared["redaction"]["redacted_fields"], 1)
        self.assertEqual(prepared["references"][0]["ref_id"], "dataset_1")

        ic_event = inspection_service.get_event("evt_ic")
        relation_types = {edge["relation_type"] for edge in ic_event["relations"]["incoming"]}
        self.assertEqual(relation_types, {"parent", "dependency"})

        search = inspection_service.search_events("trace_factor_eval", "LOW_IC")
        self.assertEqual([item["event_id"] for item in search["items"]], ["evt_ic"])

    def test_agent_audit_is_bridged_and_clear_only_hides(self):
        agent_interface_service.audit_external_action(
            actor_type="agent",
            actor_id="inspection_test_agent",
            capability="research.read",
            target_type="research_run",
            target_id="run_123",
            input_data={"run_id": "run_123", "authorization": "Bearer secret"},
            output_data={"status": "completed"},
        )

        traces = inspection_service.list_traces(subject_type="research_run", subject_id="run_123")
        self.assertEqual(len(traces["items"]), 1)
        events = inspection_service.list_events(traces["items"][0]["trace_id"])
        self.assertEqual(len(events["items"]), 1)
        detail = inspection_service.get_event(events["items"][0]["event_id"])
        self.assertEqual(detail["input"]["authorization"], "[REDACTED]")

        audit_rows = agent_interface_service.list_audit(payload={"actor_type": "human"})
        original_event_id = audit_rows[0]["event_id"]
        result = agent_interface_service.clear_audit({
            "actor_type": "human",
            "actor_id": "local_user",
            "event_ids": [original_event_id],
            "reason": "hide test fixture",
        })
        self.assertEqual(result, {"hidden": 1, "deleted": 0, "retained_for_audit": True})

        visible_ids = {
            item["event_id"]
            for item in agent_interface_service.list_audit(payload={"actor_type": "human"})
        }
        self.assertNotIn(original_event_id, visible_ids)

        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT visibility, hidden_reason FROM agent_audit_events WHERE event_id = ?",
                (original_event_id,),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row, ("hidden", "hide test fixture"))

    def test_research_worker_emits_claim_and_completion_events(self):
        worker = ResearchRunWorker(object(), "worker-inspection-test")
        run = {
            "run_id": "run_worker_1",
            "run_type": "FACTOR_EVALUATION",
            "project_id": "project_1",
            "bundle_id": "bundle_1",
            "attempt_count": 1,
        }
        output = {
            "product_run_type": "FACTOR_RUN",
            "produced_factor_artifact_ids": ["factor_artifact_1"],
            "produced_evaluation_artifact_ids": ["evaluation_artifact_1"],
            "metrics": {"factor_a": {"ic": 0.03}},
        }
        with (
            patch.object(worker, "claim", return_value=run),
            patch.object(FormalResearchRunExecutor, "execute", return_value=output),
            patch.object(worker, "complete", return_value={"run_id": "run_worker_1", "status": "SUCCEEDED"}),
            patch("services.data_platform.research_run_service._emit_inspection_safely") as emit,
        ):
            completed = worker.run_once()

        self.assertEqual(completed["status"], "SUCCEEDED")
        self.assertEqual(emit.call_count, 2)
        claim = emit.call_args_list[0].kwargs
        finish = emit.call_args_list[1].kwargs
        self.assertEqual(claim["event_kind"], "state_change")
        self.assertEqual(finish["event_kind"], "agent_step")
        self.assertEqual(finish["trace_status"], "succeeded")
        self.assertEqual(
            {item["ref_id"] for item in finish["refs"]},
            {"factor_artifact_1", "evaluation_artifact_1"},
        )

    def test_best_effort_emitter_marks_trace_partial_after_event_drop(self):
        with patch.object(inspection_service, "emit_event", side_effect=sqlite3.OperationalError("busy")):
            result = inspection_service.emit_event_safely(
                trace_id="trace_partial",
                subject_type="research_run",
                subject_id="run_partial",
                trace_title="Partial trace",
                event_kind="tool_call",
                title="Dropped event",
            )
        self.assertIsNone(result)
        trace = inspection_service.get_trace("trace_partial")
        self.assertEqual(trace["status"], "partial")
        self.assertEqual(trace["completeness"], "partial")
        self.assertEqual(trace["dropped_event_count"], 1)


if __name__ == "__main__":
    unittest.main()
