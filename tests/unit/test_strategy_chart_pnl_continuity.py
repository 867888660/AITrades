import unittest
from unittest.mock import MagicMock, patch

from StrategyCode.Stragy_Fllow_Stock_Value import _UseDataProxy, _run_strategy
from services.strategy_chart_delta_service import _build_stats_delta
from services.strategy_chart_service import _apply_virtual_account_pnl_to_rows, _metric_series_items


class StrategyChartPnlContinuityTests(unittest.TestCase):
    def test_virtual_replay_carries_last_pnl_when_current_mark_is_missing(self):
        account = {"initial_cash": 100.0}
        orders = [
            {
                "id": 1,
                "leg_index": 0,
                "action": "BUY",
                "side": "YES",
                "qty": 2.0,
                "price": 0.48,
                "gross_notional": 0.96,
                "fee_rate": 0.0,
                "fee": 0.0,
                "net_cash_change": -0.96,
                "liquidity_role": "taker",
                "status": "filled",
                "reason": "entry",
                "created_at_utc": "2026-07-23T09:00:00+00:00",
            }
        ]
        connection = MagicMock()
        connection.execute.side_effect = [
            MagicMock(fetchone=MagicMock(return_value=account)),
            MagicMock(fetchall=MagicMock(return_value=orders)),
        ]
        rows = [
            {
                "ts": "2026-07-23T10:00:00+00:00",
                "market_0_yes_bid": 0.85,
                "market_0_yes_ask": 0.88,
            },
            {
                "ts": "2026-07-23T11:00:00+00:00",
                "market_0_yes_bid": None,
                "market_0_yes_ask": None,
                "strategy_pnl": 0.0,
            },
        ]
        detail = {
            "mode": "Virtual",
            "row_id": 86,
            "strategy_bankroll": 100.0,
            "pnl_source": "virtual_account_partial_cost_fallback",
        }

        with patch("services.strategy_chart_service.strategy_data_source.connect", return_value=connection):
            _apply_virtual_account_pnl_to_rows(detail, rows, chart_interval_seconds=3600)

        self.assertAlmostEqual(rows[0]["strategy_pnl"], 0.74)
        self.assertAlmostEqual(rows[1]["strategy_pnl"], rows[0]["strategy_pnl"])
        self.assertEqual(rows[1]["pnl_source"], "virtual_order_replay_last_valid_mark")

    def test_delta_does_not_overwrite_replayed_pnl_with_partial_cost_fallback(self):
        ts = "2026-07-23T10:00:00+00:00"
        detail = {
            "mode": "Virtual",
            "row_id": 86,
            "strategy_pnl": 0.0,
            "pnl_source": "virtual_account_partial_cost_fallback",
        }

        def replay(_detail, rows):
            rows[-1]["strategy_pnl"] = 71.3013726445744
            rows[-1]["pnl_source"] = "virtual_order_replay_depth"

        with (
            patch("services.strategy_chart_delta_service._load_stats_samples", return_value={}),
            patch("services.strategy_chart_delta_service._load_strategy_tick_price_samples", return_value={}),
            patch("services.strategy_chart_delta_service._load_price_samples", return_value={}),
            patch(
                "services.strategy_chart_delta_service._detail_sample",
                return_value={ts: {"ts": ts, "strategy_pnl": 0.0}},
            ),
            patch(
                "services.strategy_chart_delta_service._apply_virtual_account_pnl_to_rows",
                side_effect=replay,
            ),
        ):
            rows = _build_stats_delta(
                detail,
                [],
                "2026-07-23T09:00:00+00:00",
                "2026-07-23T11:00:00+00:00",
                3600,
            )

        self.assertAlmostEqual(rows[-1]["strategy_pnl"], 71.3013726445744)


class StockValueMetricUnitTests(unittest.TestCase):
    def test_market_cap_gaps_are_compact_currency_not_ratios(self):
        usedata = {
            "McapUsd_AAPL": 4_000_000_000_000,
            "McapUsd_MSFT": 3_000_000_000_000,
            "McapUsd_NVDA": 5_000_000_000_000,
            "McapUsd_GOOGL": 2_800_000_000_000,
            "McapUsd_AMZN": 2_500_000_000_000,
            "McapUsd_META": 1_800_000_000_000,
            "McapUsd_TSLA": 1_200_000_000_000,
            "day_to_end": 8.0,
            "Yes_now_bid": 0.85,
            "Yes_now_ask": 0.88,
            "No_now_bid": 0.12,
            "No_now_ask": 0.15,
        }

        result = _run_strategy(_UseDataProxy(usedata), "AAPL", 2)

        self.assertEqual(result["metrics_meta"]["gap_up"]["unit"], "compact_currency")
        self.assertEqual(result["metrics_meta"]["gap_down"]["unit"], "compact_currency")
        self.assertEqual(result["metrics_meta"]["gap_up"]["panel"], "market_mcap")
        self.assertEqual(result["metrics_meta"]["gap_down"]["panel"], "market_mcap")

        series = _metric_series_items(
            ["gap_up", "gap_down"],
            {
                "metric_catalog": {
                    "items": [
                        {"key": "gap_up", "label": "Gap Up", "unit": "compact_currency"},
                        {"key": "gap_down", "label": "Gap Down", "unit": "compact_currency"},
                    ]
                }
            },
        )
        self.assertTrue(all(item["panel"] == "market_mcap" for item in series))


if __name__ == "__main__":
    unittest.main()
