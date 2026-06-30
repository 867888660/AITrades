"""
One-time migration: polyMarket_Monitoring -> strategy_registry + strategy_legs
Run from project root:  python migrate_to_strategy_tables.py
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "Data" / "PolyMarketMonitoring.db"
OLD_TABLE = "polyMarket_Monitoring"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_float(v, default: float = 0.0) -> float:
    try:
        if v is None or str(v).strip() in ("", "\u7a7a"):
            return default
        return float(v)
    except Exception:
        return default


def build_input_json(row: dict) -> str:
    payload: dict = {}
    for i in range(1, 14):
        key = f"Inputs{i}"
        val = row.get(key)
        if val is not None and str(val).strip() not in ("", "\u7a7a"):
            payload[key] = val
    return json.dumps(payload, ensure_ascii=False)


def resolve_mode(row: dict) -> str:
    raw = str(row.get("State") or row.get("IsVirtual") or "").strip().lower()
    if raw in ("true", "1", "yes", "virtual"):
        return "Virtual"
    if raw in ("real",):
        return "Real"
    return "Stop"


def polymarket_instrument_id(condition_id: str, yes_token: str | None, no_token: str | None) -> str:
    condition_id = str(condition_id or "").strip()
    if condition_id:
        return f"poly:condition:{condition_id}"
    yes_token = str(yes_token or "").strip()
    no_token = str(no_token or "").strip()
    if yes_token or no_token:
        return f"poly:tokens:{yes_token}:{no_token}"
    return "polymarket_binary:polymarket:leg0"


DDL_REGISTRY = """
CREATE TABLE IF NOT EXISTS strategy_registry (
    strategy_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_uid      TEXT    NOT NULL UNIQUE,
    strategy_name     TEXT    NOT NULL,
    strategy_code     TEXT    NOT NULL DEFAULT '',
    mode              TEXT    NOT NULL DEFAULT 'Stop'
                      CHECK (mode IN ('Stop', 'Virtual', 'Real')),
    initial_capital   REAL    NOT NULL DEFAULT 0,
    strategy_bankroll REAL    NOT NULL DEFAULT 0,
    profit_roll_ratio REAL    NOT NULL DEFAULT 0,
    realized_profit   REAL    NOT NULL DEFAULT 0,
    input_json        TEXT    NOT NULL DEFAULT '{}',
    created_at_utc    TEXT    NOT NULL,
    updated_at_utc    TEXT    NOT NULL
);
"""

DDL_LEGS = """
CREATE TABLE IF NOT EXISTS strategy_legs (
    leg_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id     INTEGER NOT NULL,
    leg_uid         TEXT    NOT NULL DEFAULT '',
    leg_index       INTEGER NOT NULL DEFAULT 0,
    condition_id    TEXT    NOT NULL DEFAULT '',
    yes_token       TEXT,
    no_token        TEXT,
    leg_kind        TEXT    NOT NULL DEFAULT 'binary_market',
    asset_class     TEXT    NOT NULL DEFAULT 'polymarket_binary',
    venue           TEXT    NOT NULL DEFAULT 'polymarket',
    symbol          TEXT    NOT NULL DEFAULT '',
    instrument_id   TEXT    NOT NULL DEFAULT '',
    instrument_json TEXT    NOT NULL DEFAULT '{}',
    budget_cap      REAL    NOT NULL DEFAULT 0,
    params_json     TEXT    NOT NULL DEFAULT '{}',
    created_at_utc  TEXT    NOT NULL,
    updated_at_utc  TEXT    NOT NULL,
    FOREIGN KEY (strategy_id) REFERENCES strategy_registry(strategy_id) ON DELETE CASCADE,
    UNIQUE(strategy_id, leg_index)
);
"""

DDL_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_strategy_registry_mode ON strategy_registry(mode);
CREATE INDEX IF NOT EXISTS idx_strategy_legs_strategy_id ON strategy_legs(strategy_id);
CREATE INDEX IF NOT EXISTS idx_strategy_legs_condition_id ON strategy_legs(condition_id);
CREATE INDEX IF NOT EXISTS idx_strategy_legs_yes_token ON strategy_legs(yes_token);
CREATE INDEX IF NOT EXISTS idx_strategy_legs_no_token ON strategy_legs(no_token);
CREATE INDEX IF NOT EXISTS idx_strategy_legs_instrument_id ON strategy_legs(instrument_id);
CREATE INDEX IF NOT EXISTS idx_strategy_legs_asset_class ON strategy_legs(asset_class);
CREATE INDEX IF NOT EXISTS idx_strategy_legs_leg_kind ON strategy_legs(leg_kind);
"""

