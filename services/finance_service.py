from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

from services.crypto_service import parse_symbols
from services.http_client import SESSION, get_timeout


FINNHUB_API = "https://finnhub.io/api/v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any):
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _resolve_api_key(api_key: str | None = None) -> str:
    if api_key and api_key.strip():
        return api_key.strip()
    return os.getenv("FINNHUB_API_KEY", "").strip()


def fetch_finance_quotes(symbols_text: str | list[str], api_key: str | None = None) -> Dict[str, Any]:
    symbols = parse_symbols(symbols_text, uppercase=True)
    token = _resolve_api_key(api_key)
    if not symbols:
        return {"ok": False, "error": "No finance symbols provided.", "data": []}
    if not token:
        return {"ok": False, "error": "Missing Finnhub API key. Set FINNHUB_API_KEY or pass api_key.", "data": []}

    started = time.time()
    rows: List[Dict[str, Any]] = []
    errors: List[str] = []
    for symbol in symbols:
        row: Dict[str, Any] = {"symbol": symbol, "source": "finnhub_quote_profile2"}
        try:
            quote_resp = SESSION.get(
                f"{FINNHUB_API}/quote",
                params={"symbol": symbol, "token": token},
                timeout=get_timeout(),
            )
            quote_resp.raise_for_status()
            quote = quote_resp.json()
            row["price"] = _safe_float(quote.get("c"))
            row["change"] = _safe_float(quote.get("d"))
            row["change_percent"] = _safe_float(quote.get("dp"))
            row["high"] = _safe_float(quote.get("h"))
            row["low"] = _safe_float(quote.get("l"))
            row["open"] = _safe_float(quote.get("o"))
            row["previous_close"] = _safe_float(quote.get("pc"))
        except Exception as exc:  # pragma: no cover
            row["error_quote"] = repr(exc)

        try:
            profile_resp = SESSION.get(
                f"{FINNHUB_API}/stock/profile2",
                params={"symbol": symbol, "token": token},
                timeout=get_timeout(),
            )
            profile_resp.raise_for_status()
            profile = profile_resp.json()
            row["company_name"] = profile.get("name")
            row["exchange"] = profile.get("exchange")
            row["currency"] = profile.get("currency")
            row["market_cap_musd"] = _safe_float(profile.get("marketCapitalization"))
            row["market_cap_usd"] = row["market_cap_musd"] * 1_000_000 if row.get("market_cap_musd") is not None else None
        except Exception as exc:  # pragma: no cover
            row["error_profile2"] = repr(exc)

        row["ts_utc"] = _utc_now()
        if row.get("error_quote") or row.get("error_profile2"):
            errors.append(f"{symbol}: quote={row.get('error_quote')} profile2={row.get('error_profile2')}")
        rows.append(row)

    return {
        "ok": True,
        "ts_utc": _utc_now(),
        "latency_ms": int((time.time() - started) * 1000),
        "count": len(rows),
        "data": rows,
        "errors": errors,
    }


def build_finance_snapshot_row(payload: Dict[str, Any]) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "ok": payload.get("ok"),
        "ts_utc": payload.get("ts_utc"),
        "latency_ms": payload.get("latency_ms"),
        "count": payload.get("count"),
        "data": json.dumps(payload.get("data", []), ensure_ascii=False),
    }
    for item in payload.get("data", []):
        symbol = str(item.get("symbol", "")).upper().strip()
        if not symbol:
            continue
        row[f"Price_{symbol}"] = item.get("price")
        row[f"McapUsd_{symbol}"] = item.get("market_cap_usd")
        row[f"ts_utc_{symbol}"] = item.get("ts_utc")
        row[f"source_{symbol}"] = item.get("source")
        row[f"error_{symbol}"] = item.get("error_profile2")
        row[f"error_quote_{symbol}"] = item.get("error_quote")
    return row
