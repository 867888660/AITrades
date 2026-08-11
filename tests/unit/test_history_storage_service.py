from __future__ import annotations

import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from services.history_storage_service import (
    HistoryStorageService,
    MARKER_NAME,
    get_history_storage_job,
    resolve_managed_history_path,
)


def _sqlite(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE sample(value TEXT NOT NULL)")
        conn.execute("INSERT INTO sample(value) VALUES (?)", (value,))
        conn.commit()
    finally:
        conn.close()


class HistoryStorageServiceTests(unittest.TestCase):
    def test_archive_coverage_distinguishes_raw_inventory_from_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            base = temp / "repo"
            target = temp / "managed-history"
            inventory_path = target / "sources" / "01-archive" / ".datatube" / "inventory.json"
            inventory_path.parent.mkdir(parents=True)
            (target / MARKER_NAME).write_text(json.dumps({
                "schema_version": "datatube_history_root.v1",
                "state": "READY",
            }), encoding="utf-8")
            inventory_path.write_text(json.dumps({
                "schema_version": "us_equity_archive_inventory.v1",
                "generated_at": "2026-08-10T00:00:00+00:00",
                "summary": {
                    "archive_bytes": 246767016517,
                    "archive_count": 271,
                    "archive_failures": 0,
                    "daily_stock_archives": 200,
                    "daily_stock_entries": 5944,
                    "daily_stock_start": "2002-05-01",
                    "daily_stock_end": "2025-12-31",
                    "daily_option_archives": 200,
                    "daily_option_entries": 5944,
                    "daily_option_start": "2002-05-01",
                    "daily_option_end": "2025-12-31",
                    "quarterly_option_archives": 2,
                    "quarterly_option_entries": 100,
                    "crsp_outer_archives": 1,
                },
                "archives": [
                    {"path": "firstrate/2010_q1_option_chain.zip", "quarterly_option_entries": 50},
                    {"path": "firstrate/2026_q3_option_chain.zip", "quarterly_option_entries": 50},
                    {
                        "path": "crsp.zip",
                        "nested_zip_entries": [{"name": "CRSP daily 19251231-20251231_csv.zip"}],
                    },
                ],
            }), encoding="utf-8")

            data = HistoryStorageService(
                base_dir=base,
                settings={"history_data_root": str(target)},
            ).archive_coverage()

            self.assertEqual("READY", data["state"])
            self.assertEqual(4, len(data["collections"]))
            stocks = next(item for item in data["collections"] if item["id"] == "us_equity_daily_snapshots")
            self.assertEqual("2002-05-01", stocks["start"])
            self.assertEqual("2025-12-31", stocks["end"])
            self.assertEqual(5944, stocks["metrics"][0]["value"])
            self.assertEqual("RAW_ARCHIVE", stocks["status"])
            crsp = next(item for item in data["collections"] if item["id"] == "crsp_ciz_daily")
            self.assertEqual("1925-12-31", crsp["start"])
            self.assertEqual("2025-12-31", crsp["end"])

    def test_normalization_copies_verifies_and_activates_managed_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            base = temp / "repo"
            target = temp / "managed-history"
            source_archive = temp / "downloaded-history"
            metrics = base / "strategy_metrics_dbs"
            _sqlite(base / "Data" / "history_workspace.db", "history")
            _sqlite(base / "storage" / "metadata" / "data_platform.db", "metadata")
            (base / "storage" / "canonical").mkdir(parents=True)
            (base / "storage" / "canonical" / "bars.parquet").write_bytes(b"parquet-placeholder")
            _sqlite(metrics / "strategy.db", "strategy")
            source_archive.mkdir(parents=True)
            (source_archive / "daily.zip").write_bytes(b"zip-placeholder")
            saved: dict = {}
            service = HistoryStorageService(
                base_dir=base,
                settings={"strategy_metrics_db_dir": str(metrics)},
                settings_saver=lambda payload: saved.update(payload),
            )
            with patch.object(service, "_discover_external_catalog_roots", return_value=[]):
                plan = service.plan(target, [source_archive])
                self.assertTrue(plan["enough_space"])
                self.assertEqual(4, len(plan["entries"]))
                job = service.start(target, [source_archive])

            deadline = time.time() + 10
            current = job
            while current["status"] == "RUNNING" and time.time() < deadline:
                time.sleep(0.05)
                current = get_history_storage_job(job["job_id"]) or current
            self.assertEqual("SUCCEEDED", current["status"], current.get("error"))
            self.assertTrue((target / MARKER_NAME).is_file())
            self.assertTrue((target / "workspace" / "history_workspace.db").is_file())
            self.assertTrue((target / "platform" / "metadata" / "data_platform.db").is_file())
            self.assertTrue((target / "platform" / "canonical" / "bars.parquet").is_file())
            self.assertTrue((target / "strategy-history" / "strategy.db").is_file())
            copied_archive = next((target / "sources").rglob("daily.zip"))
            self.assertEqual(b"zip-placeholder", copied_archive.read_bytes())
            self.assertTrue(source_archive.is_dir(), "source data must be preserved")
            self.assertEqual(target.resolve(strict=False), Path(saved["history_data_root"]).resolve(strict=False))
            conn = sqlite3.connect(target / "workspace" / "history_workspace.db")
            try:
                self.assertEqual("history", conn.execute("SELECT value FROM sample").fetchone()[0])
            finally:
                conn.close()

            resolved = resolve_managed_history_path(
                "storage/canonical/bars.parquet",
                base_dir=base,
                settings={"history_data_root": str(target)},
            )
            self.assertEqual(target / "platform" / "canonical" / "bars.parquet", resolved)
            marker = json.loads((target / MARKER_NAME).read_text(encoding="utf-8"))
            self.assertEqual("READY", marker["state"])

    def test_rejects_target_nested_inside_a_source_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            base = temp / "repo"
            source = temp / "source"
            base.mkdir()
            source.mkdir()
            service = HistoryStorageService(base_dir=base, settings={})
            with self.assertRaisesRegex(ValueError, "cannot be inside a source root"):
                service.plan(source / "managed", [source])


if __name__ == "__main__":
    unittest.main()
