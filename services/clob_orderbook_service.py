from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

from services.config_loader import load_config
from services.http_client import SESSION, get_timeout


_CONFIG = load_config()
_CLOB_API = _CONFIG.get("api", {}).get("clob_base", "https://clob.polymarket.com").rstrip("/")
_CACHE_TTL_SECONDS = 2.0
_ORDERBOOK_TIMEOUT_SECONDS = 2.0
_LOCK = threading.Lock()
_QUOTE_CACHE: Dict[str, Dict[str, Any]] = {}


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_levels(levels: Any, *, side: str) -> List[Dict[str, float]]:
    if not isinstance(levels, list):
        return []
    by_price: Dict[float, float] = {}
    for level in levels:
        if not isinstance(level, dict):
            continue
        price = _safe_float(level.get("price"))
        size = _safe_float(level.get("size", level.get("qty")))
        if price is None or size is None or price <= 0 or size <= 0:
            continue
        by_price[price] = by_price.get(price, 0.0) + size
    reverse = side == "bid"
    return [
        {"price": price, "qty": qty, "size": qty}
        for price, qty in sorted(by_price.items(), key=lambda item: item[0], reverse=reverse)
    ]


def _best_price(levels: Any, *, side: str) -> float | None:
    normalized = _normalize_levels(levels, side=side)
    if not normalized:
        return None
    return normalized[0]["price"]


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

    resp = SESSION.get(
        f"{_CLOB_API}/book",
        params={"token_id": token},
        timeout=min(get_timeout(), _ORDERBOOK_TIMEOUT_SECONDS),
    )
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, dict):
        return {}

    bids = _normalize_levels(payload.get("bids"), side="bid")
    asks = _normalize_levels(payload.get("asks"), side="ask")
    bid = bids[0]["price"] if bids else None
    ask = asks[0]["price"] if asks else None
    quote = {
        "token_id": token,
        "condition_id": payload.get("market"),
        "bid": bid,
        "ask": ask,
        "best_bid": bid,
        "best_ask": ask,
        "bid_size": _best_size(bids, bid),
        "ask_size": _best_size(asks, ask),
        "bids": bids,
        "asks": asks,
        "bid_depth_qty": sum(level["qty"] for level in bids),
        "ask_depth_qty": sum(level["qty"] for level in asks),
        "bid_depth_notional": sum(level["price"] * level["qty"] for level in bids),
        "ask_depth_notional": sum(level["price"] * level["qty"] for level in asks),
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
        "yes_bids": yes_quote.get("bids") or [],
        "yes_asks": yes_quote.get("asks") or [],
        "no_bids": no_quote.get("bids") or [],
        "no_asks": no_quote.get("asks") or [],
        "updated_at": yes_quote.get("updated_at") or no_quote.get("updated_at"),
        "price_source": "clob_book",
        "yes_quote": yes_quote,
        "no_quote": no_quote,
    }
