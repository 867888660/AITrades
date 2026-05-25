from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from services.config_loader import BASE_DIR, load_web_settings


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def strategy_metrics_db_directory() -> Path:
    settings = load_web_settings()
    text = str(settings.get("strategy_metrics_db_dir", "") or "").strip()
    if not text:
        return BASE_DIR / "strategy_metrics_dbs"
    return Path(text).expanduser()


def sanitize_translation_for_filename(translation: str, row_id: int | None = None) -> str:
    raw = str(translation or "").strip() or "unnamed_strategy"
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw)
    safe = re.sub(r"\s+", "_", safe).strip("._") or "unnamed_strategy"
    if len(safe) > 120:
        safe = safe[:120]
    if row_id is not None:
        safe = f"{safe}__r{int(row_id)}"
    return safe


def _ensure_stats_db(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS strategy_stats (
            monitoring_row_id INTEGER PRIMARY KEY,
            updated_at_utc TEXT NOT NULL,
            translation TEXT,
            condition_id TEXT,
            question TEXT,
            strategy TEXT,
            initial_capital REAL,
            profit_roll_ratio REAL,
            realized_profit REAL,
            strategy_bankroll REAL,
            yes_qty REAL,
            no_qty REAL,
            yes_avg REAL,
            no_avg REAL,
            yes_pic REAL,
            no_pic REAL,
            strategy_pnl REAL,
            position_source TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS strategy_stats_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            monitoring_row_id INTEGER NOT NULL,
            updated_at_utc TEXT NOT NULL,
            translation TEXT,
            condition_id TEXT,
            question TEXT,
            strategy TEXT,
            initial_capital REAL,
            profit_roll_ratio REAL,
            realized_profit REAL,
            strategy_bankroll REAL,
            yes_qty REAL,
            no_qty REAL,
            yes_avg REAL,
            no_avg REAL,
            yes_pic REAL,
            no_pic REAL,
            strategy_pnl REAL,
            position_source TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_strategy_stats_history_row_time
        ON strategy_stats_history(monitoring_row_id, updated_at_utc DESC)
        """
    )
    conn.commit()


def get_strategy_stats_db_path(strategy_item: Dict[str, Any]) -> Path | None:
    row_id = strategy_item.get("row_id")
    if row_id is None:
        return None
    raw = strategy_item.get("raw") or {}
    translation = raw.get("Translation") or strategy_item.get("question") or ""
    base = sanitize_translation_for_filename(str(translation), row_id=int(row_id))
    return strategy_metrics_db_directory() / f"{base}.db"


def load_latest_valid_position_snapshot(strategy_item: Dict[str, Any]) -> Dict[str, Any] | None:
    db_path = get_strategy_stats_db_path(strategy_item)
    row_id = strategy_item.get("row_id")
    if db_path is None or row_id is None or not db_path.exists():
        return None

    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        tables = {str(row[0]) for row in conn.execute("select name from sqlite_master where type='table'").fetchall()}
        table_name = "strategy_stats_history" if "strategy_stats_history" in tables else "strategy_stats"
        if table_name not in tables:
            return None
        order_by = "updated_at_utc DESC"
        if table_name == "strategy_stats_history":
            order_by = "updated_at_utc DESC, id DESC"
        row = conn.execute(
            f"""
            SELECT updated_at_utc, yes_qty, no_qty, yes_avg, no_avg, yes_pic, no_pic, strategy_pnl, position_source
            FROM "{table_name}"
            WHERE monitoring_row_id = ?
              AND COALESCE(position_source, '') != 'monitoring_db_wallet_api_unavailable'
            ORDER BY {order_by}
            LIMIT 1
            """,
            (int(row_id),),
        ).fetchone()
        if row is None:
            return None
        return {
            "updated_at_utc": str(row["updated_at_utc"] or ""),
            "yes_qty": _safe_float(row["yes_qty"]),
            "no_qty": _safe_float(row["no_qty"]),
            "yes_avg": _safe_float(row["yes_avg"]),
            "no_avg": _safe_float(row["no_avg"]),
            "yes_position": _safe_float(row["yes_pic"]),
            "no_position": _safe_float(row["no_pic"]),
            "strategy_pnl": _safe_float(row["strategy_pnl"]),
            "position_source": str(row["position_source"] or "").strip(),
        }
    finally:
        conn.close()


def persist_strategy_row_stat(strategy_item: Dict[str, Any]) -> Dict[str, Any] | None:
    db_path = get_strategy_stats_db_path(strategy_item)
    row_id = strategy_item.get("row_id")
    if db_path is None or row_id is None:
        return None
    if str(strategy_item.get("position_source") or "").strip() == "monitoring_db_wallet_api_unavailable":
        return None
    db_path.parent.mkdir(parents=True, exist_ok=True)
    raw = strategy_item.get("raw") or {}
    translation = raw.get("Translation") or strategy_item.get("question") or ""
    snapshot = (
        int(row_id),
        _now_iso(),
        str(translation or ""),
        str(strategy_item.get("condition_id") or ""),
        str(strategy_item.get("question") or ""),
        str(strategy_item.get("strategy") or ""),
        _safe_float(raw.get("initial_capital")),
        _safe_float(raw.get("profit_roll_ratio")),
        _safe_float(raw.get("realized_profit")),
        _safe_float(strategy_item.get("strategy_bankroll")),
        _safe_float(strategy_item.get("yes_qty")),
        _safe_float(strategy_item.get("no_qty")),
        _safe_float(strategy_item.get("yes_avg")),
        _safe_float(strategy_item.get("no_avg")),
        _safe_float(strategy_item.get("yes_position")),
        _safe_float(strategy_item.get("no_position")),
        _safe_float(strategy_item.get("strategy_pnl")),
        str(strategy_item.get("position_source") or ""),
    )

    conn = sqlite3.connect(str(db_path), timeout=5.0)
    try:
        _ensure_stats_db(conn)
        conn.execute(
            """
            INSERT INTO strategy_stats (
                monitoring_row_id, updated_at_utc, translation, condition_id, question, strategy,
                initial_capital, profit_roll_ratio, realized_profit, strategy_bankroll,
                yes_qty, no_qty, yes_avg, no_avg, yes_pic, no_pic, strategy_pnl, position_source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(monitoring_row_id) DO UPDATE SET
                updated_at_utc = excluded.updated_at_utc,
                translation = excluded.translation,
                condition_id = excluded.condition_id,
                question = excluded.question,
                strategy = excluded.strategy,
                initial_capital = excluded.initial_capital,
                profit_roll_ratio = excluded.profit_roll_ratio,
                realized_profit = excluded.realized_profit,
                strategy_bankroll = excluded.strategy_bankroll,
                yes_qty = excluded.yes_qty,
                no_qty = excluded.no_qty,
                yes_avg = excluded.yes_avg,
                no_avg = excluded.no_avg,
                yes_pic = excluded.yes_pic,
                no_pic = excluded.no_pic,
                strategy_pnl = excluded.strategy_pnl,
                position_source = excluded.position_source
            """,
            snapshot,
        )
        conn.execute(
            """
            INSERT INTO strategy_stats_history (
                monitoring_row_id, updated_at_utc, translation, condition_id, question, strategy,
                initial_capital, profit_roll_ratio, realized_profit, strategy_bankroll,
                yes_qty, no_qty, yes_avg, no_avg, yes_pic, no_pic, strategy_pnl, position_source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            snapshot,
        )
        conn.commit()
        return {"db_path": str(db_path), "monitoring_row_id": int(row_id)}
    finally:
        conn.close()


def sync_all_strategy_stats(items: list[Dict[str, Any]]) -> Dict[str, Any]:
    written: list[Dict[str, Any]] = []
    errors: list[str] = []
    for item in items:
        try:
            info = persist_strategy_row_stat(item)
            if info:
                written.append(info)
        except Exception as exc:  # pragma: no cover
            errors.append(str(exc))
    return {
        "ok": not errors,
        "directory": str(strategy_metrics_db_directory()),
        "files_touched": len(written),
        "sample": written[:20],
        "errors": errors,
    }
