from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from services.openbb_provider_service import OpenBBProviderService, normalize_equity_adjustment

from .canonical_bars import CanonicalBarsCommitter
from .instrument_registry import InstrumentRegistry, make_instrument_id
from .models import Instrument
from .provenance_service import ManifestProvenanceService
from .store import DataPlatformStore, get_default_store


OPENBB_ADAPTER_VERSION = "openbb_equity_daily.v1"
SUPPORTED_VENUES = {"XNAS", "XNYS"}
SUPPORTED_ADJUSTMENTS = {"splits_only", "splits_and_dividends"}


def _parse_date(value: Any) -> date:
    text = str(value or "").strip()
    if not text:
        raise ValueError("OpenBB historical row is missing date")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return date.fromisoformat(text[:10])


def _finite_price(value: Any, field: str, event_date: date) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"OpenBB {field} is missing or invalid at {event_date}") from exc
    if result <= 0:
        raise ValueError(f"OpenBB {field} must be positive at {event_date}")
    return result


def _canonical_daily_rows(
    rows: list[dict[str, Any]],
    *,
    instrument_id: str,
    source: str,
    ingested_at: str,
) -> tuple[list[dict[str, Any]], int]:
    now = datetime.now(timezone.utc)
    canonical: list[dict[str, Any]] = []
    seen: set[date] = set()
    excluded = 0
    for raw in rows:
        event_date = _parse_date(raw.get("date"))
        if event_date in seen:
            raise ValueError(f"duplicate OpenBB daily bar: {event_date}")
        seen.add(event_date)
        open_price = _finite_price(raw.get("open"), "open", event_date)
        high = _finite_price(raw.get("high"), "high", event_date)
        low = _finite_price(raw.get("low"), "low", event_date)
        close = _finite_price(raw.get("close"), "close", event_date)
        if high < low or not low <= open_price <= high or not low <= close <= high:
            raise ValueError(f"invalid OpenBB OHLC range at {event_date}")
        volume_raw = raw.get("volume")
        volume = 0.0 if volume_raw in (None, "") else float(volume_raw)
        if volume < 0:
            raise ValueError(f"negative OpenBB volume at {event_date}")

        # A conservative D+1 UTC availability boundary avoids assuming a
        # provider-specific exchange close or early-close calendar.  It is
        # deliberately later than XNAS/XNYS regular close and prevents the
        # current trading day's partial bar from entering a Manifest.
        bar_start = datetime.combine(event_date, time.min, tzinfo=timezone.utc)
        available = datetime.combine(event_date + timedelta(days=1), time.min, tzinfo=timezone.utc)
        if available > now:
            excluded += 1
            continue
        canonical.append({
            "instrument_id": instrument_id,
            "frequency": "1d",
            "bar_start_time": bar_start.isoformat(),
            "bar_end_time": available.isoformat(),
            "available_time": available.isoformat(),
            "ingested_at": ingested_at,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "turnover": 0.0,
            "trade_count": 0,
            "bar_status": "COMPLETE",
            "source": source,
            "source_version": OPENBB_ADAPTER_VERSION,
            "quality_status": "PASS",
        })
    canonical.sort(key=lambda item: item["bar_start_time"])
    return canonical, excluded


class OpenBBEquityHistoryAdapter:
    """Convert OpenBB equity daily output into immutable DataTube bars.v1."""

    def __init__(
        self,
        settings: dict[str, Any],
        *,
        output_root: str | Path | None = None,
        store: DataPlatformStore | None = None,
        provider_service: OpenBBProviderService | None = None,
    ):
        self.settings = settings
        self.store = store or get_default_store()
        self.provider_service = provider_service or OpenBBProviderService(settings)
        self.committer = CanonicalBarsCommitter(self.store, output_root)
        self.catalog = self.committer.catalog
        self.registry = InstrumentRegistry(self.store)
        self.provenance = ManifestProvenanceService(self.store)

    def export(self, payload: dict[str, Any]) -> dict[str, Any]:
        symbol = str(payload.get("symbol") or "").strip().upper()
        venue = str(payload.get("venue") or "").strip().upper()
        currency = str(payload.get("currency") or "USD").strip().upper()
        adjustment = normalize_equity_adjustment(payload.get("adjustment"))
        if not symbol:
            raise ValueError("symbol is required")
        if venue not in SUPPORTED_VENUES:
            raise ValueError("venue must be XNAS or XNYS for OpenBB equity daily export")
        if adjustment not in SUPPORTED_ADJUSTMENTS:
            raise ValueError(f"unsupported adjustment policy: {adjustment}")

        upstream = self.provider_service.fetch_equity_historical({**payload, "interval": "1d", "adjustment": adjustment})
        provider = str(upstream["upstream_provider"]).strip().lower()
        instrument_id = str(payload.get("instrument_id") or "").strip() or make_instrument_id("equity", venue, symbol)
        if not self.registry.get(instrument_id):
            self.registry.register(
                Instrument(
                    instrument_id=instrument_id,
                    asset_class="equity",
                    venue=venue,
                    market_type="EQUITY",
                    native_symbol=symbol,
                    display_symbol=symbol,
                    currency=currency,
                    timezone="America/New_York",
                    trading_calendar=venue,
                    metadata={"openbb_gateway": True},
                ),
                aliases=[(f"openbb:{provider}", symbol)],
            )
        source = f"OPENBB/{provider.upper()}"
        ingested_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        rows, excluded = _canonical_daily_rows(
            upstream["results"], instrument_id=instrument_id, source=source, ingested_at=ingested_at
        )
        if not rows:
            raise ValueError(f"no completed OpenBB daily bars found for {symbol}")
        dataset_id = f"openbb:{provider}:{instrument_id}:bars:1d:{adjustment}"
        result = self.committer.commit(
            dataset_id=dataset_id,
            instrument_id=instrument_id,
            asset_class="equity",
            venue=venue,
            frequency="1d",
            source=source,
            source_version=f"{OPENBB_ADAPTER_VERSION}:{provider}",
            rows=rows,
            excluded_incomplete_rows=excluded,
            adjustment=adjustment,
        )
        provenance = self.provenance.record(
            manifest_id=result["manifest"].manifest_id,
            dataset_id=result["dataset_id"],
            gateway="OPENBB",
            upstream_provider=provider,
            endpoint="equity.price.historical",
            request=payload,
            gateway_version=OPENBB_ADAPTER_VERSION,
            provider_version=str(upstream.get("provider_version") or ""),
            source_policy=payload.get("source_policy") if isinstance(payload.get("source_policy"), dict) else None,
        )
        return {
            **result,
            "symbol": symbol,
            "venue": venue,
            "currency": currency,
            "gateway": "openbb",
            "upstream_provider": provider,
            "adjustment": adjustment,
            "availability_policy": "CONSERVATIVE_D_PLUS_1_UTC",
            "warnings": upstream.get("warnings", []),
            "provenance": provenance,
        }
