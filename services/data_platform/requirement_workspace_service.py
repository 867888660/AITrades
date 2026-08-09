from __future__ import annotations

import hashlib
import json
import math
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

import yaml

from services.config_loader import load_web_settings
from services.binance_market_service import get_binance_spot_symbol_status

from .data_capability_service import ResearchDataCapabilityService
from .library_service import ResearchLibraryService
from .instrument_registry import InstrumentRegistry
from .manifest_resolver import DeterministicManifestResolver
from .models import DataRequirement
from .requirement_compiler import RequirementCompiler
from .requirement_service import normalize_source_selection_policy
from .store import BASE_DIR, DataPlatformStore, json_dumps, utc_now
from .universe_service import UniverseService


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _upper(value: Any, default: str = "") -> str:
    return (_clean(value) or default).upper()


def _lower(value: Any, default: str = "") -> str:
    return (_clean(value) or default).lower()


def _hash(value: Any) -> str:
    return hashlib.sha256(json_dumps(value).encode("utf-8")).hexdigest()


def _date_start(value: Any) -> str | None:
    raw = _clean(value)
    if not raw:
        return None
    if "T" in raw:
        return raw
    return f"{raw}T00:00:00+00:00"


def _date_end(value: Any) -> str | None:
    raw = _clean(value)
    if not raw:
        return None
    if "T" in raw:
        return raw
    return f"{raw}T23:59:59+00:00"


