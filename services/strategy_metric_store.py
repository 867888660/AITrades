from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Tuple

from services.strategy_data_source import connect as ds_connect


_DDL = """
CREATE TABLE IF NOT EXISTS strategy_metric_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id     INTEGER NOT NULL,
    tick_id         INTEGER,
    run_at_utc      TEXT    NOT NULL,
    metric_key      TEXT    NOT NULL,
    metric_kind     TEXT    NOT NULL DEFAULT '',
    metric_type     TEXT    NOT NULL DEFAULT '',
    number_value    REAL,
    text_value      TEXT,
    bool_value      INTEGER,
    raw_value_json  TEXT,
    value_hash      TEXT    NOT NULL DEFAULT '',
    value_state     TEXT    NOT NULL DEFAULT 'value',
    metric_label    TEXT,
    unit            TEXT,
    panel           TEXT,
    meta_json       TEXT,
    created_at_utc  TEXT    NOT NULL,
    FOREIGN KEY (strategy_id) REFERENCES strategy_registry(strategy_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_strategy_metric_events_series
ON strategy_metric_events(strategy_id, metric_key, run_at_utc DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_strategy_metric_events_run
ON strategy_metric_events(strategy_id, run_at_utc DESC, id DESC);
"""

_CATALOG_HIDE_BY_DEFAULT: set = set()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    conn.commit()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _safe_key(value: Any) -> str:
    return str(value or "").strip()


def _normalize_metric(metric_key: str, raw_value: Any, meta: Dict[str, Any] | None = None) -> Dict[str, Any] | None:
    key = _safe_key(metric_key)
    if not key:
        return None
    meta = dict(meta or {})
    value = raw_value
    if isinstance(raw_value, dict) and "value" in raw_value:
        value = raw_value.get("value")
        for field in ("kind", "label", "unit", "panel", "states", "bands", "color"):
            if field in raw_value and field not in meta:
                meta[field] = raw_value.get(field)

    value_state = "null" if value is None else "value"
    metric_type = "null"
    number_value = None
    text_value = None
    bool_value = None
    if isinstance(value, bool):
        metric_type = "bool"
        bool_value = 1 if value else 0
    elif isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
        metric_type = "number"
        number_value = float(value)
    elif value is None:
        metric_type = "null"
    elif isinstance(value, str):
        metric_type = "text"
        text_value = value
    else:
        metric_type = "json"

    metric_kind = str(meta.get("kind") or "").strip().lower()
    if not metric_kind:
        if metric_type == "number":
            metric_kind = "continuous"
        elif metric_type in {"text", "bool"}:
            metric_kind = "state"
        else:
            metric_kind = "event" if metric_type == "json" else "unknown"

    return {
        "metric_key": key,
        "metric_kind": metric_kind,
        "metric_type": metric_type,
        "number_value": number_value,
        "text_value": text_value,
        "bool_value": bool_value,
        "raw_value_json": _json_dumps(value),
        "value_hash": _json_dumps({"state": value_state, "value": value}),
        "value_state": value_state,
        "metric_label": str(meta.get("label") or key),
        "unit": str(meta.get("unit") or ""),
        "panel": str(meta.get("panel") or ""),
        "meta_json": _json_dumps(meta) if meta else None,
    }


def _latest_hashes(conn: sqlite3.Connection, strategy_id: int, keys: Iterable[str]) -> Dict[str, str]:
    output: Dict[str, str] = {}
    for key in keys:
        row = conn.execute(
            """
            SELECT value_hash
            FROM strategy_metric_events
            WHERE strategy_id = ? AND metric_key = ?
            ORDER BY run_at_utc DESC, id DESC
            LIMIT 1
            """,
            (int(strategy_id), key),
        ).fetchone()
        if row:
            output[key] = str(row["value_hash"] or "")
    return output


