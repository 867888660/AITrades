from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services import history_data_service


class _Response:
    status_code = 200
    text = ""

    def __init__(self, history=None):
        self._history = history or []

    def json(self):
        return {"history": self._history}


class PolymarketHistoryDownloadTest(unittest.TestCase):
    def test_latest_uses_relative_interval_without_absolute_bounds(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(
            history_data_service, "HISTORY_DB_PATH", Path(temp) / "history.db"
        ), patch.object(
            history_data_service.SESSION, "get", return_value=_Response()
        ) as request:
            history_data_service.download_polymarket_price_history({
                "token_id": "token-1",
                "start": "2025-01-01T00:00:00+00:00",
                "end": "2026-01-01T00:00:00+00:00",
                "interval": "max",
                "fidelity": "1440",
                "latest_available": True,
            })
        params = request.call_args.kwargs["params"]
        self.assertEqual("max", params["interval"])
        self.assertNotIn("startTs", params)
        self.assertNotIn("endTs", params)

    def test_fixed_long_range_is_chunked_without_interval_filter(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(
            history_data_service, "HISTORY_DB_PATH", Path(temp) / "history.db"
        ), patch.object(
            history_data_service.SESSION, "get", return_value=_Response()
        ) as request:
            result = history_data_service.download_polymarket_price_history({
                "token_id": "token-1",
                "start": "2026-01-01T00:00:00+00:00",
                "end": "2026-02-10T00:00:00+00:00",
                "interval": "max",
                "fidelity": "1440",
            })
        self.assertEqual(3, request.call_count)
        for call in request.call_args_list:
            params = call.kwargs["params"]
            self.assertNotIn("interval", params)
            self.assertLessEqual(params["endTs"] - params["startTs"], 14 * 24 * 60 * 60)
        self.assertEqual(3, len(result["requested"]))


if __name__ == "__main__":
    unittest.main()
