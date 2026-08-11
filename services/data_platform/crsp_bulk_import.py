from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from services.history_storage_service import get_data_platform_canonical_root

from .catalog_service import DatasetCatalogService
from .crsp_ciz import CRSP_CIZ_NORMALIZER_VERSION
from .store import BASE_DIR, DataPlatformStore, json_dumps, utc_now


BULK_IMPORT_VERSION = "crsp_ciz_bulk_import.v1"
DATASET_CONTRACTS = {
    "security_master": ("security_master", "security_master.v1", "event", "event_time", "NONE"),
    "bars": ("bars", "bars_daily.v2", "1d", "bar_start_time", "CRSP_FIELDS"),
    "valuation": ("equity_valuation_daily", "equity_valuation_daily.v1", "1d", "event_time", "NONE"),
    "corporate_actions": ("corporate_actions", "corporate_actions.v1", "event", "event_time", "NONE"),
}

SOURCE_COLUMNS = [
    "PERMNO", "PERMCO", "SecInfoStartDt", "SecInfoEndDt", "SecurityBegDt",
    "SecurityEndDt", "CUSIP", "CUSIP9", "HdrCUSIP", "HdrCUSIP9",
    "PrimaryExch", "SecurityNm", "ShareClass", "SecurityType", "ShareType",
    "SecurityActiveFlg", "Ticker", "TradingSymbol", "SICCD", "NAICS", "IssuerNm",
    "YYYYMMDD", "DlyCalDt", "DlyDelFlg", "DlyPrc", "DlyCap", "DlyCapFlg",
    "DlyRet", "DlyRetx", "DlyRetI", "DlyOrdDivAmt", "DlyNonOrdDivAmt",
    "DlyFacPrc", "DlyVol", "DlyClose", "DlyLow", "DlyHigh", "DlyOpen",
    "DlyNumTrd", "DlyPrcVol", "ShrOut", "ShrSource", "DisExDt", "DisSeqNbr", "DisType",
    "DisDetailType", "DisDivAmt", "DisFacPr", "DisFacShr", "DisDeclareDt",
    "DisRecordDt", "DisPayDt", "DelActionType",
]


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_descriptor(path: Path, entry_name: str = "") -> dict[str, Any]:
    source = path.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    stat = source.stat()
    with zipfile.ZipFile(source) as archive:
        entries = [item for item in archive.infolist() if item.filename.lower().endswith(".csv")]
        selected = next((item for item in entries if item.filename == entry_name), None)
        if selected is None and len(entries) == 1 and not entry_name:
            selected = entries[0]
        if selected is None:
            raise ValueError("CRSP ZIP requires one explicit CSV entry")
        descriptor = {
            "source_path": str(source),
            "archive_size": stat.st_size,
            "archive_mtime_ns": stat.st_mtime_ns,
            "entry_name": selected.filename,
            "entry_crc32": f"{selected.CRC:08x}",
            "entry_size": selected.file_size,
            "entry_compressed_size": selected.compress_size,
        }
    descriptor["fingerprint"] = hashlib.sha256(
        json_dumps(descriptor).encode("utf-8")
    ).hexdigest()
    return descriptor


