from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as app_module
from services.data_platform import (
    DataPlatformStore,
    ResearchAgentSessionService,
    ResearchContextResolver,
)


class ResearchAgentSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = DataPlatformStore(Path(self.temp.name) / "metadata.db")
        self.service = ResearchAgentSessionService(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def test_start_builds_brief_and_internal_research_capacity(self):
        session = self.service.start({"objective": "建立 BTC 趋势跟随策略"})

        self.assertEqual("START", session["entry_mode"])
        self.assertEqual("PLANNING", session["status"])
        self.assertEqual("BTCUSDT spot", session["brief"]["instrument_scope"])
        self.assertEqual("1h", session["brief"]["frequency"])
        self.assertEqual(10, session["session_policy"]["max_runs"])
        self.assertTrue(session["internal_grant_id"])
        self.assertIsNotNone(self.service.control.get_grant(session["internal_grant_id"]))

    def test_resume_project_restores_context_without_changing_original_baseline(self):
        started = self.service.start({"objective": "BTC trend"})
        resumed = self.service.resume("PROJECT", started["project_id"])

        self.assertEqual("RESUME", resumed["entry_mode"])
        self.assertEqual(started["project_id"], resumed["context"]["project_id"])
        self.assertEqual("", resumed["original_baseline_run_id"])
        self.assertEqual("", resumed["current_branch_head_run_id"])

    def test_iteration_keep_moves_only_current_branch_head(self):
        session = self.service.start({"objective": "BTC trend"})
        iteration = self.service.create_iteration(
            session["session_id"],
            {
                "hypothesis": {"statement": "longer MA reduces noise"},
                "intervention_set": [{"ma_window": {"before": 20, "after": 50}}],
                "change_set": {"object": "factor_formula"},
            },
        )
        completed = self.service.complete_iteration(
            iteration["iteration_id"],
            {"decision": "KEEP", "candidate_run_id": "run_candidate", "decision_reason": "lower drawdown"},
        )
        updated = self.service.get(session["session_id"])

        self.assertEqual("KEEP", completed["decision"])
        self.assertEqual("", updated["original_baseline_run_id"])
        self.assertEqual("run_candidate", updated["current_branch_head_run_id"])
        self.assertEqual("FACTOR", completed["invalidation_plan"]["execution_start_point"])

    def test_need_human_is_limited_to_stable_reason_codes(self):
        session = self.service.start({"objective": "BTC trend"})
        waiting = self.service.need_human(
            session["session_id"],
            reason_code="MATERIAL_SCOPE_CHANGE",
            question="是否把研究范围从 BTC 扩展到 BTC 和 ETH？",
        )
        self.assertEqual("NEED_HUMAN", waiting["status"])
        self.assertEqual("MATERIAL_SCOPE_CHANGE", waiting["pending_question"]["reason_code"])
        with self.assertRaises(ValueError):
            self.service.need_human(
                session["session_id"], reason_code="CONFIRM_ROUTINE_CHOICE", question="继续吗？"
            )

    def test_context_resolver_reports_missing_anchor(self):
        result = ResearchContextResolver(self.store).resolve("RUN", "run_missing")
        self.assertEqual("NOT_FOUND", result["resolution_status"])

    def test_human_can_resolve_an_ambiguous_resume_to_a_project(self):
        project = self.service.start({"objective": "BTC baseline"})
        waiting = self.service.resume("RUN", "run_missing", {"objective": "continue BTC research"})
        self.assertEqual("NEED_HUMAN", waiting["status"])

        resolved = self.service.answer(waiting["session_id"], project["project_id"])
        self.assertEqual("PLANNING", resolved["status"])
        self.assertEqual(project["project_id"], resolved["project_id"])
        self.assertEqual("RESOLVED", resolved["resolution_status"])


class ResearchAgentSessionApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = DataPlatformStore(Path(self.temp.name) / "metadata.db")
        self.store_patch = patch("app.get_default_store", return_value=self.store)
        self.audit_patch = patch("app._audit_agent_research", return_value=None)
        self.store_patch.start()
        self.audit_patch.start()
        self.client = app_module.app.test_client()

    def tearDown(self):
        self.audit_patch.stop()
        self.store_patch.stop()
        self.temp.cleanup()

    def test_start_api_exposes_session_not_grant(self):
        response = self.client.post(
            "/api/agent/research/sessions",
            json={"entry_mode": "START", "objective": "BTC trend research"},
        )
        self.assertEqual(201, response.status_code, response.get_json())
        data = response.get_json()["data"]
        self.assertTrue(data["session_id"])
        self.assertNotIn("internal_grant_id", data)
        self.assertNotIn("grant_id", data["context"])

        status = self.client.get(f"/api/agent/research/sessions/{data['session_id']}")
        self.assertEqual(200, status.status_code)
        self.assertNotIn("internal_grant_id", status.get_json()["data"])

    def test_session_id_authorizes_project_scoped_research_write(self):
        created = self.client.post(
            "/api/agent/research/sessions",
            json={"entry_mode": "START", "objective": "BTC trend research"},
        ).get_json()["data"]
        response = self.client.post(
            f"/api/agent/research/projects/{created['project_id']}/definitions",
            json={
                "session_id": created["session_id"],
                "definition_type": "FACTOR",
                "spec": {
                    "name": "session_btc_ma",
                    "version": "1.0.0",
                    "operator": "ma_crossover",
                    "input_field": "close",
                    "window": 20,
                    "parameters": {"fast_window": 5},
                    "frequency": "1h",
                },
            },
        )
        self.assertEqual(201, response.status_code, response.get_json())
        self.assertEqual("PROJECT", response.get_json()["data"]["library_scope"])


if __name__ == "__main__":
    unittest.main()
