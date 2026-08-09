from __future__ import annotations

import hashlib
import json
import math
import statistics
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from .alpha_factor_candidates import AlphaFactorCandidateResolver
from .data_client import FrozenManifestData
from .definition_registry import DefinitionRegistry, ResearchDefinition
from .factor_alpha import AlphaComponent, AlphaEngine, AlphaSpec
from .factor_definition_executor import FactorDefinitionExecutor
from .factor_engine_v4 import FactorGraphSpec
from .factor_preview import FactorPreviewService
from .requirement_compiler import RequirementCompiler
from .store import DataPlatformStore, json_dumps, utc_now
from .universe_service import UniverseService


ALPHA_PREVIEW_SCHEMA_VERSION = "alpha_preview.v1"
ALPHA_PREVIEW_MAX_DAYS = 31
ALPHA_PREVIEW_MAX_VALUE_ROWS = 20_000
_FREQUENCY_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "6h": 21600,
    "8h": 28800,
    "12h": 43200,
    "1d": 86400,
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _parse_time(value: Any) -> datetime:
    text = _clean(value)
    if not text:
        raise ValueError("time value is required")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("time values must include a timezone")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _hash(value: Any) -> str:
    return hashlib.sha256(json_dumps(value).encode("utf-8")).hexdigest()


class AlphaPreviewError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        path: str = "preview",
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.diagnostics = [{
            "level": "ERROR",
            "code": code,
            "path": path,
            "message": message,
        }]


