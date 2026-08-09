from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import app as app_module
from services.data_platform import (
    ArtifactService,
    CanonicalBarsCommitter,
    DataPlatformStore,
    DefinitionRegistry,
    FactorSpec,
    FormalResearchRunExecutor,
    RequirementCompiler,
    ResearchControlPlane,
    ResearchRunPreviewService,
    ResearchRunService,
    ResearchRunWorker,
    UniverseService,
)
from services.data_platform.store import json_dumps, utc_now
from services.data_platform.alpha_run_result_service import AlphaRunResultService
from services.data_platform.research_backtest_result_service import ResearchBacktestResultService


class ResearchRunLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.inspection_db_patch = patch(
            "services.inspection_service._strategy_db_path",
            return_value=root / "inspection.db",
        )
        self.inspection_db_patch.start()
        self.store = DataPlatformStore(root / "metadata.db")
        self.project = ResearchControlPlane(self.store).create_project(
            title="Atomic research run", objective="verify formal lifecycle"
        )
        self.instrument_id = "crypto_spot:BINANCE:BTCUSDT"
        data_start = datetime(2025, 12, 31, 22, tzinfo=timezone.utc)
        rows = [
            {
                "instrument_id": self.instrument_id,
                "frequency": "1h",
                "bar_start_time": (data_start + timedelta(hours=index)).isoformat(),
                "bar_end_time": (data_start + timedelta(hours=index + 1)).isoformat(),
                "available_time": (data_start + timedelta(hours=index + 1)).isoformat(),
                "ingested_at": "2026-01-03T00:00:00+00:00",
                "open": float(close), "high": float(close + 1), "low": float(max(0.1, close - 1)),
                "close": float(close), "volume": 10.0, "turnover": float(close * 10),
                "trade_count": 2,
                "bar_status": "COMPLETE", "source": "BINANCE", "source_version": "1",
                "quality_status": "PASS",
            }
            for index, close in enumerate([4, 3, 3, 2, 1, 2, 3, 2, 1])
        ]
        self.committed = CanonicalBarsCommitter(self.store, root / "bars").commit(
            dataset_id="binance:BTCUSDT:1h", instrument_id=self.instrument_id,
            asset_class="crypto_spot", venue="BINANCE", frequency="1h",
            source="BINANCE", source_version="1", rows=rows,
        )
        universe = UniverseService(self.store).create_definition(
            name="BTC", version="1.0.0", universe_type="STATIC_LIST",
            parameters={"instrument_ids": [self.instrument_id]},
        )
        self.snapshot = UniverseService(self.store).resolve_snapshot(
            universe_definition_id=universe.universe_definition_id,
            as_of_time="2026-01-01T07:00:00+00:00",
        )
        self.factor_spec = FactorSpec(
            name="return_1", version="1.0.0", operator="pct_change",
            input_field="close", window=1, frequency="1h",
        )
        definition = DefinitionRegistry(self.store).create(
            "FACTOR",
            {
                "name": self.factor_spec.name, "version": self.factor_spec.version,
                "operator": self.factor_spec.operator, "input_field": "close",
                "window": 1, "frequency": "1h",
            },
            state="VALIDATED",
        )
        self.factor_definition = definition
        DefinitionRegistry(self.store).set_project_ref(
            project_id=self.project["project_id"], slot_key="factor:return_1",
            definition_id=definition.definition_id, definition_version=definition.version,
            reference_mode="PINNED",
        )
        self.requirements = RequirementCompiler(self.store).compile(
            project_id=self.project["project_id"], factor_specs=[self.factor_spec],
            context={
                "instrument_ids": [self.instrument_id], "data_type": "bars", "frequency": "1h",
                "history_start": "2026-01-01T00:00:00+00:00",
                "history_end": "2026-01-01T07:00:00+00:00",
                "adjustment": "NONE", "time_semantics": "BAR_END_AVAILABLE_TIME",
                "point_in_time_policy": "AS_OF", "quality_policy": "STRICT", "source_policy": "FIXED",
            },
        )
        self.grant_id = "grant_atomic_test"
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                """INSERT INTO approval_grants(
                   grant_id,project_id,plan_version,status,scope_json,budgets_json,
                   approved_by,created_at,approved_at,expires_at,grant_version,policy_version
                   ) VALUES (?,?,1,'ACTIVE',?,?,?,?,?,NULL,1,'research_policy.v1')""",
                (
                    self.grant_id, self.project["project_id"],
                    json_dumps({"allowed_run_types": ["FACTOR_EVALUATION", "ALPHA_EVALUATION", "RESEARCH_BACKTEST"]}),
                    json_dumps({"max_backtest_runs": 2, "max_download_bytes": 0, "max_runtime_seconds": 600}),
                    "human_reviewer", now, now,
                ),
            )
            conn.execute(
                "INSERT INTO approval_budget_counters(grant_id,updated_at) VALUES (?,?)",
                (self.grant_id, now),
            )

    def tearDown(self) -> None:
        self.inspection_db_patch.stop()
        self.temp.cleanup()

    def _preview(self):
        return ResearchRunPreviewService(self.store).create(
            self.project["project_id"],
            {
                "run_type": "FACTOR_EVALUATION",
                "requirement_set_id": self.requirements.requirement_set_id,
                "universe_snapshot_id": self.snapshot.universe_snapshot_id,
                "grant_id": self.grant_id,
                "source_selection_policy": {"preferred_sources": ["BINANCE"]},
                "evaluation_spec": {"horizons": [1], "minimum_cross_section_size": 2},
                "budget": {"runs": 1, "runtime_seconds": 60, "download_bytes": 0},
            },
        )

    def test_preview_bundle_run_and_worker_are_one_controlled_lifecycle(self) -> None:
        preview = self._preview()
        self.assertEqual(
            "READY",
            preview["readiness"]["overall"]["status"],
            preview["readiness"],
        )
        service = ResearchRunService(self.store)
        run = service.create(
            preview_id=preview["preview_id"], preview_fingerprint=preview["preview_fingerprint"],
            idempotency_key="run-once",
        )
        self.assertEqual("QUEUED", run["status"])
        same = service.create(
            preview_id=preview["preview_id"], preview_fingerprint=preview["preview_fingerprint"],
            idempotency_key="run-once",
        )
        self.assertEqual(run["run_id"], same["run_id"])
        bundle = service.get_bundle(run["bundle_id"], check_current_authorization=True)
        self.assertEqual("FROZEN", bundle["lifecycle_status"])
        self.assertEqual("ALLOWED", bundle["current_reuse_authorization"]["status"])
        claimed = ResearchRunWorker(self.store, "worker-test").claim()
        self.assertEqual(run["run_id"], claimed["run_id"])
        self.assertEqual(
            [self.committed["manifest"].manifest_id],
            claimed["frozen_input"]["input_closure"]["exact_manifest_ids"],
        )
        output = FormalResearchRunExecutor(
            self.store, artifact_root=self.temp.name
        ).execute(claimed)
        self.assertEqual(1, len(output["produced_factor_artifact_ids"]))
        self.assertEqual(1, len(output["produced_evaluation_artifact_ids"]))
        finished = ResearchRunWorker(self.store, "worker-test").complete(run["run_id"], output)
        self.assertEqual("SUCCEEDED", finished["status"])

    def test_alpha_evaluation_stops_before_portfolio_and_return_sections(self) -> None:
        registry = DefinitionRegistry(self.store)
        alpha = registry.create(
            "ALPHA",
            {
                "name": "return_alpha",
                "version": "1.0.0",
                "components": [{
                    "factor_definition_id": self.factor_definition.definition_id,
                    "factor_version": self.factor_definition.version,
                    "weight": 1.0,
                    "transform": "RAW",
                    "ascending": True,
                }],
                "minimum_coverage": 1.0,
                "minimum_cross_section_size": 1,
                "missing_policy": "EXCLUDE",
                "rank_method": "AVERAGE",
                "output_scale": "PERCENTILE",
            },
            state="VALIDATED",
        )
        registry.set_project_ref(
            project_id=self.project["project_id"],
            slot_key="alpha:return_alpha",
            definition_id=alpha.definition_id,
            definition_version=alpha.version,
            reference_mode="PINNED",
        )
        preview = ResearchRunPreviewService(self.store).create(
            self.project["project_id"],
            {
                "run_type": "ALPHA_EVALUATION",
                "requirement_set_id": self.requirements.requirement_set_id,
                "universe_snapshot_id": self.snapshot.universe_snapshot_id,
                "grant_id": self.grant_id,
                "source_selection_policy": {"preferred_sources": ["BINANCE"]},
                "evaluation_spec": {
                    "horizons": [1],
                    "quantile_count": 2,
                    "minimum_cross_section_size": 1,
                },
                "budget": {"runs": 1, "runtime_seconds": 60, "download_bytes": 0},
            },
        )
        self.assertEqual("READY", preview["readiness"]["overall"]["status"], preview["readiness"])
        run = ResearchRunService(self.store).create(
            preview_id=preview["preview_id"],
            preview_fingerprint=preview["preview_fingerprint"],
            idempotency_key="alpha-run-once",
        )
        claimed = ResearchRunWorker(self.store, "worker-alpha-test").claim()
        output = FormalResearchRunExecutor(
            self.store, artifact_root=self.temp.name
        ).execute(claimed)
        self.assertEqual("ALPHA_RUN", output["product_run_type"])
        self.assertEqual(1, len(output["produced_alpha_artifact_ids"]))
        self.assertEqual(1, len(output["produced_evaluation_artifact_ids"]))
        self.assertEqual([], output["produced_portfolio_artifact_ids"])
        self.assertIn("rank_ic", output["metrics"]["return_alpha"])
        self.assertNotIn("performance", output["metrics"]["return_alpha"])
        artifact_types = {
            artifact.artifact_type
            for artifact in ArtifactService(self.store).list(limit=1000)
            if artifact.created_by_run_id == run["run_id"]
        }
        self.assertTrue({
            "ALPHA_VALUES",
            "ALPHA_EVALUATION",
        }.issubset(artifact_types))
        self.assertTrue({
            "PORTFOLIO_TARGETS", "POSITION_SERIES", "BACKTEST_ORDERS",
            "EQUITY_SERIES", "BACKTEST_RESULT", "DRAWDOWN_SERIES",
        }.isdisjoint(artifact_types))
        alpha_contract = AlphaRunResultService(self.store).build(run)
        self.assertEqual("alpha-evaluation-result.v2", alpha_contract["schema_version"])
        self.assertEqual("ALPHA_RUN", alpha_contract["product_run_type"])
        self.assertEqual(1, len(alpha_contract["results"]))
        alpha_result = alpha_contract["results"][0]
        self.assertTrue(alpha_result["artifacts"]["alpha"]["artifact_id"])
        self.assertNotIn("portfolio_targets", alpha_result["artifacts"])
        self.assertNotIn("performance", alpha_result)
        with (
            patch("app.get_default_store", return_value=self.store),
            patch("app.BASE_DIR", Path(self.temp.name)),
        ):
            client = app_module.app.test_client()
            summary_response = client.get(f"/api/research/runs/{run['run_id']}/result-summary")
            self.assertEqual(200, summary_response.status_code, summary_response.get_json())
            summary = summary_response.get_json()["data"]
            self.assertEqual("alpha-evaluation-result.v2", summary["result_schema_version"])
            self.assertEqual("ALPHA_RUN", summary["alpha_run"]["product_run_type"])
            for section_key, view_type in (
                ("signals", "ALPHA_RUN_SIGNALS"),
                ("ic_accuracy", "ALPHA_RUN_IC_ACCURACY"),
                ("decay", "ALPHA_RUN_DECAY"),
                ("turnover", "ALPHA_RUN_TURNOVER"),
                ("regime_analysis", "ALPHA_RUN_REGIME_ANALYSIS"),
                ("diagnostics", "ALPHA_RUN_DIAGNOSTICS"),
            ):
                section_response = client.get(
                    f"/api/research/runs/{run['run_id']}/sections/{section_key}"
                )
                self.assertEqual(200, section_response.status_code, section_response.get_json())
                section = section_response.get_json()["data"]
                self.assertEqual(view_type, section["view_type"])
                self.assertEqual("alpha-evaluation-result.v2", section["schema_version"])
            equity = client.get(
                f"/api/research/runs/{run['run_id']}/sections/equity_curve"
            )
            self.assertEqual(400, equity.status_code)

    def test_research_backtest_materializes_portfolio_and_performance_contract(self) -> None:
        registry = DefinitionRegistry(self.store)
        alpha = registry.create(
            "ALPHA",
            {
                "name": "return_alpha", "version": "1.0.0",
                "components": [{
                    "factor_definition_id": self.factor_definition.definition_id,
                    "factor_version": self.factor_definition.version,
                    "weight": 1.0, "transform": "RAW", "ascending": True,
                }],
                "minimum_coverage": 1.0, "minimum_cross_section_size": 1,
                "missing_policy": "EXCLUDE", "rank_method": "AVERAGE",
                "output_scale": "PERCENTILE",
            },
            state="VALIDATED",
        )
        registry.set_project_ref(
            project_id=self.project["project_id"], slot_key="alpha:return_alpha",
            definition_id=alpha.definition_id, definition_version=alpha.version,
            reference_mode="PINNED",
        )
        preview = ResearchRunPreviewService(self.store).create(
            self.project["project_id"],
            {
                "run_type": "RESEARCH_BACKTEST",
                "requirement_set_id": self.requirements.requirement_set_id,
                "universe_snapshot_id": self.snapshot.universe_snapshot_id,
                "grant_id": self.grant_id,
                "source_selection_policy": {"preferred_sources": ["BINANCE"]},
                "portfolio_spec": {
                    "selection_method": "TOP_N", "top_n": 1,
                    "weighting_method": "EQUAL_WEIGHT", "direction": "LONG_ONLY",
                    "rebalance_frequency": "EVERY_SIGNAL", "max_position_weight": 1.0,
                    "cash_buffer": 0.0,
                },
                "execution_spec": {"initial_cash": 10_000, "fee_bps": 2, "slippage_bps": 10},
                "benchmark_spec": {"type": "EQUAL_WEIGHT_UNIVERSE", "rebalance_frequency": "MONTHLY"},
                "budget": {"runs": 1, "runtime_seconds": 60, "download_bytes": 0},
            },
        )
        self.assertEqual("READY", preview["readiness"]["overall"]["status"], preview["readiness"])
        run = ResearchRunService(self.store).create(
            preview_id=preview["preview_id"], preview_fingerprint=preview["preview_fingerprint"],
            idempotency_key="research-backtest-once",
        )
        worker = ResearchRunWorker(self.store, "worker-backtest-test")
        claimed = worker.claim()
        output = FormalResearchRunExecutor(self.store, artifact_root=self.temp.name).execute(claimed)
        worker.complete(run["run_id"], output)
        self.assertEqual("RESEARCH_BACKTEST", output["product_run_type"])
        self.assertEqual(1, len(output["produced_portfolio_artifact_ids"]))
        self.assertGreaterEqual(len(output["produced_backtest_artifact_ids"]), 5)
        self.assertIn("total_return", output["metrics"])
        contract = ResearchBacktestResultService(self.store).build(run["run_id"])
        self.assertEqual("research-backtest-result.v1", contract["schema_version"])
        self.assertEqual("RESEARCH_BACKTEST", contract["product_run_type"])
        self.assertTrue(contract["benchmark_status"]["configured"])
        self.assertTrue(contract["results"][0]["artifacts"]["performance"]["artifact_id"])
        self.assertIn("total_return", contract["results"][0]["performance"])
        with (
            patch("app.get_default_store", return_value=self.store),
            patch("app.BASE_DIR", Path(self.temp.name)),
        ):
            client = app_module.app.test_client()
            summary = client.get(
                f"/api/research/runs/{run['run_id']}/result-summary"
            ).get_json()["data"]
            self.assertEqual("RESEARCH_BACKTEST", summary["product_run_type"])
            self.assertEqual("research-backtest-result.v1", summary["result_schema_version"])
            self.assertIsNone(summary["alpha_run"])
            self.assertTrue(summary["research_backtest"]["benchmark_status"]["configured"])
            performance = client.get(
                f"/api/research/runs/{run['run_id']}/sections/performance_metrics"
            ).get_json()["data"]
            self.assertEqual("RESEARCH_BACKTEST_PERFORMANCE_METRICS", performance["view_type"])
            self.assertIn("total_return", performance["items"][0]["performance"])

    def test_atomic_failure_leaves_no_budget_bundle_or_run(self) -> None:
        preview = self._preview()
        service = ResearchRunService(self.store)
        with patch.object(
            ResearchRunService,
            "_create_bundle_artifact_in_conn",
            side_effect=RuntimeError("injected bundle artifact failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "injected bundle artifact failure"):
                service.create(
                    preview_id=preview["preview_id"], preview_fingerprint=preview["preview_fingerprint"],
                    idempotency_key="must-rollback",
                )
        with self.store.connection() as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM research_runs_v2").fetchone()[0])
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM frozen_research_bundles").fetchone()[0])
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM approval_budget_reservations").fetchone()[0])

    def test_ma_crossover_factor_runs_through_frozen_bundle_worker(self) -> None:
        spec = FactorSpec(
            name="btc_sma_2_3_cross",
            version="1.0.0",
            operator="ma_crossover",
            input_field="close",
            window=3,
            parameters={"fast_window": 2},
            frequency="1h",
            output_unit="SIGNAL",
        )
        definition = DefinitionRegistry(self.store).create(
            "FACTOR",
            {
                "name": spec.name,
                "version": spec.version,
                "operator": spec.operator,
                "input_field": spec.input_field,
                "window": spec.window,
                "parameters": spec.parameters,
                "frequency": spec.frequency,
                "output_unit": spec.output_unit,
            },
            state="VALIDATED",
        )
        DefinitionRegistry(self.store).set_project_ref(
            project_id=self.project["project_id"],
            slot_key="factor:return_1",
            definition_id=definition.definition_id,
            definition_version=definition.version,
            reference_mode="PINNED",
        )
        requirements = RequirementCompiler(self.store).compile(
            project_id=self.project["project_id"],
            factor_specs=[spec],
            context={
                "instrument_ids": [self.instrument_id],
                "data_type": "bars",
                "frequency": "1h",
                "history_start": "2026-01-01T00:00:00+00:00",
                "history_end": "2026-01-01T07:00:00+00:00",
                "adjustment": "NONE",
                "time_semantics": "BAR_END_AVAILABLE_TIME",
                "point_in_time_policy": "AS_OF",
                "quality_policy": "STRICT",
                "source_policy": "FIXED",
            },
        )
        preview = ResearchRunPreviewService(self.store).create(
            self.project["project_id"],
            {
                "run_type": "FACTOR_EVALUATION",
                "requirement_set_id": requirements.requirement_set_id,
                "universe_snapshot_id": self.snapshot.universe_snapshot_id,
                "grant_id": self.grant_id,
                "source_selection_policy": {"preferred_sources": ["BINANCE"]},
                "evaluation_spec": {"horizons": [1], "minimum_cross_section_size": 2},
                "budget": {"runs": 1, "runtime_seconds": 60, "download_bytes": 0},
            },
        )
        self.assertEqual(
            "READY",
            preview["readiness"]["overall"]["status"],
            preview["readiness"],
        )
        run = ResearchRunService(self.store).create(
            preview_id=preview["preview_id"],
            preview_fingerprint=preview["preview_fingerprint"],
            idempotency_key="ma-crossover-run",
        )
        claimed = ResearchRunWorker(self.store, "ma-worker").claim()
        output = FormalResearchRunExecutor(
            self.store,
            artifact_root=self.temp.name,
        ).execute(claimed)
        self.assertEqual(1, len(output["produced_factor_artifact_ids"]))
        finished = ResearchRunWorker(self.store, "ma-worker").complete(run["run_id"], output)
        self.assertEqual("SUCCEEDED", finished["status"])


if __name__ == "__main__":
    unittest.main()