def write_metric_events(
    strategy_id: int,
    tick_id: int | None,
    run_at_utc: str,
    metrics: Dict[str, Any] | None,
    metrics_meta: Dict[str, Any] | None = None,
    *,
    conn: sqlite3.Connection | None = None,
) -> Dict[str, Any]:
    if not isinstance(metrics, dict) or not metrics:
        return {"written": 0, "skipped": 0}
    own_conn = conn is None
    if own_conn:
        conn = ds_connect()
    assert conn is not None
    conn.row_factory = sqlite3.Row
    try:
        _ensure_schema(conn)
        normalized = []
        for key, value in metrics.items():
            item_meta = (metrics_meta or {}).get(key) if isinstance(metrics_meta, dict) else None
            item = _normalize_metric(str(key), value, item_meta if isinstance(item_meta, dict) else None)
            if item:
                normalized.append(item)
        latest = _latest_hashes(conn, int(strategy_id), [item["metric_key"] for item in normalized])
        ts = _now_iso()
        written = 0
        skipped = 0
        for item in normalized:
            if latest.get(item["metric_key"]) == item["value_hash"]:
                skipped += 1
                continue
            conn.execute(
                """
                INSERT INTO strategy_metric_events (
                    strategy_id, tick_id, run_at_utc, metric_key, metric_kind, metric_type,
                    number_value, text_value, bool_value, raw_value_json, value_hash, value_state,
                    metric_label, unit, panel, meta_json, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(strategy_id),
                    int(tick_id) if tick_id is not None else None,
                    str(run_at_utc),
                    item["metric_key"],
                    item["metric_kind"],
                    item["metric_type"],
                    item["number_value"],
                    item["text_value"],
                    item["bool_value"],
                    item["raw_value_json"],
                    item["value_hash"],
                    item["value_state"],
                    item["metric_label"],
                    item["unit"],
                    item["panel"],
                    item["meta_json"],
                    ts,
                ),
            )
            written += 1
        conn.commit()
        return {"written": written, "skipped": skipped}
    finally:
        if own_conn:
            conn.close()


def list_metric_catalog(strategy_id: int, limit: int = 200) -> Dict[str, Any]:
    conn = ds_connect()
    conn.row_factory = sqlite3.Row
    try:
        _ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT e.*
            FROM strategy_metric_events e
            JOIN (
                SELECT metric_key, MAX(id) AS max_id
                FROM strategy_metric_events
                WHERE strategy_id = ?
                GROUP BY metric_key
            ) latest ON latest.max_id = e.id
            ORDER BY e.metric_key
            LIMIT ?
            """,
            (int(strategy_id), int(limit)),
        ).fetchall()
        counts = {
            str(row["metric_key"]): int(row["count"])
            for row in conn.execute(
                """
                SELECT metric_key, COUNT(*) AS count
                FROM strategy_metric_events
                WHERE strategy_id = ?
                GROUP BY metric_key
                """,
                (int(strategy_id),),
            ).fetchall()
        }
        items = []
        for row in rows:
            item = _row_to_catalog_item(row, counts.get(str(row["metric_key"]), 0))
            if item["key"] in _CATALOG_HIDE_BY_DEFAULT and not item.get("meta", {}).get("display"):
                continue
            items.append(item)
        return {
            "items": items,
            "numeric": [item for item in items if item["metric_type"] == "number" and item["value_state"] == "value"],
            "state": [item for item in items if item["kind"] == "state" and item["value_state"] == "value"],
        }
    finally:
        conn.close()


def _row_to_catalog_item(row: sqlite3.Row, count: int) -> Dict[str, Any]:
    raw_value = None
    try:
        raw_value = json.loads(row["raw_value_json"] or "null")
    except Exception:
        raw_value = row["raw_value_json"]
    meta = {}
    try:
        meta = json.loads(row["meta_json"] or "{}")
    except Exception:
        meta = {}
    return {
        "key": str(row["metric_key"] or ""),
        "label": str(row["metric_label"] or row["metric_key"] or ""),
        "kind": str(row["metric_kind"] or ""),
        "metric_type": str(row["metric_type"] or ""),
        "unit": str(row["unit"] or ""),
        "panel": str(row["panel"] or ""),
        "value_state": str(row["value_state"] or ""),
        "latest_value": raw_value,
        "latest_ts": str(row["run_at_utc"] or ""),
        "count": count,
        "meta": meta if isinstance(meta, dict) else {},
    }


def load_metric_events(
    strategy_id: int,
    keys: Iterable[str],
    from_ts: str,
    to_ts: str,
) -> Dict[str, List[Dict[str, Any]]]:
    key_list = [_safe_key(key) for key in keys if _safe_key(key)]
    if not key_list:
        return {}
    conn = ds_connect()
    conn.row_factory = sqlite3.Row
    try:
        _ensure_schema(conn)
        output: Dict[str, List[Dict[str, Any]]] = {key: [] for key in key_list}
        placeholders = ",".join("?" for _ in key_list)
        prior_rows = conn.execute(
            f"""
            SELECT *
            FROM strategy_metric_events
            WHERE strategy_id = ?
              AND metric_key IN ({placeholders})
              AND run_at_utc < ?
            ORDER BY metric_key ASC, run_at_utc DESC, id DESC
            """,
            (int(strategy_id), *key_list, str(from_ts)),
        ).fetchall()
        seen_prior = set()
        for row in prior_rows:
            key = str(row["metric_key"] or "")
            if key in seen_prior:
                continue
            seen_prior.add(key)
            output.setdefault(key, []).append(_event_row(row))
        rows = conn.execute(
            f"""
            SELECT *
            FROM strategy_metric_events
            WHERE strategy_id = ?
              AND metric_key IN ({placeholders})
              AND run_at_utc >= ?
              AND run_at_utc <= ?
            ORDER BY run_at_utc ASC, id ASC
            """,
            (int(strategy_id), *key_list, str(from_ts), str(to_ts)),
        ).fetchall()
        for row in rows:
            output.setdefault(str(row["metric_key"] or ""), []).append(_event_row(row))
        return output
    finally:
        conn.close()


def _event_row(row: sqlite3.Row) -> Dict[str, Any]:
    value = None
    if row["value_state"] != "null":
        if row["metric_type"] == "number":
            value = row["number_value"]
        elif row["metric_type"] == "bool":
            value = bool(row["bool_value"])
        elif row["metric_type"] == "text":
            value = row["text_value"]
        else:
            try:
                value = json.loads(row["raw_value_json"] or "null")
            except Exception:
                value = row["raw_value_json"]
    return {
        "ts": str(row["run_at_utc"] or ""),
        "key": str(row["metric_key"] or ""),
        "kind": str(row["metric_kind"] or ""),
        "type": str(row["metric_type"] or ""),
        "value_state": str(row["value_state"] or ""),
        "value": value,
        "label": str(row["metric_label"] or row["metric_key"] or ""),
        "unit": str(row["unit"] or ""),
        "panel": str(row["panel"] or ""),
    }
