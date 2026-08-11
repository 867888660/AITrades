from __future__ import annotations

from typing import Any, Mapping


OPENBB_PROVIDER_DEFINITIONS: dict[str, dict[str, Any]] = {
    "yfinance": {
        "label": "Yahoo Finance / YFinance",
        "package": "openbb-yfinance==1.6.3",
        "credential_keys": (),
        "formal_capabilities": ("EQUITY:1D:BARS",),
    },
    "polygon": {
        "label": "Polygon / Massive",
        "package": "openbb-polygon==1.5.1",
        "credential_keys": ("polygon_api_key",),
        "formal_capabilities": ("EQUITY:1D:BARS",),
    },
    "tiingo": {
        "label": "Tiingo",
        "package": "openbb-tiingo==1.6.1",
        "credential_keys": ("tiingo_token",),
        "formal_capabilities": ("EQUITY:1D:BARS",),
    },
    "fmp": {
        "label": "Financial Modeling Prep",
        "package": "openbb-fmp==1.6.1",
        "credential_keys": ("fmp_api_key",),
        "formal_capabilities": ("EQUITY:1D:BARS",),
    },
    "intrinio": {
        "label": "Intrinio",
        "package": "openbb-intrinio==1.6.1",
        "credential_keys": ("intrinio_api_key",),
        "formal_capabilities": ("EQUITY:1D:BARS",),
    },
    "fred": {
        "label": "FRED",
        "package": "openbb-fred==1.6.1",
        "credential_keys": ("fred_api_key",),
        "formal_capabilities": (),
    },
}

OPENBB_CREDENTIAL_ENV = {
    "fmp_api_key": "FMP_API_KEY",
    "fred_api_key": "FRED_API_KEY",
    "intrinio_api_key": "INTRINIO_API_KEY",
    "polygon_api_key": "POLYGON_API_KEY",
    "tiingo_token": "TIINGO_TOKEN",
}
OPENBB_CREDENTIAL_KEYS = tuple(sorted(OPENBB_CREDENTIAL_ENV))
OPENBB_EQUITY_DAILY_PROVIDERS = tuple(
    provider_id
    for provider_id, definition in OPENBB_PROVIDER_DEFINITIONS.items()
    if "EQUITY:1D:BARS" in definition["formal_capabilities"]
)

DATA_SOURCE_POLICY_LABELS = {
    "EQUITY:1D:BARS": "US Equity · Daily OHLCV",
    "EQUITY:SNAPSHOT:QUOTE": "US Equity · Quote Snapshot",
    "CRYPTO_SPOT:*:BARS": "Crypto Spot · Historical Bars",
    "CRYPTO:SNAPSHOT:SNAPSHOT": "Crypto · Context Snapshot",
    "POLYMARKET_BINARY:*:PRICE_HISTORY": "Polymarket · Price History",
    "MACRO:1D:SERIES": "Macro · Daily Series",
}

DEFAULT_DATA_SOURCE_SETTINGS: dict[str, Any] = {
    "mode": "HYBRID",
    "version": 1,
    "priority_orders": {
        "EQUITY:1D:BARS": [
            "OPENBB:YFINANCE",
            "OPENBB:POLYGON",
            "OPENBB:TIINGO",
            "OPENBB:FMP",
            "OPENBB:INTRINIO",
        ],
        "EQUITY:SNAPSHOT:QUOTE": ["FINNHUB"],
        "CRYPTO_SPOT:*:BARS": ["BINANCE"],
        "CRYPTO:SNAPSHOT:SNAPSHOT": ["COINGECKO"],
        "POLYMARKET_BINARY:*:PRICE_HISTORY": ["POLYMARKET"],
        "MACRO:1D:SERIES": ["OPENBB:FRED"],
    },
}


def _clean_source_ids(values: Any) -> list[str]:
    if isinstance(values, str):
        values = [values]
    result: list[str] = []
    for value in values or []:
        source_id = str(value or "").strip().upper()
        if source_id and source_id not in result:
            result.append(source_id)
    return result


def normalize_data_source_settings(value: Any) -> dict[str, Any]:
    raw = dict(value) if isinstance(value, Mapping) else {}
    mode = str(raw.get("mode") or DEFAULT_DATA_SOURCE_SETTINGS["mode"]).strip().upper()
    if mode not in {"MANUAL", "HYBRID", "AUTO"}:
        raise ValueError(f"unsupported Data Source routing mode: {mode}")
    try:
        version = max(1, int(raw.get("version") or 1))
    except (TypeError, ValueError):
        version = 1
    incoming = raw.get("priority_orders") if isinstance(raw.get("priority_orders"), Mapping) else {}
    priority_orders: dict[str, list[str]] = {}
    for policy_key, defaults in DEFAULT_DATA_SOURCE_SETTINGS["priority_orders"].items():
        configured = incoming.get(policy_key, defaults)
        ordered = _clean_source_ids(configured)
        for source_id in defaults:
            if source_id not in ordered:
                ordered.append(source_id)
        priority_orders[policy_key] = ordered
    return {"mode": mode, "version": version, "priority_orders": priority_orders}


def openbb_equity_provider_sequence(
    settings: Mapping[str, Any],
    *,
    preferred_sources: Any = None,
    allowed_sources: Any = None,
) -> list[str]:
    """Return a stable whole-request OpenBB sequence for equity daily bars."""

    openbb = settings.get("openbb_settings") if isinstance(settings.get("openbb_settings"), Mapping) else {}
    configured_allowed = {
        str(item).strip().lower()
        for item in openbb.get("allowed_providers", [])
        if str(item).strip()
    }
    policy_allowed = {
        str(item).strip().lower()
        for item in (allowed_sources or [])
        if str(item).strip()
    }
    if configured_allowed and policy_allowed:
        eligible = configured_allowed & policy_allowed
    else:
        eligible = policy_allowed or configured_allowed or {str(openbb.get("default_provider") or "yfinance").lower()}
    eligible &= set(OPENBB_EQUITY_DAILY_PROVIDERS)

    result: list[str] = []
    for value in preferred_sources or []:
        provider = str(value or "").strip().lower().removeprefix("openbb:")
        if provider in eligible and provider not in result:
            result.append(provider)
    data_source_settings = normalize_data_source_settings(settings.get("data_source_settings"))
    for source_id in data_source_settings["priority_orders"]["EQUITY:1D:BARS"]:
        if not source_id.startswith("OPENBB:"):
            continue
        provider = source_id.split(":", 1)[1].lower()
        if provider in eligible and provider not in result:
            result.append(provider)
    for provider in OPENBB_EQUITY_DAILY_PROVIDERS:
        if provider in eligible and provider not in result:
            result.append(provider)
    return result
