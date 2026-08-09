#!/usr/bin/env python3
"""Build a research-only US pre-market market-cap ranking snapshot.

The script uses DataTube controlled local APIs. It does not create a Factor,
Alpha, strategy, backtest, approval, or trade. Historical ranks use a current
shares proxy and are therefore exploratory rather than PIT-valid evidence.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, time
from typing import Any


DEFAULT_BASE_URL = "http://127.0.0.1:5001"
SESSION = "PREMARKET_0400_0930_ET"
COMPANY_LABELS = {
    "NVDA": "NVIDIA",
    "AAPL": "Apple",
    "GOOGL": "Alphabet",
    "MSFT": "Microsoft",
    "AMZN": "Amazon",
    "META": "Meta",
    "TSLA": "Tesla",
}


def request_json(base_url: str, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(base_url.rstrip("/") + path, data=body, method=method, headers=headers)
    with urllib.request.urlopen(request, timeout=90) as response:
        result = json.loads(response.read().decode("utf-8"))
    if isinstance(result, dict) and result.get("ok") is False:
        raise RuntimeError(str(result.get("error") or result))
    return result


def local_time(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    # OpenBB/yfinance equity timestamps are exchange-local. Keep the returned
    # wall clock so the stdlib-only skill does not require Windows tzdata.
    return parsed


def premarket_dates(rows: list[dict[str, Any]]) -> set[date]:
    return {
        stamp.date()
        for row in rows
        for stamp in [local_time(row.get("date"))]
        if time(4, 0) <= stamp.time() < time(9, 30)
    }


def session_prices(rows: list[dict[str, Any]], session_date: date) -> tuple[float, float, str]:
    premarket: list[tuple[datetime, float]] = []
    prior_regular: list[tuple[datetime, float]] = []
    for row in rows:
        stamp = local_time(row.get("date"))
        close = float(row.get("close"))
        if stamp.date() == session_date and time(4, 0) <= stamp.time() < time(9, 30):
            premarket.append((stamp, close))
        if stamp.date() < session_date and time(9, 30) <= stamp.time() < time(16, 0):
            prior_regular.append((stamp, close))
    if not premarket:
        raise ValueError(f"no pre-market bars for {session_date.isoformat()}")
    if not prior_regular:
        raise ValueError(f"no prior regular-session close before {session_date.isoformat()}")
    premarket.sort(key=lambda item: item[0])
    prior_regular.sort(key=lambda item: item[0])
    return prior_regular[-1][1], premarket[-1][1], premarket[-1][0].isoformat()


def rank_rows(rows: list[dict[str, Any]], value_key: str, rank_key: str) -> None:
    for rank, row in enumerate(sorted(rows, key=lambda item: float(item[value_key]), reverse=True), start=1):
        row[rank_key] = rank


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--symbols", default="NVDA,AAPL,GOOGL,MSFT,AMZN,META,TSLA")
    parser.add_argument("--interval", choices=("1m", "5m"), default="5m")
    parser.add_argument("--end-date", default=date.today().isoformat(), help="Exclusive upstream end date")
    parser.add_argument("--lookback-days", type=int, default=7)
    parser.add_argument("--event-slug", default="largest-company-end-of-december-2026")
    args = parser.parse_args()

    end_day = date.fromisoformat(args.end_date)
    max_days = 8 if args.interval == "1m" else 60
    lookback_days = max(3, min(max_days, args.lookback_days))
    start_day = end_day - timedelta(days=lookback_days)
    symbols = list(dict.fromkeys(item.strip().upper() for item in args.symbols.split(",") if item.strip()))
    histories: dict[str, list[dict[str, Any]]] = {}
    quotes: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, str]] = []

    for symbol in symbols:
        try:
            history = request_json(args.base_url, "POST", "/api/research/data/providers/openbb/equity/historical", {
                "symbol": symbol,
                "provider": "yfinance",
                "interval": args.interval,
                "session": SESSION,
                "start_date": start_day.isoformat(),
                "end_date": end_day.isoformat(),
                "adjustment": "splits_only",
            })["data"]
            query = urllib.parse.urlencode({
                "provider": "OPENBB", "market": "EQUITY", "category": "equity", "q": symbol,
            })
            quote_payload = request_json(args.base_url, "GET", f"/api/research/instruments/search?{query}")
            quote = next((item for item in quote_payload.get("data", []) if item.get("symbol") == symbol), None)
            if not quote or not quote.get("market_cap_usd") or not quote.get("price"):
                raise ValueError("current quote/profile has no price or market_cap_usd")
            histories[symbol] = list(history.get("results") or [])
            quotes[symbol] = quote
        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc)})

    if len(histories) < 2:
        raise RuntimeError(f"fewer than two symbols have usable pre-market data: {errors}")
    date_coverage = {
        symbol: sorted(day.isoformat() for day in premarket_dates(rows))
        for symbol, rows in histories.items()
    }
    common_dates = set.intersection(*(premarket_dates(rows) for rows in histories.values()))
    if not common_dates:
        raise RuntimeError(
            "no common pre-market session date across the usable symbols: "
            + json.dumps({"date_coverage": date_coverage, "errors": errors}, ensure_ascii=False)
        )
    selected_date = max(common_dates)

    ranking: list[dict[str, Any]] = []
    for symbol, rows in histories.items():
        try:
            previous_close, premarket_close, available_time = session_prices(rows, selected_date)
            quote = quotes[symbol]
            shares_proxy = float(quote["market_cap_usd"]) / float(quote["price"])
            ranking.append({
                "symbol": symbol,
                "company": COMPANY_LABELS.get(symbol, symbol),
                "previous_close": previous_close,
                "premarket_close": premarket_close,
                "overnight_return": premarket_close / previous_close - 1.0,
                "shares_proxy": shares_proxy,
                "previous_close_cap_proxy": shares_proxy * previous_close,
                "premarket_cap_proxy": shares_proxy * premarket_close,
                "available_time": available_time,
            })
        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc)})

    rank_rows(ranking, "previous_close_cap_proxy", "previous_rank")
    rank_rows(ranking, "premarket_cap_proxy", "premarket_rank")

    market_query = urllib.parse.urlencode({"q": "market cap", "sort": "volume24h", "limit": 50})
    market_payload = request_json(args.base_url, "GET", f"/api/agent/markets?{market_query}")
    candidates = [
        item for item in market_payload.get("data", {}).get("data", [])
        if item.get("event_slug") == args.event_slug
    ]
    probabilities = {
        str(item.get("group_item_title")): float(item.get("yes_price"))
        for item in candidates if item.get("group_item_title") and item.get("yes_price") not in (None, "")
    }
    probability_order = {
        company: rank for rank, (company, _price) in enumerate(
            sorted(probabilities.items(), key=lambda item: item[1], reverse=True), start=1
        )
    }
    for row in ranking:
        row["rank_change"] = row["premarket_rank"] - row["previous_rank"]
        row["polymarket_probability"] = probabilities.get(row["company"])
        row["polymarket_probability_rank"] = probability_order.get(row["company"])
        probability_rank = row["polymarket_probability_rank"]
        row["market_vs_premarket_rank_gap"] = (
            probability_rank - row["premarket_rank"] if probability_rank is not None else None
        )
    ranking.sort(key=lambda item: item["premarket_rank"])

    changed = [row["symbol"] for row in ranking if row["rank_change"]]
    output = {
        "ok": True,
        "status": "EXPLORATORY_SNAPSHOT_READY",
        "session": SESSION,
        "session_date": selected_date.isoformat(),
        "interval": args.interval,
        "coverage": {"requested": len(symbols), "usable": len(ranking), "errors": errors},
        "ranking": ranking,
        "snapshot_diagnostic": {
            "rank_changes": changed,
            "interpretation": (
                "The selected pre-market session changed at least one proxy rank."
                if changed else
                "The selected pre-market session did not change the proxy market-cap ordering."
            ),
        },
        "methodology": {
            "cap_proxy": "current_market_cap/current_quote_price * historical_price",
            "premarket_window": "04:00 <= America/New_York < 09:30",
            "availability": "each bar is usable only after its bar end",
        },
        "limitations": [
            "shares_proxy is derived from the current quote/profile and is not historical point-in-time shares outstanding",
            "this snapshot cannot establish predictive usefulness or support a formal PIT Factor Evaluation",
            "SpaceX and Saudi Aramco are outside the default comparable US-equity quote set",
        ],
        "next_valid_test": "collect PIT shares/market-cap snapshots prospectively, then evaluate lead-lag versus Polymarket outcome-price history",
        "strategy_created": False,
        "trade_executed": False,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"ok": False, "status": "FAILED", "error": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(1)
