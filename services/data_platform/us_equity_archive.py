from __future__ import annotations

import csv
import hashlib
import json
import re
import zipfile
from dataclasses import asdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .canonical_bars import CanonicalBarsCommitter
from .instrument_registry import InstrumentRegistry, make_instrument_id
from .models import Instrument
from .provenance_service import ManifestProvenanceService
from .store import DataPlatformStore, get_default_store


LOCAL_DAILY_ADAPTER_VERSION = "local_daily_snapshots.v1"
INVENTORY_SCHEMA_VERSION = "us_equity_archive_inventory.v1"
DAILY_STOCK_ENTRY = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})stocks\.csv$", re.IGNORECASE)
DAILY_OPTION_ENTRY = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})options\.csv$", re.IGNORECASE)
QUARTERLY_OPTION_ENTRY = re.compile(r"_option_chain\.txt$", re.IGNORECASE)


def _iso_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).replace(microsecond=0).isoformat()


def _stable_hash(value: Any) -> str:
    material = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _archive_descriptor(root: Path, path: Path, archive: zipfile.ZipFile) -> dict[str, Any]:
    daily_stock_dates: list[str] = []
    daily_option_dates: list[str] = []
    quarterly_options = 0
    nested_zip_entries: list[dict[str, Any]] = []
    uncompressed_bytes = 0
    for entry in archive.infolist():
        if entry.is_dir():
            continue
        uncompressed_bytes += int(entry.file_size)
        name = Path(entry.filename).name
        stock_match = DAILY_STOCK_ENTRY.match(name)
        option_match = DAILY_OPTION_ENTRY.match(name)
        if stock_match:
            daily_stock_dates.append(stock_match.group("date"))
        elif option_match:
            daily_option_dates.append(option_match.group("date"))
        elif QUARTERLY_OPTION_ENTRY.search(name):
            quarterly_options += 1
        if name.lower().endswith(".zip"):
            nested_zip_entries.append({
                "name": entry.filename,
                "compressed_bytes": int(entry.compress_size),
                "uncompressed_bytes": int(entry.file_size),
                "crc32": f"{entry.CRC:08x}",
            })
    descriptor = {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "mtime_utc": _iso_mtime(path),
        "zip_entries": len(archive.infolist()),
        "uncompressed_bytes": uncompressed_bytes,
        "daily_stock_entries": len(daily_stock_dates),
        "daily_option_entries": len(daily_option_dates),
        "quarterly_option_entries": quarterly_options,
        "stock_date_start": min(daily_stock_dates) if daily_stock_dates else None,
        "stock_date_end": max(daily_stock_dates) if daily_stock_dates else None,
        "option_date_start": min(daily_option_dates) if daily_option_dates else None,
        "option_date_end": max(daily_option_dates) if daily_option_dates else None,
        "nested_zip_entries": nested_zip_entries,
    }
    descriptor["central_directory_fingerprint"] = _stable_hash({
        "path": descriptor["path"],
        "bytes": descriptor["bytes"],
        "mtime_utc": descriptor["mtime_utc"],
        "entries": [
            (item.filename, item.file_size, item.compress_size, item.CRC)
            for item in archive.infolist()
        ],
    })
    return descriptor


