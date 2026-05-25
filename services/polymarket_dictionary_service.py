from __future__ import annotations

import json
import random
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from services.config_loader import (
    get_polymarket_dictionary_db_path,
    load_web_settings,
    load_web_settings_for_ui,
)
from services.http_client import SESSION, get_timeout


GAMMA_API = "https://gamma-api.polymarket.com"
TABLE_NAME = "polyMarket_Dictionary"
PAGE_LIMIT = 100
MAX_LOG_LINES = 80
MAX_CONSECUTIVE_ERRORS = 5
REFRESH_CLOSED_MARKETS = False

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS "polyMarket_Dictionary" (
    "ask" TEXT, "condition_id" TEXT, "question" TEXT, "Translation" TEXT, "now_ask_price" TEXT,
    "now_bid_price" TEXT, "Subject" TEXT, "endDate" TEXT, "rules" TEXT, "days_to_end" TEXT,
    "UseRss" TEXT, "url" TEXT, "option_name" TEXT, "condition_id_ext" TEXT, "token" TEXT,
    "bid" TEXT, "l1_spread_c" TEXT, "band_c_used" TEXT, "depth_ask_1c_usd" TEXT, "depth_bid_1c_usd" TEXT,
    "depth_1c_usd" TEXT, "depth_ask_1c_qty" TEXT, "depth_bid_1c_qty" TEXT, "vwap_ask_1c" TEXT,
    "vwap_bid_1c" TEXT, "n_orders_ask_1c" TEXT, "n_orders_bid_1c" TEXT, "top_concentration_ask_1c" TEXT,
    "top_concentration_bid_1c" TEXT, "roi_if_win" TEXT, "daily_eff_if_win" TEXT, "apr_eff_if_win" TEXT,
    "query_time_beijing" TEXT, "ingested_at" TEXT, "News" TEXT, "llm_is_clearcut" TEXT,
    "llm_prediction_p" TEXT, "llm_explain" TEXT, "suggested_qty" TEXT, "source_file" TEXT,
    "Score" TEXT, "resolutionSource" TEXT, "opp_ask_price" TEXT, "opp_bids_price" TEXT,
    "apr_eff_if_win_num" TEXT, "llm_is_Subject" TEXT, "llm_is_Matching" TEXT, "LLM_Think" TEXT,
    "Research_Debug" TEXT, "IsRightNow" TEXT, "KeyWords" TEXT, "LLM_researh" TEXT,
    "IsRuleInweb" TEXT, "opp_token" TEXT, "black" TEXT, "explain_suggest" TEXT,
    "yes_token" TEXT, "no_token" TEXT, "EventID" TEXT
);
"""

COLS = [
    "ask", "condition_id", "question", "Translation", "now_ask_price",
    "now_bid_price", "Subject", "endDate", "rules", "days_to_end",
    "UseRss", "url", "option_name", "condition_id_ext", "token",
    "bid", "l1_spread_c", "band_c_used", "depth_ask_1c_usd", "depth_bid_1c_usd",
    "depth_1c_usd", "depth_ask_1c_qty", "depth_bid_1c_qty", "vwap_ask_1c",
    "vwap_bid_1c", "n_orders_ask_1c", "n_orders_bid_1c", "top_concentration_ask_1c",
    "top_concentration_bid_1c", "roi_if_win", "daily_eff_if_win", "apr_eff_if_win",
    "query_time_beijing", "ingested_at", "News", "llm_is_clearcut",
    "llm_prediction_p", "llm_explain", "suggested_qty", "source_file",
    "Score", "resolutionSource", "opp_ask_price", "opp_bids_price",
    "apr_eff_if_win_num", "llm_is_Subject", "llm_is_Matching", "LLM_Think",
    "Research_Debug", "IsRightNow", "KeyWords", "LLM_researh",
    "IsRuleInweb", "opp_token", "black", "explain_suggest",
    "yes_token", "no_token", "EventID",
]

ALLOWED_TAGS = {
    "crypto", "elections", "politics", "sports", "companies",
    "economic", "finance", "geopolitical", "tech", "world",
    "climate & science", "public health", "culture", "earnings",
    "regulations", "mentions",
}

_STATE_LOCK = threading.Lock()
_RUNNER: threading.Thread | None = None
_STATE: Dict[str, Any] = {
    "running": False,
    "phase": "idle",
    "message": "等待更新",
    "started_at": "",
    "finished_at": "",
    "last_error": "",
    "last_summary": "",
    "stats": {
        "mode": "",
        "offset": 0,
        "pages_processed": 0,
        "markets_fetched": 0,
        "inserted": 0,
        "skipped_existing": 0,
        "skipped_expired": 0,
        "tags_hit": 0,
        "tags_miss": 0,
        "event_cache_size": 0,
        "db_count": 0,
    },
    "logs": [],
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _display_timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _update_state(**patch: Any) -> None:
    with _STATE_LOCK:
        for key, value in patch.items():
            if key == "stats" and isinstance(value, dict):
                _STATE["stats"].update(value)
            elif key == "logs" and isinstance(value, list):
                _STATE["logs"] = list(value)[-MAX_LOG_LINES:]
            else:
                _STATE[key] = value


def _append_log(text: str) -> None:
    line = f"[{_display_timestamp()}] {text}"
    with _STATE_LOCK:
        logs = list(_STATE.get("logs") or [])
        logs.append(line)
        _STATE["logs"] = logs[-MAX_LOG_LINES:]
        _STATE["message"] = text


def _get_db_path() -> Path:
    return Path(get_polymarket_dictionary_db_path(load_web_settings())).expanduser()


def _get_db_path_for_ui() -> str:
    return str(load_web_settings_for_ui().get("polymarket_dictionary_db_path") or "Data/PolyMarketDictionary.db")


def _delete_expired_and_duplicates(conn: sqlite3.Connection) -> tuple[int, int]:
    cur = conn.cursor()
    current_date = datetime.now(timezone.utc).date().isoformat()

    cur.execute(
        f'''
        DELETE FROM "{TABLE_NAME}"
        WHERE TRIM(COALESCE("endDate", "")) != ""
          AND substr(TRIM("endDate"), 1, 10) GLOB "????-??-??"
          AND substr(TRIM("endDate"), 1, 10) < ?
        ''',
        (current_date,),
    )
    expired_deleted = max(cur.rowcount, 0)

    cur.execute(
        f'''
        DELETE FROM "{TABLE_NAME}"
        WHERE rowid NOT IN (
            SELECT MIN(rowid)
            FROM "{TABLE_NAME}"
            GROUP BY COALESCE(TRIM("condition_id"), "")
        )
        '''
    )
    duplicate_deleted = max(cur.rowcount, 0)
    conn.commit()
    return expired_deleted, duplicate_deleted


def _is_expired_end_date(value: Any) -> bool:
    text = str(value or "").strip()
    if len(text) < 10:
        return False
    date_text = text[:10]
    if len(date_text) != 10 or date_text[4] != "-" or date_text[7] != "-":
        return False
    try:
        return date_text < datetime.now(timezone.utc).date().isoformat()
    except Exception:
        return False


def _ensure_db(path: Path) -> tuple[sqlite3.Connection, int, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0, check_same_thread=False)
    cur = conn.cursor()
    cur.execute(CREATE_TABLE_SQL)
    expired_deleted, duplicate_deleted = _delete_expired_and_duplicates(conn)
    cur.execute(f'CREATE UNIQUE INDEX IF NOT EXISTS idx_cond_id ON "{TABLE_NAME}" ("condition_id")')
    conn.commit()
    return conn, expired_deleted, duplicate_deleted


def _count_rows(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        conn = sqlite3.connect(str(path), timeout=2.0)
        try:
            cur = conn.cursor()
            exists = cur.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
                (TABLE_NAME,),
            ).fetchone()
            if not exists:
                return 0
            row = cur.execute(f'SELECT COUNT(*) FROM "{TABLE_NAME}"').fetchone()
            return int(row[0] or 0) if row else 0
        finally:
            conn.close()
    except sqlite3.Error:
        with _STATE_LOCK:
            return int((_STATE.get("stats") or {}).get("db_count") or 0)


def _file_mtime(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    except OSError:
        return ""


def _normalize_token_value(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        text = text[1:-1].strip()
    return text


def _extract_token_from_dict(token_dict: Dict[str, Any]) -> str:
    if not isinstance(token_dict, dict):
        return ""
    for key in ("tokenId", "id", "value", "token_id"):
        if token_dict.get(key):
            result = _normalize_token_value(token_dict[key])
            if result:
                return result
    return ""


def _extract_yes_no_tokens(stub: Dict[str, Any]) -> tuple[str, str]:
    yes_token = ""
    no_token = ""
    yes_token_dict = stub.get("yesToken")
    no_token_dict = stub.get("noToken")
    if isinstance(yes_token_dict, dict):
        yes_token = _extract_token_from_dict(yes_token_dict)
    if isinstance(no_token_dict, dict):
        no_token = _extract_token_from_dict(no_token_dict)
    if yes_token or no_token:
        return yes_token, no_token

    token_values = stub.get("clobTokenIds")
    if isinstance(token_values, list) and token_values:
        yes_token = _extract_token_from_dict(token_values[0]) if isinstance(token_values[0], dict) else _normalize_token_value(token_values[0])
        if len(token_values) >= 2:
            no_token = _extract_token_from_dict(token_values[1]) if isinstance(token_values[1], dict) else _normalize_token_value(token_values[1])
    elif isinstance(token_values, str):
        text = token_values.strip()
        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list) and parsed:
                    yes_token = _extract_token_from_dict(parsed[0]) if isinstance(parsed[0], dict) else _normalize_token_value(parsed[0])
                    if len(parsed) >= 2:
                        no_token = _extract_token_from_dict(parsed[1]) if isinstance(parsed[1], dict) else _normalize_token_value(parsed[1])
            except json.JSONDecodeError:
                parts = [part.strip() for part in text.strip("[]").split(",")]
                if parts:
                    yes_token = _normalize_token_value(parts[0])
                if len(parts) >= 2:
                    no_token = _normalize_token_value(parts[1])

    for key in ("tokens", "outcomeTokens"):
        values = stub.get(key)
        if isinstance(values, list) and values:
            if not yes_token:
                yes_token = _extract_token_from_dict(values[0]) if isinstance(values[0], dict) else _normalize_token_value(values[0])
            if len(values) >= 2 and not no_token:
                no_token = _extract_token_from_dict(values[1]) if isinstance(values[1], dict) else _normalize_token_value(values[1])
            if yes_token and no_token:
                break
    return yes_token, no_token


def _extract_event_id_from_stub(stub: Dict[str, Any]) -> str:
    for key in ("event_id", "eventId", "eventID", "EventID"):
        value = stub.get(key)
        if value:
            return str(value).strip()
    events = stub.get("events")
    if isinstance(events, list) and events:
        first = events[0]
        if isinstance(first, dict) and first.get("id"):
            return str(first["id"]).strip()
        if isinstance(first, str):
            return first.strip()
    return ""


def _is_allowed_tag(label: str, slug: str) -> bool:
    label_lower = label.lower()
    slug_lower = slug.lower()
    for allowed in ALLOWED_TAGS:
        if allowed == label_lower or allowed == slug_lower:
            return True
        if allowed.replace(" & ", "-").replace(" ", "-") == slug_lower:
            return True
    return False


def _tags_to_subject(tags: List[Dict[str, Any]]) -> str:
    if not tags:
        return ""
    labels: List[str] = []
    fallback_labels: List[str] = []
    for tag in tags:
        if isinstance(tag, dict):
            label = str(tag.get("label") or tag.get("name") or "").strip()
            slug = str(tag.get("slug") or "").strip()
            raw_label = label or slug
            if raw_label:
                fallback_labels.append(raw_label)
            if _is_allowed_tag(label, slug):
                labels.append(raw_label)
        elif isinstance(tag, str) and tag.strip():
            raw_label = tag.strip()
            fallback_labels.append(raw_label)
            if raw_label.lower() in ALLOWED_TAGS:
                labels.append(raw_label)
    return ",".join(labels if labels else fallback_labels)


def _backoff_sleep(attempt: int) -> None:
    time.sleep((2 ** attempt) * 0.3 + random.random() * 0.5)


def _robust_get(url: str, params: Dict[str, Any] | None = None, retry: int = 3, timeout: float | None = None):
    last_error: Exception | None = None
    timeout_value = timeout or get_timeout()
    for attempt in range(retry + 1):
        try:
            response = SESSION.get(url, params=params, timeout=timeout_value)
            if response.status_code == 200:
                return response
            last_error = RuntimeError(f"HTTP {response.status_code} for {url}")
        except Exception as exc:  # pragma: no cover - network path
            last_error = exc
        if attempt < retry:
            _backoff_sleep(attempt)
    if last_error is None:
        raise RuntimeError(f"Unknown request error for {url}")
    raise last_error


def _fetch_market_tags(market_id: str) -> List[Dict[str, Any]]:
    try:
        response = _robust_get(f"{GAMMA_API}/markets/{market_id}/tags", retry=1, timeout=10)
        data = response.json()
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            return data["data"]
        if isinstance(data, list):
            return data
    except Exception:
        return []
    return []


def _fetch_event_tags(event_id: str) -> List[Dict[str, Any]]:
    try:
        response = _robust_get(f"{GAMMA_API}/events/{event_id}/tags", retry=2, timeout=12)
        data = response.json()
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            return data["data"]
        if isinstance(data, list):
            return data
    except Exception:
        return []
    return []


def _batch_fetch_event_tags(event_ids: set[str], cache: Dict[str, List[Dict[str, Any]]]) -> None:
    for event_id in [item for item in event_ids if item and item not in cache]:
        cache[event_id] = _fetch_event_tags(event_id)
        time.sleep(0.05)


def _fetch_markets_page(offset: int, closed: bool) -> List[Dict[str, Any]]:
    response = _robust_get(
        f"{GAMMA_API}/markets",
        params={"limit": PAGE_LIMIT, "offset": offset, "closed": str(closed).lower()},
        retry=3,
        timeout=20,
    )
    payload = response.json()
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return payload["data"]
    if isinstance(payload, list):
        return payload
    return []


def _build_record(stub: Dict[str, Any], event_tags_cache: Dict[str, List[Dict[str, Any]]], tags_hit: int, tags_miss: int) -> tuple[tuple[Any, ...], int, int]:
    condition_id = str(stub.get("conditionId") or stub.get("condition_id") or "")
    market_id = str(stub.get("id") or "")
    event_id = _extract_event_id_from_stub(stub)
    record = {key: "" for key in COLS}
    record["condition_id"] = condition_id

    tags = event_tags_cache.get(event_id, []) if event_id else []
    if not tags and market_id:
        tags = _fetch_market_tags(market_id)
    record["Subject"] = _tags_to_subject(tags)
    if record["Subject"]:
        tags_hit += 1
    else:
        tags_miss += 1

    record["question"] = str(stub.get("question") or stub.get("title") or "")
    record["rules"] = str(stub.get("description") or stub.get("rules") or "")
    record["endDate"] = str(stub.get("endDate") or stub.get("end_time") or "")
    slug = str(stub.get("slug") or "").strip()
    record["url"] = f"https://polymarket.com/event/{slug}" if slug else ""
    record["EventID"] = event_id

    yes_token, no_token = _extract_yes_no_tokens(stub)
    record["yes_token"] = yes_token
    record["no_token"] = no_token
    record["token"] = yes_token
    record["option_name"] = "Yes"
    record["query_time_beijing"] = _now_iso()
    record["ingested_at"] = _now_iso()
    return tuple(record[key] for key in COLS), tags_hit, tags_miss


def _run_dictionary_refresh() -> None:
    conn: sqlite3.Connection | None = None
    try:
        path = _get_db_path()
        _update_state(
            phase="starting",
            last_error="",
            last_summary="",
            stats={
                "mode": "",
                "offset": 0,
                "pages_processed": 0,
                "markets_fetched": 0,
                "inserted": 0,
                "skipped_existing": 0,
                "skipped_expired": 0,
                "tags_hit": 0,
                "tags_miss": 0,
                "event_cache_size": 0,
                "db_count": 0,
            },
            logs=[],
        )
        _append_log(f"准备更新字典: {path}")

        conn, expired_deleted, duplicate_deleted = _ensure_db(path)
        if expired_deleted or duplicate_deleted:
            _append_log(
                f"已清理历史数据: 删除过期 {expired_deleted} 条，删除重复 {duplicate_deleted} 条"
            )
        cur = conn.cursor()
        cur.execute(f'SELECT condition_id FROM "{TABLE_NAME}"')
        existing_ids = {str(row[0]) for row in cur.fetchall() if row[0]}
        initial_count = len(existing_ids)
        _update_state(stats={"db_count": initial_count})
        _append_log(f"数据库已就绪，当前已有 {initial_count} 条字典记录")

        event_tags_cache: Dict[str, List[Dict[str, Any]]] = {}
        pages_processed = 0
        markets_fetched = 0
        inserted = 0
        skipped_existing = 0
        skipped_expired = 0
        tags_hit = 0
        tags_miss = 0
        consecutive_errors = 0

        refresh_modes = (False, True) if REFRESH_CLOSED_MARKETS else (False,)
        _append_log(
            "刷新模式: active 市场"
            if not REFRESH_CLOSED_MARKETS
            else "刷新模式: active + closed 市场"
        )

        for closed in refresh_modes:
            mode = "closed" if closed else "active"
            offset = 0
            _append_log(f"开始抓取 {mode} 市场")

            while True:
                try:
                    _update_state(
                        phase=f"fetch_{mode}",
                        stats={
                            "mode": mode,
                            "offset": offset,
                            "pages_processed": pages_processed,
                            "markets_fetched": markets_fetched,
                            "inserted": inserted,
                            "skipped_existing": skipped_existing,
                            "skipped_expired": skipped_expired,
                            "tags_hit": tags_hit,
                            "tags_miss": tags_miss,
                            "event_cache_size": len(event_tags_cache),
                            "db_count": initial_count + inserted,
                        },
                    )

                    markets = _fetch_markets_page(offset=offset, closed=closed)
                    consecutive_errors = 0
                except Exception as exc:
                    consecutive_errors += 1
                    _append_log(f"{mode} offset={offset} 抓取失败: {type(exc).__name__}: {exc}")
                    if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                        raise RuntimeError(f"{mode} 市场连续失败 {MAX_CONSECUTIVE_ERRORS} 次，已停止更新") from exc
                    time.sleep(2 + consecutive_errors * 2)
                    continue

                if not markets:
                    _append_log(f"{mode} 市场抓取完成，offset={offset}")
                    break

                pages_processed += 1
                markets_fetched += len(markets)
                page_stubs: List[tuple[Dict[str, Any], str, str, str]] = []
                page_event_ids: set[str] = set()
                page_skipped_existing = 0
                page_skipped_expired = 0

                for stub in markets:
                    condition_id = str(stub.get("conditionId") or stub.get("condition_id") or "")
                    if not condition_id:
                        continue
                    if condition_id in existing_ids:
                        page_skipped_existing += 1
                        continue
                    if _is_expired_end_date(stub.get("endDate") or stub.get("end_time")):
                        page_skipped_expired += 1
                        continue
                    market_id = str(stub.get("id") or "")
                    event_id = _extract_event_id_from_stub(stub)
                    page_stubs.append((stub, condition_id, event_id, market_id))
                    if event_id:
                        page_event_ids.add(event_id)

                _batch_fetch_event_tags(page_event_ids, event_tags_cache)

                records_to_insert: List[tuple[Any, ...]] = []
                for stub, condition_id, _event_id, _market_id in page_stubs:
                    existing_ids.add(condition_id)
                    record, tags_hit, tags_miss = _build_record(stub, event_tags_cache, tags_hit, tags_miss)
                    records_to_insert.append(record)

                if records_to_insert:
                    placeholders = ",".join(["?"] * len(COLS))
                    insert_sql = f'INSERT OR IGNORE INTO "{TABLE_NAME}" VALUES ({placeholders})'
                    cur.executemany(insert_sql, records_to_insert)
                    conn.commit()
                    inserted += max(cur.rowcount, 0)

                skipped_existing += page_skipped_existing
                skipped_expired += page_skipped_expired
                _update_state(
                    stats={
                        "mode": mode,
                        "offset": offset,
                        "pages_processed": pages_processed,
                        "markets_fetched": markets_fetched,
                        "inserted": inserted,
                        "skipped_existing": skipped_existing,
                        "skipped_expired": skipped_expired,
                        "tags_hit": tags_hit,
                        "tags_miss": tags_miss,
                        "event_cache_size": len(event_tags_cache),
                        "db_count": initial_count + inserted,
                    }
                )
                _append_log(
                    f"{mode} 第 {pages_processed} 页: 拉取 {len(markets)} 条, 新增 {len(records_to_insert)} 条, 已存在 {page_skipped_existing} 条, 过期跳过 {page_skipped_expired} 条"
                )

                offset += PAGE_LIMIT
                if len(markets) < PAGE_LIMIT:
                    _append_log(f"{mode} 市场已到末页")
                    break
                time.sleep(0.4)

        final_count = _count_rows(path)
        summary = (
            f"字典更新完成，共 {final_count} 条；本次新增 {inserted} 条，"
            f"跳过已存在 {skipped_existing} 条，跳过过期 {skipped_expired} 条，tags 命中 {tags_hit} 条"
        )
        _append_log(summary)
        _update_state(
            running=False,
            phase="done",
            finished_at=_now_iso(),
            last_summary=summary,
            stats={
                "mode": "",
                "db_count": final_count,
                "pages_processed": pages_processed,
                "markets_fetched": markets_fetched,
                "inserted": inserted,
                "skipped_existing": skipped_existing,
                "skipped_expired": skipped_expired,
                "tags_hit": tags_hit,
                "tags_miss": tags_miss,
                "event_cache_size": len(event_tags_cache),
            },
        )
    except Exception as exc:
        error_text = str(exc)
        _append_log(f"更新失败: {error_text}")
        _update_state(
            running=False,
            phase="error",
            finished_at=_now_iso(),
            last_error=error_text,
        )
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass


def get_dictionary_status() -> Dict[str, Any]:
    path = _get_db_path()
    with _STATE_LOCK:
        payload = _clone(_STATE)
    payload["db_path"] = _get_db_path_for_ui()
    payload["db_path_abs"] = str(path)
    payload["table_name"] = TABLE_NAME
    payload["file_exists"] = path.exists()
    payload["file_updated_at"] = _file_mtime(path)
    payload["count"] = _count_rows(path)
    payload["stats"]["db_count"] = payload["count"]
    return payload


def start_dictionary_refresh() -> Dict[str, Any]:
    global _RUNNER
    already_running = False
    with _STATE_LOCK:
        if _STATE.get("running"):
            already_running = True
        else:
            _STATE["running"] = True
            _STATE["phase"] = "queued"
            _STATE["message"] = "已加入更新队列"
            _STATE["started_at"] = _now_iso()
            _STATE["finished_at"] = ""
            _STATE["last_error"] = ""
            _STATE["last_summary"] = ""
            _STATE["logs"] = []
    if already_running:
        return get_dictionary_status()
    _RUNNER = threading.Thread(target=_run_dictionary_refresh, name="polymarket-dictionary-refresh", daemon=True)
    _RUNNER.start()
    return get_dictionary_status()
