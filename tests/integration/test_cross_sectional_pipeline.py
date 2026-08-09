from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from services.data_platform import (
    AlphaComponent,
    AlphaEngine,
    AlphaSpec,
    FactorEngine,
    FactorSpec,
    PortfolioEngine,
    PortfolioSpec,
    ResearchBacktestProvider,
)


class CrossSectionalPipelineTest(unittest.TestCase):
    def test_five_instrument_rotation_is_deterministic(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        bars: dict[str, list[dict[str, object]]] = {}
        for instrument_index in range(5):
            instrument_id = f"ASSET{instrument_index}"
            rows = []
            for bar_index in range(240):
                bar_start = start + timedelta(hours=bar_index)
                bar_end = bar_start + timedelta(hours=1) - timedelta(milliseconds=1)
                regime = (bar_index // 24 + instrument_index) % 5
                close = 100 + instrument_index * 5 + bar_index * (0.01 + regime * 0.002)
                rows.append({
                    "bar_start_time": bar_start.isoformat(),
                    "bar_end_time": bar_end.isoformat(),
                    "available_time": bar_end.isoformat(),
                    "open": close * 0.999,
                    "high": close * 1.002,
                    "low": close * 0.998,
                    "close": close,
                    "volume": 10,
                    "bar_status": "COMPLETE",
                })
            bars[instrument_id] = rows
        factor_engine = FactorEngine()
        factors = {
            "momentum": factor_engine.compute(FactorSpec("momentum", "1", "pct_change", window=20), bars),
            "volatility": factor_engine.compute(FactorSpec("volatility", "1", "rolling_return_std", window=20), bars),
        }
        alpha = AlphaEngine().build_signals(
            AlphaSpec(
                "alpha", "1",
                (AlphaComponent("momentum", 0.7), AlphaComponent("volatility", -0.3)),
                minimum_cross_section_size=4,
            ),
            factors,
        )
        portfolio_spec = PortfolioSpec(top_n=2, rebalance_frequency="DAILY", max_position_weight=0.5)
        targets = PortfolioEngine().build_targets(alpha, portfolio_spec)[:-1]
        first = ResearchBacktestProvider().simulate(
            bars_by_instrument=bars,
            alpha_signals=targets,
            portfolio_spec=portfolio_spec,
        )
        second = ResearchBacktestProvider().simulate(
            bars_by_instrument=bars,
            alpha_signals=targets,
            portfolio_spec=portfolio_spec,
        )
        self.assertGreaterEqual(first.metrics["rebalance_count"], 8)
        self.assertGreater(len({tuple(item["selected_instrument_ids"]) for item in targets}), 1)
        self.assertEqual(first.orders, second.orders)
        self.assertEqual(first.equity_curve, second.equity_curve)


if __name__ == "__main__":
    unittest.main()
