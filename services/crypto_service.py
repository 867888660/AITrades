from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from services.http_client import SESSION, get_timeout


BINANCE_API = "https://api.binance.com"
COINGECKO_API = "https://api.coingecko.com/api/v3"
DEFAULT_TICKER_TO_COINGECKO_ID = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "BNB": "binancecoin",
    "SOL": "solana",
    "XRP": "ripple",
    "ADA": "cardano",
    "DOGE": "dogecoin",
    "AVAX": "avalanche-2",
    "DOT": "polkadot",
    "LINK": "chainlink",
    "TRX": "tron",
    "TON": "the-open-network",
    "LTC": "litecoin",
    "BCH": "bitcoin-cash",
    "APT": "aptos",
    "SUI": "sui",
}
_BINANCE_EXINFO_CACHE: Dict[str, Dict[str, str]] = {}
_COINGECKO_MARKET_CACHE: Dict[str, Dict[str, Any]] = {}
_QUOTE_SUFFIXES = ("USDT", "USDC", "USD", "BTC", "ETH", "BNB", "FDUSD", "TUSD", "EUR")


def parse_symbols(raw: str | list[str], uppercase: bool = True) -> List[str]:
    if isinstance(raw, list):
        parts = raw
    else:
        parts = re.split(r"[\s,;|]+", str(raw or "").strip())
    symbols: List[str] = []
    seen = set()
    for part in parts:
        item = str(part).strip()
        if not item:
            continue
        item = item.replace("/", "").replace("-", "")
        if uppercase:
            item = item.upper()
        if item not in seen:
            symbols.append(item)
            seen.add(item)
    return symbols


def _chunks(items: List[str], size: int) -> List[List[str]]:
    return [items[idx : idx + size] for idx in range(0, len(items), size)]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _split_symbol(symbol: str) -> tuple[str, str]:
    text = str(symbol or "").upper().strip()
    for quote in _QUOTE_SUFFIXES:
        if text.endswith(quote) and len(text) > len(quote):
            return text[: -len(quote)], quote
    return text, ""


def _fetch_exchange_info(symbols: List[str]) -> None:
    need = [symbol for symbol in symbols if symbol not in _BINANCE_EXINFO_CACHE]
    if not need:
        return
    for batch in _chunks(need, 100):
        response = SESSION.get(
            f"{BINANCE_API}/api/v3/exchangeInfo",
            params={"symbols": json.dumps(batch, separators=(",", ":"))},
            timeout=get_timeout(),
        )
        response.raise_for_status()
        payload = response.json()
        for item in payload.get("symbols", []):
            symbol = item.get("symbol")
            base = item.get("baseAsset")
            quote = item.get("quoteAsset")
            if symbol and base and quote:
                _BINANCE_EXINFO_CACHE[symbol] = {"base": base, "quote": quote}


def _coingecko_headers(config: Dict[str, Any]) -> Dict[str, str]:
    headers = {"accept": "application/json"}
    api_key = str(config.get("coingecko_api_key", "")).strip()
    header_name = str(config.get("coingecko_api_key_header", "x-cg-demo-api-key")).strip() or "x-cg-demo-api-key"
    if api_key:
        headers[header_name] = api_key
    return headers


def _resolve_coingecko_id(base_symbol: str) -> Optional[str]:
    return DEFAULT_TICKER_TO_COINGECKO_ID.get(base_symbol.upper())


