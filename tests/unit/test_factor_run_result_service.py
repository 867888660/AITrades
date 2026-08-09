from __future__ import annotations

import unittest

from services.data_platform.factor_run_result_service import (
    FACTOR_RUN_RESULT_SCHEMA_VERSION,
    build_factor_run_contract,
    factor_run_section,
)


class FactorRunResultContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = build_factor_run_contract(
            run={
                "run_id": "run-factor-1",
                "project_id": "project-1",
                "run_type": "FACTOR_EVALUATION",
                "status": "SUCCEEDED",
                "bundle_id": "bundle-1",
            },
            factor_definitions=[{
                "definition_id": "factor-definition-1",
                "name": "Momentum",
                "version": "1.0.0",
                "spec_hash": "sha256:factor",
                "engine_version": "factor-engine.v4",
                "spec": {
                    "name": "Momentum",
                    "dimension": "PRICE_RETURN",
                    "frequency": "1h",
                    "output_unit": "ratio",
                },
            }],
            universe={
                "universe_snapshot_id": "snapshot-1",
                "actual_instrument_ids": ["A", "B", "C", "D"],
            },
            data_inputs=[{"manifest_id": "manifest-1", "provider": "BINANCE"}],
            artifacts=[
                {
                    "artifact_id": "factor-artifact-1",
                    "artifact_type": "FACTOR_VALUES",
                    "logical_name": "Momentum",
                    "schema_version": "factor-values.v2",
                    "status": "READY",
                    "row_count": 40,
                    "metadata": {"factor_name": "Momentum", "row_count": 40},
                },
                {
                    "artifact_id": "evaluation-artifact-1",
                    "artifact_type": "FACTOR_EVALUATION",
                    "logical_name": "Momentum-run-factor-1",
                    "schema_version": "evaluation-records.v1",
                    "status": "READY",
                    "metadata": {
                        "evaluation_spec": {"horizons": [1], "quantile_count": 4},
                        "evaluation_spec_hash": "sha256:evaluation",
                        "summary": {
                            "coverage": 0.95,
                            "missing_rate": 0.05,
                            "valid_rows": 38,
                            "total_rows": 40,
                            "mean": 0.1,
                            "std": 0.2,
                            "quantiles": {"5": -0.2, "50": 0.1, "95": 0.4},
                            "outlier_ratio_5sigma": 0.0,
                            "average_rank_turnover": 0.25,
                            "coverage_by_instrument": {
                                "A": {"coverage": 1.0, "valid_rows": 10, "total_rows": 10},
                                "B": {"coverage": 0.8, "valid_rows": 8, "total_rows": 10},
                            },
                            "cross_section_count": 10,
                            "eligible_cross_section_count": 8,
                            "ic": {"1": {"mean": 0.12, "std": 0.05, "icir": 2.4, "count": 8}},
                            "rank_ic": {"1": {"mean": 0.16, "std": 0.04, "icir": 4.0, "count": 8}},
                            "quantile_returns": {
                                "1": {
                                    "mean_returns": {"1": -0.01, "4": 0.02},
                                    "high_minus_low": 0.03,
                                    "monotonicity": 0.9,
                                }
                            },
                            "diagnostics": [{
                                "code": "LOW_CROSS_SECTION_SAMPLE",
                                "severity": "WARNING",
                                "message": "Only eight cross-sections were evaluated.",
                            }],
                        },
                    },
                },
            ],
        )

    def test_contract_ends_at_factor_evaluation_boundary(self) -> None:
        self.assertEqual(FACTOR_RUN_RESULT_SCHEMA_VERSION, self.contract["schema_version"])
        self.assertEqual("FACTOR_RUN", self.contract["product_run_type"])
        self.assertEqual(
            "FACTOR_PREDICTIVE_POWER_AND_GROUP_PERFORMANCE",
            self.contract["boundary"]["ends_at"],
        )
        self.assertIn("TRADES", self.contract["boundary"]["excludes"])
        for forbidden in ("signals", "positions", "trades", "equity_curve", "drawdown"):
            self.assertNotIn(forbidden, self.contract)

    def test_contract_preserves_factor_identity_and_artifact_lineage(self) -> None:
        result = self.contract["results"][0]
        self.assertEqual("factor-definition-1", result["factor"]["definition_id"])
        self.assertEqual("factor-artifact-1", result["factor_artifact"]["artifact_id"])
        self.assertEqual("evaluation-artifact-1", result["evaluation_artifact"]["artifact_id"])
        self.assertEqual(0.95, result["coverage"]["overall"])
        self.assertEqual(0.16, result["predictive_power"][0]["rank_ic"]["mean"])
        self.assertEqual({"WARNING": 1}, self.contract["diagnostic_summary"])

    def test_structured_sections_have_stable_rows_and_view_types(self) -> None:
        coverage = factor_run_section(self.contract, "coverage")
        self.assertEqual("FACTOR_RUN_COVERAGE", coverage["view_type"])
        self.assertEqual(3, coverage["total_rows"])
        self.assertEqual("OVERALL", coverage["rows"][0]["scope"])

        predictive = factor_run_section(self.contract, "ic_rank_ic")
        self.assertEqual(0.12, predictive["rows"][0]["ic_mean"])
        self.assertEqual(4.0, predictive["rows"][0]["rank_ic_ir"])

        quantiles = factor_run_section(self.contract, "quantile_return")
        self.assertEqual(2, quantiles["total_rows"])
        self.assertEqual(0.03, quantiles["rows"][0]["high_minus_low"])

        diagnostics = factor_run_section(self.contract, "diagnostics")
        self.assertEqual("LOW_CROSS_SECTION_SAMPLE", diagnostics["rows"][0]["code"])

    def test_rejects_non_factor_runs_and_unknown_sections(self) -> None:
        with self.assertRaisesRegex(ValueError, "FACTOR_EVALUATION"):
            build_factor_run_contract(
                run={"run_type": "ALPHA_EVALUATION"},
                factor_definitions=[],
                universe={},
                data_inputs=[],
                artifacts=[],
            )
        with self.assertRaisesRegex(ValueError, "unsupported structured"):
            factor_run_section(self.contract, "trades")


if __name__ == "__main__":
    unittest.main()