def _parse_utc(value: Any) -> datetime | None:
    raw = _clean(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _latest_completed_time(frequency: str) -> str | None:
    seconds = {
        "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
        "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600,
        "8h": 28800, "12h": 43200, "1d": 86400,
    }.get(_lower(frequency))
    if not seconds:
        return None
    now = datetime.now(timezone.utc)
    boundary = int(now.timestamp()) // seconds * seconds
    return (datetime.fromtimestamp(boundary, tz=timezone.utc) - timedelta(milliseconds=1)).isoformat()


def _latest_available_is_current(
    instrument_id: str,
    frequency: str,
    available_end: datetime | None,
    system_latest_end: datetime | None,
) -> bool:
    if not available_end or not system_latest_end:
        return False
    if available_end >= system_latest_end:
        return True
    if instrument_id.lower().startswith("equity:") and frequency.lower() == "1d":
        return system_latest_end - available_end <= timedelta(days=7)
    return False


def default_requirement_spec(name: str = "New Requirement") -> dict[str, Any]:
    today = datetime.now(timezone.utc).date()
    start = (today - timedelta(days=365)).isoformat()
    return {
        "name": _clean(name) or "New Requirement",
        "target": {
            "scope": "MANUAL_INSTRUMENTS",
            "universe_id": "",
        },
        "scope": {
            "provider": "BINANCE",
            "gateway": "DATATUBE",
            "market": "SPOT",
            "asset_type": "CRYPTO",
            "instruments": {"type": "STATIC_LIST", "include": ["BTCUSDT"]},
        },
        "time": {
            "mode": "FIXED_START_LATEST_END",
            "start": start,
            "end": "LATEST_AVAILABLE",
            "lookback_value": None,
            "lookback_unit": "DAYS",
        },
        "data": {
            "dataset_type": "BARS",
            "frequency": "1h",
            "fields": ["open", "high", "low", "close", "volume"],
            "delivery_mode": "HISTORICAL",
        },
        "advanced": {
            "point_in_time": "AS_OF",
            "available_time": "BAR_END_AVAILABLE_TIME",
            "adjustment": "NONE",
            "quality_policy": "STRICT",
            "provider_policy": "FIXED",
            "allowed_sources": ["binance"],
            "preferred_sources": ["binance"],
            "max_latency_seconds": None,
            "gap_policy": "REQUIRE_COMPLETE",
        },
    }


def normalize_requirement_spec(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Requirement must be an object")
    source = dict(raw.get("requirement") or raw)
    base = default_requirement_spec(_clean(source.get("name")) or "New Requirement")
    target = dict(source.get("target") or {})
    scope = dict(source.get("scope") or {})
    instruments = dict(scope.get("instruments") or {})
    time_spec = dict(source.get("time") or {})
    data = dict(source.get("data") or {})
    advanced = dict(source.get("advanced") or {})

    target_scope = _upper(target.get("scope") or scope.get("target"), "MANUAL_INSTRUMENTS")
    target_scope = {
        "PRIMARY_UNIVERSE": "SPECIFIC_UNIVERSE",
        "UNIVERSE": "SPECIFIC_UNIVERSE",
    }.get(target_scope, target_scope)
    if target_scope not in {"SPECIFIC_UNIVERSE", "MANUAL_INSTRUMENTS"}:
        raise ValueError(f"Unsupported Requirement target: {target_scope}")
    universe_id = _clean(target.get("universe_id") or scope.get("universe_id"))
    if target_scope == "SPECIFIC_UNIVERSE" and not universe_id:
        raise ValueError("Specific Universe target requires universe_id")

    include = []
    for value in instruments.get("include") or []:
        item = _clean(value).upper()
        if item and item not in include:
            include.append(item)
    selection_type = "UNIVERSE_REFERENCE" if target_scope == "SPECIFIC_UNIVERSE" else _upper(instruments.get("type"), "STATIC_LIST")
    if selection_type == "STATIC_LIST" and not include:
        raise ValueError("Select at least one instrument")
    if selection_type == "UNIVERSE_REFERENCE":
        include = []

    mode = _upper(time_spec.get("mode"), base["time"]["mode"])
    allowed_modes = {"FIXED", "FIXED_START_LATEST_END", "LATEST_AVAILABLE", "ROLLING", "LIVE"}
    if mode not in allowed_modes:
        raise ValueError(f"Unsupported time mode: {mode}")
    start = _clean(time_spec.get("start")) or None
    end = _clean(time_spec.get("end")) or None
    if mode in {"FIXED", "FIXED_START_LATEST_END"} and not start:
        raise ValueError("Start date is required")
    if mode == "FIXED" and (not end or end == "LATEST_AVAILABLE"):
        raise ValueError("Fixed time mode requires an end date")
    if mode in {"FIXED_START_LATEST_END", "LATEST_AVAILABLE"}:
        end = "LATEST_AVAILABLE"

    fields = []
    for value in data.get("fields") or []:
        field = _lower(value)
        if field and field not in fields:
            fields.append(field)
    if not fields:
        raise ValueError("Select at least one field")

    normalized = deepcopy(base)
    normalized["name"] = _clean(source.get("name")) or base["name"]
    normalized["target"] = {
        "scope": target_scope,
        "universe_id": universe_id,
    }
    normalized["scope"] = {
        "provider": _upper(scope.get("provider"), "BINANCE"),
        "gateway": _upper(scope.get("gateway"), "OPENBB" if _upper(scope.get("provider"), "BINANCE") not in {"AUTO", "BINANCE", "FINNHUB", "COINGECKO", "POLYMARKET"} else "DATATUBE"),
        "market": _upper(scope.get("market"), "SPOT"),
        "asset_type": _upper(scope.get("asset_type"), "CRYPTO"),
        "instruments": {
            "type": selection_type,
            "include": include,
            "rule": dict(instruments.get("rule") or {}),
        },
    }
    normalized["time"] = {
        "mode": mode,
        "start": start,
        "end": end,
        "lookback_value": int(time_spec["lookback_value"]) if time_spec.get("lookback_value") not in (None, "") else None,
        "lookback_unit": _upper(time_spec.get("lookback_unit"), "DAYS"),
    }
    normalized["data"] = {
        "dataset_type": _upper(data.get("dataset_type"), "BARS"),
        "frequency": _lower(data.get("frequency"), "1h"),
        "fields": sorted(fields),
        "delivery_mode": _upper(data.get("delivery_mode"), "HISTORICAL"),
    }
    provider = normalized["scope"]["provider"].lower()
    raw_source_policy = advanced.get("source_selection_policy") or {
        "mode": _upper(advanced.get("provider_policy"), "FIXED"),
        "allowed_sources": advanced.get("allowed_sources") or (
            [] if provider == "auto" else [provider]
        ),
        "preferred_sources": advanced.get("preferred_sources") or (
            [] if provider == "auto" else [provider]
        ),
        "per_instrument": advanced.get("per_instrument_sources") or {},
    }
    source_selection_policy = normalize_source_selection_policy(raw_source_policy)
    normalized["advanced"] = {
        "point_in_time": _upper(advanced.get("point_in_time"), "AS_OF"),
        "available_time": _upper(advanced.get("available_time"), "BAR_END_AVAILABLE_TIME"),
        "adjustment": _upper(advanced.get("adjustment"), "NONE"),
        "quality_policy": _upper(advanced.get("quality_policy"), "STRICT"),
        "provider_policy": source_selection_policy["mode"],
        "allowed_sources": source_selection_policy["allowed_sources"],
        "preferred_sources": source_selection_policy["preferred_sources"],
        "per_instrument_sources": source_selection_policy["per_instrument"],
        "max_latency_seconds": int(advanced["max_latency_seconds"]) if advanced.get("max_latency_seconds") not in (None, "") else None,
        "gap_policy": _upper(advanced.get("gap_policy"), "REQUIRE_COMPLETE"),
    }
    return normalized


class RequirementWorkspaceService:
    """Shared Requirement authoring boundary.

    Library owns each Requirement. Research stores only a reference to that shared
    object. RequirementSet remains a derived compiler output and is never edited
    directly.
    """

    def __init__(self, store: DataPlatformStore):
        self.store = store
        self.compiler = RequirementCompiler(store)

    @staticmethod
    def to_script(spec: dict[str, Any]) -> str:
        return yaml.safe_dump(
            {"requirement": normalize_requirement_spec(spec)},
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )

    @staticmethod
    def from_script(script: str) -> dict[str, Any]:
        try:
            payload = yaml.safe_load(str(script or ""))
        except yaml.YAMLError as exc:
            raise ValueError(f"Invalid Requirement script: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("Requirement script must contain a mapping")
        return normalize_requirement_spec(payload)

    def create_research_requirement(self, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_project(project_id)
        asset = self.create_library_requirement(payload)
        return self.add_library_to_research(project_id, asset["library_asset_id"])

    def suggest_for_universe(self, project_id: str, universe_id: str) -> dict[str, Any]:
        project = self._require_project(project_id)
        universe = self._project_universe(project_id, universe_id)
        instruments = list((universe.get("current_resolution") or {}).get("instrument_ids") or [])
        if not instruments:
            raise ValueError("Universe has no resolved Instruments")

        factors = self._factor_specs(project_id)
        factor_fields = []
        factor_frequencies = []
        for factor in factors:
            formula = factor.get("formula") if isinstance(factor.get("formula"), dict) else factor
            field = _lower(formula.get("input") or factor.get("input_field"))
            if field and field not in factor_fields:
                factor_fields.append(field)
            frequency = _lower(factor.get("frequency"))
            if frequency and frequency not in factor_frequencies:
                factor_frequencies.append(frequency)

        recommendation = self._recommend_source(instruments)
        spec = default_requirement_spec(f"{universe['name']} Data")
        spec["target"] = {"scope": "SPECIFIC_UNIVERSE", "universe_id": universe["universe_id"]}
        spec["scope"].update({
            "provider": recommendation["provider"],
            "gateway": recommendation["gateway"],
            "market": recommendation["market"],
            "asset_type": recommendation["asset_type"],
            "instruments": {"type": "UNIVERSE_REFERENCE", "include": []},
        })
        spec["data"].update({
            "dataset_type": recommendation["dataset_type"],
            "frequency": factor_frequencies[0] if len(factor_frequencies) == 1 else recommendation["frequency"],
            "fields": sorted(set(recommendation["fields"]) | set(factor_fields)),
        })
        existing = next((
            item for item in self.list_project_items(project_id, include_derived=False)
            if (item.get("spec") or {}).get("target", {}).get("universe_id") == universe["universe_id"]
        ), None)
        if existing:
            spec["time"] = deepcopy(existing["spec"]["time"])
        return {
            "spec": normalize_requirement_spec(spec),
            "universe": {
                "universe_id": universe["universe_id"],
                "name": universe["name"],
                "member_count": len(instruments),
                "instrument_ids": instruments,
            },
            "suggested": {
                "data": not bool(factors),
                "frequency": len(factor_frequencies) != 1,
                "time": existing is None,
                "source": True,
            },
            "source_supports": recommendation["supports"],
            "project": {"project_id": project_id, "title": project["title"]},
        }

    def reconcile_project(self, project_id: str, *, universe_id: str = "") -> dict[str, Any]:
        self._require_project(project_id)
        universe = self._project_universe(project_id, universe_id)
        items = self.list_project_items(project_id, include_derived=False)
        matching = [
            item for item in items
            if (item.get("spec") or {}).get("target", {}).get("scope") == "SPECIFIC_UNIVERSE"
            and (item.get("spec") or {}).get("target", {}).get("universe_id") == universe["universe_id"]
        ]
        current_ids = list((universe.get("current_resolution") or {}).get("instrument_ids") or [])
        if not matching:
            legacy = next((
                item for item in items
                if (item.get("spec") or {}).get("target", {}).get("scope") != "SPECIFIC_UNIVERSE"
            ), None)
            previous_ids = self._instrument_ids(legacy["spec"]) if legacy else []
            return {
                "status": "ATTENTION" if legacy else "REQUIRED",
                "universe_id": universe["universe_id"],
                "requirement_ref_id": legacy["ref_id"] if legacy else "",
                "changes": self._membership_changes(previous_ids, current_ids),
                "reasons": ([
                    "The current Requirement stores a fixed Instrument list instead of targeting this Universe."
                ] if legacy else []),
                "message": (
                    "Update the current data configuration to target this Universe."
                    if legacy else "No data requirement is configured for this Universe."
                ),
            }

        item = matching[0]
        previous_ids = self._effective_instrument_ids(project_id, item["ref_id"])
        compatible, reasons = self._requirement_compatibility(item["spec"], current_ids)
        if not compatible:
            return {
                "status": "ATTENTION",
                "universe_id": universe["universe_id"],
                "requirement_ref_id": item["ref_id"],
                "changes": self._membership_changes(previous_ids, current_ids),
                "reasons": reasons,
                "message": f"The current data configuration does not support {len(reasons)} compatibility change(s).",
            }

        stale = bool(universe.get("requirements_stale_at"))
        compiled = self.compile_project(project_id) if stale else None
        status = self.data_status(project_id)
        rows = [
            row for row in status.get("rows") or []
            if row.get("instrument_id") in set(current_ids)
        ]
        states = {str(row.get("status") or "") for row in rows}
        if states & {"UNAVAILABLE", "FAILED"}:
            state = "ATTENTION"
        elif rows and states == {"READY"}:
            state = "READY"
        else:
            state = "PREPARING"
        ready = sum(1 for row in rows if row.get("status") == "READY")
        coverage = round(ready * 100 / len(rows)) if rows else 0
        return {
            "status": state,
            "universe_id": universe["universe_id"],
            "requirement_ref_id": item["ref_id"],
            "auto_updated": bool(compiled),
            "requirement_set_id": status.get("requirement_set_id"),
            "coverage_percent": coverage,
            "changes": self._membership_changes(previous_ids, current_ids),
            "reasons": [],
            "message": (
                "Data requirements were updated automatically."
                if compiled else "Data requirements already target the current Universe."
            ),
        }

    def update_research_requirement(self, project_id: str, ref_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        item = self.get_project_item(project_id, ref_id)
        if item is None:
            raise ValueError("Requirement not found")
        if not item.get("library_asset_id"):
            raise ValueError("Shared Library Requirement not found")
        self.update_library_requirement(item["library_asset_id"], payload)
        return self.get_project_item(project_id, ref_id)  # type: ignore[return-value]

    def duplicate_project_item(self, project_id: str, ref_id: str) -> dict[str, Any]:
        item = self.get_project_item(project_id, ref_id)
        if item is None or not item.get("library_asset_id"):
            raise ValueError("Requirement cannot be duplicated")
        spec = deepcopy(item["spec"])
        spec["name"] = f"{spec['name']} Copy"
        asset = self.save_as_library_requirement(item["library_asset_id"], {"spec": spec})
        return self.add_library_to_research(project_id, asset["library_asset_id"])

    def save_as_for_project(self, project_id: str, ref_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Create a new Library Requirement and make only this Research use it."""
        item = self.get_project_item(project_id, ref_id)
        if item is None or not item.get("library_asset_id"):
            raise ValueError("Requirement not found")
        asset = self.save_as_library_requirement(item["library_asset_id"], payload)
        return self.replace_project_item(project_id, ref_id, asset["library_asset_id"])

    def remove_project_item(self, project_id: str, ref_id: str) -> None:
        item = self.get_project_item(project_id, ref_id)
        if item is None:
            raise ValueError("Requirement not found")
        if not item.get("library_asset_id"):
            raise ValueError("Derived Requirements must be changed at their source")
        with self.store.transaction(immediate=True) as conn:
            conn.execute("DELETE FROM project_requirement_items WHERE ref_id=? AND project_id=?", (ref_id, _clean(project_id)))
        if self.compile_project(project_id, allow_empty=True) is None:
            with self.store.transaction(immediate=True) as conn:
                conn.execute("DELETE FROM research_requirement_refs WHERE project_id=?", (_clean(project_id),))

    def add_library_to_research(self, project_id: str, library_asset_id: str) -> dict[str, Any]:
        self._require_project(project_id)
        asset = self.get_library_asset(library_asset_id)
        if asset is None or asset.get("archived_at"):
            raise ValueError("Library Requirement not found")
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            existing = conn.execute(
                "SELECT ref_id FROM project_requirement_items WHERE project_id=? AND library_asset_id=?",
                (_clean(project_id), _clean(library_asset_id)),
            ).fetchone()
            if existing:
                return self.get_project_item(project_id, str(existing[0]))  # type: ignore[return-value]
            ref_id = f"requirement_ref_{uuid.uuid4().hex}"
            next_order = int(conn.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM project_requirement_items WHERE project_id=?",
                (_clean(project_id),),
            ).fetchone()[0])
            conn.execute(
                """INSERT INTO project_requirement_items(
                       ref_id, project_id, origin_type, requirement_definition_id,
                       library_asset_id, source_object_id, overrides_json, sort_order,
                       created_at, updated_at
                   ) VALUES (?, ?, 'LIBRARY', NULL, ?, ?, '{}', ?, ?, ?)""",
                (ref_id, _clean(project_id), asset["library_asset_id"], asset["library_asset_id"], next_order, now, now),
            )
        self.compile_project(project_id)
        return self.get_project_item(project_id, ref_id)  # type: ignore[return-value]

    def replace_project_item(self, project_id: str, ref_id: str, library_asset_id: str) -> dict[str, Any]:
        item = self.get_project_item(project_id, ref_id)
        asset = self.get_library_asset(library_asset_id)
        if item is None:
            raise ValueError("Requirement not found")
        if asset is None or asset.get("archived_at"):
            raise ValueError("Replacement Library Requirement not found")
        with self.store.transaction(immediate=True) as conn:
            duplicate = conn.execute(
                """SELECT ref_id FROM project_requirement_items
                   WHERE project_id=? AND library_asset_id=? AND ref_id<>?""",
                (_clean(project_id), asset["library_asset_id"], _clean(ref_id)),
            ).fetchone()
            if duplicate:
                raise ValueError("This Research already uses the selected Requirement")
            conn.execute(
                """UPDATE project_requirement_items
                   SET origin_type='LIBRARY', requirement_definition_id=NULL,
                       library_asset_id=?, source_object_id=?, updated_at=?
                   WHERE project_id=? AND ref_id=?""",
                (asset["library_asset_id"], asset["library_asset_id"], utc_now(),
                 _clean(project_id), _clean(ref_id)),
            )
        self.compile_project(project_id)
        return self.get_project_item(project_id, ref_id)  # type: ignore[return-value]

    def list_project_items(self, project_id: str, *, include_derived: bool = True) -> list[dict[str, Any]]:
        self._require_project(project_id)
        with self.store.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM project_requirement_items WHERE project_id=? ORDER BY sort_order, created_at",
                (_clean(project_id),),
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = self._item_from_row(row)
            if item:
                items.append(item)
        if include_derived:
            items.extend(self._derived_factor_items(project_id, items))
        return items

    def get_project_item(self, project_id: str, ref_id: str) -> dict[str, Any] | None:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM project_requirement_items WHERE project_id=? AND ref_id=?",
                (_clean(project_id), _clean(ref_id)),
            ).fetchone()
        return self._item_from_row(row) if row else None

    def create_library_requirement(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create the one shared Requirement object owned by Library."""
        spec = normalize_requirement_spec(dict(payload.get("spec") or payload))
        now = utc_now()
        asset_id = f"library_requirement_{uuid.uuid4().hex}"
        draft_id = f"library_requirement_record_{uuid.uuid4().hex}"
        with self.store.transaction(immediate=True) as conn:
            self._assert_unique_library_name(conn, spec["name"])
            # source_draft_id is retained as an internal compatibility record for
            # older databases. It is not an authoring state and never reaches UI.
            conn.execute(
                """INSERT INTO library_requirement_drafts(
                       draft_id, name, base_library_asset_id, base_asset_version,
                       state, spec_json, spec_hash, created_at, updated_at
                   ) VALUES (?, ?, NULL, NULL, 'PUBLISHED', ?, ?, ?, ?)""",
                (draft_id, spec["name"], json_dumps(spec), _hash(spec), now, now),
            )
            internal_version = int(conn.execute(
                "SELECT COALESCE(MAX(asset_version), 0) + 1 FROM library_requirement_assets WHERE name=?",
                (spec["name"],),
            ).fetchone()[0])
            conn.execute(
                """INSERT INTO library_requirement_assets(
                       library_asset_id, name, asset_version, spec_json, content_hash,
                       source_draft_id, published_at, updated_at, archived_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
                (asset_id, spec["name"], internal_version, json_dumps(spec), _hash(spec),
                 draft_id, now, now),
            )
        return self.get_library_asset(asset_id)  # type: ignore[return-value]

    def update_library_requirement(self, library_asset_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.get_library_asset(library_asset_id)
        if current is None or current.get("archived_at"):
            raise ValueError("Library Requirement not found")
        spec = normalize_requirement_spec(dict(payload.get("spec") or payload))
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            self._assert_unique_library_name(conn, spec["name"], except_asset_id=current["library_asset_id"])
            internal_version = current["version"]
            if spec["name"].lower() != current["name"].lower():
                internal_version = int(conn.execute(
                    "SELECT COALESCE(MAX(asset_version), 0) + 1 FROM library_requirement_assets WHERE name=?",
                    (spec["name"],),
                ).fetchone()[0])
            conn.execute(
                """UPDATE library_requirement_assets
                   SET name=?, asset_version=?, spec_json=?, content_hash=?, updated_at=?
                   WHERE library_asset_id=? AND archived_at IS NULL""",
                (spec["name"], internal_version, json_dumps(spec), _hash(spec), now, current["library_asset_id"]),
            )
            conn.execute(
                """UPDATE library_requirement_drafts
                   SET name=?, spec_json=?, spec_hash=?, updated_at=?
                   WHERE draft_id=?""",
                (spec["name"], json_dumps(spec), _hash(spec), now, current["source_draft_id"]),
            )
            project_rows = conn.execute(
                """SELECT DISTINCT r.project_id
                   FROM project_requirement_items r
                   JOIN research_projects p ON p.project_id=r.project_id
                   WHERE r.library_asset_id=? AND p.archived_at IS NULL""",
                (current["library_asset_id"],),
            ).fetchall()
        for row in project_rows:
            self.compile_project(str(row["project_id"]))
        return self.get_library_asset(current["library_asset_id"])  # type: ignore[return-value]

    def save_as_library_requirement(self, library_asset_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.get_library_asset(library_asset_id)
        if current is None:
            raise ValueError("Library Requirement not found")
        raw_spec = deepcopy(current["spec"])
        incoming = payload.get("spec") or payload
        if incoming:
            raw_spec = dict(incoming)
        if raw_spec.get("name") == current["name"]:
            raw_spec["name"] = f"{current['name']} Copy"
        return self.create_library_requirement({"spec": raw_spec})

    def archive_library_requirement(self, library_asset_id: str) -> dict[str, Any]:
        asset = self.get_library_asset(library_asset_id)
        if asset is None:
            raise ValueError("Library Requirement not found")
        if asset["usage_count"]:
            raise ValueError("Remove this Requirement from every Research before archiving it")
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                "UPDATE library_requirement_assets SET archived_at=?, updated_at=? WHERE library_asset_id=?",
                (now, now, asset["library_asset_id"]),
            )
        return {**asset, "archived_at": now, "updated_at": now}

    @staticmethod
    def _assert_unique_library_name(conn: Any, name: str, *, except_asset_id: str = "") -> None:
        row = conn.execute(
            """SELECT library_asset_id FROM library_requirement_assets
               WHERE lower(name)=lower(?) AND archived_at IS NULL AND library_asset_id<>?
               LIMIT 1""",
            (_clean(name), _clean(except_asset_id)),
        ).fetchone()
        if row:
            raise ValueError("A Library Requirement with this name already exists. Use Save As with a new name.")

    def create_library_draft(self, payload: dict[str, Any], *, base_asset_id: str = "") -> dict[str, Any]:
        asset = self.get_library_asset(base_asset_id) if _clean(base_asset_id) else None
        raw_spec = dict(payload.get("spec") or (asset or {}).get("spec") or default_requirement_spec(payload.get("name") or "New Requirement"))
        if payload.get("name"):
            raw_spec["name"] = _clean(payload["name"])
        spec = normalize_requirement_spec(raw_spec)
        now = utc_now()
        draft_id = f"library_requirement_draft_{uuid.uuid4().hex}"
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                """INSERT INTO library_requirement_drafts(
                       draft_id, name, base_library_asset_id, base_asset_version,
                       state, spec_json, spec_hash, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, 'DRAFT', ?, ?, ?, ?)""",
                (draft_id, spec["name"], asset["library_asset_id"] if asset else None,
                 asset["version"] if asset else None, json_dumps(spec), _hash(spec), now, now),
            )
        return self.get_library_draft(draft_id)  # type: ignore[return-value]

    def update_library_draft(self, draft_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.get_library_draft(draft_id)
        if current is None or current["state"] != "DRAFT":
            raise ValueError("Editable Library draft not found")
        spec = normalize_requirement_spec(dict(payload.get("spec") or payload))
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                "UPDATE library_requirement_drafts SET name=?, spec_json=?, spec_hash=?, updated_at=? WHERE draft_id=? AND state='DRAFT'",
                (spec["name"], json_dumps(spec), _hash(spec), now, _clean(draft_id)),
            )
        return self.get_library_draft(draft_id)  # type: ignore[return-value]

    def publish_library_draft(self, draft_id: str) -> dict[str, Any]:
        draft = self.get_library_draft(draft_id)
        if draft is None or draft["state"] != "DRAFT":
            raise ValueError("Editable Library draft not found")
        spec = normalize_requirement_spec(draft["spec"])
        now = utc_now()
        asset_id = f"library_requirement_{uuid.uuid4().hex}"
        with self.store.transaction(immediate=True) as conn:
            version = int(conn.execute(
                "SELECT COALESCE(MAX(asset_version), 0) + 1 FROM library_requirement_assets WHERE name=?",
                (spec["name"],),
            ).fetchone()[0])
            conn.execute(
                """INSERT INTO library_requirement_assets(
                       library_asset_id, name, asset_version, spec_json,
                       content_hash, source_draft_id, published_at, updated_at, archived_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
                (asset_id, spec["name"], version, json_dumps(spec), _hash(spec), draft["draft_id"], now, now),
            )
            conn.execute(
                "UPDATE library_requirement_drafts SET state='PUBLISHED', updated_at=? WHERE draft_id=?",
                (now, draft["draft_id"]),
            )
        return self.get_library_asset(asset_id)  # type: ignore[return-value]

    def get_library_draft(self, draft_id: str) -> dict[str, Any] | None:
        with self.store.connection() as conn:
            row = conn.execute("SELECT * FROM library_requirement_drafts WHERE draft_id=?", (_clean(draft_id),)).fetchone()
        if row is None:
            return None
        return {
            "draft_id": str(row["draft_id"]), "name": str(row["name"]),
            "base_library_asset_id": str(row["base_library_asset_id"] or ""),
            "base_asset_version": row["base_asset_version"], "state": str(row["state"]),
            "spec": json.loads(row["spec_json"]), "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    def list_library_drafts(self, *, state: str = "DRAFT") -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            rows = conn.execute(
                "SELECT draft_id FROM library_requirement_drafts WHERE state=? ORDER BY updated_at DESC",
                (_upper(state, "DRAFT"),),
            ).fetchall()
        return [self.get_library_draft(str(row[0])) for row in rows]  # type: ignore[list-item]

    def list_library_assets(self, *, current_only: bool = False) -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM library_requirement_assets
                   WHERE archived_at IS NULL
                   ORDER BY updated_at DESC, name"""
            ).fetchall()
        assets = [self._library_asset_from_row(row) for row in rows]
        return assets

    def get_library_asset(self, library_asset_id: str) -> dict[str, Any] | None:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM library_requirement_assets WHERE library_asset_id=?",
                (_clean(library_asset_id),),
            ).fetchone()
        return self._library_asset_from_row(row) if row else None

    def library_usage(self, library_asset_id: str) -> dict[str, Any]:
        asset = self.get_library_asset(library_asset_id)
        if asset is None:
            raise ValueError("Library Requirement not found")
        with self.store.connection() as conn:
            rows = conn.execute(
                """SELECT p.project_id, p.title
                   FROM project_requirement_items r
                   JOIN research_projects p ON p.project_id=r.project_id
                   WHERE r.library_asset_id=? AND p.archived_at IS NULL
                   ORDER BY p.title""",
                (asset["library_asset_id"],),
            ).fetchall()
        research = [dict(row) for row in rows]
        return {"library_asset": asset, "research": research, "research_count": len(research)}

    def publish_research_item(self, project_id: str, ref_id: str) -> dict[str, Any]:
        item = self.get_project_item(project_id, ref_id)
        if item is None or item["origin"] != "RESEARCH":
            raise ValueError("Only a local Research Requirement can be published")
        draft = self.create_library_draft({"spec": item["spec"]})
        return self.publish_library_draft(draft["draft_id"])

    def compile_project(
        self,
        project_id: str,
        *,
        allow_empty: bool = False,
        sync_availability: bool = True,
    ) -> dict[str, Any] | None:
        if sync_availability:
            self._sync_provider_availability_overrides(project_id)
        authored = self.list_project_items(project_id, include_derived=False)
        alpha_specs = self._alpha_specs(project_id)
        factor_specs = self._factor_specs(project_id, alpha_specs=alpha_specs)
        if not authored and not factor_specs and not alpha_specs:
            if allow_empty:
                return None
            raise ValueError("Add at least one Requirement or validated Factor before compiling")
        manual = [
            declaration
            for item in authored
            for declaration in self._compiler_declarations(item["spec"], item)
        ]
        if factor_specs:
            universe = self._project_universe(project_id)
            snapshot_id = str(
                (universe.get("current_resolution") or {}).get("legacy_snapshot_id") or ""
            )
            snapshot = UniverseService(self.store).get_snapshot(snapshot_id)
            if snapshot is None:
                raise ValueError("Primary Universe Snapshot is unavailable")
            first_input = dict((factor_specs[0].get("inputs") or [{}])[0])
            authored_starts = [
                _date_start((item["spec"].get("time") or {}).get("start"))
                for item in authored
                if (item["spec"].get("time") or {}).get("start")
            ]
            authored_ends = [
                _date_end((item["spec"].get("time") or {}).get("end"))
                for item in authored
                if (item["spec"].get("time") or {}).get("end")
                and (item["spec"].get("time") or {}).get("end") != "LATEST_AVAILABLE"
            ]
            context = {
                "universe_snapshot_id": snapshot.universe_snapshot_id,
                "instrument_ids": list(snapshot.actual_instrument_ids),
                "data_type": _lower(first_input.get("dataset"), "bars"),
                "frequency": _lower(first_input.get("frequency"), "1d"),
                "history_start": min(authored_starts) if authored_starts else (
                    datetime.now(timezone.utc) - timedelta(days=365)
                ).replace(microsecond=0).isoformat(),
                "history_end": max(authored_ends) if authored_ends and len(authored_ends) == len(authored) else None,
                "adjustment": "NONE",
                "time_semantics": (
                    "EVENT_TIME_AVAILABLE_TIME"
                    if _lower(first_input.get("dataset")) == "price_history"
                    else "BAR_END_AVAILABLE_TIME"
                ),
                "point_in_time_policy": "AS_OF",
                "quality_policy": "STRICT",
                "source_policy": "AUTO",
                "source_selection_policy": {
                    "mode": "AUTO",
                    "allowed_sources": [],
                    "preferred_sources": [],
                },
            }
        else:
            context = self._compiler_context(authored[0]["spec"])
        result = self.compiler.compile(
            project_id=_clean(project_id), factor_specs=factor_specs,
            alpha_specs=alpha_specs,
            manual_requirements=manual, context=context,
        )
        ResearchLibraryService(self.store).set_local_requirements(
            project_id=_clean(project_id), requirement_set_id=result.requirement_set_id,
        )
        # Only Universes explicitly targeted by an authored Requirement have
        # been reconciled. Other bindings must keep their pending state so the
        # UI can still offer Create or Review for those Universes.
        target_universe_ids = sorted({
            _clean((item.get("spec") or {}).get("target", {}).get("universe_id"))
            for item in authored
            if (item.get("spec") or {}).get("target", {}).get("scope") == "SPECIFIC_UNIVERSE"
            and _clean((item.get("spec") or {}).get("target", {}).get("universe_id"))
        })
        if target_universe_ids:
            placeholders = ",".join("?" for _ in target_universe_ids)
            with self.store.transaction(immediate=True) as conn:
                conn.execute(
                    f"""UPDATE research_universe_bindings_v2
                        SET requirements_stale_at=NULL,updated_at=?
                        WHERE project_id=? AND is_active=1 AND removed_at IS NULL
                          AND universe_id IN ({placeholders})""",
                    (utc_now(), _clean(project_id), *target_universe_ids),
                )
        return {
            "requirement_set_id": result.requirement_set_id,
            "version": result.version,
            "requirements": len(result.requirements),
            "factors": len(factor_specs),
            "alphas": len(alpha_specs),
        }

    def refresh_effective_requirements(self, project_id: str) -> dict[str, Any]:
        try:
            compiled = self.compile_project(project_id, allow_empty=True)
        except ValueError as exc:
            if str(exc) != "Universe is not used by this Research":
                raise
            return {
                "project_id": _clean(project_id),
                "requirement_set_id": "",
                "requirements": 0,
                "factors": len(self._factor_specs(project_id)),
                "alphas": len(self._alpha_specs(project_id)),
                "pending_reason": "PRIMARY_UNIVERSE_REQUIRED",
                "status": self.data_status(project_id),
            }
        if compiled is None:
            with self.store.transaction(immediate=True) as conn:
                conn.execute(
                    "DELETE FROM research_requirement_refs WHERE project_id=?",
                    (_clean(project_id),),
                )
            return {
                "project_id": _clean(project_id),
                "requirement_set_id": "",
                "requirements": 0,
                "factors": 0,
                "alphas": 0,
                "status": self.data_status(project_id),
            }
        return {
            "project_id": _clean(project_id),
            **compiled,
            "status": self.data_status(project_id),
        }

    def data_status(self, project_id: str, requirement_set_id: str = "") -> dict[str, Any]:
        requested_set_id = _clean(requirement_set_id)
        if not requested_set_id and self._sync_provider_availability_overrides(project_id):
            self.compile_project(
                project_id,
                allow_empty=True,
                sync_availability=False,
            )
        ref = ResearchLibraryService(self.store).get_requirement_ref(project_id)
        if not requested_set_id and ref is None:
            return {"summary": {"requirements": 0, "resolved_datasets": 0, "ready": 0, "partial": 0, "not_prepared": 0, "unavailable": 0, "missing": 0}, "rows": [], "latest_checked": utc_now()}
        requirement_set = self.compiler.get(
            requested_set_id or str(ref["requirement_set_id"])
        )
        if requirement_set is None:
            raise ValueError("RequirementSet not found")
        if requirement_set.project_id != _clean(project_id):
            raise ValueError("RequirementSet does not belong to this Research")
        resolution = DeterministicManifestResolver(self.store).resolve(
            requirement_set.requirement_set_id, verify_physical=False,
        ).to_dict()
        bindings = {(item["requirement_id"], item["instrument_id"]): item for item in resolution["bindings"]}
        project_items = self.list_project_items(project_id)
        item_names = {item["ref_id"]: item["name"] for item in project_items}
        item_assets = {
            item["ref_id"]: item.get("library_asset_id")
            for item in project_items if item.get("library_asset_id")
        }
        item_providers = {
            item["ref_id"]: _upper((item.get("spec") or {}).get("scope", {}).get("provider"))
            for item in project_items
            if isinstance(item.get("spec"), dict)
        }
        item_overrides = {
            item["ref_id"]: dict(item.get("overrides") or {})
            for item in project_items
        }
        checks_by_object: dict[str, list[dict[str, Any]]] = {}
        for check in resolution.get("checks") or []:
            checks_by_object.setdefault(str(check.get("object_ref") or ""), []).append(check)
        capabilities = ResearchDataCapabilityService(load_web_settings(), base_dir=BASE_DIR)
        links: dict[str, list[str]] = {}
        asset_links: dict[str, list[str]] = {}
        provider_links: dict[str, list[str]] = {}
        for link in requirement_set.dependency_links:
            label = item_names.get(link.origin_id) or link.origin_id or link.origin_type.replace("_SPEC", "").title()
            links.setdefault(link.requirement_id, [])
            if label not in links[link.requirement_id]:
                links[link.requirement_id].append(label)
            library_asset_id = item_assets.get(link.origin_id)
            if library_asset_id:
                asset_links.setdefault(link.requirement_id, [])
                if library_asset_id not in asset_links[link.requirement_id]:
                    asset_links[link.requirement_id].append(library_asset_id)
            provider = item_providers.get(link.origin_id)
            if provider:
                provider_links.setdefault(link.requirement_id, [])
                if provider not in provider_links[link.requirement_id]:
                    provider_links[link.requirement_id].append(provider)
        rows: list[dict[str, Any]] = []
        for requirement in requirement_set.requirements:
            for instrument_id in requirement.instrument_ids:
                binding = bindings.get((requirement.requirement_id, instrument_id))
                candidate = self._catalog_candidate(instrument_id, requirement.data_type, requirement.frequency)
                if binding:
                    status = "READY"
                elif candidate:
                    status = "PARTIAL"
                elif capabilities.can_prepare(instrument_id, requirement.data_type, requirement.frequency):
                    status = "NOT_PREPARED"
                else:
                    status = "UNAVAILABLE"
                object_ref = f"{instrument_id}:{requirement.data_type}:{requirement.frequency}"
                explanations = checks_by_object.get(object_ref, [])
                source_reason = None
                available_range = binding.get("range") if binding else candidate.get("range") if candidate else None
                resolved_latest_end = None
                if requirement.history_end is None and requirement.data_type.lower() == "bars":
                    resolved_latest_end = _latest_completed_time(requirement.frequency)
                    available_end = _parse_utc((available_range or {}).get("end"))
                    latest_end = _parse_utc(resolved_latest_end)
                    if status == "READY" and latest_end:
                        if _latest_available_is_current(
                            instrument_id, requirement.frequency, available_end, latest_end,
                        ):
                            if (
                                instrument_id.lower().startswith("equity:")
                                and requirement.frequency.lower() == "1d"
                                and available_end
                            ):
                                resolved_latest_end = available_end.isoformat()
                        else:
                            status = "PARTIAL"
                            source_reason = "Local data does not yet reach the latest completed interval; automatic preparation will fill the gap."
                if status == "NOT_PREPARED" and instrument_id.lower().startswith("crypto_spot:binance:") and requirement.history_end is None:
                    listed = get_binance_spot_symbol_status(instrument_id.split(":")[-1])
                    if listed is None or listed.get("status") != "TRADING":
                        status = "UNAVAILABLE"
                        source_reason = "The Binance pair is not currently trading, so it cannot satisfy a Latest requirement."
                library_asset_ids = asset_links.get(requirement.requirement_id, [])
                preparation = self._preparation_snapshot(
                    library_asset_ids,
                    instrument_id,
                    requirement.frequency,
                    requirement_id=requirement.requirement_id,
                    project_id=project_id,
                )
                display_status, display_reason = self._display_preparation_status(
                    status, preparation, source_reason,
                )
                preparation_terminal = bool(
                    preparation
                    and isinstance(preparation.get("auto_review"), dict)
                    and preparation["auto_review"].get("terminal")
                )
                adjustment_key = self._availability_override_key(
                    instrument_id,
                    requirement.data_type,
                    requirement.frequency,
                )
                automatic_adjustments = [
                    dict(
                        (
                            item_overrides.get(link.origin_id, {}).get(
                                "availability_starts"
                            )
                            or {}
                        ).get(adjustment_key)
                        or {}
                    )
                    for link in requirement_set.dependency_links
                    if link.requirement_id == requirement.requirement_id
                    and (
                        (
                            item_overrides.get(link.origin_id, {}).get(
                                "availability_starts"
                            )
                            or {}
                        ).get(adjustment_key)
                    )
                ]
                rows.append({
                    "requirement_id": requirement.requirement_id,
                    "instrument_id": instrument_id,
                    "instrument_label": self._instrument_label(instrument_id),
                    "provider": (
                        binding.get("source")
                        if binding
                        else (provider_links.get(requirement.requirement_id) or [
                            instrument_id.split(":")[1] if instrument_id.count(":") >= 2 else ""
                        ])[0]
                    ),
                    "requested_providers": provider_links.get(requirement.requirement_id, []),
                    "resolved_source": binding.get("source") if binding else None,
                    "data_type": requirement.data_type,
                    "frequency": requirement.frequency,
                    "adjustment": requirement.adjustment,
                    "fields": list(requirement.fields),
                    "evaluation_range": {
                        "start": requirement_set.context.get("history_start"),
                        "end": requirement_set.context.get("history_end") or "LATEST_AVAILABLE",
                    },
                    "required_range": {
                        "start": requirement.history_start,
                        "end": requirement.history_end or "LATEST_AVAILABLE",
                        "resolved_end": resolved_latest_end,
                    },
                    "additional_history": {
                        "observations": max(0, int(requirement.lookback_value or 1) - 1),
                        "unit": requirement.lookback_unit,
                    },
                    "available_range": available_range,
                    "dataset_id": binding.get("dataset_id") if binding else candidate.get("dataset_id") if candidate else None,
                    "manifest_id": binding.get("manifest_id") if binding else None,
                    "status": display_status,
                    "raw_status": status,
                    "can_prepare": status in {"PARTIAL", "NOT_PREPARED"} and not preparation_terminal,
                    "reason_code": explanations[0].get("code") if explanations else None,
                    "reason": display_reason or (explanations[0].get("message") if explanations else (
                        "No historical preparation adapter supports this contract." if status == "UNAVAILABLE" else None
                    )),
                    "required_by": links.get(requirement.requirement_id, []),
                    "library_asset_ids": library_asset_ids,
                    "preparation": preparation,
                    "automatic_adjustments": automatic_adjustments,
                })
        counts = {
            key.lower(): sum(1 for row in rows if row["status"] == key)
            for key in ("CHECKING", "QUEUED", "PREPARING", "READY", "NEEDS_ATTENTION", "FAILED", "UNAVAILABLE")
        }
        return {
            "summary": {
                "requirements": len(requirement_set.requirements),
                "resolved_datasets": len(rows),
                **counts,
                "missing": counts["needs_attention"] + counts["failed"] + counts["unavailable"],
            },
            "rows": rows,
            "latest_checked": utc_now(),
            "requirement_set_id": requirement_set.requirement_set_id,
            "scope": (
                "EFFECTIVE"
                if ref and str(ref["requirement_set_id"]) == requirement_set.requirement_set_id
                else "PREVIEW"
            ),
            "resolution": resolution,
        }

    def library_data_status(self, spec: dict[str, Any], library_asset_id: str = "") -> dict[str, Any]:
        """Resolve a shared Requirement without inventing a Research owner."""
        normalized = normalize_requirement_spec(spec)
        context = self._compiler_context(normalized)
        moving_latest = context["history_end"] is None
        resolved_latest_end = _latest_completed_time(context["frequency"]) if moving_latest and context["data_type"] == "bars" else None
        capabilities = ResearchDataCapabilityService(load_web_settings(), base_dir=BASE_DIR)
        resolver = DeterministicManifestResolver(self.store)
        rows: list[dict[str, Any]] = []
        for instrument_id in self._instrument_ids(normalized):
            contract = DataRequirement(
                requirement_id=f"library:{_hash(normalized)[:16]}",
                owner_type="LIBRARY", owner_id="", target_type="INSTRUMENTS",
                instrument_ids=(instrument_id,), data_type=context["data_type"],
                frequency=context["frequency"], fields=tuple(normalized["data"]["fields"]),
                history_mode=normalized["time"]["mode"], history_start=context["history_start"],
                history_end=context["history_end"], lookback_value=normalized["time"].get("lookback_value"),
                lookback_unit=normalized["time"].get("lookback_unit") or "DAYS",
                refresh_mode="AUTOMATIC", refresh_interval_seconds=None, auto_backfill=True,
                usage_level="RESEARCH", priority=50, status="ACTIVE",
                requirement_fingerprint=_hash({"spec": normalized, "instrument_id": instrument_id}),
                adjustment=context["adjustment"], time_semantics=context["time_semantics"],
                point_in_time_policy=context["point_in_time_policy"], quality_policy=context["quality_policy"],
                source_policy=context["source_policy"],
                source_selection_policy=context["source_selection_policy"],
            )
            preferred_sources, allowed_sources, _conflict = resolver._effective_source_policy(
                contract.source_selection_policy, {}, instrument_id,
            )
            binding, _ = resolver._resolve_one(
                contract, instrument_id, preferred_sources=preferred_sources,
                allowed_sources=allowed_sources, verify_physical=False,
            )
            candidate = self._catalog_candidate(instrument_id, context["data_type"], context["frequency"])
            can_prepare = capabilities.can_prepare(instrument_id, context["data_type"], context["frequency"])
            if binding:
                status = "READY"
                if resolved_latest_end:
                    available_end = _parse_utc((binding.get("range") or {}).get("end"))
                    if not available_end or available_end < _parse_utc(resolved_latest_end):
                        status = "PARTIAL"
            elif candidate:
                status = "PARTIAL"
            else:
                status = "NOT_PREPARED" if can_prepare else "UNAVAILABLE"
            preparation = self._preparation_snapshot(
                [library_asset_id] if library_asset_id else [], instrument_id, context["frequency"],
            )
            display_status, display_reason = self._display_preparation_status(status, preparation)
            rows.append({
                "instrument_id": instrument_id,
                "instrument_label": self._instrument_label(instrument_id),
                "provider": normalized["scope"]["provider"],
                "data_type": context["data_type"],
                "frequency": context["frequency"],
                "adjustment": context["adjustment"],
                "required_range": {
                    "start": context["history_start"],
                    "end": context["history_end"] or "LATEST_AVAILABLE",
                    "resolved_end": resolved_latest_end,
                },
                "library_asset_ids": [library_asset_id] if library_asset_id else [],
                "status": display_status,
                "raw_status": status,
                "can_prepare": status in {"PARTIAL", "NOT_PREPARED"} and can_prepare,
                "available_range": binding.get("range") if binding else candidate.get("range") if candidate else None,
                "dataset_id": binding.get("dataset_id") if binding else candidate.get("dataset_id") if candidate else None,
                "reason": display_reason,
                "preparation": preparation,
            })
        statuses = {row["status"] for row in rows}
        if not rows or (statuses and statuses == {"UNAVAILABLE"}):
            overall = "UNAVAILABLE"
        elif "FAILED" in statuses or "UNAVAILABLE" in statuses or "NEEDS_ATTENTION" in statuses:
            overall = "FAILED"
        elif "PREPARING" in statuses:
            overall = "PREPARING"
        elif "QUEUED" in statuses:
            overall = "QUEUED"
        elif "CHECKING" in statuses:
            overall = "CHECKING"
        else:
            overall = "READY"
        available_ends = [
            row["available_range"].get("end") for row in rows
            if row.get("available_range") and row["available_range"].get("end")
        ]
        latest_available = max(available_ends) if available_ends else None
        coverage = {
            "READY": f"Complete coverage through {latest_available}" if latest_available else "Complete coverage",
            "CHECKING": "Checking existing data and preparation eligibility.",
            "QUEUED": "Automatic data preparation is queued.",
            "PREPARING": "Automatic data preparation is in progress.",
            "NEEDS_ATTENTION": "Automatic preparation needs review before it can continue.",
            "FAILED": "Automatic preparation failed. Review the error before retrying.",
            "UNAVAILABLE": "The current provider cannot satisfy this Requirement.",
        }[overall]
        preparation_rows = [row["preparation"] for row in rows if row.get("preparation")]
        progress = self._merge_preparation_progress(preparation_rows)
        return {
            "status": overall,
            "coverage": coverage,
            "latest_available": latest_available,
            "can_prepare": any(row["can_prepare"] for row in rows),
            "preparation": progress,
            "rows": rows,
        }

    def _instrument_label(self, instrument_id: str) -> str:
        instrument = InstrumentRegistry(self.store).get(instrument_id) or {}
        label = _clean(
            instrument.get("display_symbol")
            or instrument.get("display_name")
            or instrument.get("native_symbol")
        )
        if label and not (label.isdigit() and len(label) > 24):
            return label
        native = instrument_id.split(":")[-1]
        return f"{native[:10]}…{native[-8:]}" if len(native) > 24 else native

    @staticmethod
    def _display_preparation_status(
        raw_status: str,
        preparation: dict[str, Any] | None,
        reason: str | None = None,
    ) -> tuple[str, str | None]:
        if raw_status == "READY":
            return "READY", reason
        if raw_status == "UNAVAILABLE":
            return "UNAVAILABLE", reason
        if preparation:
            return str(preparation["status"]), str(preparation.get("message") or reason or "") or None
        return (
            "PREPARING",
            reason or "Backend maintenance is preparing this data automatically.",
        )

    def _preparation_snapshot(
        self,
        library_asset_ids: list[str],
        instrument_id: str,
        frequency: str,
        *,
        requirement_id: str = "",
        project_id: str = "",
    ) -> dict[str, Any] | None:
        asset_ids = {_clean(value) for value in library_asset_ids if _clean(value)}
        with self.store.connection() as conn:
            rows = conn.execute(
                """SELECT t.task_id, t.task_type, t.status AS task_status, t.input_json, t.output_json, t.error_json,
                          t.created_at AS task_created_at, t.started_at AS task_started_at,
                          t.finished_at AS task_finished_at,
                          COALESCE(t.finished_at, t.started_at, t.created_at) AS task_updated_at,
                          j.job_id, j.status AS job_status, j.start_time, j.effective_end_time,
                          j.cursor_time, j.page_limit, j.pages_completed, j.rows_fetched,
                          j.last_error_json, j.updated_at AS job_updated_at, j.completed_at
                   FROM research_tasks t
                   LEFT JOIN binance_backfill_jobs j ON j.task_id=t.task_id
                   WHERE t.task_type IN (
                       'BINANCE_BARS_BACKFILL',
                       'OPENBB_EQUITY_DAILY_EXPORT',
                       'POLYMARKET_PRICE_HISTORY_EXPORT'
                   )
                   ORDER BY COALESCE(j.updated_at, t.finished_at, t.started_at, t.created_at) DESC
                   LIMIT 1000"""
            ).fetchall()
        target_symbol = _clean(instrument_id).split(":")[-1].upper()
        target_frequency = _lower(frequency)
        for row in rows:
            payload = json.loads(row["input_json"] or "{}")
            maintenance = (
                _clean(payload.get("authorization_mode"))
                == "SYSTEM_REQUIREMENT_MAINTENANCE"
            )
            task_asset_ids = {
                _clean(payload.get("library_asset_id")),
                *{
                    _clean(value)
                    for value in payload.get("library_asset_ids", [])
                    if _clean(value)
                },
            }
            owner_match = bool(asset_ids & task_asset_ids)
            owner_match = owner_match or bool(
                _clean(requirement_id)
                and _clean(payload.get("requirement_id")) == _clean(requirement_id)
                and (
                    not _clean(project_id)
                    or _clean(payload.get("owner_project_id")) == _clean(project_id)
                )
            )
            if not maintenance and not owner_match:
                continue
            task_symbol = _clean(payload.get("symbol") or payload.get("instrument_id")).split(":")[-1].upper()
            if task_symbol != target_symbol or _lower(payload.get("interval")) != target_frequency:
                continue
            task_status = _upper(row["task_status"])
            job_status = _upper(row["job_status"])
            if job_status == "RUNNING" or task_status == "RUNNING":
                status = "PREPARING"
            elif task_status in {"PENDING", "READY"} or job_status in {"READY", "RETRY_WAIT"}:
                status = "QUEUED"
            elif task_status == "FAILED" or job_status == "FAILED":
                status = "FAILED"
            elif task_status == "SUCCEEDED" or job_status == "SUCCEEDED":
                status = "CHECKING"
            else:
                status = "CHECKING"
            progress = (
                self._job_progress(dict(row), payload)
                if _upper(row["task_type"]) == "BINANCE_BARS_BACKFILL"
                else {}
            )
            output = json.loads(row["output_json"] or "{}")
            error = json.loads(row["last_error_json"] or row["error_json"] or "{}")
            message = None
            auto_review = None
            availability = (
                output.get("availability_adjustment")
                if isinstance(output.get("availability_adjustment"), dict)
                else None
            )
            if availability:
                status = "FAILED"
                available_from = _clean(availability.get("available_from"))
                requested_start = _clean(availability.get("requested_start"))
                message = _clean(availability.get("message")) or (
                    f"Automatic review prepared all available {target_symbol} history. "
                    f"Provider data begins {available_from}; the Requirement starts {requested_start}."
                )
                auto_review = {
                    "status": "COMPLETED",
                    "code": _clean(
                        availability.get("code")
                    ) or "DATA_AVAILABLE_AFTER_REQUEST_START",
                    "action": "UPDATE_REQUIREMENT_START",
                    "terminal": True,
                    "requested_start": requested_start,
                    "available_from": available_from,
                }
            if status == "FAILED":
                error_message = _clean(error.get("message"))
                if "HTTP 451" in error_message and "binance" in error_message.lower():
                    message = (
                        "Automatic review detected a regional restriction on the primary Binance endpoint. "
                        "A verified fallback endpoint is available and will be retried automatically."
                    )
                    auto_review = {
                        "status": "COMPLETED",
                        "code": "BINANCE_PRIMARY_REGION_RESTRICTED",
                        "action": "RETRY_WITH_FALLBACK",
                        "can_retry": True,
                        "terminal": False,
                    }
                else:
                    message = error_message or "Provider preparation failed."
            elif job_status == "RETRY_WAIT":
                message = "The provider request will retry automatically."
            started_at = _parse_utc(row["task_started_at"])
            finished_at = _parse_utc(row["task_finished_at"])
            elapsed_seconds = None
            if started_at:
                elapsed_seconds = max(
                    0,
                    round(((finished_at or datetime.now(timezone.utc)) - started_at).total_seconds()),
                )
            phase = {
                "QUEUED": "Waiting for the background data worker",
                "PREPARING": "Downloading and validating provider data",
                "CHECKING": "Verifying coverage and committing the dataset",
                "FAILED": "Data preparation stopped with an error",
            }.get(status, "Checking data maintenance status")
            return {
                "status": status,
                "task_id": str(row["task_id"]),
                "job_id": str(row["job_id"] or ""),
                "updated_at": str(row["job_updated_at"] or row["task_updated_at"] or row["task_created_at"]),
                "message": message,
                "auto_review": auto_review,
                "phase": phase,
                "elapsed_seconds": elapsed_seconds,
                "maintenance_version": int(payload.get("maintenance_version") or 0),
                "rows_fetched": max(
                    int(progress.get("rows_fetched") or 0),
                    int(output.get("row_count") or 0),
                ),
                **progress,
            }
        return None

    @staticmethod
    def _job_progress(row: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        start = _parse_utc(row.get("start_time") or payload.get("start_time"))
        end = _parse_utc(row.get("effective_end_time") or payload.get("end_time"))
        cursor = _parse_utc(row.get("cursor_time"))
        seconds = {
            "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
            "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600,
            "8h": 28800, "12h": 43200, "1d": 86400,
        }.get(_lower(payload.get("interval")))
        page_limit = max(1, int(row.get("page_limit") or payload.get("page_limit") or 1000))
        completed = max(0, int(row.get("pages_completed") or 0))
        total = None
        if start and end and seconds:
            bars = max(1, math.floor((end - start).total_seconds() / seconds) + 1)
            total = max(1, math.ceil(bars / page_limit))
        percent = min(99, round(completed * 100 / total)) if total else None
        if _upper(row.get("job_status")) == "SUCCEEDED":
            percent = 100
            completed = total or completed
        current_end = None
        if cursor and end and seconds:
            current_end = min(end, cursor + timedelta(seconds=seconds * page_limit)).isoformat()
        eta_seconds = None
        started = _parse_utc(row.get("task_started_at"))
        if started and total and completed > 0 and completed < total:
            elapsed = max(1, (datetime.now(timezone.utc) - started).total_seconds())
            eta_seconds = round(elapsed / completed * (total - completed))
        return {
            "percent": percent,
            "completed_partitions": completed,
            "total_partitions": total,
            "current_range": {
                "start": cursor.isoformat() if cursor else None,
                "end": current_end,
            },
            "eta_seconds": eta_seconds,
            "rows_fetched": max(0, int(row.get("rows_fetched") or 0)),
        }

    @staticmethod
    def _merge_preparation_progress(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not rows:
            return None
        completed = sum(int(row.get("completed_partitions") or 0) for row in rows)
        totals = [row.get("total_partitions") for row in rows]
        total = sum(int(value) for value in totals if value is not None) if all(value is not None for value in totals) else None
        percent = min(100, round(completed * 100 / total)) if total else None
        latest = max(rows, key=lambda row: str(row.get("updated_at") or ""))
        return {**latest, "percent": percent, "completed_partitions": completed, "total_partitions": total}

    def _catalog_candidate(self, instrument_id: str, data_type: str, frequency: str) -> dict[str, Any] | None:
        with self.store.connection() as conn:
            row = conn.execute(
                """SELECT c.dataset_id, c.source, c.start_time, c.end_time,
                          c.fields_json, c.schema_version,
                          MIN(p.start_time) AS partition_start, MAX(p.end_time) AS partition_end
                   FROM dataset_catalog c
                   LEFT JOIN dataset_manifests m ON m.dataset_id=c.dataset_id AND m.status='READY'
                   LEFT JOIN dataset_partitions p ON p.manifest_id=m.manifest_id
                   WHERE c.instrument_id=? AND lower(c.data_type)=? AND lower(c.frequency)=?
                     AND c.status='READY'
                   GROUP BY c.dataset_id
                   ORDER BY c.updated_at DESC LIMIT 1""",
                (_clean(instrument_id), _lower(data_type), _lower(frequency)),
            ).fetchone()
        if row is None:
            return None
        return {
            "dataset_id": str(row["dataset_id"]), "source": str(row["source"]),
            "fields": json.loads(row["fields_json"] or "[]"),
            "schema_version": str(row["schema_version"] or ""),
            "range": {
                "start": row["partition_start"] or row["start_time"],
                "end": row["partition_end"] or row["end_time"],
            },
        }

    def archive_project(self, project_id: str) -> dict[str, Any]:
        project = self._require_project(project_id)
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                "UPDATE research_projects SET summary_state='ARCHIVED', archived_at=?, updated_at=?, revision=revision+1 WHERE project_id=?",
                (now, now, _clean(project_id)),
            )
        return {**project, "summary_state": "ARCHIVED", "archived_at": now, "updated_at": now}

    def _item_from_row(self, row: Any) -> dict[str, Any] | None:
        origin = str(row["origin_type"])
        if origin == "RESEARCH":
            with self.store.connection() as conn:
                definition = conn.execute(
                    "SELECT * FROM requirement_definitions WHERE requirement_definition_id=? AND archived_at IS NULL",
                    (row["requirement_definition_id"],),
                ).fetchone()
            if definition is None:
                return None
            spec = json.loads(definition["spec_json"])
            name = str(definition["name"])
            version = int(definition["definition_version"])
            definition_id = str(definition["requirement_definition_id"])
            library_asset_id = str(definition["source_library_asset_id"] or "")
        elif origin == "LIBRARY":
            asset = self.get_library_asset(str(row["library_asset_id"]))
            if asset is None:
                return None
            spec = deepcopy(asset["spec"])
            name = asset["name"]
            version = asset["version"]
            definition_id = ""
            library_asset_id = asset["library_asset_id"]
        else:
            return None
        return {
            "ref_id": str(row["ref_id"]), "project_id": str(row["project_id"]),
            "origin": origin, "name": name, "version": version, "spec": spec,
            "requirement_definition_id": definition_id, "library_asset_id": library_asset_id,
            "source_object_id": str(row["source_object_id"] or ""),
            "overrides": json.loads(row["overrides_json"] or "{}"),
            "editable": origin in {"RESEARCH", "LIBRARY"}, "removable": origin in {"RESEARCH", "LIBRARY"},
        }

    def _derived_factor_items(self, project_id: str, authored: list[dict[str, Any]]) -> list[dict[str, Any]]:
        base = authored[0]["spec"] if authored else default_requirement_spec("Factor Data")
        items = []
        for factor in self._factor_specs(project_id):
            formula = factor.get("formula") if isinstance(factor.get("formula"), dict) else factor
            field = _lower(formula.get("input") or factor.get("input_field"), "close")
            spec = deepcopy(base)
            spec["name"] = f"{factor.get('name') or 'Factor'} · {field}"
            spec["data"]["fields"] = [field]
            spec["data"]["frequency"] = _lower(factor.get("frequency"), spec["data"]["frequency"])
            items.append({
                "ref_id": f"derived_factor:{factor.get('definition_id') or factor.get('name')}",
                "project_id": project_id, "origin": "FACTOR", "name": spec["name"],
                "version": factor.get("version") or "", "spec": spec,
                "requirement_definition_id": "", "library_asset_id": "",
                "source_object_id": factor.get("definition_id") or "",
                "overrides": {}, "editable": False, "removable": False,
            })
        return items

    def _factor_specs(
        self,
        project_id: str,
        *,
        alpha_specs: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            rows = conn.execute(
                """SELECT d.definition_id, d.version, d.spec_json
                   FROM project_definition_refs r
                   JOIN research_definitions d ON d.definition_id=r.definition_id
                   WHERE r.project_id=? AND r.definition_type='FACTOR'
                     AND r.reference_mode='PINNED' AND d.state='VALIDATED'
                   ORDER BY r.slot_key""",
                (_clean(project_id),),
            ).fetchall()
            referenced_ids = {
                _clean(component.get("factor_definition_id"))
                for alpha in (alpha_specs if alpha_specs is not None else self._alpha_specs(project_id))
                for component in alpha.get("components") or []
                if isinstance(component, dict)
                and _clean(component.get("factor_definition_id"))
            }
            direct_ids = {str(row["definition_id"]) for row in rows}
            for definition_id in sorted(referenced_ids - direct_ids):
                dependency = conn.execute(
                    """SELECT definition_id, version, spec_json
                       FROM research_definitions
                       WHERE definition_id=? AND definition_type='FACTOR'
                         AND state='VALIDATED'""",
                    (definition_id,),
                ).fetchone()
                if dependency is None:
                    raise ValueError(
                        f"Validated Alpha references unavailable Factor {definition_id}"
                    )
                rows = [*rows, dependency]
        result = []
        for row in rows:
            spec = json.loads(row["spec_json"])
            spec["definition_id"] = str(row["definition_id"])
            spec["version"] = str(row["version"])
            result.append(spec)
        return result

    def _alpha_specs(self, project_id: str) -> list[dict[str, Any]]:
        with self.store.connection() as conn:
            rows = conn.execute(
                """SELECT d.definition_id, d.version, d.spec_json
                   FROM project_definition_refs r
                   JOIN research_definitions d ON d.definition_id=r.definition_id
                   WHERE r.project_id=? AND r.definition_type='ALPHA'
                     AND r.reference_mode='PINNED' AND d.state='VALIDATED'
                   ORDER BY r.slot_key""",
                (_clean(project_id),),
            ).fetchall()
        result = []
        for row in rows:
            spec = json.loads(row["spec_json"])
            spec["definition_id"] = str(row["definition_id"])
            spec["version"] = str(row["version"])
            result.append(spec)
        return result

    def _instrument_ids(self, spec: dict[str, Any]) -> list[str]:
        target = spec.get("target") or {}
        if target.get("scope") == "SPECIFIC_UNIVERSE":
            from .shared_universe_service import SharedUniverseService

            universe = SharedUniverseService(self.store).get(_clean(target.get("universe_id")))
            if universe is None or universe.get("archived_at"):
                raise ValueError("Target Universe not found")
            return list((universe.get("current_resolution") or {}).get("instrument_ids") or [])
        scope = spec["scope"]
        provider = scope["provider"]
        market = scope["market"]
        asset_type = scope["asset_type"]
        if asset_type == "EQUITY" and market in {"XNAS", "XNYS"}:
            prefix, identity_venue = "equity", market
        elif asset_type == "MACRO":
            prefix, identity_venue = "macro", "FRED"
        elif provider in {"AUTO", "BINANCE"} and market == "SPOT":
            prefix, identity_venue = "crypto_spot", "BINANCE"
        else:
            prefix, identity_venue = asset_type.lower(), provider
        result = []
        for symbol in scope["instruments"]["include"]:
            if ":" in symbol:
                parts = symbol.split(":")
                result.append(":".join([parts[0].lower(), parts[1].upper(), *parts[2:]]))
            else:
                result.append(f"{prefix}:{identity_venue}:{symbol}")
        return result

    def _compiler_context(self, spec: dict[str, Any]) -> dict[str, Any]:
        time_spec, data, advanced = spec["time"], spec["data"], spec["advanced"]
        source_selection_policy = normalize_source_selection_policy({
            "mode": advanced["provider_policy"],
            "allowed_sources": advanced.get("allowed_sources") or [],
            "preferred_sources": advanced.get("preferred_sources") or [],
            "per_instrument": advanced.get("per_instrument_sources") or {},
        })
        return {
            "instrument_ids": self._instrument_ids(spec), "data_type": data["dataset_type"].lower(),
            "frequency": data["frequency"], "history_start": _date_start(time_spec.get("start")) or "1970-01-01T00:00:00+00:00",
            "history_end": None if time_spec.get("end") == "LATEST_AVAILABLE" else _date_end(time_spec.get("end")),
            "adjustment": advanced["adjustment"], "time_semantics": advanced["available_time"],
            "point_in_time_policy": advanced["point_in_time"], "quality_policy": advanced["quality_policy"],
            "source_policy": source_selection_policy["mode"],
            "source_selection_policy": source_selection_policy,
        }

    @staticmethod
    def _availability_override_key(
        instrument_id: str,
        data_type: str,
        frequency: str,
    ) -> str:
        return "|".join(
            (
                _clean(instrument_id).lower(),
                _lower(data_type),
                _lower(frequency),
            )
        )

    def _sync_provider_availability_overrides(self, project_id: str) -> bool:
        """Persist provider history floors as Research-local compiler overrides.

        A provider may truthfully return all of its history while still starting
        after the authored range.  The authored Library Requirement remains
        unchanged; only this Research's effective compiler input is narrowed.
        """
        project_id = _clean(project_id)
        self._require_project(project_id)
        items = self.list_project_items(project_id, include_derived=False)
        by_asset: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            asset_id = _clean(item.get("library_asset_id"))
            if asset_id:
                by_asset.setdefault(asset_id, []).append(item)
        if not by_asset:
            return False

        with self.store.connection() as conn:
            task_rows = conn.execute(
                """SELECT task_id, task_type, input_json, output_json,
                          COALESCE(finished_at, started_at, created_at) AS updated_at
                   FROM research_tasks
                   WHERE project_id=? AND status='SUCCEEDED'
                     AND task_type IN (
                         'BINANCE_BARS_BACKFILL',
                         'OPENBB_EQUITY_DAILY_EXPORT',
                         'POLYMARKET_PRICE_HISTORY_EXPORT'
                     )
                   ORDER BY COALESCE(finished_at, started_at, created_at)""",
                (project_id,),
            ).fetchall()

        pending: dict[str, dict[str, Any]] = {}
        for row in task_rows:
            payload = json.loads(row["input_json"] or "{}")
            output = json.loads(row["output_json"] or "{}")
            adjustment = (
                output.get("availability_adjustment")
                if isinstance(output.get("availability_adjustment"), dict)
                else None
            )
            if not adjustment:
                continue
            asset_id = _clean(payload.get("library_asset_id"))
            instrument_id = _clean(payload.get("instrument_id"))
            frequency = _lower(payload.get("interval"))
            task_type = _upper(row["task_type"])
            data_type = (
                "price_history"
                if task_type == "POLYMARKET_PRICE_HISTORY_EXPORT"
                else "bars"
            )
            provider = {
                "POLYMARKET_PRICE_HISTORY_EXPORT": "POLYMARKET",
                "BINANCE_BARS_BACKFILL": "BINANCE",
                "OPENBB_EQUITY_DAILY_EXPORT": _upper(
                    payload.get("provider"),
                    "OPENBB",
                ),
            }.get(task_type, "")
            available_from = _clean(adjustment.get("available_from"))
            available_time = _parse_utc(available_from)
            if (
                asset_id not in by_asset
                or not instrument_id
                or not frequency
                or available_time is None
            ):
                continue
            for item in by_asset[asset_id]:
                spec = item.get("spec") or {}
                data = spec.get("data") or {}
                if (
                    _lower(data.get("dataset_type")) != data_type
                    or _lower(data.get("frequency")) != frequency
                ):
                    continue
                if instrument_id not in self._instrument_ids(spec):
                    continue
                authored_start = _parse_utc(
                    _date_start((spec.get("time") or {}).get("start"))
                )
                if authored_start and available_time <= authored_start:
                    continue
                key = self._availability_override_key(
                    instrument_id,
                    data_type,
                    frequency,
                )
                overrides = deepcopy(item.get("overrides") or {})
                starts = dict(overrides.get("availability_starts") or {})
                current = dict(starts.get(key) or {})
                current_time = _parse_utc(current.get("available_from"))
                # A later successful observation is authoritative for the
                # common usable range and keeps every instrument satisfiable.
                if current_time and current_time >= available_time:
                    continue
                starts[key] = {
                    "code": _clean(adjustment.get("code"))
                    or "DATA_AVAILABLE_AFTER_REQUEST_START",
                    "requested_start": _clean(adjustment.get("requested_start")),
                    "available_from": available_time.astimezone(
                        timezone.utc
                    ).isoformat(),
                    "provider": provider,
                    "task_id": str(row["task_id"]),
                    "updated_at": str(row["updated_at"] or utc_now()),
                    "automatic": True,
                }
                overrides["availability_starts"] = starts
                pending[item["ref_id"]] = overrides
                item["overrides"] = overrides

        if not pending:
            return False
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            for ref_id, overrides in pending.items():
                conn.execute(
                    """UPDATE project_requirement_items
                       SET overrides_json=?, updated_at=?
                       WHERE project_id=? AND ref_id=?""",
                    (json_dumps(overrides), now, project_id, ref_id),
                )
        return True

    def _compiler_declarations(
        self,
        spec: dict[str, Any],
        item: dict[str, Any],
    ) -> list[dict[str, Any]]:
        context = self._compiler_context(spec)
        availability_starts = dict(
            (item.get("overrides") or {}).get("availability_starts") or {}
        )
        grouped_instruments: dict[str, list[str]] = {}
        authored_start = _parse_utc(context["history_start"])
        for instrument_id in context["instrument_ids"]:
            key = self._availability_override_key(
                instrument_id,
                context["data_type"],
                context["frequency"],
            )
            override = dict(availability_starts.get(key) or {})
            available_start = _parse_utc(override.get("available_from"))
            effective_start = (
                available_start
                if available_start
                and (authored_start is None or available_start > authored_start)
                else authored_start
            )
            start_value = (
                effective_start.astimezone(timezone.utc).isoformat()
                if effective_start
                else context["history_start"]
            )
            grouped_instruments.setdefault(start_value, []).append(instrument_id)

        return [{
            "id": item["ref_id"], "name": spec["name"], "version": str(item.get("version") or ""),
            "instrument_ids": instrument_ids, "data_type": context["data_type"],
            "frequency": context["frequency"], "fields": spec["data"]["fields"],
            "history_mode": spec["time"]["mode"], "history_start": history_start,
            "history_end": context["history_end"], "lookback_value": spec["time"].get("lookback_value"),
            "lookback_unit": spec["time"].get("lookback_unit") or "DAYS",
            "adjustment": context["adjustment"], "time_semantics": context["time_semantics"],
            "point_in_time_policy": context["point_in_time_policy"], "quality_policy": context["quality_policy"],
            "source_policy": context["source_policy"],
            "source_selection_policy": context["source_selection_policy"],
            "dependency_path": [item["origin"], spec["name"]],
        } for history_start, instrument_ids in sorted(grouped_instruments.items())]

    def _compiler_declaration(
        self,
        spec: dict[str, Any],
        item: dict[str, Any],
    ) -> dict[str, Any]:
        """Compatibility helper for callers that expect a single contract."""
        declarations = self._compiler_declarations(spec, item)
        if len(declarations) != 1:
            raise ValueError(
                "Requirement expands to multiple availability-specific contracts"
            )
        return declarations[0]

    def _project_universe(self, project_id: str, universe_id: str = "") -> dict[str, Any]:
        from .shared_universe_service import SharedUniverseService

        bindings = SharedUniverseService(self.store).list_project(project_id)
        requested = _clean(universe_id)
        universe = next((
            item for item in bindings
            if item["universe_id"] == requested
        ), None) if requested else next((item for item in bindings if item["role"] == "PRIMARY"), bindings[0] if bindings else None)
        if universe is None:
            raise ValueError("Universe is not used by this Research")
        return universe

    @staticmethod
    def _membership_changes(previous: list[str], current: list[str]) -> dict[str, list[str]]:
        previous_set, current_set = set(previous), set(current)
        return {
            "added": sorted(current_set - previous_set),
            "removed": sorted(previous_set - current_set),
        }

    def _effective_instrument_ids(self, project_id: str, ref_id: str) -> list[str]:
        ref = ResearchLibraryService(self.store).get_requirement_ref(project_id)
        if ref is None:
            return []
        requirement_set = self.compiler.get(ref["requirement_set_id"])
        if requirement_set is None:
            return []
        requirement_ids = {
            link.requirement_id
            for link in requirement_set.dependency_links
            if link.origin_id == ref_id
        }
        return sorted({
            instrument_id
            for requirement in requirement_set.requirements
            if requirement.requirement_id in requirement_ids
            for instrument_id in requirement.instrument_ids
        })

    def _requirement_compatibility(
        self,
        spec: dict[str, Any],
        instrument_ids: list[str],
    ) -> tuple[bool, list[str]]:
        provider = _upper(spec["scope"].get("provider"))
        market = _upper(spec["scope"].get("market"))
        dataset = _upper(spec["data"].get("dataset_type"))
        frequency = _lower(spec["data"].get("frequency"))
        fields = {_lower(value) for value in spec["data"].get("fields") or []}
        reasons: list[str] = []
        capabilities = ResearchDataCapabilityService(load_web_settings(), base_dir=BASE_DIR).describe()
        if provider == "AUTO":
            if dataset != "BARS":
                reasons.append(f"{dataset} cannot currently be resolved across multiple Providers.")
                return False, reasons
            allowed_fields = {"open", "high", "low", "close", "volume", "turnover", "trade_count"}
            if not fields.issubset(allowed_fields):
                reasons.append("One or more selected Fields are not supported by every eligible source.")
            incompatible = []
            contains_equity = False
            for instrument_id in instrument_ids:
                asset, venue, *_rest = instrument_id.split(":") + ["", ""]
                asset = asset.lower()
                venue = venue.upper()
                supported = (
                    asset == "crypto_spot" and venue == "BINANCE"
                ) or (
                    asset == "equity" and venue in {"XNAS", "XNYS"}
                )
                contains_equity = contains_equity or asset == "equity"
                if not supported:
                    incompatible.append(instrument_id)
            if contains_equity and frequency != "1d":
                reasons.append(
                    "Mixed Crypto and Equity Requirements currently need the common 1d frequency."
                )
            elif not contains_equity and frequency not in {
                _lower(value) for value in capabilities["providers"][0]["markets"][0]["frequencies"]
            }:
                reasons.append(f"{frequency} is not supported by the eligible sources.")
            if incompatible:
                labels = ", ".join(value.split(":")[-1] for value in incompatible[:3])
                reasons.append(
                    f"{labels} {'is' if len(incompatible) == 1 else 'are'} not covered by AUTO source selection."
                )
            return not reasons, reasons
        provider_spec = next((item for item in capabilities["providers"] if item["id"] == provider), None)
        market_spec = next((item for item in (provider_spec or {}).get("markets", []) if item["id"] == market), None)
        if provider_spec is None or market_spec is None:
            reasons.append("The selected Provider and Market are not available.")
            return False, reasons
        if dataset not in {_upper(value) for value in market_spec.get("dataset_types") or []}:
            reasons.append(f"{dataset} is not supported by {provider_spec['label']} {market_spec['label']}.")
        if frequency not in {_lower(value) for value in market_spec.get("frequencies") or []}:
            reasons.append(f"{frequency} is not supported by the selected data source.")
        allowed_fields = {_lower(value) for value in market_spec.get("fields") or []}
        if allowed_fields and not fields.issubset(allowed_fields):
            reasons.append("One or more selected Fields are not supported by the Dataset.")

        incompatible = []
        for instrument_id in instrument_ids:
            asset, venue, *_rest = instrument_id.split(":") + ["", ""]
            asset = asset.lower()
            venue = venue.upper()
            supported = (
                provider in {"AUTO", "BINANCE"} and market == "SPOT"
                and asset == "crypto_spot" and venue == "BINANCE"
            ) or (
                provider == "YFINANCE" and market in {"XNAS", "XNYS"}
                and asset == "equity" and venue == market
            ) or (
                provider == "FINNHUB" and asset == "equity"
            ) or (
                provider == "POLYMARKET" and asset == "polymarket_binary" and venue == "POLYMARKET"
            ) or (
                provider == "FRED" and asset == "macro" and venue == "FRED"
            )
            if not supported:
                incompatible.append(instrument_id)
        if incompatible:
            labels = ", ".join(value.split(":")[-1] for value in incompatible[:3])
            reasons.append(f"{labels} {'is' if len(incompatible) == 1 else 'are'} not supported by the current Provider.")
        return not reasons, reasons

    @staticmethod
    def _recommend_source(instrument_ids: list[str]) -> dict[str, Any]:
        identities = {(value.split(":")[0].lower(), value.split(":")[1].upper()) for value in instrument_ids}
        if identities and all(asset == "crypto_spot" and venue == "BINANCE" for asset, venue in identities):
            return {
                "provider": "BINANCE", "gateway": "DATATUBE", "market": "SPOT",
                "asset_type": "CRYPTO", "dataset_type": "BARS", "frequency": "1h",
                "fields": ["open", "high", "low", "close", "volume"],
                "supports": ["Historical Bars", "Quotes", "Trades"],
            }
        if identities and all(asset == "equity" and venue in {"XNAS", "XNYS"} for asset, venue in identities):
            venues = {venue for _asset, venue in identities}
            return {
                "provider": "YFINANCE", "gateway": "OPENBB", "market": venues.pop() if len(venues) == 1 else "XNAS",
                "asset_type": "EQUITY", "dataset_type": "BARS", "frequency": "1d",
                "fields": ["open", "high", "low", "close", "volume"],
                "supports": ["Historical Bars"],
            }
        if identities and all(asset == "polymarket_binary" and venue == "POLYMARKET" for asset, venue in identities):
            return {
                "provider": "POLYMARKET", "gateway": "DATATUBE", "market": "BINARY",
                "asset_type": "POLYMARKET_BINARY", "dataset_type": "PRICE_HISTORY", "frequency": "1h",
                "fields": ["price"], "supports": ["Price History"],
            }
        if identities and all(asset == "macro" and venue == "FRED" for asset, venue in identities):
            return {
                "provider": "FRED", "gateway": "OPENBB", "market": "MACRO",
                "asset_type": "MACRO", "dataset_type": "SERIES", "frequency": "1d",
                "fields": ["value"], "supports": ["Series Definition"],
            }
        return {
            "provider": "AUTO", "gateway": "DATATUBE", "market": "SPOT",
            "asset_type": "MIXED", "dataset_type": "BARS", "frequency": "1d",
            "fields": ["open", "high", "low", "close", "volume"],
            "supports": ["Per-Instrument Source Selection", "Historical Bars"],
        }

    def _library_asset_from_row(self, row: Any) -> dict[str, Any]:
        spec = json.loads(row["spec_json"])
        with self.store.connection() as conn:
            usage_count = int(conn.execute(
                """SELECT COUNT(*) FROM project_requirement_items r
                   JOIN research_projects p ON p.project_id=r.project_id
                   WHERE r.library_asset_id=? AND p.archived_at IS NULL""",
                (str(row["library_asset_id"]),),
            ).fetchone()[0])
        return {
            "library_asset_id": str(row["library_asset_id"]), "component_type": "REQUIREMENTS",
            "name": str(row["name"]), "version": int(row["asset_version"]),
            "spec": spec, "content": {"spec": spec}, "content_hash": str(row["content_hash"]),
            "source_draft_id": str(row["source_draft_id"]), "published_at": str(row["published_at"]),
            "updated_at": str(row["updated_at"] or row["published_at"]),
            "archived_at": str(row["archived_at"] or ""),
            "usage_count": usage_count,
            "data_status": self.library_data_status(spec, str(row["library_asset_id"])),
        }

    def _require_project(self, project_id: str) -> dict[str, Any]:
        with self.store.connection() as conn:
            row = conn.execute("SELECT * FROM research_projects WHERE project_id=?", (_clean(project_id),)).fetchone()
        if row is None:
            raise ValueError("Research not found")
        if row["archived_at"]:
            raise ValueError("Research is archived")
        return dict(row)
