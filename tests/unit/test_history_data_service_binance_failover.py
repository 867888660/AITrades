from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import patch

from services import history_data_service


class StubResponse:
    def __init__(self, status_code: int, payload, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


class BinanceHistoryFailoverTest(unittest.TestCase):
    def test_empty_fallback_response_is_not_replaced_by_primary_451(self):
        first_available_ms = 1_783_881_600_000
        responses = [
            StubResponse(451, {}, "restricted location"),
            StubResponse(200, []),
            StubResponse(200, [[first_available_ms]]),
        ]

        with (
            patch.object(history_data_service.SESSION, "get", side_effect=responses),
            patch.object(history_data_service, "_connect", side_effect=lambda: sqlite3.connect(":memory:")),
            patch.object(history_data_service, "get_binance_coverage", return_value={"count": 0}),
        ):
            result = history_data_service.download_binance_klines(
                {
                    "symbol": "TSLABUSDT",
                    "interval": "1h",
                    "start": "2025-07-20T00:00:00+00:00",
                    "end": "2026-06-11T17:00:00+00:00",
                }
            )

        self.assertEqual(0, result["fetched"])
        self.assertEqual("https://data-api.binance.vision", result["source_url"])
        self.assertEqual(
            history_data_service._ms_to_iso(first_available_ms),
            result["available_from"],
        )
        self.assertTrue(any("HTTP 451" in error for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
