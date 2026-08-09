from __future__ import annotations

import hashlib
from typing import Any, Mapping, Sequence

from .definition_registry import DefinitionRegistry, ResearchDefinition
from .store import DataPlatformStore, json_dumps


ALPHA_FACTOR_CANDIDATE_SCHEMA_VERSION = "alpha_factor_candidates.v1"


def _clean(value: Any) -> str:
    return str(value or "").strip()


class AlphaFactorCandidateResolver:
    """Resolve the exact validated Factors an Alpha in one Research may pin."""

    def __init__(self, store: DataPlatformStore):
        self.store = store
        self.registry = DefinitionRegistry(store)

    def resolve(self, project_id: str) -> dict[str, Any]:
        project_id = _clean(project_id)
        if not project_id:
            raise ValueError("project_id is required")
        with self.store.connection() as conn:
            if conn.execute(
                "SELECT 1 FROM research_projects WHERE project_id=?",
                (project_id,),
            ).fetchone() is None:
                raise ValueError("research project not found")
            rows = conn.execute(
                """
                SELECT d.*, 'RESEARCH' AS candidate_origin
                FROM research_definitions d
                WHERE d.definition_type='FACTOR'
                  AND d.state='VALIDATED'
                  AND d.library_scope='PROJECT'
                  AND d.owner_project_id=?
                UNION ALL
                SELECT d.*,
                       CASE WHEN r.library_asset_id IS NULL
                            THEN 'RESEARCH' ELSE 'LIBRARY' END AS candidate_origin
                FROM project_definition_refs r
                JOIN research_definitions d
                  ON d.definition_id=r.definition_id
                 AND d.version=r.definition_version
                WHERE r.project_id=?
                  AND r.definition_type='FACTOR'
                  AND r.reference_mode='PINNED'
                  AND d.state='VALIDATED'
                ORDER BY name, version, definition_id
                """,
                (project_id, project_id),
            ).fetchall()
        candidates_by_id: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            definition = self.registry._from_row(row)
            key = (definition.definition_id, definition.version)
            origin = str(row["candidate_origin"] or "RESEARCH")
            candidate = self._candidate(definition, origin=origin)
            existing = candidates_by_id.get(key)
            if existing is None or origin == "LIBRARY":
                candidates_by_id[key] = candidate
        factors = sorted(
            candidates_by_id.values(),
            key=lambda item: (
                str(item["name"]).lower(),
                str(item["version"]),
                str(item["definition_id"]),
            ),
        )
        fingerprint = hashlib.sha256(json_dumps([
            {
                "definition_id": item["definition_id"],
                "version": item["version"],
                "spec_hash": item["spec_hash"],
                "origin": item["origin"],
            }
            for item in factors
        ]).encode("utf-8")).hexdigest()
        maximum_components = int(
            DefinitionRegistry.engine_capabilities()["alpha"]
            ["authoring_contract"]["max_components"]
        )
        return {
            "schema_version": ALPHA_FACTOR_CANDIDATE_SCHEMA_VERSION,
            "project_id": project_id,
            "candidate_fingerprint": fingerprint,
            "maximum_components": maximum_components,
            "factors": factors,
            "diagnostics": [],
        }

    def assert_components_accessible(
        self,
        project_id: str,
        components: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        context = self.resolve(project_id)
        candidates = {
            (item["definition_id"], item["version"]): item
            for item in context["factors"]
        }
        selected: list[dict[str, Any]] = []
        definitions: list[ResearchDefinition] = []
        for index, component in enumerate(components):
            definition_id = _clean(component.get("factor_definition_id"))
            version = _clean(
                component.get("factor_version") or component.get("version")
            )
            if not definition_id or not version:
                raise ValueError(
                    "ALPHA_FACTOR_REFERENCE_REQUIRED: "
                    f"Component {index + 1} requires Factor ID and Version."
                )
            definition = self.registry.get(definition_id, version=version)
            if definition is None or definition.definition_type != "FACTOR":
                raise ValueError(
                    "ALPHA_FACTOR_NOT_FOUND: "
                    f"Factor definition not found: {definition_id}@{version}."
                )
            if definition.state != "VALIDATED":
                raise ValueError(
                    "ALPHA_FACTOR_NOT_VALIDATED: "
                    f"Factor is not VALIDATED: {definition_id}@{version}."
                )
            candidate = candidates.get((definition_id, version))
            if candidate is None:
                raise ValueError(
                    "ALPHA_FACTOR_NOT_ACCESSIBLE: "
                    f"Factor is not accessible to this Research: "
                    f"{definition_id}@{version}."
                )
            selected.append(candidate)
            definitions.append(definition)
        return {
            **context,
            "selected_factors": selected,
            "resolved_definitions": definitions,
        }

    @staticmethod
    def _candidate(
        definition: ResearchDefinition,
        *,
        origin: str,
    ) -> dict[str, Any]:
        spec = definition.spec
        return {
            "definition_id": definition.definition_id,
            "version": definition.version,
            "name": definition.name,
            "state": definition.state,
            "spec_hash": definition.spec_hash,
            "engine_version": definition.engine_version,
            "code_hash": definition.code_hash,
            "output_unit": _clean(spec.get("output_unit") or "RATIO"),
            "output_direction": _clean(
                spec.get("output_direction") or "NO_PREDEFINED_DIRECTION"
            ),
            "origin": _clean(origin).upper() or "RESEARCH",
            "accessible": True,
        }
