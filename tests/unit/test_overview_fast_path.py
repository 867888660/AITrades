from __future__ import annotations

import unittest
from unittest.mock import patch

from services import polymarket_service


class OverviewFastPathTests(unittest.TestCase):
    def test_local_market_resolution_skips_gamma_lookup(self) -> None:
        empty_index = {
            "by_condition_id": {},
            "by_token": {},
            "by_slug": {},
            "by_question": {},
        }
        with (
            patch.object(polymarket_service, "_local_market_snapshot", return_value=[]),
            patch.object(polymarket_service, "_load_dictionary_market_index", return_value=empty_index),
            patch.object(polymarket_service, "_known_markets") as remote_markets,
            patch.object(polymarket_service, "_gamma_lookup_market_stub") as gamma_lookup,
        ):
            result = polymarket_service.resolve_market_selection(
                condition_id="missing-condition",
                limit=1,
                allow_remote=False,
            )

        self.assertFalse(result["ok"])
        remote_markets.assert_not_called()
        gamma_lookup.assert_not_called()

    def test_missing_ws_token_does_not_fall_back_to_full_snapshot_scan(self) -> None:
        with (
            patch.object(polymarket_service, "_load_ws_snapshots_for_tokens", return_value={}) as targeted,
            patch.object(polymarket_service, "_load_ws_snapshot_map") as full_scan,
        ):
            result = polymarket_service._select_strategy_ws_snapshot("yes-token", "no-token")

        self.assertIsNone(result)
        targeted.assert_called_once_with(["yes-token", "no-token"])
        full_scan.assert_not_called()

    def test_local_market_snapshot_accepts_stale_data_without_remote_crawl(self) -> None:
        market = {"condition_id": "stale-condition"}
        with (
            patch.dict(polymarket_service._market_cache, {"ts": 0.0, "data": []}, clear=True),
            patch.object(polymarket_service, "_read_market_snapshot", return_value=[market]) as read_snapshot,
            patch.object(polymarket_service, "fetch_active_markets") as remote_markets,
        ):
            result = polymarket_service._local_market_snapshot()

            self.assertEqual([market], result)
            self.assertEqual([market], polymarket_service._market_cache["data"])
            self.assertEqual(0.0, polymarket_service._market_cache["ts"])

        read_snapshot.assert_called_once_with()
        remote_markets.assert_not_called()

    def test_overview_uses_snapshots_without_remote_or_strategy_scan(self) -> None:
        market = {"condition_id": "condition", "category": "Macro"}
        with (
            patch.dict(polymarket_service._market_cache, {"ts": 0.0, "data": []}, clear=True),
            patch.dict(polymarket_service._wallet_positions_cache, {}, clear=True),
            patch.object(
                polymarket_service,
                "_read_market_snapshot_payload",
                return_value=([market], 600.0),
            ),
            patch.object(polymarket_service, "fetch_active_markets") as remote_markets,
            patch.object(polymarket_service, "fetch_wallet_positions") as remote_holdings,
            patch.object(polymarket_service, "_load_strategy_monitoring_rows") as strategies,
            patch.object(polymarket_service, "get_default_wallets", return_value=[]),
        ):
            result = polymarket_service.get_overview()

        self.assertEqual(1, result["market_count"])
        self.assertEqual("degraded", result["sources"]["markets_api"]["status"])
        self.assertEqual("pending", result["sources"]["holdings_api"]["status"])
        self.assertEqual("pending", result["sources"]["strategy_profit"]["status"])
        remote_markets.assert_not_called()
        remote_holdings.assert_not_called()
        strategies.assert_not_called()


if __name__ == "__main__":
    unittest.main()
