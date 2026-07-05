from __future__ import annotations

import json
import importlib.util
import math
import sqlite3
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.config_loader import BASE_DIR, get_market_realtime_db_path, load_web_settings
from services.http_client import SESSION, get_timeout


HISTORY_DB_PATH = BASE_DIR / "Data" / "history_workspace.db"
BINANCE_BASE_URLS = ("https://api.binance.com", "https://data-api.binance.vision")
CLOB_BASE_URL = "https://clob.polymarket.com"
DEFAULT_BINANCE_INTERVAL = "1m"
DEFAULT_BACKTEST_CASH = 10_000.0
DEFAULT_BACKTEST_FEE_BPS = 2.0
BACKTEST_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="history-backtest")


def _interval_ms(interval: str) -> int:
    text = str(interval or DEFAULT_BINANCE_INTERVAL).strip().lower()
    unit = text[-1:] if text else "m"
    amount = _safe_int(text[:-1], 1)
    if unit == "s":
        return max(1, amount) * 1000
    if unit == "h":
        return max(1, amount) * 60 * 60 * 1000
    if unit == "d":
        return max(1, amount) * 24 * 60 * 60 * 1000
    if unit == "w":
        return max(1, amount) * 7 * 24 * 60 * 60 * 1000
    return max(1, amount) * 60 * 1000


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or str(value).strip() == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None or str(value).strip() == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _finite_float(value: Any, default: float = 0.0) -> float:
    number = _safe_float(value, default)
    if number is None or not math.isfinite(number):
        return default
    return float(number)


def _parse_time_ms(value: Any) -> Optional[int]:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        number = int(raw)
        return number if number > 10_000_000_000 else number * 1000
    normalized = raw
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return None


def _ms_to_iso(value: Any) -> Optional[str]:
    ms = _safe_int(value, 0)
    if ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000, timezone.utc).replace(microsecond=0).isoformat()


