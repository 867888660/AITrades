from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

from services.strategy_data_source import connect as ds_connect


MODE_VIRTUAL = "Virtual"
MODE_REAL = "Real"


_DDL_AUDIT = """
CREATE TABLE IF NOT EXISTS strategy_run_ticks (
    tick_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id         INTEGER NOT NULL,
    mode                TEXT    NOT NULL CHECK(mode IN ('Virtual', 'Real')),
    run_at_utc          TEXT    NOT NULL,
    duration_ms         REAL    NOT NULL DEFAULT 0,
    function_json       TEXT,
    function_json_hash  TEXT,
    mode_output         TEXT,
    error               TEXT,
    actions_count       INTEGER NOT NULL DEFAULT 0,
    orders_count        INTEGER NOT NULL DEFAULT 0,
    created_at_utc      TEXT    NOT NULL,
    updated_at_utc      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_run_events (
    event_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    tick_id         INTEGER,
    strategy_id     INTEGER NOT NULL,
    mode            TEXT    NOT NULL CHECK(mode IN ('Virtual', 'Real')),
    event_type      TEXT    NOT NULL CHECK(event_type IN ('print', 'error', 'decision', 'order_update')),
    content         TEXT    NOT NULL DEFAULT '',
    content_hash    TEXT    NOT NULL DEFAULT '',
    repeat_count    INTEGER NOT NULL DEFAULT 1,
    first_seen_utc  TEXT    NOT NULL,
    last_seen_utc   TEXT    NOT NULL,
    created_at_utc  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_action_events (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    tick_id                 INTEGER,
    strategy_id             INTEGER NOT NULL,
    mode                    TEXT    NOT NULL CHECK(mode IN ('Virtual', 'Real')),
    action_type             TEXT    NOT NULL DEFAULT '',
    leg_index               INTEGER NOT NULL DEFAULT 0,
    side                    TEXT,
    qty                     REAL,
    price                   REAL,
    status                  TEXT    NOT NULL DEFAULT '',
    reason                  TEXT,
    order_ref               TEXT,
    raw_action_json         TEXT,
    raw_function_json_hash  TEXT,
    created_at_utc          TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_strategy_run_ticks_strategy
ON strategy_run_ticks(strategy_id, mode, run_at_utc DESC);

CREATE INDEX IF NOT EXISTS idx_strategy_run_events_strategy
ON strategy_run_events(strategy_id, mode, last_seen_utc DESC);

CREATE INDEX IF NOT EXISTS idx_strategy_run_events_tick
ON strategy_run_events(tick_id, event_type);

CREATE INDEX IF NOT EXISTS idx_strategy_action_events_strategy
ON strategy_action_events(strategy_id, mode, created_at_utc DESC);

CREATE INDEX IF NOT EXISTS idx_strategy_action_events_tick
ON strategy_action_events(tick_id);

CREATE INDEX IF NOT EXISTS idx_strategy_action_events_order_ref
ON strategy_action_events(order_ref);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def content_hash(content: str) -> str:
    return hashlib.sha256(str(content or "").encode("utf-8")).hexdigest()[:16]


def json_hash(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    return content_hash(raw)


def normalize_mode(mode: str) -> str:
    value = str(mode or "").strip().lower()
    return MODE_REAL if value == "real" else MODE_VIRTUAL


_audit_schema_ensured: bool = False


def _ensure_schema(conn: sqlite3.Connection) -> None:
    global _audit_schema_ensured
    if _audit_schema_ensured:
        return
    conn.executescript(_DDL_AUDIT)
    conn.commit()
    _audit_schema_ensured = True


def ensure_schema() -> None:
    conn = ds_connect()
    try:
        _ensure_schema(conn)
    finally:
        conn.close()


def create_run_tick(strategy_id: int, mode: str, run_at_utc: Optional[str] = None, *, conn: Optional[sqlite3.Connection] = None) -> int:
    own_conn = conn is None
    if own_conn:
        conn = ds_connect()
    try:
        _ensure_schema(conn)
        ts = run_at_utc or now_iso()
        cur = conn.execute(
            """
            INSERT INTO strategy_run_ticks (
                strategy_id, mode, run_at_utc, created_at_utc, updated_at_utc
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (int(strategy_id), normalize_mode(mode), ts, ts, ts),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        if own_conn:
            conn.close()


def update_run_tick(
    tick_id: int,
    *,
    duration_ms: float,
    function_json: Optional[str],
    mode_output: Optional[str],
    error: Optional[str],
    actions_count: int,
    orders_count: int,
    conn: Optional[sqlite3.Connection] = None,
) -> None:
    own_conn = conn is None
    if own_conn:
        conn = ds_connect()
    try:
        _ensure_schema(conn)
        conn.execute(
            """
            UPDATE strategy_run_ticks
            SET duration_ms = ?,
                function_json = ?,
                function_json_hash = ?,
                mode_output = ?,
                error = ?,
                actions_count = ?,
                orders_count = ?,
                updated_at_utc = ?
            WHERE tick_id = ?
            """,
            (
                float(duration_ms or 0),
                function_json,
                json_hash(function_json),
                mode_output,
                error,
                int(actions_count or 0),
                int(orders_count or 0),
                now_iso(),
                int(tick_id),
            ),
        )
        conn.commit()
    finally:
        if own_conn:
            conn.close()


def write_run_event(
    strategy_id: int,
    mode: str,
    event_type: str,
    content: str,
    *,
    tick_id: Optional[int] = None,
    aggregate: bool = True,
    conn: Optional[sqlite3.Connection] = None,
) -> None:
    own_conn = conn is None
    if own_conn:
        conn = ds_connect()
    try:
        _ensure_schema(conn)
        _write_run_event_conn(
            conn,
            strategy_id,
            mode,
            event_type,
            content,
            tick_id=tick_id,
            aggregate=aggregate,
        )
        conn.commit()
    finally:
        if own_conn:
            conn.close()


def _write_run_event_conn(
    conn: sqlite3.Connection,
    strategy_id: int,
    mode: str,
    event_type: str,
    content: str,
    *,
    tick_id: Optional[int] = None,
    aggregate: bool = True,
) -> None:
    ts = now_iso()
    mode_norm = normalize_mode(mode)
    event_type_norm = str(event_type or "print").strip().lower()
    content_text = str(content or "")
    ch = content_hash(content_text)

    if aggregate:
        row = conn.execute(
            """
            SELECT event_id
            FROM strategy_run_events
            WHERE strategy_id = ?
              AND mode = ?
              AND event_type = ?
              AND content_hash = ?
            ORDER BY event_id DESC
            LIMIT 1
            """,
            (int(strategy_id), mode_norm, event_type_norm, ch),
        ).fetchone()
        if row:
            conn.execute(
                """
                UPDATE strategy_run_events
                SET repeat_count = repeat_count + 1,
                    tick_id = COALESCE(?, tick_id),
                    last_seen_utc = ?
                WHERE event_id = ?
                """,
                (tick_id, ts, int(row[0])),
            )
            return

    conn.execute(
        """
        INSERT INTO strategy_run_events (
            tick_id, strategy_id, mode, event_type, content, content_hash,
            repeat_count, first_seen_utc, last_seen_utc, created_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
        """,
        (tick_id, int(strategy_id), mode_norm, event_type_norm, content_text, ch, ts, ts, ts),
    )


def write_run_events(
    strategy_id: int,
    mode: str,
    event_type: str,
    messages: Iterable[Any],
    *,
    tick_id: Optional[int] = None,
    aggregate: bool = True,
    conn: Optional[sqlite3.Connection] = None,
) -> None:
    own_conn = conn is None
    if own_conn:
        conn = ds_connect()
    try:
        _ensure_schema(conn)
        for message in messages:
            _write_run_event_conn(
                conn,
                strategy_id,
                mode,
                event_type,
                str(message),
                tick_id=tick_id,
                aggregate=aggregate,
            )
        conn.commit()
    finally:
        if own_conn:
            conn.close()


def write_action_event(
    strategy_id: int,
    mode: str,
    action_type: str,
    *,
    tick_id: Optional[int] = None,
    leg_index: int = 0,
    side: Optional[str] = None,
    qty: Optional[float] = None,
    price: Optional[float] = None,
    status: str = "",
    reason: Optional[str] = None,
    order_ref: Optional[str] = None,
    raw_action: Optional[Dict[str, Any]] = None,
    raw_action_json: Optional[str] = None,
    raw_function_json_hash: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> None:
    own_conn = conn is None
    if own_conn:
        conn = ds_connect()
    try:
        _ensure_schema(conn)
        write_action_event_conn(
            conn,
            strategy_id,
            mode,
            action_type,
            tick_id=tick_id,
            leg_index=leg_index,
            side=side,
            qty=qty,
            price=price,
            status=status,
            reason=reason,
            order_ref=order_ref,
            raw_action=raw_action,
            raw_action_json=raw_action_json,
            raw_function_json_hash=raw_function_json_hash,
        )
        conn.commit()
    finally:
        if own_conn:
            conn.close()


def write_action_event_conn(
    conn: sqlite3.Connection,
    strategy_id: int,
    mode: str,
    action_type: str,
    *,
    tick_id: Optional[int] = None,
    leg_index: int = 0,
    side: Optional[str] = None,
    qty: Optional[float] = None,
    price: Optional[float] = None,
    status: str = "",
    reason: Optional[str] = None,
    order_ref: Optional[str] = None,
    raw_action: Optional[Dict[str, Any]] = None,
    raw_action_json: Optional[str] = None,
    raw_function_json_hash: Optional[str] = None,
) -> None:
    _ensure_schema(conn)
    raw_json = raw_action_json
    if raw_json is None and raw_action is not None:
        raw_json = json.dumps(raw_action, ensure_ascii=False, sort_keys=True)
    conn.execute(
        """
        INSERT INTO strategy_action_events (
            tick_id, strategy_id, mode, action_type, leg_index, side, qty, price,
            status, reason, order_ref, raw_action_json, raw_function_json_hash, created_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tick_id,
            int(strategy_id),
            normalize_mode(mode),
            str(action_type or "").upper(),
            int(leg_index or 0),
            str(side).upper() if side is not None else None,
            qty,
            price,
            str(status or ""),
            reason,
            str(order_ref) if order_ref is not None else None,
            raw_json,
            raw_function_json_hash,
            now_iso(),
        ),
    )
