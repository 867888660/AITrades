from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict
from typing import Any

from .definition_registry import DefinitionRegistry
from .requirement_compiler import RequirementCompiler
from .store import DataPlatformStore, json_dumps, utc_now
from .universe_service import UniverseService


def _clean(value: Any) -> str:
    return str(value or "").strip()


class ResearchLibraryService:
    """Publish immutable Library assets without changing their Research sources."""

    def __init__(self, store: DataPlatformStore):
        self.store = store
        self.definitions = DefinitionRegistry(store)
        self.universes = UniverseService(store)
        self.requirements = RequirementCompiler(store)

    def list(self, *, component_type: str = "", include_archived: bool = False) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if _clean(component_type):
            clauses.append("component_type=?")
            params.append(_clean(component_type).upper())
        if not include_archived:
            clauses.append("archived_at IS NULL")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.store.connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM research_library_assets{where} ORDER BY component_type, name, asset_version DESC",
                params,
            ).fetchall()
        return [self._row(row) for row in rows]

    def get(self, library_asset_id: str) -> dict[str, Any] | None:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM research_library_assets WHERE library_asset_id=?",
                (_clean(library_asset_id),),
            ).fetchone()
        return self._row(row) if row else None

    def publish_definition(self, *, definition_id: str, project_id: str) -> dict[str, Any]:
        definition = self.definitions.get(definition_id)
        if definition is None:
            raise ValueError("definition not found")
        if definition.owner_project_id != _clean(project_id) or definition.library_scope != "PROJECT":
            raise ValueError("only a local component from the current Research can be published")
        if definition.state != "VALIDATED":
            raise ValueError("validate the component before publishing it")
        if definition.definition_type == "ALPHA":
            with self.store.connection() as conn:
                for component in definition.spec.get("components", []):
                    published = conn.execute(
                        "SELECT 1 FROM research_library_assets WHERE component_type='FACTOR' AND source_object_id=?",
                        (_clean(component.get("factor_definition_id")),),
                    ).fetchone()
                    if published is None:
                        raise ValueError("publish every Factor used by this Alpha before publishing the Alpha")
        return self._publish(
            component_type=definition.definition_type,
            name=definition.name,
            source_object_id=definition.definition_id,
            source_object_version=definition.version,
            content=definition.to_dict(),
            content_hash=definition.spec_hash,
            project_id=project_id,
        )

    def ensure_project_factors(self, *, project_id: str) -> list[dict[str, Any]]:
        project_id = _clean(project_id)
        with self.store.connection() as conn:
            if conn.execute(
                "SELECT 1 FROM research_projects WHERE project_id=?",
                (project_id,),
            ).fetchone() is None:
                raise ValueError("research project not found")
        factors = [
            definition
            for definition in self.definitions.list(
                definition_type="FACTOR",
                state="VALIDATED",
                limit=1000,
            )
            if definition.owner_project_id == project_id
            and definition.library_scope == "PROJECT"
        ]
        return [
            self.publish_definition(
                definition_id=definition.definition_id,
                project_id=project_id,
            )
            for definition in factors
        ]

    def publish_universe(self, *, universe_definition_id: str, project_id: str) -> dict[str, Any]:
        definition = self.universes.get_definition(universe_definition_id)
        if definition is None:
            raise ValueError("universe definition not found")
        if definition.owner_project_id != _clean(project_id) or definition.library_scope != "PROJECT":
            raise ValueError("only a local Universe from the current Research can be published")
        snapshots = self.universes.list_snapshots(definition.universe_definition_id)
        if not snapshots:
            raise ValueError("resolve the Universe before publishing it")
        content = {"definition": asdict(definition), "snapshot": asdict(snapshots[0])}
        return self._publish(
            component_type="UNIVERSE",
            name=definition.name,
            source_object_id=definition.universe_definition_id,
            source_object_version=definition.version,
            content=content,
            content_hash=definition.fingerprint,
            project_id=project_id,
        )

    def publish_requirements(self, *, requirement_set_id: str, project_id: str, name: str) -> dict[str, Any]:
        requirement_set = self.requirements.get(requirement_set_id)
        if requirement_set is None:
            raise ValueError("Requirements not found")
        if requirement_set.project_id != _clean(project_id):
            raise ValueError("only Requirements from the current Research can be published")
        if requirement_set.status != "RESOLVED" or requirement_set.superseded_by_id:
            raise ValueError("only the current validated Requirements can be published")
        return self._publish(
            component_type="REQUIREMENTS",
            name=_clean(name) or "Research Requirements",
            source_object_id=requirement_set.requirement_set_id,
            source_object_version=str(requirement_set.version),
            content=asdict(requirement_set),
            content_hash=requirement_set.fingerprint,
            project_id=project_id,
        )

    def use_requirements(self, *, library_asset_id: str, project_id: str) -> dict[str, Any]:
        asset = self.get(library_asset_id)
        if asset is None or asset["component_type"] != "REQUIREMENTS":
            raise ValueError("published Requirements not found")
        content = asset["content"]
        groups: dict[str, list[dict[str, Any]]] = {
            "FACTOR_SPEC": [], "UNIVERSE_DEFINITION": [], "EVALUATION_SPEC": [],
            "BACKTEST_SPEC": [], "MANUAL": [],
        }
        for source in content.get("source_specs", []):
            groups.setdefault(str(source.get("origin_type") or ""), []).append(dict(source.get("spec") or {}))
        compiled = self.requirements.compile(
            project_id=_clean(project_id),
            factor_specs=groups["FACTOR_SPEC"],
            universe_requirements=groups["UNIVERSE_DEFINITION"],
            evaluation_requirements=groups["EVALUATION_SPEC"],
            backtest_requirements=groups["BACKTEST_SPEC"],
            manual_requirements=groups["MANUAL"],
            context=dict(content.get("context") or {}),
        )
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                """
                INSERT INTO research_requirement_refs(project_id, requirement_set_id, library_asset_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    requirement_set_id=excluded.requirement_set_id,
                    library_asset_id=excluded.library_asset_id,
                    updated_at=excluded.updated_at
                """,
                (_clean(project_id), compiled.requirement_set_id, asset["library_asset_id"], now, now),
            )
        return {"library_asset": asset, "requirements": asdict(compiled)}

    def set_local_requirements(self, *, project_id: str, requirement_set_id: str) -> dict[str, Any]:
        requirement_set = self.requirements.get(requirement_set_id)
        if requirement_set is None or requirement_set.project_id != _clean(project_id):
            raise ValueError("Requirements do not belong to the current Research")
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                """
                INSERT INTO research_requirement_refs(project_id, requirement_set_id, library_asset_id, created_at, updated_at)
                VALUES (?, ?, NULL, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    requirement_set_id=excluded.requirement_set_id,
                    library_asset_id=NULL,
                    updated_at=excluded.updated_at
                """,
                (_clean(project_id), requirement_set.requirement_set_id, now, now),
            )
        result = self.get_requirement_ref(project_id)
        if result is None:
            raise RuntimeError("failed to save Research Requirements")
        return result

    def get_requirement_ref(self, project_id: str) -> dict[str, Any] | None:
        with self.store.connection() as conn:
            row = conn.execute(
                """
                SELECT r.project_id, r.requirement_set_id, r.created_at, r.updated_at,
                       a.library_asset_id, a.asset_version, a.name AS library_name
                FROM research_requirement_refs r
                LEFT JOIN research_library_assets a ON a.library_asset_id=r.library_asset_id
                WHERE r.project_id=?
                """,
                (_clean(project_id),),
            ).fetchone()
        if row is None:
            return None
        return {
            "project_id": str(row["project_id"]),
            "requirement_set_id": str(row["requirement_set_id"]),
            "origin": "LIBRARY" if row["library_asset_id"] else "RESEARCH",
            "library_asset_id": str(row["library_asset_id"] or ""),
            "library_version": int(row["asset_version"]) if row["asset_version"] is not None else None,
            "library_name": str(row["library_name"] or ""),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    def usage(self, library_asset_id: str) -> dict[str, Any]:
        asset = self.get(library_asset_id)
        if asset is None:
            raise ValueError("Library asset not found")
        with self.store.connection() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT project_id FROM (
                    SELECT project_id FROM project_definition_refs
                    WHERE library_asset_id=? OR definition_id=?
                    UNION ALL
                    SELECT project_id FROM research_universe_refs WHERE library_asset_id=?
                    UNION ALL
                    SELECT project_id FROM research_requirement_refs WHERE library_asset_id=?
                )
                """,
                (
                    asset["library_asset_id"],
                    asset["source_object_id"],
                    asset["library_asset_id"],
                    asset["library_asset_id"],
                ),
            ).fetchall()
            research = []
            for row in rows:
                project = conn.execute("SELECT project_id,title FROM research_projects WHERE project_id=?", (row[0],)).fetchone()
                if project:
                    research.append({"project_id": str(project["project_id"]), "title": str(project["title"])})
        return {"library_asset": asset, "research": research, "research_count": len(research)}

    def _publish(
        self,
        *,
        component_type: str,
        name: str,
        source_object_id: str,
        source_object_version: str,
        content: dict[str, Any],
        content_hash: str,
        project_id: str,
    ) -> dict[str, Any]:
        component_type = _clean(component_type).upper()
        project_id = _clean(project_id)
        library_asset_id = ""
        with self.store.transaction(immediate=True) as conn:
            existing = conn.execute(
                "SELECT library_asset_id FROM research_library_assets WHERE component_type=? AND source_object_id=?",
                (component_type, _clean(source_object_id)),
            ).fetchone()
            if existing:
                library_asset_id = str(existing[0])
            else:
                version = int(conn.execute(
                    "SELECT COALESCE(MAX(asset_version), 0) + 1 FROM research_library_assets WHERE component_type=? AND name=?",
                    (component_type, _clean(name)),
                ).fetchone()[0])
                library_asset_id = f"library_{component_type.lower()}_{uuid.uuid4().hex}"
                conn.execute(
                    """
                    INSERT INTO research_library_assets(
                        library_asset_id, component_type, name, asset_version,
                        source_object_id, source_object_version, content_json, content_hash,
                        published_from_project_id, published_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (library_asset_id, component_type, _clean(name), version, _clean(source_object_id),
                     _clean(source_object_version), json_dumps(content), _clean(content_hash), project_id, utc_now()),
                )
        result = self.get(library_asset_id)
        if result is None:
            raise RuntimeError("failed to publish Library asset")
        return result

    def archive(self, library_asset_id: str) -> dict[str, Any]:
        asset = self.get(library_asset_id)
        if asset is None:
            raise ValueError("Library asset not found")
        if asset.get("archived_at"):
            raise ValueError("Library asset is already archived")
        if self.usage(library_asset_id)["research_count"]:
            raise ValueError("Remove this asset from every Research before archiving it")
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            cursor = conn.execute(
                "UPDATE research_library_assets SET archived_at=? WHERE library_asset_id=? AND archived_at IS NULL",
                (now, _clean(library_asset_id)),
            )
            if cursor.rowcount != 1:
                raise ValueError("Active Library asset not found")
        result = self.get(library_asset_id)
        if result is None:
            raise RuntimeError("failed to archive Library asset")
        return result

    @staticmethod
    def _row(row: Any) -> dict[str, Any]:
        return {
            "library_asset_id": str(row["library_asset_id"]),
            "component_type": str(row["component_type"]),
            "name": str(row["name"]),
            "version": int(row["asset_version"]),
            "source_object_id": str(row["source_object_id"]),
            "source_object_version": str(row["source_object_version"]),
            "content": json.loads(row["content_json"] or "{}"),
            "content_hash": str(row["content_hash"]),
            "published_from_research_id": str(row["published_from_project_id"]),
            "published_at": str(row["published_at"]),
            "archived_at": str(row["archived_at"] or ""),
        }
