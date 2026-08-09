from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .catalog_service import DatasetCatalogService
from .models import DatasetManifest, DatasetPartition
from .store import BASE_DIR, DataPlatformStore


class FrozenManifestData:
    """Read-only view of a READY dataset manifest.

    This is the first boundary that future Factor and Backtest engines should
    consume.  It intentionally does not scan mutable directories or expose
    raw SQLite rows.
    """

    def __init__(self, store: DataPlatformStore, manifest_id: str):
        self.store = store
        self.catalog = DatasetCatalogService(store)
        manifest = self.catalog.get_manifest(manifest_id)
        if manifest is None:
            raise ValueError(f"dataset manifest not found: {manifest_id}")
        if manifest.status != "READY":
            raise ValueError(f"dataset manifest is not READY: {manifest_id}")
        self.manifest: DatasetManifest = manifest
        self.verify()

    @property
    def manifest_id(self) -> str:
        return self.manifest.manifest_id

    @property
    def dataset_id(self) -> str:
        return self.manifest.dataset_id

    def partitions(self, *, as_of: Optional[str] = None) -> tuple[DatasetPartition, ...]:
        if not as_of:
            return self.manifest.partitions
        cutoff = self._parse_time(as_of)
        result = []
        for partition in self.manifest.partitions:
            if not partition.min_event_time:
                result.append(partition)
                continue
            event_time = self._parse_time(partition.min_event_time)
            if event_time <= cutoff:
                result.append(partition)
        return tuple(result)

    def descriptor(self) -> dict[str, object]:
        return {
            "manifest_id": self.manifest.manifest_id,
            "dataset_id": self.manifest.dataset_id,
            "dataset_fingerprint": self.manifest.dataset_fingerprint,
            "version": self.manifest.version,
            "schema_version": self.manifest.schema_version,
            "status": self.manifest.status,
            "manifest_hash": self.manifest.manifest_hash,
            "partition_count": len(self.manifest.partitions),
            "physical_validation": "PASS",
        }

    @staticmethod
    def _partition_path(partition: DatasetPartition) -> Path:
        path = Path(partition.file_uri)
        return path if path.is_absolute() else BASE_DIR / path

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _parse_time(value: Any) -> datetime:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _verify_partition(self, partition: DatasetPartition) -> None:
        path = self._partition_path(partition)
        if not path.is_file():
            raise FileNotFoundError(f"manifest partition file is missing: {path}")
        if path.stat().st_size != partition.file_size:
            raise ValueError(f"manifest partition size mismatch: {path}")
        if not partition.checksum.startswith("sha256:"):
            raise ValueError(f"manifest partition checksum is not sha256: {path}")
        checksum = self._sha256_file(path)
        if checksum != partition.checksum.split(":", 1)[1]:
            raise ValueError(f"manifest partition checksum mismatch: {path}")
        try:
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise RuntimeError("Parquet verification requires pyarrow") from exc
        parquet = pq.ParquetFile(path)
        if parquet.metadata.num_rows != partition.row_count:
            raise ValueError(f"manifest partition row count mismatch: {path}")
        schema = parquet.schema_arrow
        schema_metadata = schema.metadata or {}
        embedded_version = (schema_metadata.get(b"datatube_schema_version") or b"").decode("utf-8")
        if embedded_version != self.manifest.schema_version:
            raise ValueError(
                f"manifest partition schema version mismatch: expected {self.manifest.schema_version}, got {embedded_version or 'missing'}"
            )
        if self.manifest.schema_version == "bars.v1":
            required = {
                "instrument_id", "bar_start_time", "bar_end_time", "available_time",
                "open", "high", "low", "close", "volume", "bar_status",
            }
            missing = sorted(required - set(schema.names))
            if missing:
                raise ValueError(f"bars.v1 partition is missing columns {missing}: {path}")
            rows = parquet.read(columns=["bar_start_time", "bar_end_time", "available_time", "bar_status"]).to_pylist()
            starts = [str(row["bar_start_time"]) for row in rows]
            ends = [str(row["bar_end_time"]) for row in rows]
            min_start = min(starts, key=self._parse_time) if starts else ""
            max_start = max(starts, key=self._parse_time) if starts else ""
            max_end = max(ends, key=self._parse_time) if ends else ""
            if starts and partition.min_event_time and self._parse_time(min_start) != self._parse_time(partition.min_event_time):
                raise ValueError(f"manifest partition minimum event time mismatch: {path}")
            if starts and partition.max_event_time and self._parse_time(max_start) != self._parse_time(partition.max_event_time):
                raise ValueError(f"manifest partition maximum event time mismatch: {path}")
            if starts and partition.start_time and self._parse_time(min_start) < self._parse_time(partition.start_time):
                raise ValueError(f"manifest partition starts before declared range: {path}")
            if ends and partition.end_time and self._parse_time(max_end) > self._parse_time(partition.end_time):
                raise ValueError(f"manifest partition ends after declared range: {path}")
            for row in rows:
                if str(row["bar_status"] or "").upper() != "COMPLETE":
                    raise ValueError(f"manifest contains an incomplete bar: {path}")
                if self._parse_time(row["available_time"]) < self._parse_time(row["bar_end_time"]):
                    raise ValueError(f"bar available_time precedes bar_end_time: {path}")
        if self.manifest.schema_version == "polymarket_price.v1":
            required = {
                "event_time",
                "available_time",
                "price",
            }
            missing = sorted(required - set(schema.names))
            if missing:
                raise ValueError(
                    f"polymarket_price.v1 partition is missing columns {missing}: {path}"
                )
            rows = parquet.read(
                columns=["event_time", "available_time"]
            ).to_pylist()
            events = [str(row["event_time"]) for row in rows]
            min_event = min(events, key=self._parse_time) if events else ""
            max_event = max(events, key=self._parse_time) if events else ""
            if (
                events
                and partition.min_event_time
                and self._parse_time(min_event)
                != self._parse_time(partition.min_event_time)
            ):
                raise ValueError(
                    f"manifest partition minimum event time mismatch: {path}"
                )
            if (
                events
                and partition.max_event_time
                and self._parse_time(max_event)
                != self._parse_time(partition.max_event_time)
            ):
                raise ValueError(
                    f"manifest partition maximum event time mismatch: {path}"
                )
            for row in rows:
                if self._parse_time(row["available_time"]) < self._parse_time(
                    row["event_time"]
                ):
                    raise ValueError(
                        f"price available_time precedes event_time: {path}"
                    )

    def verify(self) -> dict[str, object]:
        if not self.manifest.partitions:
            raise ValueError(f"dataset manifest has no partitions: {self.manifest_id}")
        for partition in self.manifest.partitions:
            if partition.quality_status != "PASS":
                raise ValueError(f"manifest partition quality is not PASS: {partition.partition_id}")
            self._verify_partition(partition)
        return {
            "manifest_id": self.manifest_id,
            "partition_count": len(self.manifest.partitions),
            "status": "PASS",
        }

    def read_rows(self, *, columns: Optional[list[str]] = None, as_of: Optional[str] = None) -> list[dict[str, Any]]:
        """Read rows only from the pinned manifest partitions."""
        try:
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise RuntimeError("Parquet reads require pyarrow; install project requirements first") from exc
        requested_columns = list(columns) if columns is not None else None
        read_columns = list(requested_columns) if requested_columns is not None else None
        if as_of and read_columns is not None and "available_time" not in read_columns:
            read_columns.append("available_time")
        result: list[dict[str, Any]] = []
        for partition in self.partitions(as_of=as_of):
            self._verify_partition(partition)
            path = self._partition_path(partition)
            # Parquet paths use Hive-style directories such as
            # frequency=1m.  Reading a single file through read_table(path)
            # can merge the directory partition column with the same field
            # stored in the canonical schema and produce a type conflict.
            result.extend(pq.ParquetFile(path).read(columns=read_columns).to_pylist())
        if as_of:
            cutoff = self._parse_time(as_of)
            filtered = []
            for row in result:
                available = str(row.get("available_time") or "").strip()
                if not available:
                    continue
                available_dt = self._parse_time(available)
                if available_dt <= cutoff:
                    filtered.append(row)
            result = filtered
        if requested_columns is not None:
            result = [{column: row.get(column) for column in requested_columns} for row in result]
        return result

    def read_bars_by_instrument(self, *, as_of: Optional[str] = None) -> dict[str, list[dict[str, Any]]]:
        rows = self.read_rows(as_of=as_of)
        catalog_entry = self.catalog.get_catalog(self.dataset_id)
        fallback_instrument_id = (
            catalog_entry.instrument_id
            if catalog_entry is not None
            else ""
        )
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            instrument_id = str(
                row.get("instrument_id")
                or fallback_instrument_id
                or ""
            ).strip()
            if not instrument_id:
                continue
            grouped.setdefault(instrument_id, []).append(row)
        for instrument_rows in grouped.values():
            instrument_rows.sort(
                key=lambda item: str(
                    item.get("bar_start_time")
                    or item.get("event_time")
                    or item.get("available_time")
                    or ""
                )
            )
        return grouped