def scan_us_equity_archive(source_root: str | Path) -> dict[str, Any]:
    """Inventory ZIP structures without extracting the underlying market data."""

    root = Path(source_root).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"US-equity archive root does not exist: {root}")
    archives: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for path in sorted(root.rglob("*.zip")):
        # Files created by this integration are derived/staging material, not
        # another upstream source to recursively inventory.
        if ".datatube" in {part.lower() for part in path.relative_to(root).parts}:
            continue
        try:
            with zipfile.ZipFile(path) as archive:
                archives.append(_archive_descriptor(root, path, archive))
        except (OSError, zipfile.BadZipFile) as exc:
            failures.append({"path": path.relative_to(root).as_posix(), "error": str(exc)})

    stock_archives = [item for item in archives if item["daily_stock_entries"]]
    daily_option_archives = [item for item in archives if item["daily_option_entries"]]
    quarterly_option_archives = [item for item in archives if item["quarterly_option_entries"]]
    stock_starts = [item["stock_date_start"] for item in stock_archives if item["stock_date_start"]]
    stock_ends = [item["stock_date_end"] for item in stock_archives if item["stock_date_end"]]
    option_starts = [item["option_date_start"] for item in daily_option_archives if item["option_date_start"]]
    option_ends = [item["option_date_end"] for item in daily_option_archives if item["option_date_end"]]
    inventory = {
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source_root": str(root),
        "summary": {
            "archive_count": len(archives),
            "archive_bytes": sum(int(item["bytes"]) for item in archives),
            "archive_failures": len(failures),
            "daily_stock_archives": len(stock_archives),
            "daily_stock_entries": sum(int(item["daily_stock_entries"]) for item in stock_archives),
            "daily_stock_start": min(stock_starts) if stock_starts else None,
            "daily_stock_end": max(stock_ends) if stock_ends else None,
            "daily_option_archives": len(daily_option_archives),
            "daily_option_entries": sum(int(item["daily_option_entries"]) for item in daily_option_archives),
            "daily_option_start": min(option_starts) if option_starts else None,
            "daily_option_end": max(option_ends) if option_ends else None,
            "quarterly_option_archives": len(quarterly_option_archives),
            "quarterly_option_entries": sum(int(item["quarterly_option_entries"]) for item in quarterly_option_archives),
            "crsp_outer_archives": sum(1 for item in archives if item["nested_zip_entries"]),
        },
        "archives": archives,
        "failures": failures,
        "routing": {
            "daily_stocks": "READY_FOR_BARS_V1_IMPORT",
            "crsp_daily": "POINT_IN_TIME_MASTER_DATA_REQUIRES_STREAMING_NORMALIZATION",
            "daily_options": "OPTION_CHAIN_EOD_SCHEMA_REQUIRED",
            "quarterly_options": "OPTION_CHAIN_EOD_SCHEMA_REQUIRED",
        },
    }
    inventory["inventory_fingerprint"] = _stable_hash({
        "schema_version": inventory["schema_version"],
        "archives": [item["central_directory_fingerprint"] for item in archives],
        "failures": failures,
    })
    return inventory


