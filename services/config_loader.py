import json
import threading
import time
from pathlib import Path
from typing import Any, Dict

from services.secure_settings import SENSITIVE_SETTING_KEYS, load_secrets, save_secrets, strip_sensitive


BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.json"
WEB_SETTINGS_PATH = BASE_DIR / "web_settings.json"
WEB_SETTINGS_SECRETS_PATH = BASE_DIR / "web_settings.secrets.json"
WEB_SETTINGS_KEY_PATH = BASE_DIR / ".datatube_secret.key"

_settings_cache: Dict[str, Any] = {}
_settings_cache_ts: float = 0.0
_settings_cache_lock = threading.Lock()
_SETTINGS_CACHE_TTL = 5.0
_db_files_ensured: bool = False
DEFAULT_DB_FILES = {
    "sqlite_db_path": "market_data.db",
    "order_list_db_path": "PolyMarketOrderList.db",
    "strategy_monitoring_db_path": "PolyMarketMonitoring.db",
    "market_realtime_db_path": "polymarket_realtime.db",
    "polymarket_dictionary_db_path": "Data/PolyMarketDictionary.db",
}
UI_PATH_KEYS = tuple(DEFAULT_DB_FILES.keys()) + ("strategy_metrics_db_dir",)
DEFAULT_DIR_SETTINGS = {
    "strategy_metrics_db_dir": "strategy_metrics_dbs",
}
DEFAULT_AGENT_POLICY = {
    "enabled": True,
    "permissions": {
        "market_read": True,
        "market_search": True,
        "market_scan": True,
        "strategy_read_all": True,
        "strategy_detail_read": True,
        "strategy_workspace_read": True,
        "strategy_events_read": True,
        "strategy_state_read": True,
        "strategy_draft_create": True,
        "strategy_draft_update": True,
        "strategy_draft_delete": True,
        "strategy_batch_propose": True,
        "risk_check": True,
        "strategy_simulate": True,
        "strategy_submit": True,
        "order_read": True,
        "pnl_read": True,
        "audit_read": True,
        "event_read": True,
        "event_news_refresh": True,
        "event_news_search": True,
    },
    "limits": {
        "max_strategy_budget_usdc": 100.0,
        "max_single_order_usdc": 20.0,
        "max_daily_spend_usdc": 150.0,
        "max_market_exposure_usdc": 50.0,
        "max_global_exposure_usdc": 300.0,
        "max_slippage_bps": 100.0,
        "allowed_market_ids": [],
        "allowed_venues": ["polymarket"],
        "allow_market_order": False,
        "require_human_approval": True,
        "approval_expires_minutes": 1440,
    },
    "defaults": {
        "scan_categories": ["Elections Politics", "World", "Geopolitics"],
        "scan_sorts": ["volume24h", "volume", "liquidity", "spread"],
        "proposal_budget_usdc": 20.0,
        "proposal_single_order_usdc": 5.0,
        "max_batch_drafts": 5,
        "selection_mode": "yes",
    },
}

DEFAULT_LLM_SETTINGS = {
    "enabled": False,
    "provider": "dashscope_openai_compatible",
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "model": "qwen-plus",
    "temperature": 0.2,
    "max_tokens": 2048,
    "timeout_sec": 60,
}


def load_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_config(config: Dict[str, Any]) -> None:
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def get_default_wallets() -> list[str]:
    config = load_config()
    wallets = config.get("holdings", {}).get("wallet_addresses", [])
    return [str(wallet).strip() for wallet in wallets if str(wallet).strip()]


def _to_clean_list(raw: Any, uppercase: bool = False) -> list[str]:
    if isinstance(raw, str):
        parts = raw.replace("\r", "\n").replace(",", "\n").split("\n")
    elif isinstance(raw, list):
        parts = raw
    else:
        parts = []
    out: list[str] = []
    seen = set()
    for item in parts:
        value = str(item).strip()
        if not value:
            continue
        if uppercase:
            value = value.upper()
        if value not in seen:
            out.append(value)
            seen.add(value)
    return out


