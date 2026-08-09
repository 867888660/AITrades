from __future__ import annotations

import base64
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from services.strategy_data_source import _db_path as _strategy_db_path


TRACE_STATUSES = {"queued", "running", "succeeded", "failed", "cancelled", "partial"}
EVENT_STATUSES = {"queued", "running", "succeeded", "failed", "cancelled", "skipped"}
EVENT_KINDS = {
    "agent_step",
    "skill_call",
    "tool_call",
    "state_change",
    "artifact",
    "validation",
    "system",
}
SEVERITIES = {"debug", "info", "warning", "error", "critical"}
EDGE_TYPES = {"parent", "dependency", "caused_by", "retry_of"}

_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "mnemonic",
    "passphrase",
    "password",
    "private_key",
    "secret",
    "seed_phrase",
    "signature",
    "token",
)


_DDL = """
CREATE TABLE IF NOT EXISTS inspection_traces (
    trace_id             TEXT PRIMARY KEY,
    subject_type         TEXT NOT NULL DEFAULT '',
    subject_id           TEXT NOT NULL DEFAULT '',
    project_id           TEXT NOT NULL DEFAULT '',
    session_id           TEXT NOT NULL DEFAULT '',
    legacy_run_id        TEXT NOT NULL DEFAULT '',
    title                TEXT NOT NULL DEFAULT '',
    summary              TEXT NOT NULL DEFAULT '',
    status               TEXT NOT NULL DEFAULT 'running',
    actor_type           TEXT NOT NULL DEFAULT '',
    actor_id             TEXT NOT NULL DEFAULT '',
    root_event_id        TEXT NOT NULL DEFAULT '',
    completeness         TEXT NOT NULL DEFAULT 'complete',
    dropped_event_count  INTEGER NOT NULL DEFAULT 0,
    runtime_version      TEXT NOT NULL DEFAULT '',
    metadata_json        TEXT NOT NULL DEFAULT '{}',
    visibility           TEXT NOT NULL DEFAULT 'visible',
    hidden_at_utc        TEXT,
    hidden_by            TEXT NOT NULL DEFAULT '',
    hidden_reason        TEXT NOT NULL DEFAULT '',
    started_at_utc       TEXT NOT NULL,
    finished_at_utc      TEXT,
    updated_at_utc       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_inspection_traces_started
ON inspection_traces(visibility, started_at_utc DESC, trace_id DESC);

CREATE INDEX IF NOT EXISTS idx_inspection_traces_subject
ON inspection_traces(subject_type, subject_id, started_at_utc DESC);

CREATE TABLE IF NOT EXISTS inspection_events (
    event_id          TEXT PRIMARY KEY,
    trace_id          TEXT NOT NULL,
    parent_event_id   TEXT NOT NULL DEFAULT '',
    sequence_no       INTEGER NOT NULL DEFAULT 0,
    event_kind        TEXT NOT NULL DEFAULT 'system',
    title             TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'succeeded',
    severity          TEXT NOT NULL DEFAULT 'info',
    actor_type        TEXT NOT NULL DEFAULT '',
    actor_id          TEXT NOT NULL DEFAULT '',
    operation         TEXT NOT NULL DEFAULT '',
    target_type       TEXT NOT NULL DEFAULT '',
    target_id         TEXT NOT NULL DEFAULT '',
    schema_version    TEXT NOT NULL DEFAULT 'inspection.event.v1',
    input_json        TEXT NOT NULL DEFAULT '{}',
    output_json       TEXT NOT NULL DEFAULT '{}',
    error_json        TEXT NOT NULL DEFAULT '{}',
    metadata_json     TEXT NOT NULL DEFAULT '{}',
    redaction_json    TEXT NOT NULL DEFAULT '{}',
    idempotency_key   TEXT NOT NULL DEFAULT '',
    started_at_utc    TEXT NOT NULL,
    finished_at_utc   TEXT,
    duration_ms       REAL,
    FOREIGN KEY(trace_id) REFERENCES inspection_traces(trace_id)
);

CREATE INDEX IF NOT EXISTS idx_inspection_events_trace
ON inspection_events(trace_id, sequence_no, started_at_utc, event_id);

CREATE INDEX IF NOT EXISTS idx_inspection_events_filter
ON inspection_events(trace_id, event_kind, status, severity);

CREATE UNIQUE INDEX IF NOT EXISTS idx_inspection_events_idempotency
ON inspection_events(trace_id, idempotency_key)
WHERE idempotency_key <> '';

CREATE TABLE IF NOT EXISTS inspection_event_edges (
    from_event_id  TEXT NOT NULL,
    to_event_id    TEXT NOT NULL,
    relation_type  TEXT NOT NULL,
    metadata_json  TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(from_event_id, to_event_id, relation_type)
);

CREATE INDEX IF NOT EXISTS idx_inspection_edges_to
ON inspection_event_edges(to_event_id, relation_type);

CREATE TABLE IF NOT EXISTS inspection_event_refs (
    event_id    TEXT NOT NULL,
    ref_type    TEXT NOT NULL,
    ref_id      TEXT NOT NULL,
    ref_role    TEXT NOT NULL DEFAULT 'related',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(event_id, ref_type, ref_id, ref_role)
);

CREATE INDEX IF NOT EXISTS idx_inspection_refs_target
ON inspection_event_refs(ref_type, ref_id, event_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_strategy_db_path()), timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _parse_json(value: Any, default: Any = None) -> Any:
    if default is None:
        default = {}
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or json.dumps(default, ensure_ascii=False))
    except Exception:
        return default


def _json_text(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _legacy_trace_id(run_id: str) -> str:
    return f"trace_legacy_{str(run_id or '').strip()}"


def _legacy_event_id(step_id: str) -> str:
    return f"evt_legacy_{str(step_id or '').strip()}"


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def ensure_schema(conn: Optional[sqlite3.Connection] = None) -> None:
    owns_connection = conn is None
    connection = conn or _connect()
    try:
        connection.executescript(_DDL)
        _bridge_legacy_rows(connection)
        connection.commit()
    finally:
        if owns_connection:
            connection.close()


def _bridge_legacy_rows(conn: sqlite3.Connection) -> None:
    """Expose existing Agent run/step history without duplicating raw payloads."""
    if not _table_exists(conn, "agent_runs"):
        return
    now = _now()
    conn.execute(
        """INSERT OR IGNORE INTO inspection_traces(
               trace_id, subject_type, subject_id, legacy_run_id, title, summary,
               status, actor_type, actor_id, metadata_json, started_at_utc,
               finished_at_utc, updated_at_utc
           )
           SELECT 'trace_legacy_' || run_id, ref_type, ref_id, run_id, summary, summary,
                  CASE status WHEN 'completed' THEN 'succeeded' WHEN 'error' THEN 'failed' ELSE status END,
                  actor_type, actor_id, '{"source":"agent_runs"}', started_at_utc,
                  finished_at_utc, COALESCE(finished_at_utc, started_at_utc)
             FROM agent_runs
            WHERE run_id <> ''"""
    )
    if not _table_exists(conn, "agent_run_steps"):
        return
    conn.execute(
        """INSERT OR IGNORE INTO inspection_events(
               event_id, trace_id, parent_event_id, sequence_no, event_kind, title,
               status, severity, operation, target_type, target_id, metadata_json,
               idempotency_key, started_at_utc, finished_at_utc, duration_ms
           )
           SELECT 'evt_legacy_' || step_id, 'trace_legacy_' || run_id,
                  CASE WHEN parent_step_id <> '' THEN 'evt_legacy_' || parent_step_id ELSE '' END,
                  step_index,
                  CASE step_type WHEN 'api_call' THEN 'tool_call' ELSE 'agent_step' END,
                  name,
                  CASE status WHEN 'completed' THEN 'succeeded' WHEN 'error' THEN 'failed' ELSE status END,
                  CASE WHEN status IN ('failed', 'error') THEN 'error' ELSE 'info' END,
                  capability, target_type, target_id, '{"source":"agent_run_steps"}',
                  'legacy-step:' || step_id, started_at_utc, finished_at_utc, duration_ms
             FROM agent_run_steps
            WHERE run_id <> '' AND step_id <> ''"""
    )
    conn.execute(
        """INSERT OR IGNORE INTO inspection_event_edges(from_event_id, to_event_id, relation_type)
           SELECT 'evt_legacy_' || parent_step_id, 'evt_legacy_' || step_id, 'parent'
             FROM agent_run_steps
            WHERE parent_step_id <> '' AND step_id <> ''"""
    )
    conn.execute(
        """INSERT OR IGNORE INTO inspection_event_refs(event_id, ref_type, ref_id, ref_role)
           SELECT 'evt_legacy_' || step_id, target_type, target_id, 'target'
             FROM agent_run_steps
            WHERE step_id <> '' AND target_type <> '' AND target_id <> ''"""
    )
    conn.execute(
        """UPDATE inspection_traces
              SET root_event_id = COALESCE(NULLIF(root_event_id, ''), (
                    SELECT event_id FROM inspection_events e
                     WHERE e.trace_id = inspection_traces.trace_id
                     ORDER BY sequence_no, started_at_utc, event_id LIMIT 1
                  )),
                  updated_at_utc = COALESCE(NULLIF(updated_at_utc, ''), ?)
            WHERE legacy_run_id <> ''""",
        (now,),
    )


def _redact_value(value: Any, *, depth: int = 0, state: Optional[Dict[str, int]] = None) -> Any:
    state = state if state is not None else {"redacted_fields": 0, "truncated_fields": 0}
    if depth >= 6:
        state["truncated_fields"] += 1
        return "[TRUNCATED_DEPTH]"
    if isinstance(value, dict):
        result: Dict[str, Any] = {}
        items = list(value.items())
        for key, item in items[:100]:
            key_text = str(key)
            lowered = key_text.lower().replace("-", "_")
            if any(part in lowered for part in _SENSITIVE_KEY_PARTS):
                result[key_text] = "[REDACTED]"
                state["redacted_fields"] += 1
            else:
                result[key_text] = _redact_value(item, depth=depth + 1, state=state)
        if len(items) > 100:
            result["_truncated_keys"] = len(items) - 100
            state["truncated_fields"] += len(items) - 100
        return result
    if isinstance(value, (list, tuple)):
        result = [_redact_value(item, depth=depth + 1, state=state) for item in list(value)[:50]]
        if len(value) > 50:
            result.append({"_truncated_items": len(value) - 50})
            state["truncated_fields"] += len(value) - 50
        return result
    if isinstance(value, bytes):
        state["truncated_fields"] += 1
        return f"[BINARY {len(value)} bytes]"
    if isinstance(value, str) and len(value) > 4000:
        state["truncated_fields"] += 1
        return value[:3997] + "..."
    return value


def _redacted_json(value: Any) -> tuple[str, Dict[str, int]]:
    state = {"redacted_fields": 0, "truncated_fields": 0}
    return _json_text(_redact_value(value if value is not None else {}, state=state)), state


def _normalized_status(value: str, allowed: set[str], default: str) -> str:
    status = str(value or "").strip().lower()
    aliases = {"completed": "succeeded", "success": "succeeded", "error": "failed"}
    status = aliases.get(status, status)
    return status if status in allowed else default


def emit_event(
    *,
    trace_id: str = "",
    subject_type: str = "",
    subject_id: str = "",
    project_id: str = "",
    session_id: str = "",
    trace_title: str = "",
    trace_summary: str = "",
    trace_status: str = "",
    trace_metadata: Any = None,
    event_id: str = "",
    parent_event_id: str = "",
    dependency_event_ids: Optional[Iterable[str]] = None,
    caused_by_event_ids: Optional[Iterable[str]] = None,
    retry_of_event_id: str = "",
    event_kind: str = "system",
    title: str = "",
    status: str = "succeeded",
    severity: str = "info",
    actor_type: str = "",
    actor_id: str = "",
    operation: str = "",
    target_type: str = "",
    target_id: str = "",
    input_data: Any = None,
    output_data: Any = None,
    error_data: Any = None,
    metadata: Any = None,
    refs: Optional[Iterable[Dict[str, Any]]] = None,
    idempotency_key: str = "",
    started_at: str = "",
    finished_at: Optional[str] = None,
    duration_ms: Optional[float] = None,
    sequence_no: Optional[int] = None,
    legacy_run_id: str = "",
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    owns_connection = conn is None
    connection = conn or _connect()
    if owns_connection:
        ensure_schema(connection)
    now = _now()
    trace_id = str(trace_id or _new_id("trace")).strip()
    event_id = str(event_id or _new_id("evt")).strip()
    event_status = _normalized_status(status, EVENT_STATUSES, "succeeded")
    event_kind = str(event_kind or "system").strip().lower()
    event_kind = event_kind if event_kind in EVENT_KINDS else "system"
    severity = str(severity or "info").strip().lower()
    severity = severity if severity in SEVERITIES else "info"
    resolved_trace_status = _normalized_status(
        trace_status or (
            "failed" if event_status == "failed"
            else "running" if event_status in {"queued", "running"}
            else "cancelled" if event_status == "cancelled"
            else "succeeded"
        ),
        TRACE_STATUSES,
        "running",
    )
    started_at = str(started_at or now)
    input_json, input_redaction = _redacted_json(input_data)
    output_json, output_redaction = _redacted_json(output_data)
    error_json, error_redaction = _redacted_json(error_data)
    metadata_json, metadata_redaction = _redacted_json(metadata)
    trace_metadata_json, _ = _redacted_json(trace_metadata)
    redaction = {
        "policy_version": "inspection.redaction.v1",
        "redacted_fields": sum(item["redacted_fields"] for item in (input_redaction, output_redaction, error_redaction, metadata_redaction)),
        "truncated_fields": sum(item["truncated_fields"] for item in (input_redaction, output_redaction, error_redaction, metadata_redaction)),
    }
    try:
        connection.execute(
            """INSERT OR IGNORE INTO inspection_traces(
                   trace_id, subject_type, subject_id, project_id, session_id,
                   legacy_run_id, title, summary, status, actor_type, actor_id,
                   metadata_json, started_at_utc, finished_at_utc, updated_at_utc
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                trace_id, subject_type, subject_id, project_id, session_id,
                legacy_run_id, trace_title or title, trace_summary, resolved_trace_status,
                actor_type, actor_id, trace_metadata_json, started_at,
                finished_at if resolved_trace_status in {"succeeded", "failed", "cancelled"} else None,
                now,
            ),
        )
        connection.execute(
            """UPDATE inspection_traces
                  SET subject_type = CASE WHEN ? <> '' THEN ? ELSE subject_type END,
                      subject_id = CASE WHEN ? <> '' THEN ? ELSE subject_id END,
                      project_id = CASE WHEN ? <> '' THEN ? ELSE project_id END,
                      session_id = CASE WHEN ? <> '' THEN ? ELSE session_id END,
                      legacy_run_id = CASE WHEN ? <> '' THEN ? ELSE legacy_run_id END,
                      title = CASE WHEN ? <> '' THEN ? ELSE title END,
                      summary = CASE WHEN ? <> '' THEN ? ELSE summary END,
                      status = CASE WHEN status IN ('failed','partial') THEN status ELSE ? END,
                      actor_type = CASE WHEN ? <> '' THEN ? ELSE actor_type END,
                      actor_id = CASE WHEN ? <> '' THEN ? ELSE actor_id END,
                      finished_at_utc = CASE WHEN ? IN ('succeeded','failed','cancelled')
                                             THEN COALESCE(?, finished_at_utc, ?) ELSE finished_at_utc END,
                      updated_at_utc = ?
                WHERE trace_id = ?""",
            (
                subject_type, subject_type, subject_id, subject_id,
                project_id, project_id, session_id, session_id,
                legacy_run_id, legacy_run_id, trace_title, trace_title,
                trace_summary, trace_summary, resolved_trace_status,
                actor_type, actor_type, actor_id, actor_id,
                resolved_trace_status, finished_at, now, now, trace_id,
            ),
        )
        existing_sequence: Optional[int] = None
        if idempotency_key:
            existing = connection.execute(
                "SELECT event_id, sequence_no FROM inspection_events WHERE trace_id = ? AND idempotency_key = ?",
                (trace_id, idempotency_key),
            ).fetchone()
            if existing:
                event_id = str(existing["event_id"])
                existing_sequence = int(existing["sequence_no"])
        if sequence_no is None:
            if existing_sequence is not None:
                sequence_no = existing_sequence
            else:
                row = connection.execute(
                    "SELECT COALESCE(MAX(sequence_no), 0) + 1 AS next_no FROM inspection_events WHERE trace_id = ?",
                    (trace_id,),
                ).fetchone()
                sequence_no = int(row["next_no"] or 1)
        connection.execute(
            """INSERT INTO inspection_events(
                   event_id, trace_id, parent_event_id, sequence_no, event_kind,
                   title, status, severity, actor_type, actor_id, operation,
                   target_type, target_id, input_json, output_json, error_json,
                   metadata_json, redaction_json, idempotency_key, started_at_utc,
                   finished_at_utc, duration_ms
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(event_id) DO UPDATE SET
                   parent_event_id=excluded.parent_event_id,
                   event_kind=excluded.event_kind, title=excluded.title,
                   status=excluded.status, severity=excluded.severity,
                   actor_type=excluded.actor_type, actor_id=excluded.actor_id,
                   operation=excluded.operation, target_type=excluded.target_type,
                   target_id=excluded.target_id, input_json=excluded.input_json,
                   output_json=excluded.output_json, error_json=excluded.error_json,
                   metadata_json=excluded.metadata_json, redaction_json=excluded.redaction_json,
                   finished_at_utc=excluded.finished_at_utc, duration_ms=excluded.duration_ms""",
            (
                event_id, trace_id, parent_event_id, int(sequence_no), event_kind,
                title or operation or event_kind, event_status, severity, actor_type,
                actor_id, operation, target_type, target_id, input_json, output_json,
                error_json, metadata_json, _json_text(redaction), idempotency_key,
                started_at, finished_at or (now if event_status not in {"queued", "running"} else None),
                duration_ms,
            ),
        )
        edge_specs: List[tuple[str, str]] = []
        if parent_event_id:
            edge_specs.append((parent_event_id, "parent"))
        edge_specs.extend((str(item), "dependency") for item in (dependency_event_ids or []) if str(item or "").strip())
        edge_specs.extend((str(item), "caused_by") for item in (caused_by_event_ids or []) if str(item or "").strip())
        if retry_of_event_id:
            edge_specs.append((str(retry_of_event_id), "retry_of"))
        for source_id, relation_type in edge_specs:
            connection.execute(
                "INSERT OR IGNORE INTO inspection_event_edges(from_event_id, to_event_id, relation_type) VALUES (?,?,?)",
                (source_id, event_id, relation_type),
            )
        normalized_refs = list(refs or [])
        if target_type and target_id:
            normalized_refs.append({"ref_type": target_type, "ref_id": target_id, "ref_role": "target"})
        for ref in normalized_refs:
            if not isinstance(ref, dict):
                continue
            ref_type = str(ref.get("ref_type") or "").strip()
            ref_id = str(ref.get("ref_id") or "").strip()
            if not ref_type or not ref_id:
                continue
            ref_metadata_json, _ = _redacted_json(ref.get("metadata") or {})
            connection.execute(
                """INSERT OR IGNORE INTO inspection_event_refs(
                       event_id, ref_type, ref_id, ref_role, metadata_json
                   ) VALUES (?,?,?,?,?)""",
                (event_id, ref_type, ref_id, str(ref.get("ref_role") or "related"), ref_metadata_json),
            )
        connection.execute(
            """UPDATE inspection_traces
                  SET root_event_id = CASE WHEN root_event_id = '' THEN ? ELSE root_event_id END,
                      updated_at_utc = ?
                WHERE trace_id = ?""",
            (event_id, now, trace_id),
        )
        if owns_connection:
            connection.commit()
        return {"trace_id": trace_id, "event_id": event_id, "sequence_no": int(sequence_no), "redaction": redaction}
    except Exception:
        if owns_connection:
            connection.rollback()
        raise
    finally:
        if owns_connection:
            connection.close()


