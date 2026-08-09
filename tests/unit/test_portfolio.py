from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from services.data_platform import PortfolioEngine, PortfolioSpec, ResearchBacktestProvider


class PortfolioEngineTest(unittest.TestCase):
    def test_no_eligible_instrument_emits_flat_target_and_exit_trade(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        bars = {
            "BTC": [
                {
                    "bar_start_time": (start + timedelta(hours=index)).isoformat(),
                    "bar_end_time": (start + timedelta(hours=index + 1) - timedelta(milliseconds=1)).isoformat(),
                    "available_time": (start + timedelta(hours=index + 1) - timedelta(milliseconds=1)).isoformat(),
                    "open": 100.0 + index,
                    "high": 101.0 + index,
                    "low": 99.0 + index,
                    "close": 100.5 + index,
                    "volume": 10.0,
                    "bar_status": "COMPLETE",
                }
                for index in range(4)
            ]
        }
        signals = [
            {
                "as_of_time": (start + timedelta(hours=1) - timedelta(milliseconds=1)).isoformat(),
                "available_time": (start + timedelta(hours=1) - timedelta(milliseconds=1)).isoformat(),
                "raw_scores": {"BTC": 0.10},
                "ranks": {"BTC": 1.0},
                "percentiles": {"BTC": 1.0},
            },
            {
                "as_of_time": (start + timedelta(hours=2) - timedelta(milliseconds=1)).isoformat(),
                "available_time": (start + timedelta(hours=2) - timedelta(milliseconds=1)).isoformat(),
                "raw_scores": {"BTC": -0.10},
                "ranks": {"BTC": 1.0},
                "percentiles": {"BTC": 1.0},
            },
        ]
        spec = PortfolioSpec(
            top_n=1,
            rebalance_frequency="EVERY_SIGNAL",
            minimum_score=0.0,
        )

        targets = PortfolioEngine().build_targets(signals, spec)

        self.assertEqual(2, len(targets))
        self.assertEqual("INVESTED", targets[0]["target_state"])
        self.assertEqual({"BTC": 1.0}, targets[0]["weights"])
        self.assertEqual("FLAT", targets[1]["target_state"])
        self.assertEqual({}, targets[1]["weights"])
        self.assertEqual({"BTC": -0.10}, targets[1]["raw_scores"])
        self.assertEqual({}, targets[1]["eligible_scores"])

        result = ResearchBacktestProvider().simulate(
            bars_by_instrument=bars,
            alpha_signals=targets,
            portfolio_spec=spec,
        )

        self.assertEqual(["BUY", "SELL"], [order["side"] for order in result.orders])
        self.assertEqual(0.0, result.equity_curve[-1]["positions"]["BTC"])
        self.assertEqual(2, result.metrics["trade_count"])
        self.assertEqual(1, result.metrics["invested_rebalance_count"])
        self.assertEqual(1, result.metrics["flat_rebalance_count"])
        self.assertEqual("FLAT", result.rebalance_events[-1]["target_state"])


if __name__ == "__main__":
    unittest.main()
