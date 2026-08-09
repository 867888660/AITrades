from __future__ import annotations

import unittest

from services.data_platform.alpha_run_result_service import alpha_run_section
from services.data_platform.research_backtest_result_service import (
    RESEARCH_BACKTEST_RESULT_SCHEMA_VERSION,
    build_research_backtest_contract,
)


class ResearchBacktestResultContractTest(unittest.TestCase):
    def setUp(self) -> None:
        artifacts = [
            {
                "artifact_id": "alpha-1", "artifact_type": "ALPHA_VALUES",
                "logical_name": "Momentum Alpha", "status": "READY", "row_count": 20,
                "metadata": {"alpha_name": "Momentum Alpha"},
            },
            {
                "artifact_id": "targets-1", "artifact_type": "PORTFOLIO_TARGETS",
                "logical_name": "TOP_N_1", "status": "READY", "row_count": 20,
                "metadata": {},
            },
            {
                "artifact_id": "equity-1", "artifact_type": "EQUITY_SERIES",
                "logical_name": "Momentum Alpha-run-1", "status": "READY", "row_count": 20,
                "metadata": {},
            },
            {
                "artifact_id": "performance-1", "artifact_type": "BACKTEST_RESULT",
                "logical_name": "Momentum Alpha-run-1", "status": "READY", "row_count": 1,
                "metadata": {"metrics": {"total_return": 0.08, "sharpe": 1.25, "max_drawdown": -0.07}},
            },
        ]
        dependencies = {
            "targets-1": [{"parent_id": "alpha-1", "dependency_type": "INPUT_ALPHA"}],
            "equity-1": [{"parent_id": "targets-1", "dependency_type": "INPUT_PORTFOLIO_TARGETS"}],
            "performance-1": [{"parent_id": "targets-1", "dependency_type": "INPUT_PORTFOLIO_TARGETS"}],
        }
        self.contract = build_research_backtest_contract(
            run={"run_id": "run-1", "project_id": "project-1", "run_type": "RESEARCH_BACKTEST", "status": "SUCCEEDED"},
            alpha_definitions=[{
                "definition_id": "alpha-definition-1", "name": "Momentum Alpha", "version": "1.0.0",
                "spec": {"name": "Momentum Alpha", "components": []},
            }],
            factor_definitions=[],
            universe={"universe_snapshot_id": "snapshot-1", "actual_instrument_ids": ["A"]},
            data_inputs=[],
            execution_specs={
                "portfolio_spec": {"selection_method": "TOP_N", "top_n": 1},
                "execution_spec": {"initial_cash": 10_000, "fee_bps": 2, "slippage_bps": 10},
                "benchmark_spec": {"type": "EQUAL_WEIGHT_UNIVERSE", "rebalance_frequency": "MONTHLY"},
            },
            artifacts=artifacts,
            artifact_dependencies=dependencies,
        )

    def test_backtest_contract_owns_portfolio_execution_and_performance(self) -> None:
        self.assertEqual(RESEARCH_BACKTEST_RESULT_SCHEMA_VERSION, self.contract["schema_version"])
        self.assertEqual("RESEARCH_BACKTEST", self.contract["product_run_type"])
        self.assertEqual("COST_ADJUSTED_PORTFOLIO_AND_TRADING_PERFORMANCE", self.contract["boundary"]["ends_at"])
        self.assertIn("EQUITY_CURVE", self.contract["boundary"]["includes"])
        self.assertTrue(self.contract["benchmark_status"]["configured"])
        self.assertFalse(self.contract["benchmark_status"]["materialized"])

    def test_backtest_sections_expose_only_backtest_metrics_in_backtest_product(self) -> None:
        targets = alpha_run_section(self.contract, "portfolio_targets")
        self.assertEqual(["targets-1"], targets["artifact_ids"])
        performance = alpha_run_section(self.contract, "performance_metrics")
        self.assertEqual(0.08, performance["items"][0]["performance"]["total_return"])
        self.assertEqual(1.25, performance["items"][0]["performance"]["sharpe"])


if __name__ == "__main__":
    unittest.main()
