"""Load strategy-declared schemas and merge their default values.

Strategy files remain the source of truth for defaults. The database stores
only per-strategy overrides.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict, List

_BASE_DIR = Path(__file__).resolve().parent.parent
_STRATEGY_CODE_DIR = _BASE_DIR / "StrategyCode"


def _safe_code_name(code_name: Any) -> str:
    return "".join(ch for ch in str(code_name or "") if ch.isalnum() or ch in ("_", "-"))


def _strategy_file(code_name: Any) -> Path | None:
    safe = _safe_code_name(code_name)
    if not safe:
        return None
    path = _STRATEGY_CODE_DIR / f"{safe}.py"
    return path if path.is_file() else None


def _load_strategy_module(code_name: Any):
    path = _strategy_file(code_name)
    if not path:
        return None
    mod_name = "_stg_schema_" + _safe_code_name(code_name)
    try:
        spec = importlib.util.spec_from_file_location(mod_name, str(path))
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None
    finally:
        sys.modules.pop(mod_name, None)


def _normalize_type(kind: Any) -> str:
    text = str(kind or "string").strip().lower()
    if text in {"num", "number", "float", "decimal"}:
        return "number"
    if text in {"int", "integer"}:
        return "integer"
    if text in {"bool", "boolean"}:
        return "bool"
    if text in {"select", "enum"}:
        return "enum"
    if text in {"object", "json"}:
        return "object"
    return "string"


def _normalize_schema(raw: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    result: Dict[str, Dict[str, Any]] = {}
    for key, meta in raw.items():
        name = str(key or "").strip()
        if not name:
            continue
        if isinstance(meta, dict):
            item = dict(meta)
        else:
            item = {"default": meta}
        item.setdefault("label", name)
        item["type"] = _normalize_type(item.get("type") or item.get("kind"))
        result[name] = item
    return result


def _normalize_leg_schema(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, dict):
        items = []
        for index, (key, meta) in enumerate(raw.items()):
            item = dict(meta) if isinstance(meta, dict) else {}
            item.setdefault("name", str(key or f"Leg {index + 1}"))
            item.setdefault("leg_index", index)
            items.append(item)
    elif isinstance(raw, list):
        items = [dict(item) for item in raw if isinstance(item, dict)]
    else:
        items = []
    if not items:
        items = [{
            "name": "Primary Polymarket",
            "label": "Leg 1",
            "asset_class": "polymarket_binary",
            "venue": "polymarket",
            "required": True,
        }]
    result: List[Dict[str, Any]] = []
    for index, item in enumerate(items):
        asset_class = str(item.get("asset_class") or item.get("type") or "polymarket_binary").strip() or "polymarket_binary"
        venue = str(item.get("venue") or ("polymarket" if asset_class == "polymarket_binary" else "")).strip()
        result.append({
            "leg_index": int(item.get("leg_index", index) or 0),
            "name": str(item.get("name") or item.get("label") or f"Leg {index + 1}"),
            "label": str(item.get("label") or item.get("name") or f"Leg {index + 1}"),
            "purpose": str(item.get("purpose") or item.get("description") or ""),
            "asset_class": asset_class,
            "venue": venue,
            "symbol": str(item.get("symbol") or "").strip().upper(),
            "required": bool(item.get("required", True)),
            "default": item.get("default") if isinstance(item.get("default"), dict) else {},
            "instrument_json": item.get("instrument_json") if isinstance(item.get("instrument_json"), dict) else {},
            "params_schema": _normalize_schema(item.get("params_schema") or item.get("params") or {}),
        })
    return sorted(result, key=lambda item: int(item.get("leg_index") or 0))


def _params_schema_from_inputs(inputs: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(inputs, list):
        return {}
    result: Dict[str, Dict[str, Any]] = {}
    for item in inputs:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("Id") or "").strip()
        if not name or name.lower() == "usedata":
            continue
        meta: Dict[str, Any] = {
            "type": _normalize_type(item.get("Kind")),
            "label": name,
            "required": bool(item.get("Isnecessary", False)),
        }
        default = item.get("Default", item.get("Num", item.get("Context")))
        if default is not None:
            meta["default"] = default
        if item.get("Description"):
            meta["description"] = item.get("Description")
        result[name] = meta
    return result


def get_strategy_code_schemas(code_name: Any) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Return Params/Controls/RuntimeState schemas declared by one strategy file."""
    mod = _load_strategy_module(code_name)
    if mod is None:
        return {
            "params": {},
            "controls": {},
            "runtime": {},
            "legs": _normalize_leg_schema(None),
        }
    params_schema = _normalize_schema(getattr(mod, "ParamsSchema", None))
    if not params_schema:
        params_schema = _params_schema_from_inputs(getattr(mod, "Inputs", []))
    controls_schema = _normalize_schema(
        getattr(mod, "ControlsSchema", None)
        or getattr(mod, "UserStateSchema", None)
        or getattr(mod, "UserControlsSchema", None)
    )
    runtime_schema = _normalize_schema(getattr(mod, "RuntimeStateSchema", None))
    legs_schema = _normalize_leg_schema(
        getattr(mod, "LegsSchema", None)
        or getattr(mod, "InstrumentsSchema", None)
        or getattr(mod, "InstrumentSchema", None)
    )
    return {
        "params": params_schema,
        "controls": controls_schema,
        "runtime": runtime_schema,
        "legs": legs_schema,
    }


def schema_defaults(schema: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    defaults: Dict[str, Any] = {}
    for key, meta in (schema or {}).items():
        if isinstance(meta, dict) and "default" in meta:
            defaults[key] = meta.get("default")
    return defaults


def merge_schema_defaults(
    schema: Dict[str, Dict[str, Any]],
    overrides: Dict[str, Any] | None,
) -> Dict[str, Any]:
    result = schema_defaults(schema)
    if isinstance(overrides, dict):
        result.update(overrides)
    return result


def strategy_state_payload(code_name: Any, state_bundle: Dict[str, Any]) -> Dict[str, Any]:
    """Build API payload with defaults, overrides and effective state."""
    schemas = get_strategy_code_schemas(code_name)
    user_overrides = dict((state_bundle or {}).get("user") or {})
    runtime_overrides = dict((state_bundle or {}).get("runtime") or {})
    system_overrides = dict((state_bundle or {}).get("system") or {})
    controls_schema = schemas.get("controls") or {}
    runtime_schema = schemas.get("runtime") or {}
    return {
        "schemas": schemas,
        "controls_schema": controls_schema,
        "runtime_state_schema": runtime_schema,
        "params_schema": schemas.get("params") or {},
        "user_defaults": schema_defaults(controls_schema),
        "runtime_defaults": schema_defaults(runtime_schema),
        "user_overrides": user_overrides,
        "runtime_overrides": runtime_overrides,
        "system_overrides": system_overrides,
        "controls": merge_schema_defaults(controls_schema, user_overrides),
        "user": merge_schema_defaults(controls_schema, user_overrides),
        "runtime": merge_schema_defaults(runtime_schema, runtime_overrides),
        "system": system_overrides,
    }