def emit_event_safely(**kwargs: Any) -> Optional[Dict[str, Any]]:
    """Best-effort runtime instrumentation that never breaks the business Run.

    When the primary write fails, a second bounded attempt marks the Trace as
    partial and increments its dropped-event count. If storage itself is
    unavailable, the original workflow still continues.
    """
    try:
        return emit_event(**kwargs)
    except Exception:
        trace_id = str(kwargs.get("trace_id") or "").strip()
        if not trace_id:
            return None
        conn: Optional[sqlite3.Connection] = None
        try:
            conn = _connect()
            ensure_schema(conn)
            now = _now()
            conn.execute(
                """INSERT OR IGNORE INTO inspection_traces(
                       trace_id, subject_type, subject_id, project_id, session_id,
                       title, status, completeness, dropped_event_count,
                       metadata_json, started_at_utc, updated_at_utc
                   ) VALUES (?,?,?,?,?,?,'partial','partial',0,'{}',?,?)""",
                (
                    trace_id,
                    str(kwargs.get("subject_type") or ""),
                    str(kwargs.get("subject_id") or ""),
                    str(kwargs.get("project_id") or ""),
                    str(kwargs.get("session_id") or ""),
                    str(kwargs.get("trace_title") or "Inspection Trace"),
                    now,
                    now,
                ),
            )
            conn.execute(
                """UPDATE inspection_traces
                      SET status='partial', completeness='partial',
                          dropped_event_count=dropped_event_count+1, updated_at_utc=?
                    WHERE trace_id=?""",
                (now, trace_id),
            )
            conn.commit()
        except Exception:
            if conn is not None:
                conn.rollback()
        finally:
            if conn is not None:
                conn.close()
        return None