DDL_COMPAT_VIEW = """
CREATE VIEW IF NOT EXISTS strategy_monitoring_compat AS
SELECT
    s.strategy_id                AS row_id,
    s.strategy_name              AS "Strategy",
    s.strategy_code              AS "Code",
    s.mode                       AS "Mode",
    s.mode                       AS "State",
    s.initial_capital            AS "initial_capital",
    s.strategy_bankroll          AS "strategy_bankroll",
    s.profit_roll_ratio          AS "profit_roll_ratio",
    s.realized_profit            AS "realized_profit",
    s.input_json                 AS "input_json",
    l.condition_id               AS "condition_id",
    l.yes_token                  AS "yes_token",
    l.no_token                   AS "no_token",
    l.leg_kind                   AS "leg_kind",
    l.asset_class                AS "asset_class",
    l.venue                      AS "venue",
    l.symbol                     AS "symbol",
    l.instrument_id              AS "instrument_id",
    l.budget_cap                 AS "budget_cap"
FROM strategy_registry s
LEFT JOIN strategy_legs l
       ON l.strategy_id = s.strategy_id AND l.leg_index = 0;
"""


def migrate():
    if not DB_PATH.exists():
        print(f"DB not found: {DB_PATH}")
        return

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row

    # Check if already migrated
    existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "strategy_registry" in existing:
        count = conn.execute("SELECT COUNT(*) FROM strategy_registry").fetchone()[0]
        if count > 0:
            print(f"strategy_registry already has {count} rows, skipping migration.")
            conn.close()
            return

    # Create new tables
    conn.executescript(DDL_REGISTRY)
    conn.executescript(DDL_LEGS)
    conn.executescript(DDL_INDEXES)
    # Drop view if exists before recreating
    conn.execute("DROP VIEW IF EXISTS strategy_monitoring_compat")
    conn.executescript(DDL_COMPAT_VIEW)

    # Read old data
    try:
        rows = conn.execute(f'SELECT rowid, * FROM "{OLD_TABLE}"').fetchall()
    except Exception as exc:
        print(f"Cannot read {OLD_TABLE}: {exc}")
        conn.close()
        return

    print(f"Migrating {len(rows)} rows from {OLD_TABLE} ...")

    ts = now_iso()
    for row in rows:
        d = dict(row)
        rid = d.get("rowid")
        strategy_uid = f"stg_{uuid.uuid4().hex[:16]}"
        strategy_name = d.get("Strategy") or d.get("Code") or f"strategy_{rid}"
        strategy_code = d.get("Strategy") or d.get("Code") or ""
        mode = resolve_mode(d)

        conn.execute(
            """INSERT INTO strategy_registry(
                strategy_uid, strategy_name, strategy_code, mode,
                initial_capital, strategy_bankroll, profit_roll_ratio, realized_profit,
                input_json, created_at_utc, updated_at_utc
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                strategy_uid,
                strategy_name,
                strategy_code,
                mode,
                safe_float(d.get("initial_capital")),
                safe_float(d.get("strategy_bankroll")),
                safe_float(d.get("profit_roll_ratio")),
                safe_float(d.get("realized_profit")),
                build_input_json(d),
                ts,
                ts,
            ),
        )
        strategy_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        yes_cap = safe_float(d.get("Yes_Max_BudgetCap"))
        no_cap = safe_float(d.get("No_Max_BudgetCap"))
        budget_cap = max(yes_cap, no_cap)
        condition_id = d.get("condition_id") or ""
        yes_token = d.get("yes_token") or None
        no_token = d.get("no_token") or None
        instrument_id = polymarket_instrument_id(condition_id, yes_token, no_token)

        conn.execute(
            """INSERT INTO strategy_legs(
                strategy_id, leg_uid, leg_index, condition_id, yes_token, no_token,
                leg_kind, asset_class, venue, symbol, instrument_id, instrument_json,
                budget_cap, params_json,
                created_at_utc, updated_at_utc
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                strategy_id,
                f"leg_{strategy_id}_0_{uuid.uuid4().hex[:8]}",
                0,
                condition_id,
                yes_token,
                no_token,
                "binary_market",
                "polymarket_binary",
                "polymarket",
                "",
                instrument_id,
                "{}",
                budget_cap,
                "{}",
                ts,
                ts,
            ),
        )
        print(f"  rowid={rid} -> strategy_id={strategy_id} uid={strategy_uid} mode={mode}")

    conn.commit()
    # Verify
    reg_count = conn.execute("SELECT COUNT(*) FROM strategy_registry").fetchone()[0]
    leg_count = conn.execute("SELECT COUNT(*) FROM strategy_legs").fetchone()[0]
    print(f"\nDone. strategy_registry={reg_count}, strategy_legs={leg_count}")
    conn.close()


if __name__ == "__main__":
    migrate()
