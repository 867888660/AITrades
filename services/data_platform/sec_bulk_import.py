from __future__ import annotations

import hashlib
import json
import math
import os
import re
import uuid
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import requests

from services.history_storage_service import get_data_platform_canonical_root

from .catalog_service import DatasetCatalogService
from .store import BASE_DIR, DataPlatformStore, json_dumps, utc_now


SEC_BULK_IMPORT_VERSION = "sec_edgar_bulk_pit.v1"
SEC_SOURCE_URLS = {
    "companyfacts": "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip",
    "submissions": "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip",
    "tickers": "https://www.sec.gov/files/company_tickers_exchange.json",
}
SEC_USER_AGENT = "DataTube/1.0 research@example.com"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _stamp(value: Any) -> str:
    text = _clean(value)
    if not text:
        return ""
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _filed_fallback(value: Any) -> str:
    text = _clean(value)
    if not text:
        return ""
    filing_day = date.fromisoformat(text[:10])
    return datetime.combine(filing_day + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc).isoformat()


def _exchange_family(value: Any) -> str:
    text = re.sub(r"[^A-Z0-9]", "", _clean(value).upper())
    if text in {"N", "NYSE", "NEWYORKSTOCKEXCHANGE"}:
        return "NYSE"
    if text in {"Q", "NASDAQ", "NASDAQGLOBALSELECT", "NASDAQGLOBALMARKET", "NASDAQCAPITALMARKET"}:
        return "NASDAQ"
    if text in {"A", "NYSEAMERICAN", "AMEX", "NYSEMKT"}:
        return "NYSEAMERICAN"
    if text in {"P", "NYSEARCA", "ARCA"}:
        return "NYSEARCA"
    if text in {"Z", "CBOE", "CBOEBZX", "BATS"}:
        return "CBOE"
    return text


