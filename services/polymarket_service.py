from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from services.config_loader import (
    BASE_DIR,
    get_default_wallets,
    get_market_realtime_db_path,
    get_polymarket_dictionary_db_path,
    load_config,
    load_web_settings,
)
from services.http_client import SESSION, get_timeout
from services import strategy_data_source
from services.clob_orderbook_service import fetch_binary_orderbook_quotes
from services.strategy_profit_engine import (
    apply_live_position_overrides,
    calculate_position_pcts,
    fetch_local_order_map,
    fetch_remote_position_map,
    resolve_strategy_bankroll,
)
from services.strategy_stats_store import (
    load_latest_valid_position_snapshot,
    strategy_metrics_db_directory,
    sync_all_strategy_stats,
)


_CONFIG = load_config()
GAMMA_API = _CONFIG.get("api", {}).get("gamma_base", "https://gamma-api.polymarket.com").rstrip("/")
DATA_API = _CONFIG.get("holdings", {}).get("data_api", "https://data-api.polymarket.com").rstrip("/")

_CACHE_TTL_SECONDS = 60
_MARKET_SNAPSHOT_MAX_AGE_SECONDS = 300
_market_cache: Dict[str, Any] = {"ts": 0.0, "data": []}
_market_index_cache: Dict[str, Any] = {"ts": 0.0, "count": 0, "index": None}
_wallet_positions_cache: Dict[str, Dict[str, Any]] = {}
_WS_SNAPSHOT_TTL_SECONDS = 2
_WS_FRESHNESS_SECONDS = 180
_ws_snapshot_cache: Dict[str, Any] = {"ts": 0.0, "db_key": "", "by_token": {}}
_MARKET_SNAPSHOT_PATH = BASE_DIR / "polymarket_active_markets_cache.json"
_DICTIONARY_INDEX_TTL_SECONDS = 60
_dictionary_index_cache: Dict[str, Any] = {
    "ts": 0.0,
    "path": "",
    "by_condition_id": {},
    "by_token": {},
    "by_slug": {},
    "by_question": {},
}
_GAMMA_RESOLVE_TTL_SECONDS = 300
_GAMMA_LOOKUP_TIMEOUT_SECONDS = 3.0
_gamma_resolve_cache: Dict[str, Dict[str, Any]] = {}
_LIVE_POS_CACHE: Dict[str, Any] = {
    "ts": 0.0,
    "remote": {},
    "local": {},
    "remote_ok": False,
    "error": None,
}
_LIVE_POS_TTL_SECONDS = 5.0
_LIVE_POS_STALE_OK_SECONDS = 60.0
_LIVE_POS_REFRESH_LOCK = threading.Lock()
_LIVE_POS_REFRESH_RUNNING = False
_LOCAL_ORDER_CACHE: Dict[str, Any] = {"ts": 0.0, "data": {}}
_LOCAL_ORDER_TTL_SECONDS = 15.0
STRATEGY_EDITABLE_FIELDS = [
    "Inputs1",
    "Inputs2",
    "Inputs3",
    "Inputs4",
    "Inputs5",
    "Inputs6",
    "Inputs7",
    "Inputs8",
    "Inputs9",
    "Inputs10",
    "Inputs11",
    "Inputs12",
    "Inputs13",
    "strategy_bankroll",
]

_STRATEGY_MAIN_ALLOWED = ["yes_mid", "no_mid"]
_STRATEGY_SUB_ALLOWED = [
    "yes_position",
    "no_position",
    "yes_qty",
    "no_qty",
    "yes_avg",
    "no_avg",
    "strategy_pnl",
]


def get_strategy_chart_defaults(detail: Dict[str, Any] | None = None) -> Dict[str, Any]:
    _ = detail
    return {
        "interval": "5s",
        "range": "1d",
        "main_side": "all",
        "main_series": list(_STRATEGY_MAIN_ALLOWED),
        "sub_series": ["yes_position", "no_position", "strategy_pnl"],
        "overlay_crypto": [],
        "overlay_finance": [],
        "overlay_crypto_fields": ["price", "mcap_usd"],
        "overlay_finance_fields": ["price", "mcap_usd"],
        "indicator_config": {
            "ma": {"enabled": False, "window": 9},
            "macd": {"enabled": False, "fast": 12, "slow": 26, "signal": 9},
        },
        "series_style": {},
        "template": "default",
    }


def get_strategy_chart_capabilities(detail: Dict[str, Any] | None = None) -> Dict[str, Any]:
    settings = load_web_settings()
    metric_catalog = {"items": [], "numeric": [], "state": []}
    try:
        row_id = (detail or {}).get("row_id")
        if row_id is not None:
            from services.strategy_metric_store import list_metric_catalog
            metric_catalog = list_metric_catalog(int(row_id))
    except Exception:
        metric_catalog = {"items": [], "numeric": [], "state": []}
    return {
        "main_allowed": list(_STRATEGY_MAIN_ALLOWED),
        "sub_allowed": list(_STRATEGY_SUB_ALLOWED),
        "metric_catalog": metric_catalog,
        "overlay_allowed": {
            "crypto": list(settings.get("crypto_symbols", []) or []),
            "finance": list(settings.get("finance_symbols", []) or []),
        },
        "overlay_field_allowed": {
            "crypto": [
                "price",
                "mcap_usd",
                "fdv_usd",
                "vol_24h_base",
                "vol_24h_quote",
                "circ_supply",
                "total_supply",
                "max_supply",
            ],
            "finance": ["price", "mcap_usd"],
        },
        "style_capabilities": {
            "line_types": ["solid", "dashed", "dotted"],
            "width_min": 1,
            "width_max": 6,
        },
        "indicator_capabilities": {
            "ma": {"window_min": 2, "window_max": 240},
            "macd": {"fast_min": 2, "fast_max": 60, "slow_min": 3, "slow_max": 120, "signal_min": 2, "signal_max": 60},
        },
    }


def _compress_text(text: str, limit: int = 24) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)].rstrip() + "..."


def _build_strategy_display_name(item: Dict[str, Any], row_id: int | None = None) -> str:
    raw_strategy = str(
        item.get("Strategy") or item.get("strategy") or item.get("Code") or item.get("display_name") or f"策略{row_id or ''}"
    ).strip()
    if raw_strategy.lower().endswith(".py"):
        raw_strategy = raw_strategy[:-3].strip()
    translation = str(
        item.get("Translation") or item.get("question") or item.get("Subject") or item.get("subject") or ""
    ).strip()
    translation = _compress_text(translation, 24)
    if translation:
        return f"{raw_strategy}: {translation}"
    return raw_strategy


def _resolve_workspace_db_path(value: Any, default_name: str) -> Path:
    text = str(value or "").strip()
    path = Path(text).expanduser() if text else (BASE_DIR / default_name)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


def _ensure_sqlite_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)


def _parse_json_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _source_status(status: str, error: str | None = None, **extra: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"status": status, "error": error}
    payload.update(extra)
    return payload


def _normalize_market(raw: Dict[str, Any], category: str = "Unknown", event: Dict[str, Any] | None = None) -> Dict[str, Any]:
    outcomes = _parse_json_list(raw.get("outcomes", []))
    prices = _parse_json_list(raw.get("outcomePrices", []))
    token_ids = _parse_json_list(raw.get("clobTokenIds", []))

    yes_price = None
    no_price = None
    yes_token = None
    no_token = None

    # ① outcomes 是 dict 列表
    if isinstance(outcomes, list) and outcomes and any(isinstance(x, dict) for x in outcomes):
        for idx, outcome in enumerate(outcomes):
            if not isinstance(outcome, dict):
                continue
            outcome_name = str(outcome.get("name") or outcome.get("outcome") or "").strip().lower()
            token = (
                outcome.get("clobTokenId")
                or outcome.get("tokenId")
                or outcome.get("token_id")
                or (token_ids[idx] if idx < len(token_ids) else None)
            )
            price = prices[idx] if idx < len(prices) else None
            try:
                price = float(price) if price is not None else None
            except (TypeError, ValueError):
                price = None

            if outcome_name == "yes":
                yes_price = price
                yes_token = token
            elif outcome_name == "no":
                no_price = price
                no_token = token

    # ② outcomes 是字符串列表
    elif isinstance(outcomes, list):
        for idx, outcome in enumerate(outcomes):
            outcome_name = str(outcome).strip().lower()
            token = token_ids[idx] if idx < len(token_ids) else None
            price = prices[idx] if idx < len(prices) else None
            try:
                price = float(price) if price is not None else None
            except (TypeError, ValueError):
                price = None

            if outcome_name == "yes":
                yes_price = price
                yes_token = token
            elif outcome_name == "no":
                no_price = price
                no_token = token

    event = event or {}
    event_slug = raw.get("eventSlug") or raw.get("event_slug") or event.get("slug")
    group_item_title = raw.get("groupItemTitle")
    raw_url = raw.get("url")
    market_url = raw_url
    if not market_url and event_slug and raw.get("slug"):
        market_url = f"https://polymarket.com/event/{event_slug}/{raw.get('slug')}"

    return {
        "condition_id": raw.get("conditionId"),
        "slug": raw.get("slug"),
        "event_slug": event_slug,
        "group_item_title": group_item_title,
        "url": market_url,
        "question": raw.get("question"),
        "category": category,
        "active": bool(raw.get("active", False)),
        "closed": bool(raw.get("closed", False)),
        "volume": raw.get("volumeNum") or raw.get("volume"),
        "liquidity": raw.get("liquidityNum") or raw.get("liquidity"),
        "best_bid": raw.get("bestBid"),
        "best_ask": raw.get("bestAsk"),
        "last_trade_price": raw.get("lastTradePrice"),
        "spread": raw.get("spread"),
        "end_date": raw.get("endDate") or raw.get("umaEndDate"),
        "yes_price": yes_price,
        "no_price": no_price,
        "yes_token": yes_token,
        "no_token": no_token,
        "raw": raw,
    }


def _read_market_snapshot_payload() -> tuple[List[Dict[str, Any]], float | None]:
    if not _MARKET_SNAPSHOT_PATH.exists():
        return [], None
    try:
        payload = json.loads(_MARKET_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], None
    data = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(data, list):
        return [], None
    updated_at = payload.get("updated_at") if isinstance(payload, dict) else None
    age_seconds: float | None = None
    if updated_at:
        parsed = _parse_iso_datetime(updated_at)
        if parsed:
            age_seconds = max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())
    if age_seconds is None:
        try:
            age_seconds = max(0.0, time.time() - _MARKET_SNAPSHOT_PATH.stat().st_mtime)
        except OSError:
            age_seconds = None
    return [item for item in data if isinstance(item, dict)], age_seconds


def _read_market_snapshot(*, max_age_seconds: float | None = None) -> List[Dict[str, Any]]:
    markets, age_seconds = _read_market_snapshot_payload()
    if max_age_seconds is not None and age_seconds is not None and age_seconds > max_age_seconds:
        return []
    return markets


