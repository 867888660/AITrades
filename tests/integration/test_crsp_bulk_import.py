from __future__ import annotations

import csv
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from integrations.qlib import Alpha158ImportService
from services.data_platform.catalog_service import DatasetCatalogService
from services.data_platform.crsp_bulk_import import CrspBulkImportService, SOURCE_COLUMNS
from services.data_platform.data_client import FrozenManifestData
from services.data_platform.manifest_resolver import DeterministicManifestResolver
from services.data_platform.requirement_compiler import RequirementCompiler
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
        bars_entry = next(item for item in catalog if item.data_type == "bars")
        selected = FrozenManifestData(
            self.store, bars_entry.latest_manifest_id
        ).read_bars_by_instrument(
            start_time="2024-01-03T00:00:00+00:00",
            end_time="2024-01-03T23:59:59+00:00",
            instrument_ids=["equity:CRSP:10001"],
        )
        self.assertEqual(["equity:CRSP:10001"], list(selected))
        self.assertEqual(1, len(selected["equity:CRSP:10001"]))
        requirement_set = RequirementCompiler(self.store).compile(
            project_id="crsp_daily_boundary",
            manual_requirements=[{"id": "crsp_close", "fields": ["close"]}],
            context={
                "instrument_ids": ["equity:CRSP:10001", "equity:CRSP:10002"],
                "data_type": "bars",
                "frequency": "1d",
                "history_start": "2024-01-02T00:00:00+00:00",
                "history_end": "2024-01-03T23:59:59+00:00",
                "adjustment": "CRSP_FIELDS",
                "time_semantics": "SOURCE_AVAILABLE_TIME",
                "point_in_time_policy": "AS_OF",
            },
        )
        self.assertEqual(
            "SATISFIED",
            RequirementCompiler(self.store).coverage(requirement_set.requirement_set_id)["status"],
        )
        with mock.patch.object(
            FrozenManifestData,
            "verify",
            return_value={"status": "PASS"},
        ) as verify:
            resolution = DeterministicManifestResolver(self.store).resolve(
                requirement_set.requirement_set_id,
                verify_physical=True,
            )
        self.assertTrue(resolution.ready, resolution.to_dict())
        self.assertEqual(2, len(resolution.bindings))
        # The resumed import leaves two eligible READY bars Manifests.  Each
        # must be checked once, not once per requested instrument (four calls).
        self.assertEqual(2, verify.call_count)
        importer = Alpha158ImportService(self.store, output_root=self.root / "factor-cache")
        with self.assertRaisesRegex(ValueError, "explicit row-level instrument_ids"):
            importer._load_inputs([bars_entry.latest_manifest_id])
        with self.assertRaisesRegex(ValueError, "has 2 daily bars"):
            importer._load_inputs(
                [bars_entry.latest_manifest_id],
                instrument_ids=["equity:CRSP:10001"],
            )


if __name__ == "__main__":
    unittest.main()
