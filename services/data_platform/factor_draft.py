from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from typing import Any

from .definition_registry import DefinitionRegistry, ResearchDefinition
from .factor_formula import FactorFormulaCompiler
from .store import DataPlatformStore, json_dumps, utc_now


FACTOR_DRAFT_SCHEMA_VERSION = "factor_draft.v1"
FACTOR_EDITOR_DOCUMENT_VERSION = "factor_draft.v2"
FACTOR_DRAFT_STATES = {"DRAFT", "VALIDATED", "DISCARDED"}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _fingerprint(document: dict[str, Any]) -> str:
    material = {
        "schema_version": FACTOR_DRAFT_SCHEMA_VERSION,
        "document": document,
    }
    return hashlib.sha256(json_dumps(material).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FactorDraft:
    draft_id: str
    document: dict[str, Any]
    draft_fingerprint: str
    state: str
    created_by: str
    created_at: str
    updated_at: str
    owner_project_id: str = ""
    library_scope: str = "GLOBAL"
    validated_definition_id: str = ""
    validated_at: str | None = None
    latest_preview_id: str = ""
    latest_preview_fingerprint: str = ""
    previewed_draft_fingerprint: str = ""
    previewed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FactorDraftValidationError(ValueError):
    def __init__(self, diagnostics: dict[str, Any]):
        super().__init__("factor draft has blocking validation errors")
        self.diagnostics = diagnostics


class FactorDraftService:
    """Mutable, incomplete Factor authoring documents compiled into immutable definitions."""

    def __init__(self, store: DataPlatformStore):
        self.store = store

    @staticmethod
    def inspect_document(document: dict[str, Any]) -> dict[str, Any]:
        document = dict(document or {})
        identity = document.get("identity") if isinstance(document.get("identity"), dict) else {}
        output = document.get("output") if isinstance(document.get("output"), dict) else {}
        advanced = document.get("advanced") if isinstance(document.get("advanced"), dict) else {}
        capabilities = DefinitionRegistry.engine_capabilities()["factor"]
        directions = {str(item) for item in capabilities.get("output_directions") or []}
        diagnostics: list[dict[str, Any]] = []

        def add(level: str, code: str, path: str, message: str) -> None:
            diagnostics.append({
                "level": level,
                "code": code,
                "path": path,
                "message": message,
            })

        if not _clean(identity.get("name")):
            add("ERROR", "FACTOR_NAME_REQUIRED", "identity.name", "Factor name is required.")
        if not _clean(identity.get("version")):
            add("ERROR", "FACTOR_VERSION_REQUIRED", "identity.version", "Factor version is required.")
        compiler_result = FactorFormulaCompiler.inspect(document, capabilities)
        diagnostics.extend(compiler_result["diagnostics"])

        direction = _clean(output.get("direction") or "NO_PREDEFINED_DIRECTION").upper()
        if direction not in directions:
            add("ERROR", "OUTPUT_DIRECTION_UNSUPPORTED", "output.direction", f"Unsupported output direction: {direction}.")
        output_unit = _clean(output.get("unit")).upper()
        compilation = compiler_result.get("compilation")
        if compilation is not None:
            expected_unit = _clean(compilation["factor_spec"].get("output_unit") or "RATIO").upper()
            if output_unit and output_unit != expected_unit:
                add(
                    "ERROR",
                    "OUTPUT_UNIT_MISMATCH",
                    "output.unit",
                    f"The compiled Formula produces {expected_unit}, not {output_unit}.",
                )
        missing_policy = _clean(advanced.get("missing_policy") or "STRICT").upper()
        if missing_policy not in set(capabilities.get("missing_policies") or []):
            add("ERROR", "MISSING_POLICY_UNSUPPORTED", "advanced.missing_policy", f"Unsupported missing policy: {missing_policy}.")
        if bool(advanced.get("allow_incomplete_bar", False)):
            add(
                "WARNING",
                "INCOMPLETE_BAR_REVIEW",
                "advanced.allow_incomplete_bar",
                "Incomplete bars change point-in-time and data-quality semantics.",
            )

        errors = sum(item["level"] == "ERROR" for item in diagnostics)
        warnings = sum(item["level"] == "WARNING" for item in diagnostics)
        return {
            "schema_version": FACTOR_EDITOR_DOCUMENT_VERSION,
            "definition_checks_passed": errors == 0,
            "can_compile": errors == 0,
            "can_preview": errors == 0,
            "can_validate": False,
            "can_save_factor": errors == 0,
            "preview_required": True,
            "preview_status": "NOT_RUN",
            "summary": {"errors": errors, "warnings": warnings},
            "diagnostics": diagnostics,
            "draft_fingerprint": _fingerprint(document),
            "normalized_inputs": compiler_result["inputs"],
            "normalized_parameters": compiler_result["parameters"],
            "formula_source": compiler_result["source"],
            "compiled_formula": compilation,
            "compiled_factor_spec": compilation["factor_spec"] if compilation else None,
        }

    @staticmethod
    def compile_document(document: dict[str, Any]) -> dict[str, Any]:
        return FactorFormulaCompiler.compile(
            document,
            DefinitionRegistry.engine_capabilities()["factor"],
        )

    def inspect_project_document(
        self,
        document: dict[str, Any],
        owner_project_id: str,
    ) -> dict[str, Any]:
        result = self.inspect_document(document)
        project_id = _clean(owner_project_id)
        if not project_id:
            return result
        from .input_candidate_resolver import FactorInputCandidateResolver

        try:
            candidate_context = FactorInputCandidateResolver(
                self.store
            ).assert_inputs_selectable(
                project_id,
                result.get("normalized_inputs") or [],
            )
            result["input_candidate_fingerprint"] = candidate_context[
                "candidate_fingerprint"
            ]
            result["selected_input_candidates"] = candidate_context["selected_inputs"]
            return result
        except ValueError as exc:
            raw = str(exc)
            prefix, separator, detail = raw.partition(": ")
            code = (
                prefix
                if separator and prefix.startswith(("FACTOR_", "INPUT_"))
                else "FACTOR_INPUT_CANDIDATE_UNAVAILABLE"
            )
            message = detail if separator else raw
            result["diagnostics"] = [
                *result.get("diagnostics", []),
                {
                    "level": "ERROR",
                    "code": code,
                    "path": "inputs",
                    "message": message,
                },
            ]
            errors = sum(
                item.get("level") == "ERROR"
                for item in result["diagnostics"]
            )
            warnings = sum(
                item.get("level") == "WARNING"
                for item in result["diagnostics"]
            )
            result.update({
                "definition_checks_passed": False,
                "can_preview": False,
                "can_validate": False,
                "summary": {"errors": errors, "warnings": warnings},
            })
            return result

    def create(
        self,
        document: dict[str, Any],
        *,
        created_by: str = "local_ui_user",
        owner_project_id: str = "",
        library_scope: str = "GLOBAL",
    ) -> FactorDraft:
        now = utc_now()
        draft_id = f"factor_draft_{uuid.uuid4().hex}"
        owner_project_id = _clean(owner_project_id)
        library_scope = _clean(library_scope).upper() or "GLOBAL"
        if library_scope not in {"PROJECT", "GLOBAL"}:
            raise ValueError("library_scope must be PROJECT or GLOBAL")
        if library_scope == "PROJECT" and not owner_project_id:
            raise ValueError("PROJECT Factor drafts require owner_project_id")
        document = json.loads(json_dumps(dict(document or {})))
        with self.store.transaction(immediate=True) as conn:
            if owner_project_id and conn.execute(
                "SELECT 1 FROM research_projects WHERE project_id=?",
                (owner_project_id,),
            ).fetchone() is None:
                raise ValueError("research project not found")
            conn.execute(
                """
                INSERT INTO factor_drafts(
                    draft_id, owner_project_id, library_scope, document_json,
                    draft_fingerprint, state, created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'DRAFT', ?, ?, ?)
                """,
                (
                    draft_id,
                    owner_project_id,
                    library_scope,
                    json_dumps(document),
                    _fingerprint(document),
                    _clean(created_by) or "local_ui_user",
                    now,
                    now,
                ),
            )
        return self.get(draft_id)  # type: ignore[return-value]

    def update(
        self,
        draft_id: str,
        document: dict[str, Any],
        *,
        expected_fingerprint: str = "",
    ) -> FactorDraft:
        draft = self.get(draft_id)
        if draft is None:
            raise ValueError("factor draft not found")
        if draft.state != "DRAFT":
            raise ValueError("only active Factor drafts can be updated")
        if expected_fingerprint and expected_fingerprint != draft.draft_fingerprint:
            raise ValueError("FACTOR_DRAFT_STALE: draft fingerprint changed")
        document = json.loads(json_dumps(dict(document or {})))
        next_fingerprint = _fingerprint(document)
        with self.store.transaction(immediate=True) as conn:
            if next_fingerprint == draft.draft_fingerprint:
                conn.execute(
                    """
                    UPDATE factor_drafts
                    SET document_json=?, updated_at=?
                    WHERE draft_id=? AND state='DRAFT'
                    """,
                    (json_dumps(document), utc_now(), _clean(draft_id)),
                )
            else:
                conn.execute(
                    """
                    UPDATE factor_drafts
                    SET document_json=?, draft_fingerprint=?, updated_at=?,
                        latest_preview_id='', latest_preview_fingerprint='',
                        previewed_draft_fingerprint='', previewed_at=NULL
                    WHERE draft_id=? AND state='DRAFT'
                    """,
                    (
                        json_dumps(document),
                        next_fingerprint,
                        utc_now(),
                        _clean(draft_id),
                    ),
                )
        return self.get(draft_id)  # type: ignore[return-value]

    def discard(
        self,
        draft_id: str,
        *,
        expected_fingerprint: str,
    ) -> FactorDraft:
        draft = self.get(draft_id)
        if draft is None:
            raise ValueError("factor draft not found")
        if not _clean(expected_fingerprint):
            raise ValueError("expected_fingerprint is required")
        if expected_fingerprint != draft.draft_fingerprint:
            raise ValueError("FACTOR_DRAFT_STALE: draft fingerprint changed")
        if draft.state == "VALIDATED":
            raise ValueError("validated Factors cannot be discarded")
        if draft.state == "DISCARDED":
            return draft
        with self.store.transaction(immediate=True) as conn:
            cursor = conn.execute(
                """
                UPDATE factor_drafts
                SET state='DISCARDED', updated_at=?
                WHERE draft_id=? AND state='DRAFT' AND draft_fingerprint=?
                """,
                (utc_now(), draft.draft_id, draft.draft_fingerprint),
            )
        if cursor.rowcount != 1:
            raise ValueError("FACTOR_DRAFT_STALE: draft changed while it was being discarded")
        discarded = self.get(draft.draft_id)
        if discarded is None:
            raise ValueError("factor draft not found")
        return discarded

    def validate(
        self,
        draft_id: str,
        *,
        expected_fingerprint: str,
        preview_id: str,
        preview_fingerprint: str,
    ) -> tuple[FactorDraft, ResearchDefinition]:
        draft = self.get(draft_id)
        if draft is None:
            raise ValueError("factor draft not found")
        if draft.state == "VALIDATED":
            definition = DefinitionRegistry(self.store).get(draft.validated_definition_id)
            if definition is None:
                raise ValueError("validated Factor definition not found")
            return draft, definition
        if draft.state != "DRAFT":
            raise ValueError("discarded Factor drafts cannot be validated")
        if not expected_fingerprint:
            raise ValueError("expected_fingerprint is required")
        if expected_fingerprint != draft.draft_fingerprint:
            raise ValueError("FACTOR_DRAFT_STALE: validate the latest saved draft")
        diagnostics = self.inspect_project_document(
            draft.document,
            draft.owner_project_id,
        )
        if not diagnostics["definition_checks_passed"]:
            raise FactorDraftValidationError(diagnostics)
        from .factor_preview import FactorPreviewService

        preview = FactorPreviewService(self.store).assert_current(
            draft.draft_id,
            preview_id=preview_id,
            preview_fingerprint=preview_fingerprint,
        )
        compiled_spec = dict(diagnostics["compiled_factor_spec"])
        definition = DefinitionRegistry(self.store).create(
            "FACTOR",
            compiled_spec,
            state="VALIDATED",
            created_by=draft.created_by,
            owner_project_id=draft.owner_project_id,
            library_scope=draft.library_scope,
        )
        validated_at = utc_now()
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                """
                UPDATE factor_drafts
                SET state='VALIDATED', validated_definition_id=?, validated_at=?, updated_at=?
                WHERE draft_id=? AND state='DRAFT' AND draft_fingerprint=?
                """,
                (
                    definition.definition_id,
                    validated_at,
                    validated_at,
                    draft.draft_id,
                    draft.draft_fingerprint,
                ),
            )
        validated = self.get(draft.draft_id)
        if validated is None or validated.state != "VALIDATED":
            raise ValueError("FACTOR_DRAFT_STALE: factor changed while it was being saved")
        FactorPreviewService(self.store).mark_validated(
            preview["preview_id"],
            definition.definition_id,
        )
        return validated, definition

    def inspect(self, draft_id: str) -> dict[str, Any]:
        draft = self.get(draft_id)
        if draft is None:
            raise ValueError("factor draft not found")
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
                "can_save_factor": False,
                "preview_status": "DISCARDED",
            })
            return result
        if draft.state == "VALIDATED":
            result.update({
                "can_validate": True,
                "preview_status": "VALIDATED",
                "latest_preview_id": draft.latest_preview_id,
                "latest_preview_fingerprint": draft.latest_preview_fingerprint,
            })
            return result
        if (
            draft.latest_preview_id
            and draft.latest_preview_fingerprint
            and draft.previewed_draft_fingerprint == draft.draft_fingerprint
        ):
            from .factor_preview import FactorPreviewError, FactorPreviewService

            try:
                preview = FactorPreviewService(self.store).assert_current(
                    draft.draft_id,
                    preview_id=draft.latest_preview_id,
                    preview_fingerprint=draft.latest_preview_fingerprint,
                )
                result.update({
                    "can_validate": result["definition_checks_passed"],
                    "preview_status": "READY",
                    "latest_preview_id": preview["preview_id"],
                    "latest_preview_fingerprint": preview["preview_fingerprint"],
                    "preview": preview,
                })
            except FactorPreviewError as exc:
                result.update({
                    "can_validate": False,
                    "preview_status": "STALE",
                    "preview_diagnostics": exc.diagnostics,
                })
        return result

    def get(self, draft_id: str) -> FactorDraft | None:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM factor_drafts WHERE draft_id=?",
                (_clean(draft_id),),
            ).fetchone()
        return self._from_row(row) if row else None

    def list(self, *, owner_project_id: str = "", state: str = "", limit: int = 200) -> list[FactorDraft]:
        clauses: list[str] = []
        params: list[Any] = []
        if _clean(owner_project_id):
            clauses.append("owner_project_id=?")
            params.append(_clean(owner_project_id))
        if _clean(state):
            requested_state = _clean(state).upper()
            if requested_state not in FACTOR_DRAFT_STATES:
                raise ValueError(f"unsupported Factor draft state: {requested_state}")
            clauses.append("state=?")
            params.append(requested_state)
        if not _clean(state):
            clauses.append("state!='DISCARDED'")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(int(limit), 1000)))
        with self.store.connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM factor_drafts{where} ORDER BY updated_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [self._from_row(row) for row in rows]

    @staticmethod
    def _from_row(row: Any) -> FactorDraft:
        return FactorDraft(
            draft_id=str(row["draft_id"]),
            owner_project_id=str(row["owner_project_id"] or ""),
            library_scope=str(row["library_scope"] or "GLOBAL"),
            document=json.loads(row["document_json"] or "{}"),
            draft_fingerprint=str(row["draft_fingerprint"]),
            state=str(row["state"]),
            validated_definition_id=str(row["validated_definition_id"] or ""),
            latest_preview_id=str(row["latest_preview_id"] or ""),
            latest_preview_fingerprint=str(row["latest_preview_fingerprint"] or ""),
            previewed_draft_fingerprint=str(row["previewed_draft_fingerprint"] or ""),
            created_by=str(row["created_by"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            validated_at=str(row["validated_at"]) if row["validated_at"] else None,
            previewed_at=str(row["previewed_at"]) if row["previewed_at"] else None,
        )
