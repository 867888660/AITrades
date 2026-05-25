from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from services.config_loader import load_web_settings
from services.crypto_service import build_crypto_snapshot_row, fetch_crypto_quotes
from services.finance_service import build_finance_snapshot_row, fetch_finance_quotes
from services.sqlite_store import write_wide_snapshot


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _classify_state(payload: Dict[str, Any]) -> str:
    if not payload:
        return "pending"
    if payload.get("ok"):
        return "good"
    message = " ".join(str(x) for x in [payload.get("error"), *(payload.get("errors") or [])]).lower()
    if "missing finnhub api key" in message or "no finance symbols" in message or "no crypto symbols" in message:
        return "pending"
    return "error"


def _read_latest_snapshot_payload(db_path: str | None, table_name: str) -> Dict[str, Any] | None:
    path = Path(str(db_path or "").strip()).expanduser()
    if not str(path) or not path.exists():
        return None

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        tables = {str(row[0]) for row in conn.execute("select name from sqlite_master where type='table' order by name").fetchall()}
        if table_name not in tables:
            return None
        rows = conn.execute(f'SELECT * FROM "{table_name}" ORDER BY id DESC LIMIT 20').fetchall()
        for row in rows:
            raw_data = row["data"] if "data" in row.keys() else None
            if not raw_data:
                continue
            try:
                data = json.loads(raw_data)
            except (TypeError, json.JSONDecodeError):
                data = []
            if not isinstance(data, list) or not data:
                continue
            return {
                "ok": True,
                "data": data,
                "count": len(data),
                "ts_utc": row["ts_utc"] if "ts_utc" in row.keys() else None,
                "last_run_at": row["saved_at_utc"] if "saved_at_utc" in row.keys() else None,
                "status": "good",
                "history_loaded": True,
                "history_source": "sqlite",
                "db_path": str(path),
            }
    finally:
        conn.close()
    return None


def _payload_messages(payload: Dict[str, Any]) -> list[str]:
    messages: list[str] = []
    error = str(payload.get("error") or "").strip()
    if error:
        messages.append(error)
    for item in payload.get("errors") or []:
        text = str(item or "").strip()
        if text:
            messages.append(text)
    return messages


def _payload_has_usable_rows(payload: Dict[str, Any], value_keys: tuple[str, ...]) -> bool:
    rows = payload.get("data")
    if not isinstance(rows, list) or not rows:
        return False
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in value_keys:
            if row.get(key) not in (None, ""):
                return True
    return False


def _build_stale_state(
    previous: Dict[str, Any] | None,
    history: Dict[str, Any] | None,
    errors: list[str],
    attempted_at: str,
) -> Dict[str, Any] | None:
    base = dict(previous or {})
    fallback_source = "memory"
    if not (base.get("data") or []):
        base = dict(history or {})
        fallback_source = "sqlite"
    if not (base.get("data") or []):
        return None
    combined_errors = list(base.get("errors") or [])
    for item in errors:
        text = str(item or "").strip()
        if text and text not in combined_errors:
            combined_errors.append(text)
    base["ok"] = True
    base["stale"] = True
    base["fallback_source"] = fallback_source
    base["errors"] = combined_errors
    base["last_run_at"] = attempted_at
    base["status"] = "degraded"
    return base


