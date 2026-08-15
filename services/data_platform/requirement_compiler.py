from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

from .factor_alpha import FactorSpec
from .coverage_semantics import range_end_covers_requirement
from .equity_factor_bridge import dataset_contract, field_is_available, physical_data_types
from .models import DataRequirement, RequirementDependencyLink, RequirementSet
from .requirement_service import DataRequirementService, normalize_source_selection_policy
from .store import DataPlatformStore, json_dumps


REQUIREMENT_COMPILER_VERSION = "requirement_compiler_v3"
_FREQUENCY_SECONDS = {
    "1m": 60,
    "3m": 3 * 60,
    "5m": 5 * 60,
    "15m": 15 * 60,
    "30m": 30 * 60,
    "1h": 60 * 60,
    "2h": 2 * 60 * 60,
    "4h": 4 * 60 * 60,
    "6h": 6 * 60 * 60,
    "8h": 8 * 60 * 60,
    "12h": 12 * 60 * 60,
    "1d": 24 * 60 * 60,
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _hash(value: Any) -> str:
    return hashlib.sha256(json_dumps(value).encode("utf-8")).hexdigest()


def _warmup_start(value: Any, frequency: str, observations: int) -> Any:
    text = _clean(value)
    seconds = _FREQUENCY_SECONDS.get(_clean(frequency).lower())
    if not text or not seconds or observations <= 1:
        return value
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("history_start must include a timezone")
    return (
        parsed.astimezone(timezone.utc)
        - timedelta(seconds=seconds * (observations - 1))
    ).isoformat()


def _source_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "to_dict"):
        return dict(value.to_dict())
    raise TypeError(f"unsupported requirement source: {type(value).__name__}")


