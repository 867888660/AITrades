from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from .alpha_factor_candidates import AlphaFactorCandidateResolver
from .definition_registry import DefinitionRegistry, ResearchDefinition
from .factor_alpha import ALPHA_ENGINE_VERSION, FACTOR_ALPHA_CODE_HASH
from .store import DataPlatformStore, json_dumps, utc_now
from .universe_service import UniverseService


ALPHA_DRAFT_SCHEMA_VERSION = "alpha_draft.v1"
ALPHA_EDITOR_DOCUMENT_VERSION = "alpha_draft.v2"
ALPHA_DRAFT_STATES = {"DRAFT", "VALIDATED", "DISCARDED"}
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _fingerprint(document: Mapping[str, Any]) -> str:
    material = {
        "schema_version": ALPHA_DRAFT_SCHEMA_VERSION,
        "document": dict(document),
    }
    return hashlib.sha256(json_dumps(material).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AlphaDraft:
    draft_id: str
    owner_project_id: str
    library_scope: str
    client_draft_key: str
    document: dict[str, Any]
    draft_fingerprint: str
    state: str
    created_by: str
    created_at: str
    updated_at: str
    validated_definition_id: str = ""
    latest_preview_id: str = ""
    latest_preview_fingerprint: str = ""
    previewed_draft_fingerprint: str = ""
    previewed_at: str | None = None
    validated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AlphaDraftValidationError(ValueError):
    def __init__(self, diagnostics: dict[str, Any]):
        super().__init__("alpha draft has blocking validation errors")
        self.diagnostics = diagnostics


class AlphaDraftService:
    """Mutable Alpha authoring documents compiled into immutable Alpha definitions."""

    def __init__(self, store: DataPlatformStore):
        self.store = store
        self.candidates = AlphaFactorCandidateResolver(store)
        self.universes = UniverseService(store)

    @staticmethod
    def inspect_document(document: dict[str, Any]) -> dict[str, Any]:
        document = dict(document or {})
        identity = (
            document.get("identity")
            if isinstance(document.get("identity"), Mapping)
            else {}
        )
        advanced = (
            document.get("advanced")
            if isinstance(document.get("advanced"), Mapping)
            else {}
        )
        raw_components = [
            dict(item)
            for item in document.get("components") or []
            if isinstance(item, Mapping)
        ]
        capabilities = DefinitionRegistry.engine_capabilities()["alpha"]
        transforms = {str(item).upper() for item in capabilities["transforms"]}
        maximum = int(capabilities["authoring_contract"]["max_components"])
        diagnostics: list[dict[str, Any]] = []
        normalized: list[dict[str, Any]] = []

        def add(level: str, code: str, path: str, message: str) -> None:
            diagnostics.append({
                "level": level,
                "code": code,
                "path": path,
                "message": message,
            })

        if not _clean(identity.get("name")):
            add("ERROR", "ALPHA_NAME_REQUIRED", "identity.name", "Alpha name is required.")
        if not _clean(identity.get("version")):
            add("ERROR", "ALPHA_VERSION_REQUIRED", "identity.version", "Alpha version is required.")
        if not raw_components:
            add("ERROR", "ALPHA_COMPONENT_REQUIRED", "components", "At least one Factor component is required.")
        if len(raw_components) > maximum:
            add(
                "ERROR",
                "ALPHA_COMPONENT_LIMIT_EXCEEDED",
                "components",
                f"Alpha supports at most {maximum} Factor components.",
            )

        seen_names: set[str] = set()
        seen_components: set[tuple[str, str, str, bool]] = set()
        for index, item in enumerate(raw_components):
            path = f"components.{index}"
            variable_name = _clean(item.get("variable_name"))
            if not variable_name:
                add("ERROR", "ALPHA_COMPONENT_VARIABLE_REQUIRED", f"{path}.variable_name", "Variable name is required.")
            elif not _IDENTIFIER.fullmatch(variable_name):
                add("ERROR", "ALPHA_COMPONENT_VARIABLE_INVALID", f"{path}.variable_name", "Variable name must be a valid Formula identifier.")
            elif variable_name in seen_names:
                add("ERROR", "ALPHA_COMPONENT_VARIABLE_DUPLICATE", f"{path}.variable_name", f"Duplicate variable name: {variable_name}.")
            seen_names.add(variable_name)

            definition_id = _clean(item.get("factor_definition_id"))
            version = _clean(item.get("factor_version") or item.get("version"))
            if not definition_id or not version:
                add("ERROR", "ALPHA_FACTOR_REFERENCE_REQUIRED", path, "Factor ID and Version are required.")
            transform = _clean(item.get("transform") or "CS_RANK").upper()
            if transform not in transforms:
                add("ERROR", "ALPHA_TRANSFORM_UNSUPPORTED", f"{path}.transform", f"Unsupported transform: {transform}.")
            try:
                weight = float(item.get("weight", 0.0))
            except (TypeError, ValueError):
                weight = math.nan
            if not math.isfinite(weight):
                add("ERROR", "ALPHA_WEIGHT_NON_FINITE", f"{path}.weight", "Weight must be a finite number.")
            if transform == "RAW":
                ascending = True
            elif "score_direction" in item:
                direction = _clean(item.get("score_direction")).upper()
                ascending = direction != "LOW_VALUE_HIGH_SCORE"
            else:
                ascending = bool(item.get("ascending", True))
            key = (definition_id, version, transform, ascending)
            if definition_id and key in seen_components:
                add("WARNING", "ALPHA_DUPLICATE_COMPONENT_REVIEW", path, "The same Factor transform is used more than once.")
            seen_components.add(key)
            normalized.append({
                "variable_name": variable_name,
                "factor_definition_id": definition_id,
                "factor_version": version,
                "weight": weight if math.isfinite(weight) else item.get("weight"),
                "transform": transform,
                "ascending": ascending,
            })

        finite_weights = [
            float(item["weight"])
            for item in normalized
            if isinstance(item.get("weight"), (int, float))
            and math.isfinite(float(item["weight"]))
        ]
        if finite_weights and all(weight == 0 for weight in finite_weights):
            add("WARNING", "ALPHA_ALL_WEIGHTS_ZERO", "components", "All component weights are zero.")
        if any(weight < 0 for weight in finite_weights):
            add("WARNING", "ALPHA_NEGATIVE_WEIGHT_REVIEW", "components", "Negative weights invert a component contribution.")
        component_transforms = {item["transform"] for item in normalized}
        if {"RAW", "CS_RANK"}.issubset(component_transforms):
            add("WARNING", "ALPHA_RAW_SCALE_MIXED", "components", "RAW and CS_RANK values may have incomparable scales.")

        try:
            coverage = float(advanced.get("minimum_coverage", 1.0))
        except (TypeError, ValueError):
            coverage = math.nan
        if not math.isfinite(coverage) or not 0 < coverage <= 1:
            add("ERROR", "ALPHA_COVERAGE_INVALID", "advanced.minimum_coverage", "Minimum coverage must be in (0, 1].")
        try:
            cross_section = int(advanced.get("minimum_cross_section_size", 2))
        except (TypeError, ValueError):
            cross_section = 0
        if cross_section < 1:
            add("ERROR", "ALPHA_CROSS_SECTION_INVALID", "advanced.minimum_cross_section_size", "Minimum Instruments must be positive.")
        missing_policy = _clean(advanced.get("missing_policy") or "EXCLUDE").upper()
        rank_method = _clean(advanced.get("rank_method") or "AVERAGE").upper()
        output_scale = _clean(advanced.get("output_scale") or "PERCENTILE").upper()
        if missing_policy not in set(capabilities["missing_policies"]):
            add("ERROR", "ALPHA_MISSING_POLICY_UNSUPPORTED", "advanced.missing_policy", f"Unsupported missing policy: {missing_policy}.")
        if rank_method not in set(capabilities["rank_methods"]):
            add("ERROR", "ALPHA_RANK_METHOD_UNSUPPORTED", "advanced.rank_method", f"Unsupported rank method: {rank_method}.")
        if output_scale not in set(capabilities["output_scales"]):
            add("ERROR", "ALPHA_OUTPUT_SCALE_UNSUPPORTED", "advanced.output_scale", f"Unsupported output scale: {output_scale}.")

        errors = sum(item["level"] == "ERROR" for item in diagnostics)
        warnings = sum(item["level"] == "WARNING" for item in diagnostics)
        return {
            "schema_version": ALPHA_EDITOR_DOCUMENT_VERSION,
            "definition_checks_passed": errors == 0,
            "can_compile": errors == 0,
            "can_preview": errors == 0,
            "can_validate": False,
            "can_save_alpha": True,
            "preview_required": True,
            "preview_status": "NOT_RUN",
            "summary": {"errors": errors, "warnings": warnings},
            "diagnostics": diagnostics,
            "draft_fingerprint": _fingerprint(document),
            "normalized_components": normalized,
            "dependency_closure": [],
            "compiled_alpha_spec": None,
        }

    def inspect_project_document(
        self,
        document: dict[str, Any],
        owner_project_id: str,
    ) -> dict[str, Any]:
        result = self.inspect_document(document)
        project_id = _clean(owner_project_id)
        if not project_id:
            return self._add_error(
                result,
                "ALPHA_PROJECT_REQUIRED",
                "owner_project_id",
                "Research Alpha Draft requires an owner Project.",
            )
        try:
            context = self.candidates.assert_components_accessible(
                project_id,
                result["normalized_components"],
            )
            universe_ref = self.universes.get_research_ref(project_id)
            if universe_ref is None:
                return self._add_error(
                    result,
                    "ALPHA_UNIVERSE_REQUIRED",
                    "preview.universe_snapshot_id",
                    "Select a primary Universe before running Alpha Preview.",
                )
            member_count = len(universe_ref["actual_instrument_ids"])
            advanced = (
                document.get("advanced")
                if isinstance(document.get("advanced"), Mapping)
                else {}
            )
            try:
                minimum = int(advanced.get("minimum_cross_section_size", 2))
            except (TypeError, ValueError):
                minimum = 0
            if minimum > member_count:
                result = self._add_error(
                    result,
                    "ALPHA_CROSS_SECTION_EXCEEDS_UNIVERSE",
                    "advanced.minimum_cross_section_size",
                    "Minimum Instruments exceeds the current Universe member count.",
                )
            if member_count == 1 and any(
                item["transform"] == "CS_RANK"
                for item in result["normalized_components"]
            ):
                result["diagnostics"].append({
                    "level": "WARNING",
                    "code": "ALPHA_SINGLE_INSTRUMENT_RANK",
                    "path": "components",
                    "message": "CS_RANK on a one-Instrument Universe has no cross-sectional separation.",
                })
                self._summarize(result)
            definitions = context["resolved_definitions"]
            compiled = self.compile_document(
                document,
                universe_snapshot_id=universe_ref["universe_snapshot_id"],
                resolved_factors=definitions,
            ) if result["definition_checks_passed"] else None
            result.update({
                "candidate_fingerprint": context["candidate_fingerprint"],
                "selected_factor_candidates": context["selected_factors"],
                "dependency_closure": [
                    {
                        "factor_definition_id": item.definition_id,
                        "factor_version": item.version,
                        "factor_spec_hash": item.spec_hash,
                        "engine_version": item.engine_version,
                        "code_hash": item.code_hash,
                    }
                    for item in definitions
                ],
                "universe": universe_ref,
                "compiled_alpha_spec": compiled,
            })
            return result
        except ValueError as exc:
            raw = str(exc)
            prefix, separator, detail = raw.partition(": ")
            code = prefix if separator and prefix.startswith("ALPHA_") else "ALPHA_FACTOR_NOT_ACCESSIBLE"
            return self._add_error(
                result,
                code,
                "components",
                detail if separator else raw,
            )

    @staticmethod
    def compile_document(
        document: dict[str, Any],
        *,
        universe_snapshot_id: str,
        resolved_factors: Sequence[ResearchDefinition],
    ) -> dict[str, Any]:
        inspected = AlphaDraftService.inspect_document(document)
        if not inspected["definition_checks_passed"]:
            raise AlphaDraftValidationError(inspected)
        factors = {
            (item.definition_id, item.version): item
            for item in resolved_factors
        }
        components: list[dict[str, Any]] = []
        for item in inspected["normalized_components"]:
            key = (item["factor_definition_id"], item["factor_version"])
            factor = factors.get(key)
            if factor is None:
                raise ValueError(
                    "ALPHA_FACTOR_NOT_ACCESSIBLE: "
                    f"Factor was not resolved: {key[0]}@{key[1]}."
                )
            components.append({
                "factor_definition_id": factor.definition_id,
                "factor_version": factor.version,
                "factor_spec_hash": factor.spec_hash,
                "factor_name": factor.name,
                "weight": float(item["weight"]),
                "transform": item["transform"],
                "ascending": bool(item["ascending"]),
            })
        identity = document.get("identity") or {}
        advanced = document.get("advanced") or {}
        return {
            "name": _clean(identity.get("name")),
            "version": _clean(identity.get("version")),
            "components": components,
            "universe_snapshot_id": _clean(universe_snapshot_id),
            "minimum_coverage": float(advanced.get("minimum_coverage", 1.0)),
            "minimum_cross_section_size": int(
                advanced.get("minimum_cross_section_size", 2)
            ),
            "missing_policy": _clean(
                advanced.get("missing_policy") or "EXCLUDE"
            ).upper(),
            "rank_method": _clean(
                advanced.get("rank_method") or "AVERAGE"
            ).upper(),
            "output_scale": _clean(
                advanced.get("output_scale") or "PERCENTILE"
            ).upper(),
            "engine_version": ALPHA_ENGINE_VERSION,
            "code_hash": FACTOR_ALPHA_CODE_HASH,
        }

    def create(
        self,
        document: dict[str, Any],
        *,
        owner_project_id: str,
        client_draft_key: str = "",
        created_by: str = "local_ui_user",
        library_scope: str = "PROJECT",
    ) -> AlphaDraft:
        project_id = _clean(owner_project_id)
        if _clean(library_scope).upper() != "PROJECT":
            raise ValueError(
                "ALPHA_LIBRARY_SCOPE_UNSUPPORTED: "
                "Research Alpha Drafts only support PROJECT scope."
            )
        if not project_id:
            raise ValueError("owner_project_id is required")
        document = json.loads(json_dumps(dict(document or {})))
        now = utc_now()
        draft_id = f"alpha_draft_{uuid.uuid4().hex}"
        client_key = _clean(client_draft_key)
        with self.store.transaction(immediate=True) as conn:
            if conn.execute(
                "SELECT 1 FROM research_projects WHERE project_id=?",
                (project_id,),
            ).fetchone() is None:
                raise ValueError("research project not found")
            if client_key:
                existing = conn.execute(
                    """
                    SELECT draft_id FROM alpha_drafts
                    WHERE owner_project_id=? AND client_draft_key=?
                    """,
                    (project_id, client_key),
                ).fetchone()
                if existing:
                    found = self.get(str(existing["draft_id"]))
                    if found is None:
                        raise RuntimeError("failed to load idempotent Alpha Draft")
                    return found
            conn.execute(
                """
                INSERT INTO alpha_drafts(
                    draft_id,owner_project_id,library_scope,client_draft_key,
                    document_json,draft_fingerprint,state,created_by,
                    created_at,updated_at
                ) VALUES (?,?,?,?,?,?,'DRAFT',?,?,?)
                """,
                (
                    draft_id,
                    project_id,
                    "PROJECT",
                    client_key,
                    json_dumps(document),
                    _fingerprint(document),
                    _clean(created_by) or "local_ui_user",
                    now,
                    now,
                ),
            )
            from .research_authoring_audit import ResearchAuthoringAudit

            ResearchAuthoringAudit.record(
                conn,
                object_type="ALPHA_DRAFT",
                object_id=draft_id,
                project_id=project_id,
                operation="CREATE",
                after_fingerprint=_fingerprint(document),
                actor_id=_clean(created_by) or "local_ui_user",
            )
        result = self.get(draft_id)
        if result is None:
            raise RuntimeError("failed to create Alpha Draft")
        return result

    def update(
        self,
        draft_id: str,
        document: dict[str, Any],
        *,
        expected_fingerprint: str = "",
    ) -> AlphaDraft:
        draft = self.get(draft_id)
        if draft is None:
            raise ValueError("alpha draft not found")
        if draft.state != "DRAFT":
            raise ValueError("only active Alpha drafts can be updated")
        if expected_fingerprint and expected_fingerprint != draft.draft_fingerprint:
            raise ValueError("ALPHA_DRAFT_STALE: draft fingerprint changed")
        document = json.loads(json_dumps(dict(document or {})))
        next_fingerprint = _fingerprint(document)
        with self.store.transaction(immediate=True) as conn:
            if next_fingerprint == draft.draft_fingerprint:
                cursor = conn.execute(
                    """
                    UPDATE alpha_drafts SET document_json=?,updated_at=?
                    WHERE draft_id=? AND state='DRAFT'
                    """,
                    (json_dumps(document), utc_now(), draft.draft_id),
                )
            else:
                cursor = conn.execute(
                    """
                    UPDATE alpha_drafts
                    SET document_json=?,draft_fingerprint=?,updated_at=?,
                        latest_preview_id='',latest_preview_fingerprint='',
                        previewed_draft_fingerprint='',previewed_at=NULL
                    WHERE draft_id=? AND state='DRAFT'
                      AND draft_fingerprint=?
                    """,
                    (
                        json_dumps(document),
                        next_fingerprint,
                        utc_now(),
                        draft.draft_id,
                        draft.draft_fingerprint,
                    ),
                )
            if cursor.rowcount != 1:
                raise ValueError("ALPHA_DRAFT_STALE: draft changed while saving")
            from .research_authoring_audit import ResearchAuthoringAudit

            ResearchAuthoringAudit.record(
                conn,
                object_type="ALPHA_DRAFT",
                object_id=draft.draft_id,
                project_id=draft.owner_project_id,
                operation="UPDATE",
                before_fingerprint=draft.draft_fingerprint,
                after_fingerprint=next_fingerprint,
                actor_id=draft.created_by,
            )
        result = self.get(draft.draft_id)
        if result is None:
            raise RuntimeError("failed to update Alpha Draft")
        return result

    def discard(
        self,
        draft_id: str,
        *,
        expected_fingerprint: str,
    ) -> AlphaDraft:
        draft = self.get(draft_id)
        if draft is None:
            raise ValueError("alpha draft not found")
        if not _clean(expected_fingerprint):
            raise ValueError("expected_fingerprint is required")
        if expected_fingerprint != draft.draft_fingerprint:
            raise ValueError("ALPHA_DRAFT_STALE: draft fingerprint changed")
        if draft.state == "VALIDATED":
            raise ValueError("validated Alphas cannot be discarded")
        if draft.state == "DISCARDED":
            return draft
        with self.store.transaction(immediate=True) as conn:
            cursor = conn.execute(
                """
                UPDATE alpha_drafts SET state='DISCARDED',updated_at=?
                WHERE draft_id=? AND state='DRAFT' AND draft_fingerprint=?
                """,
                (utc_now(), draft.draft_id, draft.draft_fingerprint),
            )
            if cursor.rowcount != 1:
                raise ValueError(
                    "ALPHA_DRAFT_STALE: draft changed while discarding"
                )
            from .research_authoring_audit import ResearchAuthoringAudit

            ResearchAuthoringAudit.record(
                conn,
                object_type="ALPHA_DRAFT",
                object_id=draft.draft_id,
                project_id=draft.owner_project_id,
                operation="DISCARD",
                before_fingerprint=draft.draft_fingerprint,
                after_fingerprint=draft.draft_fingerprint,
                actor_id=draft.created_by,
            )
        result = self.get(draft.draft_id)
        if result is None:
            raise RuntimeError("failed to discard Alpha Draft")
        return result

    def inspect(self, draft_id: str) -> dict[str, Any]:
        draft = self.get(draft_id)
        if draft is None:
            raise ValueError("alpha draft not found")
        result = (
            self.inspect_project_document(draft.document, draft.owner_project_id)
            if draft.state == "DRAFT"
            else self.inspect_document(draft.document)
        )
        if draft.state == "DISCARDED":
            result.update({
                "can_compile": False,
                "can_preview": False,
                "can_validate": False,
                "can_save_alpha": False,
                "preview_status": "DISCARDED",
            })
        elif draft.state == "VALIDATED":
            result.update({
                "can_validate": True,
                "preview_status": "VALIDATED",
                "latest_preview_id": draft.latest_preview_id,
                "latest_preview_fingerprint": draft.latest_preview_fingerprint,
            })
        elif (
            draft.latest_preview_id
            and draft.latest_preview_fingerprint
            and draft.previewed_draft_fingerprint == draft.draft_fingerprint
        ):
            from .alpha_preview import (
                AlphaPreviewError,
                AlphaPreviewService,
            )

            try:
                preview = AlphaPreviewService(self.store).assert_current(
                    draft.draft_id,
                    preview_id=draft.latest_preview_id,
                    preview_fingerprint=draft.latest_preview_fingerprint,
                )
                result.update({
                    "can_validate": result["definition_checks_passed"],
                    "preview_status": "READY",
                    "latest_preview_id": draft.latest_preview_id,
                    "latest_preview_fingerprint": (
                        draft.latest_preview_fingerprint
                    ),
                    "preview": preview,
                })
            except AlphaPreviewError as exc:
                result.update({
                    "can_validate": False,
                    "preview_status": "STALE",
                    "preview_diagnostics": exc.diagnostics,
                })
        return result

    def validate(
        self,
        draft_id: str,
        *,
        expected_fingerprint: str,
        preview_id: str,
        preview_fingerprint: str,
    ) -> tuple[AlphaDraft, ResearchDefinition]:
        draft = self.get(draft_id)
        if draft is None:
            raise ValueError("alpha draft not found")
        if draft.state == "VALIDATED":
            definition = DefinitionRegistry(self.store).get(
                draft.validated_definition_id
            )
            if definition is None:
                raise ValueError("validated Alpha definition not found")
            return draft, definition
        if draft.state != "DRAFT":
            raise ValueError(
                "discarded Alpha drafts cannot be validated"
            )
        if not _clean(expected_fingerprint):
            raise ValueError("expected_fingerprint is required")
        if expected_fingerprint != draft.draft_fingerprint:
            raise ValueError(
                "ALPHA_DRAFT_STALE: validate the latest saved draft"
            )
        diagnostics = self.inspect_project_document(
            draft.document,
            draft.owner_project_id,
        )
        if not diagnostics["definition_checks_passed"]:
            raise AlphaDraftValidationError(diagnostics)
        from .alpha_preview import AlphaPreviewService

        preview = AlphaPreviewService(self.store).assert_current(
            draft.draft_id,
            preview_id=preview_id,
            preview_fingerprint=preview_fingerprint,
        )
        with self.store.connection() as conn:
            missing_library_factors = [
                item["factor_definition_id"]
                for item in diagnostics["compiled_alpha_spec"]["components"]
                if conn.execute(
                    """
                    SELECT 1 FROM research_library_assets
                    WHERE component_type='FACTOR'
                      AND source_object_id=?
                    """,
                    (item["factor_definition_id"],),
                ).fetchone() is None
            ]
        if missing_library_factors:
            blocked = dict(diagnostics)
            blocked["diagnostics"] = [
                *diagnostics.get("diagnostics", []),
                {
                    "level": "ERROR",
                    "code": "ALPHA_FACTOR_LIBRARY_REQUIRED",
                    "path": "components",
                    "message": (
                        "Publish every pinned Factor before validating "
                        "the Alpha: "
                        + ", ".join(sorted(missing_library_factors))
                    ),
                },
            ]
            self._summarize(blocked)
            raise AlphaDraftValidationError(blocked)
        definition = DefinitionRegistry(self.store).create(
            "ALPHA",
            dict(diagnostics["compiled_alpha_spec"]),
            state="VALIDATED",
            created_by=draft.created_by,
            owner_project_id=draft.owner_project_id,
            library_scope="PROJECT",
        )
        validated_at = utc_now()
        with self.store.transaction(immediate=True) as conn:
            cursor = conn.execute(
                """
                UPDATE alpha_drafts
                SET state='VALIDATED',validated_definition_id=?,
                    validated_at=?,updated_at=?
                WHERE draft_id=? AND state='DRAFT'
                  AND draft_fingerprint=?
                """,
                (
                    definition.definition_id,
                    validated_at,
                    validated_at,
                    draft.draft_id,
                    draft.draft_fingerprint,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError(
                    "ALPHA_DRAFT_STALE: Alpha changed while validating"
                )
            from .research_authoring_audit import ResearchAuthoringAudit

            ResearchAuthoringAudit.record(
                conn,
                object_type="ALPHA_DRAFT",
                object_id=draft.draft_id,
                project_id=draft.owner_project_id,
                operation="VALIDATE",
                before_fingerprint=draft.draft_fingerprint,
                after_fingerprint=definition.spec_hash,
                actor_id=draft.created_by,
            )
        validated = self.get(draft.draft_id)
        if validated is None or validated.state != "VALIDATED":
            raise RuntimeError("failed to validate Alpha Draft")
        AlphaPreviewService(self.store).mark_validated(
            preview["preview_id"],
            definition.definition_id,
        )
        return validated, definition

    def get(self, draft_id: str) -> AlphaDraft | None:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM alpha_drafts WHERE draft_id=?",
                (_clean(draft_id),),
            ).fetchone()
        return self._from_row(row) if row else None

    def list(
        self,
        *,
        owner_project_id: str = "",
        state: str = "",
        limit: int = 200,
    ) -> list[AlphaDraft]:
        clauses: list[str] = []
        params: list[Any] = []
        if _clean(owner_project_id):
            clauses.append("owner_project_id=?")
            params.append(_clean(owner_project_id))
        if _clean(state):
            requested_state = _clean(state).upper()
            if requested_state not in ALPHA_DRAFT_STATES:
                raise ValueError(
                    f"unsupported Alpha draft state: {requested_state}"
                )
            clauses.append("state=?")
            params.append(requested_state)
        else:
            clauses.append("state!='DISCARDED'")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(int(limit), 1000)))
        with self.store.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM alpha_drafts{where}
                ORDER BY updated_at DESC LIMIT ?
                """,
                params,
            ).fetchall()
        return [self._from_row(row) for row in rows]

    @staticmethod
    def _add_error(
        result: dict[str, Any],
        code: str,
        path: str,
        message: str,
    ) -> dict[str, Any]:
        result["diagnostics"] = [
            *result.get("diagnostics", []),
            {
                "level": "ERROR",
                "code": code,
                "path": path,
                "message": message,
            },
        ]
        AlphaDraftService._summarize(result)
        return result

    @staticmethod
    def _summarize(result: dict[str, Any]) -> None:
        errors = sum(
            item.get("level") == "ERROR"
            for item in result.get("diagnostics", [])
        )
        warnings = sum(
            item.get("level") == "WARNING"
            for item in result.get("diagnostics", [])
        )
        result.update({
            "definition_checks_passed": errors == 0,
            "can_compile": errors == 0,
            "can_preview": errors == 0,
            "can_validate": False,
            "summary": {"errors": errors, "warnings": warnings},
        })

    @staticmethod
    def _from_row(row: Any) -> AlphaDraft:
        return AlphaDraft(
            draft_id=str(row["draft_id"]),
            owner_project_id=str(row["owner_project_id"]),
            library_scope=str(row["library_scope"] or "PROJECT"),
            client_draft_key=str(row["client_draft_key"] or ""),
            document=json.loads(row["document_json"] or "{}"),
            draft_fingerprint=str(row["draft_fingerprint"]),
            state=str(row["state"]),
            validated_definition_id=str(
                row["validated_definition_id"] or ""
            ),
            latest_preview_id=str(row["latest_preview_id"] or ""),
            latest_preview_fingerprint=str(
                row["latest_preview_fingerprint"] or ""
            ),
            previewed_draft_fingerprint=str(
                row["previewed_draft_fingerprint"] or ""
            ),
            created_by=str(row["created_by"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            previewed_at=(
                str(row["previewed_at"]) if row["previewed_at"] else None
            ),
            validated_at=(
                str(row["validated_at"]) if row["validated_at"] else None
            ),
        )
