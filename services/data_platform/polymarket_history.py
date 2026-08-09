from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.history_data_service import HISTORY_DB_PATH, download_polymarket_price_history

from .catalog_service import DatasetCatalogService
from .store import BASE_DIR, DataPlatformStore


POLYMARKET_PRICE_SCHEMA_VERSION = "polymarket_price.v1"
_FIDELITY_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iso_from_seconds(value: int) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def _seconds(value: str | None) -> int | None:
    if not value:
        return None
    return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())


class PolymarketHistoryPreparer:
    """Download CLOB outcome prices and publish them as a Research Manifest."""

    def __init__(self, store: DataPlatformStore, output_root: str | Path | None = None):
        self.store = store
        self.catalog = DatasetCatalogService(store)
        self.output_root = Path(output_root or (BASE_DIR / "storage" / "canonical"))

    def prepare(self, payload: dict[str, Any]) -> dict[str, Any]:
        instrument_id = str(payload.get("instrument_id") or "").strip()
        parts = instrument_id.split(":", 2)
        if len(parts) != 3 or parts[0].lower() != "polymarket_binary" or parts[1].upper() != "POLYMARKET":
            raise ValueError("instrument_id must identify a Polymarket outcome token")
        token_id = parts[2].strip()
        if not token_id:
            raise ValueError("Polymarket outcome token is required")
        interval = str(payload.get("interval") or "1h").strip().lower()
        if interval not in _FIDELITY_MINUTES:
            raise ValueError(f"unsupported Polymarket interval: {interval}")
        start_time = str(payload.get("start_time") or "").strip() or None
        end_time = str(payload.get("end_time") or "").strip() or datetime.now(timezone.utc).isoformat()
        latest_available = bool(payload.get("latest_available"))
        condition_id = str(payload.get("condition_id") or "").strip()

        download = download_polymarket_price_history({
            "token_id": token_id,
            "condition_id": condition_id,
            "start": start_time,
            "end": end_time,
            "interval": "max",
            "fidelity": str(_FIDELITY_MINUTES[interval]),
            "latest_available": latest_available,
        })
        rows = self._read_rows(token_id, start_time, end_time)
        if not rows:
            raise ValueError("Polymarket returned no price history for this outcome and range")
        committed = self._commit(
            instrument_id=instrument_id,
            token_id=token_id,
            condition_id=condition_id,
            frequency=interval,
            rows=rows,
        )
        return {"download": download, **committed}

    @staticmethod
    def _read_rows(token_id: str, start_time: str | None, end_time: str | None) -> list[dict[str, Any]]:
        clauses = ["token_id = ?"]
        params: list[Any] = [token_id]
        start_seconds = _seconds(start_time)
        end_seconds = _seconds(end_time)
        if start_seconds is not None:
            clauses.append("ts >= ?")
            params.append(start_seconds)
        if end_seconds is not None:
            clauses.append("ts <= ?")
            params.append(end_seconds)
        conn = sqlite3.connect(str(HISTORY_DB_PATH), timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            raw = conn.execute(
                f"SELECT ts, price, condition_id FROM polymarket_price_history WHERE {' AND '.join(clauses)} ORDER BY ts",
                params,
            ).fetchall()
        finally:
            conn.close()
        return [{
            "event_time": _iso_from_seconds(int(row["ts"])),
            "available_time": _iso_from_seconds(int(row["ts"])),
            "price": float(row["price"]),
            "condition_id": str(row["condition_id"] or ""),
            "token_id": token_id,
        } for row in raw]

    def _commit(
        self,
        *,
        instrument_id: str,
        token_id: str,
        condition_id: str,
        frequency: str,
        rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise RuntimeError("Polymarket Research export requires pyarrow") from exc

        rows = [{
            "instrument_id": instrument_id,
            "frequency": frequency,
            "event_time": str(row.get("event_time") or ""),
            "available_time": str(
                row.get("available_time")
                or row.get("event_time")
                or ""
            ),
            "price": float(row["price"]),
            "condition_id": str(row.get("condition_id") or condition_id),
            "token_id": str(row.get("token_id") or token_id),
        } for row in rows]
        token_hash = hashlib.sha256(token_id.encode("utf-8")).hexdigest()[:16]
        dataset_id = f"polymarket-price-{token_hash}-{frequency}"
        partition_root = self.output_root / "price_history" / "venue=POLYMARKET" / f"frequency={frequency}"
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            groups.setdefault(str(row["event_time"])[:7], []).append(row)
        partitions = []
        for month, month_rows in sorted(groups.items()):
            year, month_number = month.split("-")
            directory = partition_root / f"year={year}" / f"month={month_number}" / "objects"
            directory.mkdir(parents=True, exist_ok=True)
            table = pa.Table.from_pylist(month_rows)
            metadata = dict(table.schema.metadata or {})
            metadata[b"datatube_schema_version"] = POLYMARKET_PRICE_SCHEMA_VERSION.encode()
            table = table.replace_schema_metadata(metadata)
            temporary = directory / f".staging-{uuid.uuid4().hex}.parquet"
            pq.write_table(table, temporary, compression="zstd")
            part_hash = _sha256_file(temporary)
            target = directory / f"sha256-{part_hash}.parquet"
            if target.exists():
                temporary.unlink(missing_ok=True)
            else:
                temporary.rename(target)
            uri = target.relative_to(BASE_DIR).as_posix() if target.is_relative_to(BASE_DIR) else str(target)
            partitions.append({
                "partition_key": month,
                "start_time": month_rows[0]["event_time"],
                "end_time": month_rows[-1]["event_time"],
                "row_count": len(month_rows),
                "file_uri": uri,
                "file_size": target.stat().st_size,
                "checksum": f"sha256:{part_hash}",
                "min_event_time": month_rows[0]["event_time"],
                "max_event_time": month_rows[-1]["event_time"],
                "quality_status": "PASS",
            })
        start_time = rows[0]["event_time"]
        end_time = rows[-1]["event_time"]
        fingerprint = hashlib.sha256(json.dumps({
            "dataset_id": dataset_id,
            "instrument_id": instrument_id,
            "frequency": frequency,
            "start_time": start_time,
            "end_time": end_time,
            "partitions": [{"key": row["partition_key"], "checksum": row["checksum"]} for row in partitions],
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        catalog = self.catalog.upsert_catalog({
            "dataset_id": dataset_id,
            "instrument_id": instrument_id,
            "data_type": "price_history",
            "frequency": frequency,
            "source": "polymarket_clob",
            "start_time": start_time,
            "end_time": end_time,
            "last_complete_time": end_time,
            "row_count": len(rows),
            "gap_count": 0,
            "status": "PARTIAL",
            "quality_status": "PASS",
            "schema_version": POLYMARKET_PRICE_SCHEMA_VERSION,
            "storage_path": partition_root.relative_to(BASE_DIR).as_posix() if partition_root.is_relative_to(BASE_DIR) else str(partition_root),
            "fields": ["price"],
            "adjustment": "NONE",
            "time_semantics": "EVENT_TIME_AVAILABLE_TIME",
            "point_in_time_policy": "AS_OF",
            "metadata": {"token_id": token_id, "condition_id": condition_id},
        })
        manifest = self.catalog.commit_manifest(
            dataset_id=dataset_id,
            dataset_fingerprint=fingerprint,
            schema_version=POLYMARKET_PRICE_SCHEMA_VERSION,
            partitions=partitions,
        )
        return {
            "dataset_id": dataset_id,
            "row_count": len(rows),
            "start_time": start_time,
            "end_time": end_time,
            "catalog": asdict(self.catalog.get_catalog(dataset_id) or catalog),
            "manifest": asdict(manifest),
        }
