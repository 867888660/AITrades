from __future__ import annotations

import unittest
from types import SimpleNamespace

from services.data_platform import AlphaComponent, AlphaEngine, AlphaSpec, FactorEngine, FactorSpec


class FactorAlphaSemanticsTest(unittest.TestCase):
    def test_dynamic_equity_membership_excludes_not_yet_listed_and_delisted_names(self) -> None:
        snapshot = SimpleNamespace(
            universe_snapshot_id="universe_snapshot_dynamic",
            actual_instrument_ids=("A", "B"),
            selection_inputs={
                "dynamic_membership": True,
                "membership_intervals": {
                    "A": {"eligible_from": "2025-01-01", "eligible_to": "2025-01-15"},
                    "B": {"eligible_from": "2025-01-16", "eligible_to": "2025-12-31"},
                },
            },
        )
        values = {
            "size": {
                "A": [
                    {"available_time": "2025-01-10T21:00:00+00:00", "value": 1.0},
                    {"available_time": "2025-01-20T21:00:00+00:00", "value": 1.0},
                ],
                "B": [
                    {"available_time": "2025-01-10T21:00:00+00:00", "value": 2.0},
                    {"available_time": "2025-01-20T21:00:00+00:00", "value": 2.0},
                ],
            }
        }
        spec = AlphaSpec(
            "dynamic_size",
            "1.0.0",
            (AlphaComponent("size", 1.0),),
            universe_snapshot_id="universe_snapshot_dynamic",
            minimum_cross_section_size=1,
        )

        signals = AlphaEngine().build_signals(spec, values, universe_snapshot=snapshot)

        self.assertEqual([{"A"}, {"B"}], [set(item["raw_scores"]) for item in signals])
        self.assertEqual([1, 1], [item["active_universe_size"] for item in signals])

    def test_cross_sectional_ties_use_average_rank(self) -> None:
        transformed = AlphaEngine._transform({"A": 1.0, "B": 2.0, "C": 2.0}, "CS_RANK")
        self.assertAlmostEqual(1 / 3, transformed["A"])
        self.assertAlmostEqual(2.5 / 3, transformed["B"])
        self.assertEqual(transformed["B"], transformed["C"])

        ranks, percentiles = AlphaEngine._rank_scores({"A": 2.0, "B": 1.0, "C": 1.0})
        self.assertEqual(1.0, ranks["A"])
        self.assertEqual(2.5, ranks["B"])
        self.assertEqual(ranks["B"], ranks["C"])
        self.assertEqual(percentiles["B"], percentiles["C"])

    def test_factor_rejects_future_availability(self) -> None:
        bars = {"A": [{
            "bar_start_time": "2026-01-01T00:00:00+00:00",
            "bar_end_time": "2026-01-01T01:00:00+00:00",
            "available_time": "2026-01-01T00:30:00+00:00",
            "close": 100,
            "bar_status": "COMPLETE",
        }]}
        with self.assertRaisesRegex(ValueError, "available_time precedes"):
            FactorEngine().compute(FactorSpec("bad_time", "1", "rolling_mean"), bars)

    def test_alpha_rejects_non_finite_weight(self) -> None:
        with self.assertRaisesRegex(ValueError, "finite"):
            AlphaSpec("bad", "1", (AlphaComponent("factor", float("nan")),))

    def test_ema_is_recursive_after_sma_seed(self) -> None:
        bars = {"A": [
            {
                "bar_start_time": f"2026-01-01T0{index}:00:00+00:00",
                "bar_end_time": f"2026-01-01T0{index}:59:59+00:00",
                "available_time": f"2026-01-01T0{index}:59:59+00:00",
                "close": float(index + 1),
                "bar_status": "COMPLETE",
            }
            for index in range(5)
        ]}
        values = FactorEngine().compute(FactorSpec("ema_3", "1", "ema", window=3), bars)["A"]
        self.assertEqual([None, None, 2.0, 3.0, 4.0], [item["value"] for item in values])

    def test_ma_crossover_emits_golden_and_death_cross_events(self) -> None:
        closes = [3, 2, 1, 2, 3, 2, 1]
        bars = {"crypto_spot:BINANCE:BTCUSDT": [
            {
                "bar_start_time": f"2026-01-01T0{index}:00:00+00:00",
                "bar_end_time": f"2026-01-01T0{index}:59:59+00:00",
                "available_time": f"2026-01-01T0{index}:59:59+00:00",
                "close": float(close),
                "bar_status": "COMPLETE",
            }
            for index, close in enumerate(closes)
        ]}
        spec = FactorSpec(
            "btc_sma_2_3_cross",
            "1.0.0",
            "ma_crossover",
            window=3,
            parameters={"fast_window": 2},
            frequency="1h",
            output_unit="SIGNAL",
        )

        values = FactorEngine().compute(spec, bars)["crypto_spot:BINANCE:BTCUSDT"]

        self.assertEqual(
            [None, None, None, 0.0, 1.0, 0.0, -1.0],
            [item["value"] for item in values],
        )
        self.assertEqual(4, spec.required_observations)

    def test_ma_crossover_rejects_invalid_windows(self) -> None:
        with self.assertRaisesRegex(ValueError, "smaller than slow window"):
            FactorSpec(
                "bad_cross",
                "1.0.0",
                "ma_crossover",
                window=5,
                parameters={"fast_window": 5},
            )


if __name__ == "__main__":
    unittest.main()
