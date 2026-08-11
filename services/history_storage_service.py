from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable


BASE_DIR = Path(__file__).resolve().parent.parent
MARKER_NAME = ".datatube-history-root.json"
MARKER_SCHEMA_VERSION = "datatube_history_root.v1"
ARCHIVE_INVENTORY_SCHEMA_VERSION = "us_equity_archive_inventory.v1"
LAYOUT = {
    "workspace": "workspace",
    "platform": "platform",
    "metadata": "platform/metadata",
    "canonical": "platform/canonical",
    "research": "platform/research_artifacts",
    "strategy_history": "strategy-history",
    "sources": "sources",
    "imported": "imported",
    "migration": "migration",
}

_JOB_LOCK = threading.Lock()
_JOBS: dict[str, dict[str, Any]] = {}
_ACTIVE_JOB_ID = ""


def _settings(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    if settings is not None:
        return dict(settings)
    from services.config_loader import load_web_settings

    return load_web_settings()


def configured_history_data_root(settings: dict[str, Any] | None = None) -> Path | None:
    text = str(_settings(settings).get("history_data_root") or "").strip()
    return Path(text).expanduser() if text else None


def _read_marker(root: Path | None) -> dict[str, Any] | None:
    if root is None:
        return None
    try:
        payload = json.loads((root / MARKER_NAME).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema_version") != MARKER_SCHEMA_VERSION:
        return None
    if str(payload.get("state") or "").upper() != "READY":
        return None
    return payload


def active_history_data_root(settings: dict[str, Any] | None = None) -> Path | None:
    root = configured_history_data_root(settings)
    return root if _read_marker(root) else None


def get_history_workspace_db_path(settings: dict[str, Any] | None = None) -> Path:
    root = active_history_data_root(settings)
    return (root / LAYOUT["workspace"] / "history_workspace.db") if root else (BASE_DIR / "Data" / "history_workspace.db")


def get_data_platform_storage_root(settings: dict[str, Any] | None = None) -> Path:
    root = active_history_data_root(settings)
    return (root / LAYOUT["platform"]) if root else (BASE_DIR / "storage")


def get_data_platform_metadata_db_path(settings: dict[str, Any] | None = None) -> Path:
    return get_data_platform_storage_root(settings) / "metadata" / "data_platform.db"


def get_data_platform_canonical_root(settings: dict[str, Any] | None = None) -> Path:
    return get_data_platform_storage_root(settings) / "canonical"


def _normalized(path: Path) -> str:
    return os.path.normcase(str(path.expanduser().resolve(strict=False)))


def _relative_to(path: Path, parent: Path) -> Path | None:
    try:
        path_text = _normalized(path)
        parent_text = _normalized(parent)
        common = os.path.commonpath([path_text, parent_text])
        if common != parent_text:
            return None
        return Path(os.path.relpath(path_text, parent_text))
    except (OSError, ValueError):
        return None


def resolve_managed_history_path(
    raw_path: str | Path,
    *,
    base_dir: str | Path = BASE_DIR,
    settings: dict[str, Any] | None = None,
) -> Path:
    """Resolve legacy Manifest paths without mutating immutable Manifest rows."""

    base = Path(base_dir)
    path = Path(raw_path).expanduser()
    root = active_history_data_root(settings)
    if root is None:
        return path if path.is_absolute() else base / path
    if not path.is_absolute():
        parts = path.parts
        if parts and parts[0].lower() == "storage":
            return root / LAYOUT["platform"] / Path(*parts[1:])
        return base / path
    marker = _read_marker(root) or {}
    aliases = marker.get("path_aliases") if isinstance(marker.get("path_aliases"), list) else []
    for item in sorted(aliases, key=lambda value: len(str(value.get("source") or "")), reverse=True):
        if not isinstance(item, dict):
            continue
        source = Path(str(item.get("source") or ""))
        target = Path(str(item.get("target") or ""))
        if not str(source) or not str(target):
            continue
        relative = _relative_to(path, source)
        if relative is not None:
            return target / relative
    return path


def _directory_size(path: Path) -> tuple[int, int]:
    if path.is_file():
        return path.stat().st_size, 1
    total = 0
    count = 0
    for item in path.rglob("*"):
        if item.is_file() and not item.name.endswith(("-wal", "-shm")):
            try:
                total += item.stat().st_size
                count += 1
            except OSError:
                continue
    return total, count


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_folder_name(path: Path) -> str:
    name = path.name.strip().replace(" ", "-") or path.drive.rstrip(":\\/") or "source"
    cleaned = "".join(char if char.isalnum() or char in "._-" else "-" for char in name)
    return cleaned[:80] or "source"


def _path_entry(kind: str, source: Path, target: Path) -> dict[str, Any]:
    size, files = _directory_size(source)
    return {
        "kind": kind,
        "source": str(source),
        "target": str(target),
        "bytes": size,
        "files": files,
    }


class HistoryStorageService:
    def __init__(
        self,
        *,
        base_dir: str | Path = BASE_DIR,
        settings: dict[str, Any] | None = None,
        settings_saver: Any = None,
    ):
        self.base_dir = Path(base_dir).expanduser().resolve(strict=False)
        self.settings = _settings(settings)
        self.settings_saver = settings_saver

    @staticmethod
    def _validate_root(raw_root: str | Path) -> Path:
        text = str(raw_root or "").strip()
        if not text:
            raise ValueError("History Data root is required")
        root = Path(text).expanduser()
        if not root.is_absolute():
            raise ValueError("History Data root must be an absolute path")
        root = root.resolve(strict=False)
        if root == Path(root.anchor):
            raise ValueError("History Data root cannot be a drive or filesystem root")
        return root

    def _source_roots(self, values: Iterable[Any] | None) -> list[Path]:
        result: list[Path] = []
        source_values: Iterable[Any]
        if isinstance(values, str):
            source_values = values.replace("\r", "\n").split("\n")
        else:
            source_values = values or []
        for value in source_values:
            text = str(value or "").strip()
            if not text:
                continue
            path = Path(text).expanduser()
            if not path.is_absolute():
                raise ValueError(f"History source root must be absolute: {text}")
            path = path.resolve(strict=False)
            if path == Path(path.anchor):
                raise ValueError(f"History source root cannot be a drive root: {path}")
            if path not in result:
                result.append(path)
        return result

    def _discover_external_catalog_roots(self) -> list[Path]:
        result: list[Path] = []
        try:
            from services.data_platform import DatasetCatalogService, get_default_store

            for entry in DatasetCatalogService(get_default_store()).list_catalog():
                path = Path(str(entry.storage_path or "")).expanduser()
                if not path.is_absolute() or _relative_to(path, self.base_dir) is not None:
                    continue
                path = path.resolve(strict=False)
                if path.exists() and path not in result:
                    result.append(path)
        except Exception:
            return []
        return result

    def _suggest_source_roots(self) -> list[str]:
        suggestions: list[str] = []
        for storage_path in self._discover_external_catalog_roots():
            candidate = None
            for parent in (storage_path, *storage_path.parents):
                if parent.name.lower() == ".datatube":
                    candidate = parent.parent
                    break
            if candidate and candidate.is_dir() and str(candidate) not in suggestions:
                suggestions.append(str(candidate))
        return suggestions

    def plan(self, root_value: str | Path, source_roots: Iterable[Any] | None = None) -> dict[str, Any]:
        root = self._validate_root(root_value)
        sources = self._source_roots(source_roots)
        if _relative_to(root, self.base_dir) is not None:
            raise ValueError("History Data root must be outside the DataTube project directory")
        for source in sources:
            if not source.exists():
                raise ValueError(f"History source root does not exist: {source}")
            if _relative_to(root, source) is not None:
                raise ValueError(f"History Data root cannot be inside a source root: {source}")
            if _relative_to(source, root) is not None:
                raise ValueError(f"History source root cannot be inside the History Data root: {source}")
            if _relative_to(self.base_dir, source) is not None:
                raise ValueError(f"History source root cannot contain the DataTube project directory: {source}")

        entries: list[dict[str, Any]] = []
        current_root = active_history_data_root(self.settings)
        workspace_db = (
            current_root / LAYOUT["workspace"] / "history_workspace.db"
            if current_root
            else self.base_dir / "Data" / "history_workspace.db"
        )
        if workspace_db.is_file() and _relative_to(workspace_db, root) is None:
            entries.append(_path_entry("history_workspace", workspace_db, root / LAYOUT["workspace"] / workspace_db.name))

        platform_root = current_root / LAYOUT["platform"] if current_root else self.base_dir / "storage"
        if platform_root.is_dir() and _relative_to(platform_root, root) is None:
            entries.append(_path_entry("data_platform", platform_root, root / LAYOUT["platform"]))

        metrics_text = str(self.settings.get("strategy_metrics_db_dir") or "").strip()
        metrics_root = Path(metrics_text).expanduser() if metrics_text else self.base_dir / "strategy_metrics_dbs"
        if not metrics_root.is_absolute():
            metrics_root = self.base_dir / metrics_root
        metrics_root = metrics_root.resolve(strict=False)
        if metrics_root.is_dir() and _relative_to(metrics_root, root) is None:
            entries.append(_path_entry("strategy_history", metrics_root, root / LAYOUT["strategy_history"]))

        aliases: list[dict[str, str]] = [
            {"source": str(platform_root.resolve(strict=False)), "target": str((root / LAYOUT["platform"]).resolve(strict=False))}
        ]
        covered_roots: list[Path] = []
        for index, source in enumerate(sources, start=1):
            target = root / LAYOUT["sources"] / f"{index:02d}-{_safe_folder_name(source)}"
            entries.append(_path_entry("source_archive", source, target))
            aliases.append({"source": str(source), "target": str(target)})
            covered_roots.append(source)

        external_index = 0
        for source in self._discover_external_catalog_roots():
            if any(_relative_to(source, covered) is not None for covered in covered_roots):
                continue
            external_index += 1
            suffix = hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:8]
            target = root / LAYOUT["imported"] / f"{external_index:02d}-{_safe_folder_name(source)}-{suffix}"
            entries.append(_path_entry("external_catalog", source, target))
            aliases.append({"source": str(source), "target": str(target)})

        total_bytes = sum(int(item["bytes"]) for item in entries)
        existing = root
        while not existing.exists() and existing != existing.parent:
            existing = existing.parent
        free_bytes = shutil.disk_usage(existing).free
        return {
            "root": str(root),
            "layout": {key: str(root / value) for key, value in LAYOUT.items()},
            "entries": entries,
            "path_aliases": aliases,
            "total_bytes": total_bytes,
            "total_files": sum(int(item["files"]) for item in entries),
            "free_bytes": free_bytes,
            "enough_space": free_bytes >= total_bytes + max(1024**3, int(total_bytes * 0.05)),
            "copy_mode": "COPY_AND_VERIFY",
            "source_data_preserved": True,
        }

    def status(self) -> dict[str, Any]:
        root = configured_history_data_root(self.settings)
        marker = _read_marker(root)
        expected_workspace = root / LAYOUT["workspace"] / "history_workspace.db" if root else None
        runtime_workspace = None
        try:
            from services.history_data_service import HISTORY_DB_PATH

            runtime_workspace = Path(HISTORY_DB_PATH).resolve(strict=False)
        except Exception:
            pass
        with _JOB_LOCK:
            job = dict(_JOBS.get(_ACTIVE_JOB_ID, {})) if _ACTIVE_JOB_ID else None
        return {
            "configured_root": str(root) if root else "",
            "active_root": str(root) if marker else "",
            "state": "READY" if marker else ("CONFIGURED" if root else "UNCONFIGURED"),
            "marker": marker or {},
            "runtime_workspace_db": str(runtime_workspace) if runtime_workspace else "",
            "restart_required": bool(marker and expected_workspace and runtime_workspace != expected_workspace.resolve(strict=False)),
            "suggested_source_roots": self._suggest_source_roots(),
            "job": job,
        }

    def archive_coverage(self) -> dict[str, Any]:
        """Describe copied raw archives without presenting them as READY Manifests."""

        root = active_history_data_root(self.settings) or configured_history_data_root(self.settings)
        if root is None:
            return {
                "state": "UNCONFIGURED",
                "root": "",
                "inventory_path": "",
                "generated_at": "",
                "collections": [],
            }

        source_root = root / LAYOUT["sources"]
        candidates = sorted(source_root.glob("*/.datatube/inventory.json")) if source_root.is_dir() else []
        inventories: list[tuple[Path, dict[str, Any]]] = []
        for path in candidates:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict) or payload.get("schema_version") != ARCHIVE_INVENTORY_SCHEMA_VERSION:
                continue
            summary = payload.get("summary")
            if isinstance(summary, dict):
                inventories.append((path, payload))

        if not inventories:
            return {
                "state": "INVENTORY_MISSING",
                "root": str(root),
                "inventory_path": "",
                "generated_at": "",
                "collections": [],
            }

        inventory_path, inventory = max(
            inventories,
            key=lambda item: (
                int(item[1]["summary"].get("daily_stock_entries") or 0),
                int(item[1]["summary"].get("archive_bytes") or 0),
            ),
        )
        summary = inventory["summary"]
        archives = inventory.get("archives") if isinstance(inventory.get("archives"), list) else []

        quarter_labels: list[tuple[int, int]] = []
        crsp_ranges: list[tuple[str, str]] = []
        for archive in archives:
            if not isinstance(archive, dict):
                continue
            archive_path = str(archive.get("path") or "")
            quarter = re.search(r"(?P<year>20\d{2})_q(?P<quarter>[1-4])", archive_path, re.IGNORECASE)
            if quarter and int(archive.get("quarterly_option_entries") or 0) > 0:
                quarter_labels.append((int(quarter.group("year")), int(quarter.group("quarter"))))
            nested = archive.get("nested_zip_entries")
            if not isinstance(nested, list):
                continue
            for entry in nested:
                name = str(entry.get("name") or "") if isinstance(entry, dict) else ""
                match = re.search(r"(?P<start>\d{8})-(?P<end>\d{8})", name)
                if match:
                    start = match.group("start")
                    end = match.group("end")
                    crsp_ranges.append((f"{start[:4]}-{start[4:6]}-{start[6:]}", f"{end[:4]}-{end[4:6]}-{end[6:]}"))

        collections: list[dict[str, Any]] = []
        daily_stock_entries = int(summary.get("daily_stock_entries") or 0)
        if daily_stock_entries:
            collections.append({
                "id": "us_equity_daily_snapshots",
                "title": "美股全市场日快照",
                "asset_class": "equity",
                "data_type": "OHLCV 日线横截面",
                "status": "RAW_ARCHIVE",
                "status_label": "原始档案已索引",
                "start": str(summary.get("daily_stock_start") or ""),
                "end": str(summary.get("daily_stock_end") or ""),
                "metrics": [
                    {"value": daily_stock_entries, "label": "交易日快照"},
                    {"value": int(summary.get("daily_stock_archives") or 0), "label": "压缩包"},
                    {"value": int(summary.get("archive_failures") or 0), "label": "扫描失败"},
                ],
                "note": "每个交易日文件包含当日全市场股票横截面；标的数量会随上市、退市变化。",
            })

        daily_option_entries = int(summary.get("daily_option_entries") or 0)
        if daily_option_entries:
            collections.append({
                "id": "us_equity_daily_options",
                "title": "美股每日期权快照",
                "asset_class": "option",
                "data_type": "期权链 EOD",
                "status": "RAW_ARCHIVE",
                "status_label": "原始档案已索引",
                "start": str(summary.get("daily_option_start") or ""),
                "end": str(summary.get("daily_option_end") or ""),
                "metrics": [
                    {"value": daily_option_entries, "label": "交易日快照"},
                    {"value": int(summary.get("daily_option_archives") or 0), "label": "压缩包"},
                ],
                "note": "保留为期权链档案，尚未错误转换为 OHLCV bars。",
            })

        quarterly_option_entries = int(summary.get("quarterly_option_entries") or 0)
        if quarterly_option_entries:
            quarter_start = min(quarter_labels) if quarter_labels else None
            quarter_end = max(quarter_labels) if quarter_labels else None
            collections.append({
                "id": "firstrate_quarterly_options",
                "title": "FirstRate 季度期权链",
                "asset_class": "option",
                "data_type": "期权链明细",
                "status": "RAW_ARCHIVE",
                "status_label": "原始档案已索引",
                "start": f"{quarter_start[0]} Q{quarter_start[1]}" if quarter_start else "",
                "end": f"{quarter_end[0]} Q{quarter_end[1]}" if quarter_end else "",
                "metrics": [
                    {"value": quarterly_option_entries, "label": "档案条目"},
                    {"value": int(summary.get("quarterly_option_archives") or 0), "label": "压缩包"},
                ],
                "note": "等待 option_chain.eod 规范化，不计入可回测 K 线数据集。",
            })

        crsp_archives = int(summary.get("crsp_outer_archives") or 0)
        if crsp_archives:
            collections.append({
                "id": "crsp_ciz_daily",
                "title": "CRSP CIZ 美股日频",
                "asset_class": "equity",
                "data_type": "证券主表与日频行情",
                "status": "RAW_ARCHIVE",
                "status_label": "原始档案已索引",
                "start": min((item[0] for item in crsp_ranges), default=""),
                "end": max((item[1] for item in crsp_ranges), default=""),
                "metrics": [{"value": crsp_archives, "label": "外层档案"}],
                "note": "用于稳定证券身份、历史交易所归属、退市与公司行动；完整规范化状态单独管理。",
            })

        return {
            "state": "READY",
            "root": str(root),
            "inventory_path": str(inventory_path),
            "generated_at": str(inventory.get("generated_at") or ""),
            "archive_bytes": int(summary.get("archive_bytes") or 0),
            "archive_count": int(summary.get("archive_count") or 0),
            "archive_failures": int(summary.get("archive_failures") or 0),
            "collections": collections,
        }

    def start(self, root_value: str | Path, source_roots: Iterable[Any] | None = None) -> dict[str, Any]:
        global _ACTIVE_JOB_ID
        plan = self.plan(root_value, source_roots)
        if not plan["enough_space"]:
            raise ValueError("History Data root does not have enough free space for a verified copy")
        normalized_source_roots = [str(path) for path in self._source_roots(source_roots)]
        if self.settings_saver is None:
            from services.config_loader import save_web_settings

            settings_saver = save_web_settings
        else:
            settings_saver = self.settings_saver
        settings_saver({
            "history_data_root": plan["root"],
            "history_data_source_roots": normalized_source_roots,
        })
        with _JOB_LOCK:
            if _ACTIVE_JOB_ID and _JOBS.get(_ACTIVE_JOB_ID, {}).get("status") == "RUNNING":
                raise RuntimeError("A History Data normalization job is already running")
            job_id = f"history_normalize_{uuid.uuid4().hex}"
            now = time.time()
            _JOBS[job_id] = {
                "job_id": job_id,
                "status": "RUNNING",
                "phase": "PREPARING",
                "root": plan["root"],
                "total_bytes": plan["total_bytes"],
                "copied_bytes": 0,
                "total_files": plan["total_files"],
                "copied_files": 0,
                "started_at": now,
                "updated_at": now,
                "error": "",
                "restart_required": False,
            }
            _ACTIVE_JOB_ID = job_id
        thread = threading.Thread(
            target=self._run,
            args=(job_id, plan, normalized_source_roots),
            name="history-storage-normalize",
            daemon=True,
        )
        thread.start()
        return dict(_JOBS[job_id])

    def _update_job(self, job_id: str, **updates: Any) -> None:
        with _JOB_LOCK:
            job = _JOBS[job_id]
            job.update(updates)
            job["updated_at"] = time.time()

    def _copy_file(
        self,
        job_id: str,
        source: Path,
        target: Path,
        *,
        count_progress: bool = True,
    ) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        source_size = source.stat().st_size
        if target.is_file() and target.stat().st_size == source_size:
            if _sha256_file(source) == _sha256_file(target):
                if count_progress:
                    self._update_job(
                        job_id,
                        copied_bytes=int(_JOBS[job_id]["copied_bytes"]) + source_size,
                        copied_files=int(_JOBS[job_id]["copied_files"]) + 1,
                    )
                return
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.part")
        try:
            digest = hashlib.sha256()
            written = 0
            with source.open("rb") as reader, temporary.open("wb") as writer:
                for chunk in iter(lambda: reader.read(8 * 1024 * 1024), b""):
                    writer.write(chunk)
                    digest.update(chunk)
                    written += len(chunk)
                    if count_progress:
                        self._update_job(job_id, copied_bytes=int(_JOBS[job_id]["copied_bytes"]) + len(chunk))
                writer.flush()
                os.fsync(writer.fileno())
            if written != source_size or temporary.stat().st_size != source_size:
                raise IOError(f"Copied file size mismatch: {source}")
            shutil.copystat(source, temporary)
            if digest.hexdigest() != _sha256_file(temporary):
                raise IOError(f"Copied file checksum mismatch: {source}")
            os.replace(temporary, target)
            if count_progress:
                self._update_job(job_id, copied_files=int(_JOBS[job_id]["copied_files"]) + 1)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _backup_sqlite(
        self,
        job_id: str,
        source: Path,
        target: Path,
        *,
        count_progress: bool = True,
    ) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.part")
        try:
            source_uri = f"{source.resolve().as_uri()}?mode=ro"
            source_conn = sqlite3.connect(source_uri, uri=True, timeout=30.0)
            target_conn = sqlite3.connect(str(temporary), timeout=30.0)
            try:
                source_conn.backup(target_conn)
                result = target_conn.execute("PRAGMA quick_check").fetchone()
                if not result or str(result[0]).lower() != "ok":
                    raise IOError(f"SQLite integrity check failed: {source}")
            finally:
                target_conn.close()
                source_conn.close()
            os.replace(temporary, target)
            size = source.stat().st_size
            if count_progress:
                self._update_job(
                    job_id,
                    copied_bytes=int(_JOBS[job_id]["copied_bytes"]) + size,
                    copied_files=int(_JOBS[job_id]["copied_files"]) + 1,
                )
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _copy_entry(
        self,
        job_id: str,
        entry: dict[str, Any],
        *,
        count_progress: bool = True,
    ) -> None:
        source = Path(entry["source"])
        target = Path(entry["target"])
        if source.is_file():
            if source.suffix.lower() == ".db":
                self._backup_sqlite(job_id, source, target, count_progress=count_progress)
            else:
                self._copy_file(job_id, source, target, count_progress=count_progress)
            return
        for item in source.rglob("*"):
            if not item.is_file() or item.name.endswith(("-wal", "-shm")):
                continue
            destination = target / item.relative_to(source)
            if item.suffix.lower() == ".db" and entry["kind"] in {"data_platform", "strategy_history"}:
                self._backup_sqlite(job_id, item, destination, count_progress=count_progress)
            else:
                self._copy_file(job_id, item, destination, count_progress=count_progress)

    def _run(self, job_id: str, plan: dict[str, Any], source_roots: list[Any]) -> None:
        root = Path(plan["root"])
        try:
            self._update_job(job_id, phase="CREATING_LAYOUT")
            for value in LAYOUT.values():
                (root / value).mkdir(parents=True, exist_ok=True)
            for index, entry in enumerate(plan["entries"], start=1):
                self._update_job(job_id, phase=f"COPYING_{entry['kind'].upper()}", entry_index=index)
                self._copy_entry(job_id, entry)
            self._update_job(job_id, phase="FINAL_SYNC")
            for entry in plan["entries"]:
                if entry["kind"] in {"history_workspace", "data_platform", "strategy_history"}:
                    self._copy_entry(job_id, entry, count_progress=False)
            marker = {
                "schema_version": MARKER_SCHEMA_VERSION,
                "state": "READY",
                "root": str(root),
                "normalized_at": time.time(),
                "copy_mode": plan["copy_mode"],
                "source_data_preserved": True,
                "path_aliases": plan["path_aliases"],
                "entries": plan["entries"],
            }
            marker_path = root / MARKER_NAME
            temporary = marker_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, marker_path)
            if self.settings_saver is None:
                from services.config_loader import save_web_settings

                settings_saver = save_web_settings
            else:
                settings_saver = self.settings_saver
            settings_saver({
                "history_data_root": str(root),
                "history_data_source_roots": [str(Path(item).expanduser().resolve(strict=False)) for item in source_roots],
                "strategy_metrics_db_dir": str(root / LAYOUT["strategy_history"]),
            })
            self._update_job(job_id, status="SUCCEEDED", phase="READY", restart_required=True, finished_at=time.time())
        except Exception as exc:
            self._update_job(job_id, status="FAILED", phase="FAILED", error=str(exc), finished_at=time.time())


def get_history_storage_job(job_id: str) -> dict[str, Any] | None:
    with _JOB_LOCK:
        job = _JOBS.get(str(job_id))
        return dict(job) if job else None
