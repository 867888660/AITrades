from __future__ import annotations

import hashlib
import itertools
import json
import math
import uuid
from copy import deepcopy
from typing import Any, Mapping, Sequence

import yaml

from .instrument_registry import InstrumentRegistry
from .store import DataPlatformStore, json_dumps, utc_now
from .universe_service import UniverseService


UNIVERSE_TYPES = {"instrument_set", "benchmark_set", "composite_set", "multi_leg_set"}
COMPOSITE_OPERATORS = {"union", "intersection", "difference"}
COMBINATION_MODES = {"manual", "cartesian_product", "unordered_combination", "permutation"}
HARD_MAX_COMBINATIONS = 100_000


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json_dumps(dict(value)).encode("utf-8")).hexdigest()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class UniverseConflictError(ValueError):
    code = "UNIVERSE_REVISION_CONFLICT"

    def __init__(self, current_revision_id: str):
        super().__init__("This Universe changed since you opened it. Review the latest changes before saving.")
        self.current_revision_id = current_revision_id


class UniverseSharedImpactError(ValueError):
    code = "UNIVERSE_SHARED_EDIT_CONFIRMATION_REQUIRED"

    def __init__(self, research: Sequence[Mapping[str, Any]]):
        super().__init__("Saving will update this shared Universe in other Research projects.")
        self.research = [dict(item) for item in research]


