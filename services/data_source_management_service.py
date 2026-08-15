from __future__ import annotations

from pathlib import Path
import json
import threading
from typing import Any, Callable

from services.config_loader import load_web_settings, save_web_settings
from services.data_source_definitions import (
    DATA_SOURCE_POLICY_LABELS,
    DEFAULT_DATA_SOURCE_SETTINGS,
    OPENBB_PROVIDER_DEFINITIONS,
    normalize_data_source_settings,
)
from services.data_platform.data_capability_service import ResearchDataCapabilityService


_SOURCE_POLICY_KEYS = {
    "BINANCE": ("CRYPTO_SPOT:*:BARS",),
    "COINGECKO": ("CRYPTO:SNAPSHOT:SNAPSHOT",),
    "FINNHUB": ("EQUITY:SNAPSHOT:QUOTE",),
    "POLYMARKET": ("POLYMARKET_BINARY:*:PRICE_HISTORY",),
    "SEC": ("EQUITY:FUNDAMENTALS:SNAPSHOT",),
}
_ROUTING_UPDATE_LOCK = threading.Lock()


class DataSourceRoutingConflict(ValueError):
    pass


class DataSourceManagementService:
    """Central read/write boundary for Data Source connections and base routing.

    Credentials stay in encrypted settings.  This service only emits credential
    presence and never returns secret values.
    """

    def __init__(
        self,
        settings: dict[str, Any],
        *,
        base_dir: str | Path,
        settings_saver: Callable[[dict[str, Any]], dict[str, Any]] = save_web_settings,
        settings_loader: Callable[[], dict[str, Any]] = load_web_settings,
    ):
        self.settings = dict(settings)
        self.base_dir = Path(base_dir)
        self.settings_saver = settings_saver
        self.settings_loader = settings_loader

    @staticmethod
    def _source_id(provider: dict[str, Any]) -> str:
        provider_id = str(provider.get("id") or "").strip().upper()
        return f"OPENBB:{provider_id}" if provider.get("gateway") == "OPENBB" else provider_id

    @staticmethod
    def _policy_keys(provider: dict[str, Any]) -> tuple[str, ...]:
        provider_id = str(provider.get("id") or "").strip().upper()
        if provider.get("gateway") == "OPENBB":
            definition = OPENBB_PROVIDER_DEFINITIONS.get(provider_id.lower(), {})
            keys = list(definition.get("formal_capabilities") or ())
            if provider_id == "FRED":
                keys.append("MACRO:1D:SERIES")
            return tuple(keys)
        return _SOURCE_POLICY_KEYS.get(provider_id, ())

    def _credential_state(self, provider: dict[str, Any]) -> tuple[list[str], bool]:
        provider_id = str(provider.get("id") or "").strip().upper()
        if provider_id == "FINNHUB":
            return ["finnhub_api_key"], bool(self.settings.get("active_finnhub_api_key"))
        if provider_id == "COINGECKO":
            return ["coingecko_api_key"], bool(self.settings.get("coingecko_api_key"))
        if provider.get("gateway") != "OPENBB":
            return [], True
        required = list(provider.get("credential_keys") or [])
        credentials = self.settings.get("openbb_provider_credentials")
        credentials = credentials if isinstance(credentials, dict) else {}
        if provider_id == "FRED" and self.settings.get("openbb_fred_api_key"):
            credentials = {**credentials, "fred_api_key": self.settings["openbb_fred_api_key"]}
        return required, all(bool(credentials.get(key)) for key in required)

    def _configuration_state(self, provider: dict[str, Any]) -> tuple[list[str], bool]:
        required = list(provider.get("configuration_keys") or [])
        return required, all(bool(self.settings.get(key)) for key in required)

    def _openbb_runtime_snapshot(self) -> dict[str, Any]:
        marker = self.base_dir / ".datatube" / "openbb-runtime.json"
        try:
            value = json.loads(marker.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}
        return value if isinstance(value, dict) else {}

    def describe(self) -> dict[str, Any]:
        capability_result = ResearchDataCapabilityService(
            self.settings, base_dir=self.base_dir
        ).describe()
        runtime_snapshot = self._openbb_runtime_snapshot()
        loaded_credentials = {
            str(item) for item in runtime_snapshot.get("credential_keys_loaded", [])
        }
        sources: list[dict[str, Any]] = []
        for provider in capability_result["providers"]:
            if str(provider.get("id") or "").upper() == "AUTO":
                continue
            source_id = self._source_id(provider)
            credential_keys, credential_configured = self._credential_state(provider)
            configuration_keys, configuration_configured = self._configuration_state(provider)
            configured = bool(provider.get("configured"))
            online = bool(provider.get("online"))
            installed = provider.get("installed")
            is_openbb = provider.get("gateway") == "OPENBB"
            credential_loaded = (
                all(key in loaded_credentials for key in credential_keys)
                if is_openbb and credential_keys and runtime_snapshot else None
            )
            if not configured and configuration_keys and not configuration_configured:
                runtime_status = "configuration_required"
                status_detail = "需要先保存必填连接配置。"
            elif not configured and credential_keys and credential_configured:
                runtime_status = "activation_required"
                status_detail = "凭据已加密保存，尚未启用；点击“启用并加载”。"
            elif not configured:
                runtime_status = "disabled"
                status_detail = "当前未启用。"
            elif installed is False:
                runtime_status = "not_installed"
                status_detail = "Provider 扩展尚未安装。"
            elif credential_keys and not credential_configured:
                runtime_status = "credential_required"
                status_detail = "需要先保存 API 凭据。"
            elif not online:
                runtime_status = "unavailable"
                status_detail = "OpenBB 网关当前不可用。"
            else:
                runtime_status = "ready"
                status_detail = (
                    "已启用，扩展可用，OpenBB 网关在线。"
                    if is_openbb else "已配置，可通过后端连接测试验证上游。"
                )
            sources.append({
                "source_id": source_id,
                "provider_id": str(provider.get("id") or "").upper(),
                "label": provider.get("label") or source_id,
                "gateway": provider.get("gateway") or "DATATUBE",
                "configured": configured,
                "online": online,
                "installed": installed,
                "runtime_status": runtime_status,
                "status_detail": status_detail,
                "credential_keys": credential_keys,
                "credential_configured": credential_configured,
                "credential_loaded": credential_loaded,
                "configuration_keys": configuration_keys,
                "configuration_configured": configuration_configured,
                "test_supported": source_id in {"FINNHUB", "SEC"},
                "query_operations": (
                    ["quote"] if source_id == "FINNHUB" else
                    ["company-facts"] if source_id == "SEC" else []
                ),
                "can_activate": bool(
                    is_openbb
                    and installed is not False
                    and credential_keys
                    and credential_configured
                    and not configured
                ),
                "historical": bool(provider.get("historical")),
                "discovery": bool(provider.get("discovery")),
                "package": provider.get("package"),
                "description": provider.get("description") or "",
                "capability_keys": list(self._policy_keys(provider)),
                "markets": list(provider.get("markets") or []),
            })

        source_by_id = {item["source_id"]: item for item in sources}
        routing = normalize_data_source_settings(self.settings.get("data_source_settings"))
        policies = []
        for policy_key, order in routing["priority_orders"].items():
            eligible = [
                source_id for source_id, source in source_by_id.items()
                if policy_key in source["capability_keys"]
            ]
            effective_order = [source_id for source_id in order if source_id in eligible]
            effective_order.extend(source_id for source_id in eligible if source_id not in effective_order)
            policies.append({
                "policy_key": policy_key,
                "label": DATA_SOURCE_POLICY_LABELS.get(policy_key, policy_key),
                "order": effective_order,
                "sources": [source_by_id[source_id] for source_id in effective_order],
            })
        return {
            "schema_version": "data_source_management.v1",
            "mode": routing["mode"],
            "version": routing["version"],
            "sources": sources,
            "routing_policies": policies,
            "summary": {
                "total": len(sources),
                "configured": sum(1 for item in sources if item["configured"]),
                "ready": sum(1 for item in sources if item["runtime_status"] == "ready"),
                "formal_historical": sum(1 for item in sources if item["historical"]),
            },
        }

    def activate_openbb_provider(self, provider_id: Any) -> dict[str, Any]:
        """Enable one installed OpenBB provider without exposing its credential."""

        provider = str(provider_id or "").strip().lower().removeprefix("openbb:")
        definition = OPENBB_PROVIDER_DEFINITIONS.get(provider)
        if not definition:
            raise ValueError(f"Unsupported OpenBB provider: {provider or '(empty)'}")
        with _ROUTING_UPDATE_LOCK:
            self.settings = dict(self.settings_loader())
            credentials = self.settings.get("openbb_provider_credentials")
            credentials = credentials if isinstance(credentials, dict) else {}
            missing = [
                key for key in definition["credential_keys"]
                if not credentials.get(key)
            ]
            if missing:
                raise ValueError("Save the provider credential before enabling it")
            current = self.settings.get("openbb_settings")
            current = dict(current) if isinstance(current, dict) else {}
            allowed = [
                str(item).strip().lower()
                for item in current.get("allowed_providers", [])
                if str(item).strip()
            ]
            if provider not in allowed:
                allowed.append(provider)
            current["enabled"] = True
            current["allowed_providers"] = list(dict.fromkeys(allowed))
            saved = self.settings_saver({"openbb_settings": current})
            self.settings = dict(saved)
        return self.settings

    def update_routing(self, payload: dict[str, Any]) -> dict[str, Any]:
        with _ROUTING_UPDATE_LOCK:
            self.settings = dict(self.settings_loader())
            current = normalize_data_source_settings(self.settings.get("data_source_settings"))
            expected_version = payload.get("expected_version")
            if expected_version not in (None, "") and int(expected_version) != current["version"]:
                raise DataSourceRoutingConflict(
                    f"Data Source routing changed from version {expected_version} to {current['version']}; reload before saving"
                )
            candidate = normalize_data_source_settings({
                "mode": payload.get("mode") or current["mode"],
                "version": current["version"],
                "priority_orders": payload.get("priority_orders") or current["priority_orders"],
            })
            allowed_by_policy = {
                key: set(defaults)
                for key, defaults in DEFAULT_DATA_SOURCE_SETTINGS["priority_orders"].items()
            }
            for policy_key, order in candidate["priority_orders"].items():
                invalid = [source_id for source_id in order if source_id not in allowed_by_policy[policy_key]]
                if invalid:
                    raise ValueError(
                        f"Data Source {', '.join(invalid)} cannot serve routing policy {policy_key}"
                    )
            candidate["version"] = current["version"] + 1
            saved = self.settings_saver({"data_source_settings": candidate})
            self.settings = dict(saved)
        return self.describe()
