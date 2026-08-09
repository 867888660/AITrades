from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.data_platform import ResearchDataCapabilityService


class ResearchDataCapabilityServiceTest(unittest.TestCase):
    def test_surfaces_connectors_without_claiming_discovery_is_historical(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.object(
            ResearchDataCapabilityService, "_tcp_online", return_value=False
        ):
            service = ResearchDataCapabilityService({
                "active_finnhub_api_key": "configured",
                "openbb_settings": {
                    "enabled": True, "base_url": "http://127.0.0.1:6901",
                    "allowed_providers": ["yfinance", "fred"],
                },
            }, base_dir=Path(temp))
            result = service.describe()
        providers = {item["id"]: item for item in result["providers"]}
        self.assertIn("BINANCE", providers)
        self.assertIn("YFINANCE", providers)
        self.assertIn("FRED", providers)
        self.assertIn("FINNHUB", providers)
        self.assertIn("COINGECKO", providers)
        self.assertIn("POLYMARKET", providers)
        self.assertFalse(providers["FINNHUB"]["historical"])
        self.assertFalse(providers["YFINANCE"]["online"])
        self.assertTrue(providers["POLYMARKET"]["historical"])
        self.assertTrue(providers["POLYMARKET"]["markets"][0]["prepare_supported"])
        self.assertTrue(service.can_prepare(
            "polymarket_binary:POLYMARKET:12345", "price_history", "1h"
        ))

    def test_installed_online_yfinance_prepares_equity_daily_bars(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            site_packages = Path(temp) / ".openbb-venv" / "Lib" / "site-packages"
            (site_packages / "openbb_yfinance-1.6.3.dist-info").mkdir(parents=True)
            with patch.object(ResearchDataCapabilityService, "_tcp_online", return_value=True):
                service = ResearchDataCapabilityService({
                    "openbb_settings": {
                        "enabled": True,
                        "base_url": "http://127.0.0.1:6901",
                        "allowed_providers": ["yfinance"],
                    },
                }, base_dir=Path(temp))
                providers = {item["id"]: item for item in service.describe()["providers"]}
                self.assertTrue(providers["YFINANCE"]["online"])
                self.assertTrue(providers["YFINANCE"]["markets"][0]["prepare_supported"])
                self.assertEqual(["1m", "5m", "1d"], providers["YFINANCE"]["raw_query_frequencies"])
                session = providers["YFINANCE"]["research_sessions"][0]
                self.assertEqual("PREMARKET_0400_0930_ET", session["id"])
                self.assertTrue(session["raw_query_supported"])
                self.assertFalse(session["canonical_prepare_supported"])
                self.assertTrue(service.can_prepare("equity:XNAS:AAPL", "bars", "1d"))
                self.assertFalse(service.can_prepare("equity:XNAS:AAPL", "bars", "5m"))


if __name__ == "__main__":
    unittest.main()
