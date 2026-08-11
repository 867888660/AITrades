from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping

from services.history_storage_service import get_data_platform_canonical_root

from .catalog_service import DatasetCatalogService
from .store import BASE_DIR, DataPlatformStore


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class CanonicalDatasetCommitter:
    """Commit a bounded canonical point-in-time table as immutable Parquet.

    Bars keep their specialized writer.  This writer is for security master,
    valuation, corporate-action, fundamental and quality datasets.  Every row
    must expose an event time and an availability time so as-of reads cannot
    accidentally see information early.
    """

    def __init__(self, store: DataPlatformStore, output_root: str | Path | None = None):
        self.store = store
        self.catalog = DatasetCatalogService(store)
        self.output_root = Path(output_root or get_data_platform_canonical_root())

    def commit(
        self,
        *,
        dataset_id: str,
        instrument_id: str,
        data_type: str,
        frequency: str,
        source: str,
        source_version: str,
        schema_version: str,
        rows: Iterable[Mapping[str, Any]],
        event_time_field: str = "event_time",
        available_time_field: str = "available_time",
        point_in_time_policy: str = "AS_OF",
        adjustment: str = "NONE",
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = [dict(row) for row in rows]
        if not normalized:
            raise ValueError("canonical dataset commit requires at least one row")
        for index, row in enumerate(normalized):
            if not str(row.get(event_time_field) or "").strip():
                raise ValueError(f"row {index} is missing {event_time_field}")
            if not str(row.get(available_time_field) or "").strip():
                raise ValueError(f"row {index} is missing {available_time_field}")
            if str(row[available_time_field]) < str(row[event_time_field]):
                raise ValueError(f"row {index} is available before its event time")
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise RuntimeError("Parquet export requires pyarrow") from exc

        normalized.sort(
            key=lambda item: (
                str(item[event_time_field]),
                str(item.get("security_id") or item.get("instrument_id") or ""),
            )
        )
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in normalized:
            stamp = str(row[event_time_field])
            groups.setdefault(stamp[:7] if len(stamp) >= 7 else "all", []).append(row)

        root = self.output_root / data_type.lower() / f"schema={schema_version}"
        partitions: list[dict[str, Any]] = []
        for key, group in sorted(groups.items()):
            directory = root / f"period={key}" / "objects"
            directory.mkdir(parents=True, exist_ok=True)
            table = pa.Table.from_pylist(group)
            embedded = dict(table.schema.metadata or {})
            embedded[b"datatube_schema_version"] = schema_version.encode("utf-8")
            embedded[b"datatube_source_version"] = source_version.encode("utf-8")
            table = table.replace_schema_metadata(embedded)
            staging = directory / f".staging-{uuid.uuid4().hex}.parquet"
            pq.write_table(table, staging, compression="zstd")
            digest = _sha256_file(staging)
            target = directory / f"sha256-{digest}.parquet"
            if target.exists():
                if _sha256_file(target) != digest:
                    staging.unlink(missing_ok=True)
                    raise RuntimeError(f"content-addressed Parquet collision: {target}")
                staging.unlink(missing_ok=True)
            else:
                staging.replace(target)
            uri = target.relative_to(BASE_DIR).as_posix() if target.is_relative_to(BASE_DIR) else str(target)
            partitions.append(
                {
                    "partition_key": key,
                    "start_time": group[0][event_time_field],
                    "end_time": group[-1][event_time_field],
                    "row_count": len(group),
                    "file_uri": uri,
                    "file_size": target.stat().st_size,
                    "checksum": f"sha256:{digest}",
                    "min_event_time": group[0][event_time_field],
                    "max_event_time": group[-1][event_time_field],
                    "quality_status": "PASS",
                }
            )

        start = str(normalized[0][event_time_field])
        end = str(normalized[-1][event_time_field])
        last_available = max(str(row[available_time_field]) for row in normalized)
        fields = sorted({str(key).lower() for row in normalized for key in row})
        fingerprint_payload = {
            "dataset_id": dataset_id,
            "schema_version": schema_version,
            "source_version": source_version,
            "point_in_time_policy": point_in_time_policy,
            "partitions": [
                (item["partition_key"], item["row_count"], item["checksum"])
                for item in partitions
            ],
        }
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        catalog = self.catalog.upsert_catalog(
            {
                "dataset_id": dataset_id,
                "instrument_id": instrument_id,
                "data_type": data_type,
                "frequency": frequency,
                "source": source,
                "start_time": start,
                "end_time": end,
                "last_complete_time": last_available,
                "row_count": len(normalized),
                "gap_count": 0,
                "status": "PARTIAL",
                "quality_status": "PASS",
                "schema_version": schema_version,
                "storage_path": str(root),
                "fields": fields,
                "adjustment": adjustment,
                "time_semantics": "SOURCE_AVAILABLE_TIME",
                "point_in_time_policy": point_in_time_policy,
                "metadata": dict(metadata or {}),
            }
        )
        manifest = self.catalog.commit_manifest(
            dataset_id=dataset_id,
            dataset_fingerprint=fingerprint,
            schema_version=schema_version,
            partitions=partitions,
        )
        return {
            "dataset_id": dataset_id,
            "row_count": len(normalized),
            "catalog": self.catalog.get_catalog(dataset_id) or catalog,
            "manifest": manifest,
            "partitions": partitions,
        }