def bridge_audit_event(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    step_id: str,
    parent_step_id: str = "",
    actor_type: str = "",
    actor_id: str = "",
    capability: str = "",
    target_type: str = "",
    target_id: str = "",
    input_data: Any = None,
    output_data: Any = None,
    error_data: Any = None,
    policy_decision: str = "allow",
    risk_decision: str = "not_required",
    endpoint: str = "",
    method: str = "",
    status_code: Optional[int] = None,
    duration_ms: Optional[float] = None,
    created_at: str = "",
) -> Dict[str, Any]:
    failed = bool(error_data) or (status_code is not None and int(status_code) >= 400)
    blocked = str(policy_decision).lower() == "deny" or str(risk_decision).lower() == "blocked"
    severity = "error" if failed or blocked else "info"
    trace_id = _legacy_trace_id(run_id)
    return emit_event(
        conn=conn,
        trace_id=trace_id,
        legacy_run_id=run_id,
        subject_type=target_type,
        subject_id=target_id,
        trace_title=capability,
        trace_status="failed" if failed else "succeeded",
        event_id=_legacy_event_id(step_id),
        parent_event_id=_legacy_event_id(parent_step_id) if parent_step_id else "",
        event_kind="tool_call",
        title=capability or f"{method} {endpoint}".strip(),
        status="failed" if failed else "succeeded",
        severity=severity,
        actor_type=actor_type,
        actor_id=actor_id,
        operation=capability,
        target_type=target_type,
        target_id=target_id,
        input_data=input_data,
        output_data=output_data,
        error_data=error_data,
        metadata={
            "source": "agent_audit_events",
            "endpoint": endpoint,
            "method": method,
            "status_code": status_code,
            "policy_decision": policy_decision,
            "risk_decision": risk_decision,
        },
        idempotency_key=f"legacy-step:{step_id}",
        started_at=created_at or _now(),
        finished_at=created_at or _now(),
        duration_ms=duration_ms,
    )