def _fetch_coingecko_market_payload(ids: List[str], headers: Dict[str, str]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    unique_ids = [coin_id for coin_id in dict.fromkeys(ids) if coin_id]
    for batch in _chunks(unique_ids, 200):
        response = SESSION.get(
            f"{COINGECKO_API}/coins/markets",
            params={"vs_currency": "usd", "ids": ",".join(batch), "per_page": len(batch), "page": 1},
            headers=headers,
            timeout=get_timeout(),
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            rows.extend(payload)
    return rows


def _cache_coingecko_market(item: Dict[str, Any]) -> None:
    coin_id = item.get("id")
    if not coin_id:
        return
    _COINGECKO_MARKET_CACHE[coin_id] = {
        "current_price": item.get("current_price"),
        "total_volume": item.get("total_volume"),
        "market_cap": item.get("market_cap"),
        "fdv": item.get("fully_diluted_valuation"),
        "circulating_supply": item.get("circulating_supply"),
        "total_supply": item.get("total_supply"),
        "max_supply": item.get("max_supply"),
        "last_updated": item.get("last_updated"),
    }


def _fetch_coingecko_markets(ids: List[str], headers: Dict[str, str]) -> None:
    pending = [coin_id for coin_id in ids if coin_id and coin_id not in _COINGECKO_MARKET_CACHE]
    if not pending:
        return
    for item in _fetch_coingecko_market_payload(pending, headers):
        _cache_coingecko_market(item)


def _coingecko_rows_for_symbols(symbols: List[str], headers: Dict[str, str]) -> tuple[List[Dict[str, Any]], List[str]]:
    id_to_symbols: Dict[str, List[str]] = {}
    debug: List[str] = []
    for symbol in symbols:
        base, quote = _split_symbol(symbol)
        coin_id = _resolve_coingecko_id(base)
        if not coin_id:
            debug.append(f"coingecko_id_missing={symbol}")
            continue
        id_to_symbols.setdefault(coin_id, []).append(symbol)
        _BINANCE_EXINFO_CACHE.setdefault(symbol, {"base": base, "quote": quote})

    if not id_to_symbols:
        return [], debug

    market_rows = _fetch_coingecko_market_payload(list(id_to_symbols.keys()), headers)
    by_id = {str(item.get("id") or ""): item for item in market_rows if item.get("id")}
    rows: List[Dict[str, Any]] = []
    for coin_id, mapped_symbols in id_to_symbols.items():
        item = by_id.get(coin_id)
        if not item:
            debug.append(f"coingecko_market_missing={coin_id}")
            continue
        _cache_coingecko_market(item)
        for symbol in mapped_symbols:
            base, quote = _split_symbol(symbol)
            rows.append(
                {
                    "symbol": symbol,
                    "price": _safe_float(item.get("current_price")),
                    "vol_24h_base": None,
                    "vol_24h_quote": _safe_float(item.get("total_volume")),
                    "open_time_ms": None,
                    "close_time_ms": None,
                    "source": "coingecko_markets_fallback",
                    "base_asset": base,
                    "quote_asset": quote or "USD",
                    "coingecko_id": coin_id,
                    "market_cap_usd": item.get("market_cap"),
                    "fdv_usd": item.get("fully_diluted_valuation"),
                    "circulating_supply": item.get("circulating_supply"),
                    "total_supply": item.get("total_supply"),
                    "max_supply": item.get("max_supply"),
                    "fund_last_updated": item.get("last_updated"),
                }
            )
    return rows, debug


def fetch_crypto_quotes(symbols_text: str | list[str], include_fundamentals: bool = True, config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    symbols = parse_symbols(symbols_text, uppercase=True)
    if not symbols:
        return {"ok": False, "error": "No crypto symbols provided.", "data": []}

    started = time.time()
    rows: List[Dict[str, Any]] = []
    debug: List[str] = []
    config = config or {}
    headers = _coingecko_headers(config)

    for batch in _chunks(symbols, 100):
        try:
            response = SESSION.get(
                f"{BINANCE_API}/api/v3/ticker/24hr",
                params={"symbols": json.dumps(batch, separators=(",", ":")), "type": "MINI"},
                timeout=get_timeout(),
            )
            response.raise_for_status()
            payload = response.json()
            items = payload if isinstance(payload, list) else [payload]
            for item in items:
                symbol = str(item.get("symbol") or "").upper().strip()
                base, quote = _split_symbol(symbol)
                rows.append(
                    {
                        "symbol": symbol,
                        "price": _safe_float(item.get("lastPrice")),
                        "vol_24h_base": _safe_float(item.get("volume")),
                        "vol_24h_quote": _safe_float(item.get("quoteVolume")),
                        "open_time_ms": item.get("openTime"),
                        "close_time_ms": item.get("closeTime"),
                        "source": "binance_spot_rest_ticker24hr_mini",
                        "base_asset": base,
                        "quote_asset": quote,
                    }
                )
        except Exception as exc:
            debug.append(f"binance_ticker_error={exc}")

    fetched_symbols = {str(row.get("symbol") or "").upper().strip() for row in rows}
    missing_symbols = [symbol for symbol in symbols if symbol not in fetched_symbols]
    if missing_symbols:
        try:
            fallback_rows, fallback_debug = _coingecko_rows_for_symbols(missing_symbols, headers)
            rows.extend(fallback_rows)
            debug.extend(fallback_debug)
            if fallback_rows:
                debug.append(f"price_fallback=coingecko symbols={','.join(row['symbol'] for row in fallback_rows)}")
        except Exception as exc:
            debug.append(f"coingecko_price_fallback_error={exc}")

    if not rows:
        return {
            "ok": False,
            "error": "; ".join(debug) or "No crypto quote provider returned usable rows.",
            "errors": debug,
            "data": [],
            "ts_utc": _utc_now(),
            "latency_ms": int((time.time() - started) * 1000),
            "meta": {"symbols": symbols},
        }

    if include_fundamentals and rows:
        try:
            _fetch_exchange_info([row["symbol"] for row in rows if row.get("symbol")])
        except Exception as exc:  # pragma: no cover
            debug.append(f"binance_exchange_info_error={exc}")
        try:
            ids = []
            for row in rows:
                symbol = str(row.get("symbol", "")).upper().strip()
                fallback_base, fallback_quote = _split_symbol(symbol)
                meta = _BINANCE_EXINFO_CACHE.get(symbol, {})
                row["base_asset"] = row.get("base_asset") or meta.get("base") or fallback_base
                row["quote_asset"] = row.get("quote_asset") or meta.get("quote") or fallback_quote
                coin_id = _resolve_coingecko_id(str(meta.get("base", "")))
                if not coin_id:
                    coin_id = _resolve_coingecko_id(str(row.get("base_asset", "")))
                if coin_id:
                    row["coingecko_id"] = coin_id
                    ids.append(coin_id)
            _fetch_coingecko_markets(ids, headers)
            for row in rows:
                coin_id = row.get("coingecko_id")
                fundamentals = _COINGECKO_MARKET_CACHE.get(str(coin_id), {})
                row["market_cap_usd"] = row.get("market_cap_usd") if row.get("market_cap_usd") is not None else fundamentals.get("market_cap")
                row["fdv_usd"] = row.get("fdv_usd") if row.get("fdv_usd") is not None else fundamentals.get("fdv")
                row["circulating_supply"] = row.get("circulating_supply") if row.get("circulating_supply") is not None else fundamentals.get("circulating_supply")
                row["total_supply"] = row.get("total_supply") if row.get("total_supply") is not None else fundamentals.get("total_supply")
                row["max_supply"] = row.get("max_supply") if row.get("max_supply") is not None else fundamentals.get("max_supply")
                row["fund_last_updated"] = row.get("fund_last_updated") or fundamentals.get("last_updated")
        except Exception as exc:  # pragma: no cover
            debug.append(f"fundamentals_error={exc}")

    return {
        "ok": True,
        "ts_utc": _utc_now(),
        "latency_ms": int((time.time() - started) * 1000),
        "count": len(rows),
        "data": rows,
        "debug": debug,
        "meta": {"symbols": symbols},
        "include_fundamentals": include_fundamentals,
        "fundamentals_provider": "coingecko" if include_fundamentals else None,
        "vs_currency": "usd",
        "binance_base_url": BINANCE_API,
        "coingecko_base_url": COINGECKO_API if include_fundamentals else None,
    }


def build_crypto_snapshot_row(payload: Dict[str, Any]) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "ok": payload.get("ok"),
        "ts_utc": payload.get("ts_utc"),
        "latency_ms": payload.get("latency_ms"),
        "count": payload.get("count"),
        "data": json.dumps(payload.get("data", []), ensure_ascii=False),
        "debug": json.dumps(payload.get("debug", []), ensure_ascii=False),
        "meta": json.dumps(payload.get("meta", {}), ensure_ascii=False),
        "include_fundamentals": payload.get("include_fundamentals"),
        "fundamentals_provider": payload.get("fundamentals_provider"),
        "vs_currency": payload.get("vs_currency"),
        "binance_base_url": payload.get("binance_base_url"),
        "coingecko_base_url": payload.get("coingecko_base_url"),
    }
    for item in payload.get("data", []):
        symbol = str(item.get("symbol", "")).upper().strip()
        if not symbol:
            continue
        row[f"Price_{symbol}"] = item.get("price")
        row[f"Vol24hBase_{symbol}"] = item.get("vol_24h_base")
        row[f"Vol24hQuote_{symbol}"] = item.get("vol_24h_quote")
        row[f"McapUsd_{symbol}"] = item.get("market_cap_usd")
        row[f"FdvUsd_{symbol}"] = item.get("fdv_usd")
        row[f"CircSupply_{symbol}"] = item.get("circulating_supply")
        row[f"TotalSupply_{symbol}"] = item.get("total_supply")
        row[f"MaxSupply_{symbol}"] = item.get("max_supply")
        row[f"OpenTimeMs_{symbol}"] = item.get("open_time_ms")
        row[f"CloseTimeMs_{symbol}"] = item.get("close_time_ms")
        row[f"FundLastUpdated_{symbol}"] = item.get("fund_last_updated")
    return row
