import unittest
from unittest.mock import patch

from services.polymarket_service import (
    _build_strategy_item,
    _recompute_strategy_metrics,
    _virtual_total_pnl_from_account,
)
from services.strategy_chart_service import _position_liquidation, _position_mark
from services.strategy_workspace_service import _apply_multi_leg_summary_pnl
from services.virtual_runner import _price_snapshot_from_use_data


class MultiLegPnlValuationTests(unittest.TestCase):
    def test_workspace_summary_sums_per_leg_liquidation_pnl(self) -> None:
        strategy = {"mode": "Virtual", "strategy_pnl": -31.0}
        legs = [
            {"yes_qty": 10.0, "no_qty": 0.0, "pnl": -1.25},
            {"yes_qty": 0.0, "no_qty": 5.0, "pnl": 0.40},
        ]

        _apply_multi_leg_summary_pnl(strategy, legs)

        self.assertAlmostEqual(strategy["strategy_pnl"], -0.85)
        self.assertEqual(strategy["pnl_source"], "multi_leg_liquidation_sum")

    def test_chart_mark_uses_requested_leg_quote(self) -> None:
        row = {
            "market_0_yes_bid": 0.80,
            "market_1_yes_bid": 0.20,
        }

        self.assertEqual(_position_mark(row, "YES", 0.30, leg_index=1), 0.20)
        liquidation = _position_liquidation(
            row,
            "YES",
            10.0,
            0.0,
            leg_index=1,
        )
        self.assertAlmostEqual(liquidation["gross"], 2.0)

    def test_missing_leg_quote_falls_back_to_cost_not_cached_equity(self) -> None:
        strategy = {"mode": "Virtual"}
        positions = [
            {"side": "YES", "qty": 10.0, "avg": 1.0, "bid": 0.9},
            {"side": "NO", "qty": 20.0, "avg": 1.0, "bid": None, "ask": None},
        ]
        account = {
            "initial_cash": 100.0,
            "cash": 70.0,
            "equity": 50.0,
            "unrealized_pnl": -50.0,
            "realized_pnl": 0.0,
            "total_fees_paid": 0.0,
        }

        _virtual_total_pnl_from_account(strategy, 88, positions, account=account)

        self.assertEqual(strategy["pnl_source"], "virtual_account_partial_cost_fallback")
        self.assertAlmostEqual(strategy["virtual_equity"], 98.955)
        self.assertAlmostEqual(strategy["strategy_pnl"], -1.045)

    def test_virtual_tick_persists_depth_for_executable_pnl(self) -> None:
        snapshot = _price_snapshot_from_use_data(
            {
                "LegCount": 1,
                "NowTime": "2026-07-24T15:31:16+00:00",
                "Yes_now_bid": 0.80,
                "Yes_now_ask": 0.83,
                "No_now_bid": 0.17,
                "No_now_ask": 0.20,
                "Yes_BidLevels": [
                    {"price": 0.80, "qty": 24.73},
                    {"price": 0.78, "qty": 49.03},
                ],
            }
        )

        self.assertEqual(snapshot["captured_at_utc"], "2026-07-24T15:31:16+00:00")
        self.assertEqual(snapshot["yes_bid_levels"][1]["price"], 0.78)
        self.assertEqual(snapshot["legs"][0]["yes_bid_levels"][0]["qty"], 24.73)

    def test_virtual_summary_uses_recent_tick_depth_instead_of_best_bid_for_all_qty(self) -> None:
        tick_prices = {
            "yes_bid": 0.80,
            "yes_ask": 0.83,
            "no_bid": 0.17,
            "no_ask": 0.20,
            "yes_bid_levels": [
                {"price": 0.80, "qty": 24.73},
                {"price": 0.78, "qty": 49.03},
                {"price": 0.75, "qty": 50.0},
                {"price": 0.71, "qty": 177.0},
            ],
            "yes_ask_levels": [],
            "no_bid_levels": [],
            "no_ask_levels": [],
            "yes_bid_size": 24.73,
            "yes_ask_size": None,
            "no_bid_size": None,
            "no_ask_size": None,
            "yes_last_price": 0.83,
            "no_last_price": 0.20,
            "price_source": "virtual_tick_orderbook",
            "updated_at": "2026-07-24T15:31:16+00:00",
            "snapshot_db_path": "strategy.db",
        }
        empty_prices = {
            "yes_bid": None,
            "yes_ask": None,
            "no_bid": None,
            "no_ask": None,
            "yes_bid_levels": [],
            "yes_ask_levels": [],
            "no_bid_levels": [],
            "no_ask_levels": [],
            "yes_bid_size": None,
            "yes_ask_size": None,
            "no_bid_size": None,
            "no_ask_size": None,
            "yes_last_price": None,
            "no_last_price": None,
            "price_source": None,
            "updated_at": None,
            "snapshot_db_path": None,
        }
        item = {
            "mode": "Virtual",
            "Yes_now_qty": 203.05393112410658,
            "Yes_avg_cost": 0.48,
            "No_now_qty": 0.0,
            "strategy_bankroll": 100.0,
        }
        with (
            patch("services.polymarket_service._resolve_strategy_market_prices", return_value=empty_prices),
            patch("services.polymarket_service._latest_virtual_tick_price_snapshot", return_value=tick_prices),
        ):
            strategy = _build_strategy_item(
                item,
                None,
                row_id=86,
                include_realtime_prices=True,
                allow_clob_book=False,
            )
        _recompute_strategy_metrics(
            strategy,
            virtual_account={
                "initial_cash": 100.0,
                "cash": 0.0,
                "realized_pnl": 0.0,
                "total_fees_paid": 2.53411306042885,
            },
        )

        self.assertEqual(strategy["price_source"], "virtual_tick_orderbook")
        self.assertAlmostEqual(strategy["virtual_liquidation_vwap"], 0.74771313344)
        self.assertAlmostEqual(strategy["strategy_pnl"], 49.92249267719299)


if __name__ == "__main__":
    unittest.main()
