from __future__ import annotations

import unittest

from services.data_platform.alpha_run_result_service import (
    ALPHA_RUN_RESULT_SCHEMA_VERSION,
    alpha_run_section,
    build_alpha_run_contract,
)


class AlphaRunResultContractTest(unittest.TestCase):
    def setUp(self) -> None:
        artifacts = [
            {
                "artifact_id": "factor-artifact-1",
                "artifact_type": "FACTOR_VALUES",
                "logical_name": "Momentum",
                "schema_version": "factor-values.v2",
                "status": "READY",
                "row_count": 80,
                "metadata": {"factor_name": "Momentum", "row_count": 80},
            },
            {
                "artifact_id": "alpha-artifact-1",
                "artifact_type": "ALPHA_VALUES",
                "logical_name": "Momentum Alpha",
                "schema_version": "alpha-output.v2",
                "status": "READY",
                "row_count": 80,
                "metadata": {"alpha_name": "Momentum Alpha", "row_count": 80},
            },
            {
                "artifact_id": "alpha-evaluation-1",
                "artifact_type": "ALPHA_EVALUATION",
                "logical_name": "Momentum Alpha-run-alpha-1",
                "schema_version": "evaluation-records.v1",
                "status": "READY",
                "metadata": {
                    "summary": {
                        "score_count": 80,
                        "score_mean": 0.5,
                        "score_std": 0.2,
                        "score_quantiles": {"5": 0.1, "50": 0.5, "95": 0.9},
                        "average_rank_stability": 0.8,
                        "average_membership_turnover": 0.25,
                        "ic": {"1": {"count": 20, "mean": 0.12}},
                        "rank_ic": {"1": {"count": 20, "mean": 0.15}},
                        "holding_period_decay": {"1": {"count": 20, "top_mean_return": 0.01}},
                        "regime_performance": {"1": {"BULL": {"count": 10, "top_mean_return": 0.02}}},
                        "diagnostics": [{"code": "LOW_ALPHA_SAMPLE", "severity": "WARNING", "message": "Small sample."}],
                    }
                },
            },
            {
                "artifact_id": "portfolio-artifact-1",
                "artifact_type": "PORTFOLIO_TARGETS",
                "logical_name": "TOP_N_2",
                "schema_version": "portfolio-targets.v1",
                "status": "READY",
                "row_count": 40,
                "metadata": {"row_count": 40},
            },
        ]
        for artifact_type, artifact_id, row_count in (
            ("POSITION_SERIES", "positions-1", 40),
            ("BACKTEST_ORDERS", "trades-1", 12),
            ("EQUITY_SERIES", "equity-1", 20),
            ("DRAWDOWN_SERIES", "drawdown-1", 20),
        ):
            artifacts.append({
                "artifact_id": artifact_id,
                "artifact_type": artifact_type,
                "logical_name": "Momentum Alpha-run-alpha-1",
                "schema_version": f"{artifact_type.lower()}.v1",
                "status": "READY",
                "row_count": row_count,
                "metadata": {"row_count": row_count},
            })
        artifacts.append({
            "artifact_id": "performance-1",
            "artifact_type": "BACKTEST_RESULT",
            "logical_name": "Momentum Alpha-run-alpha-1",
            "schema_version": "backtest-result.v1",
            "status": "READY",
            "row_count": 1,
            "metadata": {
                "metrics": {
                    "initial_cash": 10_000.0,
                    "final_equity": 10_800.0,
                    "total_return": 0.08,
                    "annualized_return": 0.12,
                    "volatility": 0.18,
                    "sharpe": 1.25,
                    "max_drawdown": -0.07,
                    "max_underwater_bars": 8,
                    "fees": 12.5,
                    "slippage_cost": 8.0,
                    "turnover": 1.6,
                    "trade_count": 12,
                    "rebalance_count": 6,
                    "bar_count": 20,
                    "instrument_count": 4,
                    "average_exposure": 0.75,
                    "average_cash_ratio": 0.25,
                }
            },
        })
        dependencies = {
            "alpha-evaluation-1": [{"parent_id": "alpha-artifact-1", "dependency_type": "INPUT_ALPHA"}],
            "portfolio-artifact-1": [{"parent_id": "alpha-artifact-1", "dependency_type": "INPUT_ALPHA"}],
        }
        for artifact_id in ("positions-1", "trades-1", "equity-1", "drawdown-1", "performance-1"):
            dependencies[artifact_id] = [{"parent_id": "portfolio-artifact-1", "dependency_type": "INPUT_PORTFOLIO_TARGETS"}]

        self.contract = build_alpha_run_contract(
            run={
                "run_id": "run-alpha-1",
                "project_id": "project-1",
                "run_type": "ALPHA_EVALUATION",
                "status": "SUCCEEDED",
                "bundle_id": "bundle-1",
            },
            alpha_definitions=[{
                "definition_id": "alpha-definition-1",
                "name": "Momentum Alpha",
                "version": "1.0.0",
                "spec_hash": "sha256:alpha",
                "engine_version": "alpha-engine.v2",
                "spec": {
                    "name": "Momentum Alpha",
                    "output_scale": "PERCENTILE",
                    "minimum_coverage": 0.8,
                    "minimum_cross_section_size": 4,
                    "components": [{
                        "factor_definition_id": "factor-definition-1",
                        "factor_name": "Momentum",
                        "factor_version": "1.0.0",
                        "weight": 1.0,
                        "transform": "CS_RANK",
                        "ascending": False,
                    }],
                },
            }],
            factor_definitions=[{
                "definition_id": "factor-definition-1",
                "name": "Momentum",
                "version": "1.0.0",
                "spec_hash": "sha256:factor",
                "spec": {"name": "Momentum"},
            }],
            universe={"universe_snapshot_id": "snapshot-1", "actual_instrument_ids": ["A", "B", "C", "D"]},
            data_inputs=[{"manifest_id": "manifest-1", "provider": "BINANCE"}],
            execution_specs={
                "evaluation_spec": {"horizons": [1, 6]},
                "portfolio_spec": {"selection_method": "TOP_N", "top_n": 2},
                "execution_spec": {"initial_cash": 10_000, "fee_bps": 5, "slippage_bps": 10},
            },
            artifacts=artifacts[:3],
            artifact_dependencies={
                "alpha-evaluation-1": dependencies["alpha-evaluation-1"],
            },
        )

    def test_contract_stops_at_predictive_signal_evaluation(self) -> None:
        self.assertEqual(ALPHA_RUN_RESULT_SCHEMA_VERSION, self.contract["schema_version"])
        self.assertEqual("ALPHA_RUN", self.contract["product_run_type"])
        self.assertEqual("SIGNAL_CONSTRUCTION", self.contract["boundary"]["starts_at"])
        self.assertEqual("SIGNAL_PREDICTIVE_EVALUATION", self.contract["boundary"]["ends_at"])
        self.assertIn("IC_ACCURACY", self.contract["boundary"]["includes"])
        self.assertIn("TRADES", self.contract["boundary"]["excludes"])
        self.assertIn("LIVE_TRADING", self.contract["boundary"]["excludes"])

    def test_contract_preserves_definition_factor_and_artifact_lineage(self) -> None:
        result = self.contract["results"][0]
        self.assertEqual("alpha-definition-1", result["alpha"]["definition_id"])
        self.assertEqual("factor-definition-1", result["factor_inputs"][0]["definition_id"])
        self.assertEqual("factor-artifact-1", result["factor_inputs"][0]["artifact"]["artifact_id"])
        self.assertNotIn("portfolio_targets", result["artifacts"])
        self.assertNotIn("performance", result)
        self.assertNotIn("costs", result)
        self.assertEqual({"WARNING": 1}, self.contract["diagnostic_summary"])

    def test_structured_sections_expose_summaries_and_artifact_roles(self) -> None:
        signals = alpha_run_section(self.contract, "signals")
        self.assertEqual("ALPHA_RUN_SIGNALS", signals["view_type"])
        self.assertEqual("alpha-artifact-1", signals["artifact_ids"][0])
        self.assertEqual(0.8, signals["items"][0]["signal_summary"]["average_rank_stability"])
        ic_accuracy = alpha_run_section(self.contract, "ic_accuracy")
        self.assertEqual(0.12, ic_accuracy["items"][0]["ic"]["1"]["mean"])
        self.assertEqual(0.15, ic_accuracy["items"][0]["rank_ic"]["1"]["mean"])

        decay = alpha_run_section(self.contract, "decay")
        self.assertEqual(0.01, decay["items"][0]["holding_period_decay"]["1"]["top_mean_return"])

        turnover = alpha_run_section(self.contract, "turnover")
        self.assertEqual(0.25, turnover["items"][0]["turnover_summary"]["average_membership_turnover"])

    def test_rejects_non_alpha_runs_and_unknown_sections(self) -> None:
        with self.assertRaisesRegex(ValueError, "ALPHA_EVALUATION"):
            build_alpha_run_contract(
                run={"run_type": "FACTOR_EVALUATION"},
                alpha_definitions=[],
                factor_definitions=[],
                universe={},
                data_inputs=[],
                execution_specs={},
                artifacts=[],
            )
        with self.assertRaisesRegex(ValueError, "unsupported structured"):
            alpha_run_section(self.contract, "coverage")


if __name__ == "__main__":
    unittest.main()
