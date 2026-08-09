from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from .catalog_service import DatasetCatalogService
from .store import BASE_DIR, DataPlatformStore


CANONICAL_BAR_SCHEMA_VERSION = "bars.v1"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class CanonicalBarsCommitter:
    """Single persistence boundary for canonical bars from every provider."""

    def __init__(self, store: DataPlatformStore, output_root: str | Path | None = None):
        self.store = store
        self.catalog = DatasetCatalogService(store)
        self.output_root = Path(output_root or (BASE_DIR / "storage" / "canonical"))

    def commit(
        self,
        *,
        dataset_id: str,
        instrument_id: str,
        asset_class: str,
        venue: str,
        frequency: str,
        source: str,
        source_version: str,
        rows: list[dict[str, Any]],
        gap_count: int = 0,
        excluded_incomplete_rows: int = 0,
        adjustment: str = "NONE",
        time_semantics: str = "BAR_END_AVAILABLE_TIME",
        point_in_time_policy: str = "AS_OF",
    ) -> dict[str, Any]:
        if not rows:
            raise ValueError("canonical bars commit requires at least one row")
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise RuntimeError("Parquet export requires pyarrow; install project requirements first") from exc

        partition_root = (
            self.output_root / "bars" / f"asset_class={asset_class.lower()}"
            / f"venue={venue.upper()}" / f"frequency={frequency.lower()}"
        )
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            groups.setdefault(str(row["bar_start_time"])[:7], []).append(row)

        partitions: list[dict[str, Any]] = []
        for month, month_rows in sorted(groups.items()):
            month_rows.sort(key=lambda item: str(item["bar_start_time"]))
            year, month_number = month.split("-")
            directory = partition_root / f"year={year}" / f"month={month_number}" / "objects"
            directory.mkdir(parents=True, exist_ok=True)
            table = pa.Table.from_pylist(month_rows)
            metadata = dict(table.schema.metadata or {})
            metadata[b"datatube_schema_version"] = CANONICAL_BAR_SCHEMA_VERSION.encode()
            metadata[b"datatube_source_version"] = source_version.encode()
            table = table.replace_schema_metadata(metadata)
            temp = directory / f".staging-{uuid.uuid4().hex}.parquet"
            pq.write_table(table, temp, compression="zstd")
            part_hash = _sha256_file(temp)
            target = directory / f"sha256-{part_hash}.parquet"
            if target.exists():
                if _sha256_file(target) != part_hash:
                    temp.unlink(missing_ok=True)
                    raise RuntimeError(f"content-addressed Parquet collision: {target}")
                temp.unlink(missing_ok=True)
            else:
                try:
                    temp.rename(target)
                except FileExistsError:
                    temp.unlink(missing_ok=True)
                    if _sha256_file(target) != part_hash:
                        raise RuntimeError(f"content-addressed Parquet collision: {target}")
            uri = target.relative_to(BASE_DIR).as_posix() if target.is_relative_to(BASE_DIR) else str(target)
            partitions.append({
                "partition_key": month,
                "start_time": month_rows[0]["bar_start_time"],
                "end_time": month_rows[-1]["bar_end_time"],
                "row_count": len(month_rows),
                "file_uri": uri,
                "file_size": target.stat().st_size,
                "checksum": f"sha256:{part_hash}",
                "min_event_time": month_rows[0]["bar_start_time"],
                "max_event_time": month_rows[-1]["bar_start_time"],
                "quality_status": "PASS",
            })

        start = min(rows, key=lambda item: str(item["bar_start_time"]))["bar_start_time"]
        end = max(rows, key=lambda item: str(item["bar_end_time"]))["bar_end_time"]
        fingerprint_payload = {
            "dataset_id": dataset_id,
            "instrument_id": instrument_id,
            "data_type": "bars",
            "frequency": frequency,
            "source": source,
            "source_version": source_version,
            "schema_version": CANONICAL_BAR_SCHEMA_VERSION,
            "adjustment": str(adjustment or "NONE").strip().upper(),
            "time_semantics": str(time_semantics or "BAR_END_AVAILABLE_TIME").strip().upper(),
            "point_in_time_policy": str(point_in_time_policy or "AS_OF").strip().upper(),
            "start_time": start,
            "end_time": end,
            "partitions": [
                {"partition_key": item["partition_key"], "row_count": item["row_count"], "checksum": item["checksum"]}
                for item in partitions
            ],
        }
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        catalog = self.catalog.upsert_catalog({
            "dataset_id": dataset_id,
            "instrument_id": instrument_id,
            "data_type": "bars",
            "frequency": frequency,
            "source": source,
            "start_time": start,
            "end_time": end,
            "last_complete_time": max(rows, key=lambda item: str(item["available_time"]))["available_time"],
            "row_count": len(rows),
            "excluded_incomplete_rows": excluded_incomplete_rows,
            "gap_count": gap_count,
            "status": "PARTIAL",
            "quality_status": "PASS",
            "schema_version": CANONICAL_BAR_SCHEMA_VERSION,
            "adjustment": adjustment,
            "time_semantics": time_semantics,
            "point_in_time_policy": point_in_time_policy,
            "storage_path": partition_root.relative_to(BASE_DIR).as_posix() if partition_root.is_relative_to(BASE_DIR) else str(partition_root),
        })
        manifest = self.catalog.commit_manifest(
            dataset_id=dataset_id,
            dataset_fingerprint=fingerprint,
            schema_version=CANONICAL_BAR_SCHEMA_VERSION,
            partitions=partitions,
        )
        return {
            "dataset_id": dataset_id,
            "instrument_id": instrument_id,
            "row_count": len(rows),
            "excluded_incomplete_rows": excluded_incomplete_rows,
            "start_time": start,
            "end_time": end,
            "catalog": self.catalog.get_catalog(dataset_id) or catalog,
            "manifest": manifest,
            "partitions": partitions,
        }
