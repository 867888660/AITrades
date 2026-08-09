from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path
import json
from typing import Any, Iterable, Optional

from .models import DatasetCatalogEntry, DatasetManifest, DatasetPartition
from .store import BASE_DIR, DataPlatformStore, json_dumps


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class DatasetCatalogService:
    def __init__(self, store: DataPlatformStore):
        self.store = store

    def upsert_catalog(self, payload: dict[str, Any]) -> DatasetCatalogEntry:
        required = ("dataset_id", "instrument_id", "data_type", "source", "status", "quality_status", "schema_version", "storage_path")
        missing = [key for key in required if not _clean(payload.get(key))]
        if missing:
            raise ValueError(f"missing catalog fields: {', '.join(missing)}")
        now = _now()
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                """
                INSERT INTO dataset_catalog(
                    dataset_id, instrument_id, data_type, frequency, source,
                    start_time, end_time, last_complete_time, row_count, gap_count,
                    status, quality_status, schema_version, storage_path,
                    latest_manifest_id, updated_at, fields_json, adjustment,
                    time_semantics, point_in_time_policy, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(dataset_id) DO UPDATE SET
                    instrument_id=excluded.instrument_id,
                    data_type=excluded.data_type,
                    frequency=excluded.frequency,
                    source=excluded.source,
                    start_time=excluded.start_time,
                    end_time=excluded.end_time,
                    last_complete_time=excluded.last_complete_time,
                    row_count=excluded.row_count,
                    gap_count=excluded.gap_count,
                    status=excluded.status,
                    quality_status=excluded.quality_status,
                    schema_version=excluded.schema_version,
                    storage_path=excluded.storage_path,
                    fields_json=excluded.fields_json,
                    adjustment=excluded.adjustment,
                    time_semantics=excluded.time_semantics,
                    point_in_time_policy=excluded.point_in_time_policy,
                    metadata_json=excluded.metadata_json,
                    updated_at=excluded.updated_at
                """,
                (
                    _clean(payload["dataset_id"]),
                    _clean(payload["instrument_id"]),
                    _clean(payload["data_type"]).lower(),
                    _clean(payload.get("frequency")).lower(),
                    _clean(payload["source"]),
                    payload.get("start_time"),
                    payload.get("end_time"),
                    payload.get("last_complete_time"),
                    int(payload.get("row_count", 0)),
                    int(payload.get("gap_count", 0)),
                    _clean(payload["status"]).upper(),
                    _clean(payload["quality_status"]).upper(),
                    _clean(payload["schema_version"]),
                    _clean(payload["storage_path"]),
                    payload.get("latest_manifest_id"),
                    now,
                    json_dumps(sorted({_clean(item).lower() for item in payload.get("fields", []) if _clean(item)})),
                    _clean(payload.get("adjustment") or "NONE").upper(),
                    _clean(payload.get("time_semantics") or "BAR_END_AVAILABLE_TIME").upper(),
                    _clean(payload.get("point_in_time_policy") or "AS_OF").upper(),
                    json_dumps(payload.get("metadata") or {}),
                ),
            )
        return self.get_catalog(_clean(payload["dataset_id"]))  # type: ignore[return-value]

    def get_catalog(self, dataset_id: str) -> Optional[DatasetCatalogEntry]:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM dataset_catalog WHERE dataset_id = ?",
                (_clean(dataset_id),),
            ).fetchone()
        if row is None:
            return None
        return DatasetCatalogEntry(
            dataset_id=str(row["dataset_id"]),
            instrument_id=str(row["instrument_id"]),
            data_type=str(row["data_type"]),
            frequency=str(row["frequency"]),
            source=str(row["source"]),
            start_time=row["start_time"],
            end_time=row["end_time"],
            last_complete_time=row["last_complete_time"],
            row_count=int(row["row_count"]),
            gap_count=int(row["gap_count"]),
            status=str(row["status"]),
            quality_status=str(row["quality_status"]),
            schema_version=str(row["schema_version"]),
            storage_path=str(row["storage_path"]),
            latest_manifest_id=row["latest_manifest_id"],
            updated_at=row["updated_at"],
            fields=tuple(json.loads(row["fields_json"] or "[]")),
            adjustment=str(row["adjustment"]),
            time_semantics=str(row["time_semantics"]),
            point_in_time_policy=str(row["point_in_time_policy"]),
            metadata=json.loads(row["metadata_json"] or "{}"),
        )

    def list_catalog(
        self,
        *,
        instrument_id: str = "",
        data_type: str = "",
        status: str = "",
    ) -> list[DatasetCatalogEntry]:
        clauses = []
        params: list[str] = []
        if _clean(instrument_id):
            clauses.append("instrument_id = ?")
            params.append(_clean(instrument_id))
        if _clean(data_type):
            clauses.append("data_type = ?")
            params.append(_clean(data_type).lower())
        if _clean(status):
            clauses.append("status = ?")
            params.append(_clean(status).upper())
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.store.connection() as conn:
            rows = conn.execute(
                f"SELECT dataset_id FROM dataset_catalog{where} ORDER BY updated_at DESC, dataset_id",
                params,
            ).fetchall()
        return [self.get_catalog(str(row[0])) for row in rows]  # type: ignore[list-item]

    def commit_manifest(
        self,
        *,
        dataset_id: str,
        dataset_fingerprint: str,
        schema_version: str,
        partitions: Iterable[dict[str, Any]],
        manifest_id: str | None = None,
    ) -> DatasetManifest:
        partition_payloads = []
        for item in partitions:
            partition_payloads.append({
                "partition_key": _clean(item.get("partition_key")),
                "start_time": item.get("start_time"),
                "end_time": item.get("end_time"),
                "row_count": int(item.get("row_count", 0)),
                "file_uri": _clean(item.get("file_uri")),
                "file_size": int(item.get("file_size", 0)),
                "checksum": _clean(item.get("checksum")),
                "min_event_time": item.get("min_event_time"),
                "max_event_time": item.get("max_event_time"),
                "quality_status": _clean(item.get("quality_status") or "PASS").upper(),
            })
        if not partition_payloads:
            raise ValueError("a dataset manifest requires at least one partition")
        for item in partition_payloads:
            if not item["partition_key"] or not item["file_uri"]:
                raise ValueError("manifest partitions require partition_key and file_uri")
            if not item["checksum"].startswith("sha256:"):
                raise ValueError("manifest partitions require a sha256 checksum")
            path = Path(item["file_uri"])
            if not path.is_absolute():
                path = BASE_DIR / path
            if not path.is_file():
                raise FileNotFoundError(f"manifest partition does not exist: {path}")
            actual_size = path.stat().st_size
            if actual_size != item["file_size"]:
                raise ValueError(f"manifest partition size mismatch: {path}")
            actual_checksum = _sha256_file(path)
            if actual_checksum != item["checksum"].split(":", 1)[1]:
                raise ValueError(f"manifest partition checksum mismatch: {path}")
        manifest_material = {
            "dataset_id": _clean(dataset_id),
            "dataset_fingerprint": _clean(dataset_fingerprint),
            "schema_version": _clean(schema_version),
            "partitions": partition_payloads,
        }
        manifest_hash = hashlib.sha256(json_dumps(manifest_material).encode("utf-8")).hexdigest()
        now = _now()
        manifest_id = _clean(manifest_id) or f"manifest_{uuid.uuid4().hex}"

        with self.store.transaction(immediate=True) as conn:
            existing = conn.execute(
                "SELECT manifest_id, manifest_version FROM dataset_manifests WHERE manifest_hash = ?",
                (manifest_hash,),
            ).fetchone()
            if existing:
                manifest_id = str(existing[0])
                version = int(existing[1])
            else:
                catalog = conn.execute(
                    "SELECT dataset_id FROM dataset_catalog WHERE dataset_id = ?",
                    (_clean(dataset_id),),
                ).fetchone()
                if not catalog:
                    raise ValueError("catalog entry must exist before committing a manifest")
                version_row = conn.execute(
                    "SELECT COALESCE(MAX(manifest_version), 0) + 1 FROM dataset_manifests WHERE dataset_id = ?",
                    (_clean(dataset_id),),
                ).fetchone()
                version = int(version_row[0])
                conn.execute(
                    """
                    INSERT INTO dataset_manifests(
                        manifest_id, dataset_id, dataset_fingerprint, manifest_version,
                        schema_version, status, manifest_hash, created_at, committed_at
                    ) VALUES (?, ?, ?, ?, ?, 'READY', ?, ?, ?)
                    """,
                    (
                        manifest_id,
                        _clean(dataset_id),
                        _clean(dataset_fingerprint),
                        version,
                        _clean(schema_version),
                        manifest_hash,
                        now,
                        now,
                    ),
                )
                for index, item in enumerate(partition_payloads, start=1):
                    conn.execute(
                        """
                        INSERT INTO dataset_partitions(
                            partition_id, manifest_id, partition_key, start_time, end_time,
                            row_count, file_uri, file_size, checksum, min_event_time,
                            max_event_time, quality_status
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            f"{manifest_id}:p{index}",
                            manifest_id,
                            item["partition_key"],
                            item["start_time"],
                            item["end_time"],
                            item["row_count"],
                            item["file_uri"],
                            item["file_size"],
                            item["checksum"],
                            item["min_event_time"],
                            item["max_event_time"],
                            item["quality_status"],
                        ),
                    )
            latest_version_row = conn.execute(
                """
                SELECT manifest_id, manifest_version FROM dataset_manifests
                WHERE dataset_id = ? ORDER BY manifest_version DESC LIMIT 1
                """,
                (_clean(dataset_id),),
            ).fetchone()
            latest_manifest_id = str(latest_version_row[0])
            latest_partitions = conn.execute(
                "SELECT * FROM dataset_partitions WHERE manifest_id = ?",
                (latest_manifest_id,),
            ).fetchall()
            quality_status = "PASS"
            if any(str(item["quality_status"]) == "FAIL" for item in latest_partitions):
                quality_status = "FAIL"
            elif any(str(item["quality_status"]) == "WARN" for item in latest_partitions):
                quality_status = "WARN"
            start_values = [item["start_time"] for item in latest_partitions if item["start_time"]]
            end_values = [item["end_time"] for item in latest_partitions if item["end_time"]]
            max_event_values = [item["max_event_time"] for item in latest_partitions if item["max_event_time"]]
            conn.execute(
                """
                UPDATE dataset_catalog
                SET start_time = ?,
                    end_time = ?,
                    last_complete_time = ?,
                    row_count = ?,
                    status = 'READY',
                    quality_status = ?,
                    latest_manifest_id = ?,
                    updated_at = ?
                WHERE dataset_id = ?
                """,
                (
                    min(start_values) if start_values else None,
                    max(end_values) if end_values else None,
                    max(max_event_values) if max_event_values else None,
                    sum(int(item["row_count"]) for item in latest_partitions),
                    quality_status,
                    latest_manifest_id,
                    now,
                    _clean(dataset_id),
                ),
            )

        return self.get_manifest(manifest_id)  # type: ignore[return-value]

    def get_manifest(self, manifest_id: str) -> Optional[DatasetManifest]:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM dataset_manifests WHERE manifest_id = ?",
                (_clean(manifest_id),),
            ).fetchone()
            if row is None:
                return None
            partition_rows = conn.execute(
                "SELECT * FROM dataset_partitions WHERE manifest_id = ? ORDER BY partition_id",
                (_clean(manifest_id),),
            ).fetchall()
        partitions = tuple(
            DatasetPartition(
                partition_id=str(item["partition_id"]),
                manifest_id=str(item["manifest_id"]),
                partition_key=str(item["partition_key"]),
                start_time=item["start_time"],
                end_time=item["end_time"],
                row_count=int(item["row_count"]),
                file_uri=str(item["file_uri"]),
                file_size=int(item["file_size"]),
                checksum=str(item["checksum"]),
                min_event_time=item["min_event_time"],
                max_event_time=item["max_event_time"],
                quality_status=str(item["quality_status"]),
            )
            for item in partition_rows
        )
        return DatasetManifest(
            manifest_id=str(row["manifest_id"]),
            dataset_id=str(row["dataset_id"]),
            dataset_fingerprint=str(row["dataset_fingerprint"]),
            version=int(row["manifest_version"]),
            schema_version=str(row["schema_version"]),
            status=str(row["status"]),
            manifest_hash=str(row["manifest_hash"]),
            created_at=str(row["created_at"]),
            committed_at=row["committed_at"],
            partitions=partitions,
        )

    def list_manifests(self, dataset_id: str) -> list[DatasetManifest]:
        with self.store.connection() as conn:
            rows = conn.execute(
                "SELECT manifest_id FROM dataset_manifests WHERE dataset_id = ? ORDER BY manifest_version DESC",
                (_clean(dataset_id),),
            ).fetchall()
        return [self.get_manifest(str(row[0])) for row in rows]  # type: ignore[list-item]