class SecBulkImportService:
    """SEC official bulk ingestion with evidence-gated CRSP identity linking."""

    def __init__(self, store: DataPlatformStore, *, canonical_root: str | Path | None = None):
        self.store = store
        self.catalog = DatasetCatalogService(store)
        self.canonical_root = Path(canonical_root or get_data_platform_canonical_root()).resolve()

    def create(
        self,
        *,
        dataset_id: str = "sec:edgar:fundamentals_pit",
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        material = {
            "dataset_id": _clean(dataset_id),
            "source_urls": SEC_SOURCE_URLS,
            "normalizer_version": SEC_BULK_IMPORT_VERSION,
        }
        key = _clean(idempotency_key) or "sec-bulk:" + hashlib.sha256(
            json_dumps(material).encode("utf-8")
        ).hexdigest()
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            existing = conn.execute(
                "SELECT * FROM sec_bulk_import_jobs WHERE idempotency_key=?", (key,)
            ).fetchone()
            if existing:
                if str(existing["dataset_id"]) != material["dataset_id"]:
                    raise ValueError("SEC bulk idempotency key was reused for another Dataset")
                return self._decode(existing)
            job_id = f"sec_import_{uuid.uuid4().hex}"
            staging = self.canonical_root / "sec_edgar" / f"import={job_id}"
            conn.execute(
                """INSERT INTO sec_bulk_import_jobs(
                       job_id,idempotency_key,status,dataset_id,staging_root,source_urls_json,
                       created_at,updated_at
                   ) VALUES (?,?,'QUEUED',?,?,?,?,?)""",
                (job_id, key, material["dataset_id"], str(staging), json_dumps(SEC_SOURCE_URLS), now, now),
            )
            row = conn.execute("SELECT * FROM sec_bulk_import_jobs WHERE job_id=?", (job_id,)).fetchone()
        return self._decode(row)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM sec_bulk_import_jobs WHERE job_id=?", (_clean(job_id),)
            ).fetchone()
        return self._decode(row) if row else None

    def list(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM sec_bulk_import_jobs ORDER BY created_at DESC LIMIT ?",
                (max(1, min(int(limit), 200)),),
            ).fetchall()
        return [self._decode(row) for row in rows]

    def resume(self, job_id: str) -> dict[str, Any]:
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            row = conn.execute("SELECT * FROM sec_bulk_import_jobs WHERE job_id=?", (_clean(job_id),)).fetchone()
            if row is None:
                raise ValueError("SEC bulk import job not found")
            if str(row["status"]) == "READY":
                return self._decode(row)
            conn.execute(
                "UPDATE sec_bulk_import_jobs SET status='QUEUED',worker_id='',error='',updated_at=? WHERE job_id=?",
                (now, _clean(job_id)),
            )
        result = self.get(job_id)
        if result is None:
            raise RuntimeError("failed to resume SEC bulk import")
        return result

    def cancel_for_recovery(self, job_id: str) -> dict[str, Any]:
        """Stop one local worker while preserving every staged checkpoint."""
        job = self.get(job_id)
        if job is None:
            raise ValueError("SEC bulk import job not found")
        if job["status"] == "READY":
            return job
        match = re.fullmatch(r"sec-worker-(\d+)", _clean(job.get("worker_id")))
        if match and job["status"] in {"DOWNLOADING", "MAPPING", "RUNNING", "FINALIZING"}:
            pid = int(match.group(1))
            if pid != os.getpid():
                try:
                    os.kill(pid, 15)
                except ProcessLookupError:
                    pass
                except OSError as exc:
                    if getattr(exc, "winerror", None) != 87:
                        raise RuntimeError(f"failed to stop SEC worker {pid}: {exc}") from exc
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                """UPDATE sec_bulk_import_jobs SET status='FAILED',worker_id='',
                       error='CANCELLED_FOR_VERIFIED_SOURCE_REUSE',heartbeat_at=?,updated_at=?
                   WHERE job_id=? AND status<>'READY'""",
                (now, now, _clean(job_id)),
            )
        result = self.get(job_id)
        if result is None:
            raise RuntimeError("SEC bulk import disappeared during recovery cancel")
        return result

    def reuse_verified_sources(self, job_id: str, *, source_job_id: str) -> dict[str, Any]:
        """Atomically hard-link immutable SEC inputs from an already READY job."""
        target_job = self.get(job_id)
        source_job = self.get(source_job_id)
        if target_job is None or source_job is None:
            raise ValueError("SEC source reuse requires existing target and source jobs")
        if source_job["status"] != "READY":
            raise ValueError("SEC source reuse requires a READY source job")
        if target_job["status"] not in {"QUEUED", "FAILED"}:
            raise ValueError("SEC source reuse target must be QUEUED or FAILED")
        source_files = dict(source_job.get("source_files") or {})
        names = {
            "companyfacts": "companyfacts.zip",
            "submissions": "submissions.zip",
            "tickers": "company_tickers_exchange.json",
        }
        raw_root = Path(target_job["staging_root"]) / "raw"
        raw_root.mkdir(parents=True, exist_ok=True)
        reused: dict[str, Any] = {}
        for key, filename in names.items():
            evidence = dict(source_files.get(key) or {})
            source = Path(_clean(evidence.get("path")))
            expected_size = int(evidence.get("size") or 0)
            expected_hash = _clean(evidence.get("sha256"))
            if not source.is_file() or source.stat().st_size != expected_size:
                raise RuntimeError(f"verified SEC source is missing or changed: {key}")
            if not expected_hash or _sha256_file(source) != expected_hash:
                raise RuntimeError(f"verified SEC source checksum mismatch: {key}")
            target = raw_root / filename
            if target.exists():
                if target.stat().st_size != expected_size or _sha256_file(target) != expected_hash:
                    raise RuntimeError(f"target SEC source conflicts with verified source: {key}")
            else:
                temporary = raw_root / f".reuse-{uuid.uuid4().hex}-{filename}"
                os.link(source, temporary)
                temporary.replace(target)
            reused[key] = {"path": str(target), "size": expected_size, "sha256": expected_hash}
        now = utc_now()
        checkpoint = dict(target_job.get("checkpoint") or {})
        checkpoint["verified_source_reuse"] = {
            "source_job_id": source_job_id,
            "source_fingerprint": source_job["source_fingerprint"],
        }
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                """UPDATE sec_bulk_import_jobs SET status='QUEUED',worker_id='',error='',
                       source_files_json=?,source_fingerprint=?,checkpoint_json=?,updated_at=?
                   WHERE job_id=?""",
                (
                    json_dumps(reused), source_job["source_fingerprint"],
                    json_dumps(checkpoint), now, _clean(job_id),
                ),
            )
        result = self.get(job_id)
        if result is None:
            raise RuntimeError("SEC bulk import disappeared during source reuse")
        return result

    @staticmethod
    def _decode(row: Any) -> dict[str, Any]:
        result = dict(row)
        for name in (
            "source_urls_json", "source_files_json", "mapping_report_json",
            "quality_report_json", "checkpoint_json", "manifest_json",
        ):
            result[name.removesuffix("_json")] = json.loads(result.pop(name) or "{}")
        return result

    def _claim(self, job_id: str, worker_id: str) -> dict[str, Any]:
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            row = conn.execute("SELECT * FROM sec_bulk_import_jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise ValueError("SEC bulk import job not found")
            if str(row["status"]) == "READY":
                return self._decode(row)
            heartbeat = _clean(row["heartbeat_at"])
            if str(row["status"]) in {"DOWNLOADING", "MAPPING", "RUNNING", "FINALIZING"} and heartbeat:
                age = datetime.now(timezone.utc) - datetime.fromisoformat(heartbeat)
                if age.total_seconds() < 600 and str(row["worker_id"]) != worker_id:
                    raise RuntimeError("SEC bulk import job is already running")
            conn.execute(
                """UPDATE sec_bulk_import_jobs SET status='DOWNLOADING',worker_id=?,heartbeat_at=?,
                       error='',updated_at=? WHERE job_id=?""",
                (worker_id, now, now, job_id),
            )
        result = self.get(job_id)
        if result is None:
            raise RuntimeError("failed to claim SEC bulk import")
        return result

    def run(self, job_id: str, *, worker_id: str = "", target_rows: int = 250_000) -> dict[str, Any]:
        worker_id = _clean(worker_id) or f"sec-worker-{os.getpid()}"
        job = self._claim(_clean(job_id), worker_id)
        if job["status"] == "READY":
            return job
        try:
            staging = Path(job["staging_root"])
            raw_root = staging / "raw"
            raw_root.mkdir(parents=True, exist_ok=True)
            files = {
                "companyfacts": self._download(job["job_id"], worker_id, SEC_SOURCE_URLS["companyfacts"], raw_root / "companyfacts.zip"),
                "submissions": self._download(job["job_id"], worker_id, SEC_SOURCE_URLS["submissions"], raw_root / "submissions.zip"),
                "tickers": self._download(job["job_id"], worker_id, SEC_SOURCE_URLS["tickers"], raw_root / "company_tickers_exchange.json"),
            }
            source_files = {
                key: {"path": str(path), "size": path.stat().st_size, "sha256": _sha256_file(path)}
                for key, path in files.items()
            }
            source_identity = {
                key: {
                    "url": SEC_SOURCE_URLS[key],
                    "size": item["size"],
                    "sha256": item["sha256"],
                }
                for key, item in source_files.items()
            }
            source_fingerprint = hashlib.sha256(
                json_dumps(source_identity).encode("utf-8")
            ).hexdigest()
            self._update_sources(job["job_id"], worker_id, source_files, source_fingerprint)

            mapping_report = self._build_authoritative_mapping(
                files["tickers"], files["submissions"], source_fingerprint
            )
            self._update_mapping(job["job_id"], worker_id, mapping_report)
            links = self._load_links(source_fingerprint)
            result = self._import_companyfacts(
                job["job_id"], worker_id, files["companyfacts"], files["submissions"],
                links, source_fingerprint, target_rows=max(25_000, int(target_rows)),
            )
            return result
        except Exception as exc:
            now = utc_now()
            with self.store.transaction(immediate=True) as conn:
                conn.execute(
                    "UPDATE sec_bulk_import_jobs SET status='FAILED',error=?,heartbeat_at=?,updated_at=? WHERE job_id=?",
                    (f"{type(exc).__name__}: {exc}", now, now, _clean(job_id)),
                )
            raise

    def _download(self, job_id: str, worker_id: str, url: str, target: Path) -> Path:
        if not url.startswith("https://www.sec.gov/"):
            raise ValueError("SEC bulk downloader only permits official sec.gov URLs")
        partial = target.with_suffix(target.suffix + ".partial")
        if target.is_file():
            return target
        headers = {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "identity", "Range": "bytes=0-0"}
        probe = requests.get(url, headers=headers, timeout=60, stream=True)
        probe.raise_for_status()
        content_range = _clean(probe.headers.get("Content-Range"))
        total = int(content_range.rsplit("/", 1)[1]) if "/" in content_range else int(probe.headers.get("Content-Length") or 0)
        probe.close()
        existing = partial.stat().st_size if partial.is_file() else 0
        request_headers = {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "identity"}
        if existing:
            request_headers["Range"] = f"bytes={existing}-"
        response = requests.get(url, headers=request_headers, timeout=(30, 120), stream=True)
        response.raise_for_status()
        append = existing > 0 and response.status_code == 206
        if existing and not append:
            existing = 0
        mode = "ab" if append else "wb"
        downloaded = existing
        with partial.open(mode) as handle:
            for block in response.iter_content(8 * 1024 * 1024):
                if not block:
                    continue
                handle.write(block)
                downloaded += len(block)
                self._download_heartbeat(job_id, worker_id, target.name, downloaded, total)
        response.close()
        if total and partial.stat().st_size != total:
            raise RuntimeError(f"incomplete SEC download {target.name}: {partial.stat().st_size}/{total}")
        partial.replace(target)
        return target

    def _download_heartbeat(self, job_id: str, worker_id: str, name: str, current: int, total: int) -> None:
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            row = conn.execute("SELECT checkpoint_json FROM sec_bulk_import_jobs WHERE job_id=?", (job_id,)).fetchone()
            checkpoint = json.loads(str(row[0] or "{}")) if row else {}
            checkpoint["download"] = {"file": name, "bytes": current, "total_bytes": total}
            conn.execute(
                """UPDATE sec_bulk_import_jobs SET checkpoint_json=?,heartbeat_at=?,updated_at=?
                   WHERE job_id=? AND worker_id=?""",
                (json_dumps(checkpoint), now, now, job_id, worker_id),
            )

    def _update_sources(
        self, job_id: str, worker_id: str, source_files: Mapping[str, Any], fingerprint: str
    ) -> None:
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                """UPDATE sec_bulk_import_jobs SET status='MAPPING',source_files_json=?,
                       source_fingerprint=?,heartbeat_at=?,updated_at=? WHERE job_id=? AND worker_id=?""",
                (json_dumps(source_files), fingerprint, now, now, job_id, worker_id),
            )

    def _build_authoritative_mapping(
        self, ticker_path: Path, submissions_path: Path, source_version: str
    ) -> dict[str, Any]:
        ticker_payload = json.loads(ticker_path.read_text(encoding="utf-8"))
        fields = list(ticker_payload.get("fields") or [])
        official_rows = [dict(zip(fields, row)) for row in ticker_payload.get("data") or []]
        with zipfile.ZipFile(submissions_path) as submissions:
            names = {
                Path(name).name: name
                for name in submissions.namelist()
                if Path(name).name
            }
            confirmed: list[dict[str, Any]] = []
            missing_submission = 0
            for item in official_rows:
                cik = _clean(item.get("cik")).zfill(10)
                ticker = _clean(item.get("ticker")).upper()
                exchange = _clean(item.get("exchange"))
                entry = f"CIK{cik}.json"
                if not cik.isdigit() or not ticker or entry not in names:
                    missing_submission += 1
                    continue
                payload = json.loads(submissions.read(names[entry]))
                pairs = {
                    (_clean(t).upper(), _clean(e))
                    for t, e in zip(payload.get("tickers") or [], payload.get("exchanges") or [])
                }
                if (ticker, exchange) in pairs or any(ticker == candidate[0] for candidate in pairs):
                    confirmed.append({"cik": cik, "ticker": ticker, "exchange": exchange, "name": _clean(item.get("name"))})

        cutoff = self._crsp_cutoff()
        with self.store.connection() as conn:
            candidates = [
                dict(row) for row in conn.execute(
                    """SELECT m.security_id,m.permno,m.permco,m.primary_exchange,m.valid_from,m.valid_to,
                              a.alias_value AS ticker,a.valid_from AS ticker_from,a.valid_to AS ticker_to
                       FROM equity_security_aliases AS a
                       JOIN equity_security_master AS m ON m.security_id=a.security_id
                       WHERE a.alias_type='TICKER'
                         AND (a.valid_from='' OR a.valid_from<=?)
                         AND (a.valid_to='' OR a.valid_to>=?)
                         AND (m.valid_from IS NULL OR m.valid_from='' OR m.valid_from<=?)
                         AND (m.valid_to IS NULL OR m.valid_to='' OR m.valid_to>=?)
                       ORDER BY a.alias_value,m.security_id""",
                    (cutoff, cutoff, cutoff, cutoff),
                ).fetchall()
            ]
            total_security = int(conn.execute("SELECT COUNT(*) FROM equity_security_master").fetchone()[0])
            total_permco = int(conn.execute(
                "SELECT COUNT(DISTINCT permco) FROM equity_security_master WHERE permco IS NOT NULL"
            ).fetchone()[0])
        by_ticker: dict[str, list[dict[str, Any]]] = {}
        for candidate in candidates:
            by_ticker.setdefault(_clean(candidate["ticker"]).upper(), []).append(candidate)
        seeds: list[tuple[dict[str, Any], dict[str, Any]]] = []
        ambiguous_ticker = 0
        for sec in confirmed:
            scoped = list(by_ticker.get(sec["ticker"]) or [])
            sec_family = _exchange_family(sec["exchange"])
            compatible = [row for row in scoped if not sec_family or _exchange_family(row["primary_exchange"]) == sec_family]
            if sec_family:
                scoped = compatible
            groups = {row["permco"] if row["permco"] is not None else row["security_id"] for row in scoped}
            if len(groups) != 1 or not scoped:
                if scoped:
                    ambiguous_ticker += 1
                continue
            seeds.extend((sec, row) for row in scoped)

        cik_groups: dict[str, set[Any]] = {}
        group_ciks: dict[Any, set[str]] = {}
        for sec, row in seeds:
            group = row["permco"] if row["permco"] is not None else row["security_id"]
            cik_groups.setdefault(sec["cik"], set()).add(group)
            group_ciks.setdefault(group, set()).add(sec["cik"])
        accepted = {
            cik: next(iter(groups))
            for cik, groups in cik_groups.items()
            if len(groups) == 1 and len(group_ciks[next(iter(groups))]) == 1
        }
        conflict_ciks = len(cik_groups) - len(accepted)
        seed_evidence: dict[tuple[str, Any], dict[str, Any]] = {}
        for sec, row in seeds:
            group = row["permco"] if row["permco"] is not None else row["security_id"]
            if accepted.get(sec["cik"]) == group:
                seed_evidence[(sec["cik"], group)] = sec

        with self.store.connection() as conn:
            master_rows = [dict(row) for row in conn.execute(
                "SELECT * FROM equity_security_master ORDER BY security_id"
            ).fetchall()]
        links: list[dict[str, Any]] = []
        for cik, group in accepted.items():
            evidence = seed_evidence[(cik, group)]
            for security in master_rows:
                matches = security["permco"] == group if isinstance(group, int) else security["security_id"] == group
                if not matches:
                    continue
                links.append({
                    "security_id": security["security_id"], "permno": security["permno"],
                    "permco": security["permco"], "cik": cik,
                    "valid_from": _clean(security["valid_from"]), "valid_to": _clean(security["valid_to"]),
                    "sec_ticker": evidence["ticker"], "sec_exchange": evidence["exchange"],
                    "evidence_type": "SEC_TICKERS_AND_SUBMISSIONS_EXACT_THEN_CRSP_PERMCO_PROPAGATION",
                    "evidence": {"cutoff": cutoff, "official_name": evidence["name"], "seed_ticker": evidence["ticker"]},
                })
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            prior_security_ids = [
                str(row[0]) for row in conn.execute(
                    "SELECT DISTINCT security_id FROM sec_security_links WHERE status='ACTIVE' AND source_version<>?",
                    (source_version,),
                ).fetchall()
            ]
            if prior_security_ids:
                placeholders = ",".join("?" for _ in prior_security_ids)
                conn.execute(
                    f"UPDATE equity_security_master SET cik='',updated_at=? WHERE security_id IN ({placeholders})",
                    (now, *prior_security_ids),
                )
                conn.execute(
                    f"DELETE FROM equity_security_aliases WHERE alias_type='CIK' AND source='SEC/CRSP_AUTHORITY' AND security_id IN ({placeholders})",
                    tuple(prior_security_ids),
                )
            conn.execute(
                "UPDATE sec_security_links SET status='SUPERSEDED',updated_at=? WHERE status='ACTIVE' AND source_version<>?",
                (now, source_version),
            )
            for link in links:
                link_id = "sec_link_" + hashlib.sha256(json_dumps({
                    "security_id": link["security_id"], "cik": link["cik"],
                    "valid_from": link["valid_from"], "source_version": source_version,
                }).encode("utf-8")).hexdigest()[:32]
                conn.execute(
                    """INSERT INTO sec_security_links(
                           link_id,security_id,permno,permco,cik,valid_from,valid_to,sec_ticker,
                           sec_exchange,evidence_type,evidence_json,source_version,confidence,status,
                           created_at,updated_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'ACTIVE',?,?)
                       ON CONFLICT(security_id,cik,valid_from,source_version) DO UPDATE SET
                           valid_to=excluded.valid_to,sec_ticker=excluded.sec_ticker,
                           sec_exchange=excluded.sec_exchange,evidence_json=excluded.evidence_json,
                           status='ACTIVE',updated_at=excluded.updated_at""",
                    (
                        link_id, link["security_id"], link["permno"], link["permco"], link["cik"],
                        link["valid_from"], link["valid_to"], link["sec_ticker"], link["sec_exchange"],
                        link["evidence_type"], json_dumps(link["evidence"]), source_version,
                        "AUTHORITATIVE_EXACT", now, now,
                    ),
                )
                conn.execute(
                    "UPDATE equity_security_master SET cik=?,updated_at=? WHERE security_id=?",
                    (link["cik"], now, link["security_id"]),
                )
                conn.execute(
                    """INSERT OR IGNORE INTO equity_security_aliases(
                           security_id,alias_type,alias_value,valid_from,valid_to,source,created_at
                       ) VALUES (?,'CIK',?,?,?,?,?)""",
                    (link["security_id"], link["cik"], link["valid_from"], link["valid_to"], "SEC/CRSP_AUTHORITY", now),
                )
        mapped_security = len({link["security_id"] for link in links})
        mapped_permco = len({link["permco"] for link in links if link["permco"] is not None})
        return {
            "schema_version": "sec_crsp_authoritative_mapping_report.v1",
            "source_version": source_version, "crsp_cutoff": cutoff,
            "sec_official_ticker_rows": len(official_rows),
            "sec_double_confirmed_rows": len(confirmed), "missing_submission_rows": missing_submission,
            "unambiguous_seed_rows": len(seeds), "ambiguous_ticker_rows": ambiguous_ticker,
            "conflicting_ciks": conflict_ciks, "mapped_ciks": len(accepted),
            "total_crsp_securities": total_security, "mapped_crsp_securities": mapped_security,
            "unmapped_crsp_securities": total_security - mapped_security,
            "security_mapping_coverage": mapped_security / total_security if total_security else 0.0,
            "total_crsp_permcos": total_permco, "mapped_crsp_permcos": mapped_permco,
            "permco_mapping_coverage": mapped_permco / total_permco if total_permco else 0.0,
            "status": "PASS" if links else "FAIL",
        }

    def _crsp_cutoff(self) -> str:
        catalog = self.catalog.get_catalog("crsp:ciz:security_master")
        if catalog is None or catalog.status != "READY" or not catalog.end_time:
            raise RuntimeError("READY CRSP Security Master is required before SEC mapping")
        return str(catalog.end_time)[:10]

    def _update_mapping(self, job_id: str, worker_id: str, report: Mapping[str, Any]) -> None:
        if report.get("status") != "PASS":
            raise RuntimeError("SEC/CRSP authoritative mapping produced no valid links")
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                """UPDATE sec_bulk_import_jobs SET status='RUNNING',mapping_report_json=?,
                       heartbeat_at=?,updated_at=? WHERE job_id=? AND worker_id=?""",
                (json_dumps(report), now, now, job_id, worker_id),
            )

    def _load_links(self, source_version: str) -> dict[str, list[dict[str, Any]]]:
        with self.store.connection() as conn:
            rows = [dict(row) for row in conn.execute(
                """SELECT * FROM sec_security_links WHERE source_version=? AND status='ACTIVE'
                   ORDER BY cik,valid_from,security_id""",
                (source_version,),
            ).fetchall()]
        result: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            result.setdefault(str(row["cik"]), []).append(row)
        return result

    @staticmethod
    def _acceptance_index(payload: Mapping[str, Any]) -> dict[str, str]:
        recent = dict((payload.get("filings") or {}).get("recent") or {})
        accessions = list(recent.get("accessionNumber") or [])
        accepted = list(recent.get("acceptanceDateTime") or [])
        return {
            _clean(accession): _stamp(accepted[index])
            for index, accession in enumerate(accessions)
            if _clean(accession) and index < len(accepted) and _clean(accepted[index])
        }

    @staticmethod
    def _active_links(links: list[dict[str, Any]], available: str) -> list[dict[str, Any]]:
        day = available[:10]
        active = [
            link for link in links
            if (not _clean(link["valid_from"]) or _clean(link["valid_from"]) <= day)
            and (not _clean(link["valid_to"]) or _clean(link["valid_to"]) >= day)
        ]
        return active

    def _normalize_company(
        self,
        company: Mapping[str, Any],
        submission: Mapping[str, Any],
        links: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        cik = _clean(company.get("cik")).zfill(10)
        accepted = self._acceptance_index(submission)
        rows: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()
        stats = {
            "observations": 0, "duplicates": 0, "missing_dates": 0,
            "missing_active_security": 0, "acceptance_fallbacks": 0,
            "nonnumeric_values": 0, "period_after_available": 0,
            "invalid_period_ranges": 0, "future_available_times": 0,
            "available_before_filed": 0, "invalid_acceptance_fallbacks": 0,
        }
        current_time = datetime.now(timezone.utc).isoformat()
        facts = company.get("facts") if isinstance(company.get("facts"), Mapping) else {}
        for taxonomy, concepts in facts.items():
            if not isinstance(concepts, Mapping):
                continue
            for concept, definition in concepts.items():
                units = definition.get("units") if isinstance(definition, Mapping) else {}
                if not isinstance(units, Mapping):
                    continue
                for unit, observations in units.items():
                    if not isinstance(observations, list):
                        continue
                    for observation in observations:
                        if not isinstance(observation, Mapping):
                            continue
                        stats["observations"] += 1
                        accession = _clean(observation.get("accn"))
                        filed_at = _stamp(observation.get("filed"))
                        accepted_at = accepted.get(accession, "")
                        available = accepted_at or _filed_fallback(observation.get("filed"))
                        period_end = _stamp(observation.get("end"))
                        period_start = _stamp(observation.get("start"))
                        if not available or not period_end:
                            stats["missing_dates"] += 1
                            continue
                        if filed_at and available < filed_at:
                            stats["available_before_filed"] += 1
                            stats["invalid_acceptance_fallbacks"] += 1
                            accepted_at = ""
                            available = _filed_fallback(observation.get("filed"))
                        if available > current_time:
                            stats["future_available_times"] += 1
                            continue
                        if period_end > available:
                            stats["period_after_available"] += 1
                            continue
                        if period_start and period_start > period_end:
                            stats["invalid_period_ranges"] += 1
                            continue
                        if not accepted_at:
                            stats["acceptance_fallbacks"] += 1
                        key = (
                            taxonomy, concept, unit, accession, _clean(observation.get("start")),
                            _clean(observation.get("end")), _clean(observation.get("frame")),
                            repr(observation.get("val")),
                        )
                        if key in seen:
                            stats["duplicates"] += 1
                            continue
                        seen.add(key)
                        active = self._active_links(links, available)
                        if not active:
                            stats["missing_active_security"] += 1
                            continue
                        raw_value = observation.get("val")
                        try:
                            numeric = float(raw_value)
                            if not math.isfinite(numeric):
                                raise ValueError
                        except (TypeError, ValueError, OverflowError):
                            numeric = None
                            stats["nonnumeric_values"] += 1
                        for link in active:
                            rows.append({
                                "security_id": link["security_id"],
                                "instrument_id": f"equity:CRSP:{int(link['permno'])}",
                                "permno": int(link["permno"]), "permco": link["permco"], "cik": cik,
                                "entity_name": _clean(company.get("entityName")),
                                "taxonomy": _clean(taxonomy), "concept": _clean(concept),
                                "label": _clean(definition.get("label")), "unit": _clean(unit),
                                "value": numeric, "value_text": _clean(raw_value),
                                "period_start": period_start,
                                "period_end": period_end, "event_time": period_end,
                                "filed_at": filed_at, "accepted_at": accepted_at,
                                "available_time": available, "form": _clean(observation.get("form")),
                                "fiscal_year": self._fiscal_year(observation.get("fy")),
                                "fiscal_period": _clean(observation.get("fp")),
                                "frame": _clean(observation.get("frame")),
                                "accession_number": accession, "source": "SEC/COMPANYFACTS",
                            })
        rows.sort(key=lambda row: (row["event_time"], row["security_id"], row["concept"], row["accession_number"]))
        stats["emitted_rows"] = len(rows)
        return rows, stats

    @staticmethod
    def _fiscal_year(value: Any) -> int | None:
        text = _clean(value)
        if not text:
            return None
        try:
            year = int(float(text))
        except (TypeError, ValueError, OverflowError):
            return None
        return year if 1000 <= year <= 9999 else None

    def _import_companyfacts(
        self,
        job_id: str,
        worker_id: str,
        companyfacts_path: Path,
        submissions_path: Path,
        links: dict[str, list[dict[str, Any]]],
        source_fingerprint: str,
        *,
        target_rows: int,
    ) -> dict[str, Any]:
        job = self.get(job_id)
        if job is None:
            raise RuntimeError("SEC bulk job not found")
        start_index = int(job["entry_index"])
        partition_index = int(job["partition_count"])
        row_count = int(job["row_count"])
        mapped_company_count = int(job["mapped_company_count"])
        quality = dict(job["quality_report"] or {})
        counters = dict(quality.get("counters") or {})
        buffer: list[dict[str, Any]] = []
        with zipfile.ZipFile(companyfacts_path) as facts_zip, zipfile.ZipFile(submissions_path) as submissions_zip:
            entries = sorted(
                name for name in facts_zip.namelist()
                if re.fullmatch(r"CIK\d{10}\.json", Path(name).name)
            )
            submission_names = {
                Path(name).name: name
                for name in submissions_zip.namelist()
                if Path(name).name
            }
            for index in range(start_index, len(entries)):
                entry = entries[index]
                cik = re.search(r"CIK(\d{10})", Path(entry).name).group(1)  # type: ignore[union-attr]
                scoped_links = links.get(cik) or []
                if scoped_links:
                    company = json.loads(facts_zip.read(entry))
                    submission_entry = f"CIK{cik}.json"
                    submission = (
                        json.loads(submissions_zip.read(submission_names[submission_entry]))
                        if submission_entry in submission_names else {}
                    )
                    rows, stats = self._normalize_company(company, submission, scoped_links)
                    for key, value in stats.items():
                        counters[key] = int(counters.get(key, 0)) + int(value)
                    if rows:
                        mapped_company_count += 1
                        buffer.extend(rows)
                if len(buffer) >= target_rows:
                    partition_index += 1
                    partition = self._write_partition(Path(job["staging_root"]), partition_index, buffer)
                    row_count += len(buffer)
                    self._checkpoint_partition(
                        job_id, worker_id, index + 1, len(entries), mapped_company_count,
                        row_count, partition_index, partition, counters,
                    )
                    buffer = []
                elif not buffer and (index + 1) % 500 == 0:
                    self._checkpoint_progress(job_id, worker_id, index + 1, len(entries), mapped_company_count, counters)
            if buffer:
                partition_index += 1
                partition = self._write_partition(Path(job["staging_root"]), partition_index, buffer)
                row_count += len(buffer)
                self._checkpoint_partition(
                    job_id, worker_id, len(entries), len(entries), mapped_company_count,
                    row_count, partition_index, partition, counters,
                )
        if row_count <= 0 or partition_index <= 0:
            raise RuntimeError("SEC bulk import produced no mapped PIT facts")
        return self._finalize(job_id, worker_id, len(entries), mapped_company_count, row_count, counters, source_fingerprint)

    @staticmethod
    def _write_partition(root: Path, index: int, rows: list[dict[str, Any]]) -> dict[str, Any]:
        import pyarrow as pa
        import pyarrow.parquet as pq

        integer_fields = {"permno", "permco", "fiscal_year"}
        float_fields = {"value"}
        names = list(rows[0])
        arrays = {}
        for name in names:
            arrow_type = pa.int64() if name in integer_fields else (pa.float64() if name in float_fields else pa.string())
            values = [row.get(name) for row in rows]
            if pa.types.is_string(arrow_type):
                values = [None if value is None else str(value) for value in values]
            arrays[name] = pa.array(values, type=arrow_type, from_pandas=True)
        table = pa.table(arrays)
        metadata = dict(table.schema.metadata or {})
        metadata[b"datatube_schema_version"] = b"fundamentals_pit.v1"
        metadata[b"datatube_source_version"] = SEC_BULK_IMPORT_VERSION.encode("utf-8")
        table = table.replace_schema_metadata(metadata)
        directory = root / "fundamentals_pit" / f"chunk={index:06d}" / "objects"
        directory.mkdir(parents=True, exist_ok=True)
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
        events = [str(row["event_time"]) for row in rows]
        uri = target.relative_to(BASE_DIR).as_posix() if target.is_relative_to(BASE_DIR) else str(target)
        return {
            "partition_key": f"chunk-{index:06d}", "start_time": min(events), "end_time": max(events),
            "row_count": len(rows), "file_uri": uri, "file_size": target.stat().st_size,
            "checksum": f"sha256:{digest}", "min_event_time": min(events),
            "max_event_time": max(events), "quality_status": "PASS",
        }

    def _checkpoint_partition(
        self, job_id: str, worker_id: str, entry_index: int, company_count: int,
        mapped_company_count: int, row_count: int, partition_count: int,
        partition: Mapping[str, Any], counters: Mapping[str, int],
    ) -> None:
        now = utc_now()
        quality = {"schema_version": "sec_bulk_quality_report.v1", "status": "PASS", "counters": dict(counters)}
        checkpoint = {"entry_index": entry_index, "company_count": company_count}
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO sec_bulk_import_partitions(
                       job_id,partition_index,partition_key,start_time,end_time,row_count,file_uri,
                       file_size,checksum,min_event_time,max_event_time,quality_status
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    job_id, partition_count, partition["partition_key"], partition["start_time"],
                    partition["end_time"], partition["row_count"], partition["file_uri"],
                    partition["file_size"], partition["checksum"], partition["min_event_time"],
                    partition["max_event_time"], partition["quality_status"],
                ),
            )
            updated = conn.execute(
                """UPDATE sec_bulk_import_jobs SET entry_index=?,company_count=?,mapped_company_count=?,
                       row_count=?,partition_count=?,quality_report_json=?,checkpoint_json=?,
                       heartbeat_at=?,updated_at=? WHERE job_id=? AND worker_id=? AND status='RUNNING'""",
                (
                    entry_index, company_count, mapped_company_count, row_count, partition_count,
                    json_dumps(quality), json_dumps(checkpoint), now, now, job_id, worker_id,
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeError("SEC bulk worker lost ownership")

    def _checkpoint_progress(
        self, job_id: str, worker_id: str, entry_index: int, company_count: int,
        mapped_company_count: int, counters: Mapping[str, int],
    ) -> None:
        now = utc_now()
        quality = {"schema_version": "sec_bulk_quality_report.v1", "status": "PASS", "counters": dict(counters)}
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                """UPDATE sec_bulk_import_jobs SET entry_index=?,company_count=?,mapped_company_count=?,
                       quality_report_json=?,checkpoint_json=?,heartbeat_at=?,updated_at=?
                   WHERE job_id=? AND worker_id=? AND status='RUNNING'""",
                (
                    entry_index, company_count, mapped_company_count, json_dumps(quality),
                    json_dumps({"entry_index": entry_index, "company_count": company_count}),
                    now, now, job_id, worker_id,
                ),
            )

    def _finalize(
        self, job_id: str, worker_id: str, company_count: int, mapped_company_count: int,
        row_count: int, counters: Mapping[str, int], source_fingerprint: str,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                "UPDATE sec_bulk_import_jobs SET status='FINALIZING',heartbeat_at=?,updated_at=? WHERE job_id=? AND worker_id=?",
                (now, now, job_id, worker_id),
            )
            partition_rows = conn.execute(
                "SELECT * FROM sec_bulk_import_partitions WHERE job_id=? ORDER BY partition_index", (job_id,)
            ).fetchall()
            job_row = conn.execute("SELECT * FROM sec_bulk_import_jobs WHERE job_id=?", (job_id,)).fetchone()
        job = self._decode(job_row)
        partitions = []
        for row in partition_rows:
            item = dict(row)
            item.pop("job_id", None)
            item.pop("partition_index", None)
            partitions.append(item)
        fingerprint = hashlib.sha256(json_dumps({
            "dataset_id": job["dataset_id"], "source_fingerprint": source_fingerprint,
            "schema_version": "fundamentals_pit.v1",
            "partitions": [(p["partition_key"], p["row_count"], p["checksum"]) for p in partitions],
        }).encode("utf-8")).hexdigest()
        checks = {
            "nonempty_output": row_count > 0,
            "all_partitions_passed_staging_checks": bool(partitions) and all(
                item["quality_status"] == "PASS" for item in partitions
            ),
            "partition_rows_match_job": sum(int(item["row_count"]) for item in partitions) == row_count,
            "mapping_available": int((job["mapping_report"] or {}).get("mapped_crsp_securities") or 0) > 0,
            "event_time_not_in_future": max(
                str(item["max_event_time"] or "") for item in partitions
            ) <= now,
        }
        quality_status = "PASS" if all(checks.values()) else "FAIL"
        quality = {
            "schema_version": "sec_bulk_quality_report.v1",
            "status": quality_status,
            "checks": checks,
            "counters": dict(counters),
        }
        if quality_status != "PASS":
            raise RuntimeError(f"SEC bulk Data Quality gate failed: {checks}")
        published = self.catalog.commit_manifests_atomically([{
            "catalog": {
                "dataset_id": job["dataset_id"], "instrument_id": "equity:CRSP:ALL",
                "data_type": "fundamentals_pit", "frequency": "event", "source": "SEC/COMPANYFACTS",
                "schema_version": "fundamentals_pit.v1",
                "storage_path": str(Path(job["staging_root"]) / "fundamentals_pit"),
                "row_count": row_count, "gap_count": 0, "quality_status": "PASS",
                "fields": [
                    "security_id", "instrument_id", "permno", "permco", "cik", "entity_name",
                    "taxonomy", "concept", "label", "unit", "value", "value_text", "period_start",
                    "period_end", "event_time", "filed_at", "accepted_at", "available_time", "form",
                    "fiscal_year", "fiscal_period", "frame", "accession_number", "source",
                ],
                "adjustment": "NONE", "time_semantics": "SOURCE_AVAILABLE_TIME",
                "point_in_time_policy": "FILED_OR_ACCEPTED_AT",
                "metadata": {
                    "full_import": True, "job_id": job_id, "source_fingerprint": source_fingerprint,
                    "normalizer_version": SEC_BULK_IMPORT_VERSION, "company_count": company_count,
                    "mapped_company_count": mapped_company_count, "mapping_report": job["mapping_report"],
                    "quality_report": quality,
                },
            },
            "dataset_fingerprint": fingerprint, "partitions": partitions,
        }])
        manifest = published[job["dataset_id"]]
        manifest_payload = {
            "manifest_id": manifest.manifest_id, "manifest_hash": manifest.manifest_hash,
            "partition_count": len(manifest.partitions),
            "row_count": sum(part.row_count for part in manifest.partitions), "status": manifest.status,
        }
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                """UPDATE sec_bulk_import_jobs SET status='READY',company_count=?,mapped_company_count=?,
                       row_count=?,quality_report_json=?,manifest_json=?,heartbeat_at=?,updated_at=?,
                       completed_at=?,error='' WHERE job_id=?""",
                (
                    company_count, mapped_company_count, row_count, json_dumps(quality),
                    json_dumps(manifest_payload), now, now, now, job_id,
                ),
            )
        result = self.get(job_id)
        if result is None:
            raise RuntimeError("SEC bulk job disappeared after completion")
        return result


def run_sec_bulk_import_job(
    store: DataPlatformStore, job_id: str, *, target_rows: int = 250_000
) -> dict[str, Any]:
    return SecBulkImportService(store).run(job_id, target_rows=target_rows)
