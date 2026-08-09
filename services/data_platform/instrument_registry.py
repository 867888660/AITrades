from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from .models import Instrument
from .store import DataPlatformStore, json_dumps


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _upper(value: Any) -> str:
    return _clean(value).upper()


def make_instrument_id(
    asset_class: str,
    venue: str,
    native_symbol: str,
    *,
    condition_id: str = "",
    outcome_side: str = "",
) -> str:
    asset = _clean(asset_class).lower()
    venue_text = _upper(venue)
    symbol = _upper(native_symbol)
    if asset == "prediction_market":
        condition = _clean(condition_id)
        side = _upper(outcome_side)
        if not condition or side not in {"YES", "NO"}:
            raise ValueError("prediction_market instruments require condition_id and YES/NO outcome_side")
        return f"prediction_market:{venue_text}:{condition}:{side}"
    if not asset or not venue_text or not symbol:
        raise ValueError("asset_class, venue, and native_symbol are required")
    return f"{asset}:{venue_text}:{symbol}"


class InstrumentRegistry:
    def __init__(self, store: DataPlatformStore):
        self.store = store

    def register(self, instrument: Instrument, *, aliases: list[tuple[str, str]] | None = None) -> Instrument:
        if not instrument.instrument_id:
            raise ValueError("instrument_id is required")
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                """
                INSERT INTO instrument_registry(
                    instrument_id, asset_class, venue, market_type, native_symbol,
                    display_symbol, display_name, underlying_id, base_asset, quote_asset,
                    currency, condition_id, market_id, event_id, outcome_side,
                    listing_time, delisting_time, timezone, trading_calendar,
                    tick_size, lot_size, status, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(instrument_id) DO UPDATE SET
                    display_symbol=excluded.display_symbol,
                    display_name=excluded.display_name,
                    underlying_id=excluded.underlying_id,
                    base_asset=excluded.base_asset,
                    quote_asset=excluded.quote_asset,
                    currency=excluded.currency,
                    listing_time=excluded.listing_time,
                    delisting_time=excluded.delisting_time,
                    timezone=excluded.timezone,
                    trading_calendar=excluded.trading_calendar,
                    tick_size=excluded.tick_size,
                    lot_size=excluded.lot_size,
                    status=excluded.status,
                    metadata_json=excluded.metadata_json,
                    updated_at=excluded.updated_at
                """,
                (
                    instrument.instrument_id,
                    instrument.asset_class,
                    instrument.venue,
                    instrument.market_type,
                    instrument.native_symbol,
                    instrument.display_symbol,
                    instrument.display_name,
                    instrument.underlying_id,
                    instrument.base_asset,
                    instrument.quote_asset,
                    instrument.currency,
                    instrument.condition_id,
                    instrument.market_id,
                    instrument.event_id,
                    instrument.outcome_side,
                    instrument.listing_time,
                    instrument.delisting_time,
                    instrument.timezone,
                    instrument.trading_calendar,
                    instrument.tick_size,
                    instrument.lot_size,
                    instrument.status,
                    json_dumps(instrument.metadata),
                    now,
                    now,
                ),
            )
            for source, source_symbol in aliases or []:
                source_text = _clean(source)
                symbol_text = _clean(source_symbol)
                if not source_text or not symbol_text:
                    raise ValueError("alias source and source_symbol are required")
                conn.execute(
                    """
                    INSERT INTO instrument_aliases(source, source_symbol, instrument_id, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(source, source_symbol) DO UPDATE SET
                        instrument_id=excluded.instrument_id
                    """,
                    (source_text, symbol_text, instrument.instrument_id, now),
                )
        return instrument

    def register_discovered(
        self,
        payload: dict[str, Any],
        *,
        source: str,
        market: str,
    ) -> Instrument:
        """Promote a discovery result into the canonical local Registry."""
        source_text = _upper(source)
        market_text = _upper(market)
        raw_id = _clean(payload.get("instrument_id"))
        id_parts = raw_id.split(":", 2)
        raw_asset_class = _clean(payload.get("asset_class") or (id_parts[0] if len(id_parts) == 3 else ""))
        symbol = _clean(
            payload.get("symbol")
            or payload.get("native_symbol")
            or payload.get("token_id")
            or (id_parts[2] if len(id_parts) == 3 else "")
        )
        if not raw_asset_class or not symbol:
            raise ValueError("Discovery result requires asset_class and symbol")

        asset_class = {
            "polymarket": "polymarket_binary",
            "fred": "macro",
        }.get(raw_asset_class.lower(), raw_asset_class.lower())
        if asset_class == "equity":
            venue = self._equity_venue(payload, source=source_text, market=market_text)
        elif asset_class == "macro":
            venue = "FRED"
        elif asset_class == "polymarket_binary":
            venue = "POLYMARKET"
        else:
            venue = _upper(payload.get("venue") or (id_parts[1] if len(id_parts) == 3 else "") or source_text)

        instrument_id = make_instrument_id(asset_class, venue, symbol)
        display_symbol = _clean(payload.get("display_symbol") or symbol)
        display_name = _clean(payload.get("display_name") or payload.get("name") or display_symbol)
        status = _upper(payload.get("status") or "ACTIVE")
        aliases = list(dict.fromkeys([
            (source_text.lower(), symbol.upper()),
            (venue.lower(), symbol.upper()),
        ]))
        instrument = Instrument(
            instrument_id=instrument_id,
            asset_class=asset_class,
            venue=venue,
            market_type=market_text or _upper(payload.get("market_kind") or asset_class),
            native_symbol=symbol.upper(),
            display_symbol=display_symbol,
            display_name=display_name,
            underlying_id=_clean(payload.get("underlying_id")),
            base_asset=_upper(payload.get("base_asset")),
            quote_asset=_upper(payload.get("quote_asset")),
            currency=_upper(payload.get("currency")),
            condition_id=_clean(payload.get("condition_id")),
            market_id=_clean(payload.get("market_id")),
            event_id=_clean(payload.get("event_id")),
            outcome_side=_upper(payload.get("outcome") or payload.get("outcome_side")),
            listing_time=_clean(payload.get("listing_time")) or None,
            delisting_time=_clean(payload.get("delisting_time") or payload.get("end_date")) or None,
            status=status,
            metadata={
                "discovery_source": source_text,
                "discovery_market": market_text,
                "category": _clean(payload.get("category")),
            },
        )
        return self.register(instrument, aliases=aliases)

    @staticmethod
    def _equity_venue(payload: dict[str, Any], *, source: str, market: str) -> str:
        if market in {"XNAS", "XNYS", "XASE", "ARCX", "OTCM"}:
            return market
        exchange = _upper(payload.get("exchange") or payload.get("venue"))
        if "NASDAQ" in exchange:
            return "XNAS"
        if "NYSE ARCA" in exchange:
            return "ARCX"
        if "AMERICAN STOCK EXCHANGE" in exchange or "NYSE AMERICAN" in exchange or exchange == "AMEX":
            return "XASE"
        if "NEW YORK STOCK EXCHANGE" in exchange or exchange == "NYSE":
            return "XNYS"
        if "OTC" in exchange:
            return "OTCM"
        return "US"

    def get(self, instrument_id: str) -> Optional[dict[str, Any]]:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM instrument_registry WHERE instrument_id = ?",
                (_clean(instrument_id),),
            ).fetchone()
        return self.store.row_to_dict(row)

    def resolve_alias(self, source: str, source_symbol: str) -> Optional[str]:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT instrument_id FROM instrument_aliases WHERE source = ? AND source_symbol = ?",
                (_clean(source), _clean(source_symbol)),
            ).fetchone()
        return str(row[0]) if row else None

    def list(self, *, asset_class: str = "", venue: str = "", status: str = "") -> list[dict[str, Any]]:
        clauses = []
        params: list[str] = []
        if _clean(asset_class):
            clauses.append("asset_class = ?")
            params.append(_clean(asset_class).lower())
        if _clean(venue):
            clauses.append("venue = ?")
            params.append(_upper(venue))
        if _clean(status):
            clauses.append("status = ?")
            params.append(_upper(status))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.store.connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM instrument_registry{where} ORDER BY instrument_id",
                params,
            ).fetchall()
        return [dict(row) for row in rows]
