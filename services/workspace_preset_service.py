from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from services.config_loader import BASE_DIR


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _preset_db_path() -> Path:
    return BASE_DIR / "strategy_workspace_presets.db"


def _connect() -> sqlite3.Connection:
    path = _preset_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS workspace_presets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            scope TEXT NOT NULL,
            strategy_row_id INTEGER,
            target_type TEXT NOT NULL,
            target_payload_json TEXT NOT NULL,
            config_json TEXT NOT NULL,
            created_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_workspace_presets_scope
        ON workspace_presets(scope, strategy_row_id, updated_at_utc DESC)
        """
    )
    conn.commit()
    return conn


def _parse_json(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _normalize_strategy_row_id(value: Any) -> int | None:
    try:
        row_id = int(value)
    except (TypeError, ValueError):
        return None
    return row_id if row_id > 0 else None


def _normalize_target(payload: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
    target = payload.get("target") if isinstance(payload.get("target"), dict) else {}
    target_type = str(target.get("type") or "strategy").strip().lower() or "strategy"
    clean = {
        "type": target_type,
        "row_id": _normalize_strategy_row_id(target.get("row_id")),
        "condition_id": str(target.get("condition_id") or "").strip(),
        "yes_token": str(target.get("yes_token") or "").strip(),
        "no_token": str(target.get("no_token") or "").strip(),
        "question": str(target.get("question") or "").strip(),
    }
    return target_type, clean


def _normalize_config(payload: Dict[str, Any]) -> Dict[str, Any]:
    config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
    return {
        "chart": config.get("chart") if isinstance(config.get("chart"), dict) else {},
        "styles": config.get("styles") if isinstance(config.get("styles"), dict) else {},
        "indicators": config.get("indicators") if isinstance(config.get("indicators"), dict) else {},
    }


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    target = _parse_json(row["target_payload_json"])
    config = _parse_json(row["config_json"])
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "scope": row["scope"],
        "strategy_row_id": row["strategy_row_id"],
        "target_type": row["target_type"],
        "target": target,
        "config": config,
        "created_at": row["created_at_utc"],
        "updated_at": row["updated_at_utc"],
    }


def list_workspace_presets(strategy_row_id: int | None = None) -> List[Dict[str, Any]]:
    t0 = time.perf_counter()
    row_id = _normalize_strategy_row_id(strategy_row_id)
    conn = _connect()
    try:
        if row_id is None:
            rows = conn.execute(
                """
                SELECT *
                FROM workspace_presets
                WHERE strategy_row_id IS NULL
                ORDER BY updated_at_utc DESC, id DESC
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT *
                FROM workspace_presets
                WHERE strategy_row_id IS NULL OR strategy_row_id = ?
                ORDER BY
                    CASE WHEN strategy_row_id = ? THEN 0 ELSE 1 END,
                    updated_at_utc DESC,
                    id DESC
                """,
                (row_id, row_id),
            ).fetchall()
    finally:
        conn.close()
    items = [_row_to_dict(row) for row in rows]
    print(
        f"[SV][workspace_preset] list {(time.perf_counter() - t0) * 1000:.1f}ms "
        f"strategy_row_id={row_id} count={len(items)}"
    )
    return items


def get_workspace_preset(preset_id: int) -> Dict[str, Any]:
    t0 = time.perf_counter()
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM workspace_presets WHERE id = ?", (int(preset_id),)).fetchone()
    finally:
        conn.close()
    if not row:
        raise ValueError(f"Workspace preset not found: {preset_id}")
    item = _row_to_dict(row)
    print(f"[SV][workspace_preset] get {(time.perf_counter() - t0) * 1000:.1f}ms preset_id={preset_id}")
    return item


def save_workspace_preset(strategy_row_id: int | None, payload: Dict[str, Any]) -> Dict[str, Any]:
    t0 = time.perf_counter()
    row_id = _normalize_strategy_row_id(strategy_row_id)
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("Preset name is required.")
    scope = str(payload.get("scope") or ("strategy" if row_id else "global")).strip().lower() or "global"
    if scope not in {"global", "strategy"}:
        raise ValueError("Preset scope must be global or strategy.")
    bound_row_id = row_id if scope == "strategy" else None
    target_type, target = _normalize_target(payload)
    config = _normalize_config(payload)
    now = _now_iso()
    preset_id = _normalize_strategy_row_id(payload.get("id"))
    conn = _connect()
    try:
        if preset_id is not None:
            existing = conn.execute("SELECT id FROM workspace_presets WHERE id = ?", (preset_id,)).fetchone()
            if not existing:
                raise ValueError(f"Workspace preset not found: {preset_id}")
            conn.execute(
                """
                UPDATE workspace_presets
                SET name = ?, scope = ?, strategy_row_id = ?, target_type = ?, target_payload_json = ?, config_json = ?, updated_at_utc = ?
                WHERE id = ?
                """,
                (
                    name,
                    scope,
                    bound_row_id,
                    target_type,
                    json.dumps(target, ensure_ascii=False, sort_keys=True),
                    json.dumps(config, ensure_ascii=False, sort_keys=True),
                    now,
                    preset_id,
                ),
            )
            conn.commit()
            result = get_workspace_preset(preset_id)
            print(
                f"[SV][workspace_preset] save-update {(time.perf_counter() - t0) * 1000:.1f}ms "
                f"preset_id={preset_id} scope={scope} strategy_row_id={bound_row_id}"
            )
            return result

        existing = conn.execute(
            """
            SELECT id
            FROM workspace_presets
            WHERE name = ? AND scope = ? AND (
                (strategy_row_id IS NULL AND ? IS NULL) OR strategy_row_id = ?
            )
            """,
            (name, scope, bound_row_id, bound_row_id),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE workspace_presets
                SET target_type = ?, target_payload_json = ?, config_json = ?, updated_at_utc = ?
                WHERE id = ?
                """,
                (
                    target_type,
                    json.dumps(target, ensure_ascii=False, sort_keys=True),
                    json.dumps(config, ensure_ascii=False, sort_keys=True),
                    now,
                    int(existing["id"]),
                ),
            )
            conn.commit()
            existing_id = int(existing["id"])
            result = get_workspace_preset(existing_id)
            print(
                f"[SV][workspace_preset] save-overwrite {(time.perf_counter() - t0) * 1000:.1f}ms "
                f"preset_id={existing_id} scope={scope} strategy_row_id={bound_row_id}"
            )
            return result

        cur = conn.execute(
            """
            INSERT INTO workspace_presets (
                name, scope, strategy_row_id, target_type, target_payload_json, config_json, created_at_utc, updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                scope,
                bound_row_id,
                target_type,
                json.dumps(target, ensure_ascii=False, sort_keys=True),
                json.dumps(config, ensure_ascii=False, sort_keys=True),
                now,
                now,
            ),
        )
        conn.commit()
        new_id = int(cur.lastrowid)
        result = get_workspace_preset(new_id)
        print(
            f"[SV][workspace_preset] save-insert {(time.perf_counter() - t0) * 1000:.1f}ms "
            f"preset_id={new_id} scope={scope} strategy_row_id={bound_row_id}"
        )
        return result
    finally:
        conn.close()


def delete_workspace_preset(preset_id: int) -> Dict[str, Any]:
    t0 = time.perf_counter()
    preset = get_workspace_preset(preset_id)
    conn = _connect()
    try:
        conn.execute("DELETE FROM workspace_presets WHERE id = ?", (int(preset_id),))
        conn.commit()
    finally:
        conn.close()
    print(f"[SV][workspace_preset] delete {(time.perf_counter() - t0) * 1000:.1f}ms preset_id={preset_id}")
    return {"deleted": True, "preset": preset, "db_path": str(_preset_db_path())}
