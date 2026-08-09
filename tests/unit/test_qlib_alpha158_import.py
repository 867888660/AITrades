from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from integrations.qlib.alpha158_import import (
    ALPHA158_NO_VWAP_FACTOR_COUNT,
    MINIMUM_HISTORY_BARS,
    _normalize_rows,
    alpha158_no_vwap_feature_config,
    materialize_qlib_dataset,
)


class _OfficialShapeLoader:
    @staticmethod
    def get_feature_config(config):
        names = [f"F{index:03d}" for index in range(ALPHA158_NO_VWAP_FACTOR_COUNT)]
        return [f"Expr{index}" for index in range(len(names))], names


def _rows(count: int = MINIMUM_HISTORY_BARS):
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return [
        {
            "bar_start_time": (start + timedelta(days=index)).isoformat(),
            "open": 100.0 + index,
            "high": 102.0 + index,
            "low": 99.0 + index,
            "close": 101.0 + index,
            "volume": 1_000.0 + index,
        }
        for index in range(count)
    ]


class QlibAlpha158ImportTests(unittest.TestCase):
    def test_feature_contract_is_157_and_has_no_vwap(self):
        fields, names = alpha158_no_vwap_feature_config(_OfficialShapeLoader)
        self.assertEqual(ALPHA158_NO_VWAP_FACTOR_COUNT, len(fields))
        self.assertEqual(ALPHA158_NO_VWAP_FACTOR_COUNT, len(names))
        self.assertNotIn("VWAP0", names)

    def test_normalizer_requires_60_unique_daily_bars(self):
        with self.assertRaisesRegex(ValueError, "at least 60"):
            _normalize_rows({"equity:xnas:AAPL": _rows(59)})
        duplicate_dates = _rows(60)
        duplicate_dates[-1]["bar_start_time"] = duplicate_dates[-2]["bar_start_time"]
        with self.assertRaisesRegex(ValueError, "duplicate daily bar"):
            _normalize_rows({"equity:xnas:AAPL": duplicate_dates})

    def test_materializer_uses_safe_symbols_and_qlib_float_format(self):
        rows = _rows()
        with tempfile.TemporaryDirectory() as directory:
            result = materialize_qlib_dataset(
                {"equity:xnas:AAPL": rows}, directory
            )
            self.assertEqual({"DT000001": "equity:xnas:AAPL"}, result["symbol_map"])
            mapping = json.loads(
                (Path(directory) / "datatube_symbol_map.json").read_text(encoding="utf-8")
            )
            self.assertEqual(result["symbol_map"], mapping)
            binary = np.fromfile(
                Path(directory) / "features" / "dt000001" / "close.day.bin",
                dtype="<f4",
            )
            self.assertEqual(61, len(binary))
            self.assertEqual(0.0, float(binary[0]))
            self.assertEqual(101.0, float(binary[1]))


if __name__ == "__main__":
    unittest.main()
