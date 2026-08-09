"""Small smoke test for the first Data Platform vertical slice.

Run with:
    python scripts/verify_data_platform.py
"""

from __future__ import annotations

import sys
import tempfile
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.data_platform import (
    DataPlatformStore,
    DatasetCatalogService,
    DataRequirementService,
    FrozenManifestData,
    InstrumentRegistry,
)
from services.data_platform.instrument_registry import make_instrument_id
from services.data_platform.models import Instrument


def write_bars(path: Path, instrument_id: str, count: int) -> dict[str, object]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    rows = []
    for index in range(count):
        bar_start = start + timedelta(hours=index)
        bar_end = bar_start + timedelta(hours=1) - timedelta(milliseconds=1)
        rows.append({
            "instrument_id": instrument_id,
            "bar_start_time": bar_start.isoformat(),
            "bar_end_time": bar_end.isoformat(),
            "available_time": bar_end.isoformat(),
            "open": 100.0 + index,
            "high": 101.0 + index,
            "low": 99.0 + index,
            "close": 100.5 + index,
            "volume": 10.0,
            "bar_status": "COMPLETE",
        })
    table = pa.Table.from_pylist(rows)
    table = table.replace_schema_metadata({b"datatube_schema_version": b"bars.v1"})
    pq.write_table(table, path, compression="zstd")
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "partition_key": path.stem,
        "start_time": rows[0]["bar_start_time"],
        "end_time": rows[-1]["bar_end_time"],
        "row_count": count,
        "file_uri": str(path),
        "file_size": path.stat().st_size,
        "checksum": f"sha256:{checksum}",
        "min_event_time": rows[0]["bar_start_time"],
        "max_event_time": rows[-1]["bar_start_time"],
        "quality_status": "PASS",
    }


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="datatube-data-platform-") as temp_dir:
        store = DataPlatformStore(Path(temp_dir) / "metadata.db")
        registry = InstrumentRegistry(store)
        instrument_id = make_instrument_id("crypto_spot", "BINANCE", "BTCUSDT")
        registry.register(
            Instrument(
                instrument_id=instrument_id,
                asset_class="crypto_spot",
                venue="BINANCE",
                market_type="SPOT",
                native_symbol="BTCUSDT",
                display_symbol="BTC/USDT",
                base_asset="BTC",
                quote_asset="USDT",
            ),
            aliases=[("binance", "BTCUSDT"), ("legacy", "crypto:binance:BTCUSDT")],
        )
        assert registry.resolve_alias("binance", "BTCUSDT") == instrument_id

        requirements = DataRequirementService(store)
        requirement = requirements.create({
            "owner_type": "RESEARCH_PROJECT",
            "owner_id": "project_crypto_momentum_001",
            "instrument_ids": [instrument_id],
            "data_type": "bars",
            "frequency": "1h",
            "fields": ["open", "high", "low", "close", "volume"],
            "history_mode": "FIXED",
            "history_start": "2025-01-01T00:00:00+00:00",
            "history_end": "2025-03-31T23:00:00+00:00",
        })
        same_requirement = requirements.create({
            "owner_type": "RESEARCH_PROJECT",
            "owner_id": "project_crypto_momentum_001",
            "instrument_ids": [instrument_id],
            "data_type": "bars",
            "frequency": "1h",
            "fields": ["volume", "close", "open", "low", "high"],
            "history_mode": "FIXED",
            "history_start": "2025-01-01T00:00:00+00:00",
            "history_end": "2025-03-31T23:00:00+00:00",
        })
        assert requirement.requirement_id == same_requirement.requirement_id

        catalog = DatasetCatalogService(store)
        catalog.upsert_catalog({
            "dataset_id": "binance_btcusdt_1h",
            "instrument_id": instrument_id,
            "data_type": "bars",
            "frequency": "1h",
            "source": "BINANCE",
            "status": "PARTIAL",
            "quality_status": "PASS",
            "schema_version": "bars.v1",
            "storage_path": "storage/canonical/bars/venue=BINANCE/frequency=1h",
        })
        first_partition = write_bars(Path(temp_dir) / "bars-v1.parquet", instrument_id, 2)
        second_partition = write_bars(Path(temp_dir) / "bars-v2.parquet", instrument_id, 3)
        manifest = catalog.commit_manifest(
            dataset_id="binance_btcusdt_1h",
            dataset_fingerprint="binance|BTCUSDT|bars|1h|bars.v1",
            schema_version="bars.v1",
            partitions=[first_partition],
        )
        repeat = catalog.commit_manifest(
            dataset_id="binance_btcusdt_1h",
            dataset_fingerprint="binance|BTCUSDT|bars|1h|bars.v1",
            schema_version="bars.v1",
            partitions=[first_partition],
        )
        assert manifest.manifest_id == repeat.manifest_id
        changed = catalog.commit_manifest(
            dataset_id="binance_btcusdt_1h",
            dataset_fingerprint="binance|BTCUSDT|bars|1h|bars.v1",
            schema_version="bars.v1",
            partitions=[second_partition],
        )
        assert changed.manifest_id != manifest.manifest_id
        assert changed.version == manifest.version + 1
        assert len(catalog.list_manifests("binance_btcusdt_1h")) == 2
        latest_catalog = catalog.get_catalog("binance_btcusdt_1h")
        assert latest_catalog is not None
        assert latest_catalog.row_count == 3
        assert latest_catalog.latest_manifest_id == changed.manifest_id
        old_repeat = catalog.commit_manifest(
            dataset_id="binance_btcusdt_1h",
            dataset_fingerprint="binance|BTCUSDT|bars|1h|bars.v1",
            schema_version="bars.v1",
            partitions=[first_partition],
        )
        assert old_repeat.manifest_id == manifest.manifest_id
        latest_catalog = catalog.get_catalog("binance_btcusdt_1h")
        assert latest_catalog is not None
        assert latest_catalog.latest_manifest_id == changed.manifest_id
        assert latest_catalog.row_count == 3
        frozen = FrozenManifestData(store, manifest.manifest_id)
        assert frozen.descriptor()["status"] == "READY"
        assert len(frozen.partitions(as_of="2025-01-15T00:00:00+00:00")) == 1
        print("Data Platform smoke test passed")
        print(f"instrument_id={instrument_id}")
        print(f"requirement_id={requirement.requirement_id}")
        print(f"manifest_id={manifest.manifest_id}")


if __name__ == "__main__":
    main()
