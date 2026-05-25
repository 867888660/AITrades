"""
One-time migration: remove unused strategy_legs columns.

Dropped columns:
- direction
- weight

The migration creates a timestamped SQLite backup before rebuilding the table.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from services.strategy_data_source import db_path


LEG_COLUMNS = [
    "leg_id",
    "strategy_id",
    "leg_index",
    "condition_id",
    "yes_token",
    "no_token",
    "budget_cap",
    "params_json",
    "yes_qty",
    "no_qty",
    "yes_avg_cost",
    "no_avg_cost",
    "yes_current_price",
    "no_current_price",
    "unrealized_pnl",
    "position_source",
    "position_updated_at",
    "created_at_utc",
    "updated_at_utc",
]


CREATE_LEGS_SQL = """
CREATE TABLE strategy_legs_new (
    leg_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id         INTEGER NOT NULL,
    leg_index           INTEGER NOT NULL DEFAULT 0,
    condition_id        TEXT    NOT NULL DEFAULT '',
    yes_token           TEXT,
    no_token            TEXT,
    budget_cap          REAL    NOT NULL DEFAULT 0,
    params_json         TEXT    NOT NULL DEFAULT '{}',
    yes_qty             REAL    NOT NULL DEFAULT 0,
    no_qty              REAL    NOT NULL DEFAULT 0,
    yes_avg_cost        REAL,
    no_avg_cost         REAL,
    yes_current_price   REAL,
    no_current_price    REAL,
    unrealized_pnl      REAL    NOT NULL DEFAULT 0,
    position_source     TEXT    NOT NULL DEFAULT '',
    position_updated_at TEXT,
    created_at_utc      TEXT    NOT NULL,
    updated_at_utc      TEXT    NOT NULL,
    FOREIGN KEY (strategy_id) REFERENCES strategy_registry(strategy_id) ON DELETE CASCADE,
    UNIQUE(strategy_id, leg_index)
);
"""


CREATE_INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_strategy_legs_strategy_id ON strategy_legs(strategy_id);
CREATE INDEX IF NOT EXISTS idx_strategy_legs_condition_id ON strategy_legs(condition_id);
CREATE INDEX IF NOT EXISTS idx_strategy_legs_yes_token ON strategy_legs(yes_token);
CREATE INDEX IF NOT EXISTS idx_strategy_legs_no_token ON strategy_legs(no_token);
"""


def _columns(conn: sqlite3.Connection) -> list[str]:
    return [str(row[1]) for row in conn.execute("PRAGMA table_info(strategy_legs)")]


def _dependent_views(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    rows = conn.execute(
        """SELECT name, sql
           FROM sqlite_master
           WHERE type = 'view'
             AND sql LIKE '%strategy_legs%'"""
    ).fetchall()
    return [(str(name), str(sql)) for name, sql in rows if sql]


def _backup_database(source_path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = source_path.with_name(f"{source_path.stem}.backup_before_drop_leg_columns_{timestamp}{source_path.suffix}")
    src = sqlite3.connect(str(source_path))
    try:
        dst = sqlite3.connect(str(backup_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return backup_path


def main() -> None:
    path = db_path()
    if not path.exists():
        raise SystemExit(f"Database not found: {path}")

    backup_path = _backup_database(path)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        current_cols = _columns(conn)
        unused_cols = {"direction", "weight"} & set(current_cols)
        if not unused_cols:
            print(f"No unused leg columns found. Backup kept at: {backup_path}")
            return

        missing = [col for col in LEG_COLUMNS if col not in current_cols]
        if missing:
            raise RuntimeError(f"strategy_legs is missing expected columns: {missing}")

        cols_sql = ", ".join(LEG_COLUMNS)
        views = _dependent_views(conn)
        conn.execute("BEGIN")
        for view_name, _view_sql in views:
            conn.execute(f'DROP VIEW IF EXISTS "{view_name}"')
        conn.execute("DROP TABLE IF EXISTS strategy_legs_new")
        conn.execute(CREATE_LEGS_SQL)
        conn.execute(
            f"INSERT INTO strategy_legs_new ({cols_sql}) SELECT {cols_sql} FROM strategy_legs"
        )
        conn.execute("DROP TABLE strategy_legs")
        conn.execute("ALTER TABLE strategy_legs_new RENAME TO strategy_legs")
        conn.executescript(CREATE_INDEXES_SQL)
        for _view_name, view_sql in views:
            conn.execute(view_sql)
        conn.commit()
        print(f"Dropped columns: {', '.join(sorted(unused_cols))}")
        print(f"Backup: {backup_path}")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.close()


if __name__ == "__main__":
    main()