def _write_market_snapshot(markets: List[Dict[str, Any]]) -> None:
    try:
        _MARKET_SNAPSHOT_PATH.write_text(
            json.dumps(
                {
                    "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                    "count": len(markets),
                    "data": markets,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        return


def _market_index_from_markets(markets: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    by_condition_id: Dict[str, Dict[str, Any]] = {}
    by_token: Dict[str, Dict[str, Any]] = {}
    by_slug: Dict[str, Dict[str, Any]] = {}
    by_question: Dict[str, Dict[str, Any]] = {}
    for market in markets:
        condition_id = str(market.get("condition_id") or "").strip()
        if condition_id and condition_id not in by_condition_id:
            by_condition_id[condition_id] = market
        slug = str(market.get("slug") or "").strip()
        if slug and slug not in by_slug:
            by_slug[slug] = market
        question = str(market.get("question") or "").strip().lower()
        if question and question not in by_question:
            by_question[question] = market
        for token in [str(market.get("yes_token") or "").strip(), str(market.get("no_token") or "").strip()]:
            if token and token not in by_token:
                by_token[token] = market
    return {
        "by_condition_id": by_condition_id,
        "by_token": by_token,
        "by_slug": by_slug,
        "by_question": by_question,
    }


def _cached_market_index_from_markets(markets: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    cache_ts = float(_market_cache.get("ts") or 0.0)
    cache_count = len(markets or [])
    cached = _market_index_cache.get("index")
    if cached is not None and _market_index_cache.get("ts") == cache_ts and _market_index_cache.get("count") == cache_count:
        return cached
    index = _market_index_from_markets(markets)
    _market_index_cache.update(ts=cache_ts, count=cache_count, index=index)
    return index


def _dictionary_market(record: Dict[str, Any]) -> Dict[str, Any]:
    subject = str(record.get("Subject") or "").strip()
    url = str(record.get("url") or "").strip()
    slug = ""
    marker = "/event/"
    if marker in url:
        slug = url.split(marker, 1)[1].strip().strip("/")
    return {
        "condition_id": str(record.get("condition_id") or "").strip(),
        "slug": slug,
        "event_slug": "",
        "group_item_title": "",
        "url": url,
        "question": record.get("question") or record.get("Translation") or record.get("Subject"),
        "category": subject or "Dictionary",
        "active": True,
        "closed": False,
        "volume": None,
        "liquidity": None,
        "best_bid": record.get("bid") or record.get("now_bid_price"),
        "best_ask": record.get("ask") or record.get("now_ask_price"),
        "last_trade_price": None,
        "spread": record.get("l1_spread_c"),
        "end_date": record.get("endDate"),
        "yes_price": record.get("ask") or record.get("now_ask_price"),
        "no_price": record.get("opp_ask_price") or record.get("opp_bids_price"),
        "yes_token": str(record.get("yes_token") or record.get("token") or "").strip(),
        "no_token": str(record.get("no_token") or record.get("opp_token") or "").strip(),
        "raw": record,
    }


def _load_dictionary_market_index(force_refresh: bool = False) -> Dict[str, Dict[str, Any]]:
    settings = load_web_settings()
    db_path = Path(get_polymarket_dictionary_db_path(settings)).expanduser()
    now = time.time()
    cache_path = str(_dictionary_index_cache.get("path") or "")
    current_path = str(db_path)
    if (
        not force_refresh
        and cache_path == current_path
        and now - float(_dictionary_index_cache.get("ts") or 0.0) < _DICTIONARY_INDEX_TTL_SECONDS
    ):
        return {
            "by_condition_id": dict(_dictionary_index_cache.get("by_condition_id") or {}),
            "by_token": dict(_dictionary_index_cache.get("by_token") or {}),
            "by_slug": dict(_dictionary_index_cache.get("by_slug") or {}),
            "by_question": dict(_dictionary_index_cache.get("by_question") or {}),
        }

    if not db_path.exists():
        _dictionary_index_cache.update(ts=now, path=current_path, by_condition_id={}, by_token={}, by_slug={}, by_question={})
        return {"by_condition_id": {}, "by_token": {}, "by_slug": {}, "by_question": {}}

    by_condition_id: Dict[str, Dict[str, Any]] = {}
    by_token: Dict[str, Dict[str, Any]] = {}
    by_slug: Dict[str, Dict[str, Any]] = {}
    by_question: Dict[str, Dict[str, Any]] = {}
    conn = None
    try:
        conn = sqlite3.connect(str(db_path), timeout=3.0)
        conn.row_factory = sqlite3.Row
        tables = {str(row[0]) for row in conn.execute("select name from sqlite_master where type='table' order by name").fetchall()}
        if "polyMarket_Dictionary" not in tables:
            return {"by_condition_id": {}, "by_token": {}, "by_slug": {}, "by_question": {}}
        rows = conn.execute(
            'SELECT condition_id, question, Translation, Subject, endDate, url, token, opp_token, yes_token, no_token, ask, bid, now_ask_price, now_bid_price, opp_ask_price, opp_bids_price, l1_spread_c FROM "polyMarket_Dictionary"'
        ).fetchall()
        for row in rows:
            market = _dictionary_market(dict(row))
            condition_id = str(market.get("condition_id") or "").strip()
            if condition_id and condition_id not in by_condition_id:
                by_condition_id[condition_id] = market
            slug = str(market.get("slug") or "").strip()
            if slug and slug not in by_slug:
                by_slug[slug] = market
            question = str(market.get("question") or "").strip().lower()
            if question and question not in by_question:
                by_question[question] = market
            for token in [str(market.get("yes_token") or "").strip(), str(market.get("no_token") or "").strip()]:
                if token and token not in by_token:
                    by_token[token] = market
    except sqlite3.Error:
        by_condition_id = {}
        by_token = {}
        by_slug = {}
        by_question = {}
    finally:
        if conn is not None:
            conn.close()

    _dictionary_index_cache.update(
        ts=now,
        path=current_path,
        by_condition_id=by_condition_id,
        by_token=by_token,
        by_slug=by_slug,
        by_question=by_question,
    )
    return {
        "by_condition_id": dict(by_condition_id),
        "by_token": dict(by_token),
        "by_slug": dict(by_slug),
        "by_question": dict(by_question),
    }


def _known_markets(force_refresh: bool = False) -> List[Dict[str, Any]]:
    markets = list(_market_cache.get("data") or [])
    if markets and time.time() - float(_market_cache.get("ts") or 0.0) < _CACHE_TTL_SECONDS:
        return markets
    if not force_refresh:
        snapshot_markets = _read_market_snapshot(max_age_seconds=_MARKET_SNAPSHOT_MAX_AGE_SECONDS)
        if snapshot_markets:
            _market_cache["ts"] = time.time()
            _market_cache["data"] = list(snapshot_markets)
            return snapshot_markets
    stale_markets = list(markets)
    try:
        markets = fetch_active_markets(force_refresh=True if force_refresh else False)
    except Exception:
        markets = []
    if markets and (not stale_markets or markets is not stale_markets):
        _market_cache["ts"] = time.time()
        _market_cache["data"] = list(markets)
        return markets
    snapshot_markets = _read_market_snapshot(max_age_seconds=_MARKET_SNAPSHOT_MAX_AGE_SECONDS)
    if snapshot_markets:
        _market_cache["ts"] = time.time()
        _market_cache["data"] = list(snapshot_markets)
        return snapshot_markets
    if stale_markets:
        return stale_markets
    snapshot_markets = _read_market_snapshot()
    if snapshot_markets:
        _market_cache["ts"] = 0.0
        _market_cache["data"] = list(snapshot_markets)
    return snapshot_markets


def _gamma_first_market_payload(payload: Any) -> Dict[str, Any] | None:
    if isinstance(payload, list) and payload:
        return payload[0] if isinstance(payload[0], dict) else None
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data[0]
    return None


def _gamma_lookup_market_stub(
    *,
    resolve_cache: Dict[str, Any],
    clob_token_id: str | None = None,
    condition_id: str | None = None,
) -> Dict[str, Any] | None:
    """
    按 PolyMarket_QueryToken.py 思路：用 Gamma /markets 的 clob_token_ids 或 condition_ids 反查市场，
    得到完整 outcomes + clobTokenIds，用于补全监控表里缺失的 yes/no 双面 token。
    """
    key = ""
    if clob_token_id:
        key = f"clob:{str(clob_token_id).strip()}"
    elif condition_id:
        key = f"cid:{str(condition_id).strip()}"
    else:
        return None
    if key in resolve_cache:
        return resolve_cache[key]
    cached = _gamma_resolve_cache.get(key)
    now = time.time()
    if cached and now - float(cached.get("ts") or 0.0) < _GAMMA_RESOLVE_TTL_SECONDS:
        resolve_cache[key] = cached.get("data")
        return cached.get("data")

    params_base: Dict[str, Any] = {
        "limit": 10,
        "offset": 0,
        "withClobTokenIds": "true",
        "withOutcomes": "true",
    }
    if clob_token_id:
        params_base["clob_token_ids"] = str(clob_token_id).strip()
    else:
        params_base["condition_ids"] = str(condition_id).strip()

    stub: Dict[str, Any] | None = None
    for closed in ("false", "true"):
        try:
            resp = SESSION.get(
                f"{GAMMA_API}/markets",
                params={**params_base, "closed": closed},
                timeout=min(get_timeout(), _GAMMA_LOOKUP_TIMEOUT_SECONDS),
            )
            resp.raise_for_status()
            stub = _gamma_first_market_payload(resp.json())
            if stub:
                break
        except Exception:
            continue
    resolve_cache[key] = stub
    _gamma_resolve_cache[key] = {"ts": now, "data": stub}
    return stub


def _has_binary_yes_no_tokens(item: Dict[str, Any], matched_market: Dict[str, Any] | None) -> bool:
    yes_t = str(item.get("yes_token") or "").strip() or str((matched_market or {}).get("yes_token") or "").strip()
    no_t = str(item.get("no_token") or "").strip() or str((matched_market or {}).get("no_token") or "").strip()
    return bool(yes_t and no_t)


def _has_minimum_binary_identity(item: Dict[str, Any], matched_market: Dict[str, Any] | None = None) -> bool:
    yes_t = str(item.get("yes_token") or "").strip() or str((matched_market or {}).get("yes_token") or "").strip()
    no_t = str(item.get("no_token") or "").strip() or str((matched_market or {}).get("no_token") or "").strip()
    cid = str(item.get("condition_id") or "").strip() or str((matched_market or {}).get("condition_id") or "").strip()
    return bool((yes_t and no_t) or yes_t or no_t or cid)


def _enrich_monitoring_row_tokens(
    item: Dict[str, Any],
    matched_market: Dict[str, Any] | None,
    resolve_cache: Dict[str, Any],
) -> tuple[Dict[str, Any], Dict[str, Any] | None]:
    """
    若本地行 + 活跃市场索引仍无法凑齐 Yes/No 两个 clob token，则用 Gamma 反查补全，
    以便后续仓位映射、盘口与 strategy_stats 双面指标一致。
    """
    if not _has_minimum_binary_identity(item, matched_market):
        merged = dict(item)
        merged["_binary_identity_status"] = "insufficient_identity"
        return merged, matched_market

    if _has_binary_yes_no_tokens(item, matched_market):
        merged = dict(item)
        merged["_binary_identity_status"] = "ok"
        return merged, matched_market

    merged = dict(item)
    m_cat = str((matched_market or {}).get("category") or "Unknown")
    yes_t = str(merged.get("yes_token") or "").strip() or str((matched_market or {}).get("yes_token") or "").strip()
    no_t = str(merged.get("no_token") or "").strip() or str((matched_market or {}).get("no_token") or "").strip()
    cid = str(merged.get("condition_id") or "").strip() or str((matched_market or {}).get("condition_id") or "").strip()

    stub: Dict[str, Any] | None = None
    if yes_t or no_t:
        stub = _gamma_lookup_market_stub(resolve_cache=resolve_cache, clob_token_id=yes_t or no_t)
    elif cid:
        stub = _gamma_lookup_market_stub(resolve_cache=resolve_cache, condition_id=cid)

    if not stub:
        return merged, matched_market

    norm = _normalize_market(stub, category=m_cat)
    if not str(merged.get("yes_token") or "").strip() and norm.get("yes_token"):
        merged["yes_token"] = norm.get("yes_token")
    if not str(merged.get("no_token") or "").strip() and norm.get("no_token"):
        merged["no_token"] = norm.get("no_token")
    if not str(merged.get("condition_id") or "").strip() and norm.get("condition_id"):
        merged["condition_id"] = str(norm.get("condition_id") or "").strip()
    q_stub = str(norm.get("question") or "").strip()
    if q_stub and not str(merged.get("question") or "").strip() and not str(merged.get("Translation") or "").strip():
        merged["question"] = q_stub

    if _has_binary_yes_no_tokens(merged, norm):
        merged["_binary_identity_status"] = "enriched_from_gamma"
    else:
        merged["_binary_identity_status"] = "partial_after_gamma"

    return merged, norm


def fetch_active_markets(force_refresh: bool = False, max_pages: int = 8, page_size: int = 200) -> List[Dict[str, Any]]:
    now = time.time()
    if not force_refresh and _market_cache["data"] and now - _market_cache["ts"] < _CACHE_TTL_SECONDS:
        return list(_market_cache["data"])
    if not force_refresh:
        snapshot = _read_market_snapshot(max_age_seconds=_MARKET_SNAPSHOT_MAX_AGE_SECONDS)
        if snapshot:
            _market_cache["ts"] = now
            _market_cache["data"] = list(snapshot)
            return list(snapshot)

    markets: List[Dict[str, Any]] = []
    seen_condition_ids = set()
    try:
        for page in range(max_pages):
            offset = page * page_size
            params = {
                "limit": page_size,
                "offset": offset,
                "closed": "false",
                "active": "true",
            }
            resp = SESSION.get(f"{GAMMA_API}/events", params=params, timeout=get_timeout())
            resp.raise_for_status()
            payload = resp.json()
            events = payload.get("data", payload) if isinstance(payload, dict) else payload
            if not isinstance(events, list) or not events:
                break

            for event in events:
                tags = event.get("tags") or []
                category = tags[0].get("label", "Unknown") if tags and isinstance(tags[0], dict) else "Unknown"
                for market in event.get("markets", []):
                    if market.get("closed") or not market.get("active"):
                        continue
                    condition_id = market.get("conditionId")
                    if condition_id in seen_condition_ids:
                        continue
                    seen_condition_ids.add(condition_id)
                    markets.append(_normalize_market(market, category=category, event=event))
    except Exception:
        if markets:
            _market_cache["ts"] = now
            _market_cache["data"] = list(markets)
            _write_market_snapshot(markets)
            return list(markets)
        if _market_cache["data"] and now - float(_market_cache.get("ts") or 0.0) < _CACHE_TTL_SECONDS:
            return list(_market_cache["data"])
        snapshot = _read_market_snapshot(max_age_seconds=_MARKET_SNAPSHOT_MAX_AGE_SECONDS)
        if snapshot:
            _market_cache["ts"] = now
            _market_cache["data"] = list(snapshot)
            return list(snapshot)
        raise

    if markets:
        _market_cache["ts"] = now
        _market_cache["data"] = list(markets)
        _write_market_snapshot(markets)
        return markets
    if _market_cache["data"] and now - float(_market_cache.get("ts") or 0.0) < _CACHE_TTL_SECONDS:
        return list(_market_cache["data"])
    snapshot = _read_market_snapshot(max_age_seconds=_MARKET_SNAPSHOT_MAX_AGE_SECONDS)
    if snapshot:
        _market_cache["ts"] = now
        _market_cache["data"] = list(snapshot)
        return list(snapshot)
    _market_cache["ts"] = now
    _market_cache["data"] = []
    return []


def _market_haystack(market: Dict[str, Any]) -> str:
    return " ".join(
        [
            str(market.get("question", "")),
            str(market.get("slug", "")),
            str(market.get("category", "")),
            str(market.get("condition_id", "")),
            str(market.get("yes_token", "")),
            str(market.get("no_token", "")),
            str(market.get("group_item_title", "")),
            str((market.get("raw") or {}).get("groupItemTitle", "")),
        ]
    ).lower()


def _query_matches_haystack(tokens: List[str], haystack: str) -> bool:
    """All tokens must appear in haystack (AND logic)."""
    return all(token in haystack for token in tokens)


def _tokenize_query(query: str) -> List[str]:
    """Split query into lowercase tokens, filtering empty strings and noise words."""
    _NOISE = {"x", "vs", "v", "or", "and", "the", "a", "an", "of", "in", "on", "to", "for", "-", "/", "&", "|"}
    raw = [t for t in query.strip().lower().split() if t]
    # Keep noise words only if they are the sole token
    if len(raw) <= 1:
        return raw
    return [t for t in raw if t not in _NOISE]


def _query_to_slug_candidates(query: str) -> List[str]:
    """Convert user query text into possible Gamma event slug candidates."""
    import re
    text = query.strip().lower()
    # Replace common separators with hyphens
    slug_base = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    if not slug_base:
        return []
    candidates = [slug_base]
    # Try with common suffixes that Polymarket uses for multi-outcome events
    for suffix in ["-by", "-before", "-in-2026", "-in-2027", "-before-2027"]:
        if not slug_base.endswith(suffix):
            candidates.append(slug_base + suffix)
    return candidates


def _gamma_text_search(query: str, limit: int = 30) -> List[Dict[str, Any]]:
    """Search Gamma API by converting query to slug and trying /events?slug= exact match.
    Also tries /markets endpoint with condition_ids approach as secondary."""
    tokens = _tokenize_query(query)
    if not tokens:
        return []
    results: List[Dict[str, Any]] = []
    seen_cids: set = set()

    # Strategy 1: Try slug-based event lookup
    slug_candidates = _query_to_slug_candidates(query)
    for slug in slug_candidates:
        if len(results) >= limit:
            break
        try:
            resp = SESSION.get(
                f"{GAMMA_API}/events",
                params={"slug": slug},
                timeout=min(get_timeout(), 5.0),
            )
            resp.raise_for_status()
            payload = resp.json()
            events = payload if isinstance(payload, list) else [payload] if isinstance(payload, dict) and payload.get("markets") else []
            for event in events:
                if not isinstance(event, dict):
                    continue
                tags = event.get("tags") or []
                category = tags[0].get("label", "Unknown") if tags and isinstance(tags[0], dict) else "Unknown"
                for market in event.get("markets", []):
                    if not isinstance(market, dict):
                        continue
                    cid = str(market.get("conditionId") or "").strip()
                    if cid and cid in seen_cids:
                        continue
                    norm = _normalize_market(market, category=category, event=event)
                    if cid:
                        seen_cids.add(cid)
                    results.append(norm)
                    if len(results) >= limit:
                        break
            if results:
                break
        except Exception:
            continue

    # Strategy 2: Try /markets with broader fetch + local filter
    if not results:
        try:
            resp = SESSION.get(
                f"{GAMMA_API}/markets",
                params={"limit": 100, "offset": 0, "closed": "false", "active": "true"},
                timeout=min(get_timeout(), 5.0),
            )
            resp.raise_for_status()
            payload = resp.json()
            items = payload if isinstance(payload, list) else payload.get("data", [])
            for item in items:
                if not isinstance(item, dict):
                    continue
                tags = item.get("tags") or []
                cat = tags[0].get("label", "Unknown") if tags and isinstance(tags[0], dict) else "Unknown"
                market = _normalize_market(item, category=cat)
                haystack = _market_haystack(market)
                if _query_matches_haystack(tokens, haystack):
                    cid = str(market.get("condition_id") or "").strip()
                    if cid and cid in seen_cids:
                        continue
                    if cid:
                        seen_cids.add(cid)
                    results.append(market)
                    if len(results) >= limit:
                        break
        except Exception:
            pass

    return results[:limit]


def _search_dictionary_db(tokens: List[str], category_tokens: List[str], limit: int = 60, exclude_cids: set | None = None) -> List[Dict[str, Any]]:
    """Search Dictionary DB directly with SQL LIKE for each token (AND logic)."""
    settings = load_web_settings()
    db_path = Path(get_polymarket_dictionary_db_path(settings)).expanduser()
    if not db_path.exists():
        return []
    exclude = exclude_cids or set()
    results: List[Dict[str, Any]] = []
    conn = None
    try:
        conn = sqlite3.connect(str(db_path), timeout=3.0)
        conn.row_factory = sqlite3.Row
        tables = {str(row[0]) for row in conn.execute("select name from sqlite_master where type='table'").fetchall()}
        if "polyMarket_Dictionary" not in tables:
            return []
        # Build WHERE clause: each token must appear in question OR Translation OR Subject OR url
        where_parts = []
        params: list = []
        for token in tokens:
            pattern = f"%{token}%"
            where_parts.append("(question LIKE ? OR Translation LIKE ? OR Subject LIKE ? OR url LIKE ?)")
            params.extend([pattern, pattern, pattern, pattern])
        if category_tokens:
            cat_conditions = " OR ".join(["Subject LIKE ?" for _ in category_tokens])
            where_parts.append(f"({cat_conditions})")
            params.extend([f"%{ct}%" for ct in category_tokens])
        where_clause = " AND ".join(where_parts) if where_parts else "1=1"
        sql = f'SELECT condition_id, question, Translation, Subject, endDate, url, token, opp_token, yes_token, no_token, ask, bid, now_ask_price, now_bid_price, opp_ask_price, opp_bids_price, l1_spread_c FROM "polyMarket_Dictionary" WHERE {where_clause} LIMIT ?'
        params.append(limit * 3)
        rows = conn.execute(sql, params).fetchall()
        for row in rows:
            record = dict(row)
            cid = str(record.get("condition_id") or "").strip()
            if cid and cid in exclude:
                continue
            market = _dictionary_market(record)
            results.append(market)
            if len(results) >= limit:
                break
    except sqlite3.Error:
        pass
    finally:
        if conn is not None:
            conn.close()
    return results


def _parse_categories(category: str) -> List[str]:
    """Parse category input into list of lowercase category tokens (OR logic).
    Supports separators: / , ; and whitespace between words."""
    import re
    raw = category.strip().lower()
    if not raw:
        return []
    parts = re.split(r"[/,;]+", raw)
    return [p.strip() for p in parts if p.strip()]


def _category_matches(category_tokens: List[str], market_category: str) -> bool:
    """Any of the category tokens appearing in market category = match (OR logic)."""
    if not category_tokens:
        return True
    cat = market_category.lower()
    return any(token in cat for token in category_tokens)


def search_markets(query: str = "", category: str = "", limit: int = 60, force_refresh: bool = False) -> List[Dict[str, Any]]:
    query_text = query.strip().lower()
    category_tokens = _parse_categories(category)
    tokens = _tokenize_query(query_text)
    markets = _known_markets(force_refresh=force_refresh)
    results: List[Dict[str, Any]] = []
    seen_ids: set = set()

    for market in markets:
        if category_tokens and not _category_matches(category_tokens, str(market.get("category", ""))):
            continue
        haystack = _market_haystack(market)
        if tokens and not _query_matches_haystack(tokens, haystack):
            continue
        cid = str(market.get("condition_id") or "").strip()
        if cid:
            seen_ids.add(cid)
        results.append(market)
        if len(results) >= limit:
            return results

    # Also search Dictionary database directly with SQL LIKE
    if tokens or category_tokens:
        dict_results = _search_dictionary_db(tokens, category_tokens, limit=limit - len(results), exclude_cids=seen_ids)
        for market in dict_results:
            cid = str(market.get("condition_id") or "").strip()
            if cid and cid in seen_ids:
                continue
            if cid:
                seen_ids.add(cid)
            results.append(market)
            if len(results) >= limit:
                return results

    # Fallback: if local results are sparse and we have a text query, try Gamma API
    if tokens and len(results) < min(5, limit):
        gamma_results = _gamma_text_search(query, limit=limit - len(results))
        for market in gamma_results:
            cid = str(market.get("condition_id") or "").strip()
            if cid and cid in seen_ids:
                continue
            if category_tokens and not _category_matches(category_tokens, str(market.get("category", ""))):
                continue
            if cid:
                seen_ids.add(cid)
            results.append(market)
            if len(results) >= limit:
                break

    return results


def resolve_market_selection(
    *,
    query: str = "",
    condition_id: str = "",
    token_id: str = "",
    limit: int = 20,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    query_text = str(query or "").strip()
    condition_text = str(condition_id or "").strip()
    token_text = str(token_id or "").strip()
    markets = _known_markets(force_refresh=force_refresh)
    market_index = _cached_market_index_from_markets(markets)
    selected = None
    source = "cache" if markets else "empty"
    if condition_text:
        selected = (market_index.get("by_condition_id") or {}).get(condition_text)
    if selected is None and token_text:
        selected = (market_index.get("by_token") or {}).get(token_text)

    if selected is None and (condition_text or token_text):
        stub = _gamma_lookup_market_stub(
            resolve_cache={},
            condition_id=condition_text or None,
            clob_token_id=token_text or None,
        )
        if stub:
            selected = _normalize_market(stub, category="Resolved")
            source = "gamma"

    results = search_markets(query=query_text, limit=max(1, limit), force_refresh=force_refresh) if query_text else []
    if selected:
        selected_condition = str(selected.get("condition_id") or "").strip()
        results = [
            selected,
            *[
                item
                for item in results
                if str(item.get("condition_id") or "").strip() != selected_condition
            ],
        ][: max(1, limit)]
    elif results:
        selected = results[0]
        source = source if source != "empty" else "search"

    return {
        "ok": bool(selected or results),
        "query": query_text,
        "condition_id": condition_text,
        "token_id": token_text,
        "selected": selected,
        "results": results,
        "count": len(results),
        "source": source,
        "snapshot_path": str(_MARKET_SNAPSHOT_PATH),
    }


def build_workspace_market_detail(market: Dict[str, Any] | None, *, condition_id: str = "", token_id: str = "") -> Dict[str, Any]:
    matched_market = market or {}
    resolved_condition_id = str(matched_market.get("condition_id") or condition_id or "").strip()
    yes_token = str(matched_market.get("yes_token") or "").strip()
    no_token = str(matched_market.get("no_token") or "").strip()
    if token_id and token_id not in {yes_token, no_token}:
        if not yes_token:
            yes_token = token_id
        elif not no_token:
            no_token = token_id
    ws_snapshot = _select_strategy_ws_snapshot(yes_token, no_token, resolved_condition_id)
    market_prices = _resolve_strategy_market_prices(
        matched_market,
        ws_snapshot=ws_snapshot,
        yes_token=yes_token,
        no_token=no_token,
    )
    question = str(matched_market.get("question") or matched_market.get("slug") or resolved_condition_id or "Manual Market").strip()
    return {
        "row_id": None,
        "strategy": "Manual Market",
        "question": question,
        "subject": None,
        "display_name": question,
        "condition_id": resolved_condition_id,
        "yes_token": yes_token,
        "no_token": no_token,
        "yes_bid": market_prices.get("yes_bid"),
        "yes_ask": market_prices.get("yes_ask"),
        "yes_last_price": market_prices.get("yes_last_price"),
        "no_bid": market_prices.get("no_bid"),
        "no_ask": market_prices.get("no_ask"),
        "no_last_price": market_prices.get("no_last_price"),
        "yes_qty": 0.0,
        "no_qty": 0.0,
        "yes_avg": None,
        "no_avg": None,
        "yes_position": 0.0,
        "no_position": 0.0,
        "yes_current_pct": 0.0,
        "no_current_pct": 0.0,
        "strategy_bankroll": 0.0,
        "strategy_pnl": 0.0,
        "price_source": market_prices.get("price_source") or "manual_lookup",
        "position_source": "manual_selection",
        "market_updated_at": market_prices.get("updated_at"),
        "realtime_snapshot_db_path": market_prices.get("snapshot_db_path"),
        "end_date": matched_market.get("end_date"),
        "market_category": matched_market.get("category"),
        "matched_market_raw": matched_market.get("raw"),
        "editable": {},
        "binary_identity_status": "ok" if yes_token or no_token or resolved_condition_id else "missing",
        "market_selected_manually": True,
        "raw": matched_market.get("raw") or matched_market,
    }


def _normalize_position(position: Dict[str, Any]) -> Dict[str, Any]:
    token_id = (
        position.get("asset")
        or position.get("tokenId")
        or position.get("token_id")
        or position.get("clobTokenId")
    )
    size = position.get("size")
    avg_price = position.get("avgPrice") or position.get("averagePrice") or position.get("price")
    current_value = position.get("currentValue") or position.get("cashPnl")
    return {
        "title": position.get("title") or position.get("question") or position.get("market"),
        "slug": position.get("slug"),
        "url": position.get("url"),
        "event_slug": position.get("eventSlug") or position.get("event_slug"),
        "group_item_title": position.get("groupItemTitle") or position.get("group_item_title"),
        "condition_id": position.get("conditionId") or position.get("condition_id"),
        "token_id": str(token_id) if token_id is not None else "",
        "outcome": position.get("outcome") or position.get("side"),
        "size": size,
        "avg_price": avg_price,
        "current_value": current_value,
        "redeemable": position.get("redeemable"),
        "cash_pnl": position.get("cashPnl"),
        "percent_pnl": position.get("percentPnl"),
        "raw": position,
    }


def _merge_market_link_fields(item: Dict[str, Any], matched: Dict[str, Any] | None) -> Dict[str, Any]:
    if not matched:
        return item
    raw = matched.get("raw") or {}
    for key in ("condition_id", "slug", "event_slug", "group_item_title", "url", "yes_token", "no_token"):
        if not item.get(key):
            item[key] = matched.get(key) or raw.get(key)
    if not item.get("title") and (matched.get("question") or raw.get("question")):
        item["title"] = matched.get("question") or raw.get("question")
    if not item.get("question") and (matched.get("question") or raw.get("question")):
        item["question"] = matched.get("question") or raw.get("question")
    return item


def _find_market_for_position(position: Dict[str, Any], indexes: List[Dict[str, Dict[str, Any]]]) -> Dict[str, Any] | None:
    condition_id = str(position.get("condition_id") or "").strip()
    token_id = str(position.get("token_id") or "").strip()
    slug = str(position.get("slug") or "").strip()
    title = str(position.get("title") or position.get("question") or "").strip().lower()
    for index in indexes:
        if condition_id:
            matched = (index.get("by_condition_id") or {}).get(condition_id)
            if matched:
                return matched
        if token_id:
            matched = (index.get("by_token") or {}).get(token_id)
            if matched:
                return matched
        if slug:
            matched = (index.get("by_slug") or {}).get(slug)
            if matched:
                return matched
        if title:
            matched = (index.get("by_question") or {}).get(title)
            if matched:
                return matched
    return None


def fetch_wallet_positions(wallet: str | None = None) -> Dict[str, Any]:
    wallets = [wallet.strip()] if wallet and wallet.strip() else get_default_wallets()
    if not wallets:
        return {"wallets": [], "positions": [], "error": "No wallet configured."}
    cache_key = "|".join(sorted(wallets))

    all_positions: List[Dict[str, Any]] = []
    errors: List[str] = []
    active_index = _cached_market_index_from_markets(_known_markets())
    dictionary_index = _load_dictionary_market_index()
    for user_wallet in wallets:
        try:
            resp = SESSION.get(
                f"{DATA_API}/positions",
                params={"user": user_wallet, "sizeThreshold": 0},
                timeout=get_timeout(),
            )
            resp.raise_for_status()
            payload = resp.json()
            positions = payload if isinstance(payload, list) else payload.get("data", [])
            for position in positions:
                normalized = _normalize_position(position)
                matched_market = _find_market_for_position(normalized, [active_index, dictionary_index])
                _merge_market_link_fields(normalized, matched_market)
                normalized["wallet"] = user_wallet
                all_positions.append(normalized)
        except Exception as exc:  # pragma: no cover - network fallback
            errors.append(f"{user_wallet}: {exc}")

    payload = {
        "ok": not errors,
        "wallets": wallets,
        "positions": all_positions,
        "count": len(all_positions),
        "errors": errors,
    }
    if not errors:
        _wallet_positions_cache[cache_key] = dict(payload)
        return payload
    if not all_positions:
        fallback = dict(_wallet_positions_cache.get(cache_key) or {})
        if fallback.get("positions"):
            fallback["ok"] = True
            fallback["errors"] = errors
            fallback["count"] = len(fallback.get("positions") or [])
            fallback["stale"] = True
            fallback["fallback_source"] = "cache"
            return fallback
    payload["stale"] = False
    payload["fallback_source"] = None
    return payload


def get_overview(wallet: str | None = None, allow_remote_positions: bool = False) -> Dict[str, Any]:
    t0 = time.perf_counter()
    markets: List[Dict[str, Any]] = []
    holdings: Dict[str, Any] = {"wallets": [], "positions": [], "count": 0, "errors": []}
    profit_summary: Dict[str, Any] = {"ok": False, "running_strategy_count": 0, "total_strategy_profit": 0.0}
    source_statuses: Dict[str, Dict[str, Any]] = {}

    try:
        tm0 = time.perf_counter()
        markets = fetch_active_markets()
        tm1 = time.perf_counter()
        print(f"[SV][overview] fetch_active_markets {(tm1 - tm0) * 1000:.1f}ms count={len(markets)}")
        source_statuses["markets_api"] = _source_status("good", count=len(markets))
    except Exception as exc:
        tm1 = time.perf_counter()
        print(f"[SV][overview] fetch_active_markets {(tm1 - tm0) * 1000:.1f}ms error={exc}")
        if _market_cache["data"]:
            markets = list(_market_cache["data"])
            source_statuses["markets_api"] = _source_status("degraded", str(exc), count=len(markets))
        else:
            source_statuses["markets_api"] = _source_status("error", str(exc), count=0)

    try:
        th0 = time.perf_counter()
        holdings = fetch_wallet_positions(wallet)
        th1 = time.perf_counter()
        print(f"[SV][overview] fetch_wallet_positions {(th1 - th0) * 1000:.1f}ms count={holdings.get('count', 0)}")
        holding_errors = holdings.get("errors") or []
        source_statuses["holdings_api"] = _source_status(
            "degraded" if holding_errors else "good",
            " | ".join(str(item) for item in holding_errors) if holding_errors else None,
            count=holdings.get("count", 0),
            wallet_count=len(holdings.get("wallets", [])),
        )
    except Exception as exc:
        th1 = time.perf_counter()
        print(f"[SV][overview] fetch_wallet_positions {(th1 - th0) * 1000:.1f}ms error={exc}")
        source_statuses["holdings_api"] = _source_status("error", str(exc), count=0, wallet_count=0)

    try:
        ts0 = time.perf_counter()
        local_strategy = _load_strategy_monitoring_rows(
            enrich_tokens=False,
            allow_remote_positions=allow_remote_positions,
            include_realtime_prices=False,
        )
        ts1 = time.perf_counter()
        strategy_items = local_strategy.get("data") or []
        print(
            f"[SV][overview] load_strategy_monitoring_rows {(ts1 - ts0) * 1000:.1f}ms count={len(strategy_items)} ok={bool(local_strategy.get('ok'))}"
        )
        running_strategy_count = sum(
            1
            for item in strategy_items
            if (_safe_float(item.get("yes_qty")) or 0.0) > 0 or (_safe_float(item.get("no_qty")) or 0.0) > 0
        )
        total_strategy_profit = sum(_safe_float(item.get("strategy_pnl")) or 0.0 for item in strategy_items)
        total_strategy_cost = sum(_safe_float(item.get("exposure")) or 0.0 for item in strategy_items)
        total_strategy_return_pct = (total_strategy_profit / total_strategy_cost) if total_strategy_cost > 0 else None
        profit_summary = {
            "ok": bool(local_strategy.get("ok")),
            "running_strategy_count": running_strategy_count,
            "total_strategy_profit": total_strategy_profit,
            "total_strategy_cost": total_strategy_cost,
            "total_strategy_return_pct": total_strategy_return_pct,
        }
        source_statuses["strategy_profit"] = _source_status(
            "good" if local_strategy.get("ok") and strategy_items else "pending",
            None if local_strategy.get("ok") else local_strategy.get("error"),
            running_strategy_count=running_strategy_count,
            total_strategy_profit=total_strategy_profit,
            history_loaded=bool(strategy_items),
        )
    except Exception as exc:
        ts1 = time.perf_counter()
        print(f"[SV][overview] load_strategy_monitoring_rows {(ts1 - ts0) * 1000:.1f}ms error={exc}")
        source_statuses["strategy_profit"] = _source_status("error", str(exc))

    categories = sorted({str(item.get("category", "Unknown")) for item in markets})
    t1 = time.perf_counter()
    print(f"[SV][overview] total {(t1 - t0) * 1000:.1f}ms")
    return {
        "market_count": len(markets),
        "category_count": len(categories),
        "categories": categories[:20],
        "wallets": holdings.get("wallets", []),
        "position_count": holdings.get("count", 0),
        "running_strategy_count": profit_summary.get("running_strategy_count", 0),
        "total_strategy_profit": profit_summary.get("total_strategy_profit", 0.0),
        "total_strategy_return_pct": profit_summary.get("total_strategy_return_pct"),
        "sources": source_statuses,
    }


def _discover_monitoring_table(conn: sqlite3.Connection, preferred_table: str) -> str:
    tables = [str(row[0]) for row in conn.execute("select name from sqlite_master where type='table' order by name").fetchall()]
    candidates = [preferred_table, "strategy_registry"]
    for table in candidates:
        if table and table in tables:
            return table
    for table in tables:
        cols = {str(row[1]).lower() for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}
        if "condition_id" in cols and ("strategy" in cols or "yes_token" in cols or "no_token" in cols):
            return table
    raise ValueError("No strategy monitoring table found.")


def _to_row_dict(columns: List[str], row: sqlite3.Row) -> Dict[str, Any]:
    return {columns[idx]: row[idx] for idx in range(len(columns))}


def _safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp_binary_price(value: float | None) -> float | None:
    if value is None:
        return None
    return max(0.0, min(1.0, value))


def _parse_iso_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _ws_snapshot_db_paths() -> list[Path]:
    candidates = [_strategy_storage_db_path()]
    unique_paths: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        text = str(path or "").strip()
        if not text or text in seen:
            continue
        unique_paths.append(Path(text).expanduser())
        seen.add(text)
    return unique_paths


def _load_ws_snapshot_map(force_refresh: bool = False) -> Dict[str, Any]:
    now = time.time()
    db_paths = _ws_snapshot_db_paths()
    cache_db_key = str(_ws_snapshot_cache.get("db_key") or "")
    db_key = "|".join(str(path) for path in db_paths)
    previous_by_token = dict(_ws_snapshot_cache.get("by_token") or {})
    if (
        not force_refresh
        and _ws_snapshot_cache["by_token"]
        and cache_db_key == db_key
        and now - float(_ws_snapshot_cache["ts"] or 0.0) < _WS_SNAPSHOT_TTL_SECONDS
    ):
        return dict(_ws_snapshot_cache["by_token"])

    by_token: Dict[str, Any] = {}
    for db_path in db_paths:
        if not db_path.exists():
            continue
        conn = None
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            tables = {str(row[0]) for row in conn.execute("select name from sqlite_master where type='table' order by name").fetchall()}
            if "markets_state" not in tables:
                continue
            rows = conn.execute(
                'SELECT target_option_json, target_price, market_json, depth_metrics_json, raw_clob_json, updated_at_utc, question, condition_id, status FROM "markets_state"'
            ).fetchall()
            for row in rows:
                target_option = {}
                market_json = {}
                depth_metrics = {}
                raw_clob_json = {}
                try:
                    target_option = json.loads(row["target_option_json"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    target_option = {}
                try:
                    market_json = json.loads(row["market_json"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    market_json = {}
                try:
                    depth_metrics = json.loads(row["depth_metrics_json"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    depth_metrics = {}
                try:
                    raw_clob_json = json.loads(row["raw_clob_json"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    raw_clob_json = {}
                token_id = str((target_option or {}).get("clobTokenId") or "").strip()
                if not token_id:
                    continue
                candidate = {
                    "token_id": token_id,
                    "outcome_side": str((target_option or {}).get("name") or "").strip().lower(),
                    "target_price": _safe_float(row["target_price"]),
                    "market_json": market_json if isinstance(market_json, dict) else {},
                    "depth_metrics": depth_metrics if isinstance(depth_metrics, dict) else {},
                    "raw_clob_json": raw_clob_json if isinstance(raw_clob_json, dict) else {},
                    "updated_at_utc": row["updated_at_utc"],
                    "question": row["question"],
                    "condition_id": row["condition_id"],
                    "status": row["status"],
                    "db_path": str(db_path),
                }
                existing = by_token.get(token_id)
                existing_dt = _parse_iso_datetime((existing or {}).get("updated_at_utc")) if existing else None
                candidate_dt = _parse_iso_datetime(candidate.get("updated_at_utc"))
                if existing is None or (candidate_dt and (existing_dt is None or candidate_dt > existing_dt)):
                    by_token[token_id] = candidate
        except sqlite3.Error:
            continue
        finally:
            if conn is not None:
                conn.close()
    if by_token:
        _ws_snapshot_cache.update({"ts": now, "db_key": db_key, "by_token": by_token})
        return dict(by_token)
    if previous_by_token:
        _ws_snapshot_cache.update({"ts": now, "db_key": db_key, "by_token": previous_by_token})
        return dict(previous_by_token)
    _ws_snapshot_cache.update({"ts": now, "db_key": db_key, "by_token": {}})
    return {}


def _snapshot_from_ws_row(row: sqlite3.Row, db_path: Path, token_id: str = "") -> Dict[str, Any] | None:
    target_option = {}
    market_json = {}
    depth_metrics = {}
    raw_clob_json = {}
    try:
        target_option = json.loads(row["target_option_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        target_option = {}
    try:
        market_json = json.loads(row["market_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        market_json = {}
    try:
        depth_metrics = json.loads(row["depth_metrics_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        depth_metrics = {}
    try:
        raw_clob_json = json.loads(row["raw_clob_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        raw_clob_json = {}
    resolved_token = str(token_id or (target_option or {}).get("clobTokenId") or "").strip()
    if not resolved_token:
        return None
    return {
        "token_id": resolved_token,
        "outcome_side": str((target_option or {}).get("name") or "").strip().lower(),
        "target_price": _safe_float(row["target_price"]),
        "market_json": market_json if isinstance(market_json, dict) else {},
        "depth_metrics": depth_metrics if isinstance(depth_metrics, dict) else {},
        "raw_clob_json": raw_clob_json if isinstance(raw_clob_json, dict) else {},
        "updated_at_utc": row["updated_at_utc"],
        "question": row["question"],
        "condition_id": row["condition_id"],
        "status": row["status"],
        "db_path": str(db_path),
    }


def _load_ws_snapshots_for_tokens(tokens: List[str]) -> Dict[str, Any]:
    token_list = [str(token or "").strip() for token in tokens if str(token or "").strip()]
    if not token_list:
        return {}
    by_token: Dict[str, Any] = {}
    for db_path in _ws_snapshot_db_paths():
        if not db_path.exists():
            continue
        conn = None
        try:
            conn = sqlite3.connect(str(db_path), timeout=2.0)
            conn.row_factory = sqlite3.Row
            tables = {str(row[0]) for row in conn.execute("select name from sqlite_master where type='table' order by name").fetchall()}
            if "markets_state" not in tables:
                continue
            cols = {str(row[1]) for row in conn.execute('PRAGMA table_info("markets_state")').fetchall()}
            if "clobTokenId" not in cols:
                continue
            placeholders = ", ".join(["?"] * len(token_list))
            rows = conn.execute(
                f"""
                SELECT clobTokenId, target_option_json, target_price, market_json, depth_metrics_json,
                       raw_clob_json, updated_at_utc, question, condition_id, status
                FROM "markets_state"
                WHERE clobTokenId IN ({placeholders})
                """,
                token_list,
            ).fetchall()
            for row in rows:
                token = str(row["clobTokenId"] or "").strip()
                candidate = _snapshot_from_ws_row(row, db_path, token)
                if not candidate:
                    continue
                existing = by_token.get(token)
                existing_dt = _parse_iso_datetime((existing or {}).get("updated_at_utc")) if existing else None
                candidate_dt = _parse_iso_datetime(candidate.get("updated_at_utc"))
                if existing is None or (candidate_dt and (existing_dt is None or candidate_dt > existing_dt)):
                    by_token[token] = candidate
        except sqlite3.Error:
            continue
        finally:
            if conn is not None:
                conn.close()
    return by_token


def _load_latest_condition_snapshot(condition_id: str) -> Dict[str, Any] | None:
    cid = str(condition_id or "").strip()
    if not cid:
        return None
    best: Dict[str, Any] | None = None
    best_dt = None
    for db_path in _ws_snapshot_db_paths():
        if not db_path.exists():
            continue
        conn = None
        try:
            conn = sqlite3.connect(str(db_path), timeout=2.0)
            conn.row_factory = sqlite3.Row
            tables = {str(row[0]) for row in conn.execute("select name from sqlite_master where type='table'").fetchall()}
            if "markets_state" not in tables:
                continue
            cols = {str(row[1]) for row in conn.execute('PRAGMA table_info("markets_state")').fetchall()}
            if "condition_id" not in cols:
                continue
            rows = conn.execute(
                """
                SELECT clobTokenId, target_option_json, target_price, market_json, depth_metrics_json,
                       raw_clob_json, updated_at_utc, question, condition_id, status
                FROM "markets_state"
                WHERE condition_id = ?
                ORDER BY updated_at_utc DESC
                LIMIT 4
                """,
                (cid,),
            ).fetchall()
            for row in rows:
                token = str(row["clobTokenId"] or "").strip()
                candidate = _snapshot_from_ws_row(row, db_path, token)
                if not candidate:
                    continue
                candidate_dt = _parse_iso_datetime(candidate.get("updated_at_utc"))
                if candidate_dt and (datetime.now(timezone.utc) - candidate_dt.astimezone(timezone.utc)).total_seconds() > _WS_FRESHNESS_SECONDS:
                    continue
                if best is None or (candidate_dt and (best_dt is None or candidate_dt > best_dt)):
                    best = candidate
                    best_dt = candidate_dt
        except sqlite3.Error:
            continue
        finally:
            if conn is not None:
                conn.close()
    return best


def _select_strategy_ws_snapshot(yes_token: str, no_token: str, condition_id: str = "") -> Dict[str, Any] | None:
    tokens = [str(yes_token or "").strip(), str(no_token or "").strip()]
    tokens = [token for token in tokens if token]
    if not tokens:
        snapshot = _load_latest_condition_snapshot(condition_id)
        return snapshot
    by_token = _load_ws_snapshots_for_tokens(tokens)
    if not by_token:
        by_token = _load_ws_snapshot_map()
    for token_id in tokens:
        if not token_id:
            continue
        snapshot = by_token.get(token_id)
        if not snapshot:
            continue
        updated_at = _parse_iso_datetime(snapshot.get("updated_at_utc"))
        if updated_at is None:
            continue
        if (datetime.now(timezone.utc) - updated_at.astimezone(timezone.utc)).total_seconds() > _WS_FRESHNESS_SECONDS:
            continue
        return snapshot
    return None


def _resolve_strategy_market_prices(
    matched_market: Dict[str, Any] | None,
    ws_snapshot: Dict[str, Any] | None = None,
    yes_token: str | None = None,
    no_token: str | None = None,
) -> Dict[str, Any]:
    try:
        clob_quotes = fetch_binary_orderbook_quotes(str(yes_token or ""), str(no_token or ""))
    except Exception:
        clob_quotes = {}
    if clob_quotes and any(
        clob_quotes.get(key) is not None
        for key in ("yes_bid", "yes_ask", "no_bid", "no_ask")
    ):
        yes_bid = _safe_float(clob_quotes.get("yes_bid"))
        yes_ask = _safe_float(clob_quotes.get("yes_ask"))
        no_bid = _safe_float(clob_quotes.get("no_bid"))
        no_ask = _safe_float(clob_quotes.get("no_ask"))
        return {
            "yes_bid": yes_bid,
            "yes_ask": yes_ask,
            "no_bid": no_bid,
            "no_ask": no_ask,
            "yes_last_price": yes_ask if yes_ask is not None else yes_bid,
            "no_last_price": no_ask if no_ask is not None else no_bid,
            "price_source": "clob_book",
            "updated_at": clob_quotes.get("updated_at"),
            "snapshot_db_path": None,
        }

    if ws_snapshot:
        best_bid = _safe_float((ws_snapshot.get("depth_metrics") or {}).get("best_bid"))
        best_ask = _safe_float((ws_snapshot.get("depth_metrics") or {}).get("best_ask"))
        raw_clob = (ws_snapshot.get("raw_clob_json") or {}) if isinstance(ws_snapshot.get("raw_clob_json"), dict) else {}
        market_json = (ws_snapshot.get("market_json") or {}) if isinstance(ws_snapshot.get("market_json"), dict) else {}
        raw_last = _safe_float(
            raw_clob.get("price")
            or raw_clob.get("last_trade_price")
            or raw_clob.get("lastPrice")
            or ws_snapshot.get("target_price")
            or market_json.get("lastTradePrice")
        )
        if best_bid is not None or best_ask is not None:
            outcome_side = str(ws_snapshot.get("outcome_side") or "").strip().lower()
            if outcome_side == "no":
                no_bid = best_bid
                no_ask = best_ask
                yes_bid = None
                yes_ask = None
                no_last = raw_last
                yes_last = _clamp_binary_price(1.0 - raw_last) if raw_last is not None else None
            else:
                yes_bid = best_bid
                yes_ask = best_ask
                no_bid = None
                no_ask = None
                yes_last = raw_last
                no_last = _clamp_binary_price(1.0 - raw_last) if raw_last is not None else None
            return {
                "yes_bid": yes_bid,
                "yes_ask": yes_ask,
                "no_bid": no_bid,
                "no_ask": no_ask,
                "yes_last_price": yes_last,
                "no_last_price": no_last,
                "price_source": "websocket",
                "updated_at": ws_snapshot.get("updated_at_utc"),
                "snapshot_db_path": ws_snapshot.get("db_path"),
            }

    if not matched_market:
        return {
            "yes_bid": None,
            "yes_ask": None,
            "no_bid": None,
            "no_ask": None,
            "yes_last_price": None,
            "no_last_price": None,
            "price_source": None,
            "updated_at": None,
            "snapshot_db_path": None,
        }

    yes_bid = _safe_float(matched_market.get("best_bid"))
    yes_ask = _safe_float(matched_market.get("best_ask"))
    yes_last = _safe_float(matched_market.get("yes_price"))
    no_last = _safe_float(matched_market.get("no_price"))

    no_bid = _safe_float(matched_market.get("no_bid"))
    no_ask = _safe_float(matched_market.get("no_ask"))

    return {
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_bid": no_bid,
        "no_ask": no_ask,
        "yes_last_price": yes_last,
        "no_last_price": no_last,
        "price_source": "market",
        "updated_at": ((matched_market or {}).get("raw") or {}).get("updatedAt"),
        "snapshot_db_path": None,
    }


def _inject_virtual_positions(item: Dict[str, Any], strategy_id: int) -> None:
    """For Virtual mode strategies, read positions from strategy_virtual_positions."""
    try:
        conn = strategy_data_source.connect(readonly=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM strategy_virtual_positions WHERE strategy_id = ?",
            (strategy_id,),
        ).fetchall()
        conn.close()
        for r in rows:
            d = dict(r)
            side = str(d.get("side") or "").upper()
            qty = float(d.get("qty") or 0.0)
            avg_price = d.get("avg_price")
            if side == "YES":
                item["Yes_now_qty"] = qty
                item["Yes_avg_cost"] = avg_price
            elif side == "NO":
                item["No_now_qty"] = qty
                item["No_avg_cost"] = avg_price
        item["position_source"] = "virtual"
    except Exception:
        pass


_VIRTUAL_FEE_RATES: Dict[str, float] = {
    "crypto": 0.072,
    "sports": 0.03,
    "politics": 0.04,
    "finance": 0.04,
    "tech": 0.04,
    "mentions": 0.04,
    "economics": 0.05,
    "culture": 0.05,
    "weather": 0.05,
    "other": 0.05,
    "geopolitics": 0.0,
}
_DEFAULT_VIRTUAL_FEE_RATE = 0.05


def _virtual_fee_rate(market_category: Any) -> float:
    category = str(market_category or "").strip().lower()
    return _VIRTUAL_FEE_RATES.get(category, _DEFAULT_VIRTUAL_FEE_RATE)


def _virtual_fee(qty: float, price: float, fee_rate: float) -> float:
    if qty <= 0 or price <= 0:
        return 0.0
    return qty * fee_rate * price * (1.0 - price)


def _load_virtual_account(strategy_id: int) -> Dict[str, Any] | None:
    try:
        conn = strategy_data_source.connect(readonly=True)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM strategy_virtual_account WHERE strategy_id = ?",
            (int(strategy_id),),
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception:
        return None


def _virtual_total_pnl_from_account(
    strategy_item: Dict[str, Any],
    strategy_id: int,
    positions: List[Dict[str, Any]] | None = None,
) -> None:
    """Overlay Virtual summary PnL with account-equity PnL.

    Existing per-row chart PnL is an unrealized mark. The workspace and dashboard
    summary need account PnL so closed round trips keep their realized loss/fees.
    """
    if str(strategy_item.get("state") or "").strip().lower() != "virtual":
        return
    account = _load_virtual_account(strategy_id)
    if not account:
        return

    fee_rate = _virtual_fee_rate(strategy_item.get("market_category"))
    if positions is None:
        positions = [
            {
                "side": "YES",
                "qty": _safe_float(strategy_item.get("yes_qty")) or 0.0,
                "avg": _safe_float(strategy_item.get("yes_avg")),
                "bid": _safe_float(strategy_item.get("yes_bid")),
                "ask": _safe_float(strategy_item.get("yes_ask")),
            },
            {
                "side": "NO",
                "qty": _safe_float(strategy_item.get("no_qty")) or 0.0,
                "avg": _safe_float(strategy_item.get("no_avg")),
                "bid": _safe_float(strategy_item.get("no_bid")),
                "ask": _safe_float(strategy_item.get("no_ask")),
            },
        ]

    open_cost = 0.0
    liquidation_value = 0.0
    estimated_exit_fees = 0.0
    for pos in positions:
        qty = _safe_float(pos.get("qty")) or 0.0
        avg = _safe_float(pos.get("avg"))
        if qty <= 0 or avg is None:
            continue
        mark = _safe_float(pos.get("bid"))
        if mark is None:
            mark = _safe_float(pos.get("ask"))
        if mark is None:
            continue
        open_cost += qty * avg
        liquidation_value += qty * mark
        estimated_exit_fees += _virtual_fee(qty, mark, fee_rate)

    initial_cash = _safe_float(account.get("initial_cash")) or 0.0
    cash = _safe_float(account.get("cash")) or 0.0
    realized_pnl = _safe_float(account.get("realized_pnl")) or 0.0
    fees_paid = _safe_float(account.get("total_fees_paid")) or 0.0
    unrealized_pnl = liquidation_value - open_cost - estimated_exit_fees
    equity = cash + liquidation_value - estimated_exit_fees
    total_pnl = equity - initial_cash

    strategy_item.update(
        {
            "strategy_pnl": total_pnl,
            "virtual_total_pnl": total_pnl,
            "virtual_unrealized_pnl": unrealized_pnl,
            "virtual_realized_pnl": realized_pnl,
            "virtual_fees_paid": fees_paid,
            "virtual_cash": cash,
            "virtual_equity": equity,
            "virtual_initial_cash": initial_cash,
            "virtual_liquidation_value": liquidation_value,
            "virtual_estimated_exit_fees": estimated_exit_fees,
            "pnl_source": "virtual_account_equity",
        }
    )


def _recompute_strategy_metrics(strategy_item: Dict[str, Any]) -> None:
    yes_qty = _safe_float(strategy_item.get("yes_qty")) or 0.0
    no_qty = _safe_float(strategy_item.get("no_qty")) or 0.0
    yes_avg = _safe_float(strategy_item.get("yes_avg"))
    no_avg = _safe_float(strategy_item.get("no_avg"))
    yes_price = _safe_float(strategy_item.get("yes_ask"))
    no_price = _safe_float(strategy_item.get("no_ask"))

    yes_position, no_position = calculate_position_pcts(strategy_item, yes_qty, yes_price, no_qty, no_price)
    strategy_item["yes_position"] = yes_position
    strategy_item["no_position"] = no_position
    strategy_item["yes_current_pct"] = yes_position
    strategy_item["no_current_pct"] = no_position
    strategy_item["strategy_bankroll"] = resolve_strategy_bankroll(strategy_item)

    pnl_yes = ((yes_price or 0.0) - yes_avg) * yes_qty if yes_avg is not None else 0.0
    pnl_no = ((no_price or 0.0) - no_avg) * no_qty if no_avg is not None else 0.0
    strategy_item["strategy_pnl"] = pnl_yes + pnl_no
    strategy_item["unrealized_pnl"] = pnl_yes + pnl_no
    row_id = _safe_float(strategy_item.get("strategy_id") or strategy_item.get("row_id"))
    if row_id is not None:
        _virtual_total_pnl_from_account(strategy_item, int(row_id))


def _strategy_leg_side(yes_qty: float, no_qty: float) -> str:
    if yes_qty > 0 and no_qty > 0:
        return "Both"
    if yes_qty > 0:
        return "YES"
    if no_qty > 0:
        return "NO"
    return "None"


def _strategy_leg_exposure(yes_qty: float, yes_avg: float | None, no_qty: float, no_avg: float | None) -> float:
    return (yes_qty * (yes_avg or 0.0)) + (no_qty * (no_avg or 0.0))


def _strategy_leg_mark(side: str, yes_price: float | None, no_price: float | None) -> float | None:
    if side == "YES":
        return yes_price
    if side == "NO":
        return no_price
    if side == "Both":
        return yes_price if yes_price is not None else no_price
    return None


def _summarize_leg_params(raw_params: Any) -> tuple[Dict[str, Any], str]:
    params: Dict[str, Any] = {}
    if isinstance(raw_params, dict):
        params = raw_params
    elif raw_params not in (None, ""):
        try:
            parsed = json.loads(str(raw_params))
            if isinstance(parsed, dict):
                params = parsed
        except (TypeError, ValueError):
            text = str(raw_params).strip()
            return {}, text[:120] if text else "-"

    if not params:
        return {}, "-"

    parts = []
    for key, value in params.items():
        if value in (None, ""):
            continue
        text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
        parts.append(f"{key}={text}")
        if len(parts) >= 4:
            break
    return params, ", ".join(parts) if parts else "-"


def _build_strategy_leg_snapshot(
    strategy: Dict[str, Any],
    leg: Dict[str, Any],
    matched_market: Dict[str, Any] | None,
    row_id: int,
    include_realtime_prices: bool,
) -> Dict[str, Any]:
    leg_index = int(leg.get("leg_index") or 0)
    flat = strategy_data_source.strategy_to_flat_dict(strategy, leg_index=leg_index)
    flat_row_id = flat.pop("row_id", row_id)
    # Virtual 模式：注入虚拟持仓
    if str(strategy.get("state") or "").strip().lower() == "virtual":
        _inject_virtual_positions(flat, int(strategy.get("strategy_id") or row_id))
    item = _build_strategy_item(flat, matched_market, row_id=flat_row_id, include_realtime_prices=include_realtime_prices)
    yes_qty = _safe_float(item.get("yes_qty")) or 0.0
    no_qty = _safe_float(item.get("no_qty")) or 0.0
    yes_avg = _safe_float(item.get("yes_avg"))
    no_avg = _safe_float(item.get("no_avg"))
    yes_price = _safe_float(item.get("yes_ask"))
    no_price = _safe_float(item.get("no_ask"))
    side = _strategy_leg_side(yes_qty, no_qty)
    exposure = _strategy_leg_exposure(yes_qty, yes_avg, no_qty, no_avg)
    pnl = 0.0
    if yes_avg is not None:
        pnl += ((yes_price or 0.0) - yes_avg) * yes_qty
    if no_avg is not None:
        pnl += ((no_price or 0.0) - no_avg) * no_qty
    params, params_summary = _summarize_leg_params(leg.get("params_json"))
    market_raw = (matched_market or {}).get("raw") or {}
    question = (
        item.get("question")
        or (matched_market or {}).get("question")
        or leg.get("condition_id")
        or leg.get("yes_token")
        or leg.get("no_token")
        or "-"
    )
    updated_at = leg.get("position_updated_at") or leg.get("updated_at_utc") or item.get("market_updated_at")
    return {
        "leg": leg_index + 1,
        "leg_index": leg_index,
        "question": question,
        "condition_id": item.get("condition_id") or leg.get("condition_id"),
        "slug": (matched_market or {}).get("slug") or market_raw.get("slug") or item.get("slug"),
        "event_slug": (matched_market or {}).get("event_slug") or market_raw.get("eventSlug") or market_raw.get("event_slug") or item.get("event_slug"),
        "group_item_title": (matched_market or {}).get("group_item_title") or market_raw.get("groupItemTitle") or item.get("group_item_title"),
        "url": (matched_market or {}).get("url") or market_raw.get("url"),
        "yes_token": leg.get("yes_token") or item.get("yes_token"),
        "no_token": leg.get("no_token") or item.get("no_token"),
        "params": params,
        "params_summary": params_summary,
        "side": side,
        "yes_qty": yes_qty,
        "no_qty": no_qty,
        "yes_avg": yes_avg,
        "no_avg": no_avg,
        "yes_mark": yes_price,
        "no_mark": no_price,
        "yes_bid": _safe_float(item.get("yes_bid")),
        "no_bid": _safe_float(item.get("no_bid")),
        "exposure": exposure,
        "pnl": pnl,
        "updated_at": updated_at,
    }


def _recent_strategy_events(strategy_id: int, limit: int = 5) -> List[Dict[str, Any]]:
    try:
        conn = strategy_data_source.connect(readonly=True)
    except Exception:
        return []
    try:
        rows = conn.execute(
            """SELECT event_type, content, repeat_count, last_seen_utc, created_at_utc
               FROM strategy_virtual_events
               WHERE strategy_id = ?
               ORDER BY COALESCE(NULLIF(last_seen_utc, ''), created_at_utc) DESC, id DESC
               LIMIT ?""",
            (strategy_id, max(1, int(limit))),
        ).fetchall()
        return [
            {
                "time": row["last_seen_utc"] or row["created_at_utc"],
                "type": row["event_type"],
                "content": row["content"],
                "repeat_count": row["repeat_count"],
            }
            for row in rows
        ]
    except Exception:
        return []
    finally:
        conn.close()


def _decorate_strategy_overview(
    strategy_item: Dict[str, Any],
    strategy: Dict[str, Any],
    active_index: Dict[str, Any],
    dictionary_index: Dict[str, Any],
    include_realtime_prices: bool,
) -> Dict[str, Any]:
    row_id = int(strategy.get("strategy_id") or strategy_item.get("row_id") or 0)
    legs = strategy.get("legs") or []
    snapshots: List[Dict[str, Any]] = []
    for leg in legs:
        flat = strategy_data_source.strategy_to_flat_dict(strategy, leg_index=int(leg.get("leg_index") or 0))
        _flat_row_id = flat.pop("row_id", None)
        matched_market, _source = _match_strategy_market_with_source(flat, active_index, dictionary_index)
        snapshots.append(_build_strategy_leg_snapshot(strategy, leg, matched_market, row_id, include_realtime_prices))

    total_exposure = sum(_safe_float(item.get("exposure")) or 0.0 for item in snapshots)
    total_pnl = sum(_safe_float(item.get("pnl")) or 0.0 for item in snapshots)
    events = _recent_strategy_events(row_id, limit=30)
    last_event = events[0] if events else None
    last_action = (last_event or {}).get("content") or "No signal"
    updated_candidates = [
        strategy.get("updated_at_utc"),
        strategy_item.get("market_updated_at"),
        *[item.get("updated_at") for item in snapshots],
        (last_event or {}).get("time"),
    ]
    strategy_item.update(
        {
            "strategy_id": row_id,
            "strategy_code": strategy.get("strategy_code") or strategy_item.get("raw", {}).get("Code") or "",
            "strategy_name": strategy.get("strategy_name") or strategy_item.get("strategy") or "",
            "legs_count": len(legs) or 1,
            "legs_snapshot": snapshots,
            "exposure": total_exposure,
            "strategy_pnl": total_pnl if snapshots else strategy_item.get("strategy_pnl"),
            "recent_events": events,
            "last_action": last_action,
            "last_action_type": (last_event or {}).get("type") or "",
            "updated_at": next((value for value in reversed(updated_candidates) if value), None),
        }
    )
    virtual_positions = []
    for snap in snapshots:
        virtual_positions.extend(
            [
                {
                    "side": "YES",
                    "qty": snap.get("yes_qty"),
                    "avg": snap.get("yes_avg"),
                    "bid": snap.get("yes_bid"),
                    "ask": snap.get("yes_mark"),
                },
                {
                    "side": "NO",
                    "qty": snap.get("no_qty"),
                    "avg": snap.get("no_avg"),
                    "bid": snap.get("no_bid"),
                    "ask": snap.get("no_mark"),
                },
            ]
        )
    _virtual_total_pnl_from_account(strategy_item, row_id, virtual_positions)
    return strategy_item


def _get_local_order_map_cached(now: float | None = None) -> Dict[str, Any]:
    current = now if now is not None else time.time()
    local_cache_ts = float(_LOCAL_ORDER_CACHE.get("ts") or 0.0)
    if local_cache_ts and current - local_cache_ts < _LOCAL_ORDER_TTL_SECONDS:
        return dict(_LOCAL_ORDER_CACHE.get("data") or {})
    try:
        local_map = fetch_local_order_map()
    except Exception:
        local_map = {}
    _LOCAL_ORDER_CACHE.update(ts=current, data=dict(local_map))
    return dict(local_map)


def _start_live_position_refresh() -> bool:
    global _LIVE_POS_REFRESH_RUNNING
    with _LIVE_POS_REFRESH_LOCK:
        if _LIVE_POS_REFRESH_RUNNING:
            return False
        _LIVE_POS_REFRESH_RUNNING = True

    def worker() -> None:
        global _LIVE_POS_REFRESH_RUNNING
        started = time.perf_counter()
        now = time.time()
        local_map = _get_local_order_map_cached(now)
        remote_map: Dict[str, Any] = {}
        remote_ok = False
        err: str | None = None
        try:
            remote_map = fetch_remote_position_map()
            remote_ok = True
        except Exception as exc:  # pragma: no cover - network
            err = str(exc)
        _LIVE_POS_CACHE.update(
            ts=now,
            remote=remote_map,
            local=local_map,
            remote_ok=remote_ok,
            error=err,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        print(
            f"[SV][live_pos] async_refresh cost={elapsed_ms:.1f}ms remote_ok={remote_ok} "
            f"remote_tokens={len(remote_map)} local_tokens={len(local_map)} error={err or '-'}"
        )
        with _LIVE_POS_REFRESH_LOCK:
            _LIVE_POS_REFRESH_RUNNING = False

    threading.Thread(target=worker, daemon=True, name="live-position-refresh").start()
    return True


def _get_live_position_cache(allow_remote: bool = True) -> Dict[str, Any]:
    now = time.time()
    ts = float(_LIVE_POS_CACHE.get("ts") or 0.0)
    if allow_remote and ts and now - ts < _LIVE_POS_TTL_SECONDS:
        return _LIVE_POS_CACHE

    local_map = _get_local_order_map_cached(now)

    if not allow_remote:
        if ts and now - ts < _LIVE_POS_STALE_OK_SECONDS and bool(_LIVE_POS_CACHE.get("remote_ok")):
            return {
                "ts": ts,
                "remote": dict(_LIVE_POS_CACHE.get("remote") or {}),
                "local": local_map,
                "remote_ok": True,
                "error": _LIVE_POS_CACHE.get("error"),
            }
        _start_live_position_refresh()
        return {
            "ts": ts,
            "remote": {},
            "local": local_map,
            "remote_ok": False,
            "error": "remote_position_lookup_background",
        }

    remote_map: Dict[str, Any] = {}
    remote_ok = False
    err: str | None = None
    try:
        remote_map = fetch_remote_position_map()
        remote_ok = True
    except Exception as exc:  # pragma: no cover - network
        err = str(exc)

    _LIVE_POS_CACHE.update(
        ts=now,
        remote=remote_map,
        local=local_map,
        remote_ok=remote_ok,
        error=err,
    )
    return _LIVE_POS_CACHE


def _has_local_position_payload(strategy_item: Dict[str, Any]) -> bool:
    yes_qty = _safe_float(strategy_item.get("yes_qty")) or 0.0
    no_qty = _safe_float(strategy_item.get("no_qty")) or 0.0
    yes_avg = _safe_float(strategy_item.get("yes_avg"))
    no_avg = _safe_float(strategy_item.get("no_avg"))
    return yes_qty > 0 or no_qty > 0 or yes_avg is not None or no_avg is not None


def _apply_local_order_position_estimate(strategy_item: Dict[str, Any], local_map: Dict[str, Any]) -> bool:
    applied = False
    for side in ("yes", "no"):
        qty_key = f"{side}_qty"
        avg_key = f"{side}_avg"
        current_qty = _safe_float(strategy_item.get(qty_key)) or 0.0
        current_avg = _safe_float(strategy_item.get(avg_key))
        if current_qty > 0 or current_avg is not None:
            continue
        token = str(strategy_item.get(f"{side}_token") or "").strip()
        if not token:
            continue
        entry = (local_map or {}).get(token) or {}
        buy_qty = float(entry.get("buy_qty") or 0.0)
        sell_qty = float(entry.get("sell_qty") or 0.0)
        buy_cost = float(entry.get("buy_cost") or 0.0)
        net_qty = max(0.0, buy_qty - sell_qty)
        if net_qty <= 0:
            continue
        avg_price = (buy_cost / buy_qty) if buy_qty > 0 else None
        strategy_item[qty_key] = net_qty
        strategy_item[avg_key] = avg_price
        applied = True
    if applied:
        strategy_item["position_source"] = "local_order_db_estimate"
    return applied


def _apply_wallet_live_positions(strategy_item: Dict[str, Any], ctx: Dict[str, Any]) -> None:
    if not ctx.get("remote_ok"):
        if _apply_local_order_position_estimate(strategy_item, ctx.get("local") or {}):
            return
        if _has_local_position_payload(strategy_item):
            strategy_item["position_source"] = strategy_item.get("position_source") or "monitoring_db"
            return
        fallback_cache = ctx.setdefault("fallback_cache", {})
        row_id = strategy_item.get("row_id")
        fallback = None
        if row_id is not None:
            cache_key = int(row_id)
            if cache_key not in fallback_cache:
                fallback_cache[cache_key] = load_latest_valid_position_snapshot(strategy_item)
            fallback = fallback_cache.get(cache_key)
        if fallback:
            strategy_item["yes_qty"] = fallback.get("yes_qty")
            strategy_item["no_qty"] = fallback.get("no_qty")
            strategy_item["yes_avg"] = fallback.get("yes_avg")
            strategy_item["no_avg"] = fallback.get("no_avg")
            previous_source = str(fallback.get("position_source") or "").strip() or "previous_valid_snapshot"
            strategy_item["position_source"] = f"fallback:{previous_source}"
            strategy_item["position_fallback_updated_at"] = fallback.get("updated_at_utc")
            return
        strategy_item["position_source"] = strategy_item.get("position_source") or "monitoring_db_wallet_api_unavailable"
        return
    apply_live_position_overrides(strategy_item, ctx.get("remote") or {}, ctx.get("local") or {})


def _strategy_db_info() -> tuple[Path, str]:
    settings = load_web_settings()
    db_path = _resolve_workspace_db_path(settings.get("strategy_monitoring_db_path", ""), "PolyMarketMonitoring.db")
    preferred_table = str(settings.get("strategy_monitoring_table", "monitoring")).strip() or "monitoring"
    return db_path, preferred_table


def _strategy_storage_db_path() -> Path:
    settings = load_web_settings()
    value = get_market_realtime_db_path(settings)
    return _resolve_workspace_db_path(value, "polymarket_realtime.db")


def _load_strategy_market_index() -> Dict[str, Dict[str, Any]]:
    active_index = _cached_market_index_from_markets(_known_markets())
    dictionary_index = _load_dictionary_market_index()
    by_condition_id = dict(dictionary_index.get("by_condition_id") or {})
    by_condition_id.update(active_index.get("by_condition_id") or {})
    by_token = dict(dictionary_index.get("by_token") or {})
    by_token.update(active_index.get("by_token") or {})
    return {"by_condition_id": by_condition_id, "by_token": by_token}


def _match_strategy_market_with_source(
    item: Dict[str, Any],
    active_index: Dict[str, Dict[str, Any]],
    dictionary_index: Dict[str, Dict[str, Any]],
) -> tuple[Dict[str, Any] | None, str]:
    condition_id = str(item.get("condition_id") or "").strip()
    if condition_id:
        matched = (active_index.get("by_condition_id") or {}).get(condition_id)
        if matched:
            return matched, "active"
        matched = (dictionary_index.get("by_condition_id") or {}).get(condition_id)
        if matched:
            return matched, "dictionary"

    for token in [str(item.get("yes_token") or "").strip(), str(item.get("no_token") or "").strip()]:
        if not token:
            continue
        matched = (active_index.get("by_token") or {}).get(token)
        if matched:
            return matched, "active"
        matched = (dictionary_index.get("by_token") or {}).get(token)
        if matched:
            return matched, "dictionary"
    return None, "unmatched"


def _match_strategy_market(item: Dict[str, Any], market_index: Dict[str, Dict[str, Any]]) -> Dict[str, Any] | None:
    condition_id = str(item.get("condition_id") or "").strip()
    if condition_id:
        matched = (market_index.get("by_condition_id") or {}).get(condition_id)
        if matched:
            return matched

    for token in [str(item.get("yes_token") or "").strip(), str(item.get("no_token") or "").strip()]:
        if not token:
            continue
        matched = (market_index.get("by_token") or {}).get(token)
        if matched:
            return matched
    return None


def _persist_enriched_monitoring_row(
    conn: sqlite3.Connection,
    table: str,
    row_id: int,
    before: Dict[str, Any],
    after: Dict[str, Any],
) -> None:
    updates: list[str] = []
    values: list[Any] = []

    candidate_fields = ["yes_token", "no_token", "condition_id", "question"]

    cols = {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}
    for field in candidate_fields:
        if field not in cols:
            continue
        before_v = str(before.get(field) or "").strip()
        after_v = str(after.get(field) or "").strip()
        if (not before_v) and after_v:
            updates.append(f'"{field}" = ?')
            values.append(after_v)

    if not updates:
        return

    values.append(row_id)
    conn.execute(f'UPDATE "{table}" SET {", ".join(updates)} WHERE rowid = ?', values)


def _load_strategy_monitoring_rows(
    limit: int | None = None,
    enrich_tokens: bool = True,
    allow_remote_positions: bool = True,
    include_realtime_prices: bool = False,
) -> Dict[str, Any]:
    t0 = time.perf_counter()
    db_path, preferred_table = _strategy_db_info()
    _ensure_sqlite_file(db_path)

    # --- NEW PATH: if strategy_registry has data, use unified data source ---
    try:
        _new_strategies = strategy_data_source.list_strategies()
    except Exception:
        _new_strategies = []
    if _new_strategies:
        flat_rows = [strategy_data_source.strategy_to_flat_dict(s) for s in _new_strategies]
        # Virtual 模式：注入虚拟持仓到 flat dict
        for i, s in enumerate(_new_strategies):
            if str(s.get("state") or "").strip().lower() == "virtual":
                _inject_virtual_positions(flat_rows[i], int(s.get("strategy_id") or 0))
        if limit is not None:
            flat_rows = flat_rows[:max(1, int(limit))]
        live_ctx = _get_live_position_cache(allow_remote=allow_remote_positions)
        active_index = _cached_market_index_from_markets(_known_markets())
        dictionary_index = _load_dictionary_market_index()
        items: List[Dict[str, Any]] = []
        strategies_by_id = {int(s.get("strategy_id")): s for s in _new_strategies if s.get("strategy_id") is not None}
        for item in flat_rows:
            row_id = item.pop("row_id", None)
            matched_market, _match_source = _match_strategy_market_with_source(item, active_index, dictionary_index)
            if enrich_tokens and _has_minimum_binary_identity(item, matched_market) and not _has_binary_yes_no_tokens(item, matched_market):
                item, matched_market = _enrich_monitoring_row_tokens(item, matched_market, {})
            strategy_item = _build_strategy_item(
                item, matched_market, row_id=row_id, include_realtime_prices=include_realtime_prices,
            )
            _apply_wallet_live_positions(strategy_item, live_ctx)
            _recompute_strategy_metrics(strategy_item)
            strategy_item["display_name"] = _build_strategy_display_name(strategy_item, row_id=row_id)
            source_strategy = strategies_by_id.get(int(row_id or 0))
            if source_strategy:
                strategy_item = _decorate_strategy_overview(
                    strategy_item,
                    source_strategy,
                    active_index,
                    dictionary_index,
                    include_realtime_prices,
                )
            items.append(strategy_item)
        total_ms = (time.perf_counter() - t0) * 1000
        print(f"[SV][monitoring_rows] new_path total={total_ms:.1f}ms rows={len(items)}")
        return {
            "ok": True,
            "data": items,
            "db_path": str(strategy_data_source.get_db_path()),
            "table": "strategy_registry",
            "source_statuses": {
                "strategy_db": _source_status("good", None, count=len(items)),
                "market_lookup": _source_status("good", None, count=sum(1 for i in items if i.get("condition_id"))),
            },
        }
    # --- END NEW PATH ---

    try:
        conn, actual_table = _strategy_conn_and_table()
    except ValueError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "data": [],
            "db_path": str(db_path),
            "table": preferred_table,
            "source_statuses": {
                "strategy_db": _source_status("error", str(exc)),
                "market_lookup": _source_status("pending", None, count=0),
            },
        }

    try:
        _ensure_strategy_editable_columns(conn, actual_table)
        cur = conn.cursor()
        cols = [str(row[1]) for row in cur.execute(f'PRAGMA table_info("{actual_table}")').fetchall()]
        limit_sql = f" LIMIT {max(1, int(limit))}" if limit is not None else ""
        t_query0 = time.perf_counter()
        rows = cur.execute(f'SELECT rowid, * FROM "{actual_table}"{limit_sql}').fetchall()
        t_query1 = time.perf_counter()
        t_index0 = time.perf_counter()
        active_index = _cached_market_index_from_markets(_known_markets())
        dictionary_index = _load_dictionary_market_index()
        t_index1 = time.perf_counter()
        t_live0 = time.perf_counter()
        live_ctx = _get_live_position_cache(allow_remote=allow_remote_positions)
        t_live1 = time.perf_counter()
        gamma_resolve_cache: Dict[str, Any] = {}

        items: List[Dict[str, Any]] = []
        market_match_count = 0
        active_match_count = 0
        dictionary_match_count = 0
        unmatched_count = 0
        gamma_candidate_count = 0
        gamma_enriched_count = 0
        missing_identity_count = 0
        slow_rows: List[str] = []
        print(
            "[SV][monitoring_rows] "
            f"query={(t_query1 - t_query0) * 1000:.1f}ms rows={len(rows)} "
            f"index={(t_index1 - t_index0) * 1000:.1f}ms active_cond={len(active_index.get('by_condition_id') or {})} "
            f"active_token={len(active_index.get('by_token') or {})} dict_cond={len(dictionary_index.get('by_condition_id') or {})} "
            f"dict_token={len(dictionary_index.get('by_token') or {})} "
            f"live_ctx={(t_live1 - t_live0) * 1000:.1f}ms remote_ok={bool(live_ctx.get('remote_ok'))} "
            f"local_order_tokens={len(live_ctx.get('local') or {})} enrich_tokens={enrich_tokens} "
            f"allow_remote_positions={allow_remote_positions} include_realtime_prices={include_realtime_prices}"
        )
        for raw_row in rows:
            row_t0 = time.perf_counter()
            row_id = raw_row[0]
            item = _to_row_dict(cols, raw_row[1:])
            matched_market, match_source = _match_strategy_market_with_source(item, active_index, dictionary_index)
            if match_source == "active":
                active_match_count += 1
            elif match_source == "dictionary":
                dictionary_match_count += 1
            else:
                unmatched_count += 1
            if not _has_minimum_binary_identity(item, matched_market):
                missing_identity_count += 1
            if enrich_tokens:
                if _has_minimum_binary_identity(item, matched_market) and not _has_binary_yes_no_tokens(item, matched_market):
                    gamma_candidate_count += 1
                raw_before_enrich = dict(item)
                item, matched_market = _enrich_monitoring_row_tokens(item, matched_market, gamma_resolve_cache)
                _persist_enriched_monitoring_row(conn, actual_table, row_id, raw_before_enrich, item)
                if str(item.get("_binary_identity_status") or "").strip() == "enriched_from_gamma":
                    gamma_enriched_count += 1
            strategy_item = _build_strategy_item(
                item,
                matched_market,
                row_id=row_id,
                include_realtime_prices=include_realtime_prices,
            )
            _apply_wallet_live_positions(strategy_item, live_ctx)
            if matched_market:
                market_match_count += 1
            _recompute_strategy_metrics(strategy_item)
            strategy_item["display_name"] = _build_strategy_display_name(strategy_item, row_id=row_id)
            items.append(strategy_item)
            row_ms = (time.perf_counter() - row_t0) * 1000
            if row_ms >= 500:
                slow_rows.append(
                    f"row_id={row_id} source={match_source} cost={row_ms:.1f}ms "
                    f"binary={strategy_item.get('binary_identity_status')} condition_id={strategy_item.get('condition_id') or '-'} "
                    f"yes_token={'Y' if strategy_item.get('yes_token') else 'N'} no_token={'Y' if strategy_item.get('no_token') else 'N'}"
                )

        conn.commit()
        total_ms = (time.perf_counter() - t0) * 1000
        print(
            "[SV][monitoring_rows] "
            f"summary total={total_ms:.1f}ms matched={market_match_count}/{len(items)} "
            f"active={active_match_count} dictionary={dictionary_match_count} unmatched={unmatched_count} "
            f"missing_identity={missing_identity_count} gamma_candidates={gamma_candidate_count} "
            f"gamma_enriched={gamma_enriched_count}"
        )
        for entry in slow_rows[:10]:
            print(f"[SV][monitoring_rows][slow] {entry}")

        return {
            "ok": True,
            "data": items,
            "db_path": str(db_path),
            "table": actual_table,
            "source_statuses": {
                "strategy_db": _source_status("good", None, count=len(items)),
                "market_lookup": _source_status(
                    "good" if market_match_count else "pending",
                    None,
                    count=market_match_count,
                    db_path=str(_strategy_storage_db_path()),
                ),
                "live_positions": _source_status(
                    "good" if live_ctx.get("remote_ok") else "error",
                    live_ctx.get("error"),
                    token_count=len(live_ctx.get("remote") or {}),
                    local_order_tokens=len(live_ctx.get("local") or {}),
                ),
            },
        }
    finally:
        conn.close()


def _strategy_conn_and_table() -> tuple[sqlite3.Connection, str]:
    db_path, preferred_table = _strategy_db_info()
    _ensure_sqlite_file(db_path)
    conn = sqlite3.connect(str(db_path))
    table = _discover_monitoring_table(conn, preferred_table)
    return conn, table


def _ensure_strategy_editable_columns(conn: sqlite3.Connection, table: str) -> None:
    cols = {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}
    changed = False
    for field in STRATEGY_EDITABLE_FIELDS:
        if field not in cols:
            conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{field}" TEXT')
            changed = True
    if changed:
        conn.commit()


def _build_editable_fields(item: Dict[str, Any]) -> Dict[str, Any]:
    """Build editable fields dict from item, expanding input_json into Inputs1~13."""
    result: Dict[str, Any] = {}
    # Expand input_json (new table) into Inputs1~13 keys
    raw_json = item.get("input_json")
    if raw_json:
        try:
            parsed = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
            for k, v in (parsed or {}).items():
                result[k] = v
        except (ValueError, TypeError):
            pass
    # Also pick up any Inputs1~13 already on the item (old table path)
    for field in STRATEGY_EDITABLE_FIELDS:
        if field not in result:
            v = item.get(field)
            if v is not None:
                result[field] = v
    return result


def _build_strategy_item(
    item: Dict[str, Any],
    matched_market: Dict[str, Any] | None,
    row_id: int | None = None,
    include_realtime_prices: bool = True,
) -> Dict[str, Any]:
    yes_qty = _safe_float(item.get("Yes_now_qty")) or 0.0
    no_qty = _safe_float(item.get("No_now_qty")) or 0.0
    yes_avg = _safe_float(item.get("Yes_avg_cost"))
    no_avg = _safe_float(item.get("No_avg_cost"))
    yes_token = str(item.get("yes_token") or "").strip() or str((matched_market or {}).get("yes_token") or "").strip()
    no_token = str(item.get("no_token") or "").strip() or str((matched_market or {}).get("no_token") or "").strip()
    condition_id = str(item.get("condition_id") or "").strip() or (matched_market or {}).get("condition_id")
    ws_snapshot = _select_strategy_ws_snapshot(yes_token, no_token, condition_id) if include_realtime_prices else None
    market_prices = _resolve_strategy_market_prices(
        matched_market,
        ws_snapshot=ws_snapshot,
        yes_token=yes_token,
        no_token=no_token,
    )
    yes_price = market_prices["yes_ask"] or _safe_float(item.get("Yes_ask") or item.get("ask"))
    yes_bid = market_prices["yes_bid"] or _safe_float(item.get("Yes_bid") or item.get("bid"))
    no_ask = market_prices["no_ask"] or _safe_float(item.get("No_ask"))
    no_bid = market_prices["no_bid"] or _safe_float(item.get("No_bid"))
    yes_last_price = market_prices.get("yes_last_price")
    no_last_price = market_prices.get("no_last_price")
    if yes_last_price is None:
        yes_last_price = yes_price
    if no_last_price is None:
        no_last_price = no_ask
    yes_position, no_position = calculate_position_pcts(item, yes_qty, yes_price, no_qty, no_ask)
    strategy_bankroll = resolve_strategy_bankroll(item)
    pnl_yes = ((yes_price or 0.0) - (yes_avg or 0.0)) * yes_qty if yes_avg is not None else 0.0
    pnl_no = ((no_ask or 0.0) - (no_avg or 0.0)) * no_qty if no_avg is not None else 0.0
    return {
        "row_id": row_id,
        "strategy": item.get("Strategy") or item.get("strategy") or item.get("Code") or "Unnamed",
        "question": item.get("question") or item.get("Translation") or item.get("Subject") or (matched_market or {}).get("question"),
        "subject": item.get("Subject"),
        "display_name": _build_strategy_display_name(item, row_id=row_id),
        "condition_id": condition_id,
        "yes_token": yes_token,
        "no_token": no_token,
        "score": item.get("Score"),
        "use_data": item.get("UseData"),
        "use_rss": item.get("UseRss"),
        "code_ok": item.get("IsCodeOk?"),
        "yes_bid": yes_bid,
        "yes_ask": yes_price,
        "yes_last_price": yes_last_price,
        "no_bid": no_bid,
        "no_ask": no_ask,
        "no_last_price": no_last_price,
        "yes_qty": yes_qty,
        "no_qty": no_qty,
        "yes_avg": yes_avg,
        "no_avg": no_avg,
        "yes_position": yes_position,
        "no_position": no_position,
        "yes_current_pct": yes_position,
        "no_current_pct": no_position,
        "strategy_bankroll": strategy_bankroll,
        "state": item.get("state"),
        "strategy_pnl": pnl_yes + pnl_no,
        "price_source": market_prices.get("price_source") or ("strategy_db" if not matched_market else "market"),
        "market_updated_at": market_prices.get("updated_at"),
        "realtime_snapshot_db_path": market_prices.get("snapshot_db_path"),
        "llm_prediction_p": item.get("llm_prediction_p"),
        "news": item.get("News"),
        "end_date": item.get("endDate") or (matched_market or {}).get("end_date"),
        "market_category": (matched_market or {}).get("category"),
        "slug": (matched_market or {}).get("slug") or ((matched_market or {}).get("raw") or {}).get("slug"),
        "event_slug": (matched_market or {}).get("event_slug") or ((matched_market or {}).get("raw") or {}).get("eventSlug") or ((matched_market or {}).get("raw") or {}).get("event_slug"),
        "group_item_title": (matched_market or {}).get("group_item_title") or ((matched_market or {}).get("raw") or {}).get("groupItemTitle"),
        "url": (matched_market or {}).get("url") or ((matched_market or {}).get("raw") or {}).get("url"),
        "matched_market_raw": (matched_market or {}).get("raw"),
        "editable": _build_editable_fields(item),
        "binary_identity_status": item.get("_binary_identity_status")
        or ("ok" if yes_token and no_token else "missing"),
        "raw": item,
    }


def fetch_strategy_monitoring(limit: int = 100, sync_stats: bool = True, allow_remote_positions: bool = False) -> Dict[str, Any]:
    t0 = time.perf_counter()
    t_load0 = time.perf_counter()
    local_result = _load_strategy_monitoring_rows(
        limit=max(1, limit),
        enrich_tokens=False,
        allow_remote_positions=allow_remote_positions,
        include_realtime_prices=False,
    )
    t_load1 = time.perf_counter()
    print(f"[SV][strategies] _load_strategy_monitoring_rows {(t_load1 - t_load0) * 1000:.1f}ms")
    if not local_result.get("ok"):
        print(f"[SV][strategies] failed total {(time.perf_counter() - t0) * 1000:.1f}ms")
        return {
            "ok": False,
            "status": "pending",
            "error": local_result.get("error"),
            "data": [],
            "db_path": local_result.get("db_path"),
            "table": local_result.get("table"),
            "source_statuses": local_result.get("source_statuses") or {},
        }

    items = local_result.get("data") or []
    t_sync0 = time.perf_counter()
    if sync_stats:
        stats_sync = sync_all_strategy_stats(items)
    else:
        stats_sync = {
            "ok": True,
            "directory": str(strategy_metrics_db_directory()),
            "files_touched": 0,
            "sample": [],
            "errors": [],
            "skipped": True,
        }
    t_sync1 = time.perf_counter()
    print(f"[SV][strategies] sync_all_strategy_stats {(t_sync1 - t_sync0) * 1000:.1f}ms items={len(items)} sync={sync_stats}")
    running_strategy_count = sum(
        1 for item in items if (_safe_float(item.get("yes_qty")) or 0.0) > 0 or (_safe_float(item.get("no_qty")) or 0.0) > 0
    )
    total_strategy_profit = sum(_safe_float(item.get("strategy_pnl")) or 0.0 for item in items)
    total_ms = (time.perf_counter() - t0) * 1000
    print(f"[SV][strategies] total {total_ms:.1f}ms items={len(items)}")
    source_statuses = dict(local_result.get("source_statuses") or {})
    source_statuses["strategy_profit"] = _source_status(
        "good",
        None,
        running_strategy_count=running_strategy_count,
        total_strategy_profit=total_strategy_profit,
        history_loaded=bool(items),
    )
    return {
        "ok": True,
        "status": "good" if items else "pending",
        "data": items,
        "count": len(items),
        "db_path": local_result.get("db_path"),
        "table": local_result.get("table"),
        "snapshot_db_path": local_result.get("db_path"),
        "realtime_snapshot_db_path": str(_strategy_storage_db_path()),
        "running_strategy_count": running_strategy_count,
        "total_strategy_profit": total_strategy_profit,
        "historical_loaded": bool(items),
        "source_statuses": source_statuses,
        "strategy_metrics_db_dir": stats_sync.get("directory"),
        "strategy_stats_sync": stats_sync,
    }


def fetch_strategy_detail(row_id: int, allow_remote_positions: bool = True) -> Dict[str, Any]:
    t0 = time.perf_counter()
    print(f"[SV][strategy_detail] start row_id={row_id} allow_remote_positions={allow_remote_positions}")

    # --- NEW PATH: try unified data source first ---
    try:
        _strat = strategy_data_source.get_strategy(row_id)
    except Exception:
        _strat = None
    if _strat:
        item = strategy_data_source.strategy_to_flat_dict(_strat)
        item_row_id = item.pop("row_id", row_id)
        # Virtual 模式：从 strategy_virtual_positions 注入持仓
        is_virtual = str(_strat.get("state") or "").strip().lower() == "virtual"
        if is_virtual:
            _inject_virtual_positions(item, int(row_id))
        if is_virtual or not allow_remote_positions:
            _matched = _match_strategy_market(item, _load_strategy_market_index())
            result = _build_strategy_item(item, _matched, row_id=item_row_id, include_realtime_prices=True)
            _recompute_strategy_metrics(result)
            result["position_source"] = result.get("position_source") or ("virtual" if is_virtual else None)
            result["display_name"] = _build_strategy_display_name(result, row_id=item_row_id)
            result["table"] = "strategy_registry"
            result["price_source"] = result.get("price_source") or "strategy_registry"
            total_ms = (time.perf_counter() - t0) * 1000
            print(f"[SV][strategy_detail] workspace_fast_path total={total_ms:.1f}ms row_id={row_id}")
            return result
        live_ctx = _get_live_position_cache(allow_remote=allow_remote_positions)
        matched_market = _match_strategy_market(item, _load_strategy_market_index())
        item, matched_market = _enrich_monitoring_row_tokens(item, matched_market, {})
        result = _build_strategy_item(item, matched_market, row_id=item_row_id, include_realtime_prices=True)
        _apply_wallet_live_positions(result, live_ctx)
        _recompute_strategy_metrics(result)
        result["display_name"] = _build_strategy_display_name(result, row_id=item_row_id)
        result["table"] = "strategy_registry"
        total_ms = (time.perf_counter() - t0) * 1000
        print(f"[SV][strategy_detail] new_path total={total_ms:.1f}ms row_id={row_id}")
        return result
    # --- END NEW PATH ---

    t_live0 = time.perf_counter()
    live_ctx = _get_live_position_cache(allow_remote=allow_remote_positions)
    t_live1 = time.perf_counter()
    print(
        f"[SV][strategy_detail] live_position_cache {(t_live1 - t_live0) * 1000:.1f}ms remote_ok={bool(live_ctx.get('remote_ok'))}"
    )
    t_conn0 = time.perf_counter()
    conn, actual_table = _strategy_conn_and_table()
    t_conn1 = time.perf_counter()
    print(f"[SV][strategy_detail] strategy_conn_and_table {(t_conn1 - t_conn0) * 1000:.1f}ms table={actual_table}")
    try:
        _ensure_strategy_editable_columns(conn, actual_table)
        t_query0 = time.perf_counter()
        cur = conn.cursor()
        cols = [str(row[1]) for row in cur.execute(f'PRAGMA table_info("{actual_table}")').fetchall()]
        raw_row = cur.execute(f'SELECT rowid, * FROM "{actual_table}" WHERE rowid = ?', (row_id,)).fetchone()
        t_query1 = time.perf_counter()
        print(f"[SV][strategy_detail] db_read {(t_query1 - t_query0) * 1000:.1f}ms")
        if not raw_row:
            raise ValueError(f"Strategy row not found: {row_id}")
        item = _to_row_dict(cols, raw_row[1:])
        t_build0 = time.perf_counter()
        matched_market = _match_strategy_market(item, _load_strategy_market_index())
        item, matched_market = _enrich_monitoring_row_tokens(item, matched_market, {})
        result = _build_strategy_item(item, matched_market, row_id=row_id, include_realtime_prices=True)
        _apply_wallet_live_positions(result, live_ctx)
        _recompute_strategy_metrics(result)
        result["display_name"] = _build_strategy_display_name(result, row_id=row_id)
        result["table"] = actual_table
        t_build1 = time.perf_counter()
        print(
            f"[SV][strategy_detail] build_result {(t_build1 - t_build0) * 1000:.1f}ms matched_market={bool(matched_market)}"
        )
        print(f"[SV][strategy_detail] total {(t_build1 - t0) * 1000:.1f}ms row_id={row_id}")
        return result
    finally:
        conn.close()


def update_strategy_detail(row_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    # --- NEW PATH: write to strategy_registry ---
    try:
        existing = strategy_data_source.get_strategy(row_id)
    except Exception:
        existing = None
    if existing is not None:
        # Separate scalar fields from Inputs* (which go into input_json)
        scalar_fields = {"initial_capital", "profit_roll_ratio", "realized_profit", "strategy_bankroll"}
        scalar_updates: Dict[str, Any] = {}
        input_updates: Dict[str, Any] = {}
        for k, v in payload.items():
            if k in scalar_fields:
                scalar_updates[k] = v
            elif k.startswith("Inputs") or k not in scalar_fields:
                input_updates[k] = v

        conn = strategy_data_source.connect()
        try:
            if scalar_updates:
                sets = ", ".join(f'"{f}" = ?' for f in scalar_updates)
                conn.execute(
                    f'UPDATE strategy_registry SET {sets}, updated_at_utc = ? WHERE strategy_id = ?',
                    [*scalar_updates.values(), datetime.now(timezone.utc).isoformat(), row_id],
                )
            if input_updates:
                # Merge into existing input_json
                try:
                    current_json = json.loads(existing.get("input_json") or "{}")
                except (ValueError, TypeError):
                    current_json = {}
                current_json.update(input_updates)
                conn.execute(
                    'UPDATE strategy_registry SET input_json = ?, updated_at_utc = ? WHERE strategy_id = ?',
                    [json.dumps(current_json), datetime.now(timezone.utc).isoformat(), row_id],
                )
            conn.commit()
        finally:
            conn.close()
        if "strategy_bankroll" in scalar_updates:
            from services.virtual_execution import sync_virtual_account_bankroll

            sync_virtual_account_bankroll(row_id, scalar_updates["strategy_bankroll"])
        detail = fetch_strategy_detail(row_id)
        sync_all_strategy_stats([detail])
        return detail
    # --- END NEW PATH ---

    conn, actual_table = _strategy_conn_and_table()
    try:
        _ensure_strategy_editable_columns(conn, actual_table)
        cur = conn.cursor()
        cols = {str(row[1]) for row in cur.execute(f'PRAGMA table_info("{actual_table}")').fetchall()}
        updates = []
        values: list[Any] = []
        for field in STRATEGY_EDITABLE_FIELDS:
            if field not in cols:
                continue
            if field in payload:
                updates.append(f'"{field}" = ?')
                values.append(payload.get(field))
        if not updates:
            detail = fetch_strategy_detail(row_id)
            sync_all_strategy_stats([detail])
            return detail
        values.append(row_id)
        cur.execute(f'UPDATE "{actual_table}" SET {", ".join(updates)} WHERE rowid = ?', values)
        conn.commit()
    finally:
        conn.close()
    detail = fetch_strategy_detail(row_id)
    sync_all_strategy_stats([detail])
    return detail