def _clone_json(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _to_setting_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on", "enable", "enabled", "是", "启用"}:
        return True
    if text in {"0", "false", "no", "n", "off", "disable", "disabled", "否", "禁用"}:
        return False
    return default


def _to_setting_float(value: Any, default: float, min_value: float = 0.0, max_value: float | None = None) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = float(default)
    result = max(min_value, result)
    if max_value is not None:
        result = min(max_value, result)
    return result


def _to_setting_int(value: Any, default: int, min_value: int = 0, max_value: int | None = None) -> int:
    try:
        result = int(float(value))
    except (TypeError, ValueError):
        result = int(default)
    result = max(min_value, result)
    if max_value is not None:
        result = min(max_value, result)
    return result


def _normalize_agent_policy(policy: Any) -> Dict[str, Any]:
    normalized = _clone_json(DEFAULT_AGENT_POLICY)
    incoming = policy if isinstance(policy, dict) else {}

    normalized["enabled"] = _to_setting_bool(incoming.get("enabled"), normalized["enabled"])

    incoming_permissions = incoming.get("permissions") if isinstance(incoming.get("permissions"), dict) else {}
    for key, default_value in normalized["permissions"].items():
        normalized["permissions"][key] = _to_setting_bool(incoming_permissions.get(key), bool(default_value))

    incoming_limits = incoming.get("limits") if isinstance(incoming.get("limits"), dict) else {}
    limit_defaults = normalized["limits"]
    normalized["limits"].update({
        "max_strategy_budget_usdc": _to_setting_float(incoming_limits.get("max_strategy_budget_usdc"), limit_defaults["max_strategy_budget_usdc"]),
        "max_single_order_usdc": _to_setting_float(incoming_limits.get("max_single_order_usdc"), limit_defaults["max_single_order_usdc"]),
        "max_daily_spend_usdc": _to_setting_float(incoming_limits.get("max_daily_spend_usdc"), limit_defaults["max_daily_spend_usdc"]),
        "max_market_exposure_usdc": _to_setting_float(incoming_limits.get("max_market_exposure_usdc"), limit_defaults["max_market_exposure_usdc"]),
        "max_global_exposure_usdc": _to_setting_float(incoming_limits.get("max_global_exposure_usdc"), limit_defaults["max_global_exposure_usdc"]),
        "max_slippage_bps": _to_setting_float(incoming_limits.get("max_slippage_bps"), limit_defaults["max_slippage_bps"], 0.0, 10000.0),
        "allowed_market_ids": _to_clean_list(incoming_limits.get("allowed_market_ids", limit_defaults["allowed_market_ids"])),
        "allowed_venues": [item.lower() for item in _to_clean_list(incoming_limits.get("allowed_venues", limit_defaults["allowed_venues"]))],
        "allow_market_order": _to_setting_bool(incoming_limits.get("allow_market_order"), bool(limit_defaults["allow_market_order"])),
        "require_human_approval": _to_setting_bool(incoming_limits.get("require_human_approval"), bool(limit_defaults["require_human_approval"])),
        "approval_expires_minutes": _to_setting_int(incoming_limits.get("approval_expires_minutes"), int(limit_defaults["approval_expires_minutes"]), 1, 43200),
    })

    incoming_defaults = incoming.get("defaults") if isinstance(incoming.get("defaults"), dict) else {}
    default_values = normalized["defaults"]
    selection_mode = str(incoming_defaults.get("selection_mode", default_values["selection_mode"]) or "yes").strip().lower()
    if selection_mode not in {"yes", "no", "cheaper", "balanced"}:
        selection_mode = "yes"
    normalized["defaults"].update({
        "scan_categories": _to_clean_list(incoming_defaults.get("scan_categories", default_values["scan_categories"])),
        "scan_sorts": _to_clean_list(incoming_defaults.get("scan_sorts", default_values["scan_sorts"])),
        "proposal_budget_usdc": _to_setting_float(incoming_defaults.get("proposal_budget_usdc"), default_values["proposal_budget_usdc"]),
        "proposal_single_order_usdc": _to_setting_float(incoming_defaults.get("proposal_single_order_usdc"), default_values["proposal_single_order_usdc"]),
        "max_batch_drafts": _to_setting_int(incoming_defaults.get("max_batch_drafts"), int(default_values["max_batch_drafts"]), 1, 50),
        "selection_mode": selection_mode,
    })
    if not normalized["limits"]["allowed_venues"]:
        normalized["limits"]["allowed_venues"] = ["polymarket"]
    if not normalized["defaults"]["scan_categories"]:
        normalized["defaults"]["scan_categories"] = list(DEFAULT_AGENT_POLICY["defaults"]["scan_categories"])
    if not normalized["defaults"]["scan_sorts"]:
        normalized["defaults"]["scan_sorts"] = list(DEFAULT_AGENT_POLICY["defaults"]["scan_sorts"])
    return normalized


def _normalize_llm_settings(settings: Any) -> Dict[str, Any]:
    normalized = _clone_json(DEFAULT_LLM_SETTINGS)
    incoming = settings if isinstance(settings, dict) else {}
    provider = str(incoming.get("provider") or normalized["provider"]).strip() or normalized["provider"]
    if provider != "dashscope_openai_compatible":
        provider = "dashscope_openai_compatible"
    base_url = str(incoming.get("base_url") or normalized["base_url"]).strip().rstrip("/")
    if not base_url:
        base_url = normalized["base_url"]
    model = str(incoming.get("model") or normalized["model"]).strip() or normalized["model"]
    normalized.update({
        "enabled": _to_setting_bool(incoming.get("enabled"), bool(normalized["enabled"])),
        "provider": provider,
        "base_url": base_url,
        "model": model,
        "temperature": _to_setting_float(incoming.get("temperature"), float(normalized["temperature"]), 0.0, 2.0),
        "max_tokens": _to_setting_int(incoming.get("max_tokens"), int(normalized["max_tokens"]), 1, 32768),
        "timeout_sec": _to_setting_int(incoming.get("timeout_sec"), int(normalized["timeout_sec"]), 5, 300),
    })
    return normalized


def _looks_like_sqlite_path(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    return text.endswith((".db", ".sqlite", ".sqlite3")) or (":\\" in text) or (":/" in text)


def _resolve_workspace_path(value: Any, default_name: str) -> Path:
    text = str(value or "").strip()
    path = Path(text).expanduser() if text else (BASE_DIR / default_name)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


def _to_ui_path(value: Any, default_name: str | None = None) -> str:
    text = str(value or "").strip()
    path = _resolve_workspace_path(text, default_name or "")
    try:
        return str(path.relative_to(BASE_DIR))
    except ValueError:
        return str(path)


def _ensure_sqlite_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)


def _normalize_db_paths(settings: Dict[str, Any]) -> Dict[str, Any]:
    global _db_files_ensured
    for key, default_name in DEFAULT_DB_FILES.items():
        resolved = _resolve_workspace_path(settings.get(key, ""), default_name)
        settings[key] = str(resolved)
        if not _db_files_ensured:
            _ensure_sqlite_file(resolved)
    _db_files_ensured = True
    return settings


def _normalize_dir_paths(settings: Dict[str, Any]) -> Dict[str, Any]:
    for key, default_dir in DEFAULT_DIR_SETTINGS.items():
        text = str(settings.get(key, "")).strip()
        path = Path(text).expanduser() if text else (BASE_DIR / default_dir)
        if not path.is_absolute():
            path = BASE_DIR / path
        path.mkdir(parents=True, exist_ok=True)
        settings[key] = str(path)
    return settings


def _normalize_strategy_storage_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    legacy_realtime_db_path = str(settings.get("strategy_option_sqlite_db_path", "")).strip()
    table_value = str(settings.get("strategy_monitoring_table", "")).strip()
    realtime_db_path = str(settings.get("market_realtime_db_path", "")).strip() or legacy_realtime_db_path
    if _looks_like_sqlite_path(table_value):
        if not realtime_db_path:
            settings["market_realtime_db_path"] = table_value
        settings["strategy_monitoring_table"] = "monitoring"
    if not str(settings.get("market_realtime_db_path", "")).strip():
        settings["market_realtime_db_path"] = (
            legacy_realtime_db_path
            or str(BASE_DIR / "polymarket_realtime.db")
        )
    settings.pop("strategy_option_sqlite_db_path", None)
    return settings


def get_market_realtime_db_path(settings: Dict[str, Any] | None = None) -> str:
    current = dict(settings or load_web_settings())
    current = _normalize_strategy_storage_settings(current)
    resolved = _resolve_workspace_path(
        current.get("market_realtime_db_path", ""),
        DEFAULT_DB_FILES["market_realtime_db_path"],
    )
    return str(resolved)


def get_polymarket_dictionary_db_path(settings: Dict[str, Any] | None = None) -> str:
    current = dict(settings or load_web_settings())
    resolved = _resolve_workspace_path(
        current.get("polymarket_dictionary_db_path", ""),
        DEFAULT_DB_FILES["polymarket_dictionary_db_path"],
    )
    return str(resolved)


def get_default_web_settings() -> Dict[str, Any]:
    return {
        "wallet_addresses": get_default_wallets(),
        "finnhub_api_keys": [],
        "active_finnhub_api_key": "",
        "crypto_symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        "finance_symbols": ["AAPL", "MSFT", "GLD", "SLV"],
        "crypto_refresh_sec": 15,
        "finance_refresh_sec": 20,
        "ui_refresh_sec": 5,
        "sqlite_db_path": str(BASE_DIR / "market_data.db"),
        "order_list_db_path": str(BASE_DIR / "PolyMarketOrderList.db"),
        "strategy_monitoring_db_path": str(BASE_DIR / "PolyMarketMonitoring.db"),
        "market_realtime_db_path": str(BASE_DIR / "polymarket_realtime.db"),
        "polymarket_dictionary_db_path": str(BASE_DIR / "Data" / "PolyMarketDictionary.db"),
        "strategy_metrics_db_dir": str(BASE_DIR / "strategy_metrics_dbs"),
        "strategy_monitoring_table": "monitoring",
        "include_crypto_fundamentals": True,
        "coingecko_api_key": "",
        "coingecko_api_key_header": "x-cg-demo-api-key",
        "agent_policy": _normalize_agent_policy({}),
        "llm_settings": _normalize_llm_settings({}),
        "llm_api_key": "",
    }


def _load_web_settings_uncached() -> Dict[str, Any]:
    defaults = get_default_web_settings()
    if WEB_SETTINGS_PATH.exists():
        with WEB_SETTINGS_PATH.open("r", encoding="utf-8") as f:
            stored = json.load(f)
        defaults.update(stored)
    defaults.update(load_secrets(WEB_SETTINGS_SECRETS_PATH, WEB_SETTINGS_KEY_PATH))
    defaults["wallet_addresses"] = _to_clean_list(defaults.get("wallet_addresses", []))
    defaults["finnhub_api_keys"] = _to_clean_list(defaults.get("finnhub_api_keys", []))
    defaults["crypto_symbols"] = _to_clean_list(defaults.get("crypto_symbols", []), uppercase=True)
    defaults["finance_symbols"] = _to_clean_list(defaults.get("finance_symbols", []), uppercase=True)
    defaults["agent_policy"] = _normalize_agent_policy(defaults.get("agent_policy", {}))
    defaults["llm_settings"] = _normalize_llm_settings(defaults.get("llm_settings", {}))
    defaults = _normalize_strategy_storage_settings(defaults)
    defaults = _normalize_db_paths(defaults)
    return _normalize_dir_paths(defaults)


def load_web_settings() -> Dict[str, Any]:
    global _settings_cache, _settings_cache_ts
    now = time.monotonic()
    if _settings_cache and now - _settings_cache_ts < _SETTINGS_CACHE_TTL:
        return dict(_settings_cache)
    with _settings_cache_lock:
        if _settings_cache and now - _settings_cache_ts < _SETTINGS_CACHE_TTL:
            return dict(_settings_cache)
        result = _load_web_settings_uncached()
        _settings_cache = dict(result)
        _settings_cache_ts = time.monotonic()
        return dict(result)


def load_web_settings_for_ui() -> Dict[str, Any]:
    settings = load_web_settings()
    for key in UI_PATH_KEYS:
        if key in DEFAULT_DB_FILES:
            settings[key] = _to_ui_path(settings.get(key, ""), DEFAULT_DB_FILES[key])
        else:
            settings[key] = _to_ui_path(settings.get(key, ""), DEFAULT_DIR_SETTINGS[key])
    return settings


def load_public_web_settings() -> Dict[str, Any]:
    settings = load_web_settings_for_ui()
    return strip_sensitive(settings)


def save_web_settings(payload: Dict[str, Any]) -> Dict[str, Any]:
    current = load_web_settings()
    current["wallet_addresses"] = _to_clean_list(payload.get("wallet_addresses", current.get("wallet_addresses", [])))
    current["finnhub_api_keys"] = _to_clean_list(payload.get("finnhub_api_keys", current.get("finnhub_api_keys", [])))
    current["crypto_symbols"] = _to_clean_list(payload.get("crypto_symbols", current.get("crypto_symbols", [])), uppercase=True)
    current["finance_symbols"] = _to_clean_list(payload.get("finance_symbols", current.get("finance_symbols", [])), uppercase=True)

    active_key = str(payload.get("active_finnhub_api_key", current.get("active_finnhub_api_key", ""))).strip()
    if not active_key and current["finnhub_api_keys"]:
        active_key = current["finnhub_api_keys"][0]
    current["active_finnhub_api_key"] = active_key

    current["sqlite_db_path"] = str(payload.get("sqlite_db_path", current.get("sqlite_db_path", ""))).strip() or str(BASE_DIR / "market_data.db")
    current["order_list_db_path"] = str(
        payload.get("order_list_db_path", current.get("order_list_db_path", ""))
    ).strip() or str(BASE_DIR / "PolyMarketOrderList.db")
    current["strategy_monitoring_db_path"] = str(
        payload.get("strategy_monitoring_db_path", current.get("strategy_monitoring_db_path", ""))
    ).strip() or str(BASE_DIR / "PolyMarketMonitoring.db")
    current["market_realtime_db_path"] = str(
        payload.get(
            "market_realtime_db_path",
            payload.get(
                "strategy_option_sqlite_db_path",
                current.get("market_realtime_db_path", ""),
            ),
        )
    ).strip() or str(BASE_DIR / "polymarket_realtime.db")
    current["polymarket_dictionary_db_path"] = str(
        payload.get("polymarket_dictionary_db_path", current.get("polymarket_dictionary_db_path", ""))
    ).strip() or str(BASE_DIR / "Data" / "PolyMarketDictionary.db")
    current["strategy_monitoring_table"] = str(
        payload.get("strategy_monitoring_table", current.get("strategy_monitoring_table", "monitoring"))
    ).strip() or "monitoring"
    current["strategy_metrics_db_dir"] = str(
        payload.get("strategy_metrics_db_dir", current.get("strategy_metrics_db_dir", ""))
    ).strip() or str(BASE_DIR / "strategy_metrics_dbs")
    current["coingecko_api_key"] = str(payload.get("coingecko_api_key", current.get("coingecko_api_key", ""))).strip()
    current["coingecko_api_key_header"] = str(payload.get("coingecko_api_key_header", current.get("coingecko_api_key_header", "x-cg-demo-api-key"))).strip() or "x-cg-demo-api-key"
    current["include_crypto_fundamentals"] = bool(payload.get("include_crypto_fundamentals", current.get("include_crypto_fundamentals", True)))
    current["agent_policy"] = _normalize_agent_policy(payload.get("agent_policy", current.get("agent_policy", {})))
    current["llm_settings"] = _normalize_llm_settings(payload.get("llm_settings", current.get("llm_settings", {})))
    current["llm_api_key"] = str(payload.get("llm_api_key", current.get("llm_api_key", ""))).strip()
    current = _normalize_strategy_storage_settings(current)
    current = _normalize_db_paths(current)
    current = _normalize_dir_paths(current)
    current.pop("strategy_option_sqlite_db_path", None)

    for key, default_value in [("crypto_refresh_sec", 15), ("finance_refresh_sec", 20), ("ui_refresh_sec", 5)]:
        raw = payload.get(key, current.get(key, default_value))
        try:
            current[key] = max(2, int(raw))
        except (TypeError, ValueError):
            current[key] = default_value

    secrets = {key: current.get(key) for key in SENSITIVE_SETTING_KEYS}
    save_secrets(WEB_SETTINGS_SECRETS_PATH, WEB_SETTINGS_KEY_PATH, secrets)
    stored = {key: value for key, value in current.items() if key not in SENSITIVE_SETTING_KEYS}
    with WEB_SETTINGS_PATH.open("w", encoding="utf-8") as f:
        json.dump(stored, f, ensure_ascii=False, indent=2)

    config = load_config()
    config.setdefault("holdings", {})
    config["holdings"]["wallet_addresses"] = list(current["wallet_addresses"])
    save_config(config)
    # Invalidate settings cache so next read picks up the new values
    global _settings_cache, _settings_cache_ts
    _settings_cache = dict(current)
    _settings_cache_ts = time.monotonic()
    return current
