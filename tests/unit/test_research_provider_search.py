from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as app_module
from services.data_platform import DataPlatformStore, InstrumentRegistry


class ResearchProviderSearchTest(unittest.TestCase):
    def test_polymarket_search_returns_both_outcome_tokens(self) -> None:
        market = {
            "question": "Will BTC close above 100k?",
            "condition_id": "0xcondition",
            "yes_token": "yes-token",
            "no_token": "no-token",
            "active": True,
            "closed": False,
            "category": "Crypto",
        }
        with patch.object(app_module, "search_markets", return_value=[market]):
            response = app_module.app.test_client().get(
                "/api/research/instruments/search?provider=POLYMARKET&market=BINARY&category=polymarket&q=BTC"
            )
        self.assertEqual(200, response.status_code)
        rows = response.get_json()["data"]
        self.assertEqual(2, len(rows))
        self.assertEqual("polymarket_binary:POLYMARKET:yes-token", rows[0]["instrument_id"])
        self.assertEqual("polymarket_binary:POLYMARKET:no-token", rows[1]["instrument_id"])
        self.assertEqual("ACTIVE", rows[0]["status"])

    def test_discovered_binance_instrument_is_registered_before_universe_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DataPlatformStore(Path(temp_dir) / "metadata.db")
            client = app_module.app.test_client()
            discovered = {
                "instrument_id": "crypto_spot:binance:ETHFIUSDT",
                "asset_class": "crypto_spot",
                "venue": "binance",
                "symbol": "ETHFIUSDT",
                "display_symbol": "ETHFI/USDT",
                "display_name": "ETHFI / USDT",
                "market_kind": "spot",
                "status": "TRADING",
                "base_asset": "ETHFI",
                "quote_asset": "USDT",
            }
            with patch.object(app_module, "get_default_store", return_value=store):
                registered_response = client.post(
                    "/api/research/instruments/register",
                    json={"provider": "BINANCE", "market": "SPOT", "instrument": discovered},
                )
                self.assertEqual(201, registered_response.status_code, registered_response.get_json())
                registered = registered_response.get_json()["data"]
                self.assertEqual("crypto_spot:BINANCE:ETHFIUSDT", registered["instrument_id"])
                self.assertIsNotNone(InstrumentRegistry(store).get(registered["instrument_id"]))

                preview = client.post(
                    "/api/library/universes/preview",
                    json={
                        "definition": {
                            "name": "ETHFI",
                            "type": "instrument_set",
                            "members": [registered["instrument_id"]],
                        }
                    },
                )
            self.assertEqual(200, preview.status_code, preview.get_json())
            self.assertEqual(["crypto_spot:BINANCE:ETHFIUSDT"], preview.get_json()["data"]["instrument_ids"])

    def test_fred_exact_series_id_is_exposed_as_a_definition_result(self) -> None:
        response = app_module.app.test_client().get(
            "/api/research/instruments/search?provider=FRED&market=MACRO&category=fred&q=DGS10"
        )
        self.assertEqual(200, response.status_code)
        row = response.get_json()["data"][0]
        self.assertEqual("macro:FRED:DGS10", row["instrument_id"])
        self.assertEqual("DEFINITION_ONLY", row["status"])


if __name__ == "__main__":
    unittest.main()
