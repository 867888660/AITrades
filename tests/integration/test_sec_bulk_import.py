from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import pyarrow.parquet as pq

from services.data_platform.catalog_service import DatasetCatalogService
from services.data_platform.sec_bulk_import import SecBulkImportService
from services.data_platform.store import DataPlatformStore, utc_now


class SecBulkImportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = DataPlatformStore(self.root / "metadata.sqlite")
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            for security_id, permno, valid_from, valid_to, ticker in (
                ("crsp:permno:10001", 10001, "2020-01-01", "", "ACME"),
                ("crsp:permno:10002", 10002, "1990-01-01", "2019-12-31", "OLD"),
            ):
                conn.execute(
                    """INSERT INTO equity_security_master(
                           security_id,permno,permco,primary_exchange,valid_from,valid_to,
                           source,created_at,updated_at
                       ) VALUES (?,?,77,'N',?,?,'CRSP/CIZ',?,?)""",
                    (security_id, permno, valid_from, valid_to, now, now),
                )
                conn.execute(
                    """INSERT INTO equity_security_aliases(
                           security_id,alias_type,alias_value,valid_from,valid_to,source,created_at
                       ) VALUES (?,'TICKER',?,?,?,'CRSP/CIZ',?)""",
                    (security_id, ticker, valid_from, valid_to, now),
                )
        DatasetCatalogService(self.store).upsert_catalog({
            "dataset_id": "crsp:ciz:security_master",
            "instrument_id": "equity:CRSP:ALL",
            "data_type": "security_master",
            "source": "CRSP/CIZ",
            "status": "READY",
            "quality_status": "PASS",
            "schema_version": "security_master.v1",
            "storage_path": str(self.root / "crsp"),
            "start_time": "1990-01-01T00:00:00+00:00",
            "end_time": "2025-12-31T00:00:00+00:00",
        })

        self.tickers = self.root / "company_tickers_exchange.json"
        self.tickers.write_text(json.dumps({
            "fields": ["cik", "name", "ticker", "exchange"],
            "data": [[1, "Acme Corp", "ACME", "NYSE"]],
        }), encoding="utf-8")
        submission = {
            "cik": "0000000001",
            "tickers": ["ACME"],
            "exchanges": ["NYSE"],
            "filings": {"recent": {
                "accessionNumber": ["0001-19-000001", "0001-20-bad-accept", "0001-25-000001"],
                "acceptanceDateTime": ["2019-02-02T12:00:00.000Z", "2019-02-02T12:00:00.000Z", "2025-02-02T12:00:00.000Z"],
            }},
        }
        self.submissions = self.root / "submissions.zip"
        with zipfile.ZipFile(self.submissions, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("bulk/CIK0000000001.json", json.dumps(submission))
        company = {
            "cik": 1,
            "entityName": "Acme Corp",
            "facts": {"us-gaap": {"Assets": {
                "label": "Assets",
                "units": {"USD": [
                    {"end": "2018-12-31", "val": 10, "accn": "0001-19-000001", "filed": "2019-02-02", "form": "10-K", "fy": 2018, "fp": "FY"},
                    {"end": "2024-12-31", "val": 20, "accn": "0001-25-000001", "filed": "2025-02-02", "form": "10-K", "fy": "2024.0", "fp": "FY"},
                    {"end": "2019-12-31", "val": 15, "accn": "0001-20-bad-accept", "filed": "2020-02-02", "form": "10-K", "fy": 2019, "fp": "FY"},
                    {"end": "6016-06-30", "val": 999, "accn": "0001-19-bad-date", "filed": "2019-02-02", "form": "10-K", "fy": 6016, "fp": "FY"},
                ]},
            }}},
        }
        self.companyfacts = self.root / "companyfacts.zip"
        with zipfile.ZipFile(self.companyfacts, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("xbrl/CIK0000000001.json", json.dumps(company))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_authoritative_mapping_propagates_permco_and_publishes_pit(self) -> None:
        service = SecBulkImportService(self.store, canonical_root=self.root / "canonical")
        job = service.create(dataset_id="fixture:fundamentals_pit", idempotency_key="fixture-sec")
        sources = {
            "companyfacts.zip": self.companyfacts,
            "submissions.zip": self.submissions,
            "company_tickers_exchange.json": self.tickers,
        }
        with mock.patch.object(service, "_download", side_effect=lambda _j, _w, _u, target: sources[target.name]):
            ready = service.run(job["job_id"], target_rows=25_000)

        self.assertEqual("READY", ready["status"])
        self.assertEqual(1, ready["company_count"])
        self.assertEqual(1, ready["mapped_company_count"])
        self.assertEqual(3, ready["row_count"])
        self.assertEqual("PASS", ready["quality_report"]["status"])
        self.assertEqual(1, ready["quality_report"]["counters"]["period_after_available"])
        self.assertEqual(1, ready["quality_report"]["counters"]["invalid_acceptance_fallbacks"])
        self.assertEqual(2, ready["mapping_report"]["mapped_crsp_securities"])
        self.assertEqual(0, ready["mapping_report"]["unmapped_crsp_securities"])

        with self.store.connection() as conn:
            links = conn.execute(
                "SELECT permno,cik,valid_from,valid_to FROM sec_security_links ORDER BY permno"
            ).fetchall()
            partition = conn.execute(
                "SELECT file_uri FROM sec_bulk_import_partitions WHERE job_id=?",
                (job["job_id"],),
            ).fetchone()
        self.assertEqual([10001, 10002], [row["permno"] for row in links])
        rows = pq.ParquetFile(Path(partition["file_uri"])).read().to_pylist()
        self.assertEqual([10002, 10001, 10001], [row["permno"] for row in rows])
        self.assertEqual([2018, 2019, 2024], [row["fiscal_year"] for row in rows])
        self.assertLess(rows[0]["event_time"], rows[0]["available_time"])
        catalog = DatasetCatalogService(self.store).get_catalog("fixture:fundamentals_pit")
        self.assertEqual("READY", catalog.status)
        self.assertEqual("fundamentals_pit.v1", catalog.schema_version)

        recovery = service.create(
            dataset_id="fixture:fundamentals_recovery",
            idempotency_key="fixture-source-reuse",
        )
        reused = service.reuse_verified_sources(
            recovery["job_id"], source_job_id=job["job_id"]
        )
        self.assertEqual("QUEUED", reused["status"])
        self.assertEqual(job["job_id"], reused["checkpoint"]["verified_source_reuse"]["source_job_id"])
        self.assertTrue(
            Path(reused["source_files"]["companyfacts"]["path"]).samefile(self.companyfacts)
        )

    def test_resume_after_atomic_publish_failure(self) -> None:
        service = SecBulkImportService(self.store, canonical_root=self.root / "canonical")
        job = service.create(dataset_id="fixture:fundamentals_resume", idempotency_key="fixture-resume")
        sources = {
            "companyfacts.zip": self.companyfacts,
            "submissions.zip": self.submissions,
            "company_tickers_exchange.json": self.tickers,
        }
        download = mock.patch.object(
            service,
            "_download",
            side_effect=lambda _j, _w, _u, target: sources[target.name],
        )
        with download, mock.patch.object(
            DatasetCatalogService,
            "commit_manifests_atomically",
            side_effect=RuntimeError("injected SEC publish failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "injected SEC publish failure"):
                service.run(job["job_id"], target_rows=25_000)
        failed = service.get(job["job_id"])
        self.assertEqual("FAILED", failed["status"])
        self.assertEqual(3, failed["row_count"])
        self.assertIsNone(DatasetCatalogService(self.store).get_catalog("fixture:fundamentals_resume"))

        service.resume(job["job_id"])
        with mock.patch.object(
            service,
            "_download",
            side_effect=lambda _j, _w, _u, target: sources[target.name],
        ):
            ready = service.run(job["job_id"], target_rows=25_000)
        self.assertEqual("READY", ready["status"])
        self.assertEqual(3, ready["row_count"])
        self.assertEqual(1, ready["manifest"]["partition_count"])


if __name__ == "__main__":
    unittest.main()
