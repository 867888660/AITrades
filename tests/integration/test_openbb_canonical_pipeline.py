from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from services.data_platform import DataPlatformStore, FrozenManifestData, OpenBBEquityHistoryAdapter


class StubOpenBBService:
    def fetch_equity_historical(self, payload):
        day = date.today() - timedelta(days=10)
        return {
            "upstream_provider": "yfinance",
            "results": [
                {"date": day.isoformat(), "open": 100, "high": 103, "low": 99, "close": 102, "volume": 1000},
                {"date": (day + timedelta(days=1)).isoformat(), "open": 102, "high": 104, "low": 101, "close": 103, "volume": 1200},
            ],
            "warnings": [],
        }


class OpenBBCanonicalPipelineTest(unittest.TestCase):
    def test_openbb_daily_bars_commit_and_freeze(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = DataPlatformStore(root / "metadata.db")
            adapter = OpenBBEquityHistoryAdapter(
                {"openbb_settings": {"enabled": True}},
                output_root=root / "canonical",
                store=store,
                provider_service=StubOpenBBService(),
            )
            exported = adapter.export({
                "symbol": "AAPL",
                "venue": "XNAS",
                "currency": "USD",
                "adjustment": "unadjusted",
            })
            self.assertEqual(exported["gateway"], "openbb")
            self.assertEqual(exported["upstream_provider"], "yfinance")
            self.assertEqual(exported["row_count"], 2)
            self.assertEqual(exported["adjustment"], "splits_only")
            self.assertIn("splits_only", exported["dataset_id"])
            self.assertEqual(exported["catalog"].adjustment, "SPLITS_ONLY")
            frozen = FrozenManifestData(store, exported["manifest"].manifest_id)
            rows = frozen.read_rows()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["source"], "OPENBB/YFINANCE")
            self.assertGreaterEqual(rows[0]["available_time"], rows[0]["bar_end_time"])

    def test_total_return_adjustment_is_preserved_in_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = DataPlatformStore(root / "metadata.db")
            adapter = OpenBBEquityHistoryAdapter(
                {"openbb_settings": {"enabled": True}},
                output_root=root / "canonical",
                store=store,
                provider_service=StubOpenBBService(),
            )
            exported = adapter.export({
                "symbol": "MSFT",
                "venue": "XNAS",
                "currency": "USD",
                "adjustment": "SPLITS_AND_DIVIDENDS",
            })
            self.assertEqual(exported["adjustment"], "splits_and_dividends")
            self.assertEqual(exported["catalog"].adjustment, "SPLITS_AND_DIVIDENDS")


if __name__ == "__main__":
    unittest.main()
