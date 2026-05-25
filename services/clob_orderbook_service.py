from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict

from services.config_loader import load_config
from services.http_client import SESSION, get_timeout


_CONFIG = load_config()
_CLOB_API = _CONFIG.get("api", {}).get("clob_base", "https://clob.polymarket.com").rstrip("/")
_CACHE_TTL_SECONDS = 2.0
_LOCK = threading.Lock()
_QUOTE_CACHE: Dict[str, Dict[str, Any]] = {}


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _best_price(levels: Any, *, side: str) -> float | None:
    if not isinstance(levels, list):
        return None
    prices = []
    for level in levels:
        price = _safe_float((level or {}).get("price") if isinstance(level, dict) else None)
        if price is not None:
            prices.append(price)
    if not prices:
        return None
    return max(prices) if side == "bid" else min(prices)


def _best_size(levels: Any, price: float | None) -> float | None:
    if price is None or not isinstance(levels, list):
        return None
    for level in levels:
        if not isinstance(level, dict):
            continue
        level_price = _safe_float(level.get("price"))
        if level_price is not None and abs(level_price - price) < 1e-12:
            return _safe_float(level.get("size"))
    return None


def _book_timestamp_to_iso(raw_ts: Any) -> str:
    parsed = _safe_float(raw_ts)
    if parsed is None:
        return datetime.now(timezone.utc).isoformat()
    if parsed > 1_000_000_000_000:
        parsed = parsed / 1000.0
    return datetime.fromtimestamp(parsed, tz=timezone.utc).isoformat()


def fetch_orderbook_quote(token_id: str, *, force_refresh: bool = False) -> Dict[str, Any]:
    token = str(token_id or "").strip()
    if not token:
        return {}

    now = time.time()
    with _LOCK:
        cached = _QUOTE_CACHE.get(token)
        if (
            cached
            and not force_refresh
            and now - float(cached.get("_cache_ts") or 0.0) < _CACHE_TTL_SECONDS
        ):
            return dict(cached)

    resp = SESSION.get(f"{_CLOB_API}/book", params={"token_id": token}, timeout=get_timeout())
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, dict):
        return {}

    bids = payload.get("bids")
    asks = payload.get("asks")
    bid = _best_price(bids, side="bid")
    ask = _best_price(asks, side="ask")
    quote = {
        "token_id": token,
        "condition_id": payload.get("market"),
        "bid": bid,
        "ask": ask,
        "best_bid": bid,
        "best_ask": ask,
        "bid_size": _best_size(bids, bid),
        "ask_size": _best_size(asks, ask),
        "updated_at": _book_timestamp_to_iso(payload.get("timestamp")),
        "source": "clob_book",
        "_cache_ts": now,
    }
    with _LOCK:
        _QUOTE_CACHE[token] = dict(quote)
    return quote


def fetch_binary_orderbook_quotes(yes_token: str, no_token: str) -> Dict[str, Any]:
    yes_quote = fetch_orderbook_quote(yes_token) if str(yes_token or "").strip() else {}
    no_quote = fetch_orderbook_quote(no_token) if str(no_token or "").strip() else {}
    if not yes_quote and not no_quote:
        return {}
    return {
        "yes_bid": yes_quote.get("bid"),
        "yes_ask": yes_quote.get("ask"),
        "no_bid": no_quote.get("bid"),
        "no_ask": no_quote.get("ask"),
        "yes_bid_size": yes_quote.get("bid_size"),
        "yes_ask_size": yes_quote.get("ask_size"),
        "no_bid_size": no_quote.get("bid_size"),
        "no_ask_size": no_quote.get("ask_size"),
        "updated_at": yes_quote.get("updated_at") or no_quote.get("updated_at"),
        "price_source": "clob_book",
        "yes_quote": yes_quote,
        "no_quote": no_quote,
    }
