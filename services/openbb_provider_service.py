from __future__ import annotations

import socket
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict
from urllib.parse import urlparse

from services.http_client import SESSION


DEFAULT_OPENBB_BASE_URL = "http://127.0.0.1:6901"
SUPPORTED_ENDPOINTS = ("equity.price.historical", "economy.fred_series")
SUPPORTED_EQUITY_ADJUSTMENTS = ("splits_only", "splits_and_dividends")
SUPPORTED_EQUITY_INTERVALS = ("1d", "1m", "5m")
PREMARKET_SESSION = "PREMARKET_0400_0930_ET"
_INTRADAY_MAX_DAYS = {"1m": 8, "5m": 60}


def normalize_equity_adjustment(value: Any) -> str:
    """Map DataTube adjustment semantics to values accepted by OpenBB equity history."""
    adjustment = str(value or "splits_only").strip().lower()
    normalized = {
        "none": "splits_only",
        "unadjusted": "splits_only",
        "split": "splits_only",
        "splits": "splits_only",
        "total_return": "splits_and_dividends",
    }.get(adjustment, adjustment)
    if normalized not in SUPPORTED_EQUITY_ADJUSTMENTS:
        raise ValueError(f"Unsupported OpenBB equity adjustment: {adjustment}")
    return normalized


def _clean_base_url(value: Any) -> str:
    text = str(value or DEFAULT_OPENBB_BASE_URL).strip().rstrip("/")
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("OpenBB base URL must be an http(s) URL")
    return text


@dataclass(frozen=True)
class OpenBBProviderConfig:
    enabled: bool
    base_url: str
    default_provider: str
    allowed_providers: tuple[str, ...]
    timeout_sec: int

    @classmethod
    def from_settings(cls, settings: Dict[str, Any]) -> "OpenBBProviderConfig":
        raw = settings.get("openbb_settings") if isinstance(settings.get("openbb_settings"), dict) else {}
        allowed = tuple(dict.fromkeys(str(item).strip().lower() for item in raw.get("allowed_providers", []) if str(item).strip()))
        default_provider = str(raw.get("default_provider") or "yfinance").strip().lower()
        if not allowed:
            allowed = (default_provider,)
        if default_provider not in allowed:
            allowed = (default_provider, *allowed)
        return cls(
            enabled=bool(raw.get("enabled", False)),
            base_url=_clean_base_url(raw.get("base_url")),
            default_provider=default_provider,
            allowed_providers=allowed,
            timeout_sec=max(2, min(120, int(raw.get("timeout_sec") or 30))),
        )


