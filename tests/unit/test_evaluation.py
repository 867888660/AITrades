from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from services.data_platform import (
    AlphaEvaluator,
    DataPlatformStore,
    EvaluationSpec,
    FactorEvaluator,
    FutureReturnBuilder,
    ResearchArtifactMaterializer,
)


def evaluation_fixture() -> tuple[dict[str, list[dict[str, object]]], dict[str, list[dict[str, object]]], list[dict[str, object]]]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars: dict[str, list[dict[str, object]]] = {}
    factors: dict[str, list[dict[str, object]]] = {}
    signals_by_time: dict[str, dict[str, float]] = {}
    for index, slope in enumerate((0.001, 0.002, 0.003, 0.004)):
        instrument = f"A{index}"
        instrument_bars = []
        instrument_factors = []
        price = 100.0
        for bar_index in range(8):
            bar_start = start + timedelta(hours=bar_index)
            bar_end = bar_start + timedelta(hours=1) - timedelta(milliseconds=1)
            close = price * (1.0 + slope)
            instrument_bars.append({
                "bar_start_time": bar_start.isoformat(),
                "bar_end_time": bar_end.isoformat(),
                "available_time": bar_end.isoformat(),
                "open": price,
                "close": close,
            })
            if bar_index < 6:
                instrument_factors.append({
                    "instrument_id": instrument,
                    "factor_as_of_time": bar_end.isoformat(),
                    "available_time": bar_end.isoformat(),
                    "value": slope,
                })
                signals_by_time.setdefault(bar_end.isoformat(), {})[instrument] = slope
            price = close
        bars[instrument] = instrument_bars
        factors[instrument] = instrument_factors
    signals = [
        {
            "as_of_time": as_of,
            "available_time": as_of,
            "raw_scores": scores,
            "scores": scores,
        }
        for as_of, scores in sorted(signals_by_time.items())
    ]
    return bars, factors, signals


class EvaluationTest(unittest.TestCase):
    def test_future_return_starts_at_first_bar_after_signal_availability(self) -> None:
        bars, _, _ = evaluation_fixture()
        result = FutureReturnBuilder(bars).build("A0", "2026-01-01T00:59:59.999000+00:00", 1)
        self.assertIsNotNone(result)
        self.assertEqual("2026-01-01T01:00:00+00:00", result["return_start_time"])
        self.assertAlmostEqual(0.001, result["future_return"], places=12)

    def test_factor_rank_ic_and_quantile_spread_are_positive(self) -> None:
        bars, factors, _ = evaluation_fixture()
        result = FactorEvaluator().evaluate(
            spec=EvaluationSpec(horizons=(1, 2), quantile_count=4, minimum_cross_section_size=4),
            factor_values_by_instrument=factors,
            bars_by_instrument=bars,
        )
        self.assertEqual("FACTOR_RUN", result.summary["product_run_type"])
        self.assertEqual(1.0, result.summary["coverage"])
        self.assertAlmostEqual(1.0, result.summary["ic"]["1"]["mean"], places=12)
        self.assertAlmostEqual(1.0, result.summary["rank_ic"]["1"]["mean"], places=12)
        self.assertGreater(result.summary["rank_ic"]["1"]["count"], 0)
        self.assertEqual({"A0", "A1", "A2", "A3"}, set(result.summary["coverage_by_instrument"]))
        self.assertGreater(result.summary["quantile_returns"]["1"]["high_minus_low"], 0)
        self.assertIn("cross_section_mean_lag1_correlation", result.summary["time_stability"])
        self.assertTrue(all(item["return_start_time"] > item["available_time"] for item in result.observations))

    def test_alpha_evaluation_reports_decay_stability_turnover_and_cost(self) -> None:
        bars, _, signals = evaluation_fixture()
        result = AlphaEvaluator().evaluate(
            spec=EvaluationSpec(
                horizons=(1, 2),
                minimum_cross_section_size=4,
                top_n=1,
                fee_bps=10,
                slippage_bps=5,
            ),
            alpha_signals=signals,
            bars_by_instrument=bars,
        )
        self.assertEqual("ALPHA_RUN", result.summary["product_run_type"])
        self.assertAlmostEqual(1.0, result.summary["ic"]["1"]["mean"], places=12)
        self.assertAlmostEqual(1.0, result.summary["rank_ic"]["1"]["mean"], places=12)
        self.assertGreater(result.summary["rank_ic"]["1"]["count"], 0)
        self.assertGreater(len(result.ic_series), 0)
        self.assertAlmostEqual(1.0, result.summary["average_rank_stability"], places=12)
        self.assertEqual(0.0, result.summary["average_membership_turnover"])
        self.assertGreater(result.summary["holding_period_decay"]["1"]["long_short_spread"], 0)
        self.assertLessEqual(
            result.summary["holding_period_decay"]["1"]["top_mean_return_after_cost"],
            result.summary["holding_period_decay"]["1"]["top_mean_return"],
        )

    def test_evaluation_artifact_records_lineage(self) -> None:
        bars, factors, _ = evaluation_fixture()
        spec = EvaluationSpec(horizons=(1,), quantile_count=4, minimum_cross_section_size=4)
        result = FactorEvaluator().evaluate(
            spec=spec,
            factor_values_by_instrument=factors,
            bars_by_instrument=bars,
        )
        with tempfile.TemporaryDirectory() as temp:
            store = DataPlatformStore(Path(temp) / "metadata.db")
            materializer = ResearchArtifactMaterializer(store, root=Path(temp) / "artifacts")
            artifact = materializer.materialize_evaluation(
                logical_name="factor-evaluation",
                result=result,
                spec=spec,
                input_artifact_id="factor-artifact-test",
                dataset_manifest_ids=["manifest-test"],
                universe_snapshot_id="snapshot-test",
            )
            self.assertEqual("FACTOR_EVALUATION", artifact.artifact_type)
            dependencies = materializer.artifacts.dependencies(artifact.artifact_id)
            self.assertEqual(
                {"factor-artifact-test", "manifest-test", "snapshot-test"},
                {item["parent_id"] for item in dependencies},
            )


if __name__ == "__main__":
    unittest.main()
