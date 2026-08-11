from __future__ import annotations

import csv
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from services.data_platform.catalog_service import DatasetCatalogService
from services.data_platform.crsp_bulk_import import CrspBulkImportService, SOURCE_COLUMNS
from services.data_platform.data_client import FrozenManifestData
from services.data_platform.store import DataPlatformStore


class CrspBulkImportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = DataPlatformStore(self.root / "metadata.sqlite")
        csv_path = self.root / "fixture.csv"
        rows = []
        for permno, day, price in (
            (10001, "20240102", "10"),
            (10001, "20240103", "11"),
            (10002, "20240102", "20"),
            (10002, "20240103", "21"),
        ):
            row = {name: "" for name in SOURCE_COLUMNS}
            row.update({
                "PERMNO": str(permno), "PERMCO": str(permno + 1000),
                "SecInfoStartDt": "2024-01-01", "SecurityBegDt": "2024-01-01",
                "CUSIP9": f"{permno:09d}", "PrimaryExch": "N",
                "SecurityNm": f"Fixture {permno}", "IssuerNm": f"Issuer {permno}",
                "SecurityType": "EQTY", "ShareType": "COM", "SecurityActiveFlg": "Y",
                "Ticker": f"T{permno}", "YYYYMMDD": day,
                "DlyPrc": price, "DlyOpen": price, "DlyHigh": price, "DlyLow": price,
                "DlyVol": "100", "DlyCap": "1000", "ShrOut": "100",
            })
            if permno == 10001 and day == "20240103":
                row.update({"DisExDt": "2024-01-03", "DisType": "CD", "DisDivAmt": "0.25"})
            rows.append(row)
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=SOURCE_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        self.archive = self.root / "fixture.zip"
        with zipfile.ZipFile(self.archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(csv_path, "fixture.csv")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_resume_after_publish_failure_and_atomically_ready(self) -> None:
        service = CrspBulkImportService(self.store, canonical_root=self.root / "canonical")
        job = service.create(source_path=self.archive, dataset_prefix="fixture:crsp:full")
        with mock.patch.object(
            DatasetCatalogService,
            "commit_manifests_atomically",
            side_effect=RuntimeError("injected publish failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "injected publish failure"):
                service.run(job["job_id"], chunk_rows=10_000)
        failed = service.get(job["job_id"])
        self.assertEqual("FAILED", failed["status"])
        self.assertEqual(4, failed["rows_processed"])
        self.assertEqual([], DatasetCatalogService(self.store).list_catalog(status="READY"))

        service.resume(job["job_id"])
        ready = service.run(job["job_id"], chunk_rows=10_000)
        self.assertEqual("READY", ready["status"])
        self.assertEqual(4, len(ready["manifests"]))
        catalog = DatasetCatalogService(self.store).list_catalog(status="READY")
        self.assertEqual(4, len(catalog))
        for entry in catalog:
            self.assertEqual(
                "PASS",
                FrozenManifestData(self.store, entry.latest_manifest_id).verify()["status"],
            )


if __name__ == "__main__":
    unittest.main()