class OpenBBProviderService:
    """Read-only adapter for an existing OpenBB REST service.

    DataTube remains responsible for instrument identity, canonical schemas,
    quality checks, Catalog and Manifest persistence.
    """

    def __init__(self, settings: Dict[str, Any]):
        self.config = OpenBBProviderConfig.from_settings(settings)

    def _require_enabled(self) -> None:
        if not self.config.enabled:
            raise ValueError("OpenBB provider is disabled in Settings")

    def _provider(self, value: Any) -> str:
        provider = str(value or self.config.default_provider).strip().lower()
        if provider not in self.config.allowed_providers:
            raise ValueError(f"OpenBB provider is not allowed: {provider}")
        return provider

    def health(self) -> Dict[str, Any]:
        started = time.perf_counter()
        if not self.config.enabled:
            return {
                "enabled": False,
                "ok": False,
                "status": "disabled",
                "base_url": self.config.base_url,
                "latency_ms": None,
                "error": None,
            }
        parsed = urlparse(self.config.base_url)
        try:
            with socket.create_connection(
                (parsed.hostname or "127.0.0.1", parsed.port or (443 if parsed.scheme == "https" else 80)),
                timeout=min(0.5, self.config.timeout_sec),
            ):
                pass
        except OSError as exc:
            return {
                "enabled": True,
                "ok": False,
                "status": "unavailable",
                "base_url": self.config.base_url,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "error": str(exc),
            }
        errors = []
        for path in ("/system/health", "/health", "/openapi.json", "/"):
            try:
                response = SESSION.get(
                    f"{self.config.base_url}{path}",
                    timeout=min(5, self.config.timeout_sec),
                    allow_redirects=True,
                )
                if response.status_code < 400:
                    return {
                        "enabled": True,
                        "ok": True,
                        "status": "healthy",
                        "base_url": self.config.base_url,
                        "probe_path": path,
                        "http_status": response.status_code,
                        "latency_ms": int((time.perf_counter() - started) * 1000),
                        "error": None,
                    }
                errors.append(f"{path}: HTTP {response.status_code}")
            except Exception as exc:
                errors.append(f"{path}: {exc}")
        return {
            "enabled": True,
            "ok": False,
            "status": "unavailable",
            "base_url": self.config.base_url,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "error": "; ".join(errors)[-1200:],
        }

    def capabilities(self) -> Dict[str, Any]:
        health = self.health()
        discovered_paths: list[str] = []
        if health.get("ok"):
            try:
                response = SESSION.get(
                    f"{self.config.base_url}/openapi.json",
                    timeout=self.config.timeout_sec,
                )
                response.raise_for_status()
                payload = response.json()
                discovered_paths = sorted(str(path) for path in (payload.get("paths") or {}).keys())
            except Exception:
                discovered_paths = []
        return {
            "gateway": "openbb",
            "enabled": self.config.enabled,
            "health": health,
            "default_provider": self.config.default_provider,
            "allowed_providers": list(self.config.allowed_providers),
            "supported_endpoints": list(SUPPORTED_ENDPOINTS),
            "discovered_path_count": len(discovered_paths),
            "historical_path_available": "/api/v1/equity/price/historical" in discovered_paths,
            "write_supported": False,
            "canonical_storage_owner": "datatube",
        }

    def fetch_equity_historical(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._require_enabled()
        symbol = str(payload.get("symbol") or "").strip().upper()
        if not symbol:
            raise ValueError("symbol is required")
        provider = self._provider(payload.get("provider"))
        interval = str(payload.get("interval") or "1d").strip().lower()
        if interval not in SUPPORTED_EQUITY_INTERVALS:
            raise ValueError(
                "OpenBB equity history supports 1d bars and the controlled "
                "1m/5m pre-market session only"
            )
        session = str(payload.get("session") or "").strip().upper()
        if interval in _INTRADAY_MAX_DAYS:
            if session != PREMARKET_SESSION:
                raise ValueError(
                    f"OpenBB equity {interval} research requires session={PREMARKET_SESSION}"
                )
            start_date = str(payload.get("start_date") or "").strip()
            end_date = str(payload.get("end_date") or "").strip()
            if not start_date or not end_date:
                raise ValueError("Pre-market equity history requires start_date and end_date")
            start_day = date.fromisoformat(start_date)
            end_day = date.fromisoformat(end_date)
            if end_day <= start_day:
                raise ValueError("Pre-market end_date must be after start_date")
            if (end_day - start_day).days > _INTRADAY_MAX_DAYS[interval]:
                raise ValueError(
                    f"OpenBB/yfinance {interval} pre-market requests are limited to "
                    f"{_INTRADAY_MAX_DAYS[interval]} calendar days"
                )
        params: Dict[str, Any] = {
            "symbol": symbol,
            "provider": provider,
            "interval": interval,
        }
        for key in ("start_date", "end_date"):
            if payload.get(key) not in (None, ""):
                params[key] = payload[key]
        if interval in _INTRADAY_MAX_DAYS:
            # Scheme A is fixed: request extended-hours upstream. Consumers
            # must retain only bars where 04:00 <= America/New_York < 09:30.
            params["extended_hours"] = True
        elif payload.get("extended_hours") not in (None, ""):
            params["extended_hours"] = bool(payload.get("extended_hours"))
        params["adjustment"] = normalize_equity_adjustment(payload.get("adjustment"))
        latest_available = bool(payload.get("latest_available", False))
        if latest_available and not params.get("end_date"):
            params["end_date"] = date.today().isoformat()
        started = time.perf_counter()
        raw: Dict[str, Any] = {}
        resolved_end_date = str(params.get("end_date") or "")
        latest_attempts = 15 if interval == "1d" and latest_available and resolved_end_date else 1
        for latest_offset in range(latest_attempts):
            if latest_offset:
                candidate = date.fromisoformat(resolved_end_date) - timedelta(days=1)
                resolved_end_date = candidate.isoformat()
                params["end_date"] = resolved_end_date
            should_try_earlier = False
            for attempt in range(3):
                response = SESSION.get(
                    f"{self.config.base_url}/api/v1/equity/price/historical",
                    params=dict(params),
                    timeout=self.config.timeout_sec,
                )
                if response.status_code == 422:
                    try:
                        detail = response.json().get("detail")
                    except Exception:
                        detail = None
                    detail_text = " ".join(str(item) for item in detail) if isinstance(detail, list) else str(detail or "")
                    if "out of range float values" in detail_text.lower():
                        if latest_available and resolved_end_date:
                            should_try_earlier = True
                            break
                        requested_range = " to ".join(
                            str(params.get(key)) for key in ("start_date", "end_date") if params.get(key)
                        )
                        suffix = f" ({requested_range})" if requested_range else ""
                        raise ValueError(
                            f"No usable OpenBB daily bars were returned for {symbol} in the requested range{suffix}. "
                            "Choose an earlier end date or use Latest available."
                        )
                response.raise_for_status()
                if response.status_code != 204:
                    raw = response.json()
                    if isinstance(raw, dict) and raw.get("results"):
                        break
                if attempt < 2:
                    time.sleep(0.4 * (attempt + 1))
            if isinstance(raw, dict) and raw.get("results"):
                break
            if interval == "1d" and latest_available and resolved_end_date:
                continue
            if not should_try_earlier:
                break
        results = raw.get("results", []) if isinstance(raw, dict) else []
        if not isinstance(results, list):
            raise ValueError("OpenBB returned an invalid historical results payload")
        if latest_available and not results:
            raise ValueError(
                f"No completed OpenBB daily bars were available for {symbol} in the requested range."
            )
        warnings = (raw.get("warnings") or []) if isinstance(raw, dict) else []
        if latest_available and resolved_end_date and resolved_end_date != str(payload.get("end_date") or ""):
            warnings = [
                *warnings,
                f"Latest available daily data was resolved through {resolved_end_date}.",
            ]
        return {
            "gateway": "openbb",
            "upstream_provider": str(raw.get("provider") or provider).lower() if isinstance(raw, dict) else provider,
            "endpoint": "equity.price.historical",
            "symbol": symbol,
            "frequency": interval,
            "session": PREMARKET_SESSION if interval in _INTRADAY_MAX_DAYS else "DAILY",
            "timezone": "America/New_York" if interval in _INTRADAY_MAX_DAYS else None,
            "row_count": len(results),
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "results": results,
            "resolved_end_date": resolved_end_date or None,
            "warnings": warnings,
            "extra": raw.get("extra", {}) if isinstance(raw, dict) else {},
        }

    def fetch_fred_series(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._require_enabled()
        symbol = str(payload.get("symbol") or "").strip().upper()
        if not symbol:
            raise ValueError("FRED series symbol is required")
        params: Dict[str, Any] = {"provider": "fred", "symbol": symbol}
        for key in ("start_date", "end_date", "limit", "frequency", "aggregation_method", "transform"):
            if payload.get(key) not in (None, ""):
                params[key] = payload[key]
        started = time.perf_counter()
        response = SESSION.get(
            f"{self.config.base_url}/api/v1/economy/fred_series",
            params=params,
            timeout=self.config.timeout_sec,
        )
        response.raise_for_status()
        raw = response.json()
        results = raw.get("results", []) if isinstance(raw, dict) else []
        if not isinstance(results, list):
            raise ValueError("OpenBB returned an invalid FRED series payload")
        return {
            "gateway": "openbb",
            "upstream_provider": "fred",
            "endpoint": "economy.fred_series",
            "symbol": symbol,
            "row_count": len(results),
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "results": results,
            "warnings": raw.get("warnings", []) if isinstance(raw, dict) else [],
            "extra": raw.get("extra", {}) if isinstance(raw, dict) else {},
        }