class CrspBulkImportService:
    """Persistent control plane for one bounded-memory CRSP CIZ full import."""

    def __init__(self, store: DataPlatformStore, *, canonical_root: str | Path | None = None):
        self.store = store
        self.catalog = DatasetCatalogService(store)
        self.canonical_root = Path(canonical_root or get_data_platform_canonical_root()).resolve()

    def create(
        self,
        *,
        source_path: str | Path,
        source_entry: str = "",
        dataset_prefix: str = "crsp:ciz:full",
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        descriptor = _source_descriptor(Path(source_path), source_entry)
        request_material = {
            "source_fingerprint": descriptor["fingerprint"],
            "dataset_prefix": _clean(dataset_prefix),
            "normalizer_version": BULK_IMPORT_VERSION,
        }
        key = _clean(idempotency_key) or "crsp-full:" + hashlib.sha256(
            json_dumps(request_material).encode("utf-8")
        ).hexdigest()
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            existing = conn.execute(
                "SELECT * FROM crsp_import_jobs WHERE idempotency_key=?", (key,)
            ).fetchone()
            if existing:
                if str(existing["source_fingerprint"]) != descriptor["fingerprint"]:
                    raise ValueError("CRSP import idempotency key was reused for another source")
                return self._decode(existing)
            job_id = f"crsp_import_{uuid.uuid4().hex}"
            staging_root = self.canonical_root / "crsp_ciz" / f"import={job_id}"
            conn.execute(
                """INSERT INTO crsp_import_jobs(
                       job_id,idempotency_key,source_path,source_entry,source_fingerprint,
                       dataset_prefix,normalizer_version,status,staging_root,checkpoint_json,
                       created_at,updated_at
                   ) VALUES (?,?,?,?,?,?,?,'QUEUED',?,?,?,?)""",
                (
                    job_id, key, descriptor["source_path"], descriptor["entry_name"],
                    descriptor["fingerprint"], _clean(dataset_prefix), BULK_IMPORT_VERSION,
                    str(staging_root), json_dumps({"source": descriptor}), now, now,
                ),
            )
            row = conn.execute("SELECT * FROM crsp_import_jobs WHERE job_id=?", (job_id,)).fetchone()
        return self._decode(row)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM crsp_import_jobs WHERE job_id=?", (_clean(job_id),)
            ).fetchone()
        return self._decode(row) if row else None

    def list(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM crsp_import_jobs ORDER BY created_at DESC LIMIT ?",
                (max(1, min(int(limit), 200)),),
            ).fetchall()
        return [self._decode(row) for row in rows]

    def resume(self, job_id: str) -> dict[str, Any]:
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            row = conn.execute("SELECT * FROM crsp_import_jobs WHERE job_id=?", (_clean(job_id),)).fetchone()
            if row is None:
                raise ValueError("CRSP import job not found")
            if str(row["status"]) == "READY":
                return self._decode(row)
            conn.execute(
                "UPDATE crsp_import_jobs SET status='QUEUED',worker_id='',error='',updated_at=? WHERE job_id=?",
                (now, _clean(job_id)),
            )
        result = self.get(job_id)
        if result is None:
            raise RuntimeError("failed to resume CRSP import")
        return result

    @staticmethod
    def _decode(row: Any) -> dict[str, Any]:
        result = dict(row)
        for name in ("output_counts_json", "checkpoint_json", "manifests_json"):
            result[name.removesuffix("_json")] = json.loads(result.pop(name) or "{}")
        return result

    def _claim(self, job_id: str, worker_id: str) -> dict[str, Any]:
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            row = conn.execute("SELECT * FROM crsp_import_jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise ValueError("CRSP import job not found")
            if str(row["status"]) == "READY":
                return self._decode(row)
            heartbeat = str(row["heartbeat_at"] or "")
            if str(row["status"]) == "RUNNING" and heartbeat:
                age = datetime.now(timezone.utc) - datetime.fromisoformat(heartbeat)
                if age.total_seconds() < 600 and str(row["worker_id"]) != worker_id:
                    raise RuntimeError("CRSP import job is already running")
            conn.execute(
                """UPDATE crsp_import_jobs SET status='RUNNING',worker_id=?,heartbeat_at=?,
                       error='',updated_at=? WHERE job_id=?""",
                (worker_id, now, now, job_id),
            )
        result = self.get(job_id)
        if result is None:
            raise RuntimeError("failed to claim CRSP import")
        return result

    def run(self, job_id: str, *, worker_id: str = "", chunk_rows: int = 250_000) -> dict[str, Any]:
        worker_id = _clean(worker_id) or f"crsp-worker-{os.getpid()}"
        job = self._claim(_clean(job_id), worker_id)
        if job["status"] == "READY":
            return job
        try:
            descriptor = _source_descriptor(Path(job["source_path"]), job["source_entry"])
            if descriptor["fingerprint"] != job["source_fingerprint"]:
                raise RuntimeError("CRSP source changed after the import job was created")
            staging = Path(job["staging_root"])
            staging.mkdir(parents=True, exist_ok=True)
            identity_db = staging / "identity.sqlite"
            self._initialize_identity(identity_db)
            checkpoint = dict(job["checkpoint"] or {})
            rows_processed = int(job["rows_processed"])
            chunk_index = int(job["chunk_count"])
            counts = {key: int(value) for key, value in (job["output_counts"] or {}).items()}
            last_key = tuple(checkpoint.get("last_key") or ())

            for table in self._source_chunks(
                Path(job["source_path"]), job["source_entry"],
                skip_rows=rows_processed, chunk_rows=max(10_000, int(chunk_rows)),
            ):
                normalized, identity, stats = self._normalize(table, last_key=last_key)
                next_index = chunk_index + 1
                partitions: dict[str, dict[str, Any]] = {}
                for dataset_key in ("bars", "valuation", "corporate_actions"):
                    frame = normalized[dataset_key]
                    if not frame.empty:
                        partitions[dataset_key] = self._write_partition(
                            staging, dataset_key, next_index, frame,
                            DATASET_CONTRACTS[dataset_key][1], DATASET_CONTRACTS[dataset_key][3],
                        )
                self._upsert_identity(identity_db, identity)
                rows_processed += int(stats["source_rows"])
                chunk_index = next_index
                last_key = tuple(stats["last_key"])
                for key, frame in normalized.items():
                    counts[key] = counts.get(key, 0) + len(frame)
                next_checkpoint = dict(checkpoint)
                for counter in ("invalid_identity_rows", "sanitized_ohlc_rows"):
                    next_checkpoint[counter] = int(checkpoint.get(counter, 0)) + int(stats.get(counter, 0))
                for minimum in ("min_event_time",):
                    values = [value for value in (checkpoint.get(minimum), stats.get(minimum)) if value]
                    next_checkpoint[minimum] = min(values) if values else None
                for maximum in ("max_event_time", "last_available_time"):
                    values = [value for value in (checkpoint.get(maximum), stats.get(maximum)) if value]
                    next_checkpoint[maximum] = max(values) if values else None
                next_checkpoint["last_key"] = list(last_key)
                self._checkpoint(
                    job["job_id"], worker_id, rows_processed, chunk_index, counts,
                    partitions, next_checkpoint,
                )
                checkpoint = next_checkpoint

            self._set_status(job["job_id"], "FINALIZING", worker_id=worker_id)
            master = self._load_master_frame(identity_db)
            if master.empty:
                raise RuntimeError("CRSP import produced an empty security master")
            master_partition = self._write_partition(
                staging, "security_master", 0, master,
                DATASET_CONTRACTS["security_master"][1], "event_time",
            )
            self._checkpoint_master(job["job_id"], master_partition, len(master), worker_id)
            counts["security_master"] = len(master)
            self._publish_identity(identity_db)
            published = self._publish(job["job_id"], counts, checkpoint)
            now = utc_now()
            with self.store.transaction(immediate=True) as conn:
                conn.execute(
                    """UPDATE crsp_import_jobs SET status='READY',output_counts_json=?,
                           manifests_json=?,heartbeat_at=?,updated_at=?,completed_at=?,error=''
                       WHERE job_id=?""",
                    (json_dumps(counts), json_dumps(published), now, now, now, job["job_id"]),
                )
            result = self.get(job["job_id"])
            if result is None:
                raise RuntimeError("CRSP import disappeared after completion")
            return result
        except Exception as exc:
            now = utc_now()
            with self.store.transaction(immediate=True) as conn:
                conn.execute(
                    "UPDATE crsp_import_jobs SET status='FAILED',error=?,heartbeat_at=?,updated_at=? WHERE job_id=?",
                    (f"{type(exc).__name__}: {exc}", now, now, _clean(job_id)),
                )
            raise

    @staticmethod
    def _source_chunks(
        source: Path,
        entry_name: str,
        *,
        skip_rows: int,
        chunk_rows: int,
    ) -> Iterable[Any]:
        try:
            import pyarrow as pa
            import pyarrow.csv as pacsv
        except ImportError as exc:
            raise RuntimeError("CRSP bulk import requires pyarrow") from exc
        with zipfile.ZipFile(source) as archive, archive.open(entry_name) as binary:
            reader = pacsv.open_csv(
                pa.PythonFile(binary),
                read_options=pacsv.ReadOptions(block_size=64 * 1024 * 1024, use_threads=True),
                convert_options=pacsv.ConvertOptions(
                    include_columns=SOURCE_COLUMNS,
                    column_types={name: pa.string() for name in SOURCE_COLUMNS},
                    strings_can_be_null=True,
                    null_values=[""],
                ),
            )
            remaining_skip = max(0, int(skip_rows))
            pieces: list[Any] = []
            piece_rows = 0
            for batch in reader:
                if remaining_skip:
                    amount = min(remaining_skip, batch.num_rows)
                    batch = batch.slice(amount)
                    remaining_skip -= amount
                    if batch.num_rows == 0:
                        continue
                offset = 0
                while offset < batch.num_rows:
                    amount = min(chunk_rows - piece_rows, batch.num_rows - offset)
                    pieces.append(batch.slice(offset, amount))
                    piece_rows += amount
                    offset += amount
                    if piece_rows == chunk_rows:
                        yield pa.Table.from_batches(pieces).combine_chunks()
                        pieces, piece_rows = [], 0
            if remaining_skip:
                raise RuntimeError("CRSP checkpoint is beyond the end of the source")
            if pieces:
                yield pa.Table.from_batches(pieces).combine_chunks()

    @staticmethod
    def _normalize(table: Any, *, last_key: tuple[Any, ...] = ()) -> tuple[dict[str, Any], Any, dict[str, Any]]:
        import pandas as pd

        raw = table.to_pandas(strings_to_categorical=False)
        source_rows = len(raw)

        def text(name: str) -> Any:
            return raw[name].fillna("").astype(str).str.strip()

        def number(name: str) -> Any:
            return pd.to_numeric(text(name), errors="coerce")

        def day(name: str, *, compact: bool = False) -> Any:
            return pd.to_datetime(text(name), format="%Y%m%d" if compact else "%Y-%m-%d", errors="coerce")

        permno = pd.to_numeric(text("PERMNO"), errors="coerce").astype("Int64")
        event = day("YYYYMMDD", compact=True).fillna(day("DlyCalDt"))
        valid = permno.notna() & event.notna() & (permno > 0)
        invalid_rows = int((~valid).sum())
        raw = raw.loc[valid].reset_index(drop=True)
        permno = permno.loc[valid].reset_index(drop=True)
        event = event.loc[valid].reset_index(drop=True)
        if raw.empty:
            empty = pd.DataFrame()
            return {"bars": empty, "valuation": empty, "corporate_actions": empty}, empty, {
                "source_rows": source_rows, "invalid_identity_rows": invalid_rows,
                "last_key": list(last_key),
            }

        def text2(name: str) -> Any:
            return raw[name].fillna("").astype(str).str.strip()

        def number2(name: str) -> Any:
            return pd.to_numeric(text2(name), errors="coerce")

        def day2(name: str, *, compact: bool = False) -> Any:
            return pd.to_datetime(text2(name), format="%Y%m%d" if compact else "%Y-%m-%d", errors="coerce")

        numeric_date = event.dt.strftime("%Y%m%d").astype("int64")
        key_frame = pd.DataFrame({"permno": permno.astype("int64"), "day": numeric_date})
        index = pd.MultiIndex.from_frame(key_frame)
        if not index.is_monotonic_increasing:
            raise ValueError("CRSP source is not ordered by PERMNO and trading date")
        first_key = (int(key_frame.iloc[0, 0]), int(key_frame.iloc[0, 1]))
        prior_key = (int(last_key[0]), int(last_key[1])) if last_key else None
        if prior_key and first_key < prior_key:
            raise ValueError("CRSP resume boundary precedes an already committed source row")
        final_key = (int(key_frame.iloc[-1, 0]), int(key_frame.iloc[-1, 1]))

        sid = "crsp:permno:" + permno.astype("int64").astype(str)
        iid = "equity:CRSP:" + permno.astype("int64").astype(str)
        ticker = text2("Ticker").where(text2("Ticker") != "", text2("TradingSymbol")).str.upper()
        event_stamp = event.dt.strftime("%Y-%m-%dT00:00:00+00:00")
        available = (event + pd.Timedelta(days=1)).dt.strftime("%Y-%m-%dT00:00:00+00:00")

        prices = {name: number2(name).abs() for name in ("DlyOpen", "DlyHigh", "DlyLow", "DlyClose", "DlyPrc")}
        close = prices["DlyClose"].fillna(prices["DlyPrc"])
        bars = pd.DataFrame({
            "security_id": sid, "instrument_id": iid, "native_ticker": ticker,
            "frequency": "1d", "bar_start_time": event_stamp, "bar_end_time": available,
            "available_time": available, "open": prices["DlyOpen"], "high": prices["DlyHigh"],
            "low": prices["DlyLow"], "close": close, "volume": number2("DlyVol"),
            "turnover": number2("DlyPrcVol"), "trade_count": number2("DlyNumTrd").astype("Int64"),
            "total_return": number2("DlyRet"), "price_return": number2("DlyRetx"),
            "income_return": number2("DlyRetI"), "price_adjustment_factor": number2("DlyFacPrc"),
            "bar_status": "COMPLETE", "source": "CRSP/CIZ", "quality_status": "PASS",
        })
        bars = bars.loc[bars[["open", "high", "low", "close", "volume"]].notna().any(axis=1)].reset_index(drop=True)
        bar_key = ["instrument_id", "bar_start_time"]
        if bars.duplicated(bar_key, keep=False).any():
            conflict = bars.loc[bars.duplicated(bar_key, keep=False)].groupby(bar_key, sort=False)[
                ["open", "high", "low", "close", "volume", "turnover", "trade_count",
                 "total_return", "price_return", "income_return", "price_adjustment_factor"]
            ].nunique(dropna=True).gt(1).any(axis=1)
            if conflict.any():
                raise ValueError("CRSP duplicate trading-date rows disagree on bar fields")
            bars = bars.drop_duplicates(bar_key, keep="last").reset_index(drop=True)
        if prior_key:
            bar_permno = bars["security_id"].str.rsplit(":", n=1).str[-1].astype(int)
            bar_day = bars["bar_start_time"].str[:10].str.replace("-", "", regex=False).astype(int)
            bars = bars.loc[
                (bar_permno > prior_key[0])
                | ((bar_permno == prior_key[0]) & (bar_day > prior_key[1]))
            ].reset_index(drop=True)
        invalid_range = bars["high"].notna() & bars["low"].notna() & (bars["high"] < bars["low"])
        invalid_open = bars["open"].notna() & bars["high"].notna() & bars["low"].notna() & (
            (bars["open"] > bars["high"]) | (bars["open"] < bars["low"])
        )
        invalid_close = bars["close"].notna() & bars["high"].notna() & bars["low"].notna() & (
            (bars["close"] > bars["high"]) | (bars["close"] < bars["low"])
        )
        sanitized_ohlc_rows = int((invalid_range | invalid_open | invalid_close).sum())
        # Very old CRSP observations occasionally contain internally inconsistent
        # optional high/low/open fields.  Preserve the official close and returns,
        # null only the contradictory optional fields, and count every repair.
        bars.loc[invalid_range | invalid_close, ["high", "low"]] = float("nan")
        bars.loc[invalid_open, "open"] = float("nan")
        if (bars["volume"].dropna() < 0).any() or (bars["trade_count"].dropna() < 0).any():
            raise ValueError("CRSP chunk contains negative volume or trade count")

        market_cap, shares = number2("DlyCap"), number2("ShrOut")
        valuation = pd.DataFrame({
            "security_id": sid, "instrument_id": iid, "event_time": event_stamp,
            "available_time": available, "market_cap": market_cap,
            "shares_outstanding": shares, "capitalization_flag": text2("DlyCapFlg"),
            "shares_source": text2("ShrSource"), "source": "CRSP/CIZ",
        })
        valuation = valuation.loc[market_cap.notna() | shares.notna()].reset_index(drop=True)
        valuation_key = ["instrument_id", "event_time"]
        if valuation.duplicated(valuation_key, keep=False).any():
            conflict = valuation.loc[valuation.duplicated(valuation_key, keep=False)].groupby(
                valuation_key, sort=False
            )[["market_cap", "shares_outstanding"]].nunique(dropna=True).gt(1).any(axis=1)
            if conflict.any():
                raise ValueError("CRSP duplicate trading-date rows disagree on valuation fields")
            valuation = valuation.drop_duplicates(valuation_key, keep="last").reset_index(drop=True)
        if prior_key:
            valuation_permno = valuation["security_id"].str.rsplit(":", n=1).str[-1].astype(int)
            valuation_day = valuation["event_time"].str[:10].str.replace("-", "", regex=False).astype(int)
            valuation = valuation.loc[
                (valuation_permno > prior_key[0])
                | ((valuation_permno == prior_key[0]) & (valuation_day > prior_key[1]))
            ].reset_index(drop=True)
        if (valuation["market_cap"].dropna() < 0).any() or (valuation["shares_outstanding"].dropna() < 0).any():
            raise ValueError("CRSP chunk contains negative valuation data")

        ex_day = day2("DisExDt")
        distribution_numbers = pd.DataFrame({
            name: number2(name) for name in (
                "DisDivAmt", "DlyOrdDivAmt", "DlyNonOrdDivAmt", "DisFacPr", "DisFacShr"
            )
        })
        has_distribution = ex_day.notna() | distribution_numbers.fillna(0).ne(0).any(axis=1)
        daily_delisting = text2("DlyDelFlg").str.upper().isin({"Y", "1", "TRUE", "D", "DELISTED"})
        action_mask = has_distribution | daily_delisting
        action_day = ex_day.fillna(event)
        declared = day2("DisDeclareDt")
        available_day = action_day.where(declared.isna() | (action_day >= declared), declared)
        action_type = text2("DisType").where(text2("DisType") != "", text2("DisDetailType"))
        action_type = action_type.where(has_distribution, text2("DelActionType"))
        actions = pd.DataFrame({
            "security_id": sid, "instrument_id": iid,
            "event_time": action_day.dt.strftime("%Y-%m-%dT00:00:00+00:00"),
            "available_time": available_day.dt.strftime("%Y-%m-%dT00:00:00+00:00"),
            "action_type": action_type, "cash_dividend": number2("DisDivAmt").fillna(number2("DlyOrdDivAmt")),
            "action_sequence": text2("DisSeqNbr"),
            "nonordinary_dividend": number2("DlyNonOrdDivAmt"), "price_factor": number2("DisFacPr"),
            "share_factor": number2("DisFacShr"), "declared_date": declared.dt.strftime("%Y-%m-%d").fillna(""),
            "record_date": day2("DisRecordDt").dt.strftime("%Y-%m-%d").fillna(""),
            "payment_date": day2("DisPayDt").dt.strftime("%Y-%m-%d").fillna(""), "source": "CRSP/CIZ",
        }).loc[action_mask].reset_index(drop=True)

        security_start = day2("SecurityBegDt").fillna(day2("SecInfoStartDt")).fillna(event)
        security_end = day2("SecurityEndDt")
        info_start = day2("SecInfoStartDt").fillna(security_start)
        info_end = day2("SecInfoEndDt")
        cusip = text2("CUSIP9").where(text2("CUSIP9") != "", text2("HdrCUSIP9"))
        cusip = cusip.where(cusip != "", text2("CUSIP")).where(lambda value: value != "", text2("HdrCUSIP")).str.upper()
        identity = pd.DataFrame({
            "security_id": sid, "permno": permno.astype("int64"),
            "permco": pd.to_numeric(text2("PERMCO"), errors="coerce").astype("Int64"),
            "cusip": cusip, "issuer_name": text2("IssuerNm"), "security_name": text2("SecurityNm"),
            "security_type": text2("SecurityType"), "share_type": text2("ShareType"),
            "share_class": text2("ShareClass"), "primary_exchange": text2("PrimaryExch"),
            "valid_from": security_start.dt.strftime("%Y-%m-%d").fillna(""),
            "valid_to": security_end.dt.strftime("%Y-%m-%d").fillna(""),
            "active": ~text2("SecurityActiveFlg").str.upper().isin({"N", "I", "INACTIVE"}),
            "sic": text2("SICCD"), "naics": text2("NAICS"), "ticker": ticker,
            "alias_from": info_start.dt.strftime("%Y-%m-%d").fillna(""),
            "alias_to": info_end.dt.strftime("%Y-%m-%d").fillna(""),
        })
        return {"bars": bars, "valuation": valuation, "corporate_actions": actions}, identity, {
            "source_rows": source_rows, "invalid_identity_rows": invalid_rows,
            "sanitized_ohlc_rows": sanitized_ohlc_rows,
            "last_key": list(final_key), "min_event_time": event_stamp.min(),
            "max_event_time": event_stamp.max(), "last_available_time": available.max(),
        }

    @staticmethod
    def _initialize_identity(path: Path) -> None:
        with closing(sqlite3.connect(path)) as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=NORMAL;
                CREATE TABLE IF NOT EXISTS securities(
                    security_id TEXT PRIMARY KEY,permno INTEGER NOT NULL,permco INTEGER,cusip TEXT,
                    issuer_name TEXT,security_name TEXT,security_type TEXT,share_type TEXT,share_class TEXT,
                    primary_exchange TEXT,valid_from TEXT,valid_to TEXT,active INTEGER,sic TEXT,naics TEXT
                );
                CREATE TABLE IF NOT EXISTS aliases(
                    security_id TEXT,alias_type TEXT,alias_value TEXT,valid_from TEXT,valid_to TEXT,
                    PRIMARY KEY(security_id,alias_type,alias_value,valid_from)
                );
                """
            )
            conn.commit()

    @staticmethod
    def _upsert_identity(path: Path, frame: Any) -> None:
        if frame.empty:
            return
        latest = frame.drop_duplicates("security_id", keep="last")
        aliases = []
        for row in frame.drop_duplicates(
            ["security_id", "ticker", "cusip", "alias_from", "alias_to"]
        ).itertuples(index=False):
            if row.ticker:
                aliases.append((row.security_id, "TICKER", row.ticker, row.alias_from, row.alias_to))
            if row.cusip:
                aliases.append((row.security_id, "CUSIP", row.cusip, row.alias_from, row.alias_to))
        securities = [
            (
                row.security_id, int(row.permno), None if str(row.permco) == "<NA>" else int(row.permco),
                row.cusip, row.issuer_name, row.security_name, row.security_type, row.share_type,
                row.share_class, row.primary_exchange, row.valid_from, row.valid_to, int(row.active),
                row.sic, row.naics,
            )
            for row in latest.itertuples(index=False)
        ]
        with closing(sqlite3.connect(path)) as conn:
            conn.executemany(
                """INSERT INTO securities VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(security_id) DO UPDATE SET permco=excluded.permco,cusip=excluded.cusip,
                       issuer_name=excluded.issuer_name,security_name=excluded.security_name,
                       security_type=excluded.security_type,share_type=excluded.share_type,
                       share_class=excluded.share_class,primary_exchange=excluded.primary_exchange,
                       valid_from=excluded.valid_from,valid_to=excluded.valid_to,active=excluded.active,
                       sic=excluded.sic,naics=excluded.naics""",
                securities,
            )
            conn.executemany("INSERT OR IGNORE INTO aliases VALUES (?,?,?,?,?)", aliases)
            conn.commit()

    @staticmethod
    def _load_master_frame(path: Path) -> Any:
        import pandas as pd

        with closing(sqlite3.connect(path)) as conn:
            securities = pd.read_sql_query("SELECT * FROM securities ORDER BY permno", conn)
            aliases = pd.read_sql_query(
                "SELECT * FROM aliases ORDER BY security_id,alias_type,valid_from,alias_value", conn
            )
        grouped: dict[str, str] = {}
        for security_id, group in aliases.groupby("security_id", sort=False):
            grouped[str(security_id)] = "|".join(
                f"{row.alias_type}:{row.alias_value}:{row.valid_from}:{row.valid_to}"
                for row in group.itertuples(index=False)
            )
        securities["instrument_id"] = "equity:CRSP:" + securities["permno"].astype(str)
        securities["cik"] = ""
        securities["aliases"] = securities["security_id"].map(grouped).fillna("")
        securities["event_time"] = securities["valid_from"].replace("", "1900-01-01") + "T00:00:00+00:00"
        securities["available_time"] = securities["event_time"]
        securities["source"] = "CRSP/CIZ"
        return securities[[
            "security_id", "instrument_id", "permno", "permco", "cik", "cusip", "issuer_name",
            "security_name", "primary_exchange", "valid_from", "valid_to", "aliases", "event_time",
            "available_time", "source",
        ]]

    @staticmethod
    def _write_partition(
        staging_root: Path,
        dataset_key: str,
        chunk_index: int,
        frame: Any,
        schema_version: str,
        event_field: str,
    ) -> dict[str, Any]:
        import pyarrow as pa
        import pyarrow.parquet as pq

        directory = staging_root / dataset_key / f"chunk={chunk_index:06d}" / "objects"
        directory.mkdir(parents=True, exist_ok=True)
        integer_fields = {"permno", "permco", "trade_count"}
        float_fields = {
            "open", "high", "low", "close", "volume", "turnover", "total_return",
            "price_return", "income_return", "price_adjustment_factor", "market_cap",
            "shares_outstanding", "cash_dividend", "nonordinary_dividend", "price_factor",
            "share_factor",
        }
        arrays = {}
        for name in frame.columns:
            arrow_type = pa.int64() if name in integer_fields else (
                pa.float64() if name in float_fields else pa.string()
            )
            values = frame[name]
            if pa.types.is_string(arrow_type):
                values = values.where(values.notna(), None).map(
                    lambda value: None if value is None else str(value)
                )
            arrays[name] = pa.array(values, type=arrow_type, from_pandas=True)
        table = pa.table(arrays)
        metadata = dict(table.schema.metadata or {})
        metadata[b"datatube_schema_version"] = schema_version.encode("utf-8")
        metadata[b"datatube_source_version"] = BULK_IMPORT_VERSION.encode("utf-8")
        table = table.replace_schema_metadata(metadata)
        temporary = directory / f".staging-{uuid.uuid4().hex}.parquet"
        pq.write_table(table, temporary, compression="zstd", row_group_size=100_000)
        digest = _sha256_file(temporary)
        target = directory / f"sha256-{digest}.parquet"
        if target.exists():
            if _sha256_file(target) != digest:
                raise RuntimeError(f"content-addressed Parquet collision: {target}")
            temporary.unlink(missing_ok=True)
        else:
            temporary.replace(target)
        values = frame[event_field].dropna().astype(str)
        start = values.min() if not values.empty else None
        end = values.max() if not values.empty else None
        uri = target.relative_to(BASE_DIR).as_posix() if target.is_relative_to(BASE_DIR) else str(target)
        return {
            "partition_key": f"chunk-{chunk_index:06d}", "start_time": start, "end_time": end,
            "row_count": len(frame), "file_uri": uri, "file_size": target.stat().st_size,
            "checksum": f"sha256:{digest}", "min_event_time": start, "max_event_time": end,
            "quality_status": "PASS",
        }

    def _checkpoint(
        self,
        job_id: str,
        worker_id: str,
        rows_processed: int,
        chunk_count: int,
        counts: dict[str, int],
        partitions: dict[str, dict[str, Any]],
        checkpoint: dict[str, Any],
    ) -> None:
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            for dataset_key, item in partitions.items():
                conn.execute(
                    """INSERT OR REPLACE INTO crsp_import_partitions(
                           job_id,dataset_key,chunk_index,partition_key,start_time,end_time,row_count,
                           file_uri,file_size,checksum,min_event_time,max_event_time,quality_status
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        job_id, dataset_key, chunk_count, item["partition_key"], item["start_time"],
                        item["end_time"], item["row_count"], item["file_uri"], item["file_size"],
                        item["checksum"], item["min_event_time"], item["max_event_time"],
                        item["quality_status"],
                    ),
                )
            updated = conn.execute(
                """UPDATE crsp_import_jobs SET rows_processed=?,chunk_count=?,output_counts_json=?,
                       checkpoint_json=?,heartbeat_at=?,updated_at=?
                   WHERE job_id=? AND worker_id=? AND status='RUNNING'""",
                (
                    rows_processed, chunk_count, json_dumps(counts), json_dumps(checkpoint),
                    now, now, job_id, worker_id,
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeError("CRSP import worker lost ownership of the job")

    def _checkpoint_master(self, job_id: str, item: dict[str, Any], count: int, worker_id: str) -> None:
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO crsp_import_partitions(
                       job_id,dataset_key,chunk_index,partition_key,start_time,end_time,row_count,
                       file_uri,file_size,checksum,min_event_time,max_event_time,quality_status
                   ) VALUES (?,'security_master',0,?,?,?,?,?,?,?,?,?,'PASS')""",
                (
                    job_id, item["partition_key"], item["start_time"], item["end_time"], count,
                    item["file_uri"], item["file_size"], item["checksum"], item["min_event_time"],
                    item["max_event_time"],
                ),
            )
            conn.execute(
                "UPDATE crsp_import_jobs SET heartbeat_at=?,updated_at=? WHERE job_id=? AND worker_id=?",
                (now, now, job_id, worker_id),
            )

    def _set_status(self, job_id: str, status: str, *, worker_id: str) -> None:
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                "UPDATE crsp_import_jobs SET status=?,heartbeat_at=?,updated_at=? WHERE job_id=? AND worker_id=?",
                (_clean(status).upper(), now, now, job_id, worker_id),
            )

    def _publish_identity(self, path: Path) -> None:
        """Bulk-promote the staged identity map before the atomic Catalog publish."""
        with closing(sqlite3.connect(path)) as staging:
            staging.row_factory = sqlite3.Row
            securities = staging.execute("SELECT * FROM securities ORDER BY permno").fetchall()
            alias_rows = staging.execute(
                "SELECT * FROM aliases ORDER BY security_id,alias_type,valid_from,alias_value"
            ).fetchall()
        tickers: dict[str, str] = {}
        for alias in alias_rows:
            if str(alias["alias_type"]) == "TICKER":
                tickers[str(alias["security_id"])] = str(alias["alias_value"])
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            for security in securities:
                security_id = str(security["security_id"])
                permno = int(security["permno"])
                instrument_id = f"equity:CRSP:{permno}"
                metadata = json_dumps({
                    "sic": str(security["sic"] or ""),
                    "naics": str(security["naics"] or ""),
                })
                conn.execute(
                    """INSERT INTO equity_security_master(
                           security_id,permno,permco,cik,cusip,issuer_name,security_name,
                           security_type,share_type,share_class,primary_exchange,currency,country,
                           valid_from,valid_to,active,source,metadata_json,created_at,updated_at
                       ) VALUES (?,?,?,'',?,?,?,?,?,?,?,'USD','US',?,?,?,?,?,?,?)
                       ON CONFLICT(security_id) DO UPDATE SET
                           permco=excluded.permco,
                           cusip=CASE WHEN excluded.cusip<>'' THEN excluded.cusip ELSE equity_security_master.cusip END,
                           issuer_name=excluded.issuer_name,security_name=excluded.security_name,
                           security_type=excluded.security_type,share_type=excluded.share_type,
                           share_class=excluded.share_class,primary_exchange=excluded.primary_exchange,
                           valid_from=excluded.valid_from,valid_to=excluded.valid_to,active=excluded.active,
                           source=excluded.source,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
                    (
                        security_id, permno, security["permco"], str(security["cusip"] or ""),
                        str(security["issuer_name"] or ""), str(security["security_name"] or ""),
                        str(security["security_type"] or ""), str(security["share_type"] or ""),
                        str(security["share_class"] or ""), str(security["primary_exchange"] or ""),
                        str(security["valid_from"] or "") or None,
                        str(security["valid_to"] or "") or None, int(security["active"]),
                        "CRSP/CIZ", metadata, now, now,
                    ),
                )
                display = tickers.get(security_id, str(permno))
                display_name = str(security["security_name"] or security["issuer_name"] or display)
                conn.execute(
                    """INSERT INTO instrument_registry(
                           instrument_id,asset_class,venue,market_type,native_symbol,display_symbol,
                           display_name,underlying_id,base_asset,quote_asset,currency,condition_id,
                           market_id,event_id,outcome_side,listing_time,delisting_time,timezone,
                           trading_calendar,tick_size,lot_size,status,metadata_json,created_at,updated_at
                       ) VALUES (?,'equity','CRSP','EQUITY',?,?,?,'','','','USD','','','','',?,?,
                                 'America/New_York','XNYS',NULL,NULL,?,?,?,?)
                       ON CONFLICT(instrument_id) DO UPDATE SET
                           display_symbol=excluded.display_symbol,display_name=excluded.display_name,
                           listing_time=excluded.listing_time,delisting_time=excluded.delisting_time,
                           status=excluded.status,metadata_json=excluded.metadata_json,
                           updated_at=excluded.updated_at""",
                    (
                        instrument_id, str(permno), display, display_name,
                        str(security["valid_from"] or "") or None,
                        str(security["valid_to"] or "") or None,
                        "ACTIVE" if int(security["active"]) else "INACTIVE",
                        json_dumps({
                            "security_id": security_id, "permno": permno,
                            "permco": security["permco"], "primary_exchange": security["primary_exchange"],
                            "identity_source": "CRSP/CIZ",
                        }),
                        now, now,
                    ),
                )
                conn.execute(
                    """INSERT INTO instrument_aliases(source,source_symbol,instrument_id,created_at)
                       VALUES ('crsp:permno',?,?,?)
                       ON CONFLICT(source,source_symbol) DO UPDATE SET instrument_id=excluded.instrument_id""",
                    (str(permno), instrument_id, now),
                )
            for alias in alias_rows:
                conn.execute(
                    """INSERT OR IGNORE INTO equity_security_aliases(
                           security_id,alias_type,alias_value,valid_from,valid_to,source,created_at
                       ) VALUES (?,?,?,?,?,'CRSP/CIZ',?)""",
                    (
                        str(alias["security_id"]), str(alias["alias_type"]), str(alias["alias_value"]),
                        str(alias["valid_from"] or ""), str(alias["valid_to"] or ""), now,
                    ),
                )

    def _publish(self, job_id: str, counts: dict[str, int], checkpoint: dict[str, Any]) -> dict[str, Any]:
        job = self.get(job_id)
        if job is None:
            raise RuntimeError("CRSP import job not found during publish")
        with self.store.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM crsp_import_partitions WHERE job_id=? ORDER BY dataset_key,chunk_index",
                (job_id,),
            ).fetchall()
        by_dataset: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            item = dict(row)
            item.pop("job_id", None)
            item.pop("dataset_key", None)
            item.pop("chunk_index", None)
            by_dataset.setdefault(str(row["dataset_key"]), []).append(item)
        specs = []
        for dataset_key, (data_type, schema_version, frequency, _event_field, adjustment) in DATASET_CONTRACTS.items():
            partitions = by_dataset.get(dataset_key) or []
            if not partitions:
                raise RuntimeError(f"CRSP full import produced no {dataset_key} partitions")
            dataset_id = f"{job['dataset_prefix']}:{data_type}"
            fingerprint = hashlib.sha256(json_dumps({
                "dataset_id": dataset_id, "source_fingerprint": job["source_fingerprint"],
                "schema_version": schema_version, "normalizer_version": BULK_IMPORT_VERSION,
                "partitions": [(p["partition_key"], p["row_count"], p["checksum"]) for p in partitions],
            }).encode("utf-8")).hexdigest()
            fields = self._parquet_fields(Path(partitions[0]["file_uri"]))
            specs.append({
                "catalog": {
                    "dataset_id": dataset_id, "instrument_id": "equity:CRSP:ALL",
                    "data_type": data_type, "frequency": frequency, "source": "CRSP/CIZ",
                    "schema_version": schema_version, "storage_path": str(Path(job["staging_root"]) / dataset_key),
                    "row_count": counts.get(dataset_key, 0), "gap_count": 0,
                    "quality_status": "PASS", "fields": fields, "adjustment": adjustment,
                    "time_semantics": "SOURCE_AVAILABLE_TIME", "point_in_time_policy": "AS_OF",
                    "metadata": {
                        "source_fingerprint": job["source_fingerprint"],
                        "normalizer_version": BULK_IMPORT_VERSION, "source_rows": job["rows_processed"],
                        "quality_report": {"status": "PASS", "invalid_identity_rows": checkpoint.get("invalid_identity_rows", 0)},
                        "vwap_included": False, "full_import": True, "job_id": job_id,
                    },
                },
                "dataset_fingerprint": fingerprint, "partitions": partitions,
            })
        published = self.catalog.commit_manifests_atomically(specs)
        return {
            dataset_id: {
                "manifest_id": manifest.manifest_id, "manifest_hash": manifest.manifest_hash,
                "row_count": sum(part.row_count for part in manifest.partitions),
                "partition_count": len(manifest.partitions), "status": manifest.status,
            }
            for dataset_id, manifest in published.items()
        }

    @staticmethod
    def _parquet_fields(path: Path) -> list[str]:
        import pyarrow.parquet as pq

        resolved = path if path.is_absolute() else BASE_DIR / path
        return list(pq.read_schema(resolved).names)


def run_crsp_import_job(
    store: DataPlatformStore,
    job_id: str,
    *,
    chunk_rows: int = 250_000,
) -> dict[str, Any]:
    return CrspBulkImportService(store).run(job_id, chunk_rows=chunk_rows)
