from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from services import history_data_service
from services.data_platform import DataPlatformStore, FrozenManifestData
from services.data_platform.us_equity_archive import (
    DailySnapshotEquityImporter,
    scan_us_equity_archive,
)


class UsEquityArchiveIntegrationTest(unittest.TestCase):
    def _fixture(self, root: Path) -> Path:
        source = root / "downloads"
        snapshots = source / "daily-snapshots" / "2025"
        snapshots.mkdir(parents=True)
        with zipfile.ZipFile(snapshots / "2025-01.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "2025-01-02stocks.csv",
                "symbol,open,high,low,close,volume\nAAPL,100,104,99,103,1000\nMSFT,200,205,198,204,500\n",
            )
            archive.writestr(
                "2025-01-03stocks.csv",
                "symbol,open,high,low,close,volume\nAAPL,103,106,102,105,1200\nMSFT,204,207,201,202,600\n",
            )
            archive.writestr(
                "2025-01-03options.csv",
                "contract,underlying,expiration,type,strike,quote_date\nO:AAPL250117C00100000,AAPL,2025-01-17,call,100,2025-01-03\n",
            )
        return source

    def test_inventory_routes_stocks_and_options_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._fixture(Path(tmp))
            inventory = scan_us_equity_archive(source)
            self.assertEqual(inventory["summary"]["archive_count"], 1)
            self.assertEqual(inventory["summary"]["daily_stock_entries"], 2)
            self.assertEqual(inventory["summary"]["daily_option_entries"], 1)
            self.assertEqual(inventory["routing"]["daily_stocks"], "READY_FOR_BARS_V1_IMPORT")
            self.assertEqual(inventory["routing"]["daily_options"], "OPTION_CHAIN_EOD_SCHEMA_REQUIRED")

    def test_selected_symbol_import_stays_under_external_data_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._fixture(root)
            data_root = root / "external-data"
            store = DataPlatformStore(root / "metadata.db")
            importer = DailySnapshotEquityImporter(source, data_root, store=store)
            result = importer.import_symbols(
                ["AAPL"], venues={"AAPL": "XNAS"}
            )
            dataset = result["datasets"][0]
            self.assertEqual(dataset["row_count"], 2)
            self.assertEqual(dataset["instrument_id"], "equity:XNAS:AAPL")
            manifest_id = dataset["manifest"]["manifest_id"]
            frozen = FrozenManifestData(store, manifest_id)
            rows = frozen.read_rows()
            self.assertEqual([row["close"] for row in rows], [103.0, 105.0])
            self.assertEqual(frozen.verify()["status"], "PASS")
            for partition in frozen.manifest.partitions:
                path = Path(partition.file_uri)
                self.assertTrue(path.is_absolute())
                self.assertTrue(path.is_relative_to(data_root.resolve()))

            leg = {
                "source": "datatube_manifest",
                "manifest_id": manifest_id,
                "instrument_id": "equity:XNAS:AAPL",
                "symbol": "AAPL",
                "asset_class": "equity",
                "interval": "1d",
            }
            with patch("services.data_platform.get_default_store", return_value=store):
                coverage = history_data_service.get_datatube_manifest_coverage(
                    manifest_id, "equity:XNAS:AAPL"
                )
                history_rows = history_data_service._read_datatube_manifest_rows(
                    leg, None, None
                )
                compatibility = history_data_service._case_compatibility(
                    [leg],
                    {"signal_source": {"type": "LIBRARY_ALPHA"}, "strategy_code": ""},
                )
            self.assertEqual(coverage["status"], "ok")
            self.assertEqual(coverage["count"], 2)
            self.assertEqual([row["close"] for row in history_rows], [103.0, 105.0])
            self.assertNotEqual(compatibility["severity"], "error")

            repeated = importer.import_symbols(["AAPL"], venues={"AAPL": "XNAS"})
            self.assertEqual(
                repeated["datasets"][0]["manifest"]["manifest_id"], manifest_id
            )


if __name__ == "__main__":
    unittest.main()
