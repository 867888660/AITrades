from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from services.data_platform import BinanceHistoryAdapter, DataPlatformStore, FrozenManifestData


def create_history_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE binance_klines (
                symbol TEXT, interval TEXT, open_time_ms INTEGER, open_time_utc TEXT,
                open REAL, high REAL, low REAL, close REAL, volume REAL,
                close_time_ms INTEGER, quote_volume REAL, trades INTEGER,
                fetched_at_utc TEXT
            )
            """
        )
        for index in range(3):
            open_ms = 1_735_689_600_000 + index * 3_600_000
            conn.execute(
                "INSERT INTO binance_klines VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "AAAUSDT", "1h", open_ms, f"2025-01-01T0{index}:00:00+00:00",
                    100 + index, 102 + index, 99 + index, 101 + index, 10,
                    open_ms + 3_600_000 - 1, 1000, 10, "2025-01-02T00:00:00+00:00",
                ),
            )
        conn.commit()
    finally:
        conn.close()


class ManifestImmutabilityTest(unittest.TestCase):
    def test_changed_source_creates_v2_without_mutating_v1(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            history = root / "history.db"
            create_history_db(history)
            conn = sqlite3.connect(history)
            try:
                conn.execute(
                    "INSERT INTO binance_klines VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "AAAUSDT", "1h", 4_070_908_800_000, "2099-01-01T00:00:00+00:00",
                        200, 202, 199, 201, 10, 4_070_912_399_999, 2000, 10,
                        "2025-01-02T00:00:00+00:00",
                    ),
                )
                conn.commit()
            finally:
                conn.close()
            store = DataPlatformStore(root / "metadata.db")
            adapter = BinanceHistoryAdapter(history_db_path=history, output_root=root / "canonical", store=store)
            first = adapter.export(symbol="AAAUSDT", interval="1h")
            self.assertEqual(1, first["excluded_incomplete_rows"])
            first_frozen = FrozenManifestData(store, first["manifest"].manifest_id)
            first_close = first_frozen.read_rows()[0]["close"]
            projected = first_frozen.read_rows(
                columns=["close"],
                as_of="2025-01-01T01:59:59.999000+00:00",
            )
            self.assertEqual(2, len(projected))
            self.assertEqual({"close"}, set(projected[0]))
            first_path = Path(first["manifest"].partitions[0].file_uri)

            conn = sqlite3.connect(history)
            try:
                conn.execute(
                    "UPDATE binance_klines SET close = 101.5, high = 102.5 WHERE open_time_ms = ?",
                    (1_735_689_600_000,),
                )
                conn.commit()
            finally:
                conn.close()
            second = adapter.export(symbol="AAAUSDT", interval="1h")
            second_frozen = FrozenManifestData(store, second["manifest"].manifest_id)
            second_path = Path(second["manifest"].partitions[0].file_uri)

            self.assertEqual(1, first["manifest"].version)
            self.assertEqual(2, second["manifest"].version)
            self.assertNotEqual(first["manifest"].manifest_id, second["manifest"].manifest_id)
            self.assertNotEqual(first_path, second_path)
            self.assertTrue(first_path.exists())
            self.assertTrue(second_path.exists())
            self.assertEqual(first_close, first_frozen.read_rows()[0]["close"])
            self.assertNotEqual(first_close, second_frozen.read_rows()[0]["close"])

            with first_path.open("ab") as handle:
                handle.write(b"corruption")
            with self.assertRaisesRegex(ValueError, "size mismatch|checksum mismatch"):
                first_frozen.read_rows()


if __name__ == "__main__":
    unittest.main()
