from __future__ import annotations

import hashlib
import json
import math
from bisect import bisect_right
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

from .equity_factor_bridge import project_factor_rows
from .models import UniverseDefinition, UniverseSnapshot
from .store import DataPlatformStore, json_dumps, utc_now
from .universe_v2 import UniverseFieldRegistry


SUPPORTED_UNIVERSE_TYPES = {
    "STATIC_LIST",
    "TOP_N_BY_TURNOVER",
    "HISTORICAL_EQUITY_PIT",
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _fingerprint(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(json_dumps(dict(payload)).encode("utf-8")).hexdigest()


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _interval_start(value: Mapping[str, Any]) -> datetime:
    raw = _clean(value.get("eligible_from_time") or value.get("eligible_from"))
    return _parse_time(raw)


def _interval_end_exclusive(value: Mapping[str, Any]) -> datetime:
    raw = _clean(value.get("eligible_to_exclusive"))
    if raw:
        return _parse_time(raw)
    raw = _clean(value.get("eligible_to"))
    parsed = _parse_time(raw)
    return parsed + timedelta(days=1) if len(raw) == 10 else parsed + timedelta(microseconds=1)


class UniverseMembershipIndex:
    """Efficient PIT membership checks for immutable Universe evidence.

    Legacy snapshots contain one listing interval per instrument. Dynamic-field
    Universes add compressed membership segments. Both forms are normalized to
    half-open time intervals so a cross-section does not scan the full market.
    """

    def __init__(self, snapshot: Any):
        self.members = set(getattr(snapshot, "actual_instrument_ids", ()) or ())
        selection_inputs = dict(getattr(snapshot, "selection_inputs", {}) or {})
        self.dynamic = bool(selection_inputs.get("dynamic_membership"))
        raw_segments = dict(selection_inputs.get("membership_segments") or {})
        if not raw_segments:
            raw_segments = {
                instrument_id: [interval]
                for instrument_id, interval in dict(
                    selection_inputs.get("membership_intervals") or {}
                ).items()
            }
        self._segments: dict[str, list[tuple[datetime, datetime]]] = {}
        self._starts: dict[str, list[datetime]] = {}
        events: list[tuple[datetime, int, str]] = []
        for instrument_id, values in raw_segments.items():
            items = values if isinstance(values, list) else [values]
            normalized: list[tuple[datetime, datetime]] = []
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                start = _interval_start(item)
                end = _interval_end_exclusive(item)
                if start >= end:
                    continue
                normalized.append((start, end))
                events.extend(((start, 1, str(instrument_id)), (end, 0, str(instrument_id))))
            normalized.sort()
            if normalized:
                self._segments[str(instrument_id)] = normalized
                self._starts[str(instrument_id)] = [item[0] for item in normalized]
        self._events = sorted(events, key=lambda item: (item[0], item[1], item[2]))
        self._event_index = 0
        self._active: set[str] = set()
        self._last_time: datetime | None = None

    def contains(self, instrument_id: str, as_of_time: str) -> bool:
        if not self.dynamic:
            return not self.members or instrument_id in self.members
        current = _parse_time(as_of_time)
        starts = self._starts.get(str(instrument_id), [])
        index = bisect_right(starts, current) - 1
        if index < 0:
            return False
        return current < self._segments[str(instrument_id)][index][1]

    def active_at(self, as_of_time: str) -> set[str]:
        if not self.dynamic:
            return set(self.members)
        current = _parse_time(as_of_time)
        if self._last_time is not None and current < self._last_time:
            self._event_index = 0
            self._active.clear()
        while self._event_index < len(self._events) and self._events[self._event_index][0] <= current:
            _, event_type, instrument_id = self._events[self._event_index]
            if event_type:
                self._active.add(instrument_id)
            else:
                self._active.discard(instrument_id)
            self._event_index += 1
        self._last_time = current
        return set(self._active)


class UniverseService:
    """Immutable Universe definitions and point-in-time membership snapshots."""

    def __init__(self, store: DataPlatformStore):
        self.store = store

    def create_definition(
        self,
        *,
        name: str,
        version: str,
        universe_type: str,
        parameters: Mapping[str, Any],
        selection_rule_version: str = "universe-engine.v1",
        status: str = "ACTIVE",
        owner_project_id: str = "",
        library_scope: str = "GLOBAL",
    ) -> UniverseDefinition:
        name = _clean(name)
        version = _clean(version)
        universe_type = _clean(universe_type).upper()
        selection_rule_version = _clean(selection_rule_version)
        if not name or not version or not selection_rule_version:
            raise ValueError("universe name, version, and selection_rule_version are required")
        if universe_type not in SUPPORTED_UNIVERSE_TYPES:
            raise ValueError(f"unsupported universe type: {universe_type}")
        normalized_parameters = json.loads(json_dumps(dict(parameters)))
        if universe_type == "STATIC_LIST":
            instruments = tuple(sorted({_clean(item) for item in normalized_parameters.get("instrument_ids", []) if _clean(item)}))
            if not instruments:
                raise ValueError("STATIC_LIST requires instrument_ids")
            normalized_parameters["instrument_ids"] = list(instruments)
        elif universe_type == "TOP_N_BY_TURNOVER":
            candidates = tuple(sorted({_clean(item) for item in normalized_parameters.get("candidate_instrument_ids", []) if _clean(item)}))
            top_n = int(normalized_parameters.get("top_n", 0))
            lookback_bars = int(normalized_parameters.get("lookback_bars", 0))
            if not candidates or top_n < 1 or lookback_bars < 1:
                raise ValueError("TOP_N_BY_TURNOVER requires candidates, positive top_n, and positive lookback_bars")
            normalized_parameters.update({
                "candidate_instrument_ids": list(candidates),
                "top_n": top_n,
                "lookback_bars": lookback_bars,
            })
        else:
            history_start = _clean(normalized_parameters.get("history_start"))[:10]
            history_end = _clean(normalized_parameters.get("history_end"))[:10]
            if not history_start or not history_end or date.fromisoformat(history_start) > date.fromisoformat(history_end):
                raise ValueError("HISTORICAL_EQUITY_PIT requires history_start <= history_end")
            point_in_time_filters = self._normalize_point_in_time_filters(
                normalized_parameters.get("point_in_time_filters") or []
            )
            normalized_parameters.update({
                "source_scope": "equity:CRSP:ALL",
                "history_start": history_start,
                "history_end": history_end,
                "primary_exchanges": sorted({
                    _clean(item).upper()
                    for item in normalized_parameters.get("primary_exchanges", [])
                    if _clean(item)
                }),
                "security_types": sorted({
                    _clean(item).upper()
                    for item in normalized_parameters.get("security_types", ["EQTY"])
                    if _clean(item)
                }),
                "share_types": sorted({
                    _clean(item).upper()
                    for item in normalized_parameters.get("share_types", ["NS", "COM"])
                    if _clean(item)
                }),
                "minimum_listing_age_days": max(
                    0, int(normalized_parameters.get("minimum_listing_age_days") or 0)
                ),
                "excluded_instrument_ids": sorted({
                    _clean(item)
                    for item in normalized_parameters.get("excluded_instrument_ids", [])
                    if _clean(item)
                }),
                "point_in_time_filters": point_in_time_filters,
            })
        material = {
            "name": name,
            "version": version,
            "universe_type": universe_type,
            "parameters": normalized_parameters,
            "selection_rule_version": selection_rule_version,
        }
        fingerprint = _fingerprint(material)
        definition_id = f"universe_def_{fingerprint[:24]}"
        owner_project_id = _clean(owner_project_id)
        library_scope = _clean(library_scope).upper() or "GLOBAL"
        if library_scope not in {"PROJECT", "GLOBAL"}:
            raise ValueError("library_scope must be PROJECT or GLOBAL")
        if library_scope == "PROJECT" and not owner_project_id:
            raise ValueError("PROJECT universes require owner_project_id")
        with self.store.transaction(immediate=True) as conn:
            if owner_project_id and conn.execute(
                "SELECT 1 FROM research_projects WHERE project_id=?", (owner_project_id,)
            ).fetchone() is None:
                raise ValueError("research project not found")
            existing_version = conn.execute(
                "SELECT fingerprint FROM universe_definitions WHERE name = ? AND version = ?",
                (name, version),
            ).fetchone()
            if existing_version and str(existing_version[0]) != fingerprint:
                raise ValueError(f"universe definition {name} {version} is immutable; create a new version")
            conn.execute(
                """
                INSERT OR IGNORE INTO universe_definitions(
                    universe_definition_id, name, version, universe_type, parameters_json,
                    selection_rule_version, fingerprint, status, created_at,
                    owner_project_id, library_scope
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    definition_id, name, version, universe_type, json_dumps(normalized_parameters),
                    selection_rule_version, fingerprint, _clean(status).upper() or "ACTIVE", utc_now(),
                    owner_project_id, library_scope,
                ),
            )
        result = self.get_definition(definition_id)
        if result is None:
            raise RuntimeError("failed to create universe definition")
        return result

    @staticmethod
    def _normalize_point_in_time_filters(values: Any) -> list[dict[str, Any]]:
        if not isinstance(values, list):
            raise ValueError("point_in_time_filters must be a list")
        normalized: list[dict[str, Any]] = []
        for raw in values:
            if not isinstance(raw, Mapping):
                raise ValueError("each point-in-time Universe filter must be an object")
            contract = UniverseFieldRegistry.default().require(raw.get("field"))
            field = contract.field_id
            if not contract.filterable or contract.value_type != "NUMBER" or not contract.formal_pipeline:
                raise ValueError(
                    f"HISTORICAL_EQUITY_PIT field is not bound to the formal pipeline: {field}"
                )
            minimum = raw.get("minimum")
            maximum = raw.get("maximum")
            try:
                minimum = float(minimum) if minimum is not None else None
                maximum = float(maximum) if maximum is not None else None
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{field} bounds must be numeric") from exc
            if minimum is None and maximum is None:
                raise ValueError(f"{field} requires minimum or maximum")
            if minimum is not None and not math.isfinite(minimum):
                raise ValueError(f"{field} minimum must be finite")
            if maximum is not None and not math.isfinite(maximum):
                raise ValueError(f"{field} maximum must be finite")
            if field == "market_cap_usd" and minimum is not None and minimum < 0:
                raise ValueError("market_cap_usd minimum must be non-negative")
            if field == "market_cap_usd" and maximum is not None and maximum < 0:
                raise ValueError("market_cap_usd maximum must be non-negative")
            if minimum is not None and maximum is not None and minimum > maximum:
                raise ValueError(f"{field} minimum must not exceed maximum")
            normalized.append({
                "field": field,
                "minimum": minimum,
                "maximum": maximum,
                "source_data_type": contract.source_data_type,
                "source_fields": list(contract.source_fields),
                "source_unit": contract.source_unit,
                "unit": contract.unit,
                "unit_scale": contract.unit_scale,
                "calculation": contract.calculation,
                "contract_version": contract.contract_version,
                "requirements": [
                    requirement.to_dict()
                    for requirement in contract.requirement_specs()
                ],
                "as_of_policy": "LATEST_AVAILABLE",
                "missing_policy": contract.missing_policy,
            })
        return normalized

    def get_definition(self, definition_id: str) -> UniverseDefinition | None:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM universe_definitions WHERE universe_definition_id = ?",
                (_clean(definition_id),),
            ).fetchone()
        if row is None:
            return None
        return UniverseDefinition(
            universe_definition_id=str(row["universe_definition_id"]),
            name=str(row["name"]),
            version=str(row["version"]),
            universe_type=str(row["universe_type"]),
            parameters=json.loads(row["parameters_json"] or "{}"),
            selection_rule_version=str(row["selection_rule_version"]),
            fingerprint=str(row["fingerprint"]),
            status=str(row["status"]),
            created_at=str(row["created_at"]),
            owner_project_id=str(row["owner_project_id"] or ""),
            library_scope=str(row["library_scope"] or "GLOBAL"),
        )

    def list_definitions(self, *, status: str = "ACTIVE", limit: int = 200) -> list[UniverseDefinition]:
        clauses = []
        params: list[Any] = []
        if _clean(status):
            clauses.append("status = ?")
            params.append(_clean(status).upper())
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(int(limit), 1000)))
        with self.store.connection() as conn:
            rows = conn.execute(
                f"SELECT universe_definition_id FROM universe_definitions{where} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [item for row in rows if (item := self.get_definition(str(row[0]))) is not None]

    def resolve_snapshot(
        self,
        *,
        universe_definition_id: str,
        as_of_time: str,
        manifests: Sequence[Any] = (),
        selection_inputs_override: Mapping[str, Any] | None = None,
    ) -> UniverseSnapshot:
        definition = self.get_definition(universe_definition_id)
        if definition is None:
            raise ValueError(f"universe definition not found: {universe_definition_id}")
        if definition.status != "ACTIVE":
            raise ValueError(f"universe definition is not ACTIVE: {universe_definition_id}")
        as_of_time = _clean(as_of_time)
        _parse_time(as_of_time)
        manifest_ids = tuple(sorted({_clean(getattr(item, "manifest_id", "")) for item in manifests if _clean(getattr(item, "manifest_id", ""))}))

        if definition.universe_type == "STATIC_LIST":
            actual = tuple(definition.parameters["instrument_ids"])
            selection_inputs: dict[str, Any] = {
                "method": "STATIC_LIST",
                "eligible_count": len(actual),
            }
        elif definition.universe_type == "TOP_N_BY_TURNOVER":
            actual, selection_inputs = self._resolve_top_turnover(definition, as_of_time, manifests)
        else:
            actual, selection_inputs = self._resolve_historical_equity(definition)
        if selection_inputs_override:
            selection_inputs = {
                **selection_inputs,
                **json.loads(json_dumps(dict(selection_inputs_override))),
            }

        material = {
            "universe_definition_fingerprint": definition.fingerprint,
            "as_of_time": as_of_time,
            "actual_instrument_ids": list(actual),
            "selection_inputs": selection_inputs,
            "selection_rule_version": definition.selection_rule_version,
            "dataset_manifest_ids": list(manifest_ids),
        }
        fingerprint = _fingerprint(material)
        snapshot_id = f"universe_snapshot_{fingerprint[:24]}"
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO universe_snapshots(
                    universe_snapshot_id, universe_definition_id, as_of_time,
                    actual_instrument_ids_json, selection_inputs_json,
                    selection_rule_version, dataset_manifest_ids_json, fingerprint, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id, definition.universe_definition_id, as_of_time,
                    json_dumps(list(actual)), json_dumps(selection_inputs),
                    definition.selection_rule_version, json_dumps(list(manifest_ids)), fingerprint, utc_now(),
                ),
            )
        result = self.get_snapshot(snapshot_id)
        if result is None:
            raise RuntimeError("failed to create universe snapshot")
        return result

    def _resolve_historical_equity(
        self,
        definition: UniverseDefinition,
    ) -> tuple[tuple[str, ...], dict[str, Any]]:
        from .equity_security_master import EquitySecurityMasterService

        parameters = definition.parameters
        master = EquitySecurityMasterService(self.store)
        rows = master.list_overlapping(
            start=parameters["history_start"],
            end=parameters["history_end"],
            primary_exchanges=parameters.get("primary_exchanges") or (),
            security_types=parameters.get("security_types") or ("EQTY",),
            share_types=parameters.get("share_types") or ("NS", "COM"),
        )
        minimum_age = int(parameters.get("minimum_listing_age_days") or 0)
        period_start = date.fromisoformat(str(parameters["history_start"])[:10])
        period_end = date.fromisoformat(str(parameters["history_end"])[:10])
        intervals: dict[str, dict[str, str]] = {}
        excluded = set(parameters.get("excluded_instrument_ids") or [])
        for row in rows:
            listed_text = _clean(row.get("valid_from"))
            if minimum_age and not listed_text:
                continue
            listed = date.fromisoformat(listed_text[:10]) if listed_text else period_start
            eligible_from = max(period_start, listed + timedelta(days=minimum_age))
            valid_to_text = _clean(row.get("valid_to"))
            eligible_to = min(
                period_end,
                date.fromisoformat(valid_to_text[:10]) if valid_to_text else period_end,
            )
            if eligible_from > eligible_to:
                continue
            instrument_id = master.instrument_id_for_permno(row["permno"])
            if instrument_id in excluded:
                continue
            intervals[instrument_id] = {
                "eligible_from": eligible_from.isoformat(),
                "eligible_to": eligible_to.isoformat(),
                "security_id": str(row["security_id"]),
            }
        actual = tuple(sorted(intervals))
        if not actual:
            raise ValueError("historical equity PIT universe has no eligible securities")
        return actual, {
            "method": "SECURITY_MASTER_VALIDITY_INTERVALS",
            "dynamic_membership": True,
            "source_scope": "equity:CRSP:ALL",
            "history_start": parameters["history_start"],
            "history_end": parameters["history_end"],
            "primary_exchanges": list(parameters.get("primary_exchanges") or []),
            "security_types": list(parameters.get("security_types") or []),
            "share_types": list(parameters.get("share_types") or []),
            "minimum_listing_age_days": minimum_age,
            "excluded_instrument_ids": sorted(excluded),
            "membership_intervals": intervals,
            "point_in_time_filters": list(parameters.get("point_in_time_filters") or []),
            "eligible_count": len(actual),
            "survivorship_policy": "PIT_VALIDITY_INTERVAL",
        }

    @staticmethod
    def data_requirements(definition: UniverseDefinition) -> list[dict[str, Any]]:
        """Return data dependencies declared by a dynamic Universe definition."""
        filters = list(definition.parameters.get("point_in_time_filters") or [])
        if not filters:
            return []
        grouped: dict[tuple[str, str, str, str], set[str]] = {}
        for rule in filters:
            requirements = list(rule.get("requirements") or [])
            if not requirements and _clean(rule.get("source_field")):
                requirements = [{
                    "data_type": rule.get("source_data_type"),
                    "frequency": "1d",
                    "fields": [rule.get("source_field")],
                    "time_semantics": "SOURCE_AVAILABLE_TIME",
                    "point_in_time_policy": "AS_OF",
                }]
            for requirement in requirements:
                key = (
                    _clean(requirement.get("data_type")).lower(),
                    _clean(requirement.get("frequency")).lower(),
                    _clean(requirement.get("time_semantics")) or "SOURCE_AVAILABLE_TIME",
                    _clean(requirement.get("point_in_time_policy")) or "AS_OF",
                )
                grouped.setdefault(key, set()).update(
                    _clean(field)
                    for field in requirement.get("fields") or []
                    if _clean(field)
                )
        result = []
        history_start = date.fromisoformat(
            _clean(definition.parameters.get("history_start"))[:10]
        )
        for index, ((data_type, frequency, time_semantics, pit_policy), fields) in enumerate(
            sorted(grouped.items()), start=1
        ):
            requirement = {
                "id": f"{definition.universe_definition_id}:pit-eligibility:{index}",
                "name": f"PIT Universe eligibility: {data_type}",
                "data_type": data_type,
                "frequency": frequency,
                "fields": sorted(fields),
                "adjustment": "NONE",
                "time_semantics": time_semantics,
                "point_in_time_policy": pit_policy,
                "quality_policy": "STRICT",
                "dependency_path": ["UNIVERSE_DEFINITION", "point_in_time_filters"],
            }
            if data_type in {"fundamentals_pit", "fundamentals_derived"}:
                # TTM fields need four discrete quarters already available at
                # the first research decision. Eighteen calendar months covers
                # the reporting lag without changing the evaluation period.
                requirement["history_start"] = (
                    datetime.combine(
                        history_start - timedelta(days=548),
                        datetime.min.time(),
                        tzinfo=timezone.utc,
                    ).isoformat()
                )
                requirement["warmup_policy"] = "FOUR_DISCRETE_QUARTERS_PIT"
            result.append(requirement)
        return result

    @staticmethod
    def materialize_dynamic_membership(
        snapshot: UniverseSnapshot,
        manifest_inputs: Sequence[Mapping[str, Any]],
    ) -> UniverseSnapshot:
        """Derive compressed PIT membership from frozen valuation/fundamental inputs.

        The stored Snapshot freezes the candidate identity pool and rules. This
        method evaluates those rules only from Manifests already pinned by the
        Frozen Bundle, preserving availability-time semantics without mutating
        the immutable Snapshot record.
        """
        selection_inputs = dict(snapshot.selection_inputs or {})
        rules = list(selection_inputs.get("point_in_time_filters") or [])
        if not rules:
            return snapshot
        required_types = {
            _clean(requirement.get("data_type")).lower()
            for rule in rules
            for requirement in rule.get("requirements") or []
            if _clean(requirement.get("data_type"))
        }
        if not required_types:
            required_types = {"equity_valuation_daily"}
        inputs_by_type: dict[str, list[Mapping[str, Any]]] = {}
        manifest_ids: set[str] = set()
        for item in manifest_inputs:
            data_type = _clean(item.get("data_type")).lower()
            if data_type not in required_types:
                continue
            inputs_by_type.setdefault(data_type, []).append(item)
            if _clean(item.get("manifest_id")):
                manifest_ids.add(_clean(item.get("manifest_id")))
        missing_types = sorted(required_types - set(inputs_by_type))
        if missing_types:
            raise ValueError(
                "dynamic Universe requires frozen Manifests for: " + ", ".join(missing_types)
            )

        rows_by_type_and_instrument: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for data_type, inputs in inputs_by_type.items():
            typed_rows = rows_by_type_and_instrument.setdefault(data_type, {})
            for item in inputs:
                for instrument_id, rows in dict(item.get("rows") or {}).items():
                    if instrument_id not in snapshot.actual_instrument_ids:
                        continue
                    typed_rows.setdefault(str(instrument_id), []).extend(
                        dict(row) for row in rows
                    )
        if not any(rows_by_type_and_instrument.values()):
            raise ValueError("dynamic Universe frozen Manifests have no candidate rows")

        required_logical_fields = {
            field
            for rule in rules
            for field in UniverseFieldRegistry.default().require(rule.get("field")).source_fields
        }
        field_updates: dict[str, list[tuple[datetime, str, float]]] = {}
        valuation_rows = rows_by_type_and_instrument.get("equity_valuation_daily", {})
        if "market_cap_usd" in required_logical_fields:
            for instrument_id, rows in valuation_rows.items():
                for row in rows:
                    available_raw = _clean(row.get("available_time") or row.get("event_time"))
                    try:
                        value = float(row.get("market_cap")) * 1000.0
                    except (TypeError, ValueError):
                        continue
                    if available_raw and math.isfinite(value):
                        field_updates.setdefault(instrument_id, []).append(
                            (_parse_time(available_raw), "market_cap_usd", value)
                        )

        fundamental_fields = required_logical_fields - {"market_cap_usd"}
        for data_type in ("fundamentals_pit", "fundamentals_derived"):
            for instrument_id, rows in rows_by_type_and_instrument.get(data_type, {}).items():
                for field in sorted(fundamental_fields):
                    projected = project_factor_rows("fundamentals", field, rows)
                    for row in projected:
                        available_raw = _clean(row.get("available_time") or row.get("event_time"))
                        try:
                            value = float(row.get(field))
                        except (TypeError, ValueError):
                            continue
                        if available_raw and math.isfinite(value):
                            field_updates.setdefault(instrument_id, []).append(
                                (_parse_time(available_raw), field, value)
                            )

        base_intervals = dict(selection_inputs.get("membership_intervals") or {})
        segments: dict[str, list[dict[str, str]]] = {}
        snapshot_cutoff = _parse_time(snapshot.as_of_time)
        for instrument_id in snapshot.actual_instrument_ids:
            base = dict(base_intervals.get(instrument_id) or {})
            if not base:
                continue
            base_start = _interval_start(base)
            base_end = _interval_end_exclusive(base)
            updates_by_time: dict[datetime, list[tuple[datetime, str, float]]] = {}
            for available, field, value in field_updates.get(instrument_id, []):
                if available > snapshot_cutoff or available >= base_end:
                    continue
                updates_by_time.setdefault(max(base_start, available), []).append(
                    (available, field, value)
                )
            latest: dict[str, float] = {}
            events: list[tuple[datetime, bool]] = []
            for effective_time, updates in sorted(updates_by_time.items()):
                for _, field, value in sorted(updates, key=lambda item: (item[0], item[1])):
                    latest[field] = value
                eligible = True
                for rule in rules:
                    value = UniverseService._calculate_pit_field(
                        _clean(rule.get("field")), latest
                    )
                    if value is None:
                        eligible = False
                        break
                    minimum = rule.get("minimum")
                    maximum = rule.get("maximum")
                    if minimum is not None and value < float(minimum):
                        eligible = False
                    if maximum is not None and value > float(maximum):
                        eligible = False
                events.append((effective_time, eligible))
            current_start: datetime | None = None
            instrument_segments: list[dict[str, str]] = []
            for available, eligible in events:
                if available >= base_end:
                    break
                if eligible and current_start is None:
                    current_start = available
                elif not eligible and current_start is not None:
                    if current_start < available:
                        instrument_segments.append({
                            "eligible_from_time": current_start.isoformat(),
                            "eligible_to_exclusive": available.isoformat(),
                        })
                    current_start = None
            if current_start is not None and current_start < base_end:
                instrument_segments.append({
                    "eligible_from_time": current_start.isoformat(),
                    "eligible_to_exclusive": base_end.isoformat(),
                })
            if instrument_segments:
                segments[instrument_id] = instrument_segments
        if not segments:
            raise ValueError("dynamic PIT Universe has no eligible securities")
        effective_inputs = {
            **selection_inputs,
            "method": "SECURITY_MASTER_AND_PIT_FIELD_RULES",
            "membership_segments": segments,
            "dynamic_membership_source_manifest_ids": sorted(manifest_ids),
            "eligible_ever_count": len(segments),
            "survivorship_policy": "PIT_VALIDITY_AND_AVAILABLE_FIELD_RULES",
        }
        return replace(
            snapshot,
            selection_inputs=effective_inputs,
            dataset_manifest_ids=tuple(sorted(set(snapshot.dataset_manifest_ids) | manifest_ids)),
        )

    @staticmethod
    def _calculate_pit_field(field: str, latest: Mapping[str, float]) -> float | None:
        def value(name: str) -> float | None:
            raw = latest.get(name)
            if raw is None:
                return None
            parsed = float(raw)
            return parsed if math.isfinite(parsed) else None

        if field == "market_cap_usd":
            return value("market_cap_usd")
        market_cap = value("market_cap_usd")
        net_income = value("net_income_ttm")
        equity = value("equity")
        if field == "roe_ttm":
            return net_income / equity if net_income is not None and equity is not None and equity > 0 else None
        if field == "pe_ttm":
            return market_cap / net_income if market_cap is not None and net_income is not None and net_income > 0 else None
        if field == "pb_mrq":
            return market_cap / equity if market_cap is not None and equity is not None and equity > 0 else None
        if field == "fcf_yield_ttm":
            operating_cash_flow = value("operating_cash_flow_ttm")
            capex = value("capex_ttm")
            if market_cap is None or market_cap <= 0 or operating_cash_flow is None or capex is None:
                return None
            return (operating_cash_flow - capex) / market_cap
        return None

    @staticmethod
    def _resolve_top_turnover(
        definition: UniverseDefinition,
        as_of_time: str,
        manifests: Sequence[Any],
    ) -> tuple[tuple[str, ...], dict[str, Any]]:
        candidates = set(definition.parameters["candidate_instrument_ids"])
        lookback = int(definition.parameters["lookback_bars"])
        top_n = int(definition.parameters["top_n"])
        cutoff = _parse_time(as_of_time)
        bars: dict[str, list[dict[str, Any]]] = {}
        for frozen in manifests:
            for instrument_id, rows in frozen.read_bars_by_instrument(as_of=as_of_time).items():
                if instrument_id in candidates:
                    bars.setdefault(instrument_id, []).extend(rows)
        averages: dict[str, float] = {}
        observations: dict[str, int] = {}
        for instrument_id in sorted(candidates):
            eligible_rows = []
            for row in bars.get(instrument_id, []):
                available = _clean(row.get("available_time"))
                if available and _parse_time(available) <= cutoff:
                    eligible_rows.append(row)
            eligible_rows.sort(key=lambda row: _clean(row.get("available_time")))
            window = eligible_rows[-lookback:]
            values = []
            for row in window:
                try:
                    value = float(row.get("turnover"))
                except (TypeError, ValueError):
                    continue
                if math.isfinite(value) and value >= 0:
                    values.append(value)
            if len(values) == lookback:
                averages[instrument_id] = sum(values) / len(values)
                observations[instrument_id] = len(values)
        ranked = sorted(averages, key=lambda item: (-averages[item], item))
        actual = tuple(sorted(ranked[:top_n]))
        selection_inputs = {
            "method": "TOP_N_BY_TURNOVER",
            "lookback_bars": lookback,
            "top_n": top_n,
            "turnover_average": {item: averages[item] for item in sorted(averages)},
            "observations": observations,
            "eligible_count": len(averages),
        }
        return actual, selection_inputs

    def get_snapshot(self, snapshot_id: str) -> UniverseSnapshot | None:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM universe_snapshots WHERE universe_snapshot_id = ?",
                (_clean(snapshot_id),),
            ).fetchone()
        if row is None:
            return None
        return UniverseSnapshot(
            universe_snapshot_id=str(row["universe_snapshot_id"]),
            universe_definition_id=str(row["universe_definition_id"]),
            as_of_time=str(row["as_of_time"]),
            actual_instrument_ids=tuple(json.loads(row["actual_instrument_ids_json"] or "[]")),
            selection_inputs=json.loads(row["selection_inputs_json"] or "{}"),
            selection_rule_version=str(row["selection_rule_version"]),
            dataset_manifest_ids=tuple(json.loads(row["dataset_manifest_ids_json"] or "[]")),
            fingerprint=str(row["fingerprint"]),
            created_at=str(row["created_at"]),
        )

    def list_snapshots(self, universe_definition_id: str) -> list[UniverseSnapshot]:
        with self.store.connection() as conn:
            rows = conn.execute(
                """
                SELECT universe_snapshot_id FROM universe_snapshots
                WHERE universe_definition_id = ? ORDER BY as_of_time DESC, created_at DESC
                """,
                (_clean(universe_definition_id),),
            ).fetchall()
        return [item for row in rows if (item := self.get_snapshot(str(row[0]))) is not None]

    def set_research_ref(
        self, *, project_id: str, universe_snapshot_id: str, library_asset_id: str = ""
    ) -> dict[str, Any]:
        project_id = _clean(project_id)
        snapshot = self.get_snapshot(universe_snapshot_id)
        if snapshot is None:
            raise ValueError("universe snapshot not found")
        definition = self.get_definition(snapshot.universe_definition_id)
        if definition is None:
            raise ValueError("universe definition not found")
        library_asset_id = _clean(library_asset_id)
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            if conn.execute("SELECT 1 FROM research_projects WHERE project_id=?", (project_id,)).fetchone() is None:
                raise ValueError("Research not found")
            if library_asset_id:
                asset = conn.execute(
                    "SELECT component_type,source_object_id FROM research_library_assets WHERE library_asset_id=?",
                    (library_asset_id,),
                ).fetchone()
                if asset is None or str(asset[0]) != "UNIVERSE" or str(asset[1]) != definition.universe_definition_id:
                    raise ValueError("Library asset does not match the selected Universe")
            elif definition.library_scope == "PROJECT" and definition.owner_project_id != project_id:
                raise ValueError("Universe belongs to another Research")
            conn.execute(
                """
                INSERT INTO research_universe_refs(
                    project_id, universe_snapshot_id, library_asset_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    universe_snapshot_id=excluded.universe_snapshot_id,
                    library_asset_id=excluded.library_asset_id,
                    updated_at=excluded.updated_at
                """,
                (project_id, snapshot.universe_snapshot_id, library_asset_id or None, now, now),
            )
        result = self.get_research_ref(project_id)
        if result is None:
            raise RuntimeError("failed to save Research Universe")
        return result

    def get_research_ref(self, project_id: str) -> dict[str, Any] | None:
        with self.store.connection() as conn:
            row = conn.execute(
                """
                SELECT r.project_id, r.universe_snapshot_id, r.created_at, r.updated_at,
                       s.universe_definition_id, s.as_of_time, s.actual_instrument_ids_json,
                       d.name, d.version, d.universe_type, d.status,
                       d.owner_project_id, d.library_scope,
                       a.library_asset_id AS published_asset_id, a.asset_version AS library_version
                FROM research_universe_refs r
                JOIN universe_snapshots s ON s.universe_snapshot_id=r.universe_snapshot_id
                JOIN universe_definitions d ON d.universe_definition_id=s.universe_definition_id
                LEFT JOIN research_library_assets a ON a.library_asset_id=r.library_asset_id
                WHERE r.project_id=?
                """,
                (_clean(project_id),),
            ).fetchone()
        if row is None:
            return None
        return {
            "project_id": str(row["project_id"]),
            "universe_snapshot_id": str(row["universe_snapshot_id"]),
            "universe_definition_id": str(row["universe_definition_id"]),
            "name": str(row["name"]),
            "version": str(row["version"]),
            "universe_type": str(row["universe_type"]),
            "status": str(row["status"]),
            "owner_project_id": str(row["owner_project_id"] or ""),
            "library_scope": str(row["library_scope"] or "GLOBAL"),
            "origin": "LIBRARY" if row["published_asset_id"] else "RESEARCH",
            "library_asset_id": str(row["published_asset_id"] or ""),
            "library_version": int(row["library_version"]) if row["library_version"] is not None else None,
            "as_of_time": str(row["as_of_time"]),
            "actual_instrument_ids": json.loads(row["actual_instrument_ids_json"] or "[]"),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    def remove_research_ref(self, *, project_id: str) -> dict[str, Any]:
        project_id = _clean(project_id)
        with self.store.transaction(immediate=True) as conn:
            if conn.execute("SELECT 1 FROM research_projects WHERE project_id=?", (project_id,)).fetchone() is None:
                raise ValueError("Research not found")
            cursor = conn.execute(
                "DELETE FROM research_universe_refs WHERE project_id=?", (project_id,)
            )
            if cursor.rowcount < 1:
                raise ValueError("Universe is not used by this Research")
        return {"removed": True, "project_id": project_id}

    def usage(self, universe_definition_id: str) -> dict[str, Any]:
        definition = self.get_definition(universe_definition_id)
        if definition is None:
            raise ValueError("universe definition not found")
        with self.store.connection() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT r.project_id, p.title
                FROM research_universe_refs r
                JOIN universe_snapshots s ON s.universe_snapshot_id=r.universe_snapshot_id
                JOIN research_projects p ON p.project_id=r.project_id
                WHERE s.universe_definition_id=?
                ORDER BY p.title
                """,
                (definition.universe_definition_id,),
            ).fetchall()
        research = [{"project_id": str(row["project_id"]), "title": str(row["title"])} for row in rows]
        return {"universe": definition.__dict__, "research": research, "research_count": len(research)}
