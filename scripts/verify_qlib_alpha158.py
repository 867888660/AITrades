from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from integrations.qlib import (
    ALPHA158_NO_VWAP_FACTOR_COUNT,
    Alpha158ImportService,
)
from services.data_platform.canonical_bars import CanonicalBarsCommitter
from services.data_platform.store import DataPlatformStore


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="datatube-qlib-verify-") as directory:
        root = Path(directory)
        store = DataPlatformStore(root / "metadata.db")
        rows = []
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        for index in range(100):
            bar_start = start + timedelta(days=index)
            bar_end = bar_start + timedelta(days=1)
            close = 100.0 + index * 0.2 + (index % 7) * 0.05
            rows.append(
                {
                    "instrument_id": "equity:xnas:AAPL",
                    "frequency": "1d",
                    "bar_start_time": bar_start.isoformat(),
                    "bar_end_time": bar_end.isoformat(),
                    "available_time": bar_end.isoformat(),
                    "ingested_at": bar_end.isoformat(),
                    "open": close - 0.1,
                    "high": close + 0.4,
                    "low": close - 0.5,
                    "close": close,
                    "volume": 1_000_000.0 + index * 1_000.0,
                    "turnover": 0.0,
                    "trade_count": 0,
                    "bar_status": "COMPLETE",
                    "source": "VERIFY/YFINANCE_SHAPE",
                    "source_version": "verify.v1",
                    "quality_status": "PASS",
                }
            )
        committed = CanonicalBarsCommitter(
            store, output_root=root / "canonical"
        ).commit(
            dataset_id="verify:yfinance:equity:xnas:AAPL:bars:1d:splits_only",
            instrument_id="equity:xnas:AAPL",
            asset_class="equity",
            venue="XNAS",
            frequency="1d",
            source="VERIFY/YFINANCE_SHAPE",
            source_version="verify.v1",
            rows=rows,
            adjustment="splits_only",
        )
        manifest_id = committed["manifest"].manifest_id
        service = Alpha158ImportService(store, output_root=root / "factor_cache")
        first = service.run(manifest_ids=[manifest_id])
        second = service.run(manifest_ids=[manifest_id])
        manifest = first["manifest"]
        assert first["status"] == "READY" and not first["cache_hit"]
        assert second["status"] == "READY" and second["cache_hit"]
        assert manifest["factor_count"] == ALPHA158_NO_VWAP_FACTOR_COUNT
        assert manifest["excluded_factors"] == ["VWAP0"]
        assert manifest["is_standard_alpha158"] is False
        assert Path(first["factor_path"]).is_file()
        import pyarrow.parquet as pq

        factor_frame = pq.ParquetFile(first["factor_path"]).read().to_pandas()
        assert len(factor_frame.columns) == ALPHA158_NO_VWAP_FACTOR_COUNT + 2
        assert "VWAP0" not in factor_frame.columns
        assert factor_frame["KMID"].notna().all()
        assert factor_frame["MA60"].notna().any()
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "manifest_id": manifest_id,
                    "cache_id": first["cache_id"],
                    "factor_count": manifest["factor_count"],
                    "row_count": manifest["output"]["row_count"],
                    "cache_hit_verified": second["cache_hit"],
                    "qlib_version": manifest["engine"]["qlib_version"],
                },
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
