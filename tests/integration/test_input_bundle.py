from __future__ import annotations
import tempfile, unittest
from pathlib import Path
from services.data_platform import CanonicalBarsCommitter, DataPlatformStore, ResearchInputBundleService

class InputBundleTest(unittest.TestCase):
    def test_bundle_reuses_ready_manifest_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            store = DataPlatformStore(Path(temp) / "meta.db")
            row = {"instrument_id":"crypto_spot:BINANCE:BTCUSDT","frequency":"1h",
                   "bar_start_time":"2026-01-01T00:00:00+00:00","bar_end_time":"2026-01-01T01:00:00+00:00",
                   "available_time":"2026-01-01T01:00:00+00:00","ingested_at":"2026-01-02T00:00:00+00:00",
                   "open":1.0,"high":1.1,"low":0.9,"close":1.0,"volume":1.0,"turnover":1.0,
                   "trade_count":1,"bar_status":"COMPLETE","source":"TEST","source_version":"1","quality_status":"PASS"}
            committed = CanonicalBarsCommitter(store, Path(temp)/"data").commit(dataset_id="test:btc", instrument_id=row["instrument_id"],
                asset_class="crypto_spot", venue="BINANCE", frequency="1h", source="TEST", source_version="1", rows=[row])
            service = ResearchInputBundleService(store, Path(temp)/"bundles")
            first = service.create(project_id="p1", logical_name="inputs", manifest_ids=[committed["manifest"].manifest_id])
            second = service.create(project_id="p1", logical_name="inputs", manifest_ids=[committed["manifest"].manifest_id])
            self.assertEqual(first.artifact_id, second.artifact_id)
            self.assertEqual("READY", service.verify(first.artifact_id)["status"])
            Path(first.content_uri).write_text('{"tampered":true}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                service.verify(first.artifact_id)

if __name__ == "__main__": unittest.main()
