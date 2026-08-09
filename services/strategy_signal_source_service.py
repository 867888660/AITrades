from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from services.data_platform import get_default_store
from services.data_platform.library_service import ResearchLibraryService


SIGNAL_SOURCE_SCHEMA_VERSION = "strategy_signal_source.v1"
LEGACY_STRATEGY_CODE = "LEGACY_STRATEGY_CODE"
LIBRARY_ALPHA = "LIBRARY_ALPHA"
SIGNAL_SOURCE_TYPES = {LEGACY_STRATEGY_CODE, LIBRARY_ALPHA}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def legacy_strategy_code_source(strategy_code: Any) -> dict[str, Any]:
    """Return the compatibility source for existing StrategyCode strategies."""
    return {
        "schema_version": SIGNAL_SOURCE_SCHEMA_VERSION,
        "type": LEGACY_STRATEGY_CODE,
        "status": "READY",
        "execution_status": "CONNECTED",
        "strategy_code": _clean(strategy_code),
    }


def effective_strategy_signal_source(raw: Any, *, strategy_code: Any = "") -> dict[str, Any]:
    """Normalize persisted data while keeping pre-v1 Strategy rows readable."""
    if not isinstance(raw, dict) or not raw:
        return legacy_strategy_code_source(strategy_code)
    source_type = _clean(raw.get("type")).upper()
    if source_type == LEGACY_STRATEGY_CODE:
        return legacy_strategy_code_source(raw.get("strategy_code") or strategy_code)
    if source_type == LIBRARY_ALPHA:
        result = dict(raw)
        result["schema_version"] = SIGNAL_SOURCE_SCHEMA_VERSION
        result["type"] = LIBRARY_ALPHA
        result.setdefault("status", "REFERENCE_READY")
        result.setdefault("execution_status", "NOT_CONNECTED")
        result["factor_closure"] = [
            dict(item) for item in result.get("factor_closure", []) if isinstance(item, dict)
        ]
        return result
    return legacy_strategy_code_source(strategy_code)


def resolve_library_alpha_source(
    library_asset_id: Any,
    *,
    library: ResearchLibraryService | None = None,
) -> dict[str, Any]:
    """Resolve one published Alpha into an immutable Strategy dependency closure."""
    asset_id = _clean(library_asset_id)
    if not asset_id:
        raise ValueError("library_asset_id is required for LIBRARY_ALPHA")
    service = library or ResearchLibraryService(get_default_store())
    alpha_asset = service.get(asset_id)
    if alpha_asset is None or _clean(alpha_asset.get("component_type")).upper() != "ALPHA":
        raise ValueError("published Library Alpha not found")

    alpha = dict(alpha_asset.get("content") or {})
    if _clean(alpha.get("definition_type")).upper() != "ALPHA":
        raise ValueError("Library asset does not contain an Alpha definition")
    if _clean(alpha.get("state")).upper() != "VALIDATED":
        raise ValueError("Strategy may only reference a validated Library Alpha")
    alpha_hash = _clean(alpha.get("spec_hash"))
    if not alpha_hash or alpha_hash != _clean(alpha_asset.get("content_hash")):
        raise ValueError("Library Alpha content hash does not match its definition")

    factor_assets = {
        _clean(item.get("source_object_id")): item
        for item in service.list(component_type="FACTOR")
    }
    factor_closure: list[dict[str, Any]] = []
    for index, component in enumerate((alpha.get("spec") or {}).get("components") or []):
        factor_id = _clean(component.get("factor_definition_id"))
        factor_version = _clean(component.get("factor_version"))
        factor_hash = _clean(component.get("factor_spec_hash"))
        factor_asset = factor_assets.get(factor_id)
        if factor_asset is None:
            raise ValueError(f"Alpha dependency is not published in Library: {factor_id}")
        factor = dict(factor_asset.get("content") or {})
        if _clean(factor.get("state")).upper() != "VALIDATED":
            raise ValueError(f"Alpha dependency is not validated: {factor_id}@{factor_version}")
        if _clean(factor_asset.get("source_object_version")) != factor_version:
            raise ValueError(f"Alpha dependency version mismatch: {factor_id}@{factor_version}")
        if factor_hash != _clean(factor_asset.get("content_hash")):
            raise ValueError(f"Alpha dependency hash mismatch: {factor_id}@{factor_version}")
        factor_closure.append({
            "component_index": index,
            "library_asset_id": _clean(factor_asset.get("library_asset_id")),
            "library_asset_version": factor_asset.get("version"),
            "factor_definition_id": factor_id,
            "factor_version": factor_version,
            "factor_spec_hash": factor_hash,
            "factor_name": _clean(component.get("factor_name") or factor.get("name")),
            "weight": component.get("weight"),
            "transform": _clean(component.get("transform")),
            "ascending": bool(component.get("ascending", True)),
        })

    return {
        "schema_version": SIGNAL_SOURCE_SCHEMA_VERSION,
        "type": LIBRARY_ALPHA,
        "status": "REFERENCE_READY",
        "execution_status": "NOT_CONNECTED",
        "library_asset_id": _clean(alpha_asset.get("library_asset_id")),
        "library_asset_version": alpha_asset.get("version"),
        "alpha_definition_id": _clean(alpha.get("definition_id")),
        "alpha_version": _clean(alpha.get("version")),
        "alpha_spec_hash": alpha_hash,
        "alpha_engine_version": _clean(alpha.get("engine_version")),
        "alpha_code_hash": _clean(alpha.get("code_hash")),
        "alpha_name": _clean(alpha.get("name") or alpha_asset.get("name")),
        "factor_closure": factor_closure,
        "pinned_at": _now(),
    }


def resolve_strategy_signal_source(
    raw: Any,
    *,
    strategy_code: Any = "",
    library: ResearchLibraryService | None = None,
) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    source_type = _clean(source.get("type") or LEGACY_STRATEGY_CODE).upper()
    if source_type not in SIGNAL_SOURCE_TYPES:
        raise ValueError(f"signal_source.type must be one of {sorted(SIGNAL_SOURCE_TYPES)}")
    if source_type == LEGACY_STRATEGY_CODE:
        return legacy_strategy_code_source(source.get("strategy_code") or strategy_code)
    return resolve_library_alpha_source(source.get("library_asset_id"), library=library)


def list_library_alpha_sources(
    *, library: ResearchLibraryService | None = None,
) -> list[dict[str, Any]]:
    """List Strategy-compatible Alpha choices without hiding invalid assets."""
    service = library or ResearchLibraryService(get_default_store())
    results: list[dict[str, Any]] = []
    for asset in service.list(component_type="ALPHA"):
        try:
            source = resolve_library_alpha_source(asset.get("library_asset_id"), library=service)
            results.append(source)
        except ValueError as exc:
            results.append({
                "schema_version": SIGNAL_SOURCE_SCHEMA_VERSION,
                "type": LIBRARY_ALPHA,
                "status": "INVALID",
                "execution_status": "NOT_CONNECTED",
                "library_asset_id": _clean(asset.get("library_asset_id")),
                "library_asset_version": asset.get("version"),
                "alpha_name": _clean(asset.get("name")),
                "error": str(exc),
                "factor_closure": [],
            })
    return results
