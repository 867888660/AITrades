from __future__ import annotations

import json
import importlib.util
import math
import sqlite3
import sys
import time
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
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_backtest_cases_collection ON backtest_cases(collection_name, updated_at_utc DESC)"
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
    strategy_code = str(payload.get("strategy_code") or "").strip()
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    strategy = None
    if strategy_id:
        try:
            from services.strategy_registry_service import get_strategy

            strategy = get_strategy(int(strategy_id))
        except Exception:
            strategy = None
    case_snapshot = dict(case)
    case_snapshot["run_strategy_id"] = strategy_id
    case_snapshot["run_strategy_code"] = strategy_code
    case_snapshot["run_params"] = params
    case_snapshot["run_strategy_snapshot"] = strategy or {}
    case_snapshot["run_compatibility"] = _case_compatibility(case.get("legs") or [], strategy, strategy_code=strategy_code)
    case_snapshot["auto_download_missing"] = bool(payload.get("auto_download") or payload.get("auto_download_missing"))
    ts = _now_iso()
    status = str(payload.get("status") or "queued").strip() or "queued"
    metrics = {
        "implemented": True,
        "note": "Backtest run is queued and will execute from local historical data.",
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
                   case_id, strategy_id, status, case_snapshot_json, metrics_json,
                   error, created_at_utc, started_at_utc, finished_at_utc, updated_at_utc
               ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)""",
            (
                int(case_id),
                strategy_id,
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
        finish_failed("First executable version only supports one Binance leg.")
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


def list_backtest_runs(case_id: Optional[int] = None) -> List[Dict[str, Any]]:
    conn = _connect()
    try:
        if case_id:
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
                   ORDER BY ts_utc
                   LIMIT ?""",
                (int(run_id), stride, int(equity_limit)),
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
