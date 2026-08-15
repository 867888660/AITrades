from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Iterator
from typing import Any, Iterable, Optional

from .catalog_service import DatasetCatalogService
from .models import DatasetManifest, DatasetPartition
from .store import BASE_DIR, DataPlatformStore
from services.history_storage_service import resolve_managed_history_path


class FrozenManifestData:
    """Read-only view of a READY dataset manifest.

    This is the first boundary that future Factor and Backtest engines should
    consume.  It intentionally does not scan mutable directories or expose
    raw SQLite rows.
    """

    def __init__(self, store: DataPlatformStore, manifest_id: str):
        self.store = store
        self.catalog = DatasetCatalogService(store)
        self._verified_partition_stats: dict[str, tuple[int, int]] = {}
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

    def _partitions_in_range(
        self,
        *,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        as_of: Optional[str] = None,
    ) -> tuple[DatasetPartition, ...]:
        """Prune immutable partitions using their declared event-time bounds."""
        start = self._parse_time(start_time) if start_time else None
        end = self._parse_time(end_time) if end_time else None
        result = []
        for partition in self.partitions(as_of=as_of):
            partition_start = self._parse_time(partition.min_event_time) if partition.min_event_time else None
            partition_end = self._parse_time(partition.max_event_time) if partition.max_event_time else None
            if start and partition_end and partition_end < start:
                continue
            if end and partition_start and partition_start > end:
                continue
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
        return resolve_managed_history_path(partition.file_uri, base_dir=BASE_DIR)

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
        stat = path.stat()
        state = (stat.st_size, stat.st_mtime_ns)
        if self._verified_partition_stats.get(partition.partition_id) == state:
            return
        if stat.st_size != partition.file_size:
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
            min_start: datetime | None = None
            max_start: datetime | None = None
            max_end: datetime | None = None
            for batch in parquet.iter_batches(
                columns=["bar_start_time", "bar_end_time", "available_time", "bar_status"],
                batch_size=65_536,
            ):
                for row in batch.to_pylist():
                    start_value = self._parse_time(row["bar_start_time"])
                    end_value = self._parse_time(row["bar_end_time"])
                    available_value = self._parse_time(row["available_time"])
                    min_start = start_value if min_start is None else min(min_start, start_value)
                    max_start = start_value if max_start is None else max(max_start, start_value)
                    max_end = end_value if max_end is None else max(max_end, end_value)
                    if str(row["bar_status"] or "").upper() != "COMPLETE":
                        raise ValueError(f"manifest contains an incomplete bar: {path}")
                    if available_value < end_value:
                        raise ValueError(f"bar available_time precedes bar_end_time: {path}")
            if min_start is not None and partition.min_event_time and min_start != self._parse_time(partition.min_event_time):
                raise ValueError(f"manifest partition minimum event time mismatch: {path}")
            if max_start is not None and partition.max_event_time and max_start != self._parse_time(partition.max_event_time):
                raise ValueError(f"manifest partition maximum event time mismatch: {path}")
            if min_start is not None and partition.start_time and min_start < self._parse_time(partition.start_time):
                raise ValueError(f"manifest partition starts before declared range: {path}")
            if max_end is not None and partition.end_time and max_end > self._parse_time(partition.end_time):
                raise ValueError(f"manifest partition ends after declared range: {path}")
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
            min_event: datetime | None = None
            max_event: datetime | None = None
            for batch in parquet.iter_batches(
                columns=["event_time", "available_time"], batch_size=65_536
            ):
                for row in batch.to_pylist():
                    event_value = self._parse_time(row["event_time"])
                    available_value = self._parse_time(row["available_time"])
                    min_event = event_value if min_event is None else min(min_event, event_value)
                    max_event = event_value if max_event is None else max(max_event, event_value)
                    if available_value < event_value:
                        raise ValueError(
                            f"price available_time precedes event_time: {path}"
                        )
            if (
                min_event is not None
                and partition.min_event_time
                and min_event != self._parse_time(partition.min_event_time)
            ):
                raise ValueError(
                    f"manifest partition minimum event time mismatch: {path}"
                )
            if (
                max_event is not None
                and partition.max_event_time
                and max_event != self._parse_time(partition.max_event_time)
            ):
                raise ValueError(
                    f"manifest partition maximum event time mismatch: {path}"
                )
        self._verified_partition_stats[partition.partition_id] = state

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

    def iter_rows(
        self,
        *,
        columns: Optional[list[str]] = None,
        as_of: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        instrument_ids: Optional[Iterable[str]] = None,
        batch_size: int = 65_536,
    ) -> Iterator[dict[str, Any]]:
        """Stream filtered rows from pinned partitions without a whole-table list.

        Date and instrument filters are applied to Arrow record batches. This is
        the safe read boundary for collection Manifests such as CRSP/CIZ.
        """
        try:
            import pyarrow as pa
            import pyarrow.compute as pc
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise RuntimeError("Parquet reads require pyarrow; install project requirements first") from exc
        if start_time and end_time and self._parse_time(start_time) > self._parse_time(end_time):
            raise ValueError("start_time must not be after end_time")
        selected_instruments = sorted({str(item).strip() for item in (instrument_ids or ()) if str(item).strip()})
        requested_columns = list(columns) if columns is not None else None
        read_columns = list(requested_columns) if requested_columns is not None else None
        filter_columns = []
        if as_of:
            filter_columns.append("available_time")
        if start_time or end_time:
            filter_columns.append(
                "bar_start_time" if self.manifest.schema_version.startswith("bars") else "event_time"
            )
        if selected_instruments:
            filter_columns.append("instrument_id")
        if read_columns is not None:
            for name in filter_columns:
                if name not in read_columns:
                    read_columns.append(name)
        cutoff = self._parse_time(as_of) if as_of else None
        start = self._parse_time(start_time) if start_time else None
        end = self._parse_time(end_time) if end_time else None

        def time_scalar(values: Any, value: datetime) -> Any:
            if pa.types.is_string(values.type) or pa.types.is_large_string(values.type):
                return pa.scalar(value.isoformat(), type=values.type)
            return pa.scalar(value, type=values.type)

        for partition in self._partitions_in_range(
            start_time=start_time, end_time=end_time, as_of=as_of
        ):
            self._verify_partition(partition)
            path = self._partition_path(partition)
            # Parquet paths use Hive-style directories such as
            # frequency=1m.  Reading a single file through read_table(path)
            # can merge the directory partition column with the same field
            # stored in the canonical schema and produce a type conflict.
            parquet = pq.ParquetFile(path)
            for batch in parquet.iter_batches(columns=read_columns, batch_size=max(1, int(batch_size))):
                mask = None
                if cutoff:
                    values = batch.column(batch.schema.get_field_index("available_time"))
                    item = pc.less_equal(values, time_scalar(values, cutoff))
                    mask = item if mask is None else pc.and_(mask, item)
                event_column = "bar_start_time" if "bar_start_time" in batch.schema.names else "event_time"
                if start:
                    values = batch.column(batch.schema.get_field_index(event_column))
                    item = pc.greater_equal(values, time_scalar(values, start))
                    mask = item if mask is None else pc.and_(mask, item)
                if end:
                    values = batch.column(batch.schema.get_field_index(event_column))
                    item = pc.less_equal(values, time_scalar(values, end))
                    mask = item if mask is None else pc.and_(mask, item)
                if selected_instruments:
                    values = batch.column(batch.schema.get_field_index("instrument_id"))
                    item = pc.is_in(values, value_set=pa.array(selected_instruments, type=values.type))
                    mask = item if mask is None else pc.and_(mask, item)
                if mask is not None:
                    batch = batch.filter(pc.fill_null(mask, False))

                # OPTIMIZATION: Delay to_pylist() conversion to reduce memory overhead.
                # Only convert to dict when absolutely necessary (i.e., when yielding).
                # This avoids creating intermediate Python objects for filtered-out rows.
                if requested_columns is not None:
                    # Only select needed columns at Arrow level (zero-copy)
                    batch = batch.select(requested_columns)

                for row in batch.to_pylist():
                    yield row

    def read_rows(
        self,
        *,
        columns: Optional[list[str]] = None,
        as_of: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        instrument_ids: Optional[Iterable[str]] = None,
    ) -> list[dict[str, Any]]:
        """Read filtered rows only from the pinned manifest partitions."""
        return list(self.iter_rows(
            columns=columns,
            as_of=as_of,
            start_time=start_time,
            end_time=end_time,
            instrument_ids=instrument_ids,
        ))

    def read_bars_by_instrument(
        self,
        *,
        columns: Optional[list[str]] = None,
        as_of: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        instrument_ids: Optional[Iterable[str]] = None,
    ) -> dict[str, list[dict[str, Any]]]:
        catalog_entry = self.catalog.get_catalog(self.dataset_id)
        fallback_instrument_id = (
            catalog_entry.instrument_id
            if catalog_entry is not None
            else ""
        )
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in self.iter_rows(
            columns=columns,
            as_of=as_of,
            start_time=start_time,
            end_time=end_time,
            instrument_ids=instrument_ids,
        ):
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
