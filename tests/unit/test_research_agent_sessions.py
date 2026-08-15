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

    def test_start_is_idempotent_for_the_same_research_brief(self):
        payload = {
            "objective": "Evaluate AAPL daily momentum",
            "instrument_scope": ["AAPL"],
            "provider": "OPENBB",
            "frequency": "1d",
            "research_period": {"start": "2022-01-01", "end": "2026-08-09"},
        }

        first = self.service.start(payload)
        second = self.service.start(payload)

        self.assertEqual(first["project_id"], second["project_id"])
        self.assertEqual(first["session_id"], second["session_id"])
        self.assertFalse(first["idempotency_reused"])
        self.assertTrue(second["idempotency_reused"])
        self.assertEqual(1, len(self.service.control.list_projects()))

    def test_explicit_start_idempotency_key_rejects_a_different_brief(self):
        self.service.start({"objective": "BTC trend", "idempotency_key": "same-request"})

        with self.assertRaisesRegex(ValueError, "RESEARCH_IDEMPOTENCY_CONFLICT"):
            self.service.start({"objective": "ETH trend", "idempotency_key": "same-request"})

    def test_migration_backfills_a_legacy_start_for_future_retries(self):
        payload = {
            "objective": "Evaluate legacy AAPL momentum",
            "instrument_scope": ["AAPL"],
            "provider": "OPENBB",
            "frequency": "1d",
        }
        started = self.service.start(payload)
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE research_agent_sessions SET idempotency_key='' WHERE session_id=?",
                (started["session_id"],),
            )
            conn.execute("DELETE FROM schema_migrations WHERE migration_version=25")

        migrated_store = DataPlatformStore(Path(self.temp.name) / "metadata.db")
        migrated_service = ResearchAgentSessionService(migrated_store)
        retried = migrated_service.start(payload)

        self.assertEqual(started["project_id"], retried["project_id"])
        self.assertEqual(started["session_id"], retried["session_id"])
        self.assertTrue(retried["idempotency_reused"])

    def test_resume_project_restores_context_without_changing_original_baseline(self):
        started = self.service.start({"objective": "BTC trend"})
        resumed = self.service.resume("PROJECT", started["project_id"])

        self.assertEqual("RESUME", resumed["entry_mode"])
        self.assertEqual(started["project_id"], resumed["context"]["project_id"])
        self.assertEqual("", resumed["original_baseline_run_id"])
        self.assertEqual("", resumed["current_branch_head_run_id"])

    def test_resume_project_preserves_the_approved_equity_brief_and_scope(self):
        started = self.service.start(
            {
                "objective": "Evaluate Magnificent Seven momentum",
                "instrument_scope": ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA"],
                "provider": "OPENBB",
                "frequency": "1d",
                "research_period": {"start": "2022-01-01", "end": "2026-08-09"},
            }
        )

        resumed = self.service.resume("PROJECT", started["project_id"])

        self.assertEqual("OPENBB", resumed["brief"]["provider"])
        self.assertEqual("1d", resumed["brief"]["frequency"])
        self.assertEqual(started["brief"]["instrument_scope"], resumed["brief"]["instrument_scope"])
        self.assertEqual(started["brief"]["research_period"], resumed["brief"]["research_period"])
        self.assertEqual(["OPENBB"], resumed["resolved_grant_scope"]["allowed_providers"])
        self.assertEqual(["1d"], resumed["resolved_grant_scope"]["allowed_intervals"])
        self.assertEqual("PROJECT_PLAN", resumed["resume_brief_source"])

    def test_resume_recovers_an_equity_start_after_legacy_default_drift(self):
        started = self.service.start(
            {
                "objective": "Evaluate Magnificent Seven momentum",
                "instrument_scope": ["AAPL", "MSFT"],
                "provider": "OPENBB",
                "frequency": "1d",
            }
        )
        self.service._create_internal_research_budget(
            started["project_id"],
            {
                "objective": started["brief"]["objective"],
                "instrument_scope": "",
                "provider": "BINANCE",
                "frequency": "1h",
                "research_period": {"start": "2021-01-01", "end": "2026-08-09"},
            },
            created_by="legacy_resume",
        )

        resumed = self.service.resume("PROJECT", started["project_id"])

        self.assertEqual("OPENBB", resumed["brief"]["provider"])
        self.assertEqual("1d", resumed["brief"]["frequency"])
        self.assertEqual(["AAPL", "MSFT"], resumed["brief"]["instrument_scope"])
        self.assertEqual("START_SESSION_RECOVERY", resumed["resume_brief_source"])

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

    def test_missing_resume_anchor_does_not_create_a_need_human_session(self):
        with self.assertRaises(ValueError) as caught:
            self.service.resume(
                "RUN", "run_missing", {"objective": "continue BTC research"}
            )
        self.assertEqual("RESEARCH_RESUME_ANCHOR_NOT_FOUND", caught.exception.code)
        with self.store.connection() as conn:
            self.assertEqual(
                0,
                conn.execute("SELECT COUNT(*) FROM research_agent_sessions").fetchone()[0],
            )

    def test_terminal_session_cannot_transition_back_to_need_human(self):
        session = self.service.start({"objective": "BTC terminal session"})
        self.service.set_status(session["session_id"], "CANCELLED")

        with self.assertRaises(ValueError) as caught:
            self.service.need_human(
                session["session_id"],
                reason_code="MATERIAL_SCOPE_CHANGE",
                question="Change scope?",
            )

        self.assertEqual("RESEARCH_SESSION_TERMINAL", caught.exception.code)


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

    def test_start_api_returns_the_existing_session_for_a_repeated_request(self):
        payload = {
            "entry_mode": "START",
            "objective": "AAPL daily momentum",
            "instrument_scope": ["AAPL"],
            "provider": "OPENBB",
            "frequency": "1d",
            "idempotency_key": "aapl-daily-momentum",
        }
        first = self.client.post("/api/agent/research/sessions", json=payload)
        second = self.client.post("/api/agent/research/sessions", json=payload)

        self.assertEqual(201, first.status_code, first.get_json())
        self.assertEqual(200, second.status_code, second.get_json())
        self.assertEqual(first.get_json()["data"]["project_id"], second.get_json()["data"]["project_id"])
        self.assertEqual(first.get_json()["data"]["session_id"], second.get_json()["data"]["session_id"])
        self.assertTrue(second.get_json()["data"]["idempotency_reused"])

    def test_agent_write_cannot_implicitly_use_the_latest_session_grant(self):
        created = self.client.post(
            "/api/agent/research/sessions",
            json={"entry_mode": "START", "objective": "BTC session required"},
        ).get_json()["data"]

        response = self.client.post(
            f"/api/agent/research/projects/{created['project_id']}/definitions",
            json={"definition_type": "FACTOR", "spec": {"name": "missing_session", "version": "1.0.0"}},
        )

        self.assertEqual(403, response.status_code, response.get_json())
        self.assertEqual("RESEARCH_SESSION_REQUIRED", response.get_json()["code"])

    def test_session_from_another_project_is_rejected_before_the_write(self):
        first = self.client.post(
            "/api/agent/research/sessions",
            json={"entry_mode": "START", "objective": "BTC first project"},
        ).get_json()["data"]
        second = self.client.post(
            "/api/agent/research/sessions",
            json={"entry_mode": "START", "objective": "ETH second project"},
        ).get_json()["data"]

        response = self.client.post(
            f"/api/agent/research/projects/{first['project_id']}/definitions",
            json={
                "session_id": second["session_id"],
                "definition_type": "FACTOR",
                "spec": {"name": "wrong_project", "version": "1.0.0"},
            },
        )

        self.assertEqual(403, response.status_code, response.get_json())
        self.assertEqual("RESEARCH_SESSION_SCOPE_VIOLATION", response.get_json()["code"])

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
