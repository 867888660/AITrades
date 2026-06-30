from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from services.config_loader import load_web_settings
from services.finance_service import fetch_finance_quotes
from services.http_client import SESSION, get_timeout


SPOT_BASE_URLS = [
    "https://api.binance.com",
    "https://data-api.binance.vision",
]

DERIVATIVE_ENDPOINTS = [
    {
        "subtype": "usdm_futures",
        "label": "USD-M",
        "asset_class": "crypto_perp",
        "venue": "binance_usdm",
        "path": "/fapi/v1/exchangeInfo",
        "base_urls": ["https://fapi.binance.com"],
    },
    {
        "subtype": "coinm_futures",
        "label": "COIN-M",
        "asset_class": "crypto_perp",
        "venue": "binance_coinm",
        "path": "/dapi/v1/exchangeInfo",
        "base_urls": ["https://dapi.binance.com"],
    },
    {
        "subtype": "options",
        "label": "Options",
        "asset_class": "crypto_option",
        "venue": "binance_options",
        "path": "/eapi/v1/exchangeInfo",
        "base_urls": ["https://eapi.binance.com"],
    },
]

STOCK_TOKEN_LIST_URL = (
    "https://www.binance.com/bapi/defi/v1/public/wallet-direct/"
    "buw/wallet/market/token/rwa/stock/detail/list/ai?type=1"
)

POPULAR_BASES = [
    "BTC",
    "ETH",
    "BNB",
    "SOL",
    "XRP",
    "DOGE",
    "ADA",
    "AVAX",
    "LINK",
    "SUI",
    "TRX",
    "TON",
    "LTC",
    "BCH",
]

DERIVATIVE_FALLBACK_BASES = [
    "BTC",
    "ETH",
    "BNB",
    "SOL",
    "XRP",
    "DOGE",
    "ADA",
    "AVAX",
    "LINK",
    "SUI",
    "TRX",
    "TON",
    "LTC",
    "BCH",
    "DOT",
    "NEAR",
    "APT",
    "ARB",
    "OP",
    "UNI",
    "AAVE",
    "PEPE",
    "SHIB",
]

POPULAR_EQUITIES = [
    "AAPL",
    "MSFT",
    "NVDA",
    "TSLA",
    "META",
    "AMZN",
    "GOOGL",
    "AMD",
    "COIN",
    "MSTR",
    "NFLX",
    "BABA",
    "TSM",
    "PLTR",
    "QQQ",
    "SPY",
    "GLD",
    "SLV",
    "TLT",
    "VTI",
]

QUOTE_PRIORITY = {
    "USDT": 0,
    "USDC": 1,
    "FDUSD": 2,
    "BTC": 3,
    "ETH": 4,
    "BNB": 5,
    "EUR": 6,
    "TRY": 7,
    "JPY": 8,
}

CHAIN_LABELS = {
    "1": "Ethereum",
    "56": "BSC",
    "CT_501": "Solana",
}

