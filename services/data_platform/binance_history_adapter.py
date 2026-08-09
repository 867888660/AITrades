from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .canonical_bars import CANONICAL_BAR_SCHEMA_VERSION, CanonicalBarsCommitter
from .instrument_registry import InstrumentRegistry, make_instrument_id
from .models import Instrument
from .store import BASE_DIR, DataPlatformStore, get_default_store


SOURCE_VERSION = "history_workspace_sqlite.v1"


def _to_iso_from_ms(value: Any) -> Optional[str]:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return datetime.fromtimestamp(number / 1000.0, timezone.utc).isoformat()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _canonical_rows(rows: list[sqlite3.Row], instrument_id: str, interval: str) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        start_time = _clean(row["open_time_utc"])
        available_time = _to_iso_from_ms(row["close_time_ms"]) or start_time
        result.append({
            "instrument_id": instrument_id,
            "frequency": interval,
            "bar_start_time": start_time,
            "bar_end_time": available_time,
            "available_time": available_time,
            "ingested_at": _clean(row["fetched_at_utc"]),
            "open": float(row["open"] or 0.0),
            "high": float(row["high"] or 0.0),
            "low": float(row["low"] or 0.0),
            "close": float(row["close"] or 0.0),
            "volume": float(row["volume"] or 0.0),
            "turnover": float(row["quote_volume"] or 0.0),
            "trade_count": int(row["trades"] or 0),
            "bar_status": "COMPLETE",
            "source": "BINANCE",
            "source_version": SOURCE_VERSION,
            "quality_status": "PASS",
        })
    return result


def _interval_milliseconds(interval: str) -> int:
    unit = interval[-1:]
    value = int(interval[:-1])
    multipliers = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}
    if value < 1 or unit not in multipliers:
        raise ValueError(f"unsupported Binance interval for quality checks: {interval}")
    return value * multipliers[unit]


def _validate_canonical_rows(rows: list[dict[str, Any]], interval: str) -> int:
    expected_ms = _interval_milliseconds(interval)
    previous_ms: int | None = None
    seen: set[str] = set()
    gap_count = 0
    for row in rows:
        event_time = str(row["bar_start_time"])
        if event_time in seen:
            raise ValueError(f"duplicate Binance bar: {event_time}")
        seen.add(event_time)
        open_price = float(row["open"])
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        volume = float(row["volume"])
        turnover = float(row["turnover"])
        if min(open_price, high, low, close) <= 0:
            raise ValueError(f"non-positive OHLC value at {event_time}")
        if high < low or not low <= open_price <= high or not low <= close <= high:
            raise ValueError(f"invalid OHLC range at {event_time}")
        if volume < 0 or turnover < 0:
            raise ValueError(f"negative volume or turnover at {event_time}")
        event_ms = int(datetime.fromisoformat(event_time.replace("Z", "+00:00")).timestamp() * 1000)
        if previous_ms is not None:
            delta = event_ms - previous_ms
            if delta <= 0:
                raise ValueError(f"out-of-order Binance bars at {event_time}")
            if delta % expected_ms != 0:
                raise ValueError(f"misaligned Binance bar interval at {event_time}")
            gap_count += max(0, delta // expected_ms - 1)
        previous_ms = event_ms
    return gap_count


class BinanceHistoryAdapter:
    """Convert existing Binance history into the research canonical format.

    The adapter is read-only against history_workspace.db.  It writes only to
    the research storage root and the independent Data Platform metadata DB.
    """

    def __init__(
        self,
        *,
        history_db_path: str | Path | None = None,
        output_root: str | Path | None = None,
        store: DataPlatformStore | None = None,
    ):
        self.history_db_path = Path(history_db_path or (BASE_DIR / "Data" / "history_workspace.db"))
        self.output_root = Path(output_root or (BASE_DIR / "storage" / "canonical"))
        self.store = store or get_default_store()
        self.committer = CanonicalBarsCommitter(self.store, self.output_root)
        self.catalog = self.committer.catalog
        self.registry = InstrumentRegistry(self.store)

    def export(
        self,
        *,
        symbol: str,
        interval: str = "1m",
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int | None = None,
        instrument_id: str | None = None,
        register_instrument: bool = True,
    ) -> Dict[str, Any]:
        symbol = _clean(symbol).upper()
        interval = _clean(interval).lower()
        if not symbol or not interval:
            raise ValueError("symbol and interval are required")
        if not self.history_db_path.exists():
            raise FileNotFoundError(f"history database not found: {self.history_db_path}")
        instrument_id = _clean(instrument_id) or make_instrument_id("crypto_spot", "BINANCE", symbol)
        if register_instrument and not self.registry.get(instrument_id):
            self.registry.register(
                Instrument(
                    instrument_id=instrument_id,
                    asset_class="crypto_spot",
                    venue="BINANCE",
                    market_type="SPOT",
                    native_symbol=symbol,
                    display_symbol=f"{symbol[:-4]}/USDT" if symbol.endswith("USDT") else symbol,
                    base_asset=symbol[:-4] if symbol.endswith("USDT") else symbol,
                    quote_asset="USDT" if symbol.endswith("USDT") else "",
                ),
                aliases=[("binance", symbol)],
            )

        read_only_uri = f"{self.history_db_path.resolve().as_uri()}?mode=ro"
        conn = sqlite3.connect(read_only_uri, timeout=20.0, uri=True)
        conn.row_factory = sqlite3.Row
        try:
            clauses = ["symbol = ?", "interval = ?"]
            params: list[Any] = [symbol, interval]
            if start_time:
                clauses.append("open_time_utc >= ?")
                params.append(_clean(start_time))
            if end_time:
                clauses.append("open_time_utc <= ?")
                params.append(_clean(end_time))
            limit_sql = " LIMIT ?" if limit else ""
            if limit:
                params.append(max(1, int(limit)))
            rows = conn.execute(
                f"""
                SELECT symbol, interval, open_time_utc, open, high, low, close,
                       volume, close_time_ms, quote_volume, trades, fetched_at_utc
                FROM binance_klines
                WHERE {' AND '.join(clauses)}
                ORDER BY open_time_ms
                {limit_sql}
                """,
                params,
            ).fetchall()
        finally:
            conn.close()
        if not rows:
            raise ValueError(f"no Binance history found for {symbol} {interval}")

        completed_cutoff_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        complete_rows = [
            row for row in rows
            if int(row["close_time_ms"] or 0) > 0 and int(row["close_time_ms"]) <= completed_cutoff_ms
        ]
        excluded_incomplete_rows = len(rows) - len(complete_rows)
        if not complete_rows:
            raise ValueError(f"no completed Binance history found for {symbol} {interval}")
        canonical_rows = _canonical_rows(complete_rows, instrument_id, interval)
        gap_count = _validate_canonical_rows(canonical_rows, interval)
        dataset_id = f"binance:{symbol}:{interval}"
        result = self.committer.commit(
            dataset_id=dataset_id,
            instrument_id=instrument_id,
            asset_class="crypto_spot",
            venue="BINANCE",
            frequency=interval,
            source="BINANCE",
            source_version=SOURCE_VERSION,
            rows=canonical_rows,
            gap_count=gap_count,
            excluded_incomplete_rows=excluded_incomplete_rows,
        )
        return {**result, "symbol": symbol, "interval": interval}