class RealtimeCollector:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._next_crypto_at = 0.0
        self._next_finance_at = 0.0
        self._state: Dict[str, Any] = {
            "started_at": None,
            "crypto": {"ok": False, "data": [], "last_run_at": None, "errors": []},
            "finance": {"ok": False, "data": [], "last_run_at": None, "errors": []},
        }

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._prime_history_state(load_web_settings())
            self._running = True
            self._state["started_at"] = _now_iso()
            self._thread = threading.Thread(target=self._run_loop, daemon=True, name="realtime-collector")
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._running = False

    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def get_state(self) -> Dict[str, Any]:
        settings = load_web_settings()
        with self._lock:
            if not (self._state.get("crypto", {}) or {}).get("data") or not (self._state.get("finance", {}) or {}).get("data"):
                self._prime_history_state(settings)
            return {
                "running": self._running,
                "started_at": self._state.get("started_at"),
                "db_path": settings.get("sqlite_db_path"),
                "crypto": self._state.get("crypto", {}),
                "finance": self._state.get("finance", {}),
            }

    def _prime_history_state(self, settings: Dict[str, Any]) -> None:
        crypto_history = _read_latest_snapshot_payload(settings.get("sqlite_db_path"), "Crypto")
        finance_history = _read_latest_snapshot_payload(settings.get("sqlite_db_path"), "Stock")

        if crypto_history and not (self._state.get("crypto", {}) or {}).get("data"):
            self._state["crypto"] = crypto_history
        if finance_history and not (self._state.get("finance", {}) or {}).get("data"):
            self._state["finance"] = finance_history

    def _run_loop(self) -> None:
        while True:
            with self._lock:
                if not self._running:
                    return
            settings = load_web_settings()
            now = time.time()
            if now >= self._next_crypto_at:
                self._collect_crypto(settings)
                self._next_crypto_at = now + max(2, int(settings.get("crypto_refresh_sec", 15)))
            if now >= self._next_finance_at:
                self._collect_finance(settings)
                self._next_finance_at = now + max(2, int(settings.get("finance_refresh_sec", 20)))
            time.sleep(1)

    def _collect_crypto(self, settings: Dict[str, Any]) -> None:
        try:
            payload = fetch_crypto_quotes(
                settings.get("crypto_symbols", []),
                include_fundamentals=bool(settings.get("include_crypto_fundamentals", True)),
                config=settings,
            )
            attempted_at = _now_iso()
            usable = bool(payload.get("ok")) and _payload_has_usable_rows(payload, ("price",))
            if usable:
                write_info = write_wide_snapshot(
                    settings.get("sqlite_db_path"),
                    "Crypto",
                    build_crypto_snapshot_row(payload),
                    saved_at_utc=payload.get("ts_utc"),
                )
                payload["last_run_at"] = attempted_at
                payload["db_write"] = write_info
                payload["errors"] = _payload_messages(payload)
                payload["stale"] = False
                payload["fallback_source"] = None
                payload["status"] = _classify_state(payload)
                with self._lock:
                    self._state["crypto"] = payload
                return
            with self._lock:
                previous = dict(self._state.get("crypto", {}) or {})
            history = _read_latest_snapshot_payload(settings.get("sqlite_db_path"), "Crypto")
            stale_payload = _build_stale_state(previous, history, _payload_messages(payload), attempted_at)
            if stale_payload is not None:
                with self._lock:
                    self._state["crypto"] = stale_payload
                return
            payload["last_run_at"] = attempted_at
            payload["errors"] = _payload_messages(payload)
            payload["stale"] = False
            payload["fallback_source"] = None
            payload["status"] = _classify_state(payload)
            with self._lock:
                self._state["crypto"] = payload
        except Exception as exc:  # pragma: no cover
            attempted_at = _now_iso()
            with self._lock:
                previous = dict(self._state.get("crypto", {}) or {})
            history = _read_latest_snapshot_payload(settings.get("sqlite_db_path"), "Crypto")
            stale_payload = _build_stale_state(previous, history, [str(exc)], attempted_at)
            if stale_payload is not None:
                with self._lock:
                    self._state["crypto"] = stale_payload
                return
            with self._lock:
                self._state["crypto"] = {
                    "ok": False,
                    "data": [],
                    "errors": [str(exc)],
                    "last_run_at": attempted_at,
                    "stale": False,
                    "fallback_source": None,
                    "status": "error",
                }

    def _collect_finance(self, settings: Dict[str, Any]) -> None:
        try:
            payload = fetch_finance_quotes(
                settings.get("finance_symbols", []),
                api_key=settings.get("active_finnhub_api_key") or None,
            )
            attempted_at = _now_iso()
            usable = bool(payload.get("ok")) and _payload_has_usable_rows(
                payload,
                ("price", "market_cap_usd", "company_name", "exchange"),
            )
            if usable:
                write_info = write_wide_snapshot(
                    settings.get("sqlite_db_path"),
                    "Stock",
                    build_finance_snapshot_row(payload),
                    saved_at_utc=payload.get("ts_utc"),
                )
                payload["db_write"] = write_info
                payload["last_run_at"] = attempted_at
                payload["errors"] = _payload_messages(payload)
                payload["stale"] = False
                payload["fallback_source"] = None
                payload["status"] = _classify_state(payload)
                with self._lock:
                    self._state["finance"] = payload
                return
            with self._lock:
                previous = dict(self._state.get("finance", {}) or {})
            history = _read_latest_snapshot_payload(settings.get("sqlite_db_path"), "Stock")
            stale_payload = _build_stale_state(previous, history, _payload_messages(payload), attempted_at)
            if stale_payload is not None:
                with self._lock:
                    self._state["finance"] = stale_payload
                return
            payload["last_run_at"] = attempted_at
            payload["errors"] = _payload_messages(payload)
            payload["stale"] = False
            payload["fallback_source"] = None
            payload["status"] = _classify_state(payload)
            with self._lock:
                self._state["finance"] = payload
        except Exception as exc:  # pragma: no cover
            attempted_at = _now_iso()
            with self._lock:
                previous = dict(self._state.get("finance", {}) or {})
            history = _read_latest_snapshot_payload(settings.get("sqlite_db_path"), "Stock")
            stale_payload = _build_stale_state(previous, history, [str(exc)], attempted_at)
            if stale_payload is not None:
                with self._lock:
                    self._state["finance"] = stale_payload
                return
            with self._lock:
                self._state["finance"] = {
                    "ok": False,
                    "data": [],
                    "errors": [str(exc)],
                    "last_run_at": attempted_at,
                    "stale": False,
                    "fallback_source": None,
                    "status": "error",
                }


collector = RealtimeCollector()