class UniverseResolutionError(ValueError):
    def __init__(self, code: str, message: str, *, details: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


class SharedUniverseService:
    """Stable shared Universes backed by immutable revisions and legacy snapshots.

    The stable model is additive.  Formal Research execution can continue to
    consume ``universe_snapshot_id`` while the product works with stable
    ``universe_id`` and ``revision_id`` identities.
    """

    def __init__(self, store: DataPlatformStore):
        self.store = store
        self.registry = InstrumentRegistry(store)
        self.legacy = UniverseService(store)
        self._bootstrap_legacy()

    # ------------------------------------------------------------------
    # Canonical definition and Script
    # ------------------------------------------------------------------
    def normalize_definition(
        self, payload: Mapping[str, Any], *, validate_instruments: bool = True
    ) -> dict[str, Any]:
        raw = deepcopy(dict(payload.get("definition") or payload.get("canonical_definition") or payload))
        name = _clean(raw.get("name"))
        if not name:
            raise ValueError("Universe name is required")
        universe_type = _clean(raw.get("type") or raw.get("universe_type") or "instrument_set").lower()
        legacy_type_map = {
            "static_list": "instrument_set",
            "composite": "composite_set",
            "pair": "multi_leg_set",
            "multi_leg": "multi_leg_set",
        }
        universe_type = legacy_type_map.get(universe_type, universe_type)
        if universe_type not in UNIVERSE_TYPES:
            raise ValueError(f"Unsupported Universe type: {universe_type}")

        result = raw
        result["name"] = name
        result["description"] = _clean(raw.get("description"))
        result["tags"] = sorted({_clean(item) for item in raw.get("tags") or [] if _clean(item)})
        result["type"] = universe_type
        result.pop("universe_type", None)

        if universe_type == "instrument_set":
            members = raw.get("members")
            if members is None:
                members = (raw.get("parameters") or {}).get("instrument_ids") or []
            normalized = [self._normalize_instrument(item, validate=validate_instruments) for item in members]
            normalized = sorted(set(normalized))
            if not normalized:
                raise ValueError("Instrument Set requires at least one Instrument")
            result["members"] = normalized
            result.pop("parameters", None)
            result.pop("expression", None)
            result.pop("legs", None)
            result.pop("combination", None)
            result.pop("manual_tuples", None)
        elif universe_type == "benchmark_set":
            benchmark = dict(raw.get("benchmark") or {})
            benchmark_id = _clean(
                benchmark.get("benchmark_id")
                or benchmark.get("symbol")
                or raw.get("benchmark_id")
            ).upper()
            effective_at = _clean(benchmark.get("effective_at") or raw.get("effective_at"))
            if not benchmark_id or not effective_at:
                raise ValueError("Benchmark Set requires benchmark_id and effective_at")
            raw_constituents = list(raw.get("constituents") or [])
            if not raw_constituents:
                raw_constituents = [
                    {"instrument_id": item}
                    for item in raw.get("members") or []
                ]
            constituents: list[dict[str, Any]] = []
            explicit_weights = False
            for item in raw_constituents:
                if isinstance(item, Mapping):
                    instrument_value = item.get("instrument_id") or item.get("id")
                    weight_value = item.get("weight")
                else:
                    instrument_value = item
                    weight_value = None
                instrument_id = self._normalize_instrument(
                    instrument_value, validate=validate_instruments
                )
                weight = float(weight_value) if weight_value not in (None, "") else 0.0
                if not math.isfinite(weight) or weight < 0:
                    raise ValueError("Benchmark constituent weights must be finite and non-negative")
                explicit_weights = explicit_weights or weight_value not in (None, "")
                constituents.append({"instrument_id": instrument_id, "weight": weight})
            if not constituents:
                raise ValueError("Benchmark Set requires at least one constituent")
            if len({item["instrument_id"] for item in constituents}) != len(constituents):
                raise ValueError("Benchmark Set constituents must be unique")
            if not explicit_weights:
                equal_weight = 1.0 / len(constituents)
                for item in constituents:
                    item["weight"] = equal_weight
            else:
                total_weight = sum(item["weight"] for item in constituents)
                if total_weight <= 0:
                    raise ValueError("Benchmark constituent weights must have a positive total")
                for item in constituents:
                    item["weight"] = item["weight"] / total_weight
            constituents.sort(key=lambda item: item["instrument_id"])
            result["benchmark"] = {
                "benchmark_id": benchmark_id,
                "provider": _clean(benchmark.get("provider") or "OPENBB").upper(),
                "effective_at": effective_at,
                "weighting": _clean(
                    benchmark.get("weighting")
                    or ("PROVIDED" if explicit_weights else "EQUAL")
                ).upper(),
                "point_in_time_policy": "AS_OF",
            }
            result["constituents"] = constituents
            result.pop("members", None)
            result.pop("parameters", None)
            result.pop("expression", None)
            result.pop("legs", None)
            result.pop("combination", None)
            result.pop("manual_tuples", None)
        elif universe_type == "composite_set":
            expression = dict(raw.get("expression") or {})
            operator = _clean(expression.get("operator") or "union").lower()
            if operator not in COMPOSITE_OPERATORS:
                raise ValueError(f"Unsupported set operator: {operator}")
            inputs = []
            for item in expression.get("inputs") or []:
                universe_id = _clean(item.get("universe_id") if isinstance(item, Mapping) else item)
                if universe_id:
                    inputs.append({"universe_id": universe_id})
            if len(inputs) < 2:
                raise ValueError("Composite Set requires at least two input Universes")
            result["expression"] = {"operator": operator, "inputs": inputs}
            result.pop("members", None)
            result.pop("legs", None)
            result.pop("combination", None)
            result.pop("manual_tuples", None)
        else:
            combination = dict(raw.get("combination") or {})
            mode = _clean(combination.get("mode") or "unordered_combination").lower()
            if mode not in COMBINATION_MODES:
                raise ValueError(f"Unsupported combination mode: {mode}")
            raw_manual = list(raw.get("manual_tuples") or [])
            inferred_leg_count = len(raw.get("legs") or [])
            if mode == "manual" and not inferred_leg_count and raw_manual:
                inferred_leg_count = len(raw_manual[0] or [])
            if mode == "manual" and inferred_leg_count < 2:
                raise ValueError("Manual Groups require at least two Instruments per Group")
            legs = []
            raw_legs = list(raw.get("legs") or [])
            if mode == "manual" and not raw_legs:
                raw_legs = [{"id": f"leg_{index}", "name": f"Leg {index}"} for index in range(1, inferred_leg_count + 1)]
            for index, item in enumerate(raw_legs, start=1):
                item = dict(item or {})
                source_id = _clean(item.get("source_universe_id"))
                if mode != "manual" and not source_id:
                    raise ValueError(f"Leg {index} requires source_universe_id")
                leg = {
                    "id": _clean(item.get("id")) or f"leg_{index}",
                    "name": _clean(item.get("name")) or f"Leg {index}",
                }
                if source_id:
                    leg["source_universe_id"] = source_id
                legs.append(leg)
            if len(legs) < 2:
                raise ValueError("Pair / Multi-leg Set requires at least two Legs")
            maximum = int(combination.get("max_combinations") or 10_000)
            if maximum < 1 or maximum > HARD_MAX_COMBINATIONS:
                raise ValueError(f"max_combinations must be between 1 and {HARD_MAX_COMBINATIONS}")
            result["legs"] = legs
            result["combination"] = {
                "mode": mode,
                "allow_same_instrument": bool(combination.get("allow_same_instrument", False)),
                "treat_reversed_as_same": bool(combination.get("treat_reversed_as_same", True)),
                "max_combinations": maximum,
            }
            manual = []
            for row in raw_manual:
                values = [self._normalize_instrument(item, validate=validate_instruments) for item in row]
                if len(values) != len(legs):
                    raise ValueError("Every manual tuple must contain one Instrument per Leg")
                if len(set(values)) != len(values):
                    raise ValueError("A Manual Group cannot contain the same Instrument more than once")
                manual.append(values)
            if mode == "manual" and not manual:
                raise ValueError("Manual combination mode requires manual_tuples")
            result["manual_tuples"] = manual
            result.pop("members", None)
            result.pop("expression", None)
        return json.loads(json_dumps(result))

    def render_script(self, definition: Mapping[str, Any]) -> str:
        canonical = self.normalize_definition(definition)
        return yaml.safe_dump(canonical, allow_unicode=True, sort_keys=False, default_flow_style=False)

    def parse_script(self, script: str) -> dict[str, Any]:
        try:
            value = yaml.safe_load(str(script or ""))
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            line = int(getattr(mark, "line", -1)) + 1 if mark is not None else None
            raise UniverseResolutionError(
                "UNIVERSE_SCRIPT_INVALID",
                f"Universe Script is invalid{f' at line {line}' if line else ''}: {exc}",
                details={"line": line},
            ) from exc
        if not isinstance(value, Mapping):
            raise UniverseResolutionError("UNIVERSE_SCRIPT_INVALID", "Universe Script must contain one mapping")
        return self.normalize_definition(value)

    # ------------------------------------------------------------------
    # Library lifecycle
    # ------------------------------------------------------------------
    def list(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        where = "" if include_archived else " WHERE archived_at IS NULL"
        with self.store.connection() as conn:
            ids = [str(row[0]) for row in conn.execute(
                f"SELECT universe_id FROM shared_universes{where} ORDER BY updated_at DESC, name"
            ).fetchall()]
        return [item for universe_id in ids if (item := self.get(universe_id)) is not None]

    def get(self, universe_id: str) -> dict[str, Any] | None:
        with self.store.connection() as conn:
            row = conn.execute(
                """SELECT u.*, r.revision_number, r.canonical_definition_json,
                          r.semantic_hash, r.created_by AS revision_created_by,
                          r.created_at AS revision_created_at, r.parent_revision_id,
                          r.change_summary, r.legacy_definition_id
                   FROM shared_universes u
                   LEFT JOIN shared_universe_revisions r ON r.revision_id=u.current_revision_id
                   WHERE u.universe_id=?""",
                (_clean(universe_id),),
            ).fetchone()
            if row is None:
                return None
            resolution = conn.execute(
                """SELECT * FROM shared_universe_resolutions
                   WHERE universe_id=? AND revision_id=?
                   ORDER BY resolved_at DESC LIMIT 1""",
                (str(row["universe_id"]), str(row["current_revision_id"] or "")),
            ).fetchone()
            active_count = int(conn.execute(
                """SELECT COUNT(*) FROM research_universe_bindings_v2 b
                   JOIN research_projects p ON p.project_id=b.project_id
                   WHERE b.universe_id=? AND b.is_active=1 AND b.removed_at IS NULL
                     AND p.archived_at IS NULL""",
                (str(row["universe_id"]),),
            ).fetchone()[0])
        definition = json.loads(row["canonical_definition_json"] or "{}")
        return {
            "universe_id": str(row["universe_id"]),
            "name": str(row["name"]),
            "description": str(row["description"] or ""),
            "type": str(row["universe_type"]),
            "status": str(row["status"]),
            "owner_id": str(row["owner_id"]),
            "tags": json.loads(row["tags_json"] or "[]"),
            "current_revision_id": str(row["current_revision_id"] or ""),
            "revision_number": int(row["revision_number"] or 0),
            "semantic_hash": str(row["semantic_hash"] or ""),
            "definition": definition,
            "current_resolution": self._resolution_row(resolution),
            "active_research_count": active_count,
            "legacy_library_asset_id": str(row["legacy_library_asset_id"] or ""),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "archived_at": str(row["archived_at"] or ""),
        }

    def create(
        self, payload: Mapping[str, Any], *, created_by: str = "local_user", project_id: str = ""
    ) -> dict[str, Any]:
        definition = self.normalize_definition(payload)
        self._assert_name_available(definition["name"])
        universe_id = _id("universe")
        revision_id = _id("universe_revision")
        resolved = self._resolve_definition(definition, current_universe_id=universe_id)
        legacy_definition_id, legacy_snapshot_id = self._create_legacy_snapshot(
            definition, revision_id, resolved
        )
        now = utc_now()
        resolution_id = _id("universe_resolution")
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                """INSERT INTO shared_universes(
                       universe_id,name,description,universe_type,current_revision_id,status,
                       owner_id,tags_json,created_at,updated_at
                   ) VALUES (?,?,?,?,?,'VALID',?,?,?,?)""",
                (universe_id, definition["name"], definition.get("description", ""), definition["type"],
                 revision_id, _clean(created_by) or "local_user", json_dumps(definition.get("tags") or []), now, now),
            )
            self._insert_revision(conn, revision_id, universe_id, 1, definition, created_by, now, "", "Created Universe", legacy_definition_id)
            self._insert_resolution(conn, resolution_id, universe_id, revision_id, resolved, now, legacy_snapshot_id)
        if _clean(project_id):
            self.bind(project_id=project_id, universe_id=universe_id, role="PRIMARY", replace_primary=True)
        return self.get(universe_id)  # type: ignore[return-value]

    def update(
        self,
        universe_id: str,
        payload: Mapping[str, Any],
        *,
        expected_current_revision_id: str,
        confirm_shared: bool = False,
        current_project_id: str = "",
        created_by: str = "local_user",
        change_summary: str = "",
    ) -> dict[str, Any]:
        current = self.get(universe_id)
        if current is None:
            raise ValueError("Universe not found")
        if current["archived_at"]:
            raise ValueError("Archived Universe cannot be edited")
        if current["current_revision_id"] != _clean(expected_current_revision_id):
            raise UniverseConflictError(current["current_revision_id"])
        usage = self.usage(universe_id)
        affected = [
            item for item in usage["active_research"]
            if str(item["project_id"]) != _clean(current_project_id)
        ]
        if affected and not confirm_shared:
            raise UniverseSharedImpactError(affected)
        definition = self.normalize_definition(payload)
        self._assert_name_available(definition["name"], exclude_universe_id=universe_id)
        resolved = self._resolve_definition(definition, current_universe_id=universe_id)
        revision_id = _id("universe_revision")
        legacy_definition_id, legacy_snapshot_id = self._create_legacy_snapshot(
            definition, revision_id, resolved
        )
        now = utc_now()
        resolution_id = _id("universe_resolution")
        with self.store.transaction(immediate=True) as conn:
            stored = conn.execute(
                "SELECT current_revision_id FROM shared_universes WHERE universe_id=?", (universe_id,)
            ).fetchone()
            if stored is None:
                raise ValueError("Universe not found")
            if str(stored[0] or "") != _clean(expected_current_revision_id):
                raise UniverseConflictError(str(stored[0] or ""))
            revision_number = int(conn.execute(
                "SELECT COALESCE(MAX(revision_number),0)+1 FROM shared_universe_revisions WHERE universe_id=?",
                (universe_id,),
            ).fetchone()[0])
            self._insert_revision(
                conn, revision_id, universe_id, revision_number, definition, created_by, now,
                current["current_revision_id"], change_summary or self._change_summary(current["definition"], definition),
                legacy_definition_id,
            )
            self._insert_resolution(conn, resolution_id, universe_id, revision_id, resolved, now, legacy_snapshot_id)
            conn.execute(
                """UPDATE shared_universes SET name=?,description=?,universe_type=?,current_revision_id=?,
                          status='VALID',tags_json=?,updated_at=? WHERE universe_id=?""",
                (definition["name"], definition.get("description", ""), definition["type"], revision_id,
                 json_dumps(definition.get("tags") or []), now, universe_id),
            )
            projects = conn.execute(
                """SELECT project_id FROM research_universe_bindings_v2
                   WHERE universe_id=? AND is_active=1 AND removed_at IS NULL""", (universe_id,)
            ).fetchall()
            for project in projects:
                project_id = str(project[0])
                self._set_legacy_ref_in_conn(conn, project_id, legacy_snapshot_id, now)
                conn.execute(
                    """UPDATE research_universe_bindings_v2
                       SET requirements_stale_at=?,updated_at=?
                       WHERE project_id=? AND universe_id=? AND is_active=1""",
                    (now, now, project_id, universe_id),
                )
                conn.execute(
                    "UPDATE research_projects SET revision=revision+1,updated_at=? WHERE project_id=?",
                    (now, project_id),
                )
        result = self.get(universe_id)
        result["requirements_invalidated"] = bool(usage["active_research_count"])  # type: ignore[index]
        return result  # type: ignore[return-value]

    def copy(
        self,
        universe_id: str,
        *,
        name: str = "",
        project_id: str = "",
        replace_primary: bool = False,
        definition_override: Mapping[str, Any] | None = None,
        created_by: str = "local_user",
    ) -> dict[str, Any]:
        source = self.get(universe_id)
        if source is None:
            raise ValueError("Universe not found")
        definition = deepcopy(dict(definition_override or source["definition"]))
        definition["name"] = _clean(name) or f"{source['name']} Copy"
        result = self.create(definition, created_by=created_by)
        if _clean(project_id):
            binding = self.bind(
                project_id=project_id, universe_id=result["universe_id"],
                role="PRIMARY" if replace_primary else "REFERENCE",
                replace_primary=replace_primary,
            )
            result["binding"] = binding
        return result

    def preview(self, payload: Mapping[str, Any], *, universe_id: str = "") -> dict[str, Any]:
        definition = self.normalize_definition(payload)
        resolved = self._resolve_definition(definition, current_universe_id=_clean(universe_id))
        return {**resolved, "definition": definition, "semantic_hash": _hash(definition), "persisted": False}

    def history(self, universe_id: str) -> list[dict[str, Any]]:
        if self.get(universe_id) is None:
            raise ValueError("Universe not found")
        with self.store.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM shared_universe_revisions
                   WHERE universe_id=? ORDER BY revision_number DESC""", (universe_id,)
            ).fetchall()
        return [{
            "revision_id": str(row["revision_id"]),
            "universe_id": str(row["universe_id"]),
            "revision_number": int(row["revision_number"]),
            "definition": json.loads(row["canonical_definition_json"] or "{}"),
            "semantic_hash": str(row["semantic_hash"]),
            "created_by": str(row["created_by"]),
            "created_at": str(row["created_at"]),
            "parent_revision_id": str(row["parent_revision_id"] or ""),
            "change_summary": str(row["change_summary"] or ""),
        } for row in rows]

    def restore(
        self, universe_id: str, revision_id: str, *, expected_current_revision_id: str,
        confirm_shared: bool = False, current_project_id: str = "", created_by: str = "local_user",
    ) -> dict[str, Any]:
        revision = next((item for item in self.history(universe_id) if item["revision_id"] == revision_id), None)
        if revision is None:
            raise ValueError("Universe Revision not found")
        return self.update(
            universe_id, revision["definition"], expected_current_revision_id=expected_current_revision_id,
            confirm_shared=confirm_shared, current_project_id=current_project_id, created_by=created_by,
            change_summary=f"Restored from revision {revision['revision_number']}",
        )

    def archive(self, universe_id: str) -> dict[str, Any]:
        usage = self.usage(universe_id)
        if usage["active_research_count"]:
            raise ValueError("Remove this Universe from active Research before archiving it")
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            cursor = conn.execute(
                "UPDATE shared_universes SET status='ARCHIVED',archived_at=?,updated_at=? WHERE universe_id=? AND archived_at IS NULL",
                (now, now, _clean(universe_id)),
            )
            if cursor.rowcount != 1:
                raise ValueError("Active Universe not found")
        return self.get(universe_id)  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Research bindings
    # ------------------------------------------------------------------
    def list_project(self, project_id: str, *, include_removed: bool = False) -> list[dict[str, Any]]:
        where_removed = "" if include_removed else " AND b.is_active=1 AND b.removed_at IS NULL"
        with self.store.connection() as conn:
            rows = conn.execute(
                f"""SELECT b.* FROM research_universe_bindings_v2 b
                    WHERE b.project_id=?{where_removed}
                    ORDER BY CASE b.role WHEN 'PRIMARY' THEN 0 ELSE 1 END,b.created_at""",
                (_clean(project_id),),
            ).fetchall()
        result = []
        for row in rows:
            universe = self.get(str(row["universe_id"]))
            if universe:
                result.append({
                    **universe,
                    "binding_id": str(row["binding_id"]),
                    "project_id": str(row["project_id"]),
                    "role": str(row["role"]),
                    "is_active": bool(row["is_active"]),
                    "binding_created_at": str(row["created_at"]),
                    "binding_updated_at": str(row["updated_at"]),
                    "removed_at": str(row["removed_at"] or ""),
                    "requirements_stale_at": str(row["requirements_stale_at"] or ""),
                })
        return result

    def bind(
        self, *, project_id: str, universe_id: str, role: str = "REFERENCE", replace_primary: bool = False
    ) -> dict[str, Any]:
        universe = self.get(universe_id)
        if universe is None or universe["archived_at"]:
            raise ValueError("Active Universe not found")
        project_id = _clean(project_id)
        role = _clean(role).upper() or "REFERENCE"
        if role not in {"PRIMARY", "REFERENCE"}:
            raise ValueError("Universe binding role must be PRIMARY or REFERENCE")
        resolution = universe.get("current_resolution") or {}
        legacy_snapshot_id = _clean(resolution.get("legacy_snapshot_id"))
        if not legacy_snapshot_id:
            raise ValueError("Universe has no valid Resolution")
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            project = conn.execute(
                "SELECT archived_at FROM research_projects WHERE project_id=?", (project_id,)
            ).fetchone()
            if project is None or project[0]:
                raise ValueError("Active Research not found")
            primary = conn.execute(
                """SELECT binding_id,universe_id FROM research_universe_bindings_v2
                   WHERE project_id=? AND role='PRIMARY' AND is_active=1 AND removed_at IS NULL""",
                (project_id,),
            ).fetchone()
            if replace_primary and primary and str(primary["universe_id"]) != universe_id:
                conn.execute(
                    """UPDATE research_universe_bindings_v2 SET is_active=0,removed_at=?,updated_at=?
                       WHERE binding_id=?""", (now, now, str(primary["binding_id"])),
                )
                role = "PRIMARY"
            elif role == "PRIMARY" and primary and str(primary["universe_id"]) != universe_id:
                conn.execute(
                    "UPDATE research_universe_bindings_v2 SET role='REFERENCE',updated_at=? WHERE binding_id=?",
                    (now, str(primary["binding_id"])),
                )
            elif primary and str(primary["universe_id"]) == universe_id:
                role = "PRIMARY"
            elif primary is None:
                role = "PRIMARY"
            existing = conn.execute(
                "SELECT binding_id FROM research_universe_bindings_v2 WHERE project_id=? AND universe_id=?",
                (project_id, universe_id),
            ).fetchone()
            if existing:
                binding_id = str(existing[0])
                conn.execute(
                    """UPDATE research_universe_bindings_v2 SET role=?,is_active=1,removed_at=NULL,
                              requirements_stale_at=?,updated_at=? WHERE binding_id=?""",
                    (role, now, now, binding_id),
                )
            else:
                binding_id = _id("universe_binding")
                conn.execute(
                    """INSERT INTO research_universe_bindings_v2(
                           binding_id,project_id,universe_id,role,is_active,requirements_stale_at,
                           created_at,updated_at
                       ) VALUES (?,?,?,?,1,?,?,?)""",
                    (binding_id, project_id, universe_id, role, now, now, now),
                )
            self._set_legacy_ref_in_conn(conn, project_id, legacy_snapshot_id, now)
            conn.execute(
                "UPDATE research_projects SET revision=revision+1,updated_at=? WHERE project_id=?", (now, project_id)
            )
        return next(item for item in self.list_project(project_id) if item["binding_id"] == binding_id)

    def remove_binding(self, *, project_id: str, universe_id: str) -> dict[str, Any]:
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            binding = conn.execute(
                """SELECT * FROM research_universe_bindings_v2
                   WHERE project_id=? AND universe_id=? AND is_active=1 AND removed_at IS NULL""",
                (_clean(project_id), _clean(universe_id)),
            ).fetchone()
            if binding is None:
                raise ValueError("Universe is not used by this Research")
            conn.execute(
                "UPDATE research_universe_bindings_v2 SET is_active=0,removed_at=?,updated_at=? WHERE binding_id=?",
                (now, now, str(binding["binding_id"])),
            )
            replacement = conn.execute(
                """SELECT b.binding_id,b.universe_id,r.legacy_snapshot_id
                   FROM research_universe_bindings_v2 b
                   JOIN shared_universes u ON u.universe_id=b.universe_id
                   JOIN shared_universe_resolutions r
                     ON r.universe_id=u.universe_id AND r.revision_id=u.current_revision_id
                   WHERE b.project_id=? AND b.is_active=1 AND b.removed_at IS NULL
                   ORDER BY CASE b.role WHEN 'PRIMARY' THEN 0 ELSE 1 END,b.created_at LIMIT 1""",
                (_clean(project_id),),
            ).fetchone()
            if replacement:
                conn.execute(
                    "UPDATE research_universe_bindings_v2 SET role='PRIMARY',updated_at=? WHERE binding_id=?",
                    (now, str(replacement["binding_id"])),
                )
                self._set_legacy_ref_in_conn(conn, _clean(project_id), str(replacement["legacy_snapshot_id"]), now)
            else:
                conn.execute("DELETE FROM research_universe_refs WHERE project_id=?", (_clean(project_id),))
            conn.execute(
                "UPDATE research_projects SET revision=revision+1,updated_at=? WHERE project_id=?", (now, _clean(project_id))
            )
        return {"removed": True, "universe_id": universe_id, "project_id": project_id}

    def set_primary(self, *, project_id: str, universe_id: str) -> dict[str, Any]:
        bindings = self.list_project(project_id)
        target = next((item for item in bindings if item["universe_id"] == universe_id), None)
        if target is None:
            raise ValueError("Universe is not used by this Research")
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                """UPDATE research_universe_bindings_v2 SET role='REFERENCE',updated_at=?
                   WHERE project_id=? AND is_active=1 AND removed_at IS NULL""", (now, _clean(project_id))
            )
            conn.execute(
                """UPDATE research_universe_bindings_v2 SET role='PRIMARY',updated_at=?
                   WHERE project_id=? AND universe_id=? AND is_active=1""", (now, _clean(project_id), _clean(universe_id))
            )
            self._set_legacy_ref_in_conn(
                conn, _clean(project_id), str(target["current_resolution"]["legacy_snapshot_id"]), now
            )
            conn.execute(
                "UPDATE research_projects SET revision=revision+1,updated_at=? WHERE project_id=?", (now, _clean(project_id))
            )
        return next(item for item in self.list_project(project_id) if item["universe_id"] == universe_id)

    def usage(self, universe_id: str) -> dict[str, Any]:
        universe = self.get(universe_id)
        if universe is None:
            raise ValueError("Universe not found")
        with self.store.connection() as conn:
            active_rows = conn.execute(
                """SELECT p.project_id,p.title,b.role,b.created_at,b.requirements_stale_at
                   FROM research_universe_bindings_v2 b
                   JOIN research_projects p ON p.project_id=b.project_id
                   WHERE b.universe_id=? AND b.is_active=1 AND b.removed_at IS NULL
                     AND p.archived_at IS NULL ORDER BY p.title""", (universe_id,)
            ).fetchall()
            historical_rows = conn.execute(
                """SELECT p.project_id,p.title,b.role,b.created_at,b.removed_at
                   FROM research_universe_bindings_v2 b
                   LEFT JOIN research_projects p ON p.project_id=b.project_id
                   WHERE b.universe_id=? AND (b.is_active=0 OR b.removed_at IS NOT NULL)
                   ORDER BY b.removed_at DESC""", (universe_id,)
            ).fetchall()
            snapshots = [str(row[0]) for row in conn.execute(
                "SELECT legacy_snapshot_id FROM shared_universe_resolutions WHERE universe_id=? AND legacy_snapshot_id IS NOT NULL",
                (universe_id,),
            ).fetchall()]
            runs = []
            if snapshots:
                placeholders = ",".join("?" for _ in snapshots)
                runs = [dict(row) for row in conn.execute(
                    f"""SELECT DISTINCT r.run_id,r.project_id,p.title AS research_title,
                                      r.run_type,r.status,r.created_at,r.finished_at
                         FROM artifact_dependencies d
                         JOIN research_artifacts a ON a.artifact_id=d.child_artifact_id
                         JOIN research_runs_v2 r ON r.run_id=a.created_by_run_id
                         LEFT JOIN research_projects p ON p.project_id=r.project_id
                         WHERE d.parent_type='UNIVERSE_SNAPSHOT'
                           AND d.parent_id IN ({placeholders})
                         ORDER BY r.created_at DESC""", snapshots
                ).fetchall()]
        active = [{
            "project_id": str(row["project_id"]), "title": str(row["title"]),
            "role": str(row["role"]), "added_at": str(row["created_at"]),
            "requirements_stale_at": str(row["requirements_stale_at"] or ""),
        } for row in active_rows]
        historical = [{
            "project_id": str(row["project_id"]), "title": str(row["title"] or "Archived Research"),
            "role": str(row["role"]), "added_at": str(row["created_at"]),
            "removed_at": str(row["removed_at"] or ""),
        } for row in historical_rows]
        by_status: dict[str, int] = {}
        for run in runs:
            status = str(run.get("status") or "UNKNOWN")
            by_status[status] = by_status.get(status, 0) + 1
        return {
            "universe": universe,
            "active_research": active,
            "historical_research": historical,
            "active_research_count": len(active),
            "frozen_runs": runs,
            "frozen_run_count": len(runs),
            "frozen_run_status": by_status,
            "last_used_at": str(runs[0].get("created_at") or "") if runs else "",
        }

    # ------------------------------------------------------------------
    # Resolution internals
    # ------------------------------------------------------------------
    def _resolve_definition(
        self,
        definition: Mapping[str, Any],
        *,
        current_universe_id: str = "",
        stack: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        kind = str(definition["type"])
        if kind == "instrument_set":
            instruments = sorted(set(str(item) for item in definition.get("members") or []))
            return {
                "status": "VALID", "instrument_ids": instruments, "instrument_tuples": [],
                "member_count": len(instruments), "combination_count": 0, "errors": [],
                "estimated_combinations": 0, "instrument_weights": {},
                "metadata": {"universe_type": "instrument_set"},
            }
        if kind == "benchmark_set":
            constituents = list(definition.get("constituents") or [])
            instruments = sorted(str(item["instrument_id"]) for item in constituents)
            return {
                "status": "VALID",
                "instrument_ids": instruments,
                "instrument_tuples": [],
                "instrument_weights": {
                    str(item["instrument_id"]): float(item["weight"])
                    for item in constituents
                },
                "member_count": len(instruments),
                "combination_count": 0,
                "errors": [],
                "estimated_combinations": 0,
                "metadata": {
                    "universe_type": "benchmark_set",
                    "benchmark": dict(definition.get("benchmark") or {}),
                },
            }
        if kind == "composite_set":
            values: list[set[str]] = []
            for item in definition["expression"]["inputs"]:
                child_id = str(item["universe_id"])
                child = self._referenced_definition(child_id, current_universe_id=current_universe_id, stack=stack)
                child_result = self._resolve_definition(
                    child, current_universe_id=current_universe_id, stack=(*stack, child_id)
                )
                if child_result["instrument_tuples"]:
                    raise UniverseResolutionError(
                        "UNIVERSE_TYPE_MISMATCH", "Composite Set inputs must resolve to Instrument Sets"
                    )
                values.append(set(child_result["instrument_ids"]))
            operator = str(definition["expression"]["operator"])
            result = set(values[0])
            for value in values[1:]:
                if operator == "union":
                    result |= value
                elif operator == "intersection":
                    result &= value
                else:
                    result -= value
            instruments = sorted(result)
            return {
                "status": "VALID", "instrument_ids": instruments, "instrument_tuples": [],
                "member_count": len(instruments), "combination_count": 0, "errors": [],
                "estimated_combinations": 0, "instrument_weights": {},
                "metadata": {"universe_type": "composite_set", "operator": operator},
            }

        legs = definition["legs"]
        combination = definition["combination"]
        mode = str(combination["mode"])
        leg_count = len(legs)
        sources: list[list[str]] = []
        if mode != "manual" or any(leg.get("source_universe_id") for leg in legs):
            if not all(leg.get("source_universe_id") for leg in legs):
                raise UniverseResolutionError(
                    "UNIVERSE_MANUAL_SOURCE_MISMATCH",
                    "Manual Groups must either omit every Source Universe or provide one for every Leg",
                )
            for leg in legs:
                child_id = str(leg["source_universe_id"])
                child = self._referenced_definition(child_id, current_universe_id=current_universe_id, stack=stack)
                child_result = self._resolve_definition(child, current_universe_id=current_universe_id, stack=(*stack, child_id))
                if child_result["instrument_tuples"]:
                    raise UniverseResolutionError(
                        "UNIVERSE_TYPE_MISMATCH", "A Multi-leg Leg source must resolve to an Instrument Set"
                    )
                sources.append(list(child_result["instrument_ids"]))
        if mode == "manual":
            estimate = len(definition.get("manual_tuples") or [])
        elif mode == "cartesian_product":
            estimate = math.prod(len(source) for source in sources)
        elif mode == "unordered_combination":
            if len({str(leg["source_universe_id"]) for leg in legs}) != 1:
                raise UniverseResolutionError(
                    "UNIVERSE_COMBINATION_SOURCE_MISMATCH",
                    "Unordered Combination requires every Leg to use the same source Universe",
                )
            source_count = len(sources[0])
            if combination["allow_same_instrument"]:
                estimate = math.comb(source_count + leg_count - 1, leg_count) if source_count else 0
            else:
                estimate = math.comb(source_count, leg_count) if source_count >= leg_count else 0
        else:
            if len({str(leg["source_universe_id"]) for leg in legs}) != 1:
                raise UniverseResolutionError(
                    "UNIVERSE_PERMUTATION_SOURCE_MISMATCH",
                    "Permutation requires every Leg to use the same source Universe",
                )
            source_count = len(sources[0])
            if combination["allow_same_instrument"]:
                estimate = source_count ** leg_count
            else:
                estimate = math.perm(source_count, leg_count) if source_count >= leg_count else 0
        maximum = int(combination["max_combinations"])
        if estimate > maximum:
            raise UniverseResolutionError(
                "UNIVERSE_COMBINATION_LIMIT_EXCEEDED",
                f"Estimated combinations: {estimate}. Maximum allowed: {maximum}.",
                details={"estimated_combinations": estimate, "maximum_combinations": maximum},
            )
        if mode == "manual":
            tuples = [tuple(row) for row in definition.get("manual_tuples") or []]
            if sources:
                for row in tuples:
                    for index, value in enumerate(row):
                        if value not in set(sources[index]):
                            raise UniverseResolutionError(
                                "UNIVERSE_MANUAL_MEMBER_OUT_OF_SCOPE",
                                f"{value} is not a member of the source Universe for {legs[index]['name']}",
                            )
        elif mode == "cartesian_product":
            tuples = list(itertools.product(*sources))
        elif mode == "unordered_combination":
            builder = itertools.combinations_with_replacement if combination["allow_same_instrument"] else itertools.combinations
            tuples = list(builder(sources[0], leg_count))
        else:
            if combination["allow_same_instrument"]:
                tuples = list(itertools.product(sources[0], repeat=leg_count))
            else:
                tuples = list(itertools.permutations(sources[0], leg_count))
        if not combination["allow_same_instrument"]:
            tuples = [row for row in tuples if len(set(row)) == len(row)]
        if combination["treat_reversed_as_same"]:
            unique: dict[tuple[str, ...], tuple[str, ...]] = {}
            for row in tuples:
                key = min(row, tuple(reversed(row)))
                unique.setdefault(key, row)
            tuples = list(unique.values())
        tuples = sorted(set(tuples))
        flattened = sorted({item for row in tuples for item in row})
        return {
            "status": "VALID", "instrument_ids": flattened,
            "instrument_tuples": [list(row) for row in tuples],
            "member_count": len(flattened), "combination_count": len(tuples), "errors": [],
            "estimated_combinations": estimate, "instrument_weights": {},
            "metadata": {
                "universe_type": "multi_leg_set",
                "leg_count": leg_count,
                "combination_mode": mode,
            },
        }

    def _referenced_definition(
        self, universe_id: str, *, current_universe_id: str, stack: tuple[str, ...]
    ) -> dict[str, Any]:
        if universe_id == current_universe_id or universe_id in stack:
            chain = " -> ".join([*stack, universe_id])
            raise UniverseResolutionError(
                "UNIVERSE_CIRCULAR_REFERENCE", f"Circular Universe reference detected: {chain}"
            )
        item = self.get(universe_id)
        if item is None or item["archived_at"]:
            raise UniverseResolutionError("UNIVERSE_REFERENCE_NOT_FOUND", f"Referenced Universe not found: {universe_id}")
        return dict(item["definition"])

    def _normalize_instrument(self, value: Any, *, validate: bool) -> str:
        instrument_id = _clean(value)
        if not instrument_id:
            raise ValueError("Instrument ID cannot be empty")
        if ":" not in instrument_id:
            resolved = self.registry.resolve_alias("binance", instrument_id.upper())
            if resolved:
                instrument_id = resolved
            else:
                raise ValueError(f"Unknown Instrument or alias: {instrument_id}")
        parts = instrument_id.split(":")
        if len(parts) >= 3:
            instrument_id = ":".join([parts[0].lower(), parts[1].upper(), *parts[2:]])
        if validate and self.registry.get(instrument_id) is None:
            raise ValueError(f"Instrument is not registered: {instrument_id}")
        return instrument_id

    def _create_legacy_snapshot(
        self, definition: Mapping[str, Any], revision_id: str, resolved: Mapping[str, Any]
    ) -> tuple[str, str]:
        instrument_ids = list(resolved.get("instrument_ids") or [])
        if not instrument_ids:
            raise UniverseResolutionError("UNIVERSE_EMPTY", "Universe resolved to no Instruments")
        legacy = self.legacy.create_definition(
            name=str(definition["name"]), version=f"shared-{revision_id.rsplit('_', 1)[-1][:16]}",
            universe_type="STATIC_LIST", parameters={"instrument_ids": list(instrument_ids)},
            selection_rule_version="shared-universe-resolver.v1", owner_project_id="", library_scope="GLOBAL",
        )
        snapshot = self.legacy.resolve_snapshot(
            universe_definition_id=legacy.universe_definition_id,
            as_of_time=utc_now(),
            manifests=(),
            selection_inputs_override={
                "shared_universe_revision_id": revision_id,
                "instrument_weights": dict(resolved.get("instrument_weights") or {}),
                "resolution_metadata": dict(resolved.get("metadata") or {}),
            },
        )
        return legacy.universe_definition_id, snapshot.universe_snapshot_id

    # ------------------------------------------------------------------
    # Persistence helpers and legacy migration
    # ------------------------------------------------------------------
    def _bootstrap_legacy(self) -> None:
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            assets = conn.execute(
                "SELECT * FROM research_library_assets WHERE component_type='UNIVERSE' ORDER BY published_at"
            ).fetchall()
            for asset in assets:
                content = json.loads(asset["content_json"] or "{}")
                definition = dict(content.get("definition") or {})
                snapshot = dict(content.get("snapshot") or {})
                legacy_definition_id = _clean(definition.get("universe_definition_id") or asset["source_object_id"])
                existing = conn.execute(
                    "SELECT universe_id FROM shared_universe_revisions WHERE legacy_definition_id=?",
                    (legacy_definition_id,),
                ).fetchone()
                if existing:
                    continue
                canonical = self._legacy_canonical(definition, snapshot)
                universe_id = f"universe_legacy_{hashlib.sha256(str(asset['library_asset_id']).encode()).hexdigest()[:20]}"
                revision_id = f"universe_revision_legacy_{hashlib.sha256(legacy_definition_id.encode()).hexdigest()[:20]}"
                conn.execute(
                    """INSERT OR IGNORE INTO shared_universes(
                           universe_id,name,description,universe_type,current_revision_id,status,owner_id,
                           tags_json,legacy_library_asset_id,created_at,updated_at
                       ) VALUES (?,?,?,?,?,'VALID','migration','[]',?,?,?)""",
                    (universe_id, canonical["name"], canonical.get("description", ""), canonical["type"], revision_id,
                     str(asset["library_asset_id"]), str(asset["published_at"]), now),
                )
                self._insert_revision(
                    conn, revision_id, universe_id, 1, canonical, "migration", str(asset["published_at"]),
                    "", "Migrated published Universe", legacy_definition_id, ignore=True,
                )
                legacy_snapshot_id = _clean(snapshot.get("universe_snapshot_id"))
                if legacy_snapshot_id:
                    resolved = {
                        "status": "VALID",
                        "instrument_ids": list(snapshot.get("actual_instrument_ids") or canonical.get("members") or []),
                        "instrument_tuples": [],
                        "member_count": len(snapshot.get("actual_instrument_ids") or canonical.get("members") or []),
                        "combination_count": 0, "errors": [],
                    }
                    resolution_id = f"universe_resolution_legacy_{hashlib.sha256(legacy_snapshot_id.encode()).hexdigest()[:20]}"
                    self._insert_resolution(
                        conn, resolution_id, universe_id, revision_id, resolved,
                        _clean(snapshot.get("created_at")) or now, legacy_snapshot_id, ignore=True,
                    )

            definitions = conn.execute(
                """SELECT d.*,
                          (SELECT s.universe_snapshot_id FROM universe_snapshots s
                           WHERE s.universe_definition_id=d.universe_definition_id
                           ORDER BY s.as_of_time DESC,s.created_at DESC LIMIT 1) AS snapshot_id
                   FROM universe_definitions d
                   WHERE EXISTS(SELECT 1 FROM universe_snapshots s WHERE s.universe_definition_id=d.universe_definition_id)
                   ORDER BY d.created_at"""
            ).fetchall()
            for definition in definitions:
                legacy_definition_id = str(definition["universe_definition_id"])
                mapped = conn.execute(
                    "SELECT universe_id,revision_id FROM shared_universe_revisions WHERE legacy_definition_id=?",
                    (legacy_definition_id,),
                ).fetchone()
                if mapped:
                    universe_id = str(mapped["universe_id"])
                    revision_id = str(mapped["revision_id"])
                else:
                    snapshot = conn.execute(
                        "SELECT * FROM universe_snapshots WHERE universe_snapshot_id=?", (str(definition["snapshot_id"]),)
                    ).fetchone()
                    canonical = self._legacy_canonical(dict(definition), dict(snapshot) if snapshot else {})
                    universe_id = f"universe_legacy_{hashlib.sha256(legacy_definition_id.encode()).hexdigest()[:20]}"
                    revision_id = f"universe_revision_legacy_{hashlib.sha256(legacy_definition_id.encode()).hexdigest()[:20]}"
                    conn.execute(
                        """INSERT OR IGNORE INTO shared_universes(
                               universe_id,name,description,universe_type,current_revision_id,status,owner_id,
                               tags_json,created_at,updated_at
                           ) VALUES (?,?,?,?,?,'VALID','migration','[]',?,?)""",
                        (universe_id, canonical["name"], "", canonical["type"], revision_id,
                         str(definition["created_at"]), str(definition["created_at"])),
                    )
                    self._insert_revision(
                        conn, revision_id, universe_id, 1, canonical, "migration", str(definition["created_at"]),
                        "", "Migrated Universe", legacy_definition_id, ignore=True,
                    )
                self._ensure_legacy_resolution(conn, universe_id, revision_id, str(definition["snapshot_id"] or ""), now)
                self._ensure_legacy_binding(
                    conn,
                    project_id=_clean(definition["owner_project_id"]),
                    universe_id=universe_id,
                    created_at=str(definition["created_at"]),
                    updated_at=str(definition["created_at"]),
                )

            refs = conn.execute(
                """SELECT r.project_id,r.universe_snapshot_id,r.created_at,r.updated_at,s.universe_definition_id
                   FROM research_universe_refs r
                   JOIN universe_snapshots s ON s.universe_snapshot_id=r.universe_snapshot_id"""
            ).fetchall()
            for ref in refs:
                mapped = conn.execute(
                    "SELECT universe_id FROM shared_universe_revisions WHERE legacy_definition_id=?",
                    (str(ref["universe_definition_id"]),),
                ).fetchone()
                if mapped is None:
                    continue
                universe_id = str(mapped[0])
                self._ensure_legacy_binding(
                    conn,
                    project_id=str(ref["project_id"]),
                    universe_id=universe_id,
                    created_at=str(ref["created_at"]),
                    updated_at=str(ref["updated_at"]),
                )

    @staticmethod
    def _ensure_legacy_binding(
        conn: Any,
        *,
        project_id: str,
        universe_id: str,
        created_at: str,
        updated_at: str,
    ) -> None:
        """Bind legacy Project-owned Universes without resurrecting removed bindings."""
        project_id = _clean(project_id)
        universe_id = _clean(universe_id)
        if not project_id or not universe_id:
            return
        project = conn.execute(
            "SELECT 1 FROM research_projects WHERE project_id=?", (project_id,)
        ).fetchone()
        if project is None:
            return
        existing = conn.execute(
            """SELECT 1 FROM research_universe_bindings_v2
               WHERE project_id=? AND universe_id=?""",
            (project_id, universe_id),
        ).fetchone()
        if existing:
            return
        existing_primary = conn.execute(
            """SELECT 1 FROM research_universe_bindings_v2
               WHERE project_id=? AND role='PRIMARY' AND is_active=1""",
            (project_id,),
        ).fetchone()
        role = "REFERENCE" if existing_primary else "PRIMARY"
        binding_id = f"universe_binding_legacy_{hashlib.sha256((project_id+universe_id).encode()).hexdigest()[:20]}"
        conn.execute(
            """INSERT OR IGNORE INTO research_universe_bindings_v2(
                   binding_id,project_id,universe_id,role,is_active,created_at,updated_at
               ) VALUES (?,?,?,?,1,?,?)""",
            (binding_id, project_id, universe_id, role, created_at, updated_at),
        )

    def _legacy_canonical(self, definition: Mapping[str, Any], snapshot: Mapping[str, Any]) -> dict[str, Any]:
        parameters = json.loads(definition.get("parameters_json") or "{}") if definition.get("parameters_json") else dict(definition.get("parameters") or {})
        actual_raw = snapshot.get("actual_instrument_ids_json") or "[]"
        actual = json.loads(actual_raw) if isinstance(actual_raw, str) else list(snapshot.get("actual_instrument_ids") or [])
        members = parameters.get("instrument_ids") or actual or parameters.get("candidate_instrument_ids") or []
        return self.normalize_definition({
            "name": _clean(definition.get("name")) or "Migrated Universe",
            "description": "",
            "tags": [],
            "type": "instrument_set",
            "members": members,
            "extensions": {
                "legacy_universe_type": _clean(definition.get("universe_type")),
                "legacy_parameters": parameters,
            },
        }, validate_instruments=False)

    def _ensure_legacy_resolution(
        self, conn: Any, universe_id: str, revision_id: str, snapshot_id: str, now: str
    ) -> None:
        if not snapshot_id:
            return
        exists = conn.execute(
            "SELECT 1 FROM shared_universe_resolutions WHERE legacy_snapshot_id=?", (snapshot_id,)
        ).fetchone()
        if exists:
            return
        snapshot = conn.execute(
            "SELECT * FROM universe_snapshots WHERE universe_snapshot_id=?", (snapshot_id,)
        ).fetchone()
        if snapshot is None:
            return
        members = json.loads(snapshot["actual_instrument_ids_json"] or "[]")
        resolved = {
            "status": "VALID", "instrument_ids": members, "instrument_tuples": [],
            "member_count": len(members), "combination_count": 0, "errors": [],
        }
        resolution_id = f"universe_resolution_legacy_{hashlib.sha256(snapshot_id.encode()).hexdigest()[:20]}"
        self._insert_resolution(
            conn, resolution_id, universe_id, revision_id, resolved, str(snapshot["created_at"] or now), snapshot_id, ignore=True
        )

    @staticmethod
    def _insert_revision(
        conn: Any, revision_id: str, universe_id: str, number: int, definition: Mapping[str, Any],
        created_by: str, created_at: str, parent_id: str, summary: str, legacy_definition_id: str,
        *, ignore: bool = False,
    ) -> None:
        verb = "INSERT OR IGNORE" if ignore else "INSERT"
        conn.execute(
            f"""{verb} INTO shared_universe_revisions(
                   revision_id,universe_id,revision_number,canonical_definition_json,semantic_hash,
                   created_by,created_at,parent_revision_id,change_summary,legacy_definition_id
               ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (revision_id, universe_id, number, json_dumps(dict(definition)), _hash(definition),
             _clean(created_by) or "local_user", created_at, parent_id or None, summary, legacy_definition_id or None),
        )

    @staticmethod
    def _insert_resolution(
        conn: Any, resolution_id: str, universe_id: str, revision_id: str,
        resolved: Mapping[str, Any], resolved_at: str, legacy_snapshot_id: str, *, ignore: bool = False,
    ) -> None:
        verb = "INSERT OR IGNORE" if ignore else "INSERT"
        conn.execute(
            f"""{verb} INTO shared_universe_resolutions(
                   resolution_id,universe_id,revision_id,resolved_at,instrument_ids_json,
                   instrument_tuples_json,instrument_weights_json,resolution_metadata_json,
                   member_count,combination_count,status,errors_json,legacy_snapshot_id
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (resolution_id, universe_id, revision_id, resolved_at,
             json_dumps(resolved.get("instrument_ids") or []), json_dumps(resolved.get("instrument_tuples") or []),
             json_dumps(resolved.get("instrument_weights") or {}),
             json_dumps(resolved.get("metadata") or {}),
             int(resolved.get("member_count") or 0), int(resolved.get("combination_count") or 0),
             _clean(resolved.get("status")) or "VALID", json_dumps(resolved.get("errors") or []),
             legacy_snapshot_id or None),
        )

    @staticmethod
    def _resolution_row(row: Any | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "resolution_id": str(row["resolution_id"]),
            "universe_id": str(row["universe_id"]),
            "revision_id": str(row["revision_id"]),
            "resolved_at": str(row["resolved_at"]),
            "instrument_ids": json.loads(row["instrument_ids_json"] or "[]"),
            "instrument_tuples": json.loads(row["instrument_tuples_json"] or "[]"),
            "instrument_weights": json.loads(row["instrument_weights_json"] or "{}")
            if "instrument_weights_json" in row.keys() else {},
            "metadata": json.loads(row["resolution_metadata_json"] or "{}")
            if "resolution_metadata_json" in row.keys() else {},
            "member_count": int(row["member_count"]),
            "combination_count": int(row["combination_count"]),
            "status": str(row["status"]),
            "errors": json.loads(row["errors_json"] or "[]"),
            "legacy_snapshot_id": str(row["legacy_snapshot_id"] or ""),
        }

    def _assert_name_available(self, name: str, *, exclude_universe_id: str = "") -> None:
        with self.store.connection() as conn:
            row = conn.execute(
                """SELECT universe_id FROM shared_universes
                   WHERE lower(name)=lower(?) AND archived_at IS NULL AND universe_id<>?""",
                (_clean(name), _clean(exclude_universe_id)),
            ).fetchone()
        if row:
            raise ValueError("An active Universe with this name already exists; use Copy with a new name")

    @staticmethod
    def _set_legacy_ref_in_conn(conn: Any, project_id: str, snapshot_id: str, now: str) -> None:
        conn.execute(
            """INSERT INTO research_universe_refs(project_id,universe_snapshot_id,library_asset_id,created_at,updated_at)
               VALUES (?,?,NULL,?,?)
               ON CONFLICT(project_id) DO UPDATE SET universe_snapshot_id=excluded.universe_snapshot_id,
                   library_asset_id=NULL,updated_at=excluded.updated_at""",
            (project_id, snapshot_id, now, now),
        )

    @staticmethod
    def _change_summary(before: Mapping[str, Any], after: Mapping[str, Any]) -> str:
        changes = []
        if before.get("name") != after.get("name"):
            changes.append("Renamed Universe")
        if before.get("type") != after.get("type"):
            changes.append("Changed Universe type")
        before_members = set(before.get("members") or [])
        after_members = set(after.get("members") or [])
        if before_members != after_members:
            added = len(after_members - before_members)
            removed = len(before_members - after_members)
            if added:
                changes.append(f"Added {added} Instrument{'s' if added != 1 else ''}")
            if removed:
                changes.append(f"Removed {removed} Instrument{'s' if removed != 1 else ''}")
        if before.get("expression") != after.get("expression"):
            changes.append("Changed set expression")
        if before.get("legs") != after.get("legs") or before.get("combination") != after.get("combination"):
            changes.append("Changed Multi-leg definition")
        return "; ".join(changes) or "Updated Universe metadata"
