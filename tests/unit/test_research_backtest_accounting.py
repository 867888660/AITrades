from __future__ import annotations

import unittest

from services.data_platform import BacktestExecutionSpec, ResearchBacktestProvider


class ResearchBacktestAccountingTest(unittest.TestCase):
    def test_orders_match_independent_hand_calculation(self) -> None:
        bars = {
            "A": [
                {"event_time": "2026-01-01T00:00:00+00:00", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1},
                {"event_time": "2026-01-01T01:00:00+00:00", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1},
                {"event_time": "2026-01-01T02:00:00+00:00", "open": 110, "high": 111, "low": 109, "close": 110, "volume": 1},
            ],
            "B": [
                {"event_time": "2026-01-01T00:00:00+00:00", "open": 200, "high": 201, "low": 199, "close": 200, "volume": 1},
                {"event_time": "2026-01-01T01:00:00+00:00", "open": 200, "high": 201, "low": 199, "close": 200, "volume": 1},
                {"event_time": "2026-01-01T02:00:00+00:00", "open": 190, "high": 191, "low": 189, "close": 190, "volume": 1},
            ],
        }
        signals = [
            {"as_of_time": "2026-01-01T00:00:00+00:00", "weights": {"A": 0.5, "B": 0.5}},
            {"as_of_time": "2026-01-01T01:00:00+00:00", "weights": {"A": 1.0, "B": 0.0}},
        ]
        fee_rate = 10 / 10_000
        slippage_rate = 100 / 10_000
        result = ResearchBacktestProvider().simulate(
            bars_by_instrument=bars,
            alpha_signals=signals,
            initial_cash=10_000,
            execution_spec=BacktestExecutionSpec(fee_bps=10, slippage_bps=100),
        )

        # Independent reference calculation; no production helper is reused.
        first_required = 50 * 101 * (1 + fee_rate) + 25 * 202 * (1 + fee_rate)
        first_scale = 10_000 / first_required
        expected_a_first = 50 * first_scale
        expected_b_first = 25 * first_scale
        expected_cash_after_first = 10_000
        expected_cash_after_first -= expected_a_first * 101 * (1 + fee_rate)
        expected_cash_after_first -= expected_b_first * 202 * (1 + fee_rate)

        equity_second_open = expected_cash_after_first + expected_a_first * 110 + expected_b_first * 190
        expected_b_sell_value = expected_b_first * 190 * (1 - slippage_rate)
        expected_b_sell_fee = expected_b_sell_value * fee_rate
        cash_after_sell = expected_cash_after_first + expected_b_sell_value - expected_b_sell_fee
        desired_a_second = equity_second_open / 110 - expected_a_first
        required_a_second = desired_a_second * 110 * (1 + slippage_rate) * (1 + fee_rate)
        second_scale = min(1.0, cash_after_sell / required_a_second)
        expected_a_second_buy = desired_a_second * second_scale
        expected_final_cash = cash_after_sell - expected_a_second_buy * 110 * (1 + slippage_rate) * (1 + fee_rate)
        expected_final_equity = expected_final_cash + (expected_a_first + expected_a_second_buy) * 110

        self.assertEqual(4, len(result.orders))
        self.assertAlmostEqual(expected_a_first, result.orders[0]["quantity"], places=10)
        self.assertAlmostEqual(expected_b_first, result.orders[1]["quantity"], places=10)
        self.assertAlmostEqual(expected_b_first, result.orders[2]["quantity"], places=10)
        self.assertAlmostEqual(expected_a_second_buy, result.orders[3]["quantity"], places=10)
        self.assertAlmostEqual(101.0, result.orders[0]["fill_price"], places=10)
        self.assertAlmostEqual(188.1, result.orders[2]["fill_price"], places=10)
        self.assertAlmostEqual(expected_final_cash, result.equity_curve[-1]["cash"], places=8)
        self.assertAlmostEqual(expected_final_equity, result.metrics["final_equity"], places=8)
        self.assertGreaterEqual(min(item["cash"] for item in result.equity_curve), 0)
        self.assertEqual(len(result.equity_curve), len(result.drawdown_curve))
        self.assertAlmostEqual(
            min(item["drawdown"] for item in result.drawdown_curve),
            result.metrics["max_drawdown"],
            places=12,
        )
        self.assertIn("position_weights", result.equity_curve[-1])
        self.assertIn("max_underwater_bars", result.metrics)

    def test_missing_bar_fails_instead_of_silent_alignment(self) -> None:
        bars = {
            "A": [
                {"event_time": "2026-01-01T00:00:00+00:00", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 0},
                {"event_time": "2026-01-01T01:00:00+00:00", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 0},
            ],
            "B": [
                {"event_time": "2026-01-01T00:00:00+00:00", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 0},
                {"event_time": "2026-01-01T02:00:00+00:00", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 0},
            ],
        }
        with self.assertRaisesRegex(ValueError, "not strictly aligned"):
            ResearchBacktestProvider().simulate(bars_by_instrument=bars, alpha_signals=[])

    def test_explicit_availability_at_next_open_executes_without_extra_bar_delay(self) -> None:
        bars = {
            "A": [
                {"event_time": f"2026-01-01T0{hour}:00:00+00:00", "open": 100 + hour, "high": 102 + hour, "low": 99 + hour, "close": 101 + hour, "volume": 1}
                for hour in range(3)
            ]
        }
        result = ResearchBacktestProvider().simulate(
            bars_by_instrument=bars,
            alpha_signals=[{
                "as_of_time": "2026-01-01T01:00:00+00:00",
                "available_time": "2026-01-01T01:00:00+00:00",
                "weights": {"A": 1.0},
            }],
            fee_bps=0,
            slippage_bps=0,
        )
        self.assertEqual("2026-01-01T01:00:00+00:00", result.orders[0]["event_time"])

    def test_execution_spec_rejects_ambiguous_boolean_and_nan_cost(self) -> None:
        parsed = BacktestExecutionSpec.from_payload({"allow_short": "false"})
        self.assertFalse(parsed.allow_short)
        with self.assertRaisesRegex(ValueError, "invalid boolean"):
            BacktestExecutionSpec.from_payload({"allow_short": "sometimes"})
        with self.assertRaisesRegex(ValueError, "finite"):
            BacktestExecutionSpec(fee_bps=float("nan"))

    def test_annualization_uses_full_calendar_span(self) -> None:
        event_times = [
            "2026-01-02T00:00:00+00:00",  # Friday
            "2026-01-05T00:00:00+00:00",  # Monday
            "2026-01-06T00:00:00+00:00",
            "2026-01-07T00:00:00+00:00",
            "2026-01-08T00:00:00+00:00",
        ]
        bars = {
            "A": [
                {"event_time": event_time, "open": 100 + index, "high": 101 + index,
                 "low": 99 + index, "close": 100 + index, "volume": 1}
                for index, event_time in enumerate(event_times)
            ]
        }
        result = ResearchBacktestProvider().simulate(
            bars_by_instrument=bars,
            alpha_signals=[{"as_of_time": event_times[0], "weights": {"A": 1.0}}],
            fee_bps=0,
            slippage_bps=0,
        )
        expected_observations = 4 * 365.25 / 6
        expected_cagr = (
            result.metrics["final_equity"] / result.metrics["initial_cash"]
        ) ** (365.25 / 6) - 1
        self.assertAlmostEqual(expected_observations, result.metrics["observations_per_year"], places=10)
        self.assertAlmostEqual(expected_cagr, result.metrics["annualized_return"], places=10)


if __name__ == "__main__":
    unittest.main()
