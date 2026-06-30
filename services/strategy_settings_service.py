from __future__ import annotations

from typing import Any, Dict, List

from services.polymarket_service import STRATEGY_EDITABLE_FIELDS, update_strategy_detail
from services.strategy_event_service import record_strategy_event
from services.strategy_registry_service import get_strategy_code_inputs


_NUMBER_FIELDS = {"strategy_bankroll"}
_BOOLEAN_FIELDS: set = set()
_DEADLINE_FIELD = "Enddate"
_DEADLINE_ALIASES = {"enddate", "endtime", "l0endtime"}
_GROUPS = {
    "capital": {"strategy_bankroll"},
}


def _field_group(field: str) -> str:
    for group, fields in _GROUPS.items():
        if field in fields:
            return group
    return "inputs"


def _field_type(field: str) -> str:
    if field in _NUMBER_FIELDS:
        return "number"
    if field in _BOOLEAN_FIELDS:
        return "boolean"
    return "text"


def _schema_type(kind: Any, field: str) -> str:
    text = str(kind or "").strip().lower()
    if field in _NUMBER_FIELDS or text in {"num", "number", "float", "int", "integer"}:
        return "number"
    if text in {"bool", "boolean"}:
        return "boolean"
    return "text"


def _field_label(field: str) -> str:
    if field.startswith("Inputs"):
        return field
    return field.replace("_", " ").title()


def _normalize_param_name(value: Any) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _has_deadline_field(fields: List[str]) -> bool:
    return any(_normalize_param_name(field) in _DEADLINE_ALIASES for field in fields)


def _strategy_code_input_meta(detail: Dict[str, Any] | None) -> Dict[str, Dict[str, Any]]:
    if not isinstance(detail, dict):
        return {}
    raw = detail.get("raw") if isinstance(detail.get("raw"), dict) else {}
    code = str(
        detail.get("strategy_code")
        or detail.get("code")
        or raw.get("Code")
        or raw.get("strategy_code")
        or ""
    ).strip()
    if not code:
        return {}
    try:
        items = get_strategy_code_inputs(code)
    except Exception:
        return {}
    return {
        str(item.get("name") or ""): item
        for item in items
        if str(item.get("name") or "").strip()
    }


def build_strategy_settings_schema(detail: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
    editable = (detail or {}).get("editable") if isinstance(detail, dict) else {}
    code_inputs = _strategy_code_input_meta(detail)
    input_keys: List[str] = []
    if isinstance(editable, dict):
        for key, value in editable.items():
            if key in _NUMBER_FIELDS or key in {"mode", "state"}:
                continue
            text = str(value or "").strip()
            if key.startswith("Inputs") and not text:
                continue
            input_keys.append(str(key))
    if not input_keys:
        input_keys = [field for field in STRATEGY_EDITABLE_FIELDS if field.startswith("Inputs")]
    if not _has_deadline_field(input_keys):
        input_keys.append(_DEADLINE_FIELD)

    fields = [
        *input_keys,
        "strategy_bankroll",
    ]
    deduped = list(dict.fromkeys(fields))
    schema = []
    for field in deduped:
        meta = code_inputs.get(field, {})
        schema.append({
            "key": field,
            "label": meta.get("name") or _field_label(field),
            "group": _field_group(field),
            "type": _schema_type(meta.get("kind"), field) if meta else _field_type(field),
            "description": meta.get("description") or "",
            "required": bool(meta.get("required", False)),
            "source": "strategy_code" if meta else "editable",
        })
    return schema


def validate_strategy_settings_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    clean: Dict[str, Any] = {}
    dynamic_fields = [
        str(field)
        for field in payload.keys()
        if str(field) not in STRATEGY_EDITABLE_FIELDS
        and str(field) not in _NUMBER_FIELDS
        and str(field) not in {"mode", "state"}
    ]
    for field in [*STRATEGY_EDITABLE_FIELDS, *dynamic_fields]:
        if field not in payload:
            continue
        value = payload.get(field)
        if field in _BOOLEAN_FIELDS:
            clean[field] = "True" if str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"} else "False"
            continue
        if field in _NUMBER_FIELDS:
            text = str(value or "").strip()
            if not text:
                clean[field] = ""
                continue
            try:
                clean[field] = float(text)
            except ValueError as exc:
                raise ValueError(f"Invalid number for {field}: {value}") from exc
            continue
        clean[field] = str(value or "").strip()
    return clean


def update_strategy_settings(row_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    clean_payload = validate_strategy_settings_payload(payload)
    detail = update_strategy_detail(row_id, clean_payload)
    if clean_payload:
        record_strategy_event(
            strategy_row_id=row_id,
            event_type="settings_updated",
            event_subtype="manual_save",
            summary=f"Updated {', '.join(clean_payload.keys())}",
            payload={"fields": clean_payload},
            source="settings",
        )
    return detail
