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
