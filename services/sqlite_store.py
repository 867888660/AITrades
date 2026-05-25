from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


def _to_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def _ensure_db(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute("PRAGMA synchronous=NORMAL;")
    cur.execute("PRAGMA busy_timeout=5000;")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS "__meta_cols" (
            table_name TEXT NOT NULL,
            col_name TEXT NOT NULL,
            last_value TEXT,
            updated_at_utc TEXT,
            PRIMARY KEY (table_name, col_name)
        )
        """
    )
    conn.commit()


def _ensure_table(conn: sqlite3.Connection, table_name: str) -> None:
    cur = conn.cursor()
    cur.execute(
        f'''
        CREATE TABLE IF NOT EXISTS "{table_name}" (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            saved_at_utc TEXT NOT NULL
        )
        '''
    )
    cur.execute(f'CREATE INDEX IF NOT EXISTS "idx_{table_name}_saved_at" ON "{table_name}"(saved_at_utc)')
    conn.commit()


def _get_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.cursor().execute(f'PRAGMA table_info("{table_name}")').fetchall()
    return {str(row[1]) for row in rows}


def _ensure_columns(conn: sqlite3.Connection, table_name: str, columns: list[str]) -> None:
    existing = _get_columns(conn, table_name)
    cur = conn.cursor()
    changed = False
    for column in columns:
        if column in existing:
            continue
        cur.execute(f'ALTER TABLE "{table_name}" ADD COLUMN "{column}" TEXT')
        changed = True
    if changed:
        conn.commit()


def _get_last_value(conn: sqlite3.Connection, table_name: str, column: str) -> Optional[str]:
    row = conn.cursor().execute(
        'SELECT last_value FROM "__meta_cols" WHERE table_name = ? AND col_name = ? LIMIT 1',
        (table_name, column),
    ).fetchone()
    return row[0] if row else None


def _set_last_value(conn: sqlite3.Connection, table_name: str, column: str, value: Optional[str]) -> None:
    conn.cursor().execute(
        """
        INSERT INTO "__meta_cols"(table_name, col_name, last_value, updated_at_utc)
        VALUES(?, ?, ?, ?)
        ON CONFLICT(table_name, col_name) DO UPDATE SET
            last_value=excluded.last_value,
            updated_at_utc=excluded.updated_at_utc
        """,
        (table_name, column, value, datetime.now(timezone.utc).isoformat()),
    )


def write_wide_snapshot(db_path: str, table_name: str, row: Dict[str, Any], saved_at_utc: Optional[str] = None) -> Dict[str, Any]:
    db_file = Path(db_path).expanduser()
    db_file.parent.mkdir(parents=True, exist_ok=True)
    ts = saved_at_utc or datetime.now(timezone.utc).isoformat()

    conn = sqlite3.connect(str(db_file), timeout=5.0)
    try:
        _ensure_db(conn)
        _ensure_table(conn, table_name)

        columns = list(row.keys())
        _ensure_columns(conn, table_name, columns)

        row_values = []
        changed_pairs: list[tuple[str, Optional[str]]] = []
        has_change = False
        for column in columns:
            current = _to_text(row.get(column))
            last_value = _get_last_value(conn, table_name, column)
            if current == last_value:
                row_values.append(None)
            else:
                has_change = True
                row_values.append(current)
                changed_pairs.append((column, current))

        if not has_change:
            return {"inserted": 0, "skipped": 1, "table": table_name, "db_path": str(db_file)}

        all_columns = ["saved_at_utc"] + columns
        placeholders = ",".join(["?"] * len(all_columns))
        sql_columns = ",".join(f'"{column}"' for column in all_columns)
        conn.cursor().execute(
            f'INSERT INTO "{table_name}"({sql_columns}) VALUES({placeholders})',
            [ts] + row_values,
        )
        for column, value in changed_pairs:
            _set_last_value(conn, table_name, column, value)
        conn.commit()
        return {"inserted": 1, "skipped": 0, "table": table_name, "db_path": str(db_file)}
    finally:
        conn.close()