class AlphaPreviewService:
    """Preview one saved Alpha Draft with the same Factor execution as Formal Run."""

    def __init__(self, store: DataPlatformStore):
        self.store = store
        self.registry = DefinitionRegistry(store)
        self.candidates = AlphaFactorCandidateResolver(store)
        self.universes = UniverseService(store)
        self.factor_preview = FactorPreviewService(store)
        self.factor_executor = FactorDefinitionExecutor()

    def context(self, draft_id: str) -> dict[str, Any]:
        draft = self._draft(draft_id)
        inspection, definitions, compiled = self._inspection(draft)
        snapshot, universe_ref = self._snapshot(draft)
        bindings: list[dict[str, Any]] = []
        diagnostics: list[dict[str, Any]] = []
        for definition in definitions:
            for input_spec, history in self._input_specs(definition):
                frequency = _clean(input_spec.get("frequency")).lower()
                if frequency not in _FREQUENCY_SECONDS:
                    diagnostics.append({
                        "level": "ERROR",
                        "code": "ALPHA_PREVIEW_FREQUENCY_UNSUPPORTED",
                        "path": "components",
                        "message": f"Unsupported Preview frequency: {frequency}.",
                    })
                    continue
                for instrument_id in snapshot.actual_instrument_ids:
                    candidates = self.factor_preview._candidate_manifests(
                        instrument_id=instrument_id,
                        dataset=_clean(
                            input_spec.get("dataset") or "bars"
                        ).lower(),
                        frequency=frequency,
                        field=_clean(input_spec.get("field")),
                    )
                    if not candidates:
                        diagnostics.append({
                            "level": "ERROR",
                            "code": "ALPHA_PREVIEW_MANIFEST_MISSING",
                            "path": "components",
                            "message": (
                                f"No READY Manifest covers "
                                f"{definition.name}/{instrument_id}/"
                                f"{input_spec.get('field')}@{frequency}."
                            ),
                        })
                        continue
                    bindings.append(self._binding(
                        definition,
                        input_spec,
                        history,
                        instrument_id,
                        candidates[0],
                    ))
        coverage_start: datetime | None = None
        coverage_end: datetime | None = None
        if bindings:
            starts = [
                _parse_time(item["range"]["start"])
                + timedelta(
                    seconds=_FREQUENCY_SECONDS[item["frequency"]]
                    * max(0, int(item["required_observations"]) - 1)
                )
                for item in bindings
            ]
            ends = [
                _parse_time(item["range"]["end"])
                for item in bindings
            ]
            coverage_start = max(starts)
            coverage_end = min(ends)
            if coverage_start >= coverage_end:
                diagnostics.append({
                    "level": "ERROR",
                    "code": "ALPHA_PREVIEW_RANGE_UNAVAILABLE",
                    "path": "preview.time_range",
                    "message": "Pinned Factor Inputs have no common previewable range.",
                })
        suggested_start = None
        suggested_end = None
        if coverage_start and coverage_end and coverage_start < coverage_end:
            suggested_end = coverage_end
            suggested_start = max(
                coverage_start,
                suggested_end - timedelta(days=7),
            )
        return {
            "schema_version": ALPHA_PREVIEW_SCHEMA_VERSION,
            "can_run_preview": (
                inspection["definition_checks_passed"] and not diagnostics
            ),
            "diagnostics": [
                *inspection.get("diagnostics", []),
                *diagnostics,
            ],
            "draft_id": draft["draft_id"],
            "draft_fingerprint": draft["draft_fingerprint"],
            "dependency_fingerprint": self._dependency_fingerprint(
                definitions
            ),
            "spec_hash": _hash(compiled),
            "engine_version": compiled["engine_version"],
            "code_hash": compiled["code_hash"],
            "universe": {
                "universe_snapshot_id": snapshot.universe_snapshot_id,
                "universe_fingerprint": snapshot.fingerprint,
                "name": universe_ref["name"],
                "as_of_time": snapshot.as_of_time,
                "member_count": len(snapshot.actual_instrument_ids),
                "instrument_ids": list(snapshot.actual_instrument_ids),
            },
            "time_range": {
                "minimum_start": (
                    _iso(coverage_start) if coverage_start else ""
                ),
                "maximum_end": _iso(coverage_end) if coverage_end else "",
                "suggested_start": (
                    _iso(suggested_start) if suggested_start else ""
                ),
                "suggested_end": (
                    _iso(suggested_end) if suggested_end else ""
                ),
                "maximum_days": ALPHA_PREVIEW_MAX_DAYS,
            },
            "candidate_manifest_ids": sorted({
                item["manifest_id"] for item in bindings
            }),
            "input_bindings": bindings,
            "factor_refs": self._factor_refs(definitions),
            "compiled_alpha_spec": compiled,
        }

    def compile_requirements(
        self,
        draft_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        draft = self._draft(draft_id)
        self._assert_mutable_and_current(draft, payload)
        _inspection, definitions, compiled_alpha = self._inspection(draft)
        snapshot, _ = self._snapshot(draft)
        self._assert_snapshot(payload, snapshot)
        start, end = self._range(payload)
        specs = [
            self.factor_executor.spec_for(item).to_dict()
            for item in definitions
        ]
        if not specs:
            raise AlphaPreviewError(
                "ALPHA_COMPONENT_REQUIRED",
                "At least one Factor component is required.",
                path="components",
            )
        compiler = RequirementCompiler(self.store)
        project_id = _clean(draft["owner_project_id"])
        first_inputs = list(self._input_specs(definitions[0]))
        first_input = first_inputs[0][0]
        result = compiler.compile(
            project_id=project_id,
            # Alpha Preview is a scoped, immutable draft contract.  It must
            # never inherit or replace the project's Effective RequirementSet.
            factor_specs=specs,
            alpha_specs=[compiled_alpha],
            context={
                "universe_snapshot_id": snapshot.universe_snapshot_id,
                "instrument_ids": list(snapshot.actual_instrument_ids),
                "data_type": _clean(
                    first_input.get("dataset") or "bars"
                ).lower(),
                "frequency": _clean(first_input.get("frequency")).lower(),
                "history_start": _iso(start),
                "history_end": _iso(end),
                "adjustment": "NONE",
                "time_semantics": (
                    "EVENT_TIME_AVAILABLE_TIME"
                    if _clean(first_input.get("dataset")).lower()
                    == "price_history"
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
            },
        )
        reference = {
            "project_id": project_id,
            "requirement_set_id": result.requirement_set_id,
            "scope": "ALPHA_PREVIEW",
        }
        return {
            "requirement_set": asdict(result),
            "reference": reference,
            "coverage": compiler.coverage(result.requirement_set_id),
            "factor_refs": self._factor_refs(definitions),
            "requirements": [{
                "requirement_id": item.requirement_id,
                "dataset": item.data_type,
                "fields": list(item.fields),
                "frequency": item.frequency,
                "instrument_ids": list(item.instrument_ids),
                "instrument_count": len(item.instrument_ids),
                "evaluation_range": {
                    "start": _iso(start),
                    "end": _iso(end),
                },
                "required_range": {
                    "start": item.history_start,
                    "end": item.history_end,
                },
            } for item in result.requirements],
        }

    def create(
        self,
        draft_id: str,
        payload: dict[str, Any],
        *,
        created_by: str = "local_ui_user",
    ) -> dict[str, Any]:
        draft = self._draft(draft_id)
        self._assert_mutable_and_current(draft, payload)
        _inspection, definitions, compiled = self._inspection(draft)
        snapshot, universe_ref = self._snapshot(draft)
        self._assert_snapshot(payload, snapshot)
        start, end = self._range(payload)
        requirement_result = self.compile_requirements(draft_id, payload)
        requirement_set_id = str(
            requirement_result["requirement_set"]["requirement_set_id"]
        )
        bindings = self._resolve_bindings(
            definitions,
            tuple(snapshot.actual_instrument_ids),
            start,
            end,
        )
        factor_outputs: dict[
            str,
            dict[str, list[dict[str, Any]]],
        ] = {}
        engine_closure: list[dict[str, Any]] = []
        for definition in definitions:
            scoped_bindings = [
                item for item in bindings
                if item["factor_definition_id"]
                == definition.definition_id
            ]
            manifest_inputs, bars = self._load_factor_inputs(
                scoped_bindings,
                end,
            )
            spec, values = self.factor_executor.execute(
                definition,
                manifest_inputs=manifest_inputs,
                bars_by_instrument=bars,
                allowed_instruments=set(snapshot.actual_instrument_ids),
            )
            factor_outputs[definition.name] = values
            engine_closure.append({
                "factor_definition_id": definition.definition_id,
                "factor_version": definition.version,
                "factor_spec_hash": definition.spec_hash,
                "engine_version": definition.engine_version,
                "code_hash": definition.code_hash,
                "computed_spec_hash": spec.spec_hash,
            })
        alpha_spec = AlphaSpec(
            name=compiled["name"],
            version=compiled["version"],
            components=tuple(
                AlphaComponent(
                    factor_name=item["factor_name"],
                    weight=float(item["weight"]),
                    transform=item["transform"],
                    ascending=bool(item["ascending"]),
                )
                for item in compiled["components"]
            ),
            minimum_coverage=float(compiled["minimum_coverage"]),
            universe_snapshot_id=snapshot.universe_snapshot_id,
            minimum_cross_section_size=int(
                compiled["minimum_cross_section_size"]
            ),
            missing_policy=compiled["missing_policy"],
            rank_method=compiled["rank_method"],
            output_scale=compiled["output_scale"],
        )
        signals = AlphaEngine().build_signals(
            alpha_spec,
            factor_outputs,
            universe_snapshot=snapshot,
        )
        values = self._trim_values(signals, start, end)
        if not values:
            raise AlphaPreviewError(
                "ALPHA_PREVIEW_NO_VALUES",
                "The selected range produced no usable Alpha values.",
                path="preview.time_range",
            )
        analysis = self._analysis(values)
        manifest_hashes = {
            item["manifest_id"]: item["manifest_hash"]
            for item in bindings
        }
        dependency_fingerprint = self._dependency_fingerprint(
            definitions
        )
        preview_fingerprint = _hash({
            "schema_version": ALPHA_PREVIEW_SCHEMA_VERSION,
            "draft": {
                "draft_id": draft["draft_id"],
                "draft_fingerprint": draft["draft_fingerprint"],
            },
            "dependency_fingerprint": dependency_fingerprint,
            "universe": {
                "universe_snapshot_id": snapshot.universe_snapshot_id,
                "universe_fingerprint": snapshot.fingerprint,
            },
            "requirement_set_id": requirement_set_id,
            "time_range": {
                "start": _iso(start),
                "end": _iso(end),
            },
            "manifests": [
                {
                    "manifest_id": item,
                    "manifest_hash": manifest_hashes[item],
                }
                for item in sorted(manifest_hashes)
            ],
            "alpha_spec_hash": alpha_spec.spec_hash,
        })
        now = utc_now()
        factor_refs = self._factor_refs(definitions)
        with self.store.transaction(immediate=True) as conn:
            existing = conn.execute(
                """
                SELECT preview_id FROM alpha_previews
                WHERE preview_fingerprint=?
                """,
                (preview_fingerprint,),
            ).fetchone()
            if existing:
                preview_id = str(existing["preview_id"])
            else:
                preview_id = f"alpha_preview_{uuid.uuid4().hex}"
                conn.execute(
                    """
                    INSERT INTO alpha_previews(
                        preview_id,draft_id,project_id,status,
                        draft_fingerprint,dependency_fingerprint,
                        preview_fingerprint,universe_snapshot_id,
                        universe_fingerprint,requirement_set_id,
                        time_start,time_end,factor_refs_json,
                        manifest_ids_json,manifest_hashes_json,
                        input_bindings_json,factor_engine_closure_json,
                        alpha_engine_version,alpha_code_hash,spec_hash,
                        values_json,analysis_json,diagnostics_json,
                        created_by,created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        preview_id,
                        draft["draft_id"],
                        draft["owner_project_id"],
                        "READY",
                        draft["draft_fingerprint"],
                        dependency_fingerprint,
                        preview_fingerprint,
                        snapshot.universe_snapshot_id,
                        snapshot.fingerprint,
                        requirement_set_id,
                        _iso(start),
                        _iso(end),
                        json_dumps(factor_refs),
                        json_dumps(sorted(manifest_hashes)),
                        json_dumps(manifest_hashes),
                        json_dumps(bindings),
                        json_dumps(engine_closure),
                        alpha_spec.engine_version,
                        alpha_spec.code_hash,
                        _hash(compiled),
                        json_dumps(values),
                        json_dumps(analysis),
                        "[]",
                        _clean(created_by) or "local_ui_user",
                        now,
                    ),
                )
            updated = conn.execute(
                """
                UPDATE alpha_drafts
                SET latest_preview_id=?,latest_preview_fingerprint=?,
                    previewed_draft_fingerprint=?,previewed_at=?
                WHERE draft_id=? AND state='DRAFT'
                  AND draft_fingerprint=?
                """,
                (
                    preview_id,
                    preview_fingerprint,
                    draft["draft_fingerprint"],
                    now,
                    draft["draft_id"],
                    draft["draft_fingerprint"],
                ),
            )
            if updated.rowcount != 1:
                raise AlphaPreviewError(
                    "ALPHA_DRAFT_STALE",
                    "The Draft changed while Preview was running.",
                    path="draft_fingerprint",
                )
            from .research_authoring_audit import ResearchAuthoringAudit

            ResearchAuthoringAudit.record(
                conn,
                object_type="ALPHA_PREVIEW",
                object_id=preview_id,
                project_id=draft["owner_project_id"],
                operation="PREVIEW",
                before_fingerprint=draft["draft_fingerprint"],
                after_fingerprint=preview_fingerprint,
                actor_id=_clean(created_by) or "local_ui_user",
            )
        result = self.get(preview_id)
        if result is None:
            raise RuntimeError("failed to save Alpha Preview")
        result["universe_name"] = universe_ref["name"]
        result["compiled_alpha_spec"] = compiled
        return result

    def latest(self, draft_id: str) -> dict[str, Any] | None:
        draft = self._draft(draft_id)
        preview_id = _clean(draft.get("latest_preview_id"))
        return self.get(preview_id) if preview_id else None

    def get(self, preview_id: str) -> dict[str, Any] | None:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM alpha_previews WHERE preview_id=?",
                (_clean(preview_id),),
            ).fetchone()
        return self._row(row) if row else None

    def assert_current(
        self,
        draft_id: str,
        *,
        preview_id: str,
        preview_fingerprint: str,
    ) -> dict[str, Any]:
        draft = self._draft(draft_id)
        preview = self.get(preview_id)
        if preview is None or preview["draft_id"] != draft["draft_id"]:
            raise AlphaPreviewError(
                "ALPHA_PREVIEW_REQUIRED",
                "Run Preview for this saved Draft before validation.",
            )
        if (
            not preview_fingerprint
            or preview["preview_fingerprint"] != preview_fingerprint
        ):
            raise AlphaPreviewError(
                "ALPHA_PREVIEW_FINGERPRINT_MISMATCH",
                "Preview fingerprint does not match the selected Preview.",
            )
        if (
            draft["latest_preview_id"] != preview["preview_id"]
            or draft["latest_preview_fingerprint"]
            != preview["preview_fingerprint"]
            or draft["previewed_draft_fingerprint"]
            != draft["draft_fingerprint"]
            or preview["draft_fingerprint"]
            != draft["draft_fingerprint"]
        ):
            raise AlphaPreviewError(
                "ALPHA_PREVIEW_STALE",
                "The Alpha Draft changed after this Preview.",
            )
        _inspection, definitions, compiled = self._inspection(draft)
        if (
            self._dependency_fingerprint(definitions)
            != preview["dependency_fingerprint"]
        ):
            raise AlphaPreviewError(
                "ALPHA_PREVIEW_STALE",
                "A pinned Factor dependency changed after Preview.",
            )
        snapshot, _ = self._snapshot(draft)
        if (
            snapshot.universe_snapshot_id
            != preview["universe_snapshot_id"]
            or snapshot.fingerprint != preview["universe_fingerprint"]
        ):
            raise AlphaPreviewError(
                "ALPHA_PREVIEW_STALE",
                "The current Universe Snapshot changed after Preview.",
            )
        if _hash(compiled) != preview["compiled_spec_hash"]:
            raise AlphaPreviewError(
                "ALPHA_PREVIEW_STALE",
                "The compiled Alpha changed after Preview.",
            )
        with self.store.connection() as conn:
            requirement = conn.execute(
                """
                SELECT 1 FROM requirement_sets
                WHERE requirement_set_id=?
                """,
                (preview["requirement_set_id"],),
            ).fetchone()
            if requirement is None:
                raise AlphaPreviewError(
                    "ALPHA_PREVIEW_STALE",
                    "Preview Requirement Set is unavailable.",
                )
            for manifest_id, expected_hash in (
                preview["manifest_hashes"].items()
            ):
                manifest = conn.execute(
                    """
                    SELECT status,manifest_hash FROM dataset_manifests
                    WHERE manifest_id=?
                    """,
                    (manifest_id,),
                ).fetchone()
                if (
                    manifest is None
                    or str(manifest["status"]) != "READY"
                    or str(manifest["manifest_hash"])
                    != expected_hash
                ):
                    raise AlphaPreviewError(
                        "ALPHA_PREVIEW_STALE",
                        "Manifest identity changed after Preview: "
                        f"{manifest_id}.",
                    )
        return preview

    def mark_validated(
        self,
        preview_id: str,
        definition_id: str,
    ) -> None:
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                """
                UPDATE alpha_previews
                SET validated_definition_id=?
                WHERE preview_id=? AND validated_definition_id=''
                """,
                (_clean(definition_id), _clean(preview_id)),
            )

    def _inspection(
        self,
        draft: Mapping[str, Any],
    ) -> tuple[
        dict[str, Any],
        list[ResearchDefinition],
        dict[str, Any],
    ]:
        from .alpha_draft import AlphaDraftService

        inspection = AlphaDraftService(
            self.store
        ).inspect_project_document(
            dict(draft["document"]),
            _clean(draft.get("owner_project_id")),
        )
        if not inspection["definition_checks_passed"]:
            first = next(
                (
                    item for item in inspection["diagnostics"]
                    if item["level"] == "ERROR"
                ),
                None,
            )
            raise AlphaPreviewError(
                (
                    first["code"]
                    if first else "ALPHA_PREVIEW_DEFINITION_INVALID"
                ),
                (
                    first["message"]
                    if first else "Fix the Alpha definition before Preview."
                ),
                path=first["path"] if first else "components",
            )
        definitions = []
        for item in inspection["normalized_components"]:
            definition = self.registry.get(
                item["factor_definition_id"],
                version=item["factor_version"],
            )
            if definition is None:
                raise AlphaPreviewError(
                    "ALPHA_FACTOR_NOT_FOUND",
                    "A pinned Factor is unavailable.",
                    path="components",
                )
            if all(
                existing.definition_id != definition.definition_id
                or existing.version != definition.version
                for existing in definitions
            ):
                definitions.append(definition)
        return (
            inspection,
            definitions,
            dict(inspection["compiled_alpha_spec"]),
        )

    def _snapshot(
        self,
        draft: Mapping[str, Any],
    ) -> tuple[Any, dict[str, Any]]:
        project_id = _clean(draft.get("owner_project_id"))
        universe_ref = self.universes.get_research_ref(project_id)
        if universe_ref is None:
            raise AlphaPreviewError(
                "ALPHA_UNIVERSE_REQUIRED",
                "Select a primary Universe before running Preview.",
                path="preview.universe_snapshot_id",
            )
        snapshot = self.universes.get_snapshot(
            universe_ref["universe_snapshot_id"]
        )
        if snapshot is None or not snapshot.actual_instrument_ids:
            raise AlphaPreviewError(
                "ALPHA_UNIVERSE_REQUIRED",
                "The selected Universe Snapshot has no Instruments.",
                path="preview.universe_snapshot_id",
            )
        return snapshot, universe_ref

    def _draft(self, draft_id: str) -> dict[str, Any]:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM alpha_drafts WHERE draft_id=?",
                (_clean(draft_id),),
            ).fetchone()
        if row is None:
            raise ValueError("alpha draft not found")
        result = dict(row)
        result["document"] = json.loads(
            result.pop("document_json") or "{}"
        )
        return result

    def _assert_mutable_and_current(
        self,
        draft: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> None:
        if draft["state"] != "DRAFT":
            raise AlphaPreviewError(
                "ALPHA_PREVIEW_DRAFT_IMMUTABLE",
                "Validated Alpha drafts cannot run a new Preview.",
            )
        expected = _clean(payload.get("expected_fingerprint"))
        if not expected or expected != draft["draft_fingerprint"]:
            raise AlphaPreviewError(
                "ALPHA_DRAFT_STALE",
                "Save the latest Draft before running Preview.",
                path="draft_fingerprint",
            )

    @staticmethod
    def _assert_snapshot(
        payload: Mapping[str, Any],
        snapshot: Any,
    ) -> None:
        requested = _clean(payload.get("universe_snapshot_id"))
        if (
            requested
            and requested != snapshot.universe_snapshot_id
        ):
            raise AlphaPreviewError(
                "ALPHA_PREVIEW_UNIVERSE_STALE",
                "The current Research Universe Snapshot changed.",
                path="preview.universe_snapshot_id",
            )

    @staticmethod
    def _range(
        payload: Mapping[str, Any],
    ) -> tuple[datetime, datetime]:
        start = _parse_time(payload.get("start_time"))
        end = _parse_time(payload.get("end_time"))
        if start >= end:
            raise AlphaPreviewError(
                "ALPHA_PREVIEW_RANGE_INVALID",
                "Preview start time must be earlier than end time.",
                path="preview.time_range",
            )
        if end - start > timedelta(days=ALPHA_PREVIEW_MAX_DAYS):
            raise AlphaPreviewError(
                "ALPHA_PREVIEW_RANGE_TOO_LARGE",
                "Alpha Preview is limited to "
                f"{ALPHA_PREVIEW_MAX_DAYS} days.",
                path="preview.time_range",
            )
        return start, end

    def _input_specs(
        self,
        definition: ResearchDefinition,
    ) -> list[tuple[dict[str, Any], int]]:
        spec = self.factor_executor.spec_for(definition)
        if isinstance(spec, FactorGraphSpec):
            return [
                (
                    dict(item),
                    int(
                        spec.required_history.get(
                            _clean(item.get("variable_name")),
                            1,
                        )
                    ),
                )
                for item in spec.inputs
            ]
        return [({
            "variable_name": "value",
            "dataset": "bars",
            "field": spec.input_field,
            "frequency": spec.frequency,
        }, spec.required_observations)]

    @staticmethod
    def _binding(
        definition: ResearchDefinition,
        input_spec: Mapping[str, Any],
        history: int,
        instrument_id: str,
        row: Any,
    ) -> dict[str, Any]:
        return {
            "factor_definition_id": definition.definition_id,
            "factor_version": definition.version,
            "variable_name": _clean(input_spec.get("variable_name")),
            "dataset": _clean(
                input_spec.get("dataset") or "bars"
            ).lower(),
            "field": _clean(input_spec.get("field")),
            "frequency": _clean(input_spec.get("frequency")).lower(),
            "required_observations": int(history),
            "instrument_id": _clean(instrument_id),
            "dataset_id": str(row["dataset_id"]),
            "manifest_id": str(row["manifest_id"]),
            "manifest_hash": str(row["manifest_hash"]),
            "source": str(row["source"]),
            "range": {
                "start": str(row["partition_start"]),
                "end": str(row["partition_end"]),
            },
        }

    def _resolve_bindings(
        self,
        definitions: Sequence[ResearchDefinition],
        instrument_ids: tuple[str, ...],
        start: datetime,
        end: datetime,
    ) -> list[dict[str, Any]]:
        bindings: list[dict[str, Any]] = []
        verified: set[str] = set()
        for definition in definitions:
            for input_spec, history in self._input_specs(definition):
                frequency = _clean(
                    input_spec.get("frequency")
                ).lower()
                seconds = _FREQUENCY_SECONDS.get(frequency)
                if seconds is None:
                    raise AlphaPreviewError(
                        "ALPHA_PREVIEW_FREQUENCY_UNSUPPORTED",
                        f"Unsupported Preview frequency: {frequency}.",
                        path="components",
                    )
                required_start = start - timedelta(
                    seconds=seconds * max(0, history - 1)
                )
                event_tolerance = (
                    timedelta(seconds=seconds)
                    if _clean(input_spec.get("dataset")).lower()
                    == "price_history"
                    else timedelta(0)
                )
                for instrument_id in instrument_ids:
                    candidates = self.factor_preview._candidate_manifests(
                        instrument_id=instrument_id,
                        dataset=_clean(
                            input_spec.get("dataset") or "bars"
                        ).lower(),
                        frequency=frequency,
                        field=_clean(input_spec.get("field")),
                    )
                    selected = next(
                        (
                            item for item in candidates
                            if _parse_time(item["partition_start"])
                            <= required_start + event_tolerance
                            and _parse_time(item["partition_end"])
                            >= end - event_tolerance
                        ),
                        None,
                    )
                    if selected is None:
                        raise AlphaPreviewError(
                            "ALPHA_PREVIEW_RANGE_NOT_COVERED",
                            (
                                f"No READY Manifest covers "
                                f"{definition.name}/{instrument_id} "
                                f"from {_iso(required_start)} "
                                f"through {_iso(end)}."
                            ),
                            path="components",
                        )
                    manifest_id = str(selected["manifest_id"])
                    if manifest_id not in verified:
                        try:
                            FrozenManifestData(
                                self.store,
                                manifest_id,
                            ).verify()
                        except Exception as exc:
                            raise AlphaPreviewError(
                                "ALPHA_PREVIEW_MANIFEST_DAMAGED",
                                "Manifest physical verification failed: "
                                f"{manifest_id}: {exc}",
                                path="components",
                            ) from exc
                        verified.add(manifest_id)
                    bindings.append(self._binding(
                        definition,
                        input_spec,
                        history,
                        instrument_id,
                        selected,
                    ))
        return bindings

    def _load_factor_inputs(
        self,
        bindings: Sequence[Mapping[str, Any]],
        end: datetime,
    ) -> tuple[
        list[dict[str, Any]],
        dict[str, list[dict[str, Any]]],
    ]:
        manifest_inputs: list[dict[str, Any]] = []
        bars: dict[str, list[dict[str, Any]]] = {}
        for manifest_id in sorted({
            _clean(item.get("manifest_id")) for item in bindings
        }):
            frozen = FrozenManifestData(self.store, manifest_id)
            rows = frozen.read_bars_by_instrument(as_of=_iso(end))
            catalog = frozen.catalog.get_catalog(frozen.dataset_id)
            if catalog is None:
                raise AlphaPreviewError(
                    "ALPHA_PREVIEW_MANIFEST_DAMAGED",
                    f"Manifest catalog is unavailable: {manifest_id}.",
                    path="components",
                )
            manifest_inputs.append({
                "manifest_id": manifest_id,
                "frequency": catalog.frequency,
                "fields": set(catalog.fields),
                "rows": rows,
            })
            for instrument_id, source_rows in rows.items():
                bars.setdefault(instrument_id, []).extend(
                    dict(item) for item in source_rows
                )
        for instrument_id, rows in bars.items():
            deduplicated = {
                _clean(
                    item.get("bar_start_time")
                    or item.get("event_time")
                ): item
                for item in rows
            }
            bars[instrument_id] = [
                deduplicated[key] for key in sorted(deduplicated)
            ]
        return manifest_inputs, bars

    @staticmethod
    def _trim_values(
        signals: Sequence[Mapping[str, Any]],
        start: datetime,
        end: datetime,
    ) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        for signal in signals:
            as_of = _parse_time(signal.get("as_of_time"))
            if not start <= as_of <= end:
                continue
            raw_scores = dict(signal.get("raw_scores") or {})
            ranks = dict(signal.get("ranks") or {})
            percentiles = dict(signal.get("percentiles") or {})
            for instrument_id in sorted(raw_scores):
                values.append({
                    "instrument_id": instrument_id,
                    "as_of_time": _iso(as_of),
                    "available_time": _clean(
                        signal.get("available_time")
                    ),
                    "raw_score": raw_scores[instrument_id],
                    "rank": ranks.get(instrument_id),
                    "percentile": percentiles.get(instrument_id),
                    "coverage": signal.get("coverage"),
                    "quality_status": signal.get(
                        "quality_status",
                        "PASS",
                    ),
                })
        if len(values) > ALPHA_PREVIEW_MAX_VALUE_ROWS:
            raise AlphaPreviewError(
                "ALPHA_PREVIEW_RESULT_TOO_LARGE",
                f"Preview produced {len(values)} rows; reduce the range.",
                path="preview.time_range",
            )
        return values

    @staticmethod
    def _analysis(values: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        numeric = [
            float(item["raw_score"])
            for item in values
            if isinstance(item.get("raw_score"), (int, float))
            and math.isfinite(float(item["raw_score"]))
        ]
        latest_time = max(
            (_clean(item.get("as_of_time")) for item in values),
            default="",
        )
        latest = [
            dict(item)
            for item in values
            if _clean(item.get("as_of_time")) == latest_time
        ]
        latest.sort(
            key=lambda item: (
                float(item.get("rank") or math.inf),
                str(item.get("instrument_id")),
            )
        )
        coverage_values = [
            float(item["coverage"])
            for item in values
            if isinstance(item.get("coverage"), (int, float))
        ]
        return {
            "overall": {
                "row_count": len(values),
                "valid_value_count": len(numeric),
                "time_point_count": len({
                    item["as_of_time"] for item in values
                }),
                "instrument_count": len({
                    item["instrument_id"] for item in values
                }),
                "minimum": min(numeric) if numeric else None,
                "maximum": max(numeric) if numeric else None,
                "mean": (
                    statistics.fmean(numeric) if numeric else None
                ),
                "standard_deviation": (
                    statistics.pstdev(numeric)
                    if len(numeric) > 1
                    else (0.0 if numeric else None)
                ),
                "minimum_coverage": (
                    min(coverage_values)
                    if coverage_values else None
                ),
            },
            "latest_cross_section": latest,
        }

    @staticmethod
    def _dependency_fingerprint(
        definitions: Sequence[ResearchDefinition],
    ) -> str:
        return _hash(sorted(
            [{
                "factor_definition_id": item.definition_id,
                "factor_version": item.version,
                "factor_spec_hash": item.spec_hash,
                "engine_version": item.engine_version,
                "code_hash": item.code_hash,
            } for item in definitions],
            key=lambda item: (
                item["factor_definition_id"],
                item["factor_version"],
            ),
        ))

    @staticmethod
    def _factor_refs(
        definitions: Sequence[ResearchDefinition],
    ) -> list[dict[str, Any]]:
        return [{
            "factor_definition_id": item.definition_id,
            "factor_version": item.version,
            "factor_spec_hash": item.spec_hash,
            "factor_name": item.name,
            "engine_version": item.engine_version,
            "code_hash": item.code_hash,
        } for item in definitions]

    @staticmethod
    def _row(row: Any) -> dict[str, Any]:
        return {
            "schema_version": ALPHA_PREVIEW_SCHEMA_VERSION,
            "preview_id": str(row["preview_id"]),
            "draft_id": str(row["draft_id"]),
            "project_id": str(row["project_id"]),
            "status": str(row["status"]),
            "draft_fingerprint": str(row["draft_fingerprint"]),
            "dependency_fingerprint": str(
                row["dependency_fingerprint"]
            ),
            "preview_fingerprint": str(row["preview_fingerprint"]),
            "universe_snapshot_id": str(
                row["universe_snapshot_id"]
            ),
            "universe_fingerprint": str(
                row["universe_fingerprint"]
            ),
            "requirement_set_id": str(row["requirement_set_id"]),
            "time_range": {
                "start": str(row["time_start"]),
                "end": str(row["time_end"]),
            },
            "factor_refs": json.loads(
                row["factor_refs_json"] or "[]"
            ),
            "manifest_ids": json.loads(
                row["manifest_ids_json"] or "[]"
            ),
            "manifest_hashes": json.loads(
                row["manifest_hashes_json"] or "{}"
            ),
            "input_bindings": json.loads(
                row["input_bindings_json"] or "[]"
            ),
            "factor_engine_closure": json.loads(
                row["factor_engine_closure_json"] or "[]"
            ),
            "engine_version": str(row["alpha_engine_version"]),
            "code_hash": str(row["alpha_code_hash"]),
            "spec_hash": str(row["spec_hash"]),
            "compiled_spec_hash": str(row["spec_hash"]),
            "values": json.loads(row["values_json"] or "[]"),
            "analysis": json.loads(row["analysis_json"] or "{}"),
            "diagnostics": json.loads(
                row["diagnostics_json"] or "[]"
            ),
            "validated_definition_id": str(
                row["validated_definition_id"] or ""
            ),
            "created_by": str(row["created_by"]),
            "created_at": str(row["created_at"]),
        }
