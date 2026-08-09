from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from services.data_platform import CanonicalBarsCommitter, DataPlatformStore, SourcePolicy, SourcePolicyService


class SourcePolicyTest(unittest.TestCase):
    def rows(self, instrument_id, closes, source):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        result = []
        for index, close in enumerate(closes):
            event = start + timedelta(days=index)
            available = event + timedelta(days=1)
            result.append({
                "instrument_id": instrument_id, "frequency": "1d",
                "bar_start_time": event.isoformat(), "bar_end_time": available.isoformat(),
                "available_time": available.isoformat(), "ingested_at": available.isoformat(),
                "open": close, "high": close + 1, "low": close - 1, "close": close,
                "volume": 100.0, "turnover": 0.0, "trade_count": 0,
                "bar_status": "COMPLETE", "source": source,
                "source_version": "test.v1", "quality_status": "PASS",
            })
        return result

    def test_fixed_and_compare_keep_source_manifests_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = DataPlatformStore(root / "metadata.db")
            committer = CanonicalBarsCommitter(store, root / "canonical")
            instrument_id = "equity:XNAS:AAPL"
            left = committer.commit(
                dataset_id="openbb:yfinance:aapl", instrument_id=instrument_id, asset_class="equity",
                venue="XNAS", frequency="1d", source="OPENBB/YFINANCE", source_version="test.v1",
                rows=self.rows(instrument_id, [100.0, 101.0], "OPENBB/YFINANCE"),
            )
            right = committer.commit(
                dataset_id="openbb:fmp:aapl", instrument_id=instrument_id, asset_class="equity",
                venue="XNAS", frequency="1d", source="OPENBB/FMP", source_version="test.v1",
                rows=self.rows(instrument_id, [100.0, 102.0], "OPENBB/FMP"),
            )
            left_id = left["manifest"].manifest_id
            right_id = right["manifest"].manifest_id
            service = SourcePolicyService(store)
            fixed = service.fixed(SourcePolicy.from_dict({"mode": "FIXED", "manifest_ids": [left_id]}))
            self.assertEqual(fixed["manifest"]["manifest_id"], left_id)
            comparison = service.compare(SourcePolicy.from_dict({"mode": "COMPARE", "manifest_ids": [left_id, right_id]}))
            self.assertEqual(comparison["shared_count"], 2)
            self.assertEqual(comparison["conflict_count"], 1)
            self.assertEqual(comparison["resolution"], "KEEP_BOTH")
            self.assertFalse(comparison["composite_created"])


if __name__ == "__main__":
    unittest.main()