def _decode_trace_cursor(cursor: str) -> tuple[str, str]:
    if not cursor:
        return "", ""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii") + b"===").decode("utf-8")
        value = json.loads(raw)
        return str(value.get("started_at") or ""), str(value.get("trace_id") or "")
    except Exception as exc:
        raise ValueError("invalid trace cursor") from exc


def _encode_trace_cursor(started_at: str, trace_id: str) -> str:
    raw = json.dumps({"started_at": started_at, "trace_id": trace_id}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _event_header(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    item["started_at"] = item.pop("started_at_utc", None)
    item["finished_at"] = item.pop("finished_at_utc", None)
    return item


def _trace_item(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    item["metadata"] = _parse_json(item.pop("metadata_json", "{}"), {})
    item["started_at"] = item.pop("started_at_utc", None)
    item["finished_at"] = item.pop("finished_at_utc", None)
    item["updated_at"] = item.pop("updated_at_utc", None)
    item["hidden_at"] = item.pop("hidden_at_utc", None)
    return item


def list_traces(
    *,
    limit: int = 50,
    cursor: str = "",
    subject_type: str = "",
    subject_id: str = "",
    status: str = "",
    q: str = "",
    include_hidden: bool = False,
) -> Dict[str, Any]:
    conn = _connect()
    try:
        ensure_schema(conn)
        limit = max(1, min(int(limit), 200))
        where = [] if include_hidden else ["t.visibility = 'visible'"]
        args: List[Any] = []
        for column, value in (("t.subject_type", subject_type), ("t.subject_id", subject_id), ("t.status", status)):
            if str(value or "").strip():
                where.append(f"{column} = ?")
                args.append(str(value).strip())
        if q:
            pattern = f"%{str(q).strip()}%"
            where.append("(t.title LIKE ? OR t.summary LIKE ? OR t.subject_id LIKE ? OR t.trace_id LIKE ?)")
            args.extend([pattern] * 4)
        cursor_started, cursor_id = _decode_trace_cursor(cursor)
        if cursor_started and cursor_id:
            where.append("(t.started_at_utc < ? OR (t.started_at_utc = ? AND t.trace_id < ?))")
            args.extend([cursor_started, cursor_started, cursor_id])
        where_sql = "WHERE " + " AND ".join(where) if where else ""
        args.append(limit + 1)
        rows = conn.execute(
            f"""SELECT t.*,
                       (SELECT COUNT(*) FROM inspection_events e WHERE e.trace_id = t.trace_id) AS event_count,
                       (SELECT COUNT(*) FROM inspection_events e WHERE e.trace_id = t.trace_id AND e.severity = 'warning') AS warning_count,
                       (SELECT COUNT(*) FROM inspection_events e WHERE e.trace_id = t.trace_id AND e.severity IN ('error','critical')) AS error_count
                  FROM inspection_traces t
                  {where_sql}
                 ORDER BY t.started_at_utc DESC, t.trace_id DESC
                 LIMIT ?""",
            args,
        ).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = [_trace_item(row) for row in rows]
        next_cursor = ""
        if has_more and items:
            next_cursor = _encode_trace_cursor(str(items[-1]["started_at"] or ""), str(items[-1]["trace_id"]))
        return {"items": items, "next_cursor": next_cursor, "has_more": has_more}
    finally:
        conn.close()


def get_trace(trace_id: str) -> Dict[str, Any]:
    conn = _connect()
    try:
        ensure_schema(conn)
        row = conn.execute(
            """SELECT t.*,
                      (SELECT COUNT(*) FROM inspection_events e WHERE e.trace_id = t.trace_id) AS event_count,
                      (SELECT COUNT(*) FROM inspection_events e WHERE e.trace_id = t.trace_id AND e.severity = 'warning') AS warning_count,
                      (SELECT COUNT(*) FROM inspection_events e WHERE e.trace_id = t.trace_id AND e.severity IN ('error','critical')) AS error_count,
                      (SELECT COUNT(*) FROM inspection_event_refs r JOIN inspection_events e ON e.event_id = r.event_id WHERE e.trace_id = t.trace_id) AS reference_count
                 FROM inspection_traces t WHERE t.trace_id = ?""",
            (str(trace_id),),
        ).fetchone()
        if not row:
            raise ValueError("inspection trace not found")
        return _trace_item(row)
    finally:
        conn.close()


def list_events(
    trace_id: str,
    *,
    limit: int = 100,
    cursor: int = 0,
    event_kind: str = "",
    status: str = "",
    severity: str = "",
    q: str = "",
) -> Dict[str, Any]:
    conn = _connect()
    try:
        ensure_schema(conn)
        limit = max(1, min(int(limit), 500))
        where = ["trace_id = ?", "sequence_no > ?"]
        args: List[Any] = [str(trace_id), max(0, int(cursor or 0))]
        for column, value in (("event_kind", event_kind), ("status", status), ("severity", severity)):
            if str(value or "").strip():
                where.append(f"{column} = ?")
                args.append(str(value).strip().lower())
        if q:
            pattern = f"%{str(q).strip()}%"
            where.append("(title LIKE ? OR operation LIKE ? OR target_id LIKE ?)")
            args.extend([pattern] * 3)
        args.append(limit + 1)
        rows = conn.execute(
            f"""SELECT event_id, trace_id, parent_event_id, sequence_no, event_kind,
                       title, status, severity, actor_type, actor_id, operation,
                       target_type, target_id, started_at_utc, finished_at_utc, duration_ms
                  FROM inspection_events
                 WHERE {' AND '.join(where)}
                 ORDER BY sequence_no, started_at_utc, event_id
                 LIMIT ?""",
            args,
        ).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = [_event_header(row) for row in rows]
        return {
            "trace_id": str(trace_id),
            "items": items,
            "next_cursor": int(items[-1]["sequence_no"]) if has_more and items else None,
            "has_more": has_more,
        }
    finally:
        conn.close()


def get_event(event_id: str) -> Dict[str, Any]:
    conn = _connect()
    try:
        ensure_schema(conn)
        row = conn.execute("SELECT * FROM inspection_events WHERE event_id = ?", (str(event_id),)).fetchone()
        if not row:
            raise ValueError("inspection event not found")
        item = _event_header(row)
        for key in ("input_json", "output_json", "error_json", "metadata_json", "redaction_json"):
            item[key[:-5] if key.endswith("_json") else key] = _parse_json(item.pop(key, "{}"), {})
        incoming = [
            dict(edge)
            for edge in conn.execute(
                "SELECT from_event_id, to_event_id, relation_type, metadata_json FROM inspection_event_edges WHERE to_event_id = ? ORDER BY relation_type, from_event_id",
                (str(event_id),),
            ).fetchall()
        ]
        outgoing = [
            dict(edge)
            for edge in conn.execute(
                "SELECT from_event_id, to_event_id, relation_type, metadata_json FROM inspection_event_edges WHERE from_event_id = ? ORDER BY relation_type, to_event_id",
                (str(event_id),),
            ).fetchall()
        ]
        for edge in incoming + outgoing:
            edge["metadata"] = _parse_json(edge.pop("metadata_json", "{}"), {})
        refs = []
        for ref in conn.execute(
            "SELECT ref_type, ref_id, ref_role, metadata_json FROM inspection_event_refs WHERE event_id = ? ORDER BY ref_role, ref_type, ref_id",
            (str(event_id),),
        ).fetchall():
            ref_item = dict(ref)
            ref_item["metadata"] = _parse_json(ref_item.pop("metadata_json", "{}"), {})
            refs.append(ref_item)
        item["relations"] = {"incoming": incoming, "outgoing": outgoing}
        item["references"] = refs
        return item
    finally:
        conn.close()


def search_events(trace_id: str, query: str, *, limit: int = 50) -> Dict[str, Any]:
    query = str(query or "").strip()
    if not query:
        raise ValueError("query is required")
    conn = _connect()
    try:
        ensure_schema(conn)
        pattern = f"%{query}%"
        rows = conn.execute(
            """SELECT event_id, trace_id, parent_event_id, sequence_no, event_kind,
                      title, status, severity, actor_type, actor_id, operation,
                      target_type, target_id, started_at_utc, finished_at_utc, duration_ms
                 FROM inspection_events
                WHERE trace_id = ? AND (
                      title LIKE ? OR operation LIKE ? OR target_id LIKE ? OR
                      error_json LIKE ? OR metadata_json LIKE ?
                )
                ORDER BY CASE WHEN title LIKE ? THEN 0 WHEN operation LIKE ? THEN 1 ELSE 2 END,
                         sequence_no, started_at_utc
                LIMIT ?""",
            (str(trace_id), pattern, pattern, pattern, pattern, pattern, pattern, pattern, max(1, min(int(limit), 100))),
        ).fetchall()
        return {"trace_id": str(trace_id), "query": query, "items": [_event_header(row) for row in rows]}
    finally:
        conn.close()
