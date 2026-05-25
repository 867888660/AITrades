from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from services.config_loader import BASE_DIR, get_market_realtime_db_path, load_web_settings
from services.polymarket_service import fetch_strategy_detail


EVENT_TYPE_GUARANTEE_LIMIT = 20
EVENT_TYPE_GUARANTEE_KEYS = ("print", "action", "trade", "error", "settings")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        return datetime.min.replace(tzinfo=timezone.utc)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_json(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _event_type_key(event: Dict[str, Any]) -> str:
    text = str(event.get("event_type") or event.get("type") or "").strip().lower()
    if "trade" in text or "fill" in text or "order" in text:
        return "trade"
    if "action" in text:
        return "action"
    if "error" in text or "fail" in text or "block" in text:
        return "error"
    if "settings" in text:
        return "settings"
    if "print" in text:
        return "print"
    return text or "event"


def _event_identity(event: Dict[str, Any]) -> str:
    event_id = str(event.get("id") or "").strip()
    if event_id:
        return event_id
    return "|".join(
        [
            str(event.get("ts") or ""),
            str(event.get("event_type") or event.get("type") or ""),
            str(event.get("summary") or event.get("label") or ""),
            str(event.get("source") or ""),
        ]
    )


def _sort_and_dedupe_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    output: List[Dict[str, Any]] = []
    for event in sorted(events, key=lambda item: _parse_iso(item.get("ts")), reverse=True):
        identity = _event_identity(event)
        if identity in seen:
            continue
        seen.add(identity)
        output.append(event)
    return output


def _limit_with_type_guarantee(
    events: List[Dict[str, Any]],
    limit: int,
    *,
    per_type_limit: int = EVENT_TYPE_GUARANTEE_LIMIT,
) -> List[Dict[str, Any]]:
    sorted_events = _sort_and_dedupe_events(events)
    base = sorted_events[: max(1, limit)]
    selected_ids = {_event_identity(event) for event in base}
    guaranteed: List[Dict[str, Any]] = []
    for event_type in EVENT_TYPE_GUARANTEE_KEYS:
        count = 0
        for event in sorted_events:
            if _event_type_key(event) != event_type:
                continue
            identity = _event_identity(event)
            if identity in selected_ids:
                count += 1
                if count >= per_type_limit:
                    break
                continue
            guaranteed.append(event)
            selected_ids.add(identity)
            count += 1
            if count >= per_type_limit:
                break
    return _sort_and_dedupe_events([*base, *guaranteed])


def _event_db_path() -> Path:
    return BASE_DIR / "strategy_workspace_events.db"


def _connect_event_db() -> sqlite3.Connection:
    path = _event_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS strategy_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_row_id INTEGER NOT NULL,
            ts_utc TEXT NOT NULL,
            event_type TEXT NOT NULL,
            event_subtype TEXT,
            severity TEXT,
            summary TEXT,
            payload_json TEXT,
            source TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_strategy_events_row_ts
        ON strategy_events(strategy_row_id, ts_utc DESC)
        """
    )
    conn.commit()
    return conn


def record_strategy_event(
    strategy_row_id: int,
    event_type: str,
    summary: str,
    payload: dict | None = None,
    severity: str = "info",
    source: str = "workspace",
    event_subtype: str | None = None,
) -> None:
    conn = _connect_event_db()
    try:
        conn.execute(
            """
            INSERT INTO strategy_events (
                strategy_row_id, ts_utc, event_type, event_subtype, severity, summary, payload_json, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(strategy_row_id),
                _now_iso(),
                str(event_type or "").strip() or "unknown",
                str(event_subtype or "").strip() or None,
                str(severity or "info"),
                str(summary or "").strip(),
                json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
                str(source or "workspace"),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _workspace_events(row_id: int, limit: int) -> List[Dict[str, Any]]:
    conn = _connect_event_db()
    try:
        rows = conn.execute(
            """
            SELECT id, strategy_row_id, ts_utc, event_type, event_subtype, severity, summary, payload_json, source
            FROM strategy_events
            WHERE strategy_row_id = ?
            ORDER BY ts_utc DESC
            LIMIT ?
            """,
            (int(row_id), max(1, limit)),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": f"workspace-{row['id']}",
            "strategy_row_id": row["strategy_row_id"],
            "ts": row["ts_utc"],
            "event_type": row["event_type"],
            "event_subtype": row["event_subtype"],
            "severity": row["severity"] or "info",
            "summary": row["summary"] or row["event_type"],
            "payload": _parse_json(row["payload_json"]),
            "source": row["source"] or "workspace",
        }
        for row in rows
    ]


def _format_audit_action_summary(row: sqlite3.Row) -> tuple[str, Dict[str, Any]]:
    raw_action = _parse_json(row["raw_action_json"])
    raw_type = str(raw_action.get("type") or row["action_type"] or "ACTION").upper()
    executed_type = str(row["action_type"] or "").upper()
    side = str(raw_action.get("side") or row["side"] or "").capitalize()
    status = str(row["status"] or "").strip().lower()
    reason = str(row["reason"] or "").strip()
    qty = row["qty"]
    price = row["price"]

    parts = [raw_type]
    if side:
        parts.append(side)
    if raw_type == "SETPOS":
        pct = raw_action.get("target_pct", raw_action.get("pct"))
        try:
            if pct is not None:
                parts.append(f"target={float(pct) * 100:.0f}%")
        except (TypeError, ValueError):
            pass
        if executed_type and executed_type != raw_type:
            parts.append(f"-> {executed_type}")
    elif executed_type and executed_type != raw_type:
        parts.append(f"as {executed_type}")

    try:
        if qty is not None:
            parts.append(f"qty={float(qty):.2f}")
    except (TypeError, ValueError):
        pass
    try:
        if price is not None:
            parts.append(f"@{float(price):.4f}")
    except (TypeError, ValueError):
        pass
    if status and status != "filled":
        parts.append(f"[{status}]")
    if reason:
        parts.append(reason)

    payload = {
        "leg": int(row["leg_index"] or 0),
        "side": side,
        "action_type": executed_type,
        "raw_action_type": raw_type,
        "qty": float(qty) if qty is not None else None,
        "price": float(price) if price is not None else None,
        "status": status,
        "reason": reason or None,
        "order_ref": row["order_ref"],
        "raw_action": raw_action,
    }
    return " ".join(parts), payload


def _audit_action_events(row_id: int, limit: int, event_type: str = "") -> List[Dict[str, Any]]:
    if event_type and event_type != "action":
        return []
    try:
        from services import strategy_data_source

        conn = strategy_data_source.connect(readonly=True)
    except Exception:
        return []
    try:
        row_limit = max(200, min(max(1, limit) * 20, 6000))
        rows = conn.execute(
            """SELECT id, tick_id, strategy_id, mode, action_type, leg_index, side, qty,
                      price, status, reason, order_ref, raw_action_json, created_at_utc
               FROM strategy_action_events
               WHERE strategy_id = ?
               ORDER BY created_at_utc DESC, id DESC
               LIMIT ?""",
            (int(row_id), row_limit),
        ).fetchall()
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass

    output: List[Dict[str, Any]] = []
    for row in rows:
        summary, payload = _format_audit_action_summary(row)
        status = str(row["status"] or "").lower()
        output.append(
            {
                "id": f"audit-action-{row['id']}",
                "strategy_row_id": row["strategy_id"],
                "ts": row["created_at_utc"],
                "event_type": "action",
                "event_subtype": status or None,
                "severity": "warning" if status in {"blocked", "failed"} else "info",
                "summary": summary,
                "payload": payload,
                "source": str(row["mode"] or "virtual").lower(),
            }
        )
    return output


def _virtual_strategy_events(row_id: int, limit: int, event_type: str = "") -> List[Dict[str, Any]]:
    try:
        from services import strategy_data_source

        conn = strategy_data_source.connect(readonly=True)
    except Exception:
        return []
    try:
        row_limit = max(200, min(max(1, limit) * 80, 6000))
        type_clause = "AND event_type = ?" if event_type else ""
        params: List[Any] = [int(row_id)]
        if event_type:
            params.append(event_type)
        params.append(row_limit)
        rows = conn.execute(
            f"""SELECT id, strategy_id, tick_id, mode, event_type, content, repeat_count, last_seen_utc, created_at_utc
                FROM strategy_virtual_events
                WHERE strategy_id = ? {type_clause}
                ORDER BY COALESCE(NULLIF(last_seen_utc, ''), created_at_utc) DESC, id DESC
                LIMIT ?""",
            params,
        ).fetchall()
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass

    print_batches: Dict[tuple[str, str], Dict[str, Any]] = {}
    non_print: Dict[tuple[str, str, str], Dict[str, Any]] = {}
    tick_groups: Dict[tuple[str, Any], List[Any]] = {}
    for row in rows:
        etype = str(row["event_type"] or "event").lower()
        mode = str(row["mode"] or "virtual")
        if etype == "action":
            continue
        if etype == "print":
            tick_groups.setdefault((mode, row["tick_id"] or row["id"]), []).append(row)
            continue
        content = str(row["content"] or "").strip() or etype
        key = (mode, etype, content)
        ts = row["last_seen_utc"] or row["created_at_utc"]
        current = non_print.get(key)
        if current is None or _parse_iso(ts) > _parse_iso(current["ts"]):
            non_print[key] = {
                "id": f"virtual-{row['id']}",
                "strategy_row_id": row["strategy_id"],
                "ts": ts,
                "event_type": row["event_type"],
                "event_subtype": None,
                "severity": "error" if etype == "error" else "info",
                "summary": content,
                "payload": {"repeat_count": int(row["repeat_count"] or 1), "duplicate_count": 1},
                "repeat_count": int(row["repeat_count"] or 1),
                "duplicate_count": 1,
                "source": mode,
            }
        else:
            current["repeat_count"] = int(current.get("repeat_count") or 1) + int(row["repeat_count"] or 1)
            current["duplicate_count"] = int(current.get("duplicate_count") or 1) + 1

    for (mode, tick_key), group_rows in tick_groups.items():
        ordered = sorted(group_rows, key=lambda r: int(r["id"] or 0))
        lines = [str(r["content"] or "").strip() for r in ordered if str(r["content"] or "").strip()]
        if not lines:
            continue
        signature = "\n".join(lines)
        latest_ts = max((str(r["last_seen_utc"] or r["created_at_utc"]) for r in ordered), key=lambda value: _parse_iso(value))
        repeat_sum = sum(int(r["repeat_count"] or 1) for r in ordered)
        key = (mode, signature)
        current = print_batches.get(key)
        if current is None:
            print_batches[key] = {
                "id": f"virtual-print-batch-{tick_key}",
                "strategy_row_id": row_id,
                "ts": latest_ts,
                "event_type": "print",
                "event_subtype": "batch",
                "severity": "info",
                "summary": signature,
                "payload": {"repeat_count": 1, "duplicate_count": 1, "line_count": len(lines)},
                "repeat_count": 1,
                "duplicate_count": 1,
                "source": mode,
            }
        else:
            if _parse_iso(latest_ts) > _parse_iso(current["ts"]):
                current["ts"] = latest_ts
            current["repeat_count"] = int(current.get("repeat_count") or 1) + 1
            current["duplicate_count"] = int(current.get("duplicate_count") or 1) + 1
            current["payload"]["repeat_count"] = current["repeat_count"]
            current["payload"]["duplicate_count"] = current["duplicate_count"]
            current["payload"]["raw_line_repeats"] = int(current["payload"].get("raw_line_repeats") or 0) + repeat_sum

    print_outputs = list(print_batches.values())
    has_full_print_batch = any(int(item.get("payload", {}).get("line_count") or 0) >= 3 for item in print_outputs)
    if has_full_print_batch:
        print_outputs = [
            item for item in print_outputs
            if int(item.get("payload", {}).get("line_count") or 0) >= 3
            or int(item.get("repeat_count") or 1) > 1
        ]
    action_events = _audit_action_events(row_id, limit, event_type=event_type)

    # Append filled orders from strategy_virtual_orders as "trade" events.
    # Blocked/failed attempts belong to Actions, because they are execution outcomes
    # of a strategy instruction rather than actual trades.
    trade_events: List[Dict[str, Any]] = []
    if not event_type or event_type == "trade":
        try:
            conn2 = strategy_data_source.connect(readonly=True)
            order_rows = conn2.execute(
                """SELECT id, leg_index, side, action, qty, price, gross_notional, fee,
                          net_cash_change, liquidity_role, status, reason, created_at_utc
                   FROM strategy_virtual_orders
                   WHERE strategy_id = ? AND status = 'filled'
                   ORDER BY id DESC LIMIT ?""",
                (int(row_id), max(1, limit)),
            ).fetchall()
            conn2.close()
            for o in order_rows:
                status = str(o["status"] or "").lower()
                side = str(o["side"] or "").capitalize()
                action = str(o["action"] or "BUY").upper()
                qty = float(o["qty"] or 0)
                price = float(o["price"] or 0)
                parts = [action, side, f"qty={qty:.2f}", f"@{price:.4f}"]
                if o["liquidity_role"]:
                    parts.append(str(o["liquidity_role"]))
                if status != "filled":
                    parts.append(f"[{status}]")
                if o["reason"]:
                    parts.append(str(o["reason"]))
                trade_events.append({
                    "id": f"order-{o['id']}",
                    "strategy_row_id": row_id,
                    "ts": o["created_at_utc"],
                    "event_type": "trade",
                    "event_subtype": status,
                    "severity": "info" if status == "filled" else "warning",
                    "summary": " ".join(parts),
                    "payload": {
                        "leg": int(o["leg_index"] or 0),
                        "side": side,
                        "action": action,
                        "qty": qty,
                        "price": price,
                        "gross_notional": float(o["gross_notional"] or 0),
                        "fee": float(o["fee"] or 0),
                        "net_cash_change": float(o["net_cash_change"] or 0),
                        "liquidity_role": o["liquidity_role"],
                        "status": status,
                        "reason": o["reason"],
                    },
                    "source": "virtual",
                })
        except Exception:
            pass
        try:
            conn3 = strategy_data_source.connect(readonly=True)
            order_rows_v2 = conn3.execute(
                """SELECT id, instrument_id, asset_class, action, side, qty, notional,
                          price, fee, status, reason, created_at_utc
                   FROM strategy_virtual_orders_v2
                   WHERE strategy_id = ? AND status = 'filled'
                   ORDER BY id DESC LIMIT ?""",
                (int(row_id), max(1, limit)),
            ).fetchall()
            conn3.close()
            for o in order_rows_v2:
                status = str(o["status"] or "").lower()
                action = str(o["action"] or "ORDER").upper()
                qty = float(o["qty"] or 0)
                price = float(o["price"] or 0)
                instrument_id = str(o["instrument_id"] or "")
                parts = [action, instrument_id, f"qty={qty:.4f}", f"@{price:.4f}"]
                trade_events.append({
                    "id": f"order-v2-{o['id']}",
                    "strategy_row_id": row_id,
                    "ts": o["created_at_utc"],
                    "event_type": "trade",
                    "event_subtype": status,
                    "severity": "info" if status == "filled" else "warning",
                    "summary": " ".join(parts),
                    "payload": {
                        "instrument_id": instrument_id,
                        "asset_class": o["asset_class"],
                        "side": o["side"],
                        "action": action,
                        "qty": qty,
                        "price": price,
                        "notional": float(o["notional"] or 0),
                        "fee": float(o["fee"] or 0),
                        "status": status,
                        "reason": o["reason"],
                    },
                    "source": "virtual",
                })
        except Exception:
            pass

    output = [*print_outputs, *non_print.values(), *action_events, *trade_events]
    if event_type:
        return _sort_and_dedupe_events(output)[: max(1, limit)]
    return _limit_with_type_guarantee(output, limit)


def _monitoring_db_path() -> Path:
    settings = load_web_settings()
    text = get_market_realtime_db_path(settings)
    return Path(text).expanduser() if text else (BASE_DIR / "market_data.db")


def _monitoring_events(detail: Dict[str, Any], limit: int, args: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
    db_path = _monitoring_db_path()
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        tables = {str(row[0]) for row in conn.execute("select name from sqlite_master where type='table'").fetchall()}
        if "market_deltas" not in tables:
            return []

        args = args or {}
        tokens = [str(detail.get("yes_token") or "").strip(), str(detail.get("no_token") or "").strip()]
        tokens = [token for token in tokens if token]
        clauses: list[str] = []
        params: list[Any] = []
        condition_id = str(detail.get("condition_id") or "").strip()
        if condition_id:
            clauses.append("condition_id = ?")
            params.append(condition_id)
        if tokens:
            placeholders = ", ".join(["?"] * len(tokens))
            clauses.append(f"clobTokenId IN ({placeholders})")
            params.extend(tokens)
        if not clauses:
            return []

        where_parts = [f"({' OR '.join(clauses)})"]
        from_ts = str((args or {}).get("from") or "").strip()
        to_ts = str((args or {}).get("to") or "").strip()
        if from_ts:
            where_parts.append("timestamp >= ?")
            params.append(from_ts)
        if to_ts:
            where_parts.append("timestamp <= ?")
            params.append(to_ts)
        params.append(max(1, limit * 3))
        rows = conn.execute(
            f"""
            SELECT id, timestamp, condition_id, clobTokenId, event_type, payload_json, reason
            FROM "market_deltas"
            WHERE {' AND '.join(where_parts)}
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    finally:
        conn.close()

    events: List[Dict[str, Any]] = []
    for row in rows:
        payload = _parse_json(row["payload_json"])
        summary = row["reason"] or row["event_type"]
        after = payload.get("after") if isinstance(payload.get("after"), dict) else {}
        changed = payload.get("changed_fields") if isinstance(payload.get("changed_fields"), list) else []
        if changed:
            summary = f"{row['event_type']}: {', '.join(str(item) for item in changed[:4])}"
        elif after:
            summary = f"{row['event_type']}: {payload.get('side') or payload.get('outcome_side') or '-'}"
        events.append(
            {
                "id": f"monitoring-{row['id']}",
                "strategy_row_id": detail.get("row_id"),
                "ts": row["timestamp"],
                "event_type": row["event_type"],
                "event_subtype": str(payload.get("side") or payload.get("outcome_side") or "").strip().lower() or None,
                "severity": "warning" if "REMOVE" in str(row["event_type"]) else "info",
                "summary": summary,
                "payload": payload,
                "source": "monitoring",
            }
        )
    return events


def list_strategy_events(row_id: int, args: Dict[str, Any] | None = None, *, detail: Dict[str, Any] | None = None) -> Dict[str, Any]:
    params = args or {}
    try:
        limit = max(1, min(int(params.get("limit", 50)), 500))
    except (TypeError, ValueError):
        limit = 50
    event_type = str(params.get("event_type") or "").strip().lower()
    if detail is None:
        detail = fetch_strategy_detail(row_id, allow_remote_positions=False)
    events = _workspace_events(row_id, limit)
    events.extend(_virtual_strategy_events(row_id, limit, event_type=event_type))
    include_market_events = str(params.get("include_market_events") or params.get("include_monitoring") or "").strip().lower()
    if include_market_events in {"1", "true", "yes", "on"}:
        events.extend(_monitoring_events(detail, limit, params))
    if event_type:
        events = _sort_and_dedupe_events(events)[:limit]
    else:
        events = _limit_with_type_guarantee(events, limit)
    return {
        "strategy_row_id": row_id,
        "count": len(events),
        "data": events,
        "event_db_path": str(_event_db_path()),
        "monitoring_db_path": str(_monitoring_db_path()),
    }