def write_archive_inventory(inventory: Mapping[str, Any], data_root: str | Path) -> Path:
    root = Path(data_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / "inventory.json"
    staging = root / ".inventory.json.staging"
    staging.write_text(json.dumps(dict(inventory), ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    staging.replace(path)
    return path


def _parse_date(value: str) -> date:
    return date.fromisoformat(str(value).strip()[:10])


def _finite_number(value: Any, *, field: str, symbol: str, event_date: date, positive: bool) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{symbol} has invalid {field} at {event_date}") from exc
    if positive and number <= 0:
        raise ValueError(f"{symbol} has non-positive {field} at {event_date}")
    if not positive and number < 0:
        raise ValueError(f"{symbol} has negative {field} at {event_date}")
    return number


class DailySnapshotEquityImporter:
    """Import selected symbols from daily ZIP snapshots into immutable bars.v1."""

    def __init__(
        self,
        source_root: str | Path,
        data_root: str | Path,
        *,
        store: DataPlatformStore | None = None,
    ):
        self.source_root = Path(source_root).expanduser().resolve()
        self.data_root = Path(data_root).expanduser().resolve()
        if not self.source_root.is_dir():
            raise FileNotFoundError(f"US-equity archive root does not exist: {self.source_root}")
        self.store = store or get_default_store()
        self.committer = CanonicalBarsCommitter(self.store, self.data_root / "canonical")
        self.registry = InstrumentRegistry(self.store)
        self.provenance = ManifestProvenanceService(self.store)

    def _archives(self) -> Iterable[Path]:
        snapshot_root = self.source_root / "daily-snapshots"
        if not snapshot_root.is_dir():
            raise FileNotFoundError(f"daily-snapshots directory does not exist: {snapshot_root}")
        yield from sorted(snapshot_root.rglob("*.zip"))

    @staticmethod
    def _canonical_row(
        raw: Mapping[str, Any],
        *,
        symbol: str,
        instrument_id: str,
        event_date: date,
        ingested_at: str,
        source_record: str,
    ) -> dict[str, Any]:
        open_price = _finite_number(raw.get("open"), field="open", symbol=symbol, event_date=event_date, positive=True)
        high = _finite_number(raw.get("high"), field="high", symbol=symbol, event_date=event_date, positive=True)
        low = _finite_number(raw.get("low"), field="low", symbol=symbol, event_date=event_date, positive=True)
        close = _finite_number(raw.get("close"), field="close", symbol=symbol, event_date=event_date, positive=True)
        volume = _finite_number(raw.get("volume") or 0, field="volume", symbol=symbol, event_date=event_date, positive=False)
        if high < low or not low <= open_price <= high or not low <= close <= high:
            raise ValueError(f"{symbol} has invalid OHLC range at {event_date}")
        bar_start = datetime.combine(event_date, time.min, tzinfo=timezone.utc)
        available = datetime.combine(event_date + timedelta(days=1), time.min, tzinfo=timezone.utc)
        return {
            "instrument_id": instrument_id,
            "frequency": "1d",
            "bar_start_time": bar_start.isoformat(),
            "bar_end_time": available.isoformat(),
            "available_time": available.isoformat(),
            "ingested_at": ingested_at,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "turnover": 0.0,
            "trade_count": 0,
            "bar_status": "COMPLETE",
            "source": "LOCAL/DAILY_SNAPSHOTS",
            "source_version": LOCAL_DAILY_ADAPTER_VERSION,
            "quality_status": "PASS",
            "native_symbol": symbol,
            "source_record": source_record,
        }

    def import_symbols(
        self,
        symbols: Sequence[str],
        *,
        venues: Mapping[str, str] | None = None,
        start_date: str = "",
        end_date: str = "",
    ) -> dict[str, Any]:
        requested = sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()})
        if not requested:
            raise ValueError("at least one symbol is required")
        venue_map = {str(key).strip().upper(): str(value).strip().upper() for key, value in (venues or {}).items()}
        missing_venues = [symbol for symbol in requested if venue_map.get(symbol) not in {"XNAS", "XNYS", "XASE", "ARCX", "US"}]
        if missing_venues:
            raise ValueError(f"explicit venue is required for: {', '.join(missing_venues)}")
        start = _parse_date(start_date) if start_date else None
        end = _parse_date(end_date) if end_date else None
        if start and end and start > end:
            raise ValueError("start_date must not be after end_date")

        rows_by_symbol: dict[str, dict[date, dict[str, Any]]] = {symbol: {} for symbol in requested}
        source_entries: list[dict[str, Any]] = []
        for archive_path in self._archives():
            archive_ingested_at = _iso_mtime(archive_path)
            with zipfile.ZipFile(archive_path) as archive:
                for entry in archive.infolist():
                    match = DAILY_STOCK_ENTRY.match(Path(entry.filename).name)
                    if not match:
                        continue
                    event_date = _parse_date(match.group("date"))
                    if start and event_date < start:
                        continue
                    if end and event_date > end:
                        continue
                    source_record = f"{archive_path.relative_to(self.source_root).as_posix()}!/{entry.filename}"
                    matched = 0
                    with archive.open(entry) as binary:
                        text = (line.decode("utf-8-sig") for line in binary)
                        reader = csv.DictReader(text)
                        required = {"symbol", "open", "high", "low", "close", "volume"}
                        if not required.issubset(set(reader.fieldnames or [])):
                            raise ValueError(f"daily stock snapshot has invalid header: {source_record}")
                        for raw in reader:
                            symbol = str(raw.get("symbol") or "").strip().upper()
                            if symbol not in rows_by_symbol:
                                continue
                            instrument_id = make_instrument_id("equity", venue_map[symbol], symbol)
                            row = self._canonical_row(
                                raw,
                                symbol=symbol,
                                instrument_id=instrument_id,
                                event_date=event_date,
                                ingested_at=archive_ingested_at,
                                source_record=source_record,
                            )
                            existing = rows_by_symbol[symbol].get(event_date)
                            if existing:
                                comparable = ("open", "high", "low", "close", "volume")
                                if any(existing[key] != row[key] for key in comparable):
                                    raise ValueError(f"conflicting duplicate daily bar for {symbol} at {event_date}")
                                continue
                            rows_by_symbol[symbol][event_date] = row
                            matched += 1
                    if matched:
                        source_entries.append({
                            "record": source_record,
                            "crc32": f"{entry.CRC:08x}",
                            "compressed_bytes": int(entry.compress_size),
                            "uncompressed_bytes": int(entry.file_size),
                        })

        source_fingerprint = _stable_hash(source_entries)
        results: list[dict[str, Any]] = []
        for symbol in requested:
            rows = [rows_by_symbol[symbol][key] for key in sorted(rows_by_symbol[symbol])]
            if not rows:
                raise ValueError(f"no daily snapshot rows found for {symbol}")
            venue = venue_map[symbol]
            instrument_id = make_instrument_id("equity", venue, symbol)
            if not self.registry.get(instrument_id):
                self.registry.register(
                    Instrument(
                        instrument_id=instrument_id,
                        asset_class="equity",
                        venue=venue,
                        market_type="EQUITY",
                        native_symbol=symbol,
                        display_symbol=symbol,
                        currency="USD",
                        timezone="America/New_York",
                        trading_calendar=venue if venue != "US" else "XNYS",
                        metadata={
                            "local_archive": True,
                            "source_root": str(self.source_root),
                            "source_fingerprint": source_fingerprint,
                        },
                    ),
                    aliases=[("local:daily_snapshots", symbol)],
                )
            dataset_id = f"local:daily_snapshots:{instrument_id}:bars:1d:unadjusted"
            committed = self.committer.commit(
                dataset_id=dataset_id,
                instrument_id=instrument_id,
                asset_class="equity",
                venue=venue,
                frequency="1d",
                source="LOCAL/DAILY_SNAPSHOTS",
                source_version=f"{LOCAL_DAILY_ADAPTER_VERSION}:{source_fingerprint[:16]}",
                rows=rows,
                adjustment="NONE",
                time_semantics="BAR_END_AVAILABLE_TIME",
                point_in_time_policy="AS_OF",
            )
            provenance = self.provenance.record(
                manifest_id=committed["manifest"].manifest_id,
                dataset_id=dataset_id,
                gateway="LOCAL_ARCHIVE",
                upstream_provider="daily_snapshots",
                endpoint="zip/daily-stock-csv",
                request={
                    "source_root": str(self.source_root),
                    "symbol": symbol,
                    "venue": venue,
                    "start_date": start.isoformat() if start else "",
                    "end_date": end.isoformat() if end else "",
                    "source_fingerprint": source_fingerprint,
                },
                gateway_version=LOCAL_DAILY_ADAPTER_VERSION,
                provider_version=source_fingerprint[:16],
            )
            results.append({
                "symbol": symbol,
                "venue": venue,
                "instrument_id": instrument_id,
                "dataset_id": dataset_id,
                "row_count": committed["row_count"],
                "start_time": committed["start_time"],
                "end_time": committed["end_time"],
                "manifest": asdict(committed["manifest"]),
                "catalog": asdict(committed["catalog"]),
                "provenance": provenance,
            })
        return {
            "status": "READY",
            "source_root": str(self.source_root),
            "data_root": str(self.data_root),
            "source_fingerprint": source_fingerprint,
            "source_entry_count": len(source_entries),
            "datasets": results,
        }