_CACHE: Dict[str, Dict[str, Any]] = {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _filter_map(item: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    filters = item.get("filters") or []
    if not isinstance(filters, list):
        return {}
    mapped: Dict[str, Dict[str, Any]] = {}
    for entry in filters:
        if not isinstance(entry, dict):
            continue
        filter_type = str(entry.get("filterType") or "").strip()
        if filter_type:
            mapped[filter_type] = entry
    return mapped


def _trading_rules(item: Dict[str, Any]) -> Dict[str, Any]:
    filters = _filter_map(item)
    price_filter = filters.get("PRICE_FILTER") or {}
    lot_size = filters.get("LOT_SIZE") or {}
    market_lot_size = filters.get("MARKET_LOT_SIZE") or {}
    min_notional_filter = filters.get("MIN_NOTIONAL") or filters.get("NOTIONAL") or {}
    return {
        "tick_size": price_filter.get("tickSize"),
        "min_price": price_filter.get("minPrice"),
        "max_price": price_filter.get("maxPrice"),
        "step_size": lot_size.get("stepSize"),
        "min_qty": lot_size.get("minQty"),
        "max_qty": lot_size.get("maxQty"),
        "market_step_size": market_lot_size.get("stepSize"),
        "market_min_qty": market_lot_size.get("minQty"),
        "market_max_qty": market_lot_size.get("maxQty"),
        "min_notional": min_notional_filter.get("minNotional") or min_notional_filter.get("notional"),
        "order_types": item.get("orderTypes"),
    }


def _clean_text(value: Any) -> str:
    return re.sub(r"[\s/_:-]+", "", str(value or "").upper().strip())


def _contains_query(row: Dict[str, Any], query: str, fields: Iterable[str]) -> bool:
    if not query:
        return True
    needle = _clean_text(query)
    return any(needle in _clean_text(row.get(field)) for field in fields)


def _limit_value(raw: Any, default: int = 50, maximum: int = 200) -> int:
    try:
        return max(1, min(int(raw), maximum))
    except (TypeError, ValueError):
        return default


def _cache_get(key: str, ttl_seconds: int, force_refresh: bool) -> Optional[Dict[str, Any]]:
    item = _CACHE.get(key)
    if not item or force_refresh:
        return None
    age = time.time() - float(item.get("stored_at", 0))
    if age <= ttl_seconds:
        return dict(item, cache_status="hit")
    return None


def _cache_set(key: str, payload: Any, source: str) -> Dict[str, Any]:
    item = {
        "payload": payload,
        "source": source,
        "stored_at": time.time(),
        "fetched_at_utc": _utc_now(),
        "cache_status": "fresh",
    }
    _CACHE[key] = item
    return dict(item)


def _response_summary(response: Any) -> str:
    text = ""
    try:
        text = response.text[:180]
    except Exception:
        text = ""
    return f"HTTP {response.status_code} {text}".strip()


def _get_json_from_urls(
    key: str,
    urls: List[str],
    ttl_seconds: int,
    force_refresh: bool = False,
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[Any, Dict[str, Any]]:
    cached = _cache_get(key, ttl_seconds, force_refresh)
    if cached:
        return cached["payload"], {
            "source": cached.get("source"),
            "fetched_at_utc": cached.get("fetched_at_utc"),
            "cache_status": cached.get("cache_status"),
            "errors": [],
        }

    errors: List[str] = []
    for url in urls:
        try:
            response = SESSION.get(url, headers=headers, timeout=get_timeout())
            if response.status_code >= 400:
                raise RuntimeError(f"{url} {_response_summary(response)}")
            payload = response.json()
            item = _cache_set(key, payload, url)
            return payload, {
                "source": url,
                "fetched_at_utc": item.get("fetched_at_utc"),
                "cache_status": "fresh",
                "errors": errors,
            }
        except Exception as exc:
            errors.append(str(exc))

    stale = _CACHE.get(key)
    if stale:
        return stale["payload"], {
            "source": stale.get("source"),
            "fetched_at_utc": stale.get("fetched_at_utc"),
            "cache_status": "stale",
            "errors": errors,
        }
    raise RuntimeError("; ".join(errors) or f"Unable to fetch {key}")


def _fetch_spot_exchange_info(force_refresh: bool = False) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    urls = [f"{base}/api/v3/exchangeInfo" for base in SPOT_BASE_URLS]
    payload, meta = _get_json_from_urls("binance_spot_exchange_info", urls, 30 * 60, force_refresh)
    return payload if isinstance(payload, dict) else {}, meta


def _fetch_spot_tickers(symbols: List[str]) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    if not symbols:
        return {}, []
    result: Dict[str, Dict[str, Any]] = {}
    errors: List[str] = []
    for idx in range(0, len(symbols), 100):
        batch = symbols[idx : idx + 100]
        params = {"symbols": json.dumps(batch, separators=(",", ":")), "type": "MINI"}
        fetched = False
        for base_url in SPOT_BASE_URLS:
            try:
                response = SESSION.get(
                    f"{base_url}/api/v3/ticker/24hr",
                    params=params,
                    timeout=get_timeout(),
                )
                if response.status_code >= 400:
                    raise RuntimeError(f"{base_url} {_response_summary(response)}")
                payload = response.json()
                items = payload if isinstance(payload, list) else [payload]
                for item in items:
                    symbol = str(item.get("symbol") or "").upper().strip()
                    if symbol:
                        result[symbol] = item
                fetched = True
                break
            except Exception as exc:
                errors.append(str(exc))
        if not fetched:
            continue
    return result, errors


def _spot_instrument_id(symbol: str) -> str:
    return f"crypto_spot:binance:{symbol}"


def _spot_row(item: Dict[str, Any], ticker: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    symbol = str(item.get("symbol") or "").upper().strip()
    base = str(item.get("baseAsset") or "").upper().strip()
    quote = str(item.get("quoteAsset") or "").upper().strip()
    ticker = ticker or {}
    status = str(item.get("status") or "").upper().strip()
    price = _safe_float(ticker.get("lastPrice"))
    open_price = _safe_float(ticker.get("openPrice"))
    change_percent = _safe_float(ticker.get("priceChangePercent"))
    if change_percent is None and price is not None and open_price not in (None, 0):
        change_percent = ((price - open_price) / open_price) * 100
    return {
        "instrument_id": _spot_instrument_id(symbol),
        "asset_class": "crypto_spot",
        "venue": "binance",
        "market_kind": "spot",
        "symbol": symbol,
        "display_symbol": f"{base}/{quote}" if base and quote else symbol,
        "display_name": f"{base} / {quote}" if base and quote else symbol,
        "base_asset": base,
        "quote_asset": quote,
        "settlement_asset": quote,
        "status": status,
        "is_spot_trading_allowed": bool(item.get("isSpotTradingAllowed")),
        "is_margin_trading_allowed": bool(item.get("isMarginTradingAllowed")),
        "price": price,
        "change_percent_24h": change_percent,
        "volume_24h_base": _safe_float(ticker.get("volume")),
        "volume_24h_quote": _safe_float(ticker.get("quoteVolume")),
        "price_precision": item.get("pricePrecision"),
        "quantity_precision": item.get("quantityPrecision"),
        "base_asset_precision": item.get("baseAssetPrecision"),
        "quote_asset_precision": item.get("quoteAssetPrecision"),
        "trading_rules": _trading_rules(item),
        "capabilities": {
            "spot": bool(item.get("isSpotTradingAllowed")),
            "margin": bool(item.get("isMarginTradingAllowed")),
            "derivatives": False,
            "tokenized_stock": False,
        },
        "source": "binance_spot_exchange_info",
        "_source_item": item,
    }


def _spot_sort_key(row: Dict[str, Any], query: str) -> Tuple[Any, ...]:
    base = str(row.get("base_asset") or "")
    quote = str(row.get("quote_asset") or "")
    symbol = str(row.get("symbol") or "")
    if query:
        exact = _clean_text(query) == _clean_text(symbol)
        starts = _clean_text(symbol).startswith(_clean_text(query))
        return (0 if exact else 1, 0 if starts else 1, symbol)
    base_rank = POPULAR_BASES.index(base) if base in POPULAR_BASES else 999
    quote_rank = QUOTE_PRIORITY.get(quote, 999)
    return (quote_rank, base_rank, symbol)


def _search_spot(args: Dict[str, Any]) -> Dict[str, Any]:
    query = str(args.get("q") or "").strip()
    quote = str(args.get("quote") or "").upper().strip()
    status = str(args.get("status") or "").upper().strip()
    margin_only = str(args.get("margin") or "").strip().lower() in {"1", "true", "yes", "on"}
    limit = _limit_value(args.get("limit"), default=50)
    force_refresh = str(args.get("refresh") or "").strip() == "1"

    exchange_info, source_meta = _fetch_spot_exchange_info(force_refresh=force_refresh)
    rows = []
    for item in exchange_info.get("symbols", []):
        row = _spot_row(item)
        if status and row.get("status") != status:
            continue
        if quote and row.get("quote_asset") != quote:
            continue
        if margin_only and not row.get("is_margin_trading_allowed"):
            continue
        if not _contains_query(row, query, ("symbol", "display_symbol", "base_asset", "quote_asset")):
            continue
        rows.append(row)

    rows.sort(key=lambda row: _spot_sort_key(row, query))
    rows = rows[:limit]
    tickers, ticker_errors = _fetch_spot_tickers([str(row.get("symbol") or "") for row in rows])
    rows = [_spot_row(row.get("_source_item") or {}, tickers.get(str(row.get("symbol") or ""))) for row in rows]
    for row in rows:
        row.pop("_source_item", None)

    errors = list(source_meta.get("errors") or []) + ticker_errors
    return {
        "ok": True,
        "count": len(rows),
        "data": rows,
        "meta": {
            "category": "crypto_spot",
            "source": source_meta.get("source"),
            "source_status": "degraded" if errors else "ok",
            "cache_status": source_meta.get("cache_status"),
            "fetched_at_utc": source_meta.get("fetched_at_utc"),
            "total_source_symbols": len(exchange_info.get("symbols", []) or []),
            "errors": errors,
        },
    }


def _derivative_row(item: Dict[str, Any], endpoint: Dict[str, Any]) -> Dict[str, Any]:
    subtype = endpoint["subtype"]
    symbol = str(item.get("symbol") or "").upper().strip()
    base = str(item.get("baseAsset") or item.get("underlying") or "").upper().strip()
    quote = str(item.get("quoteAsset") or "").upper().strip()
    settle = str(item.get("settleAsset") or item.get("settlementAsset") or quote or "").upper().strip()
    contract_type = str(item.get("contractType") or item.get("type") or "").upper().strip()
    status = str(item.get("status") or "").upper().strip()
    return {
        "instrument_id": f"{endpoint['asset_class']}:{endpoint['venue']}:{symbol}",
        "asset_class": endpoint["asset_class"],
        "venue": endpoint["venue"],
        "market_kind": "derivatives",
        "subtype": subtype,
        "subtype_label": endpoint["label"],
        "symbol": symbol,
        "display_symbol": symbol,
        "display_name": f"{symbol} {endpoint['label']}",
        "base_asset": base,
        "quote_asset": quote,
        "settlement_asset": settle,
        "contract_type": contract_type,
        "status": status,
        "delivery_date": item.get("deliveryDate"),
        "onboard_date": item.get("onboardDate"),
        "price_precision": item.get("pricePrecision"),
        "quantity_precision": item.get("quantityPrecision"),
        "base_asset_precision": item.get("baseAssetPrecision"),
        "quote_asset_precision": item.get("quotePrecision") or item.get("quoteAssetPrecision"),
        "trading_rules": _trading_rules(item),
        "capabilities": {
            "spot": False,
            "margin": False,
            "derivatives": True,
            "tokenized_stock": False,
        },
        "source": f"binance_{subtype}_exchange_info",
    }


def _derivative_items(payload: Any, endpoint: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    if endpoint["subtype"] == "options":
        items = payload.get("optionSymbols") or payload.get("symbols") or []
    else:
        items = payload.get("symbols") or []
    return items if isinstance(items, list) else []


def _fallback_usdm_symbols(query: str, settlement: str, limit: int) -> List[str]:
    quote = settlement if settlement in {"USDT", "USDC"} else "USDT"
    symbols = [f"{base}{quote}" for base in DERIVATIVE_FALLBACK_BASES]
    clean_query = re.sub(r"[^A-Za-z0-9]+", "", query or "").upper().strip()
    if clean_query:
        if clean_query.endswith(("USDT", "USDC")):
            symbols.insert(0, clean_query)
        elif 2 <= len(clean_query) <= 12:
            symbols.insert(0, f"{clean_query}{quote}")
        query_key = _clean_text(clean_query)
        symbols = [
            symbol
            for symbol in symbols
            if query_key in _clean_text(symbol) or _clean_text(symbol).startswith(query_key)
        ]
    deduped: List[str] = []
    for symbol in symbols:
        if symbol not in deduped:
            deduped.append(symbol)
    return deduped[:limit]


def _fallback_derivative_rows(
    *,
    query: str,
    subtype_filter: str,
    settlement: str,
    status: str,
    limit: int,
) -> List[Dict[str, Any]]:
    if subtype_filter and subtype_filter != "all" and subtype_filter != "usdm_futures":
        return []
    if settlement and settlement not in {"USDT", "USDC"}:
        return []
    endpoint = next(item for item in DERIVATIVE_ENDPOINTS if item["subtype"] == "usdm_futures")
    rows: List[Dict[str, Any]] = []
    for symbol in _fallback_usdm_symbols(query, settlement, limit):
        quote = "USDC" if symbol.endswith("USDC") else "USDT"
        base = symbol[: -len(quote)]
        item = {
            "symbol": symbol,
            "baseAsset": base,
            "quoteAsset": quote,
            "settleAsset": quote,
            "contractType": "PERPETUAL",
            "status": "TRADING",
        }
        row = _derivative_row(item, endpoint)
        row.update(
            {
                "source": "local_usdm_perp_fallback",
                "data_quality": "fallback_unverified",
                "fallback_reason": (
                    "Binance derivatives exchangeInfo unavailable; symbol inferred from common USD-M perpetuals "
                    "or the search text."
                ),
            }
        )
        if status and row.get("status") != status:
            continue
        rows.append(row)
    return rows


def _search_derivatives(args: Dict[str, Any]) -> Dict[str, Any]:
    query = str(args.get("q") or "").strip()
    subtype_filter = str(args.get("subtype") or "").strip()
    settlement = str(args.get("settlement") or args.get("quote") or "").upper().strip()
    status = str(args.get("status") or "").upper().strip()
    limit = _limit_value(args.get("limit"), default=50)
    force_refresh = str(args.get("refresh") or "").strip() == "1"
    rows: List[Dict[str, Any]] = []
    errors: List[str] = []
    source_meta: List[Dict[str, Any]] = []

    for endpoint in DERIVATIVE_ENDPOINTS:
        if subtype_filter and subtype_filter != "all" and endpoint["subtype"] != subtype_filter:
            continue
        urls = [f"{base}{endpoint['path']}" for base in endpoint["base_urls"]]
        try:
            payload, meta = _get_json_from_urls(
                f"binance_derivatives_{endpoint['subtype']}",
                urls,
                30 * 60,
                force_refresh,
            )
            source_meta.append({"subtype": endpoint["subtype"], **meta})
            for item in _derivative_items(payload, endpoint):
                row = _derivative_row(item, endpoint)
                if status and row.get("status") != status:
                    continue
                if settlement and row.get("settlement_asset") != settlement:
                    continue
                if not _contains_query(row, query, ("symbol", "base_asset", "quote_asset", "settlement_asset", "subtype_label")):
                    continue
                rows.append(row)
            errors.extend(meta.get("errors") or [])
        except Exception as exc:
            errors.append(f"{endpoint['subtype']}: {exc}")

    fallback_used = False
    if not rows and errors:
        rows = _fallback_derivative_rows(
            query=query,
            subtype_filter=subtype_filter,
            settlement=settlement,
            status=status,
            limit=limit,
        )
        fallback_used = bool(rows)

    rows.sort(key=lambda row: (row.get("subtype_label") or "", row.get("symbol") or ""))
    return {
        "ok": True,
        "count": len(rows[:limit]),
        "data": rows[:limit],
        "meta": {
            "category": "crypto_derivatives",
            "source": "binance_derivatives_exchange_info",
            "source_status": "degraded" if errors else "ok",
            "fetched_at_utc": _utc_now(),
            "sources": source_meta,
            "fallback": fallback_used,
            "fallback_reason": (
                "Binance derivatives exchangeInfo unavailable; returned local USD-M perpetual fallback rows."
                if fallback_used
                else None
            ),
            "errors": errors,
        },
    }


def _fetch_stock_tokens(force_refresh: bool = False) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    payload, meta = _get_json_from_urls(
        "binance_stock_tokens",
        [STOCK_TOKEN_LIST_URL],
        30 * 60,
        force_refresh,
        headers={
            "User-Agent": "binance-web3/1.1 (Skill)",
            "Accept-Encoding": "identity",
        },
    )
    data = payload.get("data", []) if isinstance(payload, dict) else []
    return data if isinstance(data, list) else [], meta


def _stock_token_row(item: Dict[str, Any]) -> Dict[str, Any]:
    ticker = str(item.get("ticker") or "").upper().strip()
    token_symbol = str(item.get("symbol") or "").strip()
    chain_id = str(item.get("chainId") or "").strip()
    status = str(item.get("status") or item.get("tradeStatus") or "ACTIVE").upper().strip()
    return {
        "instrument_id": f"rwa_stock_token:binance_web3:{token_symbol}:{chain_id}",
        "asset_class": "rwa_stock_token",
        "venue": "binance_web3",
        "market_kind": "stock_token",
        "symbol": token_symbol,
        "display_symbol": f"{ticker} on {CHAIN_LABELS.get(chain_id, chain_id)}",
        "display_name": f"{ticker} tokenized stock",
        "underlying_symbol": ticker,
        "ticker": ticker,
        "chain_id": chain_id,
        "chain_label": CHAIN_LABELS.get(chain_id, chain_id or "-"),
        "contract_address": item.get("contractAddress"),
        "multiplier": item.get("multiplier"),
        "status": status,
        "capabilities": {
            "spot": False,
            "margin": False,
            "derivatives": False,
            "tokenized_stock": True,
        },
        "source": "binance_web3_tokenized_securities",
    }


def _stock_token_sort_key(row: Dict[str, Any], query: str) -> Tuple[Any, ...]:
    ticker = str(row.get("ticker") or "")
    symbol = str(row.get("symbol") or "")
    chain_id = str(row.get("chain_id") or "")
    if query:
        exact = _clean_text(query) in {_clean_text(ticker), _clean_text(symbol)}
        return (0 if exact else 1, ticker, chain_id)
    rank = POPULAR_EQUITIES.index(ticker) if ticker in POPULAR_EQUITIES else 999
    return (rank, ticker, chain_id)


def _search_stock_tokens(args: Dict[str, Any]) -> Dict[str, Any]:
    query = str(args.get("q") or "").strip()
    chain_id = str(args.get("chain_id") or "").strip()
    status = str(args.get("status") or "").upper().strip()
    limit = _limit_value(args.get("limit"), default=50)
    force_refresh = str(args.get("refresh") or "").strip() == "1"
    items, source_meta = _fetch_stock_tokens(force_refresh=force_refresh)
    rows = []
    for item in items:
        row = _stock_token_row(item)
        if chain_id and row.get("chain_id") != chain_id:
            continue
        if status and row.get("status") != status:
            continue
        if not _contains_query(row, query, ("symbol", "ticker", "display_symbol", "contract_address")):
            continue
        rows.append(row)
    rows.sort(key=lambda row: _stock_token_sort_key(row, query))
    errors = list(source_meta.get("errors") or [])
    return {
        "ok": True,
        "count": len(rows[:limit]),
        "data": rows[:limit],
        "meta": {
            "category": "rwa_stock_token",
            "source": source_meta.get("source"),
            "source_status": "degraded" if errors else "ok",
            "cache_status": source_meta.get("cache_status"),
            "fetched_at_utc": source_meta.get("fetched_at_utc"),
            "total_source_rows": len(items),
            "errors": errors,
        },
    }


def _symbol_candidates_from_query(query: str, configured: List[str]) -> List[str]:
    clean = re.sub(r"[^A-Za-z0-9.,;|\s-]+", "", query or "").strip()
    if not clean:
        return configured
    parts = [part.replace("-", "").upper() for part in re.split(r"[\s,;|]+", clean) if part.strip()]
    if not parts:
        return configured
    configured_set = set(configured)
    matches = [symbol for symbol in configured if any(part in symbol for part in parts)]
    for part in parts:
        if 1 <= len(part) <= 12 and part not in configured_set and part not in matches:
            matches.append(part)
    return matches


def _search_equity(args: Dict[str, Any]) -> Dict[str, Any]:
    query = str(args.get("q") or "").strip()
    status_filter = str(args.get("status") or "").upper().strip()
    limit = _limit_value(args.get("limit"), default=50)
    settings = load_web_settings()
    configured = [str(symbol or "").strip().upper() for symbol in (settings.get("finance_symbols") or []) if str(symbol or "").strip()]
    symbols = _symbol_candidates_from_query(query, configured)[:limit]
    api_key = settings.get("active_finnhub_api_key") or None
    quote_rows: Dict[str, Dict[str, Any]] = {}
    errors: List[str] = []
    if api_key and symbols:
        try:
            payload = fetch_finance_quotes(symbols, api_key)
            for item in payload.get("data", []) or []:
                symbol = str(item.get("symbol") or "").upper().strip()
                if symbol:
                    quote_rows[symbol] = item
            errors.extend(payload.get("errors") or [])
        except Exception as exc:
            errors.append(str(exc))
    elif symbols:
        errors.append("Missing Finnhub API key; showing configured symbols only.")

    rows = []
    for symbol in symbols:
        quote = quote_rows.get(symbol, {})
        price = quote.get("price")
        status = "QUOTE_READY" if price is not None else ("CONFIGURED" if symbol in configured else "LOOKUP_ONLY")
        if status_filter and status != status_filter:
            continue
        rows.append(
            {
                "instrument_id": f"equity:finnhub:{symbol}",
                "asset_class": "equity",
                "venue": "finnhub",
                "market_kind": "equity",
                "symbol": symbol,
                "display_symbol": symbol,
                "display_name": quote.get("company_name") or symbol,
                "status": status,
                "price": price,
                "change": quote.get("change"),
                "change_percent": quote.get("change_percent"),
                "currency": quote.get("currency"),
                "exchange": quote.get("exchange"),
                "market_cap_usd": quote.get("market_cap_usd"),
                "capabilities": {
                    "spot": False,
                    "margin": False,
                    "derivatives": False,
                    "tokenized_stock": False,
                    "equity": True,
                },
                "source": "finnhub_quote_profile2" if quote else "local_finance_symbols",
            }
        )
    rows.sort(key=lambda row: (POPULAR_EQUITIES.index(row["symbol"]) if row["symbol"] in POPULAR_EQUITIES else 999, row["symbol"]))
    return {
        "ok": True,
        "count": len(rows),
        "data": rows,
        "meta": {
            "category": "equity",
            "source": "finnhub_quote_profile2",
            "source_status": "degraded" if errors else "ok",
            "configured_symbols": configured,
            "fetched_at_utc": _utc_now(),
            "errors": errors,
        },
    }


def search_binance_markets(args: Dict[str, Any]) -> Dict[str, Any]:
    category = str(args.get("category") or "crypto_spot").strip()
    if category == "crypto_spot":
        return _search_spot(args)
    if category == "crypto_derivatives":
        return _search_derivatives(args)
    if category == "rwa_stock_token":
        return _search_stock_tokens(args)
    if category == "equity":
        return _search_equity(args)
    raise ValueError(f"Unsupported Binance market category: {category}")