def _connect() -> sqlite3.Connection:
    HISTORY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(HISTORY_DB_PATH), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=10000;")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS history_watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            instrument_id TEXT NOT NULL,
            symbol TEXT NOT NULL DEFAULT '',
            display_name TEXT NOT NULL DEFAULT '',
            venue TEXT NOT NULL DEFAULT '',
            asset_class TEXT NOT NULL DEFAULT '',
            condition_id TEXT NOT NULL DEFAULT '',
            token_id TEXT NOT NULL DEFAULT '',
            side TEXT NOT NULL DEFAULT '',
            interval TEXT NOT NULL DEFAULT '',
            meta_json TEXT NOT NULL DEFAULT '{}',
            created_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL,
            UNIQUE(source, instrument_id, interval)
        );

        CREATE TABLE IF NOT EXISTS binance_klines (
            symbol TEXT NOT NULL,
            interval TEXT NOT NULL,
            open_time_ms INTEGER NOT NULL,
            open_time_utc TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            close_time_ms INTEGER,
            quote_volume REAL,
            trades INTEGER,
            taker_buy_base_volume REAL,
            taker_buy_quote_volume REAL,
            fetched_at_utc TEXT NOT NULL,
            PRIMARY KEY(symbol, interval, open_time_ms)
        );

        CREATE TABLE IF NOT EXISTS polymarket_price_history (
            token_id TEXT NOT NULL,
            condition_id TEXT NOT NULL DEFAULT '',
            ts INTEGER NOT NULL,
            ts_utc TEXT NOT NULL,
            price REAL,
            interval TEXT NOT NULL DEFAULT '',
            fetched_at_utc TEXT NOT NULL,
            PRIMARY KEY(token_id, ts, interval)
        );

        CREATE INDEX IF NOT EXISTS idx_history_watchlist_source ON history_watchlist(source, updated_at_utc DESC);
        CREATE INDEX IF NOT EXISTS idx_binance_klines_symbol_interval ON binance_klines(symbol, interval, open_time_ms);
        CREATE INDEX IF NOT EXISTS idx_poly_history_token ON polymarket_price_history(token_id, ts);

        CREATE TABLE IF NOT EXISTS backtest_cases (
            case_id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_name TEXT NOT NULL,
            collection_name TEXT NOT NULL DEFAULT '',
            strategy_id INTEGER,
            legs_json TEXT NOT NULL DEFAULT '[]',
            params_json TEXT NOT NULL DEFAULT '{}',
            data_window_json TEXT NOT NULL DEFAULT '{}',
            execution_config_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'draft',
            created_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_backtest_cases_updated
        ON backtest_cases(updated_at_utc DESC);

        CREATE TABLE IF NOT EXISTS backtest_collections (
            collection_name TEXT PRIMARY KEY,
            schema_json TEXT NOT NULL DEFAULT '{}',
            created_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS backtest_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id INTEGER NOT NULL,
            strategy_id INTEGER,
            status TEXT NOT NULL DEFAULT 'planned',
            case_snapshot_json TEXT NOT NULL DEFAULT '{}',
            metrics_json TEXT NOT NULL DEFAULT '{}',
            error TEXT,
            created_at_utc TEXT NOT NULL,
            started_at_utc TEXT,
            finished_at_utc TEXT,
            updated_at_utc TEXT NOT NULL,
            FOREIGN KEY(case_id) REFERENCES backtest_cases(case_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS backtest_equity_points (
            run_id INTEGER NOT NULL,
            ts_utc TEXT NOT NULL,
            equity REAL,
            cash REAL,
            exposure REAL,
            pnl REAL,
            meta_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY(run_id, ts_utc),
            FOREIGN KEY(run_id) REFERENCES backtest_runs(run_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS backtest_orders (
            order_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            ts_utc TEXT NOT NULL,
            leg_id TEXT NOT NULL DEFAULT '',
            instrument_id TEXT NOT NULL DEFAULT '',
            side TEXT NOT NULL DEFAULT '',
            quantity REAL,
            price REAL,
            fee REAL,
            status TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL DEFAULT '',
            meta_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY(run_id) REFERENCES backtest_runs(run_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS backtest_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            ts_utc TEXT NOT NULL,
            event_type TEXT NOT NULL DEFAULT '',
            message TEXT NOT NULL DEFAULT '',
            meta_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY(run_id) REFERENCES backtest_runs(run_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_backtest_runs_case
        ON backtest_runs(case_id, updated_at_utc DESC);

        CREATE INDEX IF NOT EXISTS idx_backtest_equity_run
        ON backtest_equity_points(run_id, ts_utc);

        CREATE INDEX IF NOT EXISTS idx_backtest_orders_run
        ON backtest_orders(run_id, ts_utc);

        CREATE INDEX IF NOT EXISTS idx_backtest_events_run
        ON backtest_events(run_id, ts_utc);
        """
    )
    _ensure_column(conn, "backtest_cases", "collection_name", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "backtest_runs", "batch_id", "TEXT NOT NULL DEFAULT ''")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_backtest_cases_collection ON backtest_cases(collection_name, updated_at_utc DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_backtest_runs_batch ON backtest_runs(batch_id, updated_at_utc DESC)"
    )
    conn.commit()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    cols = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _row_dict(row: sqlite3.Row) -> Dict[str, Any]:
    result = dict(row)
    if "meta_json" in result:
        try:
            result["meta"] = json.loads(result.get("meta_json") or "{}")
        except (TypeError, ValueError):
            result["meta"] = {}
        result.pop("meta_json", None)
    return result


def list_watchlist(source: str = "") -> List[Dict[str, Any]]:
    conn = _connect()
    try:
        if source:
            rows = conn.execute(
                "SELECT * FROM history_watchlist WHERE source = ? ORDER BY updated_at_utc DESC, id DESC",
                (source,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM history_watchlist ORDER BY updated_at_utc DESC, id DESC"
            ).fetchall()
        return [_row_dict(row) for row in rows]
    finally:
        conn.close()


def add_watchlist_item(payload: Dict[str, Any]) -> Dict[str, Any]:
    source = str(payload.get("source") or "").strip().lower()
    if source not in {"binance", "polymarket"}:
        raise ValueError("source must be binance or polymarket")
    interval = str(payload.get("interval") or "").strip()
    if source == "binance" and not interval:
        interval = DEFAULT_BINANCE_INTERVAL
    instrument_id = str(payload.get("instrument_id") or "").strip()
    symbol = str(payload.get("symbol") or "").strip().upper()
    condition_id = str(payload.get("condition_id") or "").strip()
    token_id = str(payload.get("token_id") or "").strip()
    if not instrument_id:
        if source == "binance" and symbol:
            instrument_id = f"crypto_spot:binance:{symbol}"
        elif source == "polymarket" and token_id:
            instrument_id = f"polymarket:token:{token_id}"
        elif source == "polymarket" and condition_id:
            instrument_id = f"polymarket:condition:{condition_id}"
    if not instrument_id:
        raise ValueError("instrument_id, symbol, condition_id, or token_id is required")
    ts = _now_iso()
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        meta = {k: v for k, v in payload.items() if k not in {"meta"}}
    conn = _connect()
    try:
        conn.execute(
            """INSERT INTO history_watchlist(
                   source, instrument_id, symbol, display_name, venue, asset_class,
                   condition_id, token_id, side, interval, meta_json, created_at_utc, updated_at_utc
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(source, instrument_id, interval)
               DO UPDATE SET
                   symbol = excluded.symbol,
                   display_name = excluded.display_name,
                   venue = excluded.venue,
                   asset_class = excluded.asset_class,
                   condition_id = excluded.condition_id,
                   token_id = excluded.token_id,
                   side = excluded.side,
                   meta_json = excluded.meta_json,
                   updated_at_utc = excluded.updated_at_utc""",
            (
                source,
                instrument_id,
                symbol,
                str(payload.get("display_name") or payload.get("display_symbol") or symbol or instrument_id).strip(),
                str(payload.get("venue") or source).strip(),
                str(payload.get("asset_class") or "").strip(),
                condition_id,
                token_id,
                str(payload.get("side") or "").strip(),
                interval,
                json.dumps(meta, ensure_ascii=False, sort_keys=True),
                ts,
                ts,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM history_watchlist WHERE source = ? AND instrument_id = ? AND interval = ?",
            (source, instrument_id, interval),
        ).fetchone()
        return _row_dict(row)
    finally:
        conn.close()


def delete_watchlist_item(item_id: int) -> bool:
    conn = _connect()
    try:
        affected = conn.execute("DELETE FROM history_watchlist WHERE id = ?", (int(item_id),)).rowcount
        conn.commit()
        return affected > 0
    finally:
        conn.close()


def _loads_json(raw: Any, fallback: Any) -> Any:
    try:
        return json.loads(raw or json.dumps(fallback))
    except (TypeError, ValueError):
        return fallback


def _get_coverage_cached(
    cache: Optional[Dict[str, Dict[str, Any]]],
    source: str,
    **kwargs: Any,
) -> Dict[str, Any]:
    source = str(source or "").strip().lower()
    key = json.dumps({"source": source, **kwargs}, sort_keys=True, ensure_ascii=False)
    if cache is not None and key in cache:
        return cache[key]
    coverage = get_coverage(source, **kwargs)
    if cache is not None:
        cache[key] = coverage
    return coverage


def _coverage_window_for_leg(
    leg: Dict[str, Any],
    coverage_cache: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    source = _leg_source(leg)
    if source == "binance":
        coverage = _get_coverage_cached(
            coverage_cache,
            "binance",
            symbol=leg.get("symbol"),
            interval=leg.get("interval") or DEFAULT_BINANCE_INTERVAL,
        )
        return {
            "source": "binance",
            "instrument": leg.get("display_name") or leg.get("symbol") or leg.get("instrument_id"),
            "count": int(coverage.get("count") or 0),
            "from": coverage.get("from"),
            "to": coverage.get("to"),
            "segments": coverage.get("segments") if isinstance(coverage.get("segments"), list) else [],
            "status": coverage.get("status") or "empty",
            "coverage": coverage,
        }
    if source == "polymarket":
        coverage = _get_coverage_cached(
            coverage_cache,
            "polymarket",
            condition_id=leg.get("condition_id"),
            token_id=leg.get("token_id") or leg.get("yes_token"),
        )
        local = coverage.get("local_market_deltas") or {}
        downloaded = coverage.get("downloaded_price_history") or {}
        best = downloaded if int(downloaded.get("count") or 0) else local
        return {
            "source": "polymarket",
            "instrument": leg.get("display_name") or leg.get("instrument_id"),
            "count": int(best.get("count") or 0),
            "from": best.get("from"),
            "to": best.get("to"),
            "status": coverage.get("status") or "empty",
            "coverage": coverage,
        }
    return {
        "source": source or "unknown",
        "instrument": leg.get("display_name") or leg.get("instrument_id"),
        "count": 0,
        "from": None,
        "to": None,
        "status": "unknown_source",
        "coverage": {},
    }


def _case_data_availability(
    legs: List[Dict[str, Any]],
    coverage_cache: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    leg_windows = [_coverage_window_for_leg(leg, coverage_cache) for leg in legs]
    starts = [_parse_time_ms(item.get("from")) for item in leg_windows if int(item.get("count") or 0) > 0]
    ends = [_parse_time_ms(item.get("to")) for item in leg_windows if int(item.get("count") or 0) > 0]
    starts = [item for item in starts if item is not None]
    ends = [item for item in ends if item is not None]
    common_start = max(starts) if starts and len(starts) == len(legs) else None
    common_end = min(ends) if ends and len(ends) == len(legs) else None
    has_common = bool(common_start and common_end and common_start <= common_end)
    missing = [item for item in leg_windows if int(item.get("count") or 0) <= 0]
    single_segments = leg_windows[0].get("segments") if len(leg_windows) == 1 and isinstance(leg_windows[0].get("segments"), list) else []
    if single_segments:
        preview_segments = single_segments[:3]
        segment_summary = " | ".join(f"{item.get('from')} -> {item.get('to')}" for item in preview_segments)
        if len(single_segments) > len(preview_segments):
            segment_summary += f" | +{len(single_segments) - len(preview_segments)} more"
    else:
        segment_summary = ""
    return {
        "legs": leg_windows,
        "common_start": _ms_to_iso(common_start) if common_start else None,
        "common_end": _ms_to_iso(common_end) if common_end else None,
        "has_common_window": has_common,
        "status": "ok" if has_common else ("missing_data" if missing else "no_overlap"),
        "summary": (
            segment_summary
            if segment_summary
            else f"{_ms_to_iso(common_start)} -> {_ms_to_iso(common_end)}"
            if has_common
            else ("missing data" if missing else "no overlapping window")
        ),
    }


def _decode_case_row(
    row: sqlite3.Row | None,
    coverage_cache: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    if row is None:
        return {}
    item = dict(row)
    item["legs"] = _loads_json(item.get("legs_json"), [])
    item["params"] = _loads_json(item.get("params_json"), {})
    item["data_window"] = _loads_json(item.get("data_window_json"), {})
    item["execution_config"] = _loads_json(item.get("execution_config_json"), {})
    for key in ("legs_json", "params_json", "data_window_json", "execution_config_json"):
        item.pop(key, None)
    try:
        strategy = item.get("execution_config", {}).get("strategy_snapshot") or None
        item["execution_config"]["compatibility"] = _case_compatibility(item["legs"], strategy, coverage_cache=coverage_cache)
    except Exception:
        pass
    try:
        item["data_availability"] = _case_data_availability(item["legs"], coverage_cache)
    except Exception as exc:
        item["data_availability"] = {"status": "error", "summary": str(exc), "legs": []}
    return item


def _leg_source(leg: Dict[str, Any]) -> str:
    return str(leg.get("source") or leg.get("venue") or "").strip().lower()


def _leg_asset_class(leg: Dict[str, Any]) -> str:
    return str(leg.get("asset_class") or "").strip().lower()


def _leg_signature(leg: Dict[str, Any]) -> Dict[str, str]:
    source = _leg_source(leg)
    asset_class = _leg_asset_class(leg)
    return {
        "source": source,
        "venue": str(leg.get("venue") or source).strip().lower(),
        "asset_class": asset_class,
        "leg_kind": str(leg.get("leg_kind") or "").strip().lower(),
        "role": str(leg.get("role") or leg.get("purpose") or "").strip().lower(),
    }


def _case_schema_from_legs(legs: List[Dict[str, Any]], data_window: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    window = data_window if isinstance(data_window, dict) else {}
    return {
        "legs": [_leg_signature(leg) for leg in legs],
        "data_window": {
            "start": window.get("start"),
            "end": window.get("end"),
            "enddate_from": window.get("enddate_from"),
            "enddate_to": window.get("enddate_to"),
        },
    }


def _schema_mismatch(schema: Dict[str, Any], legs: List[Dict[str, Any]], data_window: Optional[Dict[str, Any]] = None) -> List[str]:
    expected_legs = schema.get("legs") if isinstance(schema.get("legs"), list) else []
    actual = _case_schema_from_legs(legs, data_window)
    actual_legs = actual.get("legs") or []
    messages: List[str] = []
    if expected_legs and len(expected_legs) != len(actual_legs):
        messages.append(f"collection expects {len(expected_legs)} legs, case has {len(actual_legs)} legs")
        return messages
    for index, expected in enumerate(expected_legs):
        got = actual_legs[index] if index < len(actual_legs) else {}
        for key in ("source", "venue", "asset_class"):
            if expected.get(key) and got.get(key) and expected.get(key) != got.get(key):
                messages.append(f"leg {index + 1} {key} expects {expected.get(key)}, got {got.get(key)}")
    expected_window = schema.get("data_window") if isinstance(schema.get("data_window"), dict) else {}
    actual_window = actual.get("data_window") or {}
    for key in ("start", "end", "enddate_from", "enddate_to"):
        if expected_window.get(key) and actual_window.get(key) and expected_window.get(key) != actual_window.get(key):
            messages.append(f"data_window.{key} expects {expected_window.get(key)}, got {actual_window.get(key)}")
    return messages


def _strategy_code_schema(code_name: str) -> Dict[str, Any]:
    if not code_name:
        return {}
    try:
        from services.strategy_schema_service import get_strategy_code_schemas

        return get_strategy_code_schemas(code_name) or {}
    except Exception:
        return {}


def _strategy_code_file(code_name: str) -> Optional[Path]:
    safe = "".join(ch for ch in str(code_name or "") if ch.isalnum() or ch in ("_", "-"))
    if not safe:
        return None
    path = BASE_DIR / "StrategyCode" / f"{safe}.py"
    return path if path.is_file() else None


def _load_strategy_code_module(code_name: str):
    path = _strategy_code_file(code_name)
    if not path:
        return None
    module_name = f"_history_backtest_{path.stem}_{int(time.time() * 1000)}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, str(path))
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop(module_name, None)


def _case_compatibility(
    legs: List[Dict[str, Any]],
    strategy: Optional[Dict[str, Any]] = None,
    strategy_code: str = "",
    coverage_cache: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    issues: List[Dict[str, str]] = []
    sources = {_leg_source(leg) for leg in legs if _leg_source(leg)}
    asset_classes = {_leg_asset_class(leg) for leg in legs if _leg_asset_class(leg)}
    if not legs:
        issues.append({"level": "error", "message": "No legs selected."})
    for leg in legs:
        source = _leg_source(leg)
        coverage = _get_coverage_cached(
            coverage_cache,
            "binance" if source == "binance" else "polymarket",
            symbol=leg.get("symbol"),
            interval=leg.get("interval") or DEFAULT_BINANCE_INTERVAL,
            condition_id=leg.get("condition_id"),
            token_id=leg.get("token_id") or leg.get("yes_token"),
        )
        if source == "binance":
            if int(coverage.get("count") or 0) <= 0:
                issues.append({"level": "warning", "message": f"{leg.get('display_name') or leg.get('symbol') or leg.get('instrument_id')} has no local Binance kline coverage."})
        elif source == "polymarket":
            local = coverage.get("local_market_deltas") or {}
            downloaded = coverage.get("downloaded_price_history") or {}
            if int(local.get("count") or 0) <= 0 and int(downloaded.get("count") or 0) <= 0:
                issues.append({"level": "warning", "message": f"{leg.get('display_name') or leg.get('instrument_id')} has no local Polymarket history coverage."})
        else:
            issues.append({"level": "warning", "message": f"{leg.get('display_name') or leg.get('instrument_id')} source is unknown."})

    if strategy:
        strategy_legs = strategy.get("legs") if isinstance(strategy.get("legs"), list) else []
        expected_count = len(strategy_legs)
        if expected_count and expected_count != len(legs):
            issues.append({"level": "warning", "message": f"Strategy expects {expected_count} legs, case has {len(legs)} legs."})
        expected_assets = {
            _leg_asset_class(leg) for leg in strategy_legs
            if _leg_asset_class(leg)
        }
        if expected_assets and asset_classes and not asset_classes.issubset(expected_assets):
            issues.append({"level": "warning", "message": f"Case asset classes {sorted(asset_classes)} differ from strategy legs {sorted(expected_assets)}."})
        strategy_code = str(strategy.get("strategy_code") or "").strip()
        if not strategy_code:
            issues.append({"level": "warning", "message": "Selected strategy has no strategy_code."})

    code_schema = _strategy_code_schema(strategy_code)
    strategy_legs_schema = code_schema.get("legs") if isinstance(code_schema.get("legs"), list) else []
    if strategy_code and strategy_legs_schema:
        if len(strategy_legs_schema) != len(legs):
            issues.append({"level": "error", "message": f"{strategy_code} expects {len(strategy_legs_schema)} legs, case has {len(legs)} legs."})
        for index, expected in enumerate(strategy_legs_schema):
            if index >= len(legs):
                continue
            got = legs[index]
            expected_asset = str(expected.get("asset_class") or "").strip().lower()
            expected_venue = str(expected.get("venue") or "").strip().lower()
            got_asset = _leg_asset_class(got)
            got_venue = str(got.get("venue") or _leg_source(got)).strip().lower()
            if expected_asset and got_asset and expected_asset != got_asset:
                issues.append({"level": "error", "message": f"{strategy_code} leg {index + 1} expects {expected_asset}, got {got_asset}."})
            if expected_venue and got_venue and expected_venue != got_venue:
                issues.append({"level": "error", "message": f"{strategy_code} leg {index + 1} expects venue {expected_venue}, got {got_venue}."})

    if "binance" in sources and "polymarket" in sources:
        issues.append({"level": "warning", "message": "Mixed Binance and Polymarket case. This is useful, but the engine must align timestamps and feature feeds."})

    severity = "ok"
    if any(item["level"] == "error" for item in issues):
        severity = "error"
    elif any(item["level"] == "warning" for item in issues):
        severity = "warning"
    return {
        "severity": severity,
        "summary": "Ready" if severity == "ok" else ("Blocked" if severity == "error" else "Needs review"),
        "issues": issues,
        "sources": sorted(sources),
        "asset_classes": sorted(asset_classes),
    }


def list_backtest_cases() -> List[Dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM backtest_cases ORDER BY updated_at_utc DESC, case_id DESC"
        ).fetchall()
        coverage_cache: Dict[str, Dict[str, Any]] = {}
        return [_decode_case_row(row, coverage_cache=coverage_cache) for row in rows]
    finally:
        conn.close()


def list_backtest_collections() -> List[Dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM backtest_collections ORDER BY updated_at_utc DESC, collection_name"
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["schema"] = _loads_json(item.get("schema_json"), {})
            item.pop("schema_json", None)
            result.append(item)
        case_rows = conn.execute(
            "SELECT collection_name, COUNT(*) AS case_count FROM backtest_cases GROUP BY collection_name"
        ).fetchall()
        counts = {str(row["collection_name"] or "Default"): int(row["case_count"] or 0) for row in case_rows}
        known = {row["collection_name"] for row in result}
        for item in result:
            item["case_count"] = counts.get(item["collection_name"], 0)
        for name, count in counts.items():
            if name not in known:
                result.append({
                    "collection_name": name,
                    "schema": {},
                    "created_at_utc": "",
                    "updated_at_utc": "",
                    "case_count": count,
                })
        return result
    finally:
        conn.close()


def create_backtest_collection(payload: Dict[str, Any]) -> Dict[str, Any]:
    name = str(payload.get("collection_name") or payload.get("name") or "").strip()
    if not name:
        raise ValueError("collection_name is required")
    case_ids = payload.get("case_ids") if isinstance(payload.get("case_ids"), list) else []
    legs = payload.get("legs") if isinstance(payload.get("legs"), list) else []
    schema = payload.get("schema") if isinstance(payload.get("schema"), dict) else None
    ts = _now_iso()
    conn = _connect()
    try:
        cases: List[Dict[str, Any]] = []
        if case_ids:
            placeholders = ",".join("?" for _ in case_ids)
            rows = conn.execute(
                f"SELECT * FROM backtest_cases WHERE case_id IN ({placeholders}) ORDER BY case_id",
                [int(case_id) for case_id in case_ids],
            ).fetchall()
            coverage_cache: Dict[str, Dict[str, Any]] = {}
            cases = [_decode_case_row(row, coverage_cache=coverage_cache) for row in rows]
            if len(cases) != len(set(int(case_id) for case_id in case_ids)):
                raise ValueError("some selected cases were not found")
            if schema is None:
                first = cases[0] if cases else {}
                schema = _case_schema_from_legs(first.get("legs") or [], first.get("data_window") or {})
            for case in cases:
                mismatches = _schema_mismatch(schema, case.get("legs") or [], case.get("data_window") or {})
                if mismatches:
                    raise ValueError(f"case {case.get('case_id')} does not match collection schema: " + "; ".join(mismatches))
        if schema is None:
            schema = _case_schema_from_legs(legs, payload.get("data_window") if isinstance(payload.get("data_window"), dict) else {})
        conn.execute(
            """INSERT INTO backtest_collections(collection_name, schema_json, created_at_utc, updated_at_utc)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(collection_name)
               DO UPDATE SET schema_json = excluded.schema_json, updated_at_utc = excluded.updated_at_utc""",
            (name, json.dumps(schema, ensure_ascii=False, sort_keys=True), ts, ts),
        )
        if case_ids:
            placeholders = ",".join("?" for _ in case_ids)
            conn.execute(
                f"UPDATE backtest_cases SET collection_name = ?, updated_at_utc = ? WHERE case_id IN ({placeholders})",
                [name, ts, *[int(case_id) for case_id in case_ids]],
            )
        conn.commit()
        return next((row for row in list_backtest_collections() if row.get("collection_name") == name), {"collection_name": name, "schema": schema})
    finally:
        conn.close()


def get_backtest_case(case_id: int) -> Dict[str, Any]:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM backtest_cases WHERE case_id = ?", (int(case_id),)).fetchone()
        return _decode_case_row(row)
    finally:
        conn.close()


def create_backtest_case(payload: Dict[str, Any]) -> Dict[str, Any]:
    raw_name = str(payload.get("case_name") or payload.get("name") or "").strip()
    if not raw_name:
        raw_name = f"Case {datetime.now(timezone.utc).strftime('%Y%m%d %H%M%S')}"
    legs = payload.get("legs")
    if not isinstance(legs, list):
        watchlist_ids = payload.get("watchlist_ids")
        if not isinstance(watchlist_ids, list):
            watchlist_ids = []
        legs = []
        if watchlist_ids:
            placeholders = ",".join("?" for _ in watchlist_ids)
            conn_read = _connect()
            try:
                rows = conn_read.execute(
                    f"SELECT * FROM history_watchlist WHERE id IN ({placeholders}) ORDER BY source, symbol, display_name",
                    [int(item_id) for item_id in watchlist_ids],
                ).fetchall()
                legs = [_row_dict(row) for row in rows]
            finally:
                conn_read.close()
    if not legs:
        raise ValueError("backtest case requires at least one leg")

    ts = _now_iso()
    strategy_id = _safe_int(payload.get("strategy_id"), 0) or None
    collection_name = str(payload.get("collection_name") or payload.get("folder") or "").strip()
    if not collection_name:
        collection_name = "Default"
    execution_config = payload.get("execution_config") if isinstance(payload.get("execution_config"), dict) else {}
    strategy = None
    if strategy_id:
        try:
            from services.strategy_registry_service import get_strategy

            strategy = get_strategy(strategy_id)
        except Exception:
            strategy = None
    execution_config = {
        **execution_config,
        "compatibility": _case_compatibility(legs, strategy),
        "strategy_snapshot": strategy or {},
    }
    data_window = payload.get("data_window") if isinstance(payload.get("data_window"), dict) else {}
    conn = _connect()
    try:
        is_default_collection = collection_name.strip().lower() in {"", "default"}
        collection_row = conn.execute(
            "SELECT schema_json FROM backtest_collections WHERE collection_name = ?",
            (collection_name,),
        ).fetchone()
        if collection_row:
            if not is_default_collection:
                collection_schema = _loads_json(collection_row["schema_json"], {})
                mismatches = _schema_mismatch(collection_schema, legs, data_window)
                if mismatches:
                    raise ValueError("case does not match collection schema: " + "; ".join(mismatches))
        else:
            conn.execute(
                """INSERT INTO backtest_collections(collection_name, schema_json, created_at_utc, updated_at_utc)
                   VALUES (?, ?, ?, ?)""",
                (
                    collection_name,
                    json.dumps({} if is_default_collection else _case_schema_from_legs(legs, data_window), ensure_ascii=False, sort_keys=True),
                    ts,
                    ts,
                ),
            )
        cur = conn.execute(
            """INSERT INTO backtest_cases(
                   case_name, collection_name, strategy_id, legs_json, params_json, data_window_json,
                   execution_config_json, status, created_at_utc, updated_at_utc
               ) VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)""",
            (
                raw_name,
                collection_name,
                strategy_id,
                json.dumps(legs, ensure_ascii=False, sort_keys=True),
                json.dumps(payload.get("params") if isinstance(payload.get("params"), dict) else {}, ensure_ascii=False, sort_keys=True),
                json.dumps(data_window, ensure_ascii=False, sort_keys=True),
                json.dumps(execution_config, ensure_ascii=False, sort_keys=True),
                ts,
                ts,
            ),
        )
        conn.commit()
        case_id = cur.lastrowid
        row = conn.execute("SELECT * FROM backtest_cases WHERE case_id = ?", (case_id,)).fetchone()
        return _decode_case_row(row)
    finally:
        conn.close()


def delete_backtest_case(case_id: int) -> bool:
    conn = _connect()
    try:
        affected = conn.execute("DELETE FROM backtest_cases WHERE case_id = ?", (int(case_id),)).rowcount
        conn.commit()
        return affected > 0
    finally:
        conn.close()


def evaluate_backtest_case_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    legs = payload.get("legs")
    if not isinstance(legs, list):
        watchlist_ids = payload.get("watchlist_ids")
        if not isinstance(watchlist_ids, list):
            watchlist_ids = []
        legs = []
        if watchlist_ids:
            placeholders = ",".join("?" for _ in watchlist_ids)
            conn_read = _connect()
            try:
                rows = conn_read.execute(
                    f"SELECT * FROM history_watchlist WHERE id IN ({placeholders}) ORDER BY source, symbol, display_name",
                    [int(item_id) for item_id in watchlist_ids],
                ).fetchall()
                legs = [_row_dict(row) for row in rows]
            finally:
                conn_read.close()
    strategy = None
    strategy_id = _safe_int(payload.get("strategy_id"), 0)
    strategy_code = str(payload.get("strategy_code") or "").strip()
    if strategy_id:
        try:
            from services.strategy_registry_service import get_strategy

            strategy = get_strategy(strategy_id)
        except Exception:
            strategy = None
    return {
        "ok": True,
        "legs_count": len(legs),
        "strategy_id": strategy_id or None,
        "strategy_code": strategy_code,
        "compatibility": _case_compatibility(legs, strategy, strategy_code=strategy_code),
    }


def _decode_run_row(row: sqlite3.Row | None) -> Dict[str, Any]:
    if row is None:
        return {}
    item = dict(row)
    item["case_snapshot"] = _loads_json(item.get("case_snapshot_json"), {})
    item["metrics"] = _loads_json(item.get("metrics_json"), {})
    item.pop("case_snapshot_json", None)
    item.pop("metrics_json", None)
    return item


def _mark_backtest_progress(
    run_id: int,
    percent: float,
    stage: str,
    message: str,
    *,
    event_type: str = "progress",
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    now = _now_iso()
    progress = max(0.0, min(100.0, float(percent)))
    conn = _connect()
    try:
        row = conn.execute("SELECT metrics_json FROM backtest_runs WHERE run_id = ?", (int(run_id),)).fetchone()
        metrics = _loads_json(row["metrics_json"] if row else "{}", {})
        metrics.update({
            "progress_percent": progress,
            "progress_stage": stage,
            "progress_message": message,
            "progress_updated_at": now,
        })
        conn.execute(
            """UPDATE backtest_runs
               SET metrics_json = ?, updated_at_utc = ?
               WHERE run_id = ?""",
            (json.dumps(metrics, ensure_ascii=False, sort_keys=True), now, int(run_id)),
        )
        conn.execute(
            "INSERT INTO backtest_events(run_id, ts_utc, event_type, message, meta_json) VALUES (?, ?, ?, ?, ?)",
            (
                int(run_id),
                now,
                event_type,
                message,
                json.dumps({"stage": stage, "progress_percent": progress, **(meta or {})}, ensure_ascii=False),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _execute_backtest_run_safe(run_id: int) -> None:
    try:
        _execute_backtest_run(int(run_id))
    except Exception as exc:
        message = f"Backtest worker crashed: {type(exc).__name__}: {exc}"
        now = _now_iso()
        conn = _connect()
        try:
            row = conn.execute("SELECT metrics_json FROM backtest_runs WHERE run_id = ?", (int(run_id),)).fetchone()
            metrics = _loads_json(row["metrics_json"] if row else "{}", {})
            metrics.update({
                "progress_percent": 100.0,
                "progress_stage": "failed",
                "progress_message": message,
                "progress_updated_at": now,
            })
            conn.execute(
                """UPDATE backtest_runs
                   SET status = 'failed', error = ?, metrics_json = ?, finished_at_utc = ?, updated_at_utc = ?
                   WHERE run_id = ?""",
                (message, json.dumps(metrics, ensure_ascii=False, sort_keys=True), now, now, int(run_id)),
            )
            conn.execute(
                "INSERT INTO backtest_events(run_id, ts_utc, event_type, message, meta_json) VALUES (?, ?, 'worker_error', ?, '{}')",
                (int(run_id), now, message),
            )
            conn.commit()
        finally:
            conn.close()


def _submit_backtest_run(run_id: int) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE backtest_runs SET status = 'queued', updated_at_utc = ? WHERE run_id = ?",
            (_now_iso(), int(run_id)),
        )
        conn.commit()
    finally:
        conn.close()
    _mark_backtest_progress(run_id, 5, "queued", "Backtest task queued.")
    BACKTEST_EXECUTOR.submit(_execute_backtest_run_safe, int(run_id))


def rerun_backtest_run(run_id: int, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    run = get_backtest_run(run_id)
    if not run:
        raise ValueError("backtest run not found")
    snapshot = dict(run.get("case_snapshot") or {})
    data_window = payload.get("data_window") if isinstance(payload.get("data_window"), dict) else None
    if data_window is not None:
        snapshot["data_window"] = {
            **(snapshot.get("data_window") if isinstance(snapshot.get("data_window"), dict) else {}),
            **data_window,
        }
        snapshot["data_window"]["strict"] = bool(data_window.get("start") or data_window.get("end"))
    if isinstance(payload.get("params"), dict):
        snapshot["run_params"] = {
            **(snapshot.get("run_params") if isinstance(snapshot.get("run_params"), dict) else {}),
            **payload["params"],
        }
    if "auto_download" in payload or "auto_download_missing" in payload:
        snapshot["auto_download_missing"] = bool(payload.get("auto_download") or payload.get("auto_download_missing"))
    if str(payload.get("strategy_code") or "").strip():
        snapshot["run_strategy_code"] = str(payload.get("strategy_code") or "").strip()
    ts = _now_iso()
    conn = _connect()
    try:
        conn.execute(
            """UPDATE backtest_runs
               SET status = 'queued',
                   case_snapshot_json = ?,
                   metrics_json = ?,
                   error = NULL,
                   started_at_utc = ?,
                   finished_at_utc = NULL,
                   updated_at_utc = ?
               WHERE run_id = ?""",
            (
                json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
                json.dumps({
                    "implemented": True,
                    "note": "Backtest run is executing from the edited report window.",
                    "legs_count": len(snapshot.get("legs") if isinstance(snapshot.get("legs"), list) else []),
                    "equity_points": 0,
                    "orders": 0,
                }, ensure_ascii=False, sort_keys=True),
                ts,
                ts,
                int(run_id),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    if str(payload.get("run_mode") or "").strip().lower() == "sync":
        _execute_backtest_run(int(run_id))
    else:
        _submit_backtest_run(int(run_id))
    return get_backtest_run(int(run_id))


def create_backtest_run(case_id: int, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    case = get_backtest_case(case_id)
    if not case:
        raise ValueError("backtest case not found")
    strategy_id = _safe_int(payload.get("strategy_id"), 0) or case.get("strategy_id")
    case_params = case.get("params") if isinstance(case.get("params"), dict) else {}
    payload_params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    params = {**case_params, **payload_params}
    execution_config = case.get("execution_config") if isinstance(case.get("execution_config"), dict) else {}
    strategy_snapshot = execution_config.get("strategy_snapshot") if isinstance(execution_config.get("strategy_snapshot"), dict) else {}
    strategy = None
    if strategy_id:
        try:
            from services.strategy_registry_service import get_strategy

            strategy = get_strategy(int(strategy_id))
        except Exception:
            strategy = None
    strategy_code = str(
        payload.get("strategy_code")
        or (strategy or {}).get("strategy_code")
        or execution_config.get("strategy_code")
        or strategy_snapshot.get("strategy_code")
        or ""
    ).strip()
    batch_id = str(payload.get("batch_id") or "").strip()
    case_snapshot = dict(case)
    case_snapshot["run_strategy_id"] = strategy_id
    case_snapshot["run_strategy_code"] = strategy_code
    case_snapshot["run_params"] = params
    case_snapshot["run_strategy_snapshot"] = strategy or {}
    case_snapshot["run_compatibility"] = _case_compatibility(case.get("legs") or [], strategy, strategy_code=strategy_code)
    case_snapshot["auto_download_missing"] = bool(payload.get("auto_download") or payload.get("auto_download_missing"))
    if batch_id:
        case_snapshot["batch_id"] = batch_id
        case_snapshot["batch_name"] = str(payload.get("batch_name") or "").strip()
    ts = _now_iso()
    status = str(payload.get("status") or "queued").strip() or "queued"
    metrics = {
        "implemented": True,
        "note": "Backtest run is queued and will execute from local historical data.",
        "batch_id": batch_id,
        "batch_name": str(payload.get("batch_name") or "").strip(),
        "legs_count": len(case.get("legs") or []),
        "equity_points": 0,
        "orders": 0,
        "progress_percent": 0.0,
        "progress_stage": "created",
        "progress_message": "Backtest run created.",
    }
    conn = _connect()
    try:
        cur = conn.execute(
            """INSERT INTO backtest_runs(
                   case_id, strategy_id, batch_id, status, case_snapshot_json, metrics_json,
                   error, created_at_utc, started_at_utc, finished_at_utc, updated_at_utc
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)""",
            (
                int(case_id),
                strategy_id,
                batch_id,
                status,
                json.dumps(case_snapshot, ensure_ascii=False, sort_keys=True),
                json.dumps(metrics, ensure_ascii=False, sort_keys=True),
                payload.get("error"),
                ts,
                ts,
            ),
        )
        conn.commit()
        run_id = int(cur.lastrowid)
    finally:
        conn.close()
    if str(payload.get("run_mode") or "").strip().lower() == "sync":
        _execute_backtest_run(run_id)
    else:
        _submit_backtest_run(run_id)
    return get_backtest_run(run_id)


def _window_ms_from_snapshot(snapshot: Dict[str, Any]) -> tuple[Optional[int], Optional[int]]:
    window = snapshot.get("data_window") if isinstance(snapshot.get("data_window"), dict) else {}
    start_ms = _parse_time_ms(window.get("start") or window.get("from") or window.get("history_start"))
    end_ms = _parse_time_ms(window.get("end") or window.get("to") or window.get("history_end"))
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    if end_ms is None or end_ms > now_ms:
        end_ms = now_ms
    if start_ms is None:
        start_ms = end_ms - int(timedelta(days=7).total_seconds() * 1000)
    if start_ms >= end_ms:
        start_ms = end_ms - int(timedelta(days=7).total_seconds() * 1000)
    return start_ms, end_ms


def _read_binance_kline_rows(
    conn: sqlite3.Connection,
    symbol: str,
    interval: str,
    start_ms: Optional[int],
    end_ms: Optional[int],
    limit: int = 500_000,
) -> List[Dict[str, Any]]:
    params: List[Any] = [symbol.upper().strip(), interval]
    where = ["symbol = ?", "interval = ?"]
    if start_ms is not None:
        where.append("open_time_ms >= ?")
        params.append(int(start_ms))
    if end_ms is not None:
        where.append("open_time_ms <= ?")
        params.append(int(end_ms))
    params.append(int(limit))
    rows = conn.execute(
        f"""SELECT open_time_ms, open_time_utc, open, high, low, close, volume
            FROM binance_klines
            WHERE {' AND '.join(where)}
            ORDER BY open_time_ms
            LIMIT ?""",
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def _maybe_download_binance_for_run(
    symbol: str,
    interval: str,
    start_ms: Optional[int],
    end_ms: Optional[int],
    progress_callback: Optional[Any] = None,
) -> Dict[str, Any]:
    if not symbol:
        return {"fetched": 0, "stored": 0, "error": "missing symbol"}
    total_fetched = 0
    total_stored = 0
    pages = 0
    errors: List[str] = []
    try:
        cursor = start_ms
        while cursor is not None and end_ms is not None and cursor <= end_ms and pages < 500:
            result = download_binance_klines({
                "symbol": symbol,
                "interval": interval or DEFAULT_BINANCE_INTERVAL,
                "start": _ms_to_iso(cursor),
                "end": _ms_to_iso(end_ms),
                "limit": 1000,
            })
            pages += 1
            fetched = int(result.get("fetched") or 0)
            stored = int(result.get("stored") or 0)
            total_fetched += fetched
            total_stored += stored
            if progress_callback:
                progress_callback({
                    "pages": pages,
                    "fetched": total_fetched,
                    "stored": total_stored,
                    "batch_from": result.get("batch_from"),
                    "batch_to": result.get("batch_to"),
                })
            if result.get("errors"):
                errors.extend(result.get("errors") or [])
            last_ms = _parse_time_ms(result.get("batch_to"))
            if fetched <= 0 or last_ms is None or last_ms < cursor:
                break
            cursor = last_ms + _interval_ms(interval)
            if fetched < 1000:
                break
        if pages > 0:
            return {
                "ok": True,
                "source": "binance",
                "symbol": symbol,
                "interval": interval or DEFAULT_BINANCE_INTERVAL,
                "fetched": total_fetched,
                "stored": total_stored,
                "pages": pages,
                "errors": errors,
                "coverage": get_binance_coverage(symbol, interval or DEFAULT_BINANCE_INTERVAL),
                "partial": bool(cursor and end_ms and cursor <= end_ms),
            }
        latest = download_binance_klines({
            "symbol": symbol,
            "interval": interval or DEFAULT_BINANCE_INTERVAL,
            "limit": 1000,
        })
        latest["fallback"] = "latest_klines"
        return latest
    except Exception as exc:
        return {"fetched": 0, "stored": 0, "error": f"{type(exc).__name__}: {exc}"}


def _run_strategy_code_once(module: Any, usedata: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    if module is None or not hasattr(module, "run_node"):
        return {"actions": [], "metrics": {}, "print": ["strategy module has no run_node"]}
    inputs = [{"Context": json.dumps(usedata, ensure_ascii=False), "Num": None}]
    schema = getattr(module, "ParamsSchema", {}) if isinstance(getattr(module, "ParamsSchema", {}), dict) else {}
    for name, meta in schema.items():
        default = meta.get("default") if isinstance(meta, dict) else None
        inputs.append({"Context": params.get(name, default), "Num": params.get(name, default)})
    outputs = module.run_node({"Inputs": inputs}) or []
    raw = None
    if outputs and isinstance(outputs[0], dict):
        raw = outputs[0].get("Context")
    try:
        parsed = json.loads(raw or "{}")
        return parsed if isinstance(parsed, dict) else {"actions": [], "metrics": {}, "print": []}
    except Exception as exc:
        return {"actions": [], "metrics": {}, "print": [f"strategy output parse failed: {exc}"]}


def _calculate_backtest_metrics(points: List[Dict[str, Any]], orders: List[Dict[str, Any]], legs_count: int) -> Dict[str, Any]:
    equities = [_finite_float(point.get("equity"), 0.0) for point in points if _finite_float(point.get("equity"), 0.0) > 0]
    initial = equities[0] if equities else 0.0
    final = equities[-1] if equities else 0.0
    total_return = (final / initial - 1.0) if initial > 0 else 0.0
    max_drawdown = 0.0
    peak = equities[0] if equities else 0.0
    for equity in equities:
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = min(max_drawdown, equity / peak - 1.0)
    returns = []
    for idx in range(1, len(equities)):
        prev = equities[idx - 1]
        if prev > 0:
            returns.append(equities[idx] / prev - 1.0)
    sharpe = 0.0
    if len(returns) > 1:
        mean = sum(returns) / len(returns)
        variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
        stdev = math.sqrt(variance)
        sharpe = (mean / stdev) * math.sqrt(len(returns)) if stdev > 0 else 0.0
    return {
        "implemented": True,
        "initial_equity": initial,
        "final_equity": final,
        "total_return": total_return,
        "max_drawdown": max_drawdown,
        "sharpe": sharpe,
        "equity_points": len(points),
        "orders": len(orders),
        "legs_count": legs_count,
        "period_start": points[0]["ts_utc"] if points else None,
        "period_end": points[-1]["ts_utc"] if points else None,
    }


_BACKTEST_TIME_METRIC_KEYS = {"now", "ts", "timestamp", "time"}
_BACKTEST_DERIVED_CATALOG = [
    {
        "key": "backtest_return",
        "label": "回测收益率",
        "kind": "continuous",
        "metric_type": "number",
        "unit": "ratio",
        "panel": "backtest_metrics",
        "meta": {"source": "backtest_derived"},
    },
    {
        "key": "backtest_drawdown",
        "label": "回测回撤",
        "kind": "continuous",
        "metric_type": "number",
        "unit": "ratio",
        "panel": "backtest_metrics",
        "meta": {"source": "backtest_derived"},
    },
    {
        "key": "backtest_position_state",
        "label": "回测仓位状态",
        "kind": "state",
        "metric_type": "text",
        "unit": "",
        "panel": "backtest_states",
        "meta": {"source": "backtest_derived"},
    },
]


def _json_safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe_value(item) for item in value]
    return str(value)


def _strategy_output_metric_payload(output: Dict[str, Any]) -> Dict[str, Any]:
    metrics = output.get("metrics") if isinstance(output, dict) and isinstance(output.get("metrics"), dict) else {}
    metrics_meta = output.get("metrics_meta") if isinstance(output, dict) and isinstance(output.get("metrics_meta"), dict) else {}
    if not metrics and not metrics_meta:
        return {}
    return {
        "strategy_metrics": _json_safe_value(metrics),
        "strategy_metrics_meta": _json_safe_value(metrics_meta),
    }


def _metric_type(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
        return "number"
    if value is None:
        return "null"
    if isinstance(value, str):
        return "text"
    return "json"


def _looks_like_datetime_text(value: Any) -> bool:
    text = str(value or "").strip()
    return len(text) >= 16 and "T" in text and (text.endswith("Z") or "+" in text[10:] or text.count("-") >= 2)


def _is_state_lane_catalog_item(item: Dict[str, Any]) -> bool:
    if str(item.get("kind") or "") != "state" or str(item.get("value_state") or "") != "value":
        return False
    key = str(item.get("key") or "").strip().lower()
    if key in _BACKTEST_TIME_METRIC_KEYS or key.endswith("_until") or key.endswith("_at") or key.endswith("_time"):
        return False
    if str(item.get("metric_type") or "") == "text" and _looks_like_datetime_text(item.get("latest_value")):
        return False
    return True


def _catalog_item_from_metric(
    key: str,
    value: Any,
    meta: Dict[str, Any] | None,
    count: int,
    latest_ts: str,
) -> Dict[str, Any] | None:
    text_key = str(key or "").strip()
    if not text_key:
        return None
    metric_type = _metric_type(value)
    if metric_type in {"null", "json"}:
        return None
    meta = dict(meta or {})
    kind = str(meta.get("kind") or "").strip().lower()
    if not kind:
        kind = "continuous" if metric_type == "number" else "state"
    item = {
        "key": text_key,
        "label": str(meta.get("label") or text_key),
        "kind": kind,
        "metric_type": metric_type,
        "unit": str(meta.get("unit") or ""),
        "panel": str(meta.get("panel") or ("metric_values" if metric_type == "number" else "metric_states")),
        "value_state": "value",
        "latest_value": value,
        "latest_ts": latest_ts,
        "count": int(count or 0),
        "meta": meta,
    }
    if item["kind"] == "state" and not _is_state_lane_catalog_item(item):
        return None
    return item


def get_backtest_metric_catalog(run_id: int, limit: int = 240) -> Dict[str, Any]:
    """Return run-aware metric catalog for the backtest workspace picker."""
    parsed_run_id = _safe_int(run_id)
    if parsed_run_id <= 0:
        return {"items": [], "numeric": [], "state": []}
    conn = _connect()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT ts_utc, equity, meta_json FROM backtest_equity_points WHERE run_id = ? ORDER BY ts_utc",
            (parsed_run_id,),
        ).fetchall()
    finally:
        conn.close()
    count = len(rows)
    if count <= 0:
        return {"items": [], "numeric": [], "state": []}

    initial_equity: float | None = None
    running_peak: float | None = None
    latest_return: float | None = None
    latest_drawdown: float | None = None
    latest_position_state = "Flat"
    metric_counts: Dict[str, int] = {}
    latest_values: Dict[str, Any] = {}
    latest_ts_by_key: Dict[str, str] = {}
    meta_by_key: Dict[str, Dict[str, Any]] = {}

    for row in rows:
        ts = str(row["ts_utc"] or "")
        equity = _safe_float(row["equity"])
        if equity is not None and equity > 0:
            if initial_equity is None:
                initial_equity = equity
            running_peak = equity if running_peak is None else max(running_peak, equity)
            if initial_equity:
                latest_return = equity / initial_equity - 1.0
            if running_peak:
                latest_drawdown = equity / running_peak - 1.0
        try:
            point_meta = json.loads(row["meta_json"] or "{}")
        except Exception:
            point_meta = {}
        point_meta = point_meta if isinstance(point_meta, dict) else {}
        position_ratio = _safe_float(point_meta.get("position_ratio"))
        if position_ratio is not None:
            latest_position_state = "Flat" if abs(position_ratio) < 1e-9 else ("Long" if position_ratio > 0 else "Short")
        strategy_metrics = point_meta.get("strategy_metrics") if isinstance(point_meta.get("strategy_metrics"), dict) else {}
        strategy_meta = point_meta.get("strategy_metrics_meta") if isinstance(point_meta.get("strategy_metrics_meta"), dict) else {}
        for key, value in strategy_metrics.items():
            text_key = str(key or "").strip()
            if not text_key:
                continue
            metric_counts[text_key] = metric_counts.get(text_key, 0) + 1
            item_meta = strategy_meta.get(text_key) if isinstance(strategy_meta.get(text_key), dict) else {}
            if item_meta:
                meta_by_key[text_key] = item_meta
            if value is not None:
                latest_values[text_key] = value
                latest_ts_by_key[text_key] = ts

    latest_ts = str(rows[-1]["ts_utc"] or "")
    items: List[Dict[str, Any]] = []
    derived_values = {
        "backtest_return": latest_return,
        "backtest_drawdown": latest_drawdown,
        "backtest_position_state": latest_position_state,
    }
    for config in _BACKTEST_DERIVED_CATALOG:
        item = dict(config)
        item.update({
            "value_state": "value",
            "latest_value": derived_values.get(str(config.get("key"))),
            "latest_ts": latest_ts,
            "count": count,
        })
        items.append(item)

    for key in sorted(latest_values):
        if len(items) >= limit:
            break
        item = _catalog_item_from_metric(
            key,
            latest_values.get(key),
            meta_by_key.get(key),
            metric_counts.get(key, 0),
            latest_ts_by_key.get(key) or latest_ts,
        )
        if item:
            items.append(item)

    return {
        "items": items,
        "numeric": [item for item in items if item.get("metric_type") == "number" and item.get("value_state") == "value"],
        "state": [item for item in items if _is_state_lane_catalog_item(item)],
    }


def _backtest_leg_id(leg: Dict[str, Any], index: int) -> str:
    return str(leg.get("id") or leg.get("instrument_id") or leg.get("symbol") or f"leg-{index}").strip()


def _resolve_action_leg_index(action: Dict[str, Any], legs: List[Dict[str, Any]]) -> int:
    raw_index = action.get("leg_index", action.get("leg", action.get("target_leg", 0)))
    try:
        return max(0, min(len(legs) - 1, int(float(raw_index))))
    except (TypeError, ValueError):
        pass
    symbol = str(action.get("symbol") or "").strip().upper()
    instrument_id = str(action.get("instrument_id") or "").strip()
    for index, leg in enumerate(legs):
        if symbol and str(leg.get("symbol") or "").strip().upper() == symbol:
            return index
        if instrument_id and str(leg.get("instrument_id") or "").strip() == instrument_id:
            return index
    return 0


def _action_target_position(action: Dict[str, Any], current_position: float) -> Optional[float]:
    action_type = str(action.get("type") or "").strip().upper()
    if action_type not in {"SET_TARGET", "SETPOS", "SET_POSITION", "SET_BINARY_TARGET"}:
        return None
    for key in ("target_position", "target_pct", "pct", "target"):
        if key in action:
            return max(0.0, min(1.0, _finite_float(action.get(key), current_position)))
    return current_position


def _outcome_side(value: Any, default: str = "Yes") -> str:
    text = str(value or default).strip().lower()
    return "No" if text == "no" else "Yes"


def _binary_price(value: Any, default: float = 0.5) -> float:
    number = _finite_float(value, default)
    if number <= 0:
        return 0.001
    if number >= 1:
        return 0.999
    return number


def _polymarket_fee_rate(params: Dict[str, Any]) -> float:
    if "polymarket_fee_rate" in params:
        return max(0.0, _finite_float(params.get("polymarket_fee_rate"), 0.05))
    if "fee_rate" in params:
        return max(0.0, _finite_float(params.get("fee_rate"), 0.05))
    return 0.05


def _polymarket_fee(qty: float, price: float, rate: float) -> float:
    return max(0.0, qty) * max(0.0, rate) * _binary_price(price) * (1.0 - _binary_price(price))


def _read_polymarket_price_rows(
    conn: sqlite3.Connection,
    leg: Dict[str, Any],
    start_ms: Optional[int],
    end_ms: Optional[int],
    limit: int = 500_000,
) -> List[Dict[str, Any]]:
    yes_token = str(leg.get("yes_token") or "").strip()
    no_token = str(leg.get("no_token") or "").strip()
    token_id = str(leg.get("token_id") or yes_token or no_token or "").strip()
    if not token_id:
        return []
    source_side = "No" if no_token and token_id == no_token and token_id != yes_token else "Yes"
    params: List[Any] = [token_id]
    where = ["token_id = ?"]
    if start_ms is not None:
        where.append("ts >= ?")
        params.append(int(start_ms / 1000))
    if end_ms is not None:
        where.append("ts <= ?")
        params.append(int(end_ms / 1000))
    params.append(int(limit))
    rows = conn.execute(
        f"""SELECT token_id, condition_id, ts, ts_utc, price
            FROM polymarket_price_history
            WHERE {' AND '.join(where)}
            ORDER BY ts
            LIMIT ?""",
        params,
    ).fetchall()
    decoded = []
    for row in rows:
        item = dict(row)
        item["price_side"] = source_side
        decoded.append(item)
    return decoded


def _portfolio_equity(cash: float, leg_states: List[Dict[str, Any]], rows: List[Dict[str, Any]]) -> tuple[float, float]:
    exposure = 0.0
    for state, row in zip(leg_states, rows):
        exposure += _finite_float(state.get("qty"), 0.0) * _finite_float(row.get("close"), 0.0)
    return cash + exposure, exposure


def _multi_binance_usedata(
    *,
    snapshot: Dict[str, Any],
    legs: List[Dict[str, Any]],
    leg_states: List[Dict[str, Any]],
    rows: List[Dict[str, Any]],
    cash: float,
    initial_cash: float,
) -> Dict[str, Any]:
    equity, exposure = _portfolio_equity(cash, leg_states, rows)
    first_row = rows[0]
    first_state = leg_states[0]
    use_data: Dict[str, Any] = {
        "SchemaVersion": "2.0",
        "NowTime": first_row.get("open_time_utc"),
        "ts": first_row.get("open_time_utc"),
        "RunMode": "Backtest",
        "StrategyId": snapshot.get("run_strategy_id") or snapshot.get("strategy_id"),
        "StrategyName": snapshot.get("case_name") or "",
        "StrategyBankroll": initial_cash,
        "LegCount": len(legs),
        "Portfolio": {
            "cash": cash,
            "equity": equity,
            "exposure": exposure,
            "initial_cash": initial_cash,
            "pnl": equity - initial_cash,
        },
    }
    instruments = []
    for index, (leg, state, row) in enumerate(zip(legs, leg_states, rows)):
        close = _finite_float(row.get("close"), 0.0)
        qty = _finite_float(state.get("qty"), 0.0)
        entry_price = _finite_float(state.get("entry_price"), close)
        closes = state.get("closes") if isinstance(state.get("closes"), list) else []
        leg_exposure = qty * close
        position_ratio = (leg_exposure / equity) if equity > 0 else 0.0
        symbol = str(leg.get("symbol") or "").strip().upper()
        instrument_id = str(leg.get("instrument_id") or symbol).strip()
        prefix = f"L{index}"
        use_data.update({
            f"{prefix}_LegUid": str(leg.get("id") or ""),
            f"{prefix}_LegKind": str(leg.get("leg_kind") or "external_price_series"),
            f"{prefix}_AssetClass": str(leg.get("asset_class") or "crypto_spot"),
            f"{prefix}_Venue": "binance",
            f"{prefix}_Symbol": symbol,
            f"{prefix}_InstrumentId": instrument_id,
            f"{prefix}_BudgetCap": _finite_float(leg.get("budget_cap"), 0.0),
            f"{prefix}_Open": row.get("open"),
            f"{prefix}_High": row.get("high"),
            f"{prefix}_Low": row.get("low"),
            f"{prefix}_Close": close,
            f"{prefix}_LastPrice": close,
            f"{prefix}_Price": close,
            f"{prefix}_Volume": row.get("volume"),
            f"{prefix}_CloseSeries": closes[-500:],
            f"{prefix}_PositionQty": qty,
            f"{prefix}_PositionAvgPrice": entry_price if qty > 0 else 0.0,
            f"{prefix}_PositionCost": qty * entry_price if qty > 0 else 0.0,
            f"{prefix}_PositionValueBid": leg_exposure,
            f"{prefix}_PositionPct": position_ratio,
            f"{prefix}_EntryPrice": entry_price,
            f"{prefix}_PeakPrice": _finite_float(state.get("peak_price"), close),
            f"{prefix}_DataStatus": "ok",
            f"Price_{symbol}": close,
        })
        instruments.append({
            "leg_index": index,
            "symbol": symbol,
            "instrument_id": instrument_id,
            "asset_class": str(leg.get("asset_class") or "crypto_spot"),
            "venue": "binance",
            "close": close,
            "position_qty": qty,
            "position_pct": position_ratio,
        })
    use_data["Instruments"] = instruments
    # Compatibility aliases for older single-leg crypto strategies.
    use_data.update({
        "close": _finite_float(first_row.get("close"), 0.0),
        "open": first_row.get("open"),
        "high": first_row.get("high"),
        "low": first_row.get("low"),
        "volume": first_row.get("volume"),
        "closes": (first_state.get("closes") if isinstance(first_state.get("closes"), list) else [])[-500:],
        "position": use_data.get("L0_PositionPct", 0.0),
        "entry_price": use_data.get("L0_EntryPrice"),
        "peak_price": use_data.get("L0_PeakPrice"),
        "symbol": str(legs[0].get("symbol") or "").strip().upper(),
        "instrument_id": str(legs[0].get("instrument_id") or legs[0].get("symbol") or "").strip(),
    })
    return use_data


def _execute_multi_binance_backtest(
    run_id: int,
    snapshot: Dict[str, Any],
    strategy_code: str,
    params: Dict[str, Any],
    legs: List[Dict[str, Any]],
    ts: str,
    finish_failed: Any,
) -> None:
    availability = _case_data_availability(legs)
    start_ms, end_ms = _window_ms_from_snapshot(snapshot)
    window = snapshot.get("data_window") if isinstance(snapshot.get("data_window"), dict) else {}
    strict_window = bool(window.get("strict"))
    auto_download_missing = bool(snapshot.get("auto_download_missing"))
    min_points = max(80, _safe_int(params.get("slow_window"), 60) + 5)
    events: List[Dict[str, Any]] = []

    def download_progress(meta: Dict[str, Any]) -> None:
        pages = _safe_int(meta.get("pages"), 0)
        progress = min(45.0, 28.0 + pages * 0.20)
        _mark_backtest_progress(
            run_id,
            progress,
            "downloading_data",
            f"Downloading Binance klines: page {pages}, stored {meta.get('stored', 0)}, fetched {meta.get('fetched', 0)}.",
            meta=meta,
        )

    _mark_backtest_progress(
        run_id,
        20,
        "loading_data",
        f"Loading local klines for {len(legs)} Binance legs.",
        meta={"legs": [{"symbol": leg.get("symbol"), "interval": leg.get("interval") or DEFAULT_BINANCE_INTERVAL} for leg in legs]},
    )
    leg_rows: List[List[Dict[str, Any]]] = []
    conn = _connect()
    try:
        for leg in legs:
            symbol = str(leg.get("symbol") or "").upper().strip()
            interval = str(leg.get("interval") or DEFAULT_BINANCE_INTERVAL).strip() or DEFAULT_BINANCE_INTERVAL
            rows = _read_binance_kline_rows(conn, symbol, interval, start_ms, end_ms)
            if len(rows) < min_points and not strict_window:
                rows = _read_binance_kline_rows(conn, symbol, interval, None, None)
                if rows:
                    events.append({
                        "event_type": "data_window_fallback",
                        "message": f"{symbol} has not enough klines in the requested window; using available local history.",
                        "meta": {
                            "requested_start": _ms_to_iso(start_ms) if start_ms else None,
                            "requested_end": _ms_to_iso(end_ms) if end_ms else None,
                            "available_start": rows[0].get("open_time_utc"),
                            "available_end": rows[-1].get("open_time_utc"),
                            "points": len(rows),
                        },
                    })
            leg_rows.append(rows)
    finally:
        conn.close()

    _mark_backtest_progress(run_id, 25, "checking_data", "Checking multi-leg data coverage and alignment.")
    for index, (leg, rows) in enumerate(zip(legs, leg_rows)):
        symbol = str(leg.get("symbol") or "").upper().strip()
        interval = str(leg.get("interval") or DEFAULT_BINANCE_INTERVAL).strip() or DEFAULT_BINANCE_INTERVAL
        if strict_window and rows:
            first_ms = _safe_int(rows[0].get("open_time_ms"), 0)
            last_ms = _safe_int(rows[-1].get("open_time_ms"), 0)
            needs_more_before = bool(start_ms and first_ms and first_ms > start_ms + _interval_ms(interval))
            needs_more_after = bool(end_ms and last_ms and last_ms < end_ms - _interval_ms(interval))
            if needs_more_before or needs_more_after:
                if auto_download_missing:
                    fetch_start = start_ms if needs_more_before else (last_ms + _interval_ms(interval) if last_ms else start_ms)
                    _mark_backtest_progress(run_id, 30, "downloading_data", f"Downloading strict-window gap for leg {index + 1}: {symbol} {interval}.")
                    download_result = _maybe_download_binance_for_run(symbol, interval, fetch_start, end_ms, download_progress)
                    events.append({
                        "event_type": "data_download",
                        "message": f"Download strict-window gap for {symbol} {interval}: stored={download_result.get('stored', 0)} fetched={download_result.get('fetched', 0)}",
                        "meta": {"leg_index": index, **download_result},
                    })
                    conn = _connect()
                    try:
                        rows = _read_binance_kline_rows(conn, symbol, interval, start_ms, end_ms)
                    finally:
                        conn.close()
                    leg_rows[index] = rows
                else:
                    events.append({
                        "event_type": "data_window_missing_download_required",
                        "message": f"{symbol} has partial local data in the requested strict window.",
                        "meta": {
                            "leg_index": index,
                            "symbol": symbol,
                            "interval": interval,
                            "requested_start": _ms_to_iso(start_ms) if start_ms else None,
                            "requested_end": _ms_to_iso(end_ms) if end_ms else None,
                            "actual_start": rows[0].get("open_time_utc") if rows else None,
                            "actual_end": rows[-1].get("open_time_utc") if rows else None,
                            "download_required": True,
                        },
                    })
                    finish_failed(f"Missing strict-window Binance kline data for leg {index + 1} {symbol} {interval}.", events)
                    return
        if len(rows) < min_points and auto_download_missing:
            _mark_backtest_progress(run_id, 30, "downloading_data", f"Downloading Binance klines for leg {index + 1}: {symbol} {interval}.")
            download_result = _maybe_download_binance_for_run(symbol, interval, start_ms, end_ms, download_progress)
            events.append({
                "event_type": "data_download",
                "message": f"Download Binance klines for {symbol} {interval}: stored={download_result.get('stored', 0)} fetched={download_result.get('fetched', 0)}",
                "meta": {"leg_index": index, **download_result},
            })
            conn = _connect()
            try:
                rows = _read_binance_kline_rows(conn, symbol, interval, start_ms, end_ms)
                if len(rows) < min_points and not strict_window:
                    rows = _read_binance_kline_rows(conn, symbol, interval, None, None)
            finally:
                conn.close()
            leg_rows[index] = rows
        if len(rows) < min_points:
            events.append({
                "event_type": "data_window_missing_download_required",
                "message": f"{symbol} has not enough local klines for multi-leg backtest.",
                "meta": {
                    "leg_index": index,
                    "symbol": symbol,
                    "interval": interval,
                    "points": len(rows),
                    "download_required": True,
                },
            })
            finish_failed(f"Missing Binance kline data for leg {index + 1} {symbol} {interval}.", events)
            return

    row_maps = [{_safe_int(row.get("open_time_ms"), 0): row for row in rows} for rows in leg_rows]
    common_times = sorted(set.intersection(*(set(item.keys()) for item in row_maps)) if row_maps else set())
    common_times = [value for value in common_times if value > 0]
    if len(common_times) < min_points:
        events.append({
            "event_type": "data_window_no_overlap",
            "message": f"Multi-leg Binance rows have only {len(common_times)} aligned timestamps.",
            "meta": {"aligned_points": len(common_times), "min_points": min_points},
        })
        finish_failed("Not enough aligned multi-leg Binance data.", events)
        return

    _mark_backtest_progress(run_id, 50, "data_ready", f"Data ready: {len(common_times)} aligned bars across {len(legs)} legs.")
    module = _load_strategy_code_module(strategy_code)
    if module is None:
        finish_failed(f"StrategyCode file not found or cannot be loaded: {strategy_code}", events)
        return
    _mark_backtest_progress(run_id, 58, "strategy_loaded", f"Loaded StrategyCode: {strategy_code}.")

    initial_cash = max(1.0, _finite_float(params.get("initial_cash"), DEFAULT_BACKTEST_CASH))
    fee_bps = max(0.0, _finite_float(params.get("fee_bps"), DEFAULT_BACKTEST_FEE_BPS))
    fee_rate = fee_bps / 10_000.0
    cash = initial_cash
    leg_states: List[Dict[str, Any]] = [
        {"qty": 0.0, "entry_price": 0.0, "peak_price": 0.0, "closes": []}
        for _ in legs
    ]
    equity_points: List[Dict[str, Any]] = []
    orders: List[Dict[str, Any]] = []

    total_rows = max(1, len(common_times))
    progress_step = max(1, total_rows // 10)
    for idx, open_time_ms in enumerate(common_times):
        rows = [row_map[open_time_ms] for row_map in row_maps]
        if idx == 0 or idx % progress_step == 0:
            percent = 60.0 + min(25.0, (idx / total_rows) * 25.0)
            _mark_backtest_progress(run_id, percent, "running_strategy", f"Running multi-leg strategy: {idx}/{total_rows} aligned bars processed.")
        valid = True
        for state, row in zip(leg_states, rows):
            close = _finite_float(row.get("close"), 0.0)
            if close <= 0:
                valid = False
                break
            closes = state.get("closes") if isinstance(state.get("closes"), list) else []
            closes.append(close)
            state["closes"] = closes[-500:]
            state["peak_price"] = max(_finite_float(state.get("peak_price"), close), close) if _finite_float(state.get("qty"), 0.0) > 0 else close
        if not valid:
            continue

        use_data = _multi_binance_usedata(
            snapshot=snapshot,
            legs=legs,
            leg_states=leg_states,
            rows=rows,
            cash=cash,
            initial_cash=initial_cash,
        )
        output = _run_strategy_code_once(module, use_data, params)
        metric_payload = _strategy_output_metric_payload(output)
        actions = output.get("actions") if isinstance(output.get("actions"), list) else []
        for action in actions:
            if not isinstance(action, dict):
                continue
            leg_index = _resolve_action_leg_index(action, legs)
            row = rows[leg_index]
            state = leg_states[leg_index]
            close = _finite_float(row.get("close"), 0.0)
            if close <= 0:
                continue
            equity_now, _ = _portfolio_equity(cash, leg_states, rows)
            current_exposure = _finite_float(state.get("qty"), 0.0) * close
            current_position = (current_exposure / equity_now) if equity_now > 0 else 0.0
            target = _action_target_position(action, current_position)
            if target is None:
                continue
            target_exposure = equity_now * target
            delta_exposure = target_exposure - current_exposure
            if abs(delta_exposure) < max(1.0, equity_now * 0.0001):
                continue
            side = "BUY" if delta_exposure > 0 else "SELL"
            qty_before = _finite_float(state.get("qty"), 0.0)
            if side == "BUY":
                desired_value = max(0.0, delta_exposure)
                trade_value = min(desired_value, cash / (1.0 + fee_rate)) if fee_rate >= 0 else min(desired_value, cash)
                fee = trade_value * fee_rate
                trade_qty = trade_value / close if close > 0 else 0.0
                if trade_qty <= 0:
                    continue
                cash -= trade_value + fee
                qty_after = qty_before + trade_qty
                prev_cost = qty_before * _finite_float(state.get("entry_price"), close)
                state["qty"] = qty_after
                state["entry_price"] = (prev_cost + trade_value) / qty_after if qty_after > 0 else 0.0
                state["peak_price"] = close
            else:
                trade_qty = min(qty_before, abs(delta_exposure) / close if close > 0 else 0.0)
                if trade_qty <= 0:
                    continue
                trade_value = trade_qty * close
                fee = trade_value * fee_rate
                cash += trade_value - fee
                qty_after = qty_before - trade_qty
                state["qty"] = qty_after if qty_after > 1e-10 else 0.0
                if state["qty"] <= 0:
                    state["entry_price"] = 0.0
                    state["peak_price"] = close
            orders.append({
                "ts_utc": row.get("open_time_utc"),
                "leg_id": _backtest_leg_id(legs[leg_index], leg_index),
                "instrument_id": str(legs[leg_index].get("instrument_id") or legs[leg_index].get("symbol") or ""),
                "side": side,
                "quantity": trade_qty,
                "price": close,
                "fee": fee,
                "status": "filled",
                "reason": str(action.get("reason") or action.get("desc") or "strategy_signal"),
                "meta": {"leg_index": leg_index, "target_position": target, "raw_action": action},
            })

        equity, exposure = _portfolio_equity(cash, leg_states, rows)
        equity_points.append({
            "ts_utc": rows[0].get("open_time_utc"),
            "equity": equity,
            "cash": cash,
            "exposure": exposure,
            "pnl": equity - initial_cash,
            "meta": {
                "engine": "binance_multi_leg",
                "aligned_open_time_ms": open_time_ms,
                **metric_payload,
                "legs": [
                    {
                        "leg_index": index,
                        "symbol": str(leg.get("symbol") or "").upper(),
                        "close": _finite_float(row.get("close"), 0.0),
                        "position_qty": _finite_float(state.get("qty"), 0.0),
                        "position_value": _finite_float(state.get("qty"), 0.0) * _finite_float(row.get("close"), 0.0),
                    }
                    for index, (leg, state, row) in enumerate(zip(legs, leg_states, rows))
                ],
            },
        })

    _mark_backtest_progress(run_id, 88, "calculating_metrics", f"Multi-leg evaluation complete: {len(equity_points)} equity points, {len(orders)} orders.")
    metrics = _calculate_backtest_metrics(equity_points, orders, len(legs))
    metrics.update({
        "engine": "binance_multi_leg",
        "fee_bps": fee_bps,
        "strategy_code": strategy_code,
        "symbols": [str(leg.get("symbol") or "").upper() for leg in legs],
        "requested_start": _ms_to_iso(start_ms) if start_ms else None,
        "requested_end": _ms_to_iso(end_ms) if end_ms else None,
        "strict_window": strict_window,
        "available_start": availability.get("common_start"),
        "available_end": availability.get("common_end"),
        "data_availability": availability,
        "aligned_points": len(common_times),
        "progress_percent": 95.0,
        "progress_stage": "writing_report",
        "progress_message": "Writing multi-leg equity curve, orders, metrics, and events.",
        "progress_updated_at": _now_iso(),
    })
    _mark_backtest_progress(run_id, 95, "writing_report", "Writing multi-leg report data to local database.")
    conn = _connect()
    try:
        conn.execute("DELETE FROM backtest_equity_points WHERE run_id = ?", (run_id,))
        conn.execute("DELETE FROM backtest_orders WHERE run_id = ?", (run_id,))
        for point in equity_points:
            conn.execute(
                """INSERT OR REPLACE INTO backtest_equity_points(run_id, ts_utc, equity, cash, exposure, pnl, meta_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (run_id, point["ts_utc"], point["equity"], point["cash"], point["exposure"], point["pnl"], json.dumps(point["meta"], ensure_ascii=False)),
            )
        for order in orders:
            conn.execute(
                """INSERT INTO backtest_orders(run_id, ts_utc, leg_id, instrument_id, side, quantity, price, fee, status, reason, meta_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    order["ts_utc"],
                    order["leg_id"],
                    order["instrument_id"],
                    order["side"],
                    order["quantity"],
                    order["price"],
                    order["fee"],
                    order["status"],
                    order["reason"],
                    json.dumps(order["meta"], ensure_ascii=False),
                ),
            )
        for event in events:
            conn.execute(
                "INSERT INTO backtest_events(run_id, ts_utc, event_type, message, meta_json) VALUES (?, ?, ?, ?, ?)",
                (run_id, ts, event.get("event_type") or "info", event.get("message") or "", json.dumps(event.get("meta") or {}, ensure_ascii=False)),
            )
        conn.execute(
            """INSERT INTO backtest_events(run_id, ts_utc, event_type, message, meta_json)
               VALUES (?, ?, 'complete', ?, ?)""",
            (run_id, _now_iso(), f"Multi-leg backtest complete: {len(equity_points)} equity points, {len(orders)} orders.", "{}"),
        )
        conn.execute(
            """UPDATE backtest_runs
               SET status = 'completed', metrics_json = ?, error = NULL, started_at_utc = COALESCE(started_at_utc, ?),
                   finished_at_utc = ?, updated_at_utc = ?
               WHERE run_id = ?""",
            (
                json.dumps({
                    **metrics,
                    "progress_percent": 100.0,
                    "progress_stage": "completed",
                    "progress_message": "Backtest completed.",
                    "progress_updated_at": _now_iso(),
                }, ensure_ascii=False, sort_keys=True),
                ts,
                _now_iso(),
                _now_iso(),
                run_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _polymarket_leg_prices(row: Dict[str, Any]) -> Dict[str, float]:
    raw = _binary_price(row.get("price"), 0.5)
    if _outcome_side(row.get("price_side"), "Yes") == "No":
        no = raw
        yes = _binary_price(1.0 - no, 0.5)
    else:
        yes = raw
        no = _binary_price(1.0 - yes, 0.5)
    return {"Yes": yes, "No": no}


def _polymarket_portfolio_equity(
    cash: float,
    leg_states: List[Dict[str, Any]],
    rows: List[Dict[str, Any]],
) -> tuple[float, float]:
    exposure = 0.0
    for state, row in zip(leg_states, rows):
        prices = _polymarket_leg_prices(row)
        exposure += _finite_float(state.get("Yes_qty"), 0.0) * prices["Yes"]
        exposure += _finite_float(state.get("No_qty"), 0.0) * prices["No"]
    return cash + exposure, exposure


def _leg_end_date(leg: Dict[str, Any]) -> str:
    if leg.get("end_date"):
        return str(leg.get("end_date") or "")
    meta = leg.get("meta") if isinstance(leg.get("meta"), dict) else {}
    source_leg = meta.get("source_strategy_leg") if isinstance(meta.get("source_strategy_leg"), dict) else {}
    instrument = source_leg.get("instrument_json") if isinstance(source_leg.get("instrument_json"), dict) else {}
    return str(source_leg.get("end_date") or instrument.get("end_date") or instrument.get("endDate") or "")


def _days_to_end(end_date: str, ts_utc: str) -> float:
    end_ms = _parse_time_ms(end_date)
    now_ms = _parse_time_ms(ts_utc)
    if end_ms is None or now_ms is None:
        return 9999.0
    return max(0.0, (end_ms - now_ms) / 86_400_000.0)


def _polymarket_usedata(
    *,
    snapshot: Dict[str, Any],
    legs: List[Dict[str, Any]],
    leg_states: List[Dict[str, Any]],
    rows: List[Dict[str, Any]],
    cash: float,
    initial_cash: float,
    runtime_state: Dict[str, Any],
    machine_state: str,
) -> Dict[str, Any]:
    equity, exposure = _polymarket_portfolio_equity(cash, leg_states, rows)
    ts_utc = str(rows[0].get("ts_utc") or "")
    use_data: Dict[str, Any] = {
        "SchemaVersion": "2.0",
        "NowTime": ts_utc,
        "ts": ts_utc,
        "RunMode": "Backtest",
        "StrategyId": snapshot.get("run_strategy_id") or snapshot.get("strategy_id"),
        "StrategyName": snapshot.get("case_name") or "",
        "StrategyBankroll": initial_cash,
        "LegCount": len(legs),
        "Params": snapshot.get("run_params") if isinstance(snapshot.get("run_params"), dict) else {},
        "Controls": {},
        "RuntimeState": runtime_state,
        "UserState": {},
        "StrategyState": {"state": machine_state or "auto"},
        "MachineState": machine_state or "auto",
        "State": runtime_state,
        "Portfolio": {
            "cash": cash,
            "equity": equity,
            "exposure": exposure,
            "initial_cash": initial_cash,
            "pnl": equity - initial_cash,
        },
    }
    for index, (leg, state, row) in enumerate(zip(legs, leg_states, rows)):
        prices = _polymarket_leg_prices(row)
        budget_cap = _finite_float(leg.get("budget_cap"), 0.0)
        if budget_cap <= 0:
            budget_cap = initial_cash / max(1, len(legs))
        end_date = _leg_end_date(leg)
        day_to_end = _days_to_end(end_date, ts_utc)
        prefix = f"L{index}"
        use_data.update({
            f"{prefix}_ConditionId": leg.get("condition_id") or row.get("condition_id") or "",
            f"{prefix}_LegUid": str(leg.get("id") or ""),
            f"{prefix}_LegKind": str(leg.get("leg_kind") or "binary_market"),
            f"{prefix}_AssetClass": str(leg.get("asset_class") or "polymarket_binary"),
            f"{prefix}_Venue": "polymarket",
            f"{prefix}_Symbol": str(leg.get("symbol") or ""),
            f"{prefix}_InstrumentId": str(leg.get("instrument_id") or leg.get("condition_id") or ""),
            f"{prefix}_MarketTitle": str(leg.get("display_name") or leg.get("instrument_id") or ""),
            f"{prefix}_MarketStatus": "open",
            f"{prefix}_BudgetCap": budget_cap,
            f"{prefix}_ConfiguredBudgetCap": _finite_float(leg.get("budget_cap"), 0.0),
            f"{prefix}_EndTime": end_date,
            f"{prefix}_DayToEnd": day_to_end,
            f"{prefix}_HourToEnd": day_to_end * 24.0,
        })
        for side in ("Yes", "No"):
            price = prices[side]
            qty = _finite_float(state.get(f"{side}_qty"), 0.0)
            avg = _finite_float(state.get(f"{side}_avg"), 0.0)
            cost = qty * avg
            value = qty * price
            pos_pct = (value / budget_cap) if budget_cap > 0 else 0.0
            use_data.update({
                f"{prefix}_{side}_TokenId": str(leg.get("yes_token" if side == "Yes" else "no_token") or ""),
                f"{prefix}_{side}_AskPrice": price,
                f"{prefix}_{side}_BidPrice": price,
                f"{prefix}_{side}_LastPrice": price,
                f"{prefix}_{side}_BestAskQty": 0.0,
                f"{prefix}_{side}_BestBidQty": 0.0,
                f"{prefix}_{side}_AskLevels": [{"price": price, "qty": 0.0}],
                f"{prefix}_{side}_BidLevels": [{"price": price, "qty": 0.0}],
                f"{prefix}_{side}_PositionQty": qty,
                f"{prefix}_{side}_PositionAvgPrice": avg,
                f"{prefix}_{side}_PositionCost": cost,
                f"{prefix}_{side}_PositionValueBid": value,
                f"{prefix}_{side}_AvailableSellQty": qty,
                f"{prefix}_{side}_OpenBuyQty": 0.0,
                f"{prefix}_{side}_OpenSellQty": 0.0,
                f"{prefix}_{side}_Now_Pos": pos_pct,
                f"{prefix}_{side}_position_pct": pos_pct,
                f"{prefix}_{side}_MaxPos": 1.0,
                f"{prefix}_{side}_PosCap": 1.0,
                f"{prefix}_{side}_DataStatus": "ok",
                f"{prefix}_{side}_LastUpdateAgeSec": 0.0,
            })
            if index == 0:
                use_data.update({
                    f"{side}_now_ask": price,
                    f"{side}_now_bid": price,
                    f"{side}_AskPrice": price,
                    f"{side}_BidPrice": price,
                    f"{side}_now_Qty": qty,
                    f"{side}_Qty": qty,
                    f"{side}_now_avgPrice": avg,
                    f"{side}_AvgPrice": avg,
                    f"{side}_Now_Pos": pos_pct,
                    f"{side}_Pos": pos_pct,
                    f"{side}_AvailableSellQty": qty,
                    f"{side}_MaxPos": 1.0,
                    f"{side}_PosCap": 1.0,
                })
    if legs:
        use_data["Enddate"] = _leg_end_date(legs[0])
        use_data["day_to_end"] = use_data.get("L0_DayToEnd", 9999.0)
        use_data["MarketStatus"] = "open"
    return use_data


def _polymarket_execute_setpos(
    *,
    action: Dict[str, Any],
    leg_index: int,
    side: str,
    target: float,
    rows: List[Dict[str, Any]],
    legs: List[Dict[str, Any]],
    leg_states: List[Dict[str, Any]],
    cash: float,
    initial_cash: float,
    fee_rate: float,
) -> tuple[float, Optional[Dict[str, Any]]]:
    row = rows[leg_index]
    leg = legs[leg_index]
    state = leg_states[leg_index]
    prices = _polymarket_leg_prices(row)
    price = prices[side]
    budget_cap = _finite_float(leg.get("budget_cap"), 0.0)
    if budget_cap <= 0:
        budget_cap = initial_cash / max(1, len(legs))
    qty_key = f"{side}_qty"
    avg_key = f"{side}_avg"
    qty_before = _finite_float(state.get(qty_key), 0.0)
    current_value = qty_before * price
    target_value = budget_cap * max(0.0, min(1.0, target))
    delta_value = target_value - current_value
    if abs(delta_value) < max(0.01, budget_cap * 0.0001):
        return cash, None
    verb = "BUY" if delta_value > 0 else "SELL"
    if verb == "BUY":
        desired_value = max(0.0, delta_value)
        per_unit_fee = fee_rate * price * (1.0 - price)
        max_qty = cash / (price + per_unit_fee) if price + per_unit_fee > 0 else 0.0
        trade_qty = min(desired_value / price if price > 0 else 0.0, max_qty)
        if trade_qty <= 0:
            return cash, None
        trade_value = trade_qty * price
        fee = _polymarket_fee(trade_qty, price, fee_rate)
        cash -= trade_value + fee
        qty_after = qty_before + trade_qty
        prev_cost = qty_before * _finite_float(state.get(avg_key), price)
        state[qty_key] = qty_after
        state[avg_key] = (prev_cost + trade_value) / qty_after if qty_after > 0 else 0.0
    else:
        trade_qty = min(qty_before, abs(delta_value) / price if price > 0 else 0.0)
        if trade_qty <= 0:
            return cash, None
        trade_value = trade_qty * price
        fee = _polymarket_fee(trade_qty, price, fee_rate)
        cash += trade_value - fee
        qty_after = qty_before - trade_qty
        state[qty_key] = qty_after if qty_after > 1e-10 else 0.0
        if state[qty_key] <= 0:
            state[avg_key] = 0.0
    return cash, {
        "ts_utc": row.get("ts_utc"),
        "leg_id": _backtest_leg_id(leg, leg_index),
        "instrument_id": str(leg.get("instrument_id") or leg.get("condition_id") or ""),
        "side": f"{verb}_{side.upper()}",
        "quantity": trade_qty,
        "price": price,
        "fee": fee,
        "status": "filled",
        "reason": str(action.get("reason") or action.get("desc") or "strategy_signal"),
        "meta": {"leg_index": leg_index, "outcome": side, "target_pct": target, "raw_action": action},
    }


def _execute_polymarket_backtest(
    run_id: int,
    snapshot: Dict[str, Any],
    strategy_code: str,
    params: Dict[str, Any],
    legs: List[Dict[str, Any]],
    ts: str,
    finish_failed: Any,
) -> None:
    availability = _case_data_availability(legs)
    start_ms, end_ms = _window_ms_from_snapshot(snapshot)
    window = snapshot.get("data_window") if isinstance(snapshot.get("data_window"), dict) else {}
    strict_window = bool(window.get("strict"))
    min_points = max(20, _safe_int(params.get("min_points"), 20))
    events: List[Dict[str, Any]] = []
    _mark_backtest_progress(run_id, 20, "loading_data", f"Loading local Polymarket price history for {len(legs)} legs.")
    conn = _connect()
    try:
        leg_rows = [_read_polymarket_price_rows(conn, leg, start_ms, end_ms) for leg in legs]
    finally:
        conn.close()

    for index, (leg, rows) in enumerate(zip(legs, leg_rows)):
        token_id = str(leg.get("token_id") or leg.get("yes_token") or "").strip()
        if strict_window and rows:
            first_ms = _safe_int(rows[0].get("ts"), 0) * 1000
            last_ms = _safe_int(rows[-1].get("ts"), 0) * 1000
            if (start_ms and first_ms > start_ms + 60_000) or (end_ms and last_ms < end_ms - 60_000):
                events.append({
                    "event_type": "data_window_partial",
                    "message": f"Polymarket token {token_id} has partial local data in the requested strict window.",
                    "meta": {
                        "leg_index": index,
                        "token_id": token_id,
                        "requested_start": _ms_to_iso(start_ms) if start_ms else None,
                        "requested_end": _ms_to_iso(end_ms) if end_ms else None,
                        "actual_start": rows[0].get("ts_utc") if rows else None,
                        "actual_end": rows[-1].get("ts_utc") if rows else None,
                    },
                })
                finish_failed(f"Missing strict-window Polymarket price data for leg {index + 1}.", events)
                return
        if len(rows) < min_points:
            events.append({
                "event_type": "data_window_missing_download_required",
                "message": f"Polymarket token {token_id} has not enough local price history.",
                "meta": {"leg_index": index, "token_id": token_id, "points": len(rows), "download_required": True},
            })
            finish_failed(f"Missing Polymarket price history for leg {index + 1}.", events)
            return

    row_maps = [{_safe_int(row.get("ts"), 0): row for row in rows} for rows in leg_rows]
    common_times = sorted(set.intersection(*(set(item.keys()) for item in row_maps)) if row_maps else set())
    common_times = [value for value in common_times if value > 0]
    if len(common_times) < min_points:
        events.append({
            "event_type": "data_window_no_overlap",
            "message": f"Polymarket legs have only {len(common_times)} aligned timestamps.",
            "meta": {"aligned_points": len(common_times), "min_points": min_points},
        })
        finish_failed("Not enough aligned Polymarket price data.", events)
        return

    module = _load_strategy_code_module(strategy_code)
    if module is None:
        finish_failed(f"StrategyCode file not found or cannot be loaded: {strategy_code}", events)
        return
    _mark_backtest_progress(run_id, 50, "data_ready", f"Data ready: {len(common_times)} aligned Polymarket points.")
    _mark_backtest_progress(run_id, 58, "strategy_loaded", f"Loaded StrategyCode: {strategy_code}.")

    initial_cash = max(1.0, _finite_float(params.get("initial_cash"), DEFAULT_BACKTEST_CASH))
    fee_rate = _polymarket_fee_rate(params)
    cash = initial_cash
    leg_states = [{"Yes_qty": 0.0, "Yes_avg": 0.0, "No_qty": 0.0, "No_avg": 0.0} for _ in legs]
    runtime_state: Dict[str, Any] = {}
    machine_state = "auto"
    equity_points: List[Dict[str, Any]] = []
    orders: List[Dict[str, Any]] = []
    total_rows = max(1, len(common_times))
    progress_step = max(1, total_rows // 10)
    skipped_action_types: set[str] = set()

    for idx, ts_sec in enumerate(common_times):
        rows = [row_map[ts_sec] for row_map in row_maps]
        if idx == 0 or idx % progress_step == 0:
            percent = 60.0 + min(25.0, (idx / total_rows) * 25.0)
            _mark_backtest_progress(run_id, percent, "running_strategy", f"Running Polymarket replay: {idx}/{total_rows} aligned points processed.")
        use_data = _polymarket_usedata(
            snapshot=snapshot,
            legs=legs,
            leg_states=leg_states,
            rows=rows,
            cash=cash,
            initial_cash=initial_cash,
            runtime_state=runtime_state,
            machine_state=machine_state,
        )
        output = _run_strategy_code_once(module, use_data, params)
        metric_payload = _strategy_output_metric_payload(output)
        actions = output.get("actions") if isinstance(output.get("actions"), list) else []
        for action in actions:
            if not isinstance(action, dict):
                continue
            action_type = str(action.get("type") or "").strip().upper()
            if action_type in {"SETPOS", "SET_TARGET", "SET_POSITION", "SET_BINARY_TARGET"}:
                leg_index = _resolve_action_leg_index(action, legs)
                side = _outcome_side(action.get("side") or action.get("outcome") or action.get("asset_side"), "Yes")
                current_price = _polymarket_leg_prices(rows[leg_index])[side]
                state = leg_states[leg_index]
                budget_cap = _finite_float(legs[leg_index].get("budget_cap"), 0.0) or initial_cash / max(1, len(legs))
                current_value = _finite_float(state.get(f"{side}_qty"), 0.0) * current_price
                current_position = current_value / budget_cap if budget_cap > 0 else 0.0
                target = _action_target_position(action, current_position)
                if target is None:
                    continue
                cash, order = _polymarket_execute_setpos(
                    action=action,
                    leg_index=leg_index,
                    side=side,
                    target=target,
                    rows=rows,
                    legs=legs,
                    leg_states=leg_states,
                    cash=cash,
                    initial_cash=initial_cash,
                    fee_rate=fee_rate,
                )
                if order:
                    orders.append(order)
                continue
            if action_type in {"BUY", "SELL"}:
                leg_index = _resolve_action_leg_index(action, legs)
                side = _outcome_side(action.get("outcome") or action.get("asset_side") or action.get("side"), "Yes")
                qty = max(0.0, _finite_float(action.get("qty") or action.get("quantity"), 0.0))
                if qty <= 0:
                    continue
                prices = _polymarket_leg_prices(rows[leg_index])
                price = prices[side]
                state = leg_states[leg_index]
                qty_key = f"{side}_qty"
                avg_key = f"{side}_avg"
                if action_type == "BUY":
                    fee = _polymarket_fee(qty, price, fee_rate)
                    total_cost = qty * price + fee
                    if total_cost > cash:
                        qty = max(0.0, cash / (price + fee_rate * price * (1 - price)))
                        fee = _polymarket_fee(qty, price, fee_rate)
                        total_cost = qty * price + fee
                    if qty <= 0:
                        continue
                    prev_qty = _finite_float(state.get(qty_key), 0.0)
                    prev_cost = prev_qty * _finite_float(state.get(avg_key), price)
                    cash -= total_cost
                    state[qty_key] = prev_qty + qty
                    state[avg_key] = (prev_cost + qty * price) / state[qty_key] if state[qty_key] > 0 else 0.0
                else:
                    prev_qty = _finite_float(state.get(qty_key), 0.0)
                    qty = min(prev_qty, qty)
                    if qty <= 0:
                        continue
                    fee = _polymarket_fee(qty, price, fee_rate)
                    cash += qty * price - fee
                    state[qty_key] = prev_qty - qty
                    if state[qty_key] <= 1e-10:
                        state[qty_key] = 0.0
                        state[avg_key] = 0.0
                orders.append({
                    "ts_utc": rows[leg_index].get("ts_utc"),
                    "leg_id": _backtest_leg_id(legs[leg_index], leg_index),
                    "instrument_id": str(legs[leg_index].get("instrument_id") or legs[leg_index].get("condition_id") or ""),
                    "side": f"{action_type}_{side.upper()}",
                    "quantity": qty,
                    "price": price,
                    "fee": fee,
                    "status": "filled",
                    "reason": str(action.get("reason") or action.get("desc") or "strategy_signal"),
                    "meta": {"leg_index": leg_index, "outcome": side, "raw_action": action},
                })
                continue
            if action_type and action_type not in skipped_action_types:
                skipped_action_types.add(action_type)
                events.append({
                    "event_type": "unsupported_action",
                    "message": f"Polymarket backtest skipped unsupported action type {action_type}.",
                    "meta": {"action_type": action_type},
                })

        if isinstance(output.get("state_updates"), dict):
            runtime_state.update(output.get("state_updates") or {})
        if isinstance(output.get("machine_state_updates"), dict) and output.get("machine_state_updates", {}).get("state"):
            machine_state = str(output["machine_state_updates"]["state"] or machine_state)

        equity, exposure = _polymarket_portfolio_equity(cash, leg_states, rows)
        equity_points.append({
            "ts_utc": rows[0].get("ts_utc"),
            "equity": equity,
            "cash": cash,
            "exposure": exposure,
            "pnl": equity - initial_cash,
            "meta": {
                "engine": "polymarket_binary_replay",
                "aligned_ts": ts_sec,
                **metric_payload,
                "legs": [
                    {
                        "leg_index": index,
                        "condition_id": leg.get("condition_id") or row.get("condition_id") or "",
                        "yes_price": _polymarket_leg_prices(row)["Yes"],
                        "no_price": _polymarket_leg_prices(row)["No"],
                        "yes_qty": _finite_float(state.get("Yes_qty"), 0.0),
                        "no_qty": _finite_float(state.get("No_qty"), 0.0),
                    }
                    for index, (leg, state, row) in enumerate(zip(legs, leg_states, rows))
                ],
            },
        })

    _mark_backtest_progress(run_id, 88, "calculating_metrics", f"Polymarket replay complete: {len(equity_points)} equity points, {len(orders)} orders.")
    metrics = _calculate_backtest_metrics(equity_points, orders, len(legs))
    metrics.update({
        "engine": "polymarket_binary_replay",
        "fee_rate": fee_rate,
        "strategy_code": strategy_code,
        "requested_start": _ms_to_iso(start_ms) if start_ms else None,
        "requested_end": _ms_to_iso(end_ms) if end_ms else None,
        "strict_window": strict_window,
        "available_start": availability.get("common_start"),
        "available_end": availability.get("common_end"),
        "data_availability": availability,
        "aligned_points": len(common_times),
        "progress_percent": 95.0,
        "progress_stage": "writing_report",
        "progress_message": "Writing Polymarket replay report data.",
        "progress_updated_at": _now_iso(),
    })
    _mark_backtest_progress(run_id, 95, "writing_report", "Writing Polymarket replay report data to local database.")
    conn = _connect()
    try:
        conn.execute("DELETE FROM backtest_equity_points WHERE run_id = ?", (run_id,))
        conn.execute("DELETE FROM backtest_orders WHERE run_id = ?", (run_id,))
        for point in equity_points:
            conn.execute(
                """INSERT OR REPLACE INTO backtest_equity_points(run_id, ts_utc, equity, cash, exposure, pnl, meta_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (run_id, point["ts_utc"], point["equity"], point["cash"], point["exposure"], point["pnl"], json.dumps(point["meta"], ensure_ascii=False)),
            )
        for order in orders:
            conn.execute(
                """INSERT INTO backtest_orders(run_id, ts_utc, leg_id, instrument_id, side, quantity, price, fee, status, reason, meta_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    order["ts_utc"],
                    order["leg_id"],
                    order["instrument_id"],
                    order["side"],
                    order["quantity"],
                    order["price"],
                    order["fee"],
                    order["status"],
                    order["reason"],
                    json.dumps(order["meta"], ensure_ascii=False),
                ),
            )
        for event in events:
            conn.execute(
                "INSERT INTO backtest_events(run_id, ts_utc, event_type, message, meta_json) VALUES (?, ?, ?, ?, ?)",
                (run_id, ts, event.get("event_type") or "info", event.get("message") or "", json.dumps(event.get("meta") or {}, ensure_ascii=False)),
            )
        conn.execute(
            """INSERT INTO backtest_events(run_id, ts_utc, event_type, message, meta_json)
               VALUES (?, ?, 'complete', ?, ?)""",
            (run_id, _now_iso(), f"Polymarket replay complete: {len(equity_points)} equity points, {len(orders)} orders.", "{}"),
        )
        conn.execute(
            """UPDATE backtest_runs
               SET status = 'completed', metrics_json = ?, error = NULL, started_at_utc = COALESCE(started_at_utc, ?),
                   finished_at_utc = ?, updated_at_utc = ?
               WHERE run_id = ?""",
            (
                json.dumps({
                    **metrics,
                    "progress_percent": 100.0,
                    "progress_stage": "completed",
                    "progress_message": "Backtest completed.",
                    "progress_updated_at": _now_iso(),
                }, ensure_ascii=False, sort_keys=True),
                ts,
                _now_iso(),
                _now_iso(),
                run_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _execute_backtest_run(run_id: int) -> None:
    _mark_backtest_progress(run_id, 8, "starting", "Backtest worker started.")
    run = get_backtest_run(run_id)
    if not run:
        return
    snapshot = run.get("case_snapshot") or {}
    strategy_code = str(snapshot.get("run_strategy_code") or "").strip()
    params = snapshot.get("run_params") if isinstance(snapshot.get("run_params"), dict) else {}
    legs = snapshot.get("legs") if isinstance(snapshot.get("legs"), list) else []
    compatibility = snapshot.get("run_compatibility") if isinstance(snapshot.get("run_compatibility"), dict) else {}
    ts = _now_iso()
    conn_clear = _connect()
    try:
        conn_clear.execute("DELETE FROM backtest_equity_points WHERE run_id = ?", (int(run_id),))
        conn_clear.execute("DELETE FROM backtest_orders WHERE run_id = ?", (int(run_id),))
        conn_clear.execute("DELETE FROM backtest_events WHERE run_id = ?", (int(run_id),))
        conn_clear.execute(
            "UPDATE backtest_runs SET status = 'running', error = NULL, updated_at_utc = ? WHERE run_id = ?",
            (ts, int(run_id)),
        )
        conn_clear.commit()
    finally:
        conn_clear.close()
    _mark_backtest_progress(run_id, 10, "initializing", "Cleared previous report data and initialized this run.")

    def finish_failed(message: str, events: Optional[List[Dict[str, Any]]] = None) -> None:
        failed_start_ms, failed_end_ms = _window_ms_from_snapshot(snapshot)
        availability = _case_data_availability(legs)
        conn_fail = _connect()
        try:
            for event in events or []:
                conn_fail.execute(
                    "INSERT INTO backtest_events(run_id, ts_utc, event_type, message, meta_json) VALUES (?, ?, ?, ?, ?)",
                    (run_id, event.get("ts_utc") or ts, event.get("event_type") or "error", event.get("message") or message, json.dumps(event.get("meta") or {}, ensure_ascii=False)),
                )
            conn_fail.execute(
                """UPDATE backtest_runs
                   SET status = 'failed', error = ?, metrics_json = ?, finished_at_utc = ?, updated_at_utc = ?
                   WHERE run_id = ?""",
                (
                    message,
                    json.dumps({
                        "implemented": True,
                        "note": message,
                        "legs_count": len(legs),
                        "equity_points": 0,
                        "orders": 0,
                        "requested_start": _ms_to_iso(failed_start_ms) if failed_start_ms else None,
                        "requested_end": _ms_to_iso(failed_end_ms) if failed_end_ms else None,
                        "strict_window": bool(
                            (snapshot.get("data_window") if isinstance(snapshot.get("data_window"), dict) else {}).get("strict")
                        ),
                        "available_start": availability.get("common_start"),
                        "available_end": availability.get("common_end"),
                        "data_availability": availability,
                        "progress_percent": 100.0,
                        "progress_stage": "failed",
                        "progress_message": message,
                        "progress_updated_at": _now_iso(),
                        "download_required": any(
                            bool((event.get("meta") or {}).get("download_required"))
                            for event in (events or [])
                            if isinstance(event, dict)
                        ),
                        "data_window_note": next(
                            (
                                event.get("message")
                                for event in reversed(events or [])
                                if str(event.get("event_type") or "").startswith("data_window")
                            ),
                            message,
                        ),
                    }, ensure_ascii=False, sort_keys=True),
                    ts,
                    ts,
                    run_id,
                ),
            )
            conn_fail.commit()
        finally:
            conn_fail.close()

    if compatibility.get("severity") == "error":
        issues = compatibility.get("issues") if isinstance(compatibility.get("issues"), list) else []
        finish_failed("Strategy and case are not compatible.", [
            {"event_type": "compatibility", "message": item.get("message") or "compatibility error"}
            for item in issues
        ])
        return
    if not strategy_code:
        finish_failed("No StrategyCode selected.")
        return
    if len(legs) != 1 or _leg_source(legs[0]) != "binance":
        if legs and all(_leg_source(leg) == "binance" for leg in legs):
            _execute_multi_binance_backtest(run_id, snapshot, strategy_code, params, legs, ts, finish_failed)
            return
        if legs and all(_leg_source(leg) == "polymarket" for leg in legs):
            _execute_polymarket_backtest(run_id, snapshot, strategy_code, params, legs, ts, finish_failed)
            return
        finish_failed("Backtest execution currently supports all-Binance or all-Polymarket cases. Mixed-source replay is planned.")
        return

    _mark_backtest_progress(run_id, 15, "validated", "Strategy and case compatibility passed.")
    leg = legs[0]
    availability = _case_data_availability(legs)
    symbol = str(leg.get("symbol") or "").upper().strip()
    interval = str(leg.get("interval") or DEFAULT_BINANCE_INTERVAL).strip() or DEFAULT_BINANCE_INTERVAL
    start_ms, end_ms = _window_ms_from_snapshot(snapshot)
    window = snapshot.get("data_window") if isinstance(snapshot.get("data_window"), dict) else {}
    strict_window = bool(window.get("strict"))
    auto_download_missing = bool(snapshot.get("auto_download_missing"))
    min_points = max(80, _safe_int(params.get("slow_window"), 60) + 5)
    events: List[Dict[str, Any]] = []
    _mark_backtest_progress(
        run_id,
        20,
        "loading_data",
        f"Loading local klines for {symbol} {interval}.",
        meta={"symbol": symbol, "interval": interval, "requested_start": _ms_to_iso(start_ms), "requested_end": _ms_to_iso(end_ms)},
    )
    conn = _connect()
    try:
        rows = _read_binance_kline_rows(conn, symbol, interval, start_ms, end_ms)
    finally:
        conn.close()
    _mark_backtest_progress(run_id, 25, "checking_data", f"Loaded {len(rows)} local kline points; checking coverage.")

    def download_progress(meta: Dict[str, Any]) -> None:
        pages = _safe_int(meta.get("pages"), 0)
        progress = min(45.0, 28.0 + pages * 0.25)
        _mark_backtest_progress(
            run_id,
            progress,
            "downloading_data",
            f"Downloading Binance klines: page {pages}, stored {meta.get('stored', 0)}, fetched {meta.get('fetched', 0)}.",
            meta=meta,
        )

    if strict_window and rows:
        first_ms = _parse_time_ms(rows[0].get("open_time_utc"))
        last_ms = _parse_time_ms(rows[-1].get("open_time_utc"))
        needs_more_before = bool(start_ms and first_ms and first_ms > start_ms + _interval_ms(interval))
        needs_more_after = bool(end_ms and last_ms and last_ms < end_ms - _interval_ms(interval))
        if needs_more_before or needs_more_after:
            if auto_download_missing:
                fetch_start = start_ms if needs_more_before else (last_ms + _interval_ms(interval) if last_ms else start_ms)
                _mark_backtest_progress(run_id, 28, "downloading_data", f"Downloading missing Binance window for {symbol} {interval}.")
                download_result = _maybe_download_binance_for_run(symbol, interval, fetch_start, end_ms, download_progress)
                events.append({
                    "event_type": "data_download",
                    "message": f"Download requested Binance window for {symbol} {interval}: stored={download_result.get('stored', 0)} fetched={download_result.get('fetched', 0)}",
                    "meta": download_result,
                })
                availability = _case_data_availability(legs)
                conn = _connect()
                try:
                    rows = _read_binance_kline_rows(conn, symbol, interval, start_ms, end_ms)
                finally:
                    conn.close()
            else:
                events.append({
                    "event_type": "data_window_missing_download_required",
                    "message": f"{symbol} has partial local data in the requested window. Confirm download in Backtest Report to fill the missing range.",
                    "meta": {
                        "requested_start": _ms_to_iso(start_ms) if start_ms else None,
                        "requested_end": _ms_to_iso(end_ms) if end_ms else None,
                        "actual_start": rows[0].get("open_time_utc") if rows else None,
                        "actual_end": rows[-1].get("open_time_utc") if rows else None,
                        "download_required": True,
                    },
                })
                finish_failed(f"Missing Binance kline data for {symbol} {interval}. Download is required before backtest.", events)
                return
    if len(rows) < min_points:
        if auto_download_missing:
            _mark_backtest_progress(run_id, 30, "downloading_data", f"Downloading Binance klines for {symbol} {interval}.")
            download_result = _maybe_download_binance_for_run(symbol, interval, start_ms, end_ms, download_progress)
            events.append({
                "event_type": "data_download",
                "message": f"Download Binance klines for {symbol} {interval}: stored={download_result.get('stored', 0)} fetched={download_result.get('fetched', 0)}",
                "meta": download_result,
            })
            availability = _case_data_availability(legs)
            conn = _connect()
            try:
                rows = _read_binance_kline_rows(conn, symbol, interval, start_ms, end_ms)
                if len(rows) < min_points and not strict_window:
                    rows = _read_binance_kline_rows(conn, symbol, interval, None, None)
                    if rows:
                        events.append({
                            "event_type": "data_window_fallback",
                            "message": f"{symbol} has no enough klines in the requested window; using available local history instead.",
                            "meta": {
                                "requested_start": _ms_to_iso(start_ms) if start_ms else None,
                                "requested_end": _ms_to_iso(end_ms) if end_ms else None,
                                "available_start": rows[0].get("open_time_utc"),
                                "available_end": rows[-1].get("open_time_utc"),
                                "points": len(rows),
                            },
                        })
            finally:
                conn.close()
        else:
            events.append({
                "event_type": "data_window_missing_download_required",
                "message": f"{symbol} has not enough local klines in the requested window. Confirm download in Backtest Report to fill the range.",
                "meta": {
                    "requested_start": _ms_to_iso(start_ms) if start_ms else None,
                    "requested_end": _ms_to_iso(end_ms) if end_ms else None,
                    "points": len(rows),
                    "download_required": True,
                },
            })
            finish_failed(f"Missing Binance kline data for {symbol} {interval}. Download is required before backtest.", events)
            return
    if len(rows) < min_points:
        if strict_window:
            events.append({
                "event_type": "data_window_empty",
                "message": f"{symbol} has no enough klines in the requested window.",
                "meta": {
                    "requested_start": _ms_to_iso(start_ms) if start_ms else None,
                    "requested_end": _ms_to_iso(end_ms) if end_ms else None,
                    "points": len(rows),
                },
            })
        finish_failed(f"Not enough Binance kline data for {symbol} {interval}: {len(rows)} points.", events)
        return
    _mark_backtest_progress(run_id, 50, "data_ready", f"Data ready: {len(rows)} kline points will be evaluated.")
    if strict_window and rows:
        actual_start_ms = _parse_time_ms(rows[0].get("open_time_utc"))
        actual_end_ms = _parse_time_ms(rows[-1].get("open_time_utc"))
        partial_messages: List[str] = []
        if start_ms and actual_start_ms and actual_start_ms > start_ms + 60_000:
            partial_messages.append(f"data starts at {rows[0].get('open_time_utc')}")
        if end_ms and actual_end_ms and actual_end_ms < end_ms - 60_000:
            partial_messages.append(f"data ends at {rows[-1].get('open_time_utc')}")
        if partial_messages:
            events.append({
                "event_type": "data_window_partial",
                "message": f"{symbol} only has partial data inside the requested window: " + "; ".join(partial_messages),
                "meta": {
                    "requested_start": _ms_to_iso(start_ms) if start_ms else None,
                    "requested_end": _ms_to_iso(end_ms) if end_ms else None,
                    "actual_start": rows[0].get("open_time_utc"),
                    "actual_end": rows[-1].get("open_time_utc"),
                    "points": len(rows),
                },
            })

    module = _load_strategy_code_module(strategy_code)
    if module is None:
        finish_failed(f"StrategyCode file not found or cannot be loaded: {strategy_code}", events)
        return
    _mark_backtest_progress(run_id, 58, "strategy_loaded", f"Loaded StrategyCode: {strategy_code}.")

    initial_cash = max(1.0, _finite_float(params.get("initial_cash"), DEFAULT_BACKTEST_CASH))
    fee_bps = max(0.0, _finite_float(params.get("fee_bps"), DEFAULT_BACKTEST_FEE_BPS))
    cash = initial_cash
    qty = 0.0
    entry_price = 0.0
    peak_price = 0.0
    closes: List[float] = []
    equity_points: List[Dict[str, Any]] = []
    orders: List[Dict[str, Any]] = []

    total_rows = max(1, len(rows))
    progress_step = max(1, total_rows // 10)
    for idx, row in enumerate(rows):
        if idx == 0 or idx % progress_step == 0:
            percent = 60.0 + min(25.0, (idx / total_rows) * 25.0)
            _mark_backtest_progress(run_id, percent, "running_strategy", f"Running strategy: {idx}/{total_rows} bars processed.")
        close = _finite_float(row.get("close"), 0.0)
        if close <= 0:
            continue
        closes.append(close)
        exposure = qty * close
        equity_before = cash + exposure
        current_position = exposure / equity_before if equity_before > 0 else 0.0
        peak_price = max(peak_price or close, close) if qty > 0 else close
        usedata = {
            "ts": row.get("open_time_utc"),
            "close": close,
            "open": row.get("open"),
            "high": row.get("high"),
            "low": row.get("low"),
            "volume": row.get("volume"),
            "closes": closes[-500:],
            "position": current_position,
            "entry_price": entry_price or close,
            "peak_price": peak_price,
            "symbol": symbol,
            "instrument_id": leg.get("instrument_id"),
        }
        output = _run_strategy_code_once(module, usedata, params)
        metric_payload = _strategy_output_metric_payload(output)
        actions = output.get("actions") if isinstance(output.get("actions"), list) else []
        for action in actions:
            if not isinstance(action, dict) or str(action.get("type") or "").upper() != "SET_TARGET":
                continue
            target = max(0.0, min(1.0, _finite_float(action.get("target_position"), current_position)))
            equity_now = cash + qty * close
            target_exposure = equity_now * target
            current_exposure = qty * close
            delta_exposure = target_exposure - current_exposure
            if abs(delta_exposure) < max(1.0, equity_now * 0.0001):
                continue
            side = "BUY" if delta_exposure > 0 else "SELL"
            trade_qty = abs(delta_exposure) / close
            fee = abs(delta_exposure) * fee_bps / 10_000.0
            if side == "BUY":
                spend = min(cash, abs(delta_exposure) + fee)
                trade_value = max(0.0, spend - fee)
                trade_qty = trade_value / close if close > 0 else 0.0
                cash -= trade_value + fee
                qty += trade_qty
                entry_price = close if qty > 0 and current_exposure <= 0 else entry_price
                peak_price = close
            else:
                trade_qty = min(qty, trade_qty)
                trade_value = trade_qty * close
                cash += trade_value - fee
                qty -= trade_qty
                if qty <= 1e-10:
                    qty = 0.0
                    entry_price = 0.0
                    peak_price = close
            orders.append({
                "ts_utc": row.get("open_time_utc"),
                "leg_id": str(leg.get("id") or leg.get("instrument_id") or symbol),
                "instrument_id": str(leg.get("instrument_id") or symbol),
                "side": side,
                "quantity": trade_qty,
                "price": close,
                "fee": fee,
                "status": "filled",
                "reason": str(action.get("reason") or "strategy_signal"),
                "meta": {"target_position": target, "raw_action": action},
            })
        exposure = qty * close
        equity = cash + exposure
        equity_points.append({
            "ts_utc": row.get("open_time_utc"),
            "equity": equity,
            "cash": cash,
            "exposure": exposure,
            "pnl": equity - initial_cash,
            "meta": {
                "close": close,
                "position_qty": qty,
                "position_ratio": (exposure / equity) if equity > 0 else 0.0,
                **metric_payload,
            },
        })

    _mark_backtest_progress(run_id, 88, "calculating_metrics", f"Strategy evaluation complete: {len(equity_points)} equity points, {len(orders)} orders.")
    metrics = _calculate_backtest_metrics(equity_points, orders, len(legs))
    metrics["fee_bps"] = fee_bps
    metrics["strategy_code"] = strategy_code
    metrics["symbol"] = symbol
    metrics["requested_start"] = _ms_to_iso(start_ms) if start_ms else None
    metrics["requested_end"] = _ms_to_iso(end_ms) if end_ms else None
    metrics["strict_window"] = strict_window
    metrics["available_start"] = availability.get("common_start")
    metrics["available_end"] = availability.get("common_end")
    metrics["data_availability"] = availability
    metrics["progress_percent"] = 95.0
    metrics["progress_stage"] = "writing_report"
    metrics["progress_message"] = "Writing equity curve, orders, metrics, and events."
    metrics["progress_updated_at"] = _now_iso()
    if events:
        window_events = [event for event in events if str(event.get("event_type") or "").startswith("data_window")]
        if window_events:
            metrics["data_window_note"] = window_events[-1].get("message")
    _mark_backtest_progress(run_id, 95, "writing_report", "Writing report data to local database.")
    conn = _connect()
    try:
        conn.execute("DELETE FROM backtest_equity_points WHERE run_id = ?", (run_id,))
        conn.execute("DELETE FROM backtest_orders WHERE run_id = ?", (run_id,))
        for point in equity_points:
            conn.execute(
                """INSERT OR REPLACE INTO backtest_equity_points(run_id, ts_utc, equity, cash, exposure, pnl, meta_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (run_id, point["ts_utc"], point["equity"], point["cash"], point["exposure"], point["pnl"], json.dumps(point["meta"], ensure_ascii=False)),
            )
        for order in orders:
            conn.execute(
                """INSERT INTO backtest_orders(run_id, ts_utc, leg_id, instrument_id, side, quantity, price, fee, status, reason, meta_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    order["ts_utc"],
                    order["leg_id"],
                    order["instrument_id"],
                    order["side"],
                    order["quantity"],
                    order["price"],
                    order["fee"],
                    order["status"],
                    order["reason"],
                    json.dumps(order["meta"], ensure_ascii=False),
                ),
            )
        for event in events:
            conn.execute(
                "INSERT INTO backtest_events(run_id, ts_utc, event_type, message, meta_json) VALUES (?, ?, ?, ?, ?)",
                (run_id, ts, event.get("event_type") or "info", event.get("message") or "", json.dumps(event.get("meta") or {}, ensure_ascii=False)),
            )
        conn.execute(
            """INSERT INTO backtest_events(run_id, ts_utc, event_type, message, meta_json)
               VALUES (?, ?, 'complete', ?, ?)""",
            (run_id, _now_iso(), f"Backtest complete: {len(equity_points)} equity points, {len(orders)} orders.", "{}"),
        )
        conn.execute(
            """UPDATE backtest_runs
               SET status = 'completed', metrics_json = ?, error = NULL, started_at_utc = COALESCE(started_at_utc, ?),
                   finished_at_utc = ?, updated_at_utc = ?
               WHERE run_id = ?""",
            (
                json.dumps({
                    **metrics,
                    "progress_percent": 100.0,
                    "progress_stage": "completed",
                    "progress_message": "Backtest completed.",
                    "progress_updated_at": _now_iso(),
                }, ensure_ascii=False, sort_keys=True),
                ts,
                _now_iso(),
                _now_iso(),
                run_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def list_backtest_runs(case_id: Optional[int] = None, batch_id: str = "") -> List[Dict[str, Any]]:
    conn = _connect()
    try:
        batch_id = str(batch_id or "").strip()
        if batch_id:
            rows = conn.execute(
                "SELECT * FROM backtest_runs WHERE batch_id = ? ORDER BY updated_at_utc DESC, run_id DESC",
                (batch_id,),
            ).fetchall()
        elif case_id:
            rows = conn.execute(
                "SELECT * FROM backtest_runs WHERE case_id = ? ORDER BY updated_at_utc DESC, run_id DESC",
                (int(case_id),),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM backtest_runs ORDER BY updated_at_utc DESC, run_id DESC LIMIT 200"
            ).fetchall()
        return [_decode_run_row(row) for row in rows]
    finally:
        conn.close()


def delete_backtest_run(run_id: int) -> bool:
    conn = _connect()
    try:
        affected = conn.execute(
            "DELETE FROM backtest_runs WHERE run_id = ?",
            (int(run_id),),
        ).rowcount
        conn.commit()
        return affected > 0
    finally:
        conn.close()


def delete_backtest_batch(batch_id: str) -> Dict[str, Any]:
    batch_id = str(batch_id or "").strip()
    if not batch_id:
        raise ValueError("batch_id is required")
    conn = _connect()
    try:
        run_rows = conn.execute(
            "SELECT run_id FROM backtest_runs WHERE batch_id = ? ORDER BY run_id",
            (batch_id,),
        ).fetchall()
        run_ids = [int(row["run_id"]) for row in run_rows]
        affected = conn.execute(
            "DELETE FROM backtest_runs WHERE batch_id = ?",
            (batch_id,),
        ).rowcount
        conn.commit()
        return {
            "batch_id": batch_id,
            "deleted_runs": int(affected or 0),
            "run_ids": run_ids,
        }
    finally:
        conn.close()


def rename_backtest_case(case_id: int, name: str) -> Dict[str, Any]:
    new_name = str(name or "").strip()
    if not new_name:
        raise ValueError("case name is required")
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM backtest_cases WHERE case_id = ?", (int(case_id),)).fetchone()
        if not row:
            raise ValueError("backtest case not found")
        now = _now_iso()
        conn.execute(
            "UPDATE backtest_cases SET case_name = ?, updated_at_utc = ? WHERE case_id = ?",
            (new_name, now, int(case_id)),
        )
        run_rows = conn.execute(
            "SELECT run_id, case_snapshot_json, metrics_json FROM backtest_runs WHERE case_id = ?",
            (int(case_id),),
        ).fetchall()
        for run_row in run_rows:
            snapshot = _loads_json(run_row["case_snapshot_json"], {})
            metrics = _loads_json(run_row["metrics_json"], {})
            snapshot["case_name"] = new_name
            if not str(metrics.get("run_name") or "").strip():
                snapshot["run_name"] = new_name
            conn.execute(
                """UPDATE backtest_runs
                   SET case_snapshot_json = ?, metrics_json = ?, updated_at_utc = ?
                   WHERE run_id = ?""",
                (
                    json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
                    json.dumps(metrics, ensure_ascii=False, sort_keys=True),
                    now,
                    int(run_row["run_id"]),
                ),
            )
        conn.commit()
        updated = conn.execute("SELECT * FROM backtest_cases WHERE case_id = ?", (int(case_id),)).fetchone()
        return _decode_case_row(updated)
    finally:
        conn.close()


def rename_backtest_run(run_id: int, name: str) -> Dict[str, Any]:
    new_name = str(name or "").strip()
    if not new_name:
        raise ValueError("run name is required")
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM backtest_runs WHERE run_id = ?", (int(run_id),)).fetchone()
        if not row:
            raise ValueError("backtest run not found")
        snapshot = _loads_json(row["case_snapshot_json"], {})
        metrics = _loads_json(row["metrics_json"], {})
        snapshot["run_name"] = new_name
        metrics["run_name"] = new_name
        now = _now_iso()
        conn.execute(
            """UPDATE backtest_runs
               SET case_snapshot_json = ?, metrics_json = ?, updated_at_utc = ?
               WHERE run_id = ?""",
            (
                json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
                json.dumps(metrics, ensure_ascii=False, sort_keys=True),
                now,
                int(run_id),
            ),
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM backtest_runs WHERE run_id = ?", (int(run_id),)).fetchone()
        return _batch_run_summary(_decode_run_row(updated))
    finally:
        conn.close()


def rename_backtest_batch(batch_id: str, name: str) -> Dict[str, Any]:
    batch_id = str(batch_id or "").strip()
    new_name = str(name or "").strip()
    if not batch_id:
        raise ValueError("batch_id is required")
    if not new_name:
        raise ValueError("batch name is required")
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT run_id, case_snapshot_json, metrics_json FROM backtest_runs WHERE batch_id = ?",
            (batch_id,),
        ).fetchall()
        if not rows:
            raise ValueError("backtest batch not found")
        now = _now_iso()
        for row in rows:
            snapshot = _loads_json(row["case_snapshot_json"], {})
            metrics = _loads_json(row["metrics_json"], {})
            snapshot["batch_name"] = new_name
            metrics["batch_name"] = new_name
            conn.execute(
                """UPDATE backtest_runs
                   SET case_snapshot_json = ?, metrics_json = ?, updated_at_utc = ?
                   WHERE run_id = ?""",
                (
                    json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
                    json.dumps(metrics, ensure_ascii=False, sort_keys=True),
                    now,
                    int(row["run_id"]),
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return get_backtest_batch(batch_id, include_runs=True)


def _workspace_leg_payload_from_backtest_leg(leg: Dict[str, Any], index: int, budget_cap: float) -> Dict[str, Any]:
    source = _leg_source(leg)
    asset_class = str(leg.get("asset_class") or ("crypto_spot" if source == "binance" else "polymarket_binary")).strip()
    venue = str(leg.get("venue") or ("binance" if source == "binance" else "polymarket")).strip()
    instrument_json = leg.get("instrument_json") if isinstance(leg.get("instrument_json"), dict) else {}
    if not instrument_json:
        instrument_json = {
            "source": source,
            "display_name": leg.get("display_name") or leg.get("question") or leg.get("symbol") or leg.get("instrument_id"),
        }
        if source == "binance":
            instrument_json["interval"] = leg.get("interval") or DEFAULT_BINANCE_INTERVAL
    params_json = leg.get("params_json") if isinstance(leg.get("params_json"), dict) else leg.get("params")
    if not isinstance(params_json, dict):
        params_json = {}
    return {
        "leg_index": index,
        "condition_id": str(leg.get("condition_id") or "").strip(),
        "yes_token": leg.get("yes_token") or leg.get("token_id") or leg.get("token"),
        "no_token": leg.get("no_token"),
        "leg_kind": leg.get("leg_kind") or ("crypto_spot" if source == "binance" else "binary_market"),
        "asset_class": asset_class,
        "venue": venue,
        "symbol": str(leg.get("symbol") or "").strip().upper(),
        "instrument_id": str(leg.get("instrument_id") or "").strip(),
        "instrument_json": instrument_json,
        "budget_cap": budget_cap,
        "params_json": params_json,
    }


def find_backtest_run_for_workspace_strategy(strategy_id: int) -> Dict[str, Any]:
    """Return the newest backtest run that owns an imported workspace strategy id."""
    target_id = _safe_int(strategy_id, 0)
    if target_id <= 0:
        return {}
    conn = _connect()
    try:
        rows = conn.execute(
            """SELECT * FROM backtest_runs
               WHERE strategy_id = ?
               ORDER BY COALESCE(NULLIF(updated_at_utc, ''), created_at_utc) DESC, run_id DESC
               LIMIT 20""",
            (target_id,),
        ).fetchall()
        for row in rows:
            run = _decode_run_row(row)
            snapshot = run.get("case_snapshot") if isinstance(run.get("case_snapshot"), dict) else {}
            metrics = run.get("metrics") if isinstance(run.get("metrics"), dict) else {}
            if (
                _safe_int(run.get("strategy_id"), 0) == target_id
                or _safe_int(snapshot.get("run_strategy_id"), 0) == target_id
                or _safe_int(metrics.get("workspace_strategy_id"), 0) == target_id
            ):
                return run

        rows = conn.execute(
            """SELECT * FROM backtest_runs
               ORDER BY COALESCE(NULLIF(updated_at_utc, ''), created_at_utc) DESC, run_id DESC
               LIMIT 300"""
        ).fetchall()
    finally:
        conn.close()

    for row in rows:
        run = _decode_run_row(row)
        snapshot = run.get("case_snapshot") if isinstance(run.get("case_snapshot"), dict) else {}
        metrics = run.get("metrics") if isinstance(run.get("metrics"), dict) else {}
        snapshot_strategy = snapshot.get("run_strategy_snapshot") if isinstance(snapshot.get("run_strategy_snapshot"), dict) else {}
        if (
            _safe_int(snapshot.get("run_strategy_id"), 0) == target_id
            or _safe_int(metrics.get("workspace_strategy_id"), 0) == target_id
            or _safe_int(snapshot_strategy.get("strategy_id"), 0) == target_id
        ):
            return run
    return {}


def get_backtest_workspace_strategy(strategy_id: int) -> Dict[str, Any]:
    """Rehydrate an imported workspace strategy from its saved backtest run snapshot."""
    target_id = _safe_int(strategy_id, 0)
    run = find_backtest_run_for_workspace_strategy(target_id)
    if not run:
        return {}
    snapshot = run.get("case_snapshot") if isinstance(run.get("case_snapshot"), dict) else {}
    metrics = run.get("metrics") if isinstance(run.get("metrics"), dict) else {}
    strategy = snapshot.get("run_strategy_snapshot") if isinstance(snapshot.get("run_strategy_snapshot"), dict) else {}
    if strategy:
        result = dict(strategy)
        result["strategy_id"] = target_id or _safe_int(result.get("strategy_id"), 0)
        if isinstance(result.get("input_json"), dict):
            result["input_json"] = json.dumps(result.get("input_json") or {}, ensure_ascii=False, sort_keys=True)
        if not isinstance(result.get("legs"), list) or not result.get("legs"):
            result["legs"] = [
                _workspace_leg_payload_from_backtest_leg(leg, index, 0.0)
                for index, leg in enumerate(snapshot.get("legs") or [])
                if isinstance(leg, dict)
            ]
        result["_snapshot_source"] = "backtest_history"
        result["_snapshot_run_id"] = run.get("run_id")
        return result

    params = snapshot.get("run_params") if isinstance(snapshot.get("run_params"), dict) else {}
    if not params and isinstance(snapshot.get("params"), dict):
        params = snapshot.get("params") or {}
    initial_cash = _finite_float(
        params.get("initial_cash")
        or metrics.get("initial_cash")
        or metrics.get("initial_capital"),
        DEFAULT_BACKTEST_CASH,
    )
    legs = snapshot.get("legs") if isinstance(snapshot.get("legs"), list) else []
    budget_cap = initial_cash / max(1, len(legs))
    return {
        "strategy_id": target_id,
        "strategy_name": str(snapshot.get("case_name") or f"Backtest run {run.get('run_id')}").strip(),
        "strategy_code": str(snapshot.get("run_strategy_code") or metrics.get("strategy_code") or "").strip(),
        "mode": "Stop",
        "state": "Stop",
        "initial_capital": initial_cash,
        "strategy_bankroll": initial_cash,
        "profit_roll_ratio": 0,
        "realized_profit": 0,
        "input_json": json.dumps(params or {}, ensure_ascii=False, sort_keys=True),
        "created_at_utc": run.get("created_at_utc") or "",
        "updated_at_utc": run.get("updated_at_utc") or "",
        "legs": [
            _workspace_leg_payload_from_backtest_leg(leg, index, budget_cap)
            for index, leg in enumerate(legs)
            if isinstance(leg, dict)
        ],
        "_snapshot_source": "backtest_history",
        "_snapshot_run_id": run.get("run_id"),
    }


def import_backtest_run_to_workspace(run_id: int, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    run_id = int(run_id)
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM backtest_runs WHERE run_id = ?", (run_id,)).fetchone()
        run = _decode_run_row(row)
    finally:
        conn.close()
    if not run:
        raise ValueError("backtest run not found")

    snapshot = run.get("case_snapshot") if isinstance(run.get("case_snapshot"), dict) else {}
    metrics = run.get("metrics") if isinstance(run.get("metrics"), dict) else {}
    requested_strategy_id = _safe_int(payload.get("strategy_id"), 0)
    existing_strategy_id = requested_strategy_id or _safe_int(run.get("strategy_id"), 0) or _safe_int(snapshot.get("run_strategy_id"), 0)
    strategy = None
    created = False
    if existing_strategy_id:
        from services.strategy_registry_service import get_strategy

        strategy = get_strategy(existing_strategy_id)
        if not strategy and requested_strategy_id:
            raise ValueError(f"strategy {requested_strategy_id} not found")

    if not strategy:
        from services.strategy_registry_service import create_strategy

        legs = snapshot.get("legs") if isinstance(snapshot.get("legs"), list) else []
        if not legs:
            raise ValueError("backtest run has no legs to import")
        params = snapshot.get("run_params") if isinstance(snapshot.get("run_params"), dict) else {}
        if not params and isinstance(snapshot.get("params"), dict):
            params = snapshot.get("params") or {}
        strategy_code = str(
            payload.get("strategy_code")
            or snapshot.get("run_strategy_code")
            or metrics.get("strategy_code")
            or ""
        ).strip()
        initial_cash = _finite_float(
            payload.get("strategy_bankroll")
            or params.get("initial_cash")
            or metrics.get("initial_cash")
            or metrics.get("initial_capital"),
            DEFAULT_BACKTEST_CASH,
        )
        initial_cash = max(1.0, initial_cash)
        budget_cap = initial_cash / max(1, len(legs))
        strategy_payload = {
            "strategy_name": str(
                payload.get("strategy_name")
                or f"{snapshot.get('case_name') or f'Backtest run {run_id}'} / run {run_id}"
            ).strip(),
            "strategy_code": strategy_code,
            "mode": "Stop",
            "initial_capital": initial_cash,
            "strategy_bankroll": initial_cash,
            "input_json": params,
            "legs": [
                _workspace_leg_payload_from_backtest_leg(leg, index, budget_cap)
                for index, leg in enumerate(legs)
                if isinstance(leg, dict)
            ],
        }
        strategy = create_strategy(strategy_payload)
        created = True

    strategy_id = _safe_int((strategy or {}).get("strategy_id"), 0)
    if strategy_id <= 0:
        raise ValueError("workspace strategy was not created")
    now = _now_iso()
    snapshot["run_strategy_id"] = strategy_id
    snapshot["run_strategy_snapshot"] = strategy
    snapshot["workspace_imported_at"] = now
    metrics["workspace_strategy_id"] = strategy_id
    metrics["workspace_imported_at"] = now
    conn = _connect()
    try:
        conn.execute(
            """UPDATE backtest_runs
               SET strategy_id = ?, case_snapshot_json = ?, metrics_json = ?, updated_at_utc = ?
               WHERE run_id = ?""",
            (
                strategy_id,
                json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
                json.dumps(metrics, ensure_ascii=False, sort_keys=True),
                now,
                run_id,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM backtest_runs WHERE run_id = ?", (run_id,)).fetchone()
        updated_run = _decode_run_row(row)
    finally:
        conn.close()
    return {
        "run_id": run_id,
        "strategy_id": strategy_id,
        "created": created,
        "strategy": strategy,
        "workspace_url": f"/strategies/{strategy_id}/workspace?source=backtest&run_id={run_id}",
        "run": _batch_run_summary(updated_run),
    }


def get_backtest_run(
    run_id: int,
    equity_limit: int = 5000,
    orders_limit: int = 3000,
    events_limit: int = 500,
) -> Dict[str, Any]:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM backtest_runs WHERE run_id = ?", (int(run_id),)).fetchone()
        run = _decode_run_row(row)
        if not run:
            return {}
        equity_count = int(conn.execute(
            "SELECT COUNT(*) FROM backtest_equity_points WHERE run_id = ?",
            (int(run_id),),
        ).fetchone()[0] or 0)
        equity_limit = max(0, int(equity_limit or 0))
        if equity_limit and equity_count > equity_limit:
            stride = max(1, equity_count // max(1, equity_limit))
            equity_rows = conn.execute(
                """SELECT ts_utc, equity, cash, exposure, pnl, meta_json
                   FROM (
                     SELECT ts_utc, equity, cash, exposure, pnl, meta_json,
                            ROW_NUMBER() OVER (ORDER BY ts_utc) AS rn,
                            COUNT(*) OVER () AS total_rows
                     FROM backtest_equity_points
                     WHERE run_id = ?
                   )
                   WHERE rn = 1 OR rn = total_rows OR ((rn - 1) % ?) = 0
                   ORDER BY ts_utc""",
                (int(run_id), stride),
            ).fetchall()
        else:
            equity_rows = conn.execute(
                "SELECT ts_utc, equity, cash, exposure, pnl, meta_json FROM backtest_equity_points WHERE run_id = ? ORDER BY ts_utc",
                (int(run_id),),
            ).fetchall()
        order_count = int(conn.execute(
            "SELECT COUNT(*) FROM backtest_orders WHERE run_id = ?",
            (int(run_id),),
        ).fetchone()[0] or 0)
        orders_limit = max(0, int(orders_limit or 0))
        order_rows = conn.execute(
            f"""SELECT order_id, ts_utc, leg_id, instrument_id, side, quantity, price, fee, status, reason, meta_json
                FROM backtest_orders WHERE run_id = ?
                ORDER BY ts_utc DESC, order_id DESC
                {"LIMIT ?" if orders_limit else ""}""",
            (int(run_id), orders_limit) if orders_limit else (int(run_id),),
        ).fetchall()
        order_rows = list(reversed(order_rows))
        event_count = int(conn.execute(
            "SELECT COUNT(*) FROM backtest_events WHERE run_id = ?",
            (int(run_id),),
        ).fetchone()[0] or 0)
        events_limit = max(0, int(events_limit or 0))
        event_rows = conn.execute(
            f"""SELECT event_id, ts_utc, event_type, message, meta_json
                FROM backtest_events WHERE run_id = ?
                ORDER BY ts_utc DESC, event_id DESC
                {"LIMIT ?" if events_limit else ""}""",
            (int(run_id), events_limit) if events_limit else (int(run_id),),
        ).fetchall()
        event_rows = list(reversed(event_rows))
        run["equity"] = [
            {**dict(item), "meta": _loads_json(item["meta_json"], {})}
            for item in equity_rows
        ]
        run["orders"] = [
            {**dict(item), "meta": _loads_json(item["meta_json"], {})}
            for item in order_rows
        ]
        run["events"] = [
            {**dict(item), "meta": _loads_json(item["meta_json"], {})}
            for item in event_rows
        ]
        for collection_name in ("equity", "orders", "events"):
            for item in run[collection_name]:
                item.pop("meta_json", None)
        run["display_limits"] = {
            "equity_total": equity_count,
            "equity_returned": len(run["equity"]),
            "equity_sampled": equity_count > len(run["equity"]),
            "orders_total": order_count,
            "orders_returned": len(run["orders"]),
            "orders_truncated": order_count > len(run["orders"]),
            "events_total": event_count,
            "events_returned": len(run["events"]),
            "events_truncated": event_count > len(run["events"]),
        }
        return run
    finally:
        conn.close()


def _batch_run_summary(run: Dict[str, Any]) -> Dict[str, Any]:
    metrics = run.get("metrics") if isinstance(run.get("metrics"), dict) else {}
    snapshot = run.get("case_snapshot") if isinstance(run.get("case_snapshot"), dict) else {}
    return {
        "run_id": run.get("run_id"),
        "case_id": run.get("case_id"),
        "run_name": metrics.get("run_name") or snapshot.get("run_name") or snapshot.get("case_name"),
        "case_name": snapshot.get("case_name"),
        "strategy_id": run.get("strategy_id") or snapshot.get("run_strategy_id"),
        "strategy_code": metrics.get("strategy_code") or snapshot.get("run_strategy_code"),
        "status": run.get("status"),
        "error": run.get("error"),
        "created_at_utc": run.get("created_at_utc"),
        "updated_at_utc": run.get("updated_at_utc"),
        "finished_at_utc": run.get("finished_at_utc"),
        "engine": metrics.get("engine"),
        "total_return": metrics.get("total_return"),
        "max_drawdown": metrics.get("max_drawdown"),
        "sharpe": metrics.get("sharpe"),
        "orders": metrics.get("orders"),
        "equity_points": metrics.get("equity_points"),
        "progress_percent": metrics.get("progress_percent"),
        "progress_stage": metrics.get("progress_stage"),
        "report_url": f"/backtests/{run.get('run_id')}",
        "workspace_url": (
            f"/strategies/{snapshot.get('run_strategy_id') or run.get('strategy_id')}/workspace?source=backtest&run_id={run.get('run_id')}"
            if (snapshot.get("run_strategy_id") or run.get("strategy_id"))
            else None
        ),
    }


def _summarize_backtest_batch(batch_id: str, runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    status_counts: Dict[str, int] = {}
    returns: List[float] = []
    sharpes: List[float] = []
    drawdowns: List[float] = []
    completed = 0
    for run in runs:
        status = str(run.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        metrics = run.get("metrics") if isinstance(run.get("metrics"), dict) else {}
        if status == "completed":
            completed += 1
            if metrics.get("total_return") is not None:
                returns.append(_finite_float(metrics.get("total_return"), 0.0))
            if metrics.get("sharpe") is not None:
                sharpes.append(_finite_float(metrics.get("sharpe"), 0.0))
            if metrics.get("max_drawdown") is not None:
                drawdowns.append(_finite_float(metrics.get("max_drawdown"), 0.0))
    summaries = [_batch_run_summary(run) for run in runs]
    best = max(summaries, key=lambda item: _finite_float(item.get("total_return"), -1e18), default=None)
    worst = min(summaries, key=lambda item: _finite_float(item.get("total_return"), 1e18), default=None)
    return {
        "batch_id": batch_id,
        "run_count": len(runs),
        "completed_count": completed,
        "status_counts": status_counts,
        "avg_total_return": (sum(returns) / len(returns)) if returns else None,
        "best_total_return": max(returns) if returns else None,
        "worst_total_return": min(returns) if returns else None,
        "avg_sharpe": (sum(sharpes) / len(sharpes)) if sharpes else None,
        "worst_max_drawdown": min(drawdowns) if drawdowns else None,
        "best_run": best,
        "worst_run": worst,
        "runs": summaries,
    }


def _select_backtest_cases_for_batch(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_ids = payload.get("case_ids")
    cases: List[Dict[str, Any]] = []
    if isinstance(raw_ids, list) and raw_ids:
        seen: set[int] = set()
        for raw_id in raw_ids:
            case_id = _safe_int(raw_id, 0)
            if case_id <= 0 or case_id in seen:
                continue
            seen.add(case_id)
            case = get_backtest_case(case_id)
            if not case:
                raise ValueError(f"backtest case not found: {case_id}")
            cases.append(case)
        return cases

    collection_name = str(payload.get("collection_name") or payload.get("dataset") or "").strip()
    strategy_id = _safe_int(payload.get("strategy_id"), 0)
    limit = max(1, min(_safe_int(payload.get("limit"), 200), 500))
    if not collection_name and not strategy_id:
        raise ValueError("case_ids, collection_name, or strategy_id is required for batch backtest")
    conn = _connect()
    try:
        where: List[str] = []
        params: List[Any] = []
        if collection_name:
            where.append("collection_name = ?")
            params.append(collection_name)
        if strategy_id:
            where.append("strategy_id = ?")
            params.append(strategy_id)
        params.append(limit)
        rows = conn.execute(
            f"""SELECT * FROM backtest_cases
                WHERE {' AND '.join(where)}
                ORDER BY updated_at_utc DESC, case_id DESC
                LIMIT ?""",
            params,
        ).fetchall()
        coverage_cache: Dict[str, Dict[str, Any]] = {}
        return [_decode_case_row(row, coverage_cache=coverage_cache) for row in rows]
    finally:
        conn.close()


def create_backtest_batch(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    cases = _select_backtest_cases_for_batch(payload)
    if not cases:
        raise ValueError("no backtest cases matched the batch request")
    max_cases = max(1, min(_safe_int(payload.get("max_cases"), 100), 500))
    if len(cases) > max_cases:
        cases = cases[:max_cases]
    batch_id = str(payload.get("batch_id") or "").strip()
    if not batch_id:
        batch_id = f"bt_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
    batch_name = str(payload.get("batch_name") or payload.get("name") or payload.get("collection_name") or batch_id).strip()
    common_params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    params_by_case = payload.get("params_by_case") if isinstance(payload.get("params_by_case"), dict) else {}
    run_mode = str(payload.get("run_mode") or "async").strip().lower()
    if run_mode not in {"sync", "async"}:
        run_mode = "async"
    runs: List[Dict[str, Any]] = []
    for case in cases:
        case_id = int(case.get("case_id") or 0)
        case_params = params_by_case.get(str(case_id)) if isinstance(params_by_case.get(str(case_id)), dict) else {}
        run_payload = {
            "strategy_id": payload.get("strategy_id") or case.get("strategy_id"),
            "strategy_code": payload.get("strategy_code"),
            "params": {**common_params, **case_params},
            "auto_download": bool(payload.get("auto_download") or payload.get("auto_download_missing")),
            "run_mode": run_mode,
            "batch_id": batch_id,
            "batch_name": batch_name,
        }
        runs.append(create_backtest_run(case_id, run_payload))
    return {
        "ok": True,
        "batch_id": batch_id,
        "batch_name": batch_name,
        "run_mode": run_mode,
        "case_count": len(cases),
        "summary": _summarize_backtest_batch(batch_id, runs),
        "runs": [_batch_run_summary(run) for run in runs],
    }


def list_backtest_batches(limit: int = 50) -> List[Dict[str, Any]]:
    limit = max(1, min(int(limit or 50), 200))
    conn = _connect()
    try:
        rows = conn.execute(
            """SELECT * FROM backtest_runs
               WHERE batch_id != ''
               ORDER BY updated_at_utc DESC, run_id DESC
               LIMIT ?""",
            (limit * 20,),
        ).fetchall()
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            run = _decode_run_row(row)
            batch_id = str(run.get("batch_id") or "")
            if not batch_id:
                continue
            grouped.setdefault(batch_id, []).append(run)
        batches = []
        for batch_id, runs in grouped.items():
            summary = _summarize_backtest_batch(batch_id, runs)
            latest = max((str(run.get("updated_at_utc") or "") for run in runs), default="")
            first_metrics = runs[0].get("metrics") if isinstance(runs[0].get("metrics"), dict) else {}
            batches.append({
                "batch_id": batch_id,
                "batch_name": first_metrics.get("batch_name") or batch_id,
                "updated_at_utc": latest,
                "summary": {key: value for key, value in summary.items() if key != "runs"},
            })
        batches.sort(key=lambda item: str(item.get("updated_at_utc") or ""), reverse=True)
        return batches[:limit]
    finally:
        conn.close()


def get_backtest_batch(batch_id: str, include_runs: bool = True) -> Dict[str, Any]:
    batch_id = str(batch_id or "").strip()
    if not batch_id:
        raise ValueError("batch_id is required")
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM backtest_runs WHERE batch_id = ? ORDER BY updated_at_utc DESC, run_id DESC",
            (batch_id,),
        ).fetchall()
        runs = [_decode_run_row(row) for row in rows]
    finally:
        conn.close()
    if not runs:
        return {}
    summary = _summarize_backtest_batch(batch_id, runs)
    if not include_runs:
        summary.pop("runs", None)
    first_metrics = runs[0].get("metrics") if isinstance(runs[0].get("metrics"), dict) else {}
    return {
        "batch_id": batch_id,
        "batch_name": first_metrics.get("batch_name") or batch_id,
        "summary": summary,
        "runs": summary.get("runs", []) if include_runs else [],
    }


def get_binance_coverage(symbol: str, interval: str = DEFAULT_BINANCE_INTERVAL) -> Dict[str, Any]:
    symbol = str(symbol or "").upper().strip()
    interval = str(interval or DEFAULT_BINANCE_INTERVAL).strip()
    if not symbol:
        return {"source": "binance", "status": "missing_symbol", "count": 0}
    conn = _connect()
    try:
        row = conn.execute(
            """SELECT COUNT(*) AS count, MIN(open_time_ms) AS min_ts, MAX(open_time_ms) AS max_ts
               FROM binance_klines WHERE symbol = ? AND interval = ?""",
            (symbol, interval),
        ).fetchone()
        count = int(row["count"] or 0)
        segments: List[Dict[str, Any]] = []
        if count:
            gap_limit = _interval_ms(interval) * 3
            segment_start: Optional[int] = None
            prev_ts: Optional[int] = None
            segment_count = 0
            for ts_row in conn.execute(
                "SELECT open_time_ms FROM binance_klines WHERE symbol = ? AND interval = ? ORDER BY open_time_ms",
                (symbol, interval),
            ):
                current = _safe_int(ts_row["open_time_ms"], 0)
                if current <= 0:
                    continue
                if segment_start is None:
                    segment_start = current
                    segment_count = 1
                elif prev_ts is not None and current - prev_ts > gap_limit:
                    segments.append({
                        "from": _ms_to_iso(segment_start),
                        "to": _ms_to_iso(prev_ts),
                        "count": segment_count,
                    })
                    segment_start = current
                    segment_count = 1
                else:
                    segment_count += 1
                prev_ts = current
            if segment_start is not None and prev_ts is not None:
                segments.append({
                    "from": _ms_to_iso(segment_start),
                    "to": _ms_to_iso(prev_ts),
                    "count": segment_count,
                })
        return {
            "source": "binance",
            "symbol": symbol,
            "interval": interval,
            "count": count,
            "from": _ms_to_iso(row["min_ts"]) if count else None,
            "to": _ms_to_iso(row["max_ts"]) if count else None,
            "segments": segments,
            "db_path": str(HISTORY_DB_PATH),
            "status": "ok" if count else "empty",
        }
    finally:
        conn.close()


def get_polymarket_coverage(condition_id: str = "", token_id: str = "") -> Dict[str, Any]:
    condition_id = str(condition_id or "").strip()
    token_id = str(token_id or "").strip()
    settings = load_web_settings()
    realtime_db = Path(get_market_realtime_db_path(settings)).expanduser()
    local = {"count": 0, "from": None, "to": None, "db_path": str(realtime_db), "status": "missing_db"}
    if realtime_db.exists() and (condition_id or token_id):
        conn_rt = sqlite3.connect(str(realtime_db), timeout=5.0)
        conn_rt.row_factory = sqlite3.Row
        try:
            tables = {str(row[0]) for row in conn_rt.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            if "market_deltas" in tables:
                clauses: List[str] = []
                vals: List[Any] = []
                if token_id:
                    clauses.append("clobTokenId = ?")
                    vals.append(token_id)
                if condition_id:
                    clauses.append("condition_id = ?")
                    vals.append(condition_id)
                where = " OR ".join(clauses)
                row = conn_rt.execute(
                    f"""SELECT COUNT(*) AS count, MIN(timestamp) AS min_ts, MAX(timestamp) AS max_ts
                        FROM market_deltas WHERE {where}""",
                    vals,
                ).fetchone()
                count = int(row["count"] or 0)
                local = {
                    "count": count,
                    "from": row["min_ts"] if count else None,
                    "to": row["max_ts"] if count else None,
                    "db_path": str(realtime_db),
                    "status": "ok" if count else "empty",
                }
        finally:
            conn_rt.close()

    downloaded = {"count": 0, "from": None, "to": None, "db_path": str(HISTORY_DB_PATH), "status": "empty"}
    if token_id:
        conn = _connect()
        try:
            row = conn.execute(
                """SELECT COUNT(*) AS count, MIN(ts) AS min_ts, MAX(ts) AS max_ts
                   FROM polymarket_price_history WHERE token_id = ?""",
                (token_id,),
            ).fetchone()
            count = int(row["count"] or 0)
            downloaded.update(
                {
                    "count": count,
                    "from": _ms_to_iso(int(row["min_ts"] or 0) * 1000) if count else None,
                    "to": _ms_to_iso(int(row["max_ts"] or 0) * 1000) if count else None,
                    "status": "ok" if count else "empty",
                }
            )
        finally:
            conn.close()

    return {
        "source": "polymarket",
        "condition_id": condition_id,
        "token_id": token_id,
        "local_market_deltas": local,
        "downloaded_price_history": downloaded,
        "status": "ok" if local.get("count") or downloaded.get("count") else "empty",
    }


def get_coverage(source: str, **kwargs: Any) -> Dict[str, Any]:
    source = str(source or "").strip().lower()
    if source == "binance":
        return get_binance_coverage(str(kwargs.get("symbol") or ""), str(kwargs.get("interval") or DEFAULT_BINANCE_INTERVAL))
    if source == "polymarket":
        return get_polymarket_coverage(str(kwargs.get("condition_id") or ""), str(kwargs.get("token_id") or ""))
    raise ValueError("source must be binance or polymarket")


def download_binance_klines(payload: Dict[str, Any]) -> Dict[str, Any]:
    symbol = str(payload.get("symbol") or "").upper().strip()
    interval = str(payload.get("interval") or DEFAULT_BINANCE_INTERVAL).strip()
    if not symbol:
        raise ValueError("symbol is required")
    start_ms = _parse_time_ms(payload.get("start") or payload.get("start_time"))
    end_ms = _parse_time_ms(payload.get("end") or payload.get("end_time"))
    limit = max(1, min(_safe_int(payload.get("limit"), 1000), 1000))
    params: Dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": limit}
    if start_ms is not None:
        params["startTime"] = start_ms
    if end_ms is not None:
        params["endTime"] = end_ms

    errors: List[str] = []
    rows: List[List[Any]] = []
    source_url = ""
    for base in BINANCE_BASE_URLS:
        try:
            response = SESSION.get(f"{base}/api/v3/klines", params=params, timeout=get_timeout())
            if response.status_code >= 400:
                raise RuntimeError(f"HTTP {response.status_code}: {response.text[:160]}")
            payload_json = response.json()
            if isinstance(payload_json, list):
                rows = payload_json
            source_url = base
            break
        except Exception as exc:
            errors.append(f"{base}: {exc}")
    if not rows and errors:
        raise RuntimeError("; ".join(errors))

    fetched_at = _now_iso()
    inserted = 0
    batch_times: List[int] = []
    conn = _connect()
    try:
        for item in rows:
            if not isinstance(item, list) or len(item) < 11:
                continue
            open_time = _safe_int(item[0])
            if open_time <= 0:
                continue
            batch_times.append(open_time)
            before = conn.total_changes
            conn.execute(
                """INSERT OR REPLACE INTO binance_klines(
                       symbol, interval, open_time_ms, open_time_utc, open, high, low, close,
                       volume, close_time_ms, quote_volume, trades, taker_buy_base_volume,
                       taker_buy_quote_volume, fetched_at_utc
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    symbol,
                    interval,
                    open_time,
                    _ms_to_iso(open_time) or "",
                    _safe_float(item[1]),
                    _safe_float(item[2]),
                    _safe_float(item[3]),
                    _safe_float(item[4]),
                    _safe_float(item[5]),
                    _safe_int(item[6]),
                    _safe_float(item[7]),
                    _safe_int(item[8]),
                    _safe_float(item[9]),
                    _safe_float(item[10]),
                    fetched_at,
                ),
            )
            if conn.total_changes > before:
                inserted += 1
        conn.commit()
    finally:
        conn.close()

    return {
        "ok": True,
        "source": "binance",
        "symbol": symbol,
        "interval": interval,
        "requested": params,
        "fetched": len(rows),
        "stored": inserted,
        "batch_from": _ms_to_iso(min(batch_times)) if batch_times else None,
        "batch_to": _ms_to_iso(max(batch_times)) if batch_times else None,
        "source_url": source_url,
        "errors": errors,
        "coverage": get_binance_coverage(symbol, interval),
    }


def download_binance_klines_range(payload: Dict[str, Any]) -> Dict[str, Any]:
    symbol = str(payload.get("symbol") or "").upper().strip()
    interval = str(payload.get("interval") or DEFAULT_BINANCE_INTERVAL).strip()
    if not symbol:
        raise ValueError("symbol is required")
    start_ms = _parse_time_ms(payload.get("start") or payload.get("start_time"))
    end_ms = _parse_time_ms(payload.get("end") or payload.get("end_time"))
    if start_ms is None or end_ms is None:
        return download_binance_klines(payload)
    if start_ms >= end_ms:
        raise ValueError("download start must be before end")

    total_fetched = 0
    total_stored = 0
    pages = 0
    errors: List[str] = []
    batch_from: Optional[str] = None
    batch_to: Optional[str] = None
    cursor = start_ms
    max_pages = max(1, min(_safe_int(payload.get("max_pages"), 1000), 5000))
    while cursor <= end_ms and pages < max_pages:
        result = download_binance_klines({
            **payload,
            "symbol": symbol,
            "interval": interval,
            "start": _ms_to_iso(cursor),
            "end": _ms_to_iso(end_ms),
            "limit": 1000,
        })
        pages += 1
        fetched = _safe_int(result.get("fetched"), 0)
        stored = _safe_int(result.get("stored"), 0)
        total_fetched += fetched
        total_stored += stored
        if result.get("errors"):
            errors.extend(result.get("errors") or [])
        batch_from = batch_from or result.get("batch_from")
        batch_to = result.get("batch_to") or batch_to
        last_ms = _parse_time_ms(result.get("batch_to"))
        if fetched <= 0 or last_ms is None or last_ms < cursor:
            break
        cursor = last_ms + _interval_ms(interval)
        if fetched < 1000:
            break

    return {
        "ok": True,
        "source": "binance",
        "symbol": symbol,
        "interval": interval,
        "requested": {
            "symbol": symbol,
            "interval": interval,
            "start": _ms_to_iso(start_ms),
            "end": _ms_to_iso(end_ms),
        },
        "fetched": total_fetched,
        "stored": total_stored,
        "pages": pages,
        "batch_from": batch_from,
        "batch_to": batch_to,
        "partial": bool(cursor <= end_ms),
        "errors": errors,
        "coverage": get_binance_coverage(symbol, interval),
    }


def download_polymarket_price_history(payload: Dict[str, Any]) -> Dict[str, Any]:
    token_id = str(payload.get("token_id") or payload.get("market") or "").strip()
    condition_id = str(payload.get("condition_id") or "").strip()
    if not token_id:
        raise ValueError("token_id is required for Polymarket price history")
    start_ms = _parse_time_ms(payload.get("start") or payload.get("start_time"))
    end_ms = _parse_time_ms(payload.get("end") or payload.get("end_time"))
    interval = str(payload.get("interval") or "max").strip()
    fidelity = str(payload.get("fidelity") or "60").strip()
    params: Dict[str, Any] = {"market": token_id, "interval": interval, "fidelity": fidelity}
    if start_ms is not None:
        params["startTs"] = int(start_ms / 1000)
    if end_ms is not None:
        params["endTs"] = int(end_ms / 1000)

    response = SESSION.get(f"{CLOB_BASE_URL}/prices-history", params=params, timeout=get_timeout())
    if response.status_code >= 400:
        raise RuntimeError(f"Polymarket prices-history HTTP {response.status_code}: {response.text[:180]}")
    data = response.json()
    points = data.get("history") if isinstance(data, dict) else data
    if not isinstance(points, list):
        points = []

    fetched_at = _now_iso()
    stored = 0
    conn = _connect()
    try:
        for point in points:
            if not isinstance(point, dict):
                continue
            ts = _safe_int(point.get("t") or point.get("timestamp"))
            price = _safe_float(point.get("p") or point.get("price"))
            if ts <= 0 or price is None:
                continue
            before = conn.total_changes
            conn.execute(
                """INSERT OR REPLACE INTO polymarket_price_history(
                       token_id, condition_id, ts, ts_utc, price, interval, fetched_at_utc
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (token_id, condition_id, ts, _ms_to_iso(ts * 1000) or "", price, interval, fetched_at),
            )
            if conn.total_changes > before:
                stored += 1
        conn.commit()
    finally:
        conn.close()

    return {
        "ok": True,
        "source": "polymarket",
        "token_id": token_id,
        "condition_id": condition_id,
        "requested": params,
        "fetched": len(points),
        "stored": stored,
        "coverage": get_polymarket_coverage(condition_id=condition_id, token_id=token_id),
    }


def preview_history(source: str, **kwargs: Any) -> Dict[str, Any]:
    source = str(source or "").strip().lower()
    limit = max(1, min(_safe_int(kwargs.get("limit"), 240), 1000))
    conn = _connect()
    try:
        if source == "binance":
            symbol = str(kwargs.get("symbol") or "").upper().strip()
            interval = str(kwargs.get("interval") or DEFAULT_BINANCE_INTERVAL).strip()
            rows = conn.execute(
                """SELECT open_time_utc AS ts, open, high, low, close, volume
                   FROM binance_klines
                   WHERE symbol = ? AND interval = ?
                   ORDER BY open_time_ms DESC LIMIT ?""",
                (symbol, interval, limit),
            ).fetchall()
            points = [_row_dict(row) for row in reversed(rows)]
            return {"ok": True, "source": source, "symbol": symbol, "interval": interval, "points": points}
        if source == "polymarket":
            token_id = str(kwargs.get("token_id") or "").strip()
            rows = conn.execute(
                """SELECT ts_utc AS ts, price
                   FROM polymarket_price_history
                   WHERE token_id = ?
                   ORDER BY ts DESC LIMIT ?""",
                (token_id, limit),
            ).fetchall()
            points = [_row_dict(row) for row in reversed(rows)]
            return {"ok": True, "source": source, "token_id": token_id, "points": points}
    finally:
        conn.close()
    raise ValueError("source must be binance or polymarket")


def health_snapshot() -> Dict[str, Any]:
    conn = _connect()
    try:
        watch_count = conn.execute("SELECT COUNT(*) FROM history_watchlist").fetchone()[0]
        kline_count = conn.execute("SELECT COUNT(*) FROM binance_klines").fetchone()[0]
        poly_count = conn.execute("SELECT COUNT(*) FROM polymarket_price_history").fetchone()[0]
        return {
            "ok": True,
            "db_path": str(HISTORY_DB_PATH),
            "watchlist_count": int(watch_count or 0),
            "binance_kline_count": int(kline_count or 0),
            "polymarket_price_count": int(poly_count or 0),
            "updated_at": _now_iso(),
        }
    finally:
        conn.close()
