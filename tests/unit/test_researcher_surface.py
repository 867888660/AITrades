from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app as app_module
from services.data_platform import (
    CanonicalBarsCommitter,
    DataPlatformStore,
    FactorPackRegistry,
    RequirementCompiler,
    RequirementMaintenanceService,
    ResearchAgentSessionService,
    ResearchExperimentService,
    ResearchRunPreviewService,
    ResearchRunService,
    UniverseService,
)
from services.data_platform.research_experiment_service import _factor_spec, _provider_bar_contract
from services.data_platform.research_run_service import PreviewStaleError


class ResearcherSurfaceApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = DataPlatformStore(Path(self.temp.name) / "metadata.db")
        self.store_patch = patch("app.get_default_store", return_value=self.store)
        self.audit_patch = patch("app._audit_agent_research", return_value=None)
        self.store_patch.start()
        self.audit_patch.start()
        self.client = app_module.app.test_client()

    def test_crsp_provider_uses_archive_bar_contract(self) -> None:
        expected = {
            "adjustment": "CRSP_FIELDS",
            "time_semantics": "SOURCE_AVAILABLE_TIME",
            "preferred_source": "crsp/ciz",
        }
        self.assertEqual(expected, _provider_bar_contract("CRSP"))
        self.assertEqual(
            expected,
            _provider_bar_contract(
                "OPENBB",
                universe_type="HISTORICAL_EQUITY_PIT",
            ),
        )
        self.assertEqual(
            {
                "adjustment": "NONE",
                "time_semantics": "BAR_END_AVAILABLE_TIME",
                "preferred_source": "openbb",
            },
            _provider_bar_contract("OPENBB", universe_type="STATIC_LIST"),
        )

    def test_pit_compile_overrides_default_openbb_with_crsp_contract(self) -> None:
        session = ResearchAgentSessionService(self.store).start({
            "objective": "Evaluate a US_EQUITY factor in a historical PIT universe",
            "frequency": "1d",
            "research_period": {"start": "2024-01-01", "end": "2024-12-31"},
            "universe_policy": {
                "eligibility": {
                    "mode": "HISTORICAL_EQUITY_PIT",
                    "security_types": ["COMMON_STOCK"],
                },
                "selection": {"method": "ALL_ELIGIBLE"},
            },
            "research_contract": {
                "evaluation": {
                    "run_type": "FACTOR_EVALUATION",
                    "primary_metric": "rank_ic",
                    "horizons": [1],
                },
            },
        })
        self.assertEqual("OPENBB", session["brief"]["provider"])
        service = ResearchExperimentService(self.store)
        snapshot = SimpleNamespace(
            universe_snapshot_id="snapshot_crsp_pit",
            as_of_time="2024-12-31T23:59:59+00:00",
            actual_instrument_ids=("equity:CRSP:10001",),
            selection_inputs={"method": "HISTORICAL_EQUITY_PIT"},
        )
        requirements = SimpleNamespace(requirement_set_id="reqset_crsp_pit")
        with (
            patch.object(UniverseService, "resolve_snapshot", return_value=snapshot),
            patch.object(UniverseService, "set_research_ref"),
            patch.object(RequirementCompiler, "compile", return_value=requirements) as compile_requirements,
            patch.object(service, "_try_preview_and_run"),
        ):
            experiment = service.submit(
                session["session_id"],
                {
                    "idempotency_key": "pit-openbb-contract-regression",
                    "candidate": {
                        "hypothesis": "One-day returns have cross-sectional predictive information",
                        "intervention_set": [
                            {"component": "factor", "change": "introduce one-day return"}
                        ],
                        "controlled_variables": ["universe", "research_period", "frequency"],
                        "factor": {
                            "name": "return_1d",
                            "operator": "pct_change",
                            "input_field": "close",
                            "window": 1,
                        },
                        "evaluation": {
                            "run_type": "FACTOR_EVALUATION",
                            "primary_metric": "rank_ic",
                            "horizons": [1],
                        },
                    },
                },
            )

        context = compile_requirements.call_args.kwargs["context"]
        self.assertEqual("CRSP_FIELDS", context["adjustment"])
        self.assertEqual("SOURCE_AVAILABLE_TIME", context["time_semantics"])
        internal = service.get(experiment["experiment_id"], public=False)
        self.assertEqual(
            ["crsp/ciz"],
            internal["execution_plan"]["source_selection_policy"]["preferred_sources"],
        )

    def test_experiment_tick_drains_provider_tasks_independently(self) -> None:
        with (
            patch.object(app_module.ResearchExperimentService, "advance_pending", return_value={"count": 1}) as advance,
            patch.object(app_module, "_start_binance_backfill_worker") as binance_worker,
            patch.object(app_module, "_start_openbb_export_worker") as openbb_worker,
            patch.object(app_module, "_start_polymarket_export_worker") as polymarket_worker,
        ):
            result = app_module._advance_research_experiments_once()

        self.assertEqual({"count": 1}, result)
        advance.assert_called_once_with(limit=20)
        binance_worker.assert_called_once_with()
        openbb_worker.assert_called_once_with()
        polymarket_worker.assert_called_once_with()

    def test_experiment_http_submit_returns_before_advancing(self) -> None:
        accepted = {
            "experiment_id": "experiment_async_submit",
            "status": "ACCEPTED",
            "phase": "COMPILING",
        }
        with patch.object(
            app_module.ResearchExperimentService,
            "submit",
            return_value=accepted,
        ) as submit:
            response = self.client.post(
                "/api/agent/researcher/sessions/session_async/experiments",
                json={
                    "actor_type": "human",
                    "actor_id": "local_user",
                    "candidate": {"hypothesis": "test"},
                },
            )

        self.assertEqual(201, response.status_code, response.get_json())
        args, kwargs = submit.call_args
        self.assertEqual("session_async", args[0])
        self.assertFalse(kwargs["advance_immediately"])

    def tearDown(self) -> None:
        self.audit_patch.stop()
        self.store_patch.stop()
        self.temp.cleanup()

    def test_zero_factor_window_is_rejected_instead_of_coerced_to_one(self) -> None:
        with self.assertRaisesRegex(ValueError, "factor window must be positive"):
            _factor_spec(
                {"name": "invalid_zero", "operator": "pct_change", "window": 0},
                "1d",
                "0" * 64,
            )

    def test_blocked_session_advertises_continue_action(self) -> None:
        session = ResearchAgentSessionService(self.store).start({
            "objective": "Evaluate AAPL daily momentum",
            "instrument_scope": ["AAPL"],
            "provider": "OPENBB",
            "frequency": "1d",
            "research_period": {"start": "2025-01-01", "end": "2025-02-01"},
            "universe_policy": {
                "eligibility": {"mode": "STATIC_LIST", "instrument_scope": ["AAPL"]},
                "selection": {"method": "ALL_ELIGIBLE"},
            },
        })
        ResearchAgentSessionService(self.store).set_status(
            session["session_id"], "BLOCKED", message="test recoverable block"
        )

        response = self.client.get(
            f"/api/agent/researcher/sessions/{session['session_id']}"
        )

        self.assertEqual(200, response.status_code, response.get_json())
        actions = response.get_json()["data"]["actions"]
        self.assertTrue(actions["can_continue"])
        self.assertFalse(actions["can_pause"])

    def test_preview_stale_refreshes_same_candidate_once(self) -> None:
        service = ResearchExperimentService(self.store)
        experiment = {
            "experiment_id": "experiment_stale_once",
            "session_id": "session_stale_once",
            "project_id": "project_stale_once",
            "execution_plan": {
                "requirement_set_id": "reqset_stale_once",
                "run_type": "FACTOR_EVALUATION",
                "universe_snapshot_id": "snapshot_stale_once",
                "source_selection_policy": {},
                "evaluation_spec": {},
                "contract_hash": "contract_hash",
                "candidate_hash": "candidate_hash",
                "goal_conformance": {"status": "PASS"},
            },
        }
        preview = {
            "preview_id": "preview_old",
            "preview_fingerprint": "fingerprint_old",
            "readiness": {"overall": {"status": "READY"}},
            "request": {"research_semantics": {
                "contract_hash": "contract_hash",
                "candidate_hash": "candidate_hash",
            }},
        }
        refreshed = {
            **preview,
            "preview_id": "preview_new",
            "preview_fingerprint": "fingerprint_new",
        }
        run = {"run_id": "run_stale_once", "bundle_id": "bundle_stale_once"}
        bundle = {"canonical_payload": {"research_semantics": {
            "contract_hash": "contract_hash",
            "candidate_hash": "candidate_hash",
        }}}
        with (
            patch.object(ResearchAgentSessionService, "get", return_value={"internal_grant_id": "grant"}),
            patch.object(ResearchRunPreviewService, "create", return_value=preview),
            patch.object(ResearchRunPreviewService, "refresh", return_value=refreshed) as refresh,
            patch.object(
                ResearchRunService,
                "create",
                side_effect=[PreviewStaleError("PREVIEW_STALE"), run],
            ) as create_run,
            patch.object(ResearchRunService, "get_bundle", return_value=bundle),
            patch.object(service, "_update"),
            patch.object(service, "_finish_run") as finish_run,
        ):
            service._try_preview_and_run(experiment)

        refresh.assert_called_once_with("preview_old")
        self.assertEqual(2, create_run.call_count)
        self.assertEqual("preview_new", create_run.call_args_list[1].kwargs["preview_id"])
        finish_run.assert_called_once()

    def test_not_ready_experiment_runs_maintenance_only_for_its_research(self) -> None:
        service = ResearchExperimentService(self.store)
        experiment = {
            "experiment_id": "experiment_scoped_maintenance",
            "session_id": "session_scoped_maintenance",
            "project_id": "project_scoped_maintenance",
            "execution_plan": {
                "requirement_set_id": "reqset_scoped_maintenance",
                "run_type": "FACTOR_EVALUATION",
                "universe_snapshot_id": "snapshot_scoped_maintenance",
                "source_selection_policy": {},
                "evaluation_spec": {},
                "contract_hash": "contract_hash",
                "candidate_hash": "candidate_hash",
                "goal_conformance": {"status": "PASS"},
            },
        }
        preview = {
            "preview_id": "preview_not_ready",
            "preview_fingerprint": "fingerprint_not_ready",
            "readiness": {
                "overall": {"status": "BLOCKED"},
                "dimensions": {"DATA": {"checks": [{
                    "code": "DATA_NOT_PREPARED", "status": "BLOCKED",
                }]}},
            },
            "request": {"research_semantics": {
                "contract_hash": "contract_hash",
                "candidate_hash": "candidate_hash",
            }},
        }
        with (
            patch.object(ResearchAgentSessionService, "get", return_value={"internal_grant_id": "grant"}),
            patch.object(ResearchRunPreviewService, "create", return_value=preview),
            patch.object(service, "_update"),
            patch.object(
                RequirementMaintenanceService,
                "run_once",
                return_value={
                    "task_types": ["OPENBB_EQUITY_DAILY_EXPORT"],
                    "errors": [],
                },
            ) as maintenance,
        ):
            service._try_preview_and_run(experiment)

        maintenance.assert_called_once_with(
            project_id="project_scoped_maintenance",
            requirement_set_id="reqset_scoped_maintenance",
        )

    def test_unpreparable_readiness_gap_does_not_stall_forever(self) -> None:
        service = ResearchExperimentService(self.store)
        experiment = {
            "experiment_id": "experiment_unpreparable",
            "session_id": "session_unpreparable",
            "project_id": "project_unpreparable",
            "execution_plan": {
                "requirement_set_id": "reqset_unpreparable",
                "run_type": "FACTOR_EVALUATION",
                "universe_snapshot_id": "snapshot_unpreparable",
                "source_selection_policy": {},
                "evaluation_spec": {},
                "contract_hash": "contract_hash",
                "candidate_hash": "candidate_hash",
                "goal_conformance": {"status": "PASS"},
            },
        }
        preview = {
            "preview_id": "preview_unpreparable",
            "preview_fingerprint": "fingerprint_unpreparable",
            "readiness": {
                "overall": {"status": "BLOCKED"},
                "dimensions": {"DATA": {"checks": [{
                    "code": "ADJUSTMENT_MISMATCH", "status": "BLOCKED",
                }]}},
            },
            "request": {"research_semantics": {
                "contract_hash": "contract_hash",
                "candidate_hash": "candidate_hash",
            }},
        }
        with (
            patch.object(ResearchAgentSessionService, "get", return_value={"internal_grant_id": "grant"}),
            patch.object(ResearchRunPreviewService, "create", return_value=preview),
            patch.object(service, "_update"),
            patch.object(
                RequirementMaintenanceService,
                "run_once",
                return_value={"task_types": [], "errors": []},
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "ADJUSTMENT_MISMATCH"):
                service._try_preview_and_run(experiment)

    def test_slow_experiment_does_not_block_other_pending_experiments(self) -> None:
        service = ResearchExperimentService(self.store)
        slow_started = threading.Event()
        release_slow = threading.Event()
        fast_finished = threading.Event()

        def advance(experiment_id: str) -> dict:
            if experiment_id == "experiment_slow":
                slow_started.set()
                release_slow.wait(timeout=5)
            else:
                fast_finished.set()
            return {"experiment_id": experiment_id}

        with (
            patch.object(
                service,
                "_pending_experiment_ids",
                return_value=["experiment_slow", "experiment_fast"],
            ),
            patch.object(service, "advance", side_effect=advance),
            patch.object(service._admission, "acquire", return_value=True),
        ):
            dispatched = service.advance_pending(limit=20)
            self.assertTrue(slow_started.wait(timeout=1))
            self.assertTrue(fast_finished.wait(timeout=1))
            self.assertEqual(2, dispatched["count"])
            release_slow.set()

        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with service._background_guard:
                if not {
                    "experiment_slow", "experiment_fast"
                } & service._background_inflight:
                    break
            time.sleep(0.01)
        with service._background_guard:
            self.assertFalse(
                {"experiment_slow", "experiment_fast"}
                & service._background_inflight
            )

    def test_heavy_experiment_runs_exclusively(self) -> None:
        service = ResearchExperimentService(self.store)
        heavy_started = threading.Event()
        release_heavy = threading.Event()

        def advance(experiment_id: str) -> dict:
            heavy_started.set()
            release_heavy.wait(timeout=5)
            return {"experiment_id": experiment_id}

        with (
            patch.object(
                service,
                "_pending_experiment_ids",
                return_value=["experiment_heavy_one", "experiment_heavy_two"],
            ),
            patch.object(service, "_resource_class", return_value="HEAVY"),
            patch.object(service, "advance", side_effect=advance),
            patch.object(service._admission, "acquire", return_value=True),
        ):
            dispatched = service.advance_pending(limit=20)
            self.assertTrue(heavy_started.wait(timeout=1))
            self.assertEqual(1, dispatched["count"])
            release_heavy.set()

    def test_equity_goal_without_universe_returns_research_guidance(self) -> None:
        response = self.client.post(
            "/api/agent/researcher/start",
            json={"objective": "研究美股中期动量是否稳定"},
        )

        self.assertEqual(422, response.status_code, response.get_json())
        body = response.get_json()
        self.assertEqual("RESEARCH_UNIVERSE_REQUIRED", body["code"])
        self.assertIn("recommended", body["context"])
        self.assertIn("大盘股", body["context"]["alternative"]["label"])
        with self.store.connection() as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM research_projects").fetchone()[0])

    def test_stock_synonyms_without_universe_return_research_guidance(self) -> None:
        for objective in ("研究美国股票中期动量是否稳定", "研究股票中期动量是否稳定"):
            with self.subTest(objective=objective):
                response = self.client.post(
                    "/api/agent/researcher/start",
                    json={"objective": objective},
                )

                self.assertEqual(422, response.status_code, response.get_json())
                self.assertEqual("RESEARCH_UNIVERSE_REQUIRED", response.get_json()["code"])
        with self.store.connection() as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM research_projects").fetchone()[0])

    def test_alignment_is_side_effect_free_and_returns_factor_plan(self) -> None:
        response = self.client.post(
            "/api/agent/researcher/align",
            json={
                "objective": "验证 AAPL 日频动量因子是否具有预测能力",
                "instrument_scope": ["AAPL"],
                "frequency": "1d",
                "research_period": {"start": "2024-01-01", "end": "2024-12-31"},
                "evidence_profile": "STANDARD",
            },
        )

        self.assertEqual(200, response.status_code, response.get_json())
        alignment = response.get_json()["data"]
        self.assertEqual("READY", alignment["status"])
        self.assertEqual("FACTOR", alignment["stop_at"])
        self.assertEqual("FACTOR_EVALUATION", alignment["route"])
        self.assertEqual("rank_ic", alignment["evidence"]["primary_metric"])
        self.assertIn("strategy creation", alignment["out_of_scope"])
        self.assertTrue(alignment["alignment_hash"])
        with self.store.connection() as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM research_projects").fetchone()[0])
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM research_agent_sessions").fetchone()[0])

    def test_start_accepts_the_frozen_alignment_as_the_semantic_handoff(self) -> None:
        request_payload = {
            "objective": "验证 AAPL 日频动量因子是否具有预测能力",
            "instrument_scope": ["AAPL"],
            "frequency": "1d",
            "research_period": {"start": "2024-01-01", "end": "2024-12-31"},
            "evidence_profile": "STANDARD",
        }
        aligned = self.client.post(
            "/api/agent/researcher/align", json=request_payload
        ).get_json()["data"]

        response = self.client.post(
            "/api/agent/researcher/start",
            json={"aligned_research_intent": aligned},
        )

        self.assertEqual(201, response.status_code, response.get_json())
        contract = response.get_json()["data"]["research_contract"]
        self.assertEqual("research-contract.v2", contract["schema_version"])
        self.assertEqual(aligned["alignment_hash"], contract["alignment_hash"])
        self.assertEqual("FACTOR", contract["stop_at"])

    def test_start_rejects_a_mutated_frozen_alignment(self) -> None:
        request_payload = {
            "objective": "验证 AAPL 日频动量因子是否具有预测能力",
            "instrument_scope": ["AAPL"],
            "frequency": "1d",
            "research_period": {"start": "2024-01-01", "end": "2024-12-31"},
        }
        aligned = self.client.post(
            "/api/agent/researcher/align", json=request_payload
        ).get_json()["data"]
        aligned["question"] = "确认后被修改的问题"

        response = self.client.post(
            "/api/agent/researcher/start",
            json={"aligned_research_intent": aligned},
        )

        self.assertEqual(422, response.status_code, response.get_json())
        self.assertEqual("RESEARCH_ALIGNMENT_HASH_MISMATCH", response.get_json()["code"])
        with self.store.connection() as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM research_projects").fetchone()[0])

    def test_alignment_requires_material_meaning_without_creating_objects(self) -> None:
        response = self.client.post(
            "/api/agent/researcher/align",
            json={"objective": "帮我研究一下"},
        )

        self.assertEqual(200, response.status_code, response.get_json())
        alignment = response.get_json()["data"]
        self.assertEqual("NEEDS_INPUT", alignment["status"])
        self.assertEqual(
            "RESEARCH_ASSET_SCOPE_REQUIRED",
            alignment["unresolved_material_question"]["reason_code"],
        )
        with self.store.connection() as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM research_projects").fetchone()[0])

    def test_researcher_library_returns_semantic_cards_only(self) -> None:
        response = self.client.get(
            "/api/agent/researcher/library?kind=FACTOR&q=momentum&asset_class=US_EQUITY&frequency=1d"
        )

        self.assertEqual(200, response.status_code, response.get_json())
        data = response.get_json()["data"]
        self.assertEqual([], data["items"])
        serialized = json.dumps(data).lower()
        for internal_name in (
            "source_object_id", "content_hash", "manifest_id", "requirement_set_id",
            "bundle_id", "preview_id", "provider",
        ):
            self.assertNotIn(internal_name, serialized)

    def test_researcher_library_does_not_treat_missing_metadata_as_compatible(self) -> None:
        rows = [
            {"library_asset_id": "known", "component_type": "FACTOR", "name": "Known", "version": 1,
             "content": {"spec": {"asset_class": "US_EQUITY", "frequency": "1d"}}},
            {"library_asset_id": "unknown", "component_type": "FACTOR", "name": "Unknown", "version": 1,
             "content": {"spec": {}}},
            {"library_asset_id": "crypto", "component_type": "FACTOR", "name": "Crypto", "version": 1,
             "content": {"spec": {"asset_class": "CRYPTO_SPOT", "frequency": "1h"}}},
        ]
        with patch.object(app_module.ResearchLibraryService, "list", return_value=rows):
            response = self.client.get(
                "/api/agent/researcher/library?kind=FACTOR&asset_class=US_EQUITY&frequency=1d"
            )

        self.assertEqual(200, response.status_code, response.get_json())
        items = {item["asset_ref"]: item for item in response.get_json()["data"]["items"]}
        self.assertEqual("COMPATIBLE", items["library:known"]["compatibility"])
        self.assertEqual("UNKNOWN", items["library:unknown"]["compatibility"])
        self.assertEqual(
            ["ASSET_CLASS_UNKNOWN", "FREQUENCY_UNKNOWN"],
            items["library:unknown"]["compatibility_reasons"],
        )
        self.assertEqual("INCOMPATIBLE", items["library:crypto"]["compatibility"])

    def test_researcher_resume_missing_anchor_returns_404_without_creating_session(self) -> None:
        response = self.client.post(
            "/api/agent/researcher/resume",
            json={"anchor_type": "RUN", "anchor_id": "run_missing"},
        )

        self.assertEqual(404, response.status_code, response.get_json())
        self.assertEqual("RESEARCH_RESUME_ANCHOR_NOT_FOUND", response.get_json()["code"])
        with self.store.connection() as conn:
            self.assertEqual(
                0,
                conn.execute("SELECT COUNT(*) FROM research_agent_sessions").fetchone()[0],
            )

    def test_portfolio_alignment_routes_to_research_backtest(self) -> None:
        payload = {
            "objective": "回测 BTC 动量策略能不能赚钱",
            "instrument_scope": ["BTCUSDT"],
            "frequency": "1h",
            "research_period": {"start": "2024-01-01", "end": "2024-12-31"},
        }
        aligned = self.client.post("/api/agent/researcher/align", json=payload)
        self.assertEqual(200, aligned.status_code, aligned.get_json())
        alignment = aligned.get_json()["data"]
        self.assertEqual("READY", alignment["status"])
        self.assertEqual("PORTFOLIO_EVIDENCE", alignment["stop_at"])
        self.assertEqual("RESEARCH_BACKTEST", alignment["route"])

        started = self.client.post("/api/agent/researcher/start", json=payload)
        self.assertEqual(201, started.status_code, started.get_json())
        self.assertEqual(
            "PORTFOLIO_EVIDENCE",
            started.get_json()["data"]["research_contract"]["stop_at"],
        )
        with self.store.connection() as conn:
            self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM research_projects").fetchone()[0])

    def test_factor_alignment_rejects_portfolio_primary_metrics(self) -> None:
        response = self.client.post(
            "/api/agent/researcher/align",
            json={
                "objective": "验证 AAPL 日频动量因子是否具有预测能力",
                "instrument_scope": ["AAPL"],
                "frequency": "1d",
                "research_period": {"start": "2024-01-01", "end": "2024-12-31"},
                "research_contract": {
                    "evaluation": {"primary_metric": "sharpe_ratio"}
                },
            },
        )

        self.assertEqual(422, response.status_code, response.get_json())
        self.assertEqual("RESEARCH_PRIMARY_METRIC_INVALID", response.get_json()["code"])
        with self.store.connection() as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM research_projects").fetchone()[0])

    def test_researcher_session_list_exposes_plan_not_execution_ir(self) -> None:
        started = self.client.post(
            "/api/agent/researcher/start",
            json={
                "objective": "验证 AAPL 日频动量因子是否具有预测能力",
                "instrument_scope": ["AAPL"],
                "frequency": "1d",
                "research_period": {"start": "2024-01-01", "end": "2024-12-31"},
            },
        )
        self.assertEqual(201, started.status_code, started.get_json())

        response = self.client.get("/api/agent/researcher/sessions?limit=10")
        self.assertEqual(200, response.status_code, response.get_json())
        item = response.get_json()["data"][0]
        self.assertEqual("FACTOR", item["research_plan"]["stop_at"])
        serialized = json.dumps(item).lower()
        for internal_name in (
            "project_id", "provider", "manifest_id", "requirement_set_id",
            "bundle_id", "preview_id", "run_id", "internal_status",
        ):
            self.assertNotIn(internal_name, serialized)

    def test_researcher_lifecycle_replaces_legacy_session_actions(self) -> None:
        started = self.client.post(
            "/api/agent/researcher/start",
            json={
                "objective": "验证 AAPL 日频动量因子是否具有预测能力",
                "instrument_scope": ["AAPL"],
                "frequency": "1d",
                "research_period": {"start": "2024-01-01", "end": "2024-12-31"},
            },
        ).get_json()["data"]
        session_id = started["session_id"]

        paused = self.client.post(
            f"/api/agent/researcher/sessions/{session_id}/status",
            json={"actor_type": "human", "status": "PAUSED", "message": "review"},
        )
        self.assertEqual(200, paused.status_code, paused.get_json())
        self.assertEqual("PAUSED", paused.get_json()["data"]["status"])
        continued = self.client.post(
            f"/api/agent/researcher/sessions/{session_id}/continue",
            json={"actor_type": "human"},
        )
        self.assertEqual(200, continued.status_code, continued.get_json())

        legacy = self.client.get("/api/agent/research/sessions")
        self.assertEqual("true", legacy.headers["Deprecation"])
        self.assertIn("/api/agent/researcher/sessions", legacy.headers["Link"])
        self.assertTrue(legacy.headers["Sunset"])

    def test_researcher_resume_uses_the_high_level_facade(self) -> None:
        started = self.client.post(
            "/api/agent/researcher/start",
            json={
                "objective": "验证 AAPL 日频动量因子是否具有预测能力",
                "instrument_scope": ["AAPL"],
                "frequency": "1d",
                "research_period": {"start": "2024-01-01", "end": "2024-12-31"},
            },
        ).get_json()["data"]
        response = self.client.post(
            "/api/agent/researcher/resume",
            json={
                "anchor_type": "SESSION",
                "anchor_id": started["session_id"],
                "objective": "继续验证 AAPL 日频动量因子的稳定性",
                "stop_at": "FACTOR",
            },
        )

        self.assertEqual(201, response.status_code, response.get_json())
        resumed = response.get_json()["data"]
        self.assertEqual("RESUME", resumed["research_plan"]["entry_mode"])
        self.assertEqual("FACTOR", resumed["research_plan"]["stop_at"])
        self.assertNotIn("project_id", json.dumps(resumed).lower())

    def test_researcher_start_returns_contract_without_execution_ir(self) -> None:
        response = self.client.post(
            "/api/agent/researcher/start",
            json={
                "objective": "研究 AAPL 日频动量",
                "instrument_scope": ["AAPL"],
                "frequency": "1d",
                "research_period": {"start": "2024-01-01", "end": "2024-12-31"},
                "universe_policy": {
                    "eligibility": {"mode": "STATIC_LIST", "instrument_scope": ["AAPL"]},
                    "selection": {"method": "ALL_ELIGIBLE"},
                },
            },
        )

        self.assertEqual(201, response.status_code, response.get_json())
        data = response.get_json()["data"]
        self.assertEqual("ACTIVE", data["research_contract"]["status"])
        self.assertEqual("US_EQUITY", data["research_contract"]["asset_scope"]["asset_class"])
        serialized = str(data).lower()
        for internal_name in ("manifest_id", "requirement_set_id", "bundle_id", "preview_id", "provider"):
            self.assertNotIn(internal_name, serialized)

        capabilities = self.client.get("/api/agent/capabilities?section=researcher").get_json()["data"]
        self.assertIn("research.experiment.submit", capabilities["allow"])
        self.assertNotIn("research.requirement.compile", capabilities["allow"])
        surface = capabilities["research_session_capabilities"]["researcher_surface"]
        self.assertEqual("/api/agent/researcher/align", surface["alignment_endpoint"])
        self.assertEqual(
            ["UNIVERSE", "FACTOR", "ALPHA", "PORTFOLIO_EVIDENCE"],
            surface["supported_stop_at"],
        )
        self.assertEqual("research-contract.v2", surface["research_contract_schema"])
        self.assertEqual(
            "market_cap_usd",
            surface["universe_capabilities"]["dynamic_point_in_time_filters"][0]["field"],
        )
        self.assertTrue(
            surface["universe_capabilities"]["dynamic_point_in_time_filters"][0][
                "requires_frozen_formal_evaluation"
            ]
        )
        self.assertIn("BLOCKED", surface["status_semantics"])
        packs = capabilities["factor_pack_capabilities"]["available"]
        self.assertEqual("qlib.alpha158_without_vwap", packs[0]["pack_id"])
        self.assertEqual(157, packs[0]["factor_count"])
        self.assertFalse(packs[0]["is_standard_alpha158"])

        internal_session = ResearchAgentSessionService(self.store).get(data["session_id"])
        denied = self.client.post(
            f"/api/agent/research/projects/{internal_session['project_id']}/definitions",
            json={
                "actor_type": "agent",
                "actor_id": "datatube_researcher",
                "session_id": data["session_id"],
                "definition_type": "FACTOR",
                "spec": {
                    "name": "forbidden_low_level",
                    "version": "1.0.0",
                    "operator": "pct_change",
                    "window": 1,
                    "frequency": "1d",
                },
            },
        )
        self.assertEqual(403, denied.status_code, denied.get_json())
        self.assertEqual("RESEARCHER_INFRASTRUCTURE_SURFACE_DENIED", denied.get_json()["code"])


class ResearchExperimentVerticalSliceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.root = root
        self.store = DataPlatformStore(root / "metadata.db")
        self.artifact_root_patch = patch(
            "services.data_platform.artifact_service.get_data_platform_storage_root",
            return_value=root / "artifacts",
        )
        self.inspection_db_patch = patch(
            "services.inspection_service._strategy_db_path",
            return_value=root / "inspection.db",
        )
        self.artifact_root_patch.start()
        self.inspection_db_patch.start()
        # Include enough history for both ordinary Factors and native Factor
        # Packs. The compiler, not the Agent, derives this requirement.
        start = datetime(2023, 10, 1, tzinfo=timezone.utc)
        rows = []
        for index in range(160):
            price = 100.0 + index + (2.0 if index % 5 == 0 else 0.0)
            rows.append(
                {
                    "instrument_id": "AAPL",
                    "frequency": "1d",
                    "bar_start_time": (start + timedelta(days=index)).isoformat(),
                    "bar_end_time": (start + timedelta(days=index + 1)).isoformat(),
                    "available_time": (start + timedelta(days=index + 1)).isoformat(),
                    "ingested_at": "2025-01-01T00:00:00+00:00",
                    "open": price - 0.5,
                    "high": price + 1.0,
                    "low": price - 1.0,
                    "close": price,
                    "volume": 1_000_000.0,
                    "turnover": price * 1_000_000.0,
                    "trade_count": 1000,
                    "bar_status": "COMPLETE",
                    "source": "OPENBB",
                    "source_version": "test",
                    "quality_status": "PASS",
                }
            )
        CanonicalBarsCommitter(self.store, root / "bars").commit(
            dataset_id="openbb:AAPL:1d",
            instrument_id="AAPL",
            asset_class="equity",
            venue="XNAS",
            frequency="1d",
            source="OPENBB",
            source_version="test",
            rows=rows,
        )

    def tearDown(self) -> None:
        self.inspection_db_patch.stop()
        self.artifact_root_patch.stop()
        self.temp.cleanup()

    def test_same_candidate_can_run_in_two_research_projects(self) -> None:
        sessions = ResearchAgentSessionService(self.store)
        brief = {
            "objective": "Evaluate the same AAPL daily momentum candidate",
            "instrument_scope": ["AAPL"],
            "provider": "OPENBB",
            "frequency": "1d",
            "research_period": {"start": "2024-01-01", "end": "2024-02-09"},
            "universe_policy": {
                "eligibility": {
                    "mode": "STATIC_LIST",
                    "instrument_scope": ["AAPL"],
                },
                "selection": {"method": "ALL_ELIGIBLE"},
            },
            "research_contract": {
                "evaluation": {
                    "run_type": "FACTOR_EVALUATION",
                    "primary_metric": "ic",
                    "horizons": [1],
                    "minimum_cross_section_size": 1,
                }
            },
        }
        first_session = sessions.start({**brief, "idempotency_key": "cross-project-one"})
        second_session = sessions.start({**brief, "idempotency_key": "cross-project-two"})
        self.assertNotEqual(first_session["project_id"], second_session["project_id"])
        candidate = {
            "hypothesis": "Two-day momentum has predictive information",
            "intervention_set": [
                {"component": "factor", "change": "introduce two-day momentum"}
            ],
            "controlled_variables": ["universe", "research_period", "frequency"],
            "factor": {
                "name": "momentum_2_1",
                "operator": "pct_change",
                "input_field": "close",
                "window": 2,
                "output_direction": "HIGHER_IS_BETTER",
            },
            "evaluation": {
                "run_type": "FACTOR_EVALUATION",
                "primary_metric": "ic",
                "horizons": [1],
                "minimum_cross_section_size": 1,
            },
        }
        service = ResearchExperimentService(self.store)
        with patch.object(RequirementMaintenanceService, "run_once") as maintenance:
            first = service.submit(
                first_session["session_id"],
                {"idempotency_key": "candidate-one", "candidate": candidate},
            )
            second = service.submit(
                second_session["session_id"],
                {"idempotency_key": "candidate-two", "candidate": candidate},
            )

        maintenance.assert_not_called()

        self.assertEqual("COMPLETE", first["status"], first)
        self.assertEqual("COMPLETE", second["status"], second)
        first_internal = service.get(first["experiment_id"], public=False)
        second_internal = service.get(second["experiment_id"], public=False)
        self.assertEqual(first_internal["candidate_hash"], second_internal["candidate_hash"])
        first_definition_id = first_internal["execution_plan"]["factor_definition_id"]
        second_definition_id = second_internal["execution_plan"]["factor_definition_id"]
        self.assertNotEqual(first_definition_id, second_definition_id)
        with self.store.connection() as conn:
            owners = {
                str(row["definition_id"]): str(row["owner_project_id"])
                for row in conn.execute(
                    "SELECT definition_id,owner_project_id FROM research_definitions "
                    "WHERE definition_id IN (?,?)",
                    (first_definition_id, second_definition_id),
                ).fetchall()
            }
        self.assertEqual(first_session["project_id"], owners[first_definition_id])
        self.assertEqual(second_session["project_id"], owners[second_definition_id])

    def test_same_experiment_cannot_be_advanced_concurrently(self) -> None:
        session = ResearchAgentSessionService(self.store).start({
            "objective": "Serialize one experiment advance",
            "instrument_scope": ["AAPL"],
            "provider": "OPENBB",
            "frequency": "1d",
            "research_period": {"start": "2024-01-01", "end": "2024-02-09"},
            "universe_policy": {
                "eligibility": {"mode": "STATIC_LIST", "instrument_scope": ["AAPL"]},
                "selection": {"method": "ALL_ELIGIBLE"},
            },
        })
        candidate = {
            "hypothesis": "Two-day momentum has predictive information",
            "intervention_set": [{"component": "factor", "change": "add momentum"}],
            "controlled_variables": ["universe"],
            "factor": {
                "name": "serialized_momentum",
                "operator": "pct_change",
                "input_field": "close",
                "window": 2,
            },
            "evaluation": {"run_type": "FACTOR_EVALUATION", "horizons": [1]},
        }
        service = ResearchExperimentService(self.store)
        with patch.object(service, "advance", return_value={}):
            experiment = service.submit(
                session["session_id"],
                {"idempotency_key": "serialized-experiment", "candidate": candidate},
            )
        entered = threading.Event()
        release = threading.Event()
        compile_calls = 0

        def fake_compile(instance, current):
            nonlocal compile_calls
            compile_calls += 1
            entered.set()
            release.wait(timeout=5)
            instance._update(
                current["experiment_id"],
                status="CANCELLED",
                phase="CANCELLED",
                completed=True,
            )

        first_service = ResearchExperimentService(self.store)
        second_service = ResearchExperimentService(self.store)
        with patch.object(ResearchExperimentService, "_compile", autospec=True, side_effect=fake_compile):
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(first_service.advance, experiment["experiment_id"])
                self.assertTrue(entered.wait(timeout=5))
                second = pool.submit(second_service.advance, experiment["experiment_id"])
                time.sleep(0.05)
                release.set()
                first.result(timeout=5)
                second.result(timeout=5)
        self.assertEqual(1, compile_calls)

    def test_factor_candidate_compiles_runs_and_returns_research_result(self) -> None:
        session = ResearchAgentSessionService(self.store).start(
            {
                "objective": "研究 AAPL 日频动量",
                "instrument_scope": ["AAPL"],
                "provider": "OPENBB",
                "frequency": "1d",
                "research_period": {
                    "start": "2024-01-01",
                    "end": "2024-02-09",
                },
                "universe_policy": {
                    "eligibility": {"mode": "STATIC_LIST", "instrument_scope": ["AAPL"]},
                    "selection": {"method": "ALL_ELIGIBLE"},
                },
                "research_contract": {
                    "evaluation": {
                        "run_type": "FACTOR_EVALUATION",
                        "primary_metric": "ic",
                    }
                },
            }
        )
        experiment = ResearchExperimentService(self.store).submit(
            session["session_id"],
            {
                "idempotency_key": "aapl-momentum-one",
                "candidate": {
                    "hypothesis": {
                        "statement": "AAPL 的短期收益具有延续性",
                        "expected_direction": "POSITIVE",
                    },
                    "intervention_set": [
                        {"component": "factor", "change": "introduce 2-day return"}
                    ],
                    "controlled_variables": ["universe", "research_period", "frequency"],
                    "factor": {
                        "name": "return_2d",
                        "operator": "pct_change",
                        "input_field": "close",
                        "window": 2,
                        "output_direction": "HIGHER_IS_BETTER",
                    },
                    "evaluation": {
                        "run_type": "FACTOR_EVALUATION",
                        "primary_metric": "ic",
                        "horizons": [1],
                        "minimum_cross_section_size": 1,
                    },
                },
            },
        )

        self.assertEqual("COMPLETE", experiment["status"], experiment)
        self.assertNotIn("execution_plan", experiment)
        self.assertNotIn("run_id", experiment)
        self.assertNotIn("project_id", experiment)
        self.assertNotIn("manifest_id", str(experiment["result"]).lower())
        self.assertEqual("FACTOR_EVALUATION", experiment["result"]["product_type"])
        self.assertEqual("PASS", experiment["result"]["goal_conformance"])
        self.assertTrue(experiment["result"]["provenance"]["reproducible"])
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT context_json FROM requirement_sets ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        requirement_context = json.loads(row["context_json"])
        self.assertEqual("2024-01-01T00:00:00+00:00", requirement_context["history_start"])
        self.assertEqual("2024-02-09T23:59:59+00:00", requirement_context["history_end"])

        decided = ResearchExperimentService(self.store).decide(
            experiment["experiment_id"],
            "KEEP",
            {"summary": "短期收益因子已产生可复现证据。"},
        )
        self.assertEqual("KEEP", decided["decision"])

        second = ResearchExperimentService(self.store).submit(
            session["session_id"],
            {
                "candidate": {
                    "hypothesis": "两日价格差比两日收益具有更稳定的延续性",
                    "intervention_set": [
                        {"component": "factor", "change": "replace two-day return with two-day price difference"}
                    ],
                    "controlled_variables": ["universe", "research_period", "frequency"],
                    "factor": {
                        "name": "difference_2d",
                        "operator": "difference",
                        "input_field": "close",
                        "window": 2,
                        "output_direction": "HIGHER_IS_BETTER",
                    },
                    "evaluation": {
                        "run_type": "FACTOR_EVALUATION",
                        "primary_metric": "ic",
                        "horizons": [1],
                        "minimum_cross_section_size": 1,
                    },
                }
            },
        )
        self.assertEqual("COMPLETE", second["status"], second)
        self.assertEqual(
            experiment["experiment_id"],
            second["result"]["comparison"]["control_experiment_id"],
        )
        second_internal = ResearchExperimentService(self.store).get(
            second["experiment_id"], public=False
        )
        run_service = ResearchRunService(self.store)
        second_run = run_service.get(second_internal["run_id"])
        closure = run_service.get_bundle(second_run["bundle_id"])["canonical_payload"]["input_closure"]
        self.assertEqual(1, len(closure["factor_definitions"]))
        self.assertEqual([], closure["alpha_definitions"])

    def test_candidate_cannot_control_infrastructure(self) -> None:
        session = ResearchAgentSessionService(self.store).start(
            {
                "objective": "研究 AAPL 日频动量",
                "instrument_scope": ["AAPL"],
                "provider": "OPENBB",
                "frequency": "1d",
                "research_period": {"start": "2024-01-01", "end": "2024-02-09"},
                "universe_policy": {
                    "eligibility": {"mode": "STATIC_LIST", "instrument_scope": ["AAPL"]},
                    "selection": {"method": "ALL_ELIGIBLE"},
                },
            }
        )

        with self.assertRaisesRegex(ValueError, "不能控制基础设施字段"):
            ResearchExperimentService(self.store).submit(
                session["session_id"],
                {
                    "candidate": {
                        "hypothesis": "测试短期动量",
                        "intervention_set": [{"component": "factor", "change": "add return"}],
                        "factor": {"name": "bad", "operator": "pct_change", "window": 2},
                        "manifest_id": "manifest_forbidden",
                    }
                },
            )

    def test_alpha_candidate_compiles_runs_without_portfolio_evidence(self) -> None:
        session = ResearchAgentSessionService(self.store).start(
            {
                "objective": "研究 AAPL 短期动量信号的预测能力",
                "instrument_scope": ["AAPL"],
                "provider": "OPENBB",
                "frequency": "1d",
                "research_period": {
                    "start": "2024-01-01T00:00:00+00:00",
                    "end": "2024-02-09T00:00:00+00:00",
                },
                "universe_policy": {
                    "eligibility": {"mode": "STATIC_LIST", "instrument_scope": ["AAPL"]},
                    "selection": {"method": "ALL_ELIGIBLE"},
                },
                "research_contract": {
                    "evaluation": {
                        "run_type": "ALPHA_EVALUATION",
                        "primary_metric": "rank_ic",
                        "horizons": [1],
                        "minimum_cross_section_size": 1,
                    }
                },
            }
        )
        service = ResearchExperimentService(self.store)
        experiment = service.submit(
            session["session_id"],
            {
                "candidate": {
                    "hypothesis": "把两日收益转换成排序信号后，对下一期收益具有正向预测能力",
                    "intervention_set": [
                        {"component": "alpha", "change": "rank the two-day return factor"}
                    ],
                    "controlled_variables": ["universe", "research_period", "frequency"],
                    "factor": {
                        "name": "return_2d",
                        "operator": "pct_change",
                        "input_field": "close",
                        "window": 2,
                        "output_direction": "HIGHER_IS_BETTER",
                    },
                    "alpha": {
                        "name": "return_rank_alpha",
                        "weight": 1.0,
                        "transform": "RAW",
                        "minimum_cross_section_size": 1,
                    },
                    "evaluation": {
                        "run_type": "ALPHA_EVALUATION",
                        "primary_metric": "rank_ic",
                    },
                }
            },
        )

        self.assertEqual("COMPLETE", experiment["status"], experiment)
        result = experiment["result"]
        self.assertEqual("ALPHA_EVALUATION", result["product_type"])
        self.assertEqual("PASS", result["goal_conformance"])
        serialized = str(result).lower()
        for forbidden in (
            "manifest_id", "bundle_id", "definition_id", "artifact_id",
            "portfolio_targets", "positions", "trades", "equity_curve",
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertNotIn("'performance':", serialized)
        internal = service.get(experiment["experiment_id"], public=False)
        run_service = ResearchRunService(self.store)
        bundle = run_service.get_bundle(run_service.get(internal["run_id"])["bundle_id"])
        semantics = bundle["canonical_payload"]["research_semantics"]
        self.assertEqual(internal["candidate_hash"], semantics["candidate_hash"])
        with self.store.connection() as conn:
            contract_hash = conn.execute(
                "SELECT contract_hash FROM research_contracts WHERE contract_id=?",
                (internal["contract_id"],),
            ).fetchone()[0]
        self.assertEqual(contract_hash, semantics["contract_hash"])
        service.decide(
            experiment["experiment_id"],
            "KEEP",
            {"summary": "信号已产生正式预测评价证据。"},
        )
        with patch("app.get_default_store", return_value=self.store):
            status = app_module.app.test_client().get(
                f"/api/agent/researcher/sessions/{session['session_id']}"
            ).get_json()["data"]
        self.assertEqual("ALPHA_EVALUATION", status["current_champion"]["product_type"])
        self.assertEqual("信号已产生正式预测评价证据。", status["latest_learning"]["summary"])

    def test_candidate_cannot_change_contract_product_or_primary_metric(self) -> None:
        session = ResearchAgentSessionService(self.store).start(
            {
                "objective": "研究 AAPL 日频动量",
                "instrument_scope": ["AAPL"],
                "provider": "OPENBB",
                "frequency": "1d",
                "research_period": {"start": "2024-01-01", "end": "2024-02-09"},
                "universe_policy": {
                    "eligibility": {"mode": "STATIC_LIST", "instrument_scope": ["AAPL"]},
                    "selection": {"method": "ALL_ELIGIBLE"},
                },
                "research_contract": {
                    "evaluation": {"run_type": "FACTOR_EVALUATION", "primary_metric": "ic"}
                },
            }
        )
        base = {
            "hypothesis": "两日收益具有延续性",
            "intervention_set": [{"component": "factor", "change": "add two-day return"}],
            "factor": {"name": "return_2d", "operator": "pct_change", "window": 2},
        }
        with self.assertRaisesRegex(ValueError, "不能改变 Research Contract"):
            ResearchExperimentService(self.store).submit(
                session["session_id"],
                {"candidate": {**base, "evaluation": {"run_type": "ALPHA_EVALUATION"}}},
            )
        with self.assertRaisesRegex(ValueError, "不能在看到结果前更换"):
            ResearchExperimentService(self.store).submit(
                session["session_id"],
                {"candidate": {**base, "evaluation": {"primary_metric": "rank_ic"}}},
            )

    def test_native_alpha158_factor_pack_is_frozen_and_evaluated(self) -> None:
        import pandas as pd

        pack = FactorPackRegistry.require("qlib.alpha158_without_vwap")
        factor_names = [f"PACK{index:03d}" for index in range(pack.factor_count)]
        dates = pd.date_range("2024-01-01", "2024-02-09", freq="D")
        frame = pd.DataFrame({
            "datetime": dates,
            "instrument": ["AAPL"] * len(dates),
            **{
                name: [float(day + index) / 1000.0 for day in range(len(dates))]
                for index, name in enumerate(factor_names)
            },
        })
        factor_path = self.root / "mock-alpha158.parquet"
        frame.to_parquet(factor_path, index=False)
        imported = {
            "status": "READY",
            "cache_hit": False,
            "cache_id": "mock-alpha158-cache",
            "factor_path": str(factor_path),
            "manifest": {
                "factor_frame_schema_version": "factor-frame.wide.v1",
                "pack_id": pack.pack_id,
                "factor_count": pack.factor_count,
                "factor_names": factor_names,
                "excluded_factors": list(pack.excluded_factors),
                "is_standard_alpha158": False,
                "engine": {"qlib_version": "test"},
                "output": {
                    "sha256": "test-factor-pack-sha256",
                    "row_count": len(frame),
                    "instrument_count": 1,
                },
            },
        }
        session = ResearchAgentSessionService(self.store).start(
            {
                "objective": "评价 AAPL 上的 Qlib Alpha158 without VWAP 因子包",
                "instrument_scope": ["AAPL"],
                "provider": "OPENBB",
                "frequency": "1d",
                "research_period": {
                    "start": "2024-01-01T00:00:00+00:00",
                    "end": "2024-02-09T00:00:00+00:00",
                },
                "universe_policy": {
                    "eligibility": {"mode": "STATIC_LIST", "instrument_scope": ["AAPL"]},
                    "selection": {"method": "ALL_ELIGIBLE"},
                },
                "research_contract": {
                    "evaluation": {
                        "run_type": "FACTOR_EVALUATION",
                        "primary_metric": "rank_ic",
                        "horizons": [1],
                        "minimum_cross_section_size": 1,
                    }
                },
            }
        )
        with patch(
            "integrations.qlib.Alpha158ImportService.run",
            return_value=imported,
        ):
            experiment = ResearchExperimentService(
                self.store, isolate_run_execution=False
            ).submit(
                session["session_id"],
                {
                    "candidate": {
                        "hypothesis": "Alpha158 no-VWAP 因子包中存在稳定的预测因子",
                        "intervention_set": [
                            {"component": "factor_pack", "change": "evaluate native Alpha158 pack"}
                        ],
                        "controlled_variables": ["universe", "research_period", "frequency"],
                        "factor_pack": {"pack_id": pack.pack_id},
                        "evaluation": {
                            "run_type": "FACTOR_EVALUATION",
                            "primary_metric": "rank_ic",
                            "horizons": [1],
                            "minimum_cross_section_size": 1,
                        },
                    }
                },
            )

        self.assertEqual("COMPLETE", experiment["status"], experiment)
        result = experiment["result"]
        self.assertEqual("FACTOR_PACK", result["research_object"])
        self.assertEqual(pack.factor_count, result["decision_metrics"]["factor_count"])
        self.assertEqual(pack.factor_count, result["decision_metrics"]["evaluated_factor_count"])
        self.assertEqual(pack.pack_id, result["product"]["factor_pack"]["pack_id"])
        self.assertFalse(result["product"]["factor_pack"]["is_standard_alpha158"])
        self.assertEqual(["VWAP0"], result["product"]["factor_pack"]["excluded_factors"])
        self.assertNotIn("VWAP0", [item["factor_name"] for item in result["product"]["top_factors"]])
        serialized = str(result).lower()
        for internal_name in ("manifest_id", "bundle_id", "artifact_id", "requirement_set_id"):
            self.assertNotIn(internal_name, serialized)
        internal = ResearchExperimentService(self.store).get(
            experiment["experiment_id"], public=False
        )
        run_service = ResearchRunService(self.store)
        run = run_service.get(internal["run_id"])
        closure = run_service.get_bundle(run["bundle_id"])["canonical_payload"]["input_closure"]
        self.assertEqual([], closure["factor_definitions"])
        self.assertEqual(pack.spec_hash, closure["factor_pack_definitions"][0]["spec_hash"])

    def _start_product_session(self, objective: str, stop_at: str, primary_metric: str) -> dict:
        return ResearchAgentSessionService(self.store).start({
            "objective": objective,
            "stop_at": stop_at,
            "instrument_scope": ["AAPL"],
            "provider": "OPENBB",
            "frequency": "1d",
            "research_period": {"start": "2024-01-01", "end": "2024-02-09"},
            "universe_policy": {
                "eligibility": {"mode": "STATIC_LIST", "instrument_scope": ["AAPL"]},
                "selection": {"method": "ALL_ELIGIBLE"},
                "exclusions": [],
            },
            "research_contract": {
                "stop_at": stop_at,
                "evaluation": {"primary_metric": primary_metric, "horizons": [1]},
            },
        })

    def test_restart_quarantines_an_interrupted_experiment(self) -> None:
        session = self._start_product_session(
            "Design the AAPL universe", "UNIVERSE", "eligible_count"
        )
        service = ResearchExperimentService(self.store)
        experiment = service.submit(
            session["session_id"],
            {"candidate": {
                "hypothesis": "AAPL remains eligible",
                "intervention_set": [{"component": "universe", "change": "freeze AAPL"}],
                "controlled_variables": ["research_period", "frequency"],
                "universe_selection": {
                    "instrument_scope": ["AAPL"],
                    "selection": {"method": "ALL_ELIGIBLE"},
                    "exclusions": [],
                },
                "evaluation": {
                    "run_type": "UNIVERSE_DESIGN",
                    "primary_metric": "eligible_count",
                },
            }},
            advance_immediately=False,
        )
        self.assertIn(experiment["queue"]["state"], {"READY", "WAITING"})
        self.assertEqual(1, experiment["queue"]["position"])
        self.assertEqual("AUTOMATIC", experiment["queue"]["mode"])
        self.assertFalse(experiment["queue"]["action_required"])
        self.assertNotIn("resource_class", experiment["queue"])
        self.assertNotIn("worker_memory_limit_mb", experiment["queue"])
        self.assertNotIn("frontend_memory_reserve_mb", experiment["queue"])
        self.assertEqual("COMPILING", experiment["progress"]["phase"])
        service._update(
            experiment["experiment_id"], status="RUNNING", phase="RUNNING"
        )

        recovery = service.quarantine_interrupted()
        recovered = service.get(experiment["experiment_id"])

        self.assertEqual(1, recovery["count"])
        self.assertEqual("SYSTEM_BLOCKED", recovered["status"])
        self.assertEqual(
            "RESEARCH_PROCESS_INTERRUPTED", recovered["system_block"]["code"]
        )

    def test_universe_candidate_materializes_independent_product_without_run(self) -> None:
        session = self._start_product_session(
            "Design the point-in-time AAPL research universe", "UNIVERSE", "eligible_count"
        )
        experiment = ResearchExperimentService(self.store).submit(
            session["session_id"],
            {"candidate": {
                "hypothesis": "The explicit AAPL pool remains non-empty after eligibility rules",
                "intervention_set": [{"component": "universe", "change": "freeze AAPL eligibility"}],
                "controlled_variables": ["research_period", "frequency"],
                "universe_selection": {
                    "instrument_scope": ["AAPL"],
                    "selection": {"method": "ALL_ELIGIBLE"},
                    "exclusions": [],
                },
                "evaluation": {"run_type": "UNIVERSE_DESIGN", "primary_metric": "eligible_count"},
            }},
        )

        self.assertEqual("COMPLETE", experiment["status"], experiment)
        self.assertEqual("UNIVERSE_DESIGN", experiment["result"]["product_type"])
        self.assertEqual(1, experiment["result"]["decision_metrics"]["eligible_count"])
        self.assertEqual(["AAPL"], experiment["result"]["product"]["instrument_ids"])
        with self.store.connection() as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM research_runs_v2").fetchone()[0])

    def test_multi_factor_alpha_candidate_runs_as_one_explicit_signal(self) -> None:
        session = self._start_product_session(
            "Evaluate a multi-factor AAPL Alpha signal", "ALPHA", "ic"
        )
        experiment = ResearchExperimentService(self.store).submit(
            session["session_id"],
            {"candidate": {
                "hypothesis": "Combining short and medium momentum creates a usable signal",
                "intervention_set": [{"component": "alpha", "change": "combine two Factors"}],
                "controlled_variables": ["universe", "research_period", "frequency"],
                "factors": [
                    {"name": "momentum_2", "operator": "pct_change", "input_field": "close", "window": 2},
                    {"name": "momentum_5", "operator": "pct_change", "input_field": "close", "window": 5},
                ],
                "alpha": {"name": "dual_momentum", "components": [
                    {"factor": "momentum_2", "weight": 0.4, "transform": "CS_RANK"},
                    {"factor": "momentum_5", "weight": 0.6, "transform": "CS_RANK"},
                ]},
                "evaluation": {
                    "run_type": "ALPHA_EVALUATION", "primary_metric": "ic",
                    "horizons": [1], "minimum_cross_section_size": 1,
                },
            }},
        )

        self.assertEqual("COMPLETE", experiment["status"], experiment)
        inputs = experiment["result"]["product"]["results"][0]["factor_inputs"]
        self.assertEqual({"momentum_2", "momentum_5"}, {item["name"] for item in inputs})

    def test_portfolio_evidence_candidate_runs_and_stops_before_strategy(self) -> None:
        session = self._start_product_session(
            "Backtest AAPL momentum portfolio evidence", "PORTFOLIO_EVIDENCE", "annualized_return"
        )
        experiment = ResearchExperimentService(self.store).submit(
            session["session_id"],
            {"candidate": {
                "hypothesis": "A momentum-ranked long-only portfolio survives explicit costs",
                "intervention_set": [{"component": "portfolio", "change": "add top-one portfolio and costs"}],
                "controlled_variables": ["universe", "research_period", "frequency"],
                "factor": {
                    "name": "portfolio_momentum", "operator": "pct_change",
                    "input_field": "close", "window": 2,
                },
                "alpha": {"name": "portfolio_alpha", "components": [
                    {"factor": "portfolio_momentum", "weight": 1.0, "transform": "RAW"}
                ]},
                "portfolio_spec": {
                    "selection_method": "TOP_N", "top_n": 1,
                    "weighting_method": "EQUAL_WEIGHT", "direction": "LONG_ONLY",
                    "rebalance_frequency": "EVERY_SIGNAL", "max_position_weight": 1.0,
                    "cash_buffer": 0.0,
                },
                "execution_spec": {"fee_bps": 2, "slippage_bps": 10},
                "benchmark_spec": {"type": "EQUAL_WEIGHT_UNIVERSE", "rebalance_frequency": "MONTHLY"},
                "evaluation": {"run_type": "RESEARCH_BACKTEST", "primary_metric": "annualized_return"},
            }},
        )

        self.assertEqual("COMPLETE", experiment["status"], experiment)
        result = experiment["result"]
        self.assertEqual("RESEARCH_BACKTEST", result["product_type"])
        self.assertEqual("STOPPED", result["gates"]["strategy_boundary"])
        self.assertIn("annualized_return", result["decision_metrics"])
        self.assertIn("STRATEGY_DEPLOYMENT", result["product"]["boundary"]["excludes"])
        serialized = str(result["product"]).lower()
        for internal_name in ("manifest_id", "artifact_id", "bundle_id", "spec_hash", "code_hash"):
            self.assertNotIn(internal_name, serialized)


if __name__ == "__main__":
    unittest.main()
