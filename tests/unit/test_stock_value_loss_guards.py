import unittest

from StrategyCode.Stragy_Fllow_Stock_Value import _UseDataProxy, _run_strategy


def _base_usedata():
    return {
        "McapUsd_NVDA": 5_000_000_000_000,
        "McapUsd_AAPL": 4_000_000_000_000,
        "McapUsd_MSFT": 3_000_000_000_000,
        "McapUsd_GOOGL": 2_800_000_000_000,
        "McapUsd_AMZN": 2_500_000_000_000,
        "McapUsd_META": 1_800_000_000_000,
        "McapUsd_TSLA": 1_200_000_000_000,
        "NowTime": "2026-07-01T00:00:00Z",
        "Enddate": "2026-12-31T00:00:00Z",
        "L0_EndTime": "2026-12-31T00:00:00Z",
        "L0_MarketStatus": "open",
        "day_to_end": 183.0,
        "Yes_now_bid": 0.59,
        "Yes_now_ask": 0.60,
        "No_now_bid": 0.39,
        "No_now_ask": 0.40,
        "Yes_Now_Pos": 0.0,
        "No_Now_Pos": 0.0,
        "Portfolio": {"cash": 100.0},
    }


class StockValueLossGuardTests(unittest.TestCase):
    def test_expired_market_never_emits_position_actions(self):
        usedata = _base_usedata()
        usedata.update(
            {
                "NowTime": "2026-08-04T00:04:00Z",
                "Enddate": "2026-07-31T23:59:00Z",
                "L0_EndTime": "2026-07-31T23:59:00Z",
                "L0_MarketStatus": "expired",
                "day_to_end": 0.0,
            }
        )

        result = _run_strategy(_UseDataProxy(usedata), "AAPL", 2)

        self.assertEqual(result["actions"], [])
        self.assertTrue(result["metrics"]["market_terminal"])
        self.assertIn("market_terminal", "\n".join(result["print"]))

    def test_settlement_lock_holds_existing_position_through_rank_noise(self):
        usedata = _base_usedata()
        usedata.update(
            {
                "McapUsd_AAPL": 2_900_000_000_000,
                "NowTime": "2026-07-28T00:00:00Z",
                "Enddate": "2026-07-31T23:59:00Z",
                "L0_EndTime": "2026-07-31T23:59:00Z",
                "day_to_end": 3.99,
                "Yes_Now_Pos": 1.0,
                "Portfolio": {"cash": 0.0},
            }
        )

        result = _run_strategy(_UseDataProxy(usedata), "AAPL", 2)

        self.assertEqual(result["metrics"]["fact_state"], "F_NO")
        self.assertTrue(result["metrics"]["settlement_locked"])
        self.assertEqual(result["actions"], [])

    def test_broken_yes_thesis_defaults_to_cash_not_no_reversal(self):
        usedata = _base_usedata()
        usedata["McapUsd_AAPL"] = 2_900_000_000_000

        result = _run_strategy(_UseDataProxy(usedata), "AAPL", 2)

        self.assertEqual(result["metrics"]["fact_state"], "F_NO")
        self.assertEqual(result["metrics"]["target_state"], "P0_EMPTY")
        self.assertEqual(result["actions"], [])

    def test_no_reversal_remains_available_by_explicit_opt_in(self):
        usedata = _base_usedata()
        usedata["McapUsd_AAPL"] = 2_900_000_000_000
        usedata["Controls"] = {"allow_no_reversal": True}

        result = _run_strategy(_UseDataProxy(usedata), "AAPL", 2)

        self.assertEqual(result["metrics"]["next_state"], "P3_NO_HALF")
        self.assertTrue(any(a["side"] == "No" and a["target_pct"] == 0.5 for a in result["actions"]))

    def test_cash_guard_suppresses_repeated_unfunded_add_orders(self):
        usedata = _base_usedata()
        usedata.update({"Yes_Now_Pos": 0.5, "Portfolio": {"cash": 0.0}})

        result = _run_strategy(_UseDataProxy(usedata), "AAPL", 2)

        self.assertEqual(result["actions"], [])
        self.assertIn("entry_cash_guard", "\n".join(result["print"]))

    def test_rank_gap_thresholds_scale_with_anchor_market_cap(self):
        usedata = _base_usedata()
        usedata.update(
            {
                "McapUsd_AAPL": 2_000_000_000_000,
                "McapUsd_MSFT": 1_000_000_000_000,
                "McapUsd_GOOGL": 900_000_000_000,
                "McapUsd_AMZN": 800_000_000_000,
                "McapUsd_META": 700_000_000_000,
                "McapUsd_TSLA": 600_000_000_000,
            }
        )

        result = _run_strategy(_UseDataProxy(usedata), "AAPL", 2)

        self.assertEqual(result["metrics"]["yes_full_on"], 60_000_000_000)
        self.assertEqual(result["metrics_meta"]["yes_full_on"]["unit"], "compact_currency")


if __name__ == "__main__":
    unittest.main()
