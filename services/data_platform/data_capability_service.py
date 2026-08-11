from __future__ import annotations

import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from services.data_source_definitions import OPENBB_PROVIDER_DEFINITIONS


BINANCE_INTERVALS = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d"]
POLYMARKET_INTERVALS = ["1m", "5m", "15m", "1h", "4h", "1d"]


class ResearchDataCapabilityService:
    """Describe real discovery and historical-preparation capabilities.

    A connector is shown even when it is discovery-only or currently offline,
    so the UI never confuses "installed/configured" with "ready to prepare".
    """

    def __init__(self, settings: dict[str, Any], *, base_dir: str | Path):
        self.settings = settings
        self.base_dir = Path(base_dir)

    def describe(self) -> dict[str, Any]:
        openbb = self.settings.get("openbb_settings") if isinstance(self.settings.get("openbb_settings"), dict) else {}
        openbb_enabled = bool(openbb.get("enabled"))
        openbb_online = self._tcp_online(str(openbb.get("base_url") or "http://127.0.0.1:6901")) if openbb_enabled else False
        installed_openbb = self._installed_openbb_extensions()
        allowed = list(dict.fromkeys(str(item).strip().lower() for item in openbb.get("allowed_providers", []) if str(item).strip()))

        providers: list[dict[str, Any]] = [
            {
                "id": "AUTO", "label": "Best Available", "gateway": "DATATUBE",
                "configured": True, "online": True, "discovery": True, "historical": True,
                "description": "Resolve any eligible prepared source; provider is not fixed in the Requirement.",
                "markets": [{
                    "id": "SPOT", "label": "Crypto Spot", "asset_type": "CRYPTO",
                    "search_category": "crypto_spot", "dataset_types": ["BARS"],
                    "frequencies": BINANCE_INTERVALS, "prepare_supported": True,
                    "search_defaults": {"status": "TRADING", "quote": "USDT"},
                }],
            },
            {
                "id": "BINANCE", "label": "Binance", "gateway": "DATATUBE",
                "configured": True, "online": True, "discovery": True, "historical": True,
                "description": "Crypto discovery and canonical historical bars.",
                "markets": [
                    {
                        "id": "SPOT", "label": "Crypto Spot", "asset_type": "CRYPTO",
                        "search_category": "crypto_spot", "dataset_types": ["BARS"],
                        "frequencies": BINANCE_INTERVALS, "prepare_supported": True,
                        "search_defaults": {"status": "TRADING", "quote": "USDT"},
                    },
                    {
                        "id": "USDM_FUTURES", "label": "USD-M Futures", "asset_type": "CRYPTO_DERIVATIVE",
                        "search_category": "crypto_derivatives", "dataset_types": ["BARS"],
                        "frequencies": BINANCE_INTERVALS, "prepare_supported": False,
                        "search_defaults": {"status": "TRADING", "settlement": "USDT", "subtype": "usdm_futures"},
                    },
                    {
                        "id": "TOKENIZED_EQUITY", "label": "Tokenized Equities", "asset_type": "RWA_STOCK_TOKEN",
                        "search_category": "rwa_stock_token", "dataset_types": ["QUOTE"],
                        "frequencies": ["snapshot"], "prepare_supported": False,
                        "search_defaults": {"status": "ACTIVE"},
                    },
                ],
            },
        ]

        for provider in OPENBB_PROVIDER_DEFINITIONS:
            definition = OPENBB_PROVIDER_DEFINITIONS[provider]
            extension_name = f"openbb-{provider}"
            installed = extension_name in installed_openbb
            formal_equity = "EQUITY:1D:BARS" in definition["formal_capabilities"]
            configured = openbb_enabled and (provider in allowed or provider == "fred")
            prepare_supported = bool(configured and openbb_online and installed and formal_equity)
            providers.append({
                "id": provider.upper(), "label": f"OpenBB · {definition['label']}", "gateway": "OPENBB",
                "configured": configured,
                "online": openbb_online, "installed": installed,
                "discovery": False, "historical": formal_equity,
                "package": definition["package"],
                "credential_keys": list(definition["credential_keys"]),
                "description": (
                    "Configured but the OpenBB gateway is offline." if openbb_enabled and not openbb_online else
                    f"Provider extension is not installed ({extension_name})." if not installed else
                    "OpenBB upstream provider."
                ),
                "raw_query_frequencies": ["1m", "5m", "1d"] if formal_equity else ["1d"],
                "research_sessions": ([{
                    "id": "PREMARKET_0400_0930_ET",
                    "label": "US pre-market 04:00-09:30 ET",
                    "frequencies": ["1m", "5m"],
                    "raw_query_supported": bool(openbb_enabled and openbb_online and installed),
                    "canonical_prepare_supported": False,
                    "time_semantics": "BAR_END_AVAILABLE_TIME",
                }] if formal_equity else []),
                "markets": ([{
                    "id": "XNAS", "label": "US Equities · Nasdaq", "asset_type": "EQUITY",
                    "search_category": "equity", "dataset_types": ["BARS"], "frequencies": ["1d"],
                    "prepare_supported": prepare_supported, "search_defaults": {},
                }, {
                    "id": "XNYS", "label": "US Equities · NYSE", "asset_type": "EQUITY",
                    "search_category": "equity", "dataset_types": ["BARS"], "frequencies": ["1d"],
                    "prepare_supported": prepare_supported, "search_defaults": {},
                }] if formal_equity else [{
                    "id": "MACRO", "label": "FRED Series", "asset_type": "MACRO",
                    "search_category": "fred", "dataset_types": ["SERIES"], "frequencies": ["1d"],
                    "prepare_supported": False, "search_defaults": {},
                }] if provider == "fred" else []),
            })

        providers.extend([
            {
                "id": "FINNHUB", "label": "Finnhub", "gateway": "DATATUBE",
                "configured": bool(self.settings.get("active_finnhub_api_key")), "online": True,
                "discovery": True, "historical": False,
                "description": "Equity quote and profile discovery; canonical historical preparation is not connected.",
                "markets": [{"id": "EQUITY", "label": "Equities", "asset_type": "EQUITY", "search_category": "equity", "dataset_types": ["QUOTE"], "frequencies": ["snapshot"], "prepare_supported": False, "search_defaults": {}}],
            },
            {
                "id": "COINGECKO", "label": "CoinGecko", "gateway": "DATATUBE",
                "configured": True, "online": True, "discovery": False, "historical": False,
                "description": "Crypto quote and fundamentals fallback; not a canonical historical-bar source.",
                "markets": [{
                    "id": "CRYPTO_CONTEXT", "label": "Crypto Fundamentals", "asset_type": "CRYPTO",
                    "search_category": "coingecko", "dataset_types": ["SNAPSHOT"],
                    "frequencies": ["snapshot"], "fields": ["price", "market_cap", "volume_24h"],
                    "prepare_supported": False, "search_defaults": {},
                }],
            },
            {
                "id": "POLYMARKET", "label": "Polymarket", "gateway": "DATATUBE",
                "configured": True, "online": True, "discovery": True, "historical": True,
                "description": "Prediction-market discovery plus CLOB outcome-price history.",
                "markets": [{
                    "id": "BINARY", "label": "Binary Markets", "asset_type": "POLYMARKET_BINARY",
                    "search_category": "polymarket", "dataset_types": ["PRICE_HISTORY"],
                    "frequencies": POLYMARKET_INTERVALS, "fields": ["price"],
                    "prepare_supported": True, "search_defaults": {"active": True},
                    "time_semantics": "EVENT_TIME_AVAILABLE_TIME",
                }],
            },
        ])
        return {
            "providers": providers,
            "summary": {
                "total": len(providers),
                "historical_ready": sum(1 for item in providers if item.get("historical") and any(m.get("prepare_supported") for m in item.get("markets", []))),
                "discovery_only": sum(1 for item in providers if item.get("discovery") and not item.get("historical")),
                "offline_or_unconfigured": sum(1 for item in providers if not item.get("configured") or not item.get("online")),
            },
        }

    def can_prepare(self, instrument_id: str, data_type: str, frequency: str) -> bool:
        parts = str(instrument_id or "").split(":")
        asset_class = parts[0].lower() if parts else ""
        venue = parts[1].upper() if len(parts) > 1 else ""
        if asset_class == "crypto_spot" and venue == "BINANCE" and str(data_type).lower() == "bars":
            return str(frequency).lower() in BINANCE_INTERVALS
        if asset_class == "polymarket_binary" and venue == "POLYMARKET" and str(data_type).lower() == "price_history":
            return str(frequency).lower() in POLYMARKET_INTERVALS
        if asset_class == "equity" and str(frequency).lower() == "1d":
            return any(
                provider.get("gateway") == "OPENBB" and any(m.get("prepare_supported") for m in provider.get("markets", []))
                for provider in self.describe()["providers"]
            )
        return False

    def _installed_openbb_extensions(self) -> set[str]:
        root = self.base_dir / ".openbb-venv" / "Lib" / "site-packages"
        result = set()
        if root.is_dir():
            for path in root.glob("openbb_*.dist-info"):
                result.add(path.name.split("-")[0].replace("_", "-").lower())
        return result

    @staticmethod
    def _tcp_online(base_url: str) -> bool:
        parsed = urlparse(base_url)
        if not parsed.hostname:
            return False
        try:
            with socket.create_connection((parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)), timeout=0.15):
                return True
        except OSError:
            return False
