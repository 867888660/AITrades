from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.data_platform import DataPlatformStore, PolymarketHistoryPreparer


class PolymarketHistoryPreparerTest(unittest.TestCase):
    def test_prepares_outcome_prices_as_a_research_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = DataPlatformStore(root / "metadata.db")
            service = PolymarketHistoryPreparer(store, output_root=root / "canonical")
            rows = [
                {"event_time": "2026-07-01T00:00:00+00:00", "available_time": "2026-07-01T00:00:00+00:00", "price": 0.40, "condition_id": "c1", "token_id": "t1"},
                {"event_time": "2026-07-01T01:00:00+00:00", "available_time": "2026-07-01T01:00:00+00:00", "price": 0.45, "condition_id": "c1", "token_id": "t1"},
            ]
            with patch("services.data_platform.polymarket_history.download_polymarket_price_history", return_value={"ok": True}) as download, patch.object(
                service, "_read_rows", return_value=rows
            ):
                result = service.prepare({
                    "instrument_id": "polymarket_binary:POLYMARKET:t1",
                    "interval": "1h",
                    "start_time": "2026-07-01T00:00:00+00:00",
                    "end_time": "2026-07-01T01:00:00+00:00",
                })
            self.assertEqual(2, result["row_count"])
            self.assertEqual("READY", result["catalog"]["status"])
            self.assertEqual("polymarket_binary:POLYMARKET:t1", result["catalog"]["instrument_id"])
            self.assertEqual(["price"], list(result["catalog"]["fields"]))
            self.assertFalse(download.call_args.args[0]["latest_available"])


if __name__ == "__main__":
    unittest.main()
