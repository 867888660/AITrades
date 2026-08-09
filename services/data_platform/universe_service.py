from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from .models import UniverseDefinition, UniverseSnapshot
from .store import DataPlatformStore, json_dumps, utc_now


SUPPORTED_UNIVERSE_TYPES = {"STATIC_LIST", "TOP_N_BY_TURNOVER"}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _fingerprint(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(json_dumps(dict(payload)).encode("utf-8")).hexdigest()


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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
        else:
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
        else:
            actual, selection_inputs = self._resolve_top_turnover(definition, as_of_time, manifests)
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
