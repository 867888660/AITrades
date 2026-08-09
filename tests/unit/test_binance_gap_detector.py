from __future__ import annotations

import unittest
from datetime import datetime, timezone

from services.data_platform import BinanceGapDetector
from services.data_platform.binance_backfill import last_complete_open_ms


class BinanceGapDetectorTest(unittest.TestCase):
    def test_last_complete_bar_excludes_current_incomplete_bar(self) -> None:
        now = datetime(2026, 1, 1, 10, 37, tzinfo=timezone.utc)
        completed_open_ms = last_complete_open_ms("1h", now=now)
        self.assertEqual(
            int(datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc).timestamp() * 1000),
            completed_open_ms,
        )

    def test_detects_exact_contiguous_ranges_duplicates_and_misalignment(self) -> None:
        observed = [
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T01:00:00+00:00",
            "2026-01-01T01:00:00+00:00",
            "2026-01-01T04:00:00+00:00",
            "2026-01-01T04:30:00+00:00",
            "2025-12-31T23:00:00+00:00",
        ]
        result = BinanceGapDetector.detect(
            start_time="2026-01-01T00:00:00+00:00",
            end_time="2026-01-01T05:00:00+00:00",
            interval="1h",
            observed_open_times=observed,
        )
        self.assertEqual(6, result["expected_count"])
        self.assertEqual(3, result["observed_count"])
        self.assertEqual(3, result["missing_count"])
        self.assertEqual(1, result["duplicate_count"])
        self.assertEqual(1, result["misaligned_count"])
        self.assertEqual(1, result["out_of_range_count"])
        self.assertEqual(
            [
                {"start_time": "2026-01-01T02:00:00+00:00", "end_time": "2026-01-01T03:00:00+00:00", "bar_count": 2},
                {"start_time": "2026-01-01T05:00:00+00:00", "end_time": "2026-01-01T05:00:00+00:00", "bar_count": 1},
            ],
            result["missing_ranges"],
        )


if __name__ == "__main__":
    unittest.main()
