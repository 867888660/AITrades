from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any, Iterable

from services.finance_service import fetch_finance_quotes
from services.sec_edgar_service import fetch_sec_company_facts, fetch_sec_submissions


class DataSourceConnectionError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _symbols(values: str | Iterable[Any]) -> list[str]:
    raw = values.split(",") if isinstance(values, str) else list(values)
    result: list[str] = []
    for value in raw:
        symbol = str(value or "").strip().upper()
        if symbol and not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,14}", symbol):
            raise ValueError(f"Invalid equity symbol: {symbol}")
        if symbol and symbol not in result:
            result.append(symbol)
    if not result:
        raise ValueError("At least one equity symbol is required")
    if len(result) > 20:
        raise ValueError("At most 20 equity symbols may be requested")
    return result


class DataSourceConnectionService:
    """Bounded online reads for connection tests and live equity snapshots."""

    def __init__(self, settings: dict[str, Any]):
        self.settings = dict(settings)

    def equity_quotes(self, symbols: str | Iterable[Any]) -> dict[str, Any]:
        selected = _symbols(symbols)
        api_key = str(self.settings.get("active_finnhub_api_key") or "").strip()
        if not api_key:
            raise ValueError("Finnhub API Key is not configured")
        result = fetch_finance_quotes(selected, api_key)
        usable = [row for row in result.get("data", []) if row.get("price") is not None]
        if not result.get("ok") or not usable:
            raise DataSourceConnectionError("Finnhub returned no usable quote")
        return result

    def sec_company_facts(self, cik: Any, concepts: Iterable[Any] | None = None) -> dict[str, Any]:
        return fetch_sec_company_facts(
            cik,
            user_agent=self.settings.get("sec_edgar_user_agent"),
            concepts=concepts,
        )

    def test(self, source_id: Any) -> dict[str, Any]:
        source = str(source_id or "").strip().upper()
        started = time.time()
        if source == "FINNHUB":
            quote = self.equity_quotes(["AAPL"])
            row = next(item for item in quote["data"] if item.get("price") is not None)
            detail = {"symbol": row.get("symbol"), "price_available": True}
        elif source == "SEC":
            result = fetch_sec_submissions(
                "0000320193",
                user_agent=self.settings.get("sec_edgar_user_agent"),
            )
            detail = {
                "cik": result.get("cik"),
                "entity_name": result.get("entity_name"),
                "latest_filing": result.get("latest_filing"),
            }
        else:
            raise ValueError(f"Connection test is not supported for {source or '(empty)'}")
        return {
            "source_id": source,
            "ok": True,
            "checked_at": _utc_now(),
            "latency_ms": int((time.time() - started) * 1000),
            "detail": detail,
        }