class RequirementCompiler:
    """Compile research definitions into immutable, attributable data contracts."""

    def __init__(self, store: DataPlatformStore):
        self.store = store
        self.requirements = DataRequirementService(store)

    def compile(
        self,
        *,
        project_id: str,
        factor_specs: Sequence[FactorSpec | Mapping[str, Any]] = (),
        alpha_specs: Sequence[Mapping[str, Any]] = (),
        universe_requirements: Sequence[Mapping[str, Any]] = (),
        evaluation_requirements: Sequence[Mapping[str, Any]] = (),
        backtest_requirements: Sequence[Mapping[str, Any]] = (),
        manual_requirements: Sequence[Mapping[str, Any]] = (),
        context: Mapping[str, Any],
    ) -> RequirementSet:
        project_id = _clean(project_id)
        if not project_id:
            raise ValueError("project_id is required")
        normalized_context = self._normalize_context(context)
        declared: list[dict[str, Any]] = []
        source_specs: list[dict[str, Any]] = []
        alpha_dependencies: dict[str, list[dict[str, Any]]] = {}
        for value in alpha_specs:
            source = _source_dict(value)
            source_specs.append({"origin_type": "ALPHA_SPEC", "spec": source})
            for component in source.get("components") or []:
                if not isinstance(component, Mapping):
                    continue
                factor_definition_id = _clean(component.get("factor_definition_id"))
                if not factor_definition_id:
                    continue
                alpha_dependencies.setdefault(factor_definition_id, []).append({
                    "origin_type": "ALPHA_SPEC",
                    "origin_id": _clean(source.get("definition_id") or source.get("name")),
                    "origin_version": _clean(source.get("version")),
                    "dependency_path": [
                        f"Alpha: {_clean(source.get('name'))} {_clean(source.get('version'))}".strip(),
                        f"Factor: {factor_definition_id}",
                    ],
                })

        for value in factor_specs:
            source = _source_dict(value)
            source_specs.append({"origin_type": "FACTOR_SPEC", "spec": source})
            dependent_alphas = alpha_dependencies.get(
                _clean(source.get("definition_id")), []
            )
            graph_inputs = [
                dict(item)
                for item in source.get("inputs") or []
                if isinstance(item, Mapping)
            ]
            if graph_inputs:
                required_history = dict(source.get("required_history") or {})
                for item in graph_inputs:
                    variable_name = _clean(item.get("variable_name"))
                    field = _clean(item.get("field") or "close")
                    logical_dataset = _clean(item.get("dataset") or "bars").lower()
                    dataset = self._physical_input_dataset(logical_dataset, field)
                    contract = dataset_contract(logical_dataset) or {}
                    observations = int(required_history.get(variable_name) or source.get("minimum_observations") or 1)
                    declaration = self._declaration(
                        normalized_context,
                        fields=[field],
                        lookback_value=observations,
                        override={
                            "data_type": dataset,
                            "frequency": _clean(item.get("frequency") or normalized_context["frequency"]),
                            "time_semantics": (
                                str(contract.get("time_semantics") or (
                                    "EVENT_TIME_AVAILABLE_TIME"
                                    if dataset == "price_history"
                                    else normalized_context["time_semantics"]
                                ))
                            ),
                            "point_in_time_policy": str(
                                contract.get("point_in_time_policy")
                                or normalized_context["point_in_time_policy"]
                            ),
                            "history_start": _warmup_start(
                                normalized_context["history_start"],
                                _clean(item.get("frequency") or normalized_context["frequency"]),
                                observations,
                            ),
                        },
                        origin_type="FACTOR_SPEC",
                        origin_id=f"{_clean(source.get('name'))}:{variable_name}",
                        origin_version=_clean(source.get("version")),
                        dependency_path=[
                            f"Factor: {_clean(source.get('name'))} {_clean(source.get('version'))}".strip(),
                            f"{variable_name} = {field}",
                        ],
                        origin_kind="SYSTEM",
                    )
                    declaration["links"].extend(dependent_alphas)
                    declared.append(declaration)
                continue
            formula = source.get("formula") if isinstance(source.get("formula"), Mapping) else source
            field = _clean(formula.get("input") or source.get("input_field") or "close")
            observations = int(source.get("minimum_observations") or source.get("window") or formula.get("window") or 1)
            frequency = _clean(source.get("frequency") or normalized_context["frequency"])
            declaration = self._declaration(
                normalized_context,
                fields=[field],
                lookback_value=observations,
                override={
                    "frequency": frequency,
                    "history_start": _warmup_start(
                        normalized_context["history_start"],
                        frequency,
                        observations,
                    ),
                },
                origin_type="FACTOR_SPEC",
                origin_id=_clean(source.get("name")),
                origin_version=_clean(source.get("version")),
                dependency_path=[f"Factor: {_clean(source.get('name'))} {_clean(source.get('version'))}".strip(), field],
                origin_kind="SYSTEM",
            )
            declaration["links"].extend(dependent_alphas)
            declared.append(declaration)

        groups = (
            ("UNIVERSE_DEFINITION", universe_requirements),
            ("EVALUATION_SPEC", evaluation_requirements),
            ("BACKTEST_SPEC", backtest_requirements),
            ("MANUAL", manual_requirements),
        )
        for origin_type, values in groups:
            for index, value in enumerate(values):
                source = dict(value)
                source_specs.append({"origin_type": origin_type, "spec": source})
                declared.append(self._declaration(
                    normalized_context,
                    override=source,
                    origin_type=origin_type,
                    origin_id=_clean(source.get("id") or source.get("name") or f"{origin_type.lower()}_{index + 1}"),
                    origin_version=_clean(source.get("version")),
                    dependency_path=list(source.get("dependency_path") or [origin_type, _clean(source.get("data_type") or normalized_context["data_type"])]),
                    origin_kind="MANUAL" if origin_type == "MANUAL" else "SYSTEM",
                ))

        merged = self._merge(declared)
        fingerprint_material = {
            "project_id": project_id,
            "compiler_version": REQUIREMENT_COMPILER_VERSION,
            "context": normalized_context,
            "sources": source_specs,
            "requirements": [item["requirement"] for item in merged],
            "links": [item["links"] for item in merged],
        }
        fingerprint = _hash(fingerprint_material)
        requirement_set_id = f"reqset_{fingerprint[:24]}"
        now = _now()

        materialized: list[tuple[dict[str, Any], DataRequirement]] = []
        for item in merged:
            payload = dict(item["requirement"])
            payload.update({"owner_type": "REQUIREMENT_SET", "owner_id": requirement_set_id})
            materialized.append((item, self.requirements.create(payload)))

        with self.store.transaction(immediate=True) as conn:
            existing = conn.execute(
                "SELECT requirement_set_id, status FROM requirement_sets WHERE fingerprint = ?",
                (fingerprint,),
            ).fetchone()
            if existing:
                existing_id = str(existing["requirement_set_id"])
                existing_result = self.get(existing_id)
                if existing_result is None:
                    raise RuntimeError("existing RequirementSet could not be loaded")
                if str(existing["status"]) == "SUPERSEDED":
                    current = conn.execute(
                        """
                        SELECT requirement_set_id FROM requirement_sets
                        WHERE project_id = ? AND requirement_set_id <> ?
                          AND superseded_by_id IS NULL AND status <> 'SUPERSEDED'
                        ORDER BY set_version DESC LIMIT 1
                        """,
                        (project_id, existing_id),
                    ).fetchone()
                    if current:
                        conn.execute(
                            """
                            UPDATE requirement_sets
                            SET status='SUPERSEDED', superseded_by_id=?
                            WHERE requirement_set_id=?
                            """,
                            (existing_id, str(current["requirement_set_id"])),
                        )
                    conn.execute(
                        """
                        UPDATE requirement_sets
                        SET status='RESOLVED', superseded_by_id=NULL
                        WHERE requirement_set_id=?
                        """,
                        (existing_id,),
                    )
                    return replace(
                        existing_result, status="RESOLVED", superseded_by_id=None
                    )
                return existing_result
            version = int(conn.execute(
                "SELECT COALESCE(MAX(set_version), 0) + 1 FROM requirement_sets WHERE project_id = ?", (project_id,)
            ).fetchone()[0])
            conn.execute(
                """INSERT INTO requirement_sets(
                    requirement_set_id, project_id, set_version, status, compiler_version,
                    source_specs_json, context_json, fingerprint, created_at
                ) VALUES (?, ?, ?, 'RESOLVED', ?, ?, ?, ?, ?)""",
                (requirement_set_id, project_id, version, REQUIREMENT_COMPILER_VERSION,
                 json_dumps(source_specs), json_dumps(normalized_context), fingerprint, now),
            )
            for item, requirement in materialized:
                conn.execute(
                    """INSERT INTO requirement_set_items(
                        requirement_set_id, requirement_id, requirement_json, origin_kind, required, removable
                    ) VALUES (?, ?, ?, ?, ?, ?)""",
                    (requirement_set_id, requirement.requirement_id, json_dumps(item["requirement"]),
                     item["origin_kind"], 1, int(item["origin_kind"] == "MANUAL")),
                )
                for link in item["links"]:
                    conn.execute(
                        """INSERT INTO requirement_dependency_links(
                            requirement_set_id, requirement_id, origin_type, origin_id,
                            origin_version, dependency_path_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (requirement_set_id, requirement.requirement_id, link["origin_type"], link["origin_id"],
                         link["origin_version"], json_dumps(link["dependency_path"]), now),
                    )
            previous = conn.execute(
                """SELECT requirement_set_id FROM requirement_sets
                   WHERE project_id = ? AND requirement_set_id <> ? AND superseded_by_id IS NULL
                   ORDER BY set_version DESC LIMIT 1""", (project_id, requirement_set_id)
            ).fetchone()
            if previous:
                conn.execute(
                    "UPDATE requirement_sets SET status = 'SUPERSEDED', superseded_by_id = ? WHERE requirement_set_id = ?",
                    (requirement_set_id, str(previous[0])),
                )
        return self.get(requirement_set_id)  # type: ignore[return-value]

    def _physical_input_dataset(self, logical_dataset: str, field: str) -> str:
        accepted = physical_data_types(logical_dataset)
        placeholders = ",".join("?" for _ in accepted)
        with self.store.connection() as conn:
            rows = conn.execute(
                f"""SELECT data_type,fields_json,updated_at
                    FROM dataset_catalog
                    WHERE lower(data_type) IN ({placeholders})
                      AND status='READY' AND quality_status!='FAIL'
                    ORDER BY updated_at DESC""",
                accepted,
            ).fetchall()
        for row in rows:
            fields = json.loads(row["fields_json"] or "[]")
            if field_is_available(
                logical_dataset,
                field,
                physical_data_type=str(row["data_type"]),
                catalog_fields=fields,
            ):
                return str(row["data_type"]).lower()
        return accepted[0]

    @staticmethod
    def _normalize_context(context: Mapping[str, Any]) -> dict[str, Any]:
        raw_source_policy = context.get("source_selection_policy")
        if raw_source_policy is None:
            raw_source_policy = context.get("source_policy") or "AUTO"
        source_selection_policy = normalize_source_selection_policy(raw_source_policy)
        result = {
            "universe_snapshot_id": _clean(context.get("universe_snapshot_id")),
            "instrument_ids": sorted({_clean(item) for item in context.get("instrument_ids", []) if _clean(item)}),
            "data_type": _clean(context.get("data_type") or "bars").lower(),
            "frequency": _clean(context.get("frequency")).lower(),
            "history_start": context.get("history_start"),
            "history_end": context.get("history_end"),
            "adjustment": _clean(context.get("adjustment") or "NONE").upper(),
            "time_semantics": _clean(context.get("time_semantics") or "BAR_END_AVAILABLE_TIME").upper(),
            "point_in_time_policy": _clean(context.get("point_in_time_policy") or "AS_OF").upper(),
            "quality_policy": _clean(context.get("quality_policy") or "STRICT").upper(),
            "source_policy": source_selection_policy["mode"],
            "source_selection_policy": source_selection_policy,
        }
        if not result["instrument_ids"] or not result["frequency"] or not result["history_start"]:
            raise ValueError("context requires instrument_ids, frequency, and history_start")
        return result

    @staticmethod
    def _declaration(
        context: Mapping[str, Any], *, fields: Iterable[str] = (), lookback_value: int | None = None,
        override: Mapping[str, Any] | None = None, origin_type: str, origin_id: str,
        origin_version: str, dependency_path: list[str], origin_kind: str,
    ) -> dict[str, Any]:
        override = dict(override or {})
        raw_source_policy = override.get("source_selection_policy")
        if raw_source_policy is None:
            raw_source_policy = override.get("source_policy")
        source_selection_policy = normalize_source_selection_policy(
            raw_source_policy
            if raw_source_policy is not None
            else context["source_selection_policy"]
        )
        requirement = {
            "target_type": "INSTRUMENTS",
            "instrument_ids": sorted({_clean(item) for item in override.get("instrument_ids", context["instrument_ids"]) if _clean(item)}),
            "data_type": _clean(override.get("data_type") or context["data_type"]).lower(),
            "frequency": _clean(override.get("frequency") or context["frequency"]).lower(),
            "fields": sorted({_clean(item) for item in (override.get("fields") or fields) if _clean(item)}),
            "history_mode": _clean(override.get("history_mode") or "FIXED").upper(),
            "history_start": override.get("history_start") or context["history_start"],
            "history_end": override.get("history_end") if "history_end" in override else context["history_end"],
            "lookback_value": int(override.get("lookback_value") or lookback_value or 0) or None,
            "lookback_unit": _clean(override.get("lookback_unit") or "OBSERVATIONS").upper(),
            "refresh_mode": _clean(override.get("refresh_mode") or "MANUAL").upper(),
            "refresh_interval_seconds": override.get("refresh_interval_seconds"),
            "auto_backfill": bool(override.get("auto_backfill", False)),
            "usage_level": _clean(override.get("usage_level") or "RESEARCH").upper(),
            "priority": int(override.get("priority", 50)),
            "adjustment": _clean(override.get("adjustment") or context["adjustment"]).upper(),
            "time_semantics": _clean(override.get("time_semantics") or context["time_semantics"]).upper(),
            "point_in_time_policy": _clean(override.get("point_in_time_policy") or context["point_in_time_policy"]).upper(),
            "quality_policy": _clean(override.get("quality_policy") or context["quality_policy"]).upper(),
            "source_policy": source_selection_policy["mode"],
            "source_selection_policy": source_selection_policy,
        }
        if not requirement["fields"]:
            raise ValueError(f"{origin_type} requirement must declare fields")
        return {"requirement": requirement, "origin_kind": origin_kind, "links": [{
            "origin_type": origin_type, "origin_id": origin_id, "origin_version": origin_version,
            "dependency_path": dependency_path,
        }]}

    @staticmethod
    def _merge(declared: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        semantic_fields = (
            "target_type", "instrument_ids", "data_type", "frequency",
            "lookback_unit", "refresh_mode", "refresh_interval_seconds", "auto_backfill", "usage_level", "priority",
            "adjustment", "time_semantics", "point_in_time_policy", "quality_policy",
            "source_policy", "source_selection_policy",
        )
        for item in declared:
            req = item["requirement"]
            key = _hash({field: req[field] for field in semantic_fields})
            if key not in merged:
                merged[key] = {"requirement": dict(req), "origin_kind": item["origin_kind"], "links": list(item["links"])}
                continue
            current = merged[key]
            current["requirement"]["fields"] = sorted(set(current["requirement"]["fields"]) | set(req["fields"]))
            starts = [value for value in (current["requirement"].get("history_start"), req.get("history_start")) if value]
            current["requirement"]["history_start"] = min(starts) if starts else None
            ends = (current["requirement"].get("history_end"), req.get("history_end"))
            current["requirement"]["history_end"] = None if None in ends else max(ends)
            modes = {str(current["requirement"].get("history_mode") or "FIXED"), str(req.get("history_mode") or "FIXED")}
            current["requirement"]["history_mode"] = (
                "LIVE" if "LIVE" in modes else
                "LATEST_AVAILABLE" if "LATEST_AVAILABLE" in modes else
                "FIXED_START_LATEST_END" if "FIXED_START_LATEST_END" in modes else
                "ROLLING" if "ROLLING" in modes else "FIXED"
            )
            current["requirement"]["lookback_value"] = max(
                int(current["requirement"].get("lookback_value") or 0), int(req.get("lookback_value") or 0)
            ) or None
            current["links"].extend(item["links"])
            if item["origin_kind"] == "SYSTEM":
                current["origin_kind"] = "SYSTEM"
        return sorted(merged.values(), key=lambda item: json_dumps(item["requirement"]))

    def get(self, requirement_set_id: str) -> RequirementSet | None:
        with self.store.connection() as conn:
            row = conn.execute("SELECT * FROM requirement_sets WHERE requirement_set_id = ?", (_clean(requirement_set_id),)).fetchone()
            if row is None:
                return None
            item_rows = conn.execute(
                "SELECT requirement_id FROM requirement_set_items WHERE requirement_set_id = ? ORDER BY requirement_id",
                (_clean(requirement_set_id),),
            ).fetchall()
            link_rows = conn.execute(
                "SELECT * FROM requirement_dependency_links WHERE requirement_set_id = ? ORDER BY requirement_id, origin_type, origin_id",
                (_clean(requirement_set_id),),
            ).fetchall()
        return RequirementSet(
            requirement_set_id=str(row["requirement_set_id"]), project_id=str(row["project_id"]),
            version=int(row["set_version"]), status=str(row["status"]), compiler_version=str(row["compiler_version"]),
            fingerprint=str(row["fingerprint"]), source_specs=tuple(json.loads(row["source_specs_json"])),
            context=json.loads(row["context_json"]),
            requirements=tuple(self.requirements.get(str(item[0])) for item in item_rows),  # type: ignore[arg-type]
            dependency_links=tuple(RequirementDependencyLink(
                requirement_id=str(item["requirement_id"]), origin_type=str(item["origin_type"]),
                origin_id=str(item["origin_id"]), origin_version=str(item["origin_version"]),
                dependency_path=tuple(json.loads(item["dependency_path_json"])),
            ) for item in link_rows), created_at=str(row["created_at"]), superseded_by_id=row["superseded_by_id"],
        )

    def list(self, *, project_id: str = "") -> list[RequirementSet]:
        with self.store.connection() as conn:
            if _clean(project_id):
                rows = conn.execute(
                    "SELECT requirement_set_id FROM requirement_sets WHERE project_id = ? ORDER BY set_version DESC",
                    (_clean(project_id),),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT requirement_set_id FROM requirement_sets
                       ORDER BY created_at DESC, project_id ASC, set_version DESC"""
                ).fetchall()
        return [self.get(str(row[0])) for row in rows]  # type: ignore[list-item]

    def coverage(self, requirement_set_id: str) -> dict[str, Any]:
        requirement_set = self.get(requirement_set_id)
        if requirement_set is None:
            raise KeyError("requirement set not found")
        checks: list[dict[str, Any]] = []
        with self.store.connection() as conn:
            for requirement in requirement_set.requirements:
                for instrument_id in requirement.instrument_ids:
                    row = conn.execute(
                        """SELECT * FROM dataset_catalog
                           WHERE instrument_id IN (?, 'equity:CRSP:ALL')
                             AND data_type = ? AND frequency = ?
                           ORDER BY updated_at DESC LIMIT 1""",
                        (instrument_id, requirement.data_type, requirement.frequency),
                    ).fetchone()
                    reasons: list[str] = []
                    if row is None:
                        reasons.append("DATASET_MISSING")
                    else:
                        if str(row["status"]) != "READY" or str(row["quality_status"]) == "FAIL":
                            reasons.append("DATASET_NOT_READY")
                        if not row["start_time"] or str(row["start_time"]) > str(requirement.history_start):
                            reasons.append("START_NOT_COVERED")
                        if requirement.history_end and not range_end_covers_requirement(
                            actual_end=row["end_time"],
                            required_end=requirement.history_end,
                            data_type=requirement.data_type,
                            frequency=requirement.frequency,
                            source=row["source"],
                            schema_version=row["schema_version"],
                            time_semantics=row["time_semantics"],
                        ):
                            reasons.append("END_NOT_COVERED")
                        if int(row["gap_count"] or 0) > 0:
                            reasons.append("KNOWN_GAPS")
                    checks.append({
                        "requirement_id": requirement.requirement_id, "instrument_id": instrument_id,
                        "dataset_id": str(row["dataset_id"]) if row else None,
                        "manifest_id": str(row["latest_manifest_id"]) if row and row["latest_manifest_id"] else None,
                        "satisfied": not reasons, "reasons": reasons,
                    })
        return {
            "requirement_set_id": requirement_set_id,
            "status": "SATISFIED" if checks and all(item["satisfied"] for item in checks) else "GAPS_FOUND",
            "checks": checks,
        }
