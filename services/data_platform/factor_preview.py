from __future__ import annotations

import hashlib
import json
import math
import statistics
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from .data_client import FrozenManifestData
from .factor_engine_v4 import FactorEngineV4, FactorGraphSpec
from .input_candidate_resolver import FactorInputCandidateResolver
from .requirement_compiler import RequirementCompiler
from .store import DataPlatformStore, json_dumps, utc_now
from .universe_service import UniverseService


FACTOR_PREVIEW_SCHEMA_VERSION = "factor_preview.v1"
FACTOR_PREVIEW_MAX_DAYS = 31
FACTOR_PREVIEW_MAX_VALUE_ROWS = 20_000
_CANONICAL_BAR_FIELDS = {
    "open", "high", "low", "close", "volume", "quote_volume", "turnover",
    "trade_count",
}
_FREQUENCY_SECONDS = {
    "1m": 60,
    "5m": 5 * 60,
    "15m": 15 * 60,
    "1h": 60 * 60,
    "4h": 4 * 60 * 60,
    "1d": 24 * 60 * 60,
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


def _hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json_dumps(dict(value)).encode("utf-8")).hexdigest()


class FactorPreviewError(ValueError):
    def __init__(self, code: str, message: str, *, path: str = "preview") -> None:
        super().__init__(f"{code}: {message}")
        self.diagnostics = [{
            "level": "ERROR",
            "code": code,
            "path": path,
            "message": message,
        }]


class FactorPreviewService:
    """Run and preserve point-in-time-safe values for one saved Factor Draft."""

    def __init__(self, store: DataPlatformStore):
        self.store = store
        self.universes = UniverseService(store)
        self.input_candidates = FactorInputCandidateResolver(store)

    def context(self, draft_id: str) -> dict[str, Any]:
        draft = self._draft(draft_id)
        compilation, spec = self._compilation(draft)
        snapshot, universe_ref = self._snapshot(draft)
        bindings: list[dict[str, Any]] = []
        diagnostics: list[dict[str, Any]] = []
        selected_candidates: dict[str, Any] | None = None
        try:
            selected_candidates = self.input_candidates.assert_inputs_selectable(
                _clean(draft.get("owner_project_id")),
                spec.inputs,
            )
        except ValueError as exc:
            diagnostics.append({
                "level": "ERROR",
                "code": "FACTOR_INPUT_CANDIDATE_UNAVAILABLE",
                "path": "inputs",
                "message": str(exc).split(": ", 1)[-1],
            })
        for input_spec in spec.inputs:
            variable_name = _clean(input_spec.get("variable_name"))
            dataset = _clean(input_spec.get("dataset") or "bars").lower()
            frequency = _clean(input_spec.get("frequency"))
            field = _clean(input_spec.get("field"))
            for instrument_id in snapshot.actual_instrument_ids:
                candidates = self._candidate_manifests(
                    instrument_id=instrument_id,
                    dataset=dataset,
                    frequency=frequency,
                    field=field,
                )
                if not candidates:
                    diagnostics.append({
                        "level": "ERROR",
                        "code": "FACTOR_PREVIEW_MANIFEST_MISSING",
                        "path": f"inputs.{variable_name}",
                        "message": (
                            f"No READY {dataset} Manifest covers {instrument_id}, "
                            f"{field}, and {frequency}."
                        ),
                    })
                    continue
                selected = candidates[0]
                bindings.append(self._binding(input_spec, instrument_id, selected))
        coverage_start: datetime | None = None
        coverage_end: datetime | None = None
        if bindings:
            valid_starts: list[datetime] = []
            ends: list[datetime] = []
            for binding in bindings:
                frequency = binding["frequency"]
                history = int(spec.required_history.get(binding["variable_name"], 1))
                start = _parse_time(binding["range"]["start"])
                valid_starts.append(
                    start + timedelta(
                        seconds=_FREQUENCY_SECONDS[frequency] * max(0, history - 1)
                    )
                )
                ends.append(_parse_time(binding["range"]["end"]))
            coverage_start = max(valid_starts)
            coverage_end = min(ends)
            if coverage_start >= coverage_end:
                diagnostics.append({
                    "level": "ERROR",
                    "code": "FACTOR_PREVIEW_RANGE_UNAVAILABLE",
                    "path": "preview.time_range",
                    "message": "The selected Inputs do not have one common previewable time range.",
                })
        suggested_start = None
        suggested_end = None
        if coverage_start and coverage_end and coverage_start < coverage_end:
            suggested_end = coverage_end
            suggested_start = max(coverage_start, suggested_end - timedelta(days=7))
        return {
            "schema_version": FACTOR_PREVIEW_SCHEMA_VERSION,
            "can_run_preview": not diagnostics,
            "diagnostics": diagnostics,
            "draft_id": draft["draft_id"],
            "draft_fingerprint": draft["draft_fingerprint"],
            "spec_hash": spec.spec_hash,
            "engine_version": spec.engine_version,
            "code_hash": spec.code_hash,
            "universe": {
                "universe_snapshot_id": snapshot.universe_snapshot_id,
                "universe_fingerprint": snapshot.fingerprint,
                "name": universe_ref["name"],
                "as_of_time": snapshot.as_of_time,
                "member_count": len(snapshot.actual_instrument_ids),
                "instrument_ids": list(snapshot.actual_instrument_ids),
            },
            "time_range": {
                "minimum_start": _iso(coverage_start) if coverage_start else "",
                "maximum_end": _iso(coverage_end) if coverage_end else "",
                "suggested_start": _iso(suggested_start) if suggested_start else "",
                "suggested_end": _iso(suggested_end) if suggested_end else "",
                "maximum_days": FACTOR_PREVIEW_MAX_DAYS,
            },
            "candidate_manifest_ids": sorted({
                binding["manifest_id"] for binding in bindings
            }),
            "input_bindings": bindings,
            "input_candidate_fingerprint": (
                selected_candidates["candidate_fingerprint"]
                if selected_candidates else ""
            ),
            "selected_input_candidates": (
                selected_candidates["selected_inputs"]
                if selected_candidates else []
            ),
            "compiled_formula": compilation,
        }

    def compile_requirements(
        self,
        draft_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        draft = self._draft(draft_id)
        if draft["state"] != "DRAFT":
            raise FactorPreviewError(
                "FACTOR_PREVIEW_DRAFT_IMMUTABLE",
                "Validated Factor drafts cannot create new Preview Requirements.",
            )
        expected = _clean(payload.get("expected_fingerprint"))
        if not expected or expected != draft["draft_fingerprint"]:
            raise FactorPreviewError(
                "FACTOR_DRAFT_STALE",
                "Save the latest Draft before generating Preview Requirements.",
                path="draft_fingerprint",
            )
        _compilation, spec = self._compilation(draft)
        snapshot, _universe_ref = self._snapshot(draft)
        requested_snapshot = _clean(payload.get("universe_snapshot_id"))
        if requested_snapshot and requested_snapshot != snapshot.universe_snapshot_id:
            raise FactorPreviewError(
                "FACTOR_PREVIEW_UNIVERSE_STALE",
                "The current Research Universe Snapshot changed. Refresh Preview settings.",
                path="preview.universe_snapshot_id",
            )
        start = _parse_time(payload.get("start_time"))
        end = _parse_time(payload.get("end_time"))
        if start >= end:
            raise FactorPreviewError(
                "FACTOR_PREVIEW_RANGE_INVALID",
                "Preview start time must be earlier than end time.",
                path="preview.time_range",
            )
        if end - start > timedelta(days=FACTOR_PREVIEW_MAX_DAYS):
            raise FactorPreviewError(
                "FACTOR_PREVIEW_RANGE_TOO_LARGE",
                f"Factor Preview is limited to {FACTOR_PREVIEW_MAX_DAYS} days.",
                path="preview.time_range",
            )
        selected_candidates = self.input_candidates.assert_inputs_selectable(
            _clean(draft.get("owner_project_id")),
            spec.inputs,
        )
        project_id = _clean(draft.get("owner_project_id"))
        compiler = RequirementCompiler(self.store)
        first_input = dict(spec.inputs[0])
        result = compiler.compile(
            project_id=project_id,
            # A Draft Preview is deliberately isolated from the project's
            # canonical Effective RequirementSet.  It contains exactly this
            # draft Factor and the current immutable Universe Snapshot.
            factor_specs=[spec.to_dict()],
            context={
                "universe_snapshot_id": snapshot.universe_snapshot_id,
                "instrument_ids": list(snapshot.actual_instrument_ids),
                "data_type": _clean(first_input.get("dataset") or "bars"),
                "frequency": _clean(first_input.get("frequency")),
                "history_start": _iso(start),
                "history_end": _iso(end),
                "adjustment": "NONE",
                "time_semantics": (
                    "EVENT_TIME_AVAILABLE_TIME"
                    if _clean(first_input.get("dataset")).lower() == "price_history"
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
            "scope": "FACTOR_PREVIEW",
        }
        requirement_rows = [{
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
            "additional_history": {
                "observations": max(0, int(item.lookback_value or 1) - 1),
                "unit": "OBSERVATIONS",
            },
        } for item in result.requirements]
        return {
            "requirement_set": asdict(result),
            "reference": reference,
            "coverage": compiler.coverage(result.requirement_set_id),
            "input_candidate_fingerprint": selected_candidates["candidate_fingerprint"],
            "selected_input_candidates": selected_candidates["selected_inputs"],
            "requirements": requirement_rows,
        }

    def create(
        self,
        draft_id: str,
        payload: dict[str, Any],
        *,
        created_by: str = "local_ui_user",
    ) -> dict[str, Any]:
        draft = self._draft(draft_id)
        if draft["state"] != "DRAFT":
            raise FactorPreviewError(
                "FACTOR_PREVIEW_DRAFT_IMMUTABLE",
                "Validated Factor drafts cannot run a new Preview.",
            )
        expected = _clean(payload.get("expected_fingerprint"))
        if not expected or expected != draft["draft_fingerprint"]:
            raise FactorPreviewError(
                "FACTOR_DRAFT_STALE",
                "Save the latest Draft before running Preview.",
                path="draft_fingerprint",
            )
        compilation, spec = self._compilation(draft)
        snapshot, universe_ref = self._snapshot(draft)
        requested_snapshot = _clean(payload.get("universe_snapshot_id"))
        if requested_snapshot and requested_snapshot != snapshot.universe_snapshot_id:
            raise FactorPreviewError(
                "FACTOR_PREVIEW_UNIVERSE_STALE",
                "The current Research Universe Snapshot changed. Refresh Preview settings.",
                path="preview.universe_snapshot_id",
            )
        start = _parse_time(payload.get("start_time"))
        end = _parse_time(payload.get("end_time"))
        if start >= end:
            raise FactorPreviewError(
                "FACTOR_PREVIEW_RANGE_INVALID",
                "Preview start time must be earlier than end time.",
                path="preview.time_range",
            )
        if end - start > timedelta(days=FACTOR_PREVIEW_MAX_DAYS):
            raise FactorPreviewError(
                "FACTOR_PREVIEW_RANGE_TOO_LARGE",
                f"Factor Preview is limited to {FACTOR_PREVIEW_MAX_DAYS} days.",
                path="preview.time_range",
            )
        try:
            self.compile_requirements(draft_id, payload)
        except ValueError as exc:
            if isinstance(exc, FactorPreviewError):
                raise
            raise FactorPreviewError(
                "FACTOR_INPUT_CANDIDATE_UNAVAILABLE",
                str(exc).split(": ", 1)[-1],
                path="inputs",
            ) from exc
        bindings = self._resolve_bindings(spec, snapshot.actual_instrument_ids, start, end)
        rows_by_input = self._load_inputs(spec, bindings, start, end)
        outputs = FactorEngineV4().compute(spec, rows_by_input)
        values = self._trim_values(outputs, start, end)
        analysis = self._analysis(values, spec)
        if not values or not analysis["overall"]["valid_value_count"]:
            raise FactorPreviewError(
                "FACTOR_PREVIEW_NO_VALUES",
                "The selected range produced no usable Factor values.",
                path="preview.time_range",
            )
        missing_instruments = [
            item["instrument_id"]
            for item in analysis["by_instrument"]
            if item["valid_value_count"] == 0
        ]
        if missing_instruments:
            raise FactorPreviewError(
                "FACTOR_PREVIEW_INSTRUMENT_COVERAGE",
                "No usable Factor value was produced for: " + ", ".join(missing_instruments),
                path="preview.universe_snapshot_id",
            )
        manifest_hashes = {
            binding["manifest_id"]: binding["manifest_hash"]
            for binding in bindings
        }
        fingerprint_material = {
            "schema_version": FACTOR_PREVIEW_SCHEMA_VERSION,
            "draft": {
                "draft_id": draft["draft_id"],
                "draft_fingerprint": draft["draft_fingerprint"],
            },
            "definition": {
                "spec_hash": spec.spec_hash,
                "engine_version": spec.engine_version,
                "code_hash": spec.code_hash,
            },
            "universe": {
                "universe_snapshot_id": snapshot.universe_snapshot_id,
                "universe_fingerprint": snapshot.fingerprint,
                "instrument_ids": list(snapshot.actual_instrument_ids),
            },
            "time_range": {
                "start": _iso(start),
                "end": _iso(end),
            },
            "manifests": [
                {"manifest_id": item, "manifest_hash": manifest_hashes[item]}
                for item in sorted(manifest_hashes)
            ],
        }
        preview_fingerprint = _hash(fingerprint_material)
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            existing = conn.execute(
                "SELECT preview_id FROM factor_previews WHERE preview_fingerprint=?",
                (preview_fingerprint,),
            ).fetchone()
            if existing:
                preview_id = str(existing["preview_id"])
            else:
                preview_id = f"factor_preview_{uuid.uuid4().hex}"
                conn.execute(
                    """
                    INSERT INTO factor_previews(
                        preview_id,draft_id,project_id,status,draft_fingerprint,
                        preview_fingerprint,universe_snapshot_id,universe_fingerprint,
                        time_start,time_end,manifest_ids_json,manifest_hashes_json,
                        input_bindings_json,engine_version,code_hash,spec_hash,
                        values_json,analysis_json,diagnostics_json,created_by,created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        preview_id,
                        draft["draft_id"],
                        draft["owner_project_id"],
                        "READY",
                        draft["draft_fingerprint"],
                        preview_fingerprint,
                        snapshot.universe_snapshot_id,
                        snapshot.fingerprint,
                        _iso(start),
                        _iso(end),
                        json_dumps(sorted(manifest_hashes)),
                        json_dumps(manifest_hashes),
                        json_dumps(bindings),
                        spec.engine_version,
                        spec.code_hash,
                        spec.spec_hash,
                        json_dumps(values),
                        json_dumps(analysis),
                        "[]",
                        _clean(created_by) or "local_ui_user",
                        now,
                    ),
                )
            updated = conn.execute(
                """
                UPDATE factor_drafts
                SET latest_preview_id=?, latest_preview_fingerprint=?,
                    previewed_draft_fingerprint=?, previewed_at=?
                WHERE draft_id=? AND state='DRAFT' AND draft_fingerprint=?
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
                raise FactorPreviewError(
                    "FACTOR_DRAFT_STALE",
                    "The Draft changed while Preview was running.",
                    path="draft_fingerprint",
                )
        result = self.get(preview_id)
        if result is None:
            raise RuntimeError("failed to save Factor Preview")
        result["universe_name"] = universe_ref["name"]
        result["compiled_formula"] = compilation
        return result

    def latest(self, draft_id: str) -> dict[str, Any] | None:
        draft = self._draft(draft_id)
        preview_id = _clean(draft.get("latest_preview_id"))
        return self.get(preview_id) if preview_id else None

    def get(self, preview_id: str) -> dict[str, Any] | None:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM factor_previews WHERE preview_id=?",
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
            raise FactorPreviewError(
                "FACTOR_PREVIEW_REQUIRED",
                "Run Preview for this saved Draft before validation.",
            )
        if not preview_fingerprint or preview["preview_fingerprint"] != preview_fingerprint:
            raise FactorPreviewError(
                "FACTOR_PREVIEW_FINGERPRINT_MISMATCH",
                "Preview fingerprint does not match the selected Preview.",
            )
        if (
            draft["latest_preview_id"] != preview["preview_id"]
            or draft["latest_preview_fingerprint"] != preview["preview_fingerprint"]
            or draft["previewed_draft_fingerprint"] != draft["draft_fingerprint"]
            or preview["draft_fingerprint"] != draft["draft_fingerprint"]
        ):
            raise FactorPreviewError(
                "FACTOR_PREVIEW_STALE",
                "The Draft changed after Preview. Run Preview again.",
            )
        _, spec = self._compilation(draft)
        if (
            spec.spec_hash != preview["spec_hash"]
            or spec.engine_version != preview["engine_version"]
            or spec.code_hash != preview["code_hash"]
        ):
            raise FactorPreviewError(
                "FACTOR_PREVIEW_STALE",
                "The compiled Factor or Engine changed after Preview.",
            )
        snapshot, _ = self._snapshot(draft)
        if (
            snapshot.universe_snapshot_id != preview["universe_snapshot_id"]
            or snapshot.fingerprint != preview["universe_fingerprint"]
        ):
            raise FactorPreviewError(
                "FACTOR_PREVIEW_STALE",
                "The current Universe Snapshot changed after Preview.",
            )
        with self.store.connection() as conn:
            for manifest_id, expected_hash in preview["manifest_hashes"].items():
                manifest = conn.execute(
                    "SELECT status,manifest_hash FROM dataset_manifests WHERE manifest_id=?",
                    (manifest_id,),
                ).fetchone()
                if (
                    manifest is None
                    or str(manifest["status"]) != "READY"
                    or str(manifest["manifest_hash"]) != expected_hash
                ):
                    raise FactorPreviewError(
                        "FACTOR_PREVIEW_STALE",
                        f"Manifest identity changed after Preview: {manifest_id}.",
                    )
        return preview

    def mark_validated(self, preview_id: str, definition_id: str) -> None:
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                """
                UPDATE factor_previews
                SET validated_definition_id=?
                WHERE preview_id=? AND validated_definition_id=''
                """,
                (_clean(definition_id), _clean(preview_id)),
            )

    def _compilation(self, draft: Mapping[str, Any]) -> tuple[dict[str, Any], FactorGraphSpec]:
        from .factor_draft import FactorDraftService

        diagnostics = FactorDraftService.inspect_document(dict(draft["document"]))
        if not diagnostics["can_preview"]:
            first = next(
                (
                    item for item in diagnostics["diagnostics"]
                    if item["level"] == "ERROR"
                ),
                None,
            )
            raise FactorPreviewError(
                first["code"] if first else "FACTOR_PREVIEW_DEFINITION_INVALID",
                first["message"] if first else "Fix the Factor definition before Preview.",
                path=first["path"] if first else "formula.source",
            )
        compilation = dict(diagnostics["compiled_formula"])
        return compilation, FactorGraphSpec.from_dict(
            dict(diagnostics["compiled_factor_spec"])
        )

    def _snapshot(self, draft: Mapping[str, Any]) -> tuple[Any, dict[str, Any]]:
        project_id = _clean(draft.get("owner_project_id"))
        if not project_id:
            raise FactorPreviewError(
                "FACTOR_PREVIEW_PROJECT_REQUIRED",
                "Factor Preview requires a Research-scoped Draft.",
            )
        universe_ref = self.universes.get_research_ref(project_id)
        if universe_ref is None:
            raise FactorPreviewError(
                "FACTOR_PREVIEW_UNIVERSE_REQUIRED",
                "Select a primary Universe before running Preview.",
                path="preview.universe_snapshot_id",
            )
        snapshot = self.universes.get_snapshot(universe_ref["universe_snapshot_id"])
        if snapshot is None or not snapshot.actual_instrument_ids:
            raise FactorPreviewError(
                "FACTOR_PREVIEW_UNIVERSE_REQUIRED",
                "The selected Universe Snapshot has no Instruments.",
                path="preview.universe_snapshot_id",
            )
        return snapshot, universe_ref

    def _draft(self, draft_id: str) -> dict[str, Any]:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM factor_drafts WHERE draft_id=?",
                (_clean(draft_id),),
            ).fetchone()
        if row is None:
            raise ValueError("factor draft not found")
        result = dict(row)
        result["document"] = json.loads(result.pop("document_json") or "{}")
        return result

    def _candidate_manifests(
        self,
        *,
        instrument_id: str,
        dataset: str,
        frequency: str,
        field: str,
    ) -> list[Any]:
        with self.store.connection() as conn:
            rows = conn.execute(
                """
                SELECT c.*,m.manifest_id,m.manifest_version,m.manifest_hash,
                       m.schema_version AS manifest_schema_version,
                       MIN(p.start_time) AS partition_start,
                       MAX(p.end_time) AS partition_end,
                       SUM(CASE WHEN p.quality_status!='PASS' THEN 1 ELSE 0 END)
                           AS bad_partitions
                FROM dataset_catalog c
                JOIN dataset_manifests m ON m.dataset_id=c.dataset_id
                JOIN dataset_partitions p ON p.manifest_id=m.manifest_id
                WHERE c.instrument_id=? AND lower(c.data_type)=lower(?)
                  AND lower(c.frequency)=lower(?)
                  AND c.status='READY' AND m.status='READY'
                GROUP BY m.manifest_id
                """,
                (_clean(instrument_id), _clean(dataset), _clean(frequency)),
            ).fetchall()
        eligible = []
        for row in rows:
            fields = set(json.loads(row["fields_json"] or "[]"))
            if not fields and str(row["manifest_schema_version"]).lower() == "bars.v1":
                fields = set(_CANONICAL_BAR_FIELDS)
            if not fields and str(row["manifest_schema_version"]).lower() == "polymarket_price.v1":
                fields = {"price"}
            if field not in fields:
                continue
            expected_time_semantics = (
                "EVENT_TIME_AVAILABLE_TIME"
                if _clean(dataset).lower() == "price_history"
                else "BAR_END_AVAILABLE_TIME"
            )
            if (
                str(row["quality_status"]).upper() != "PASS"
                or int(row["gap_count"] or 0) > 0
                or int(row["bad_partitions"] or 0) > 0
                or str(row["adjustment"]).upper() != "NONE"
                or str(row["point_in_time_policy"]).upper() != "AS_OF"
                or str(row["time_semantics"]).upper() != expected_time_semantics
            ):
                continue
            if not row["partition_start"] or not row["partition_end"]:
                continue
            eligible.append(row)
        return sorted(
            eligible,
            key=lambda row: (
                -int(row["manifest_version"]),
                str(row["source"]).lower(),
                str(row["dataset_id"]),
                str(row["manifest_id"]),
            ),
        )

    @staticmethod
    def _binding(input_spec: Mapping[str, Any], instrument_id: str, row: Any) -> dict[str, Any]:
        return {
            "variable_name": _clean(input_spec.get("variable_name")),
            "dataset": _clean(input_spec.get("dataset") or "bars").lower(),
            "field": _clean(input_spec.get("field")),
            "frequency": _clean(input_spec.get("frequency")),
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
        spec: FactorGraphSpec,
        instrument_ids: tuple[str, ...],
        start: datetime,
        end: datetime,
    ) -> list[dict[str, Any]]:
        bindings: list[dict[str, Any]] = []
        verified: set[str] = set()
        for input_spec in spec.inputs:
            variable_name = _clean(input_spec.get("variable_name"))
            dataset = _clean(input_spec.get("dataset") or "bars").lower()
            frequency = _clean(input_spec.get("frequency"))
            field = _clean(input_spec.get("field"))
            history = int(spec.required_history.get(variable_name, 1))
            required_start = start - timedelta(
                seconds=_FREQUENCY_SECONDS[frequency] * max(0, history - 1)
            )
            event_tolerance = (
                timedelta(seconds=_FREQUENCY_SECONDS[frequency])
                if dataset == "price_history"
                else timedelta(0)
            )
            for instrument_id in instrument_ids:
                candidates = self._candidate_manifests(
                    instrument_id=instrument_id,
                    dataset=dataset,
                    frequency=frequency,
                    field=field,
                )
                selected = next(
                    (
                        row for row in candidates
                        if _parse_time(row["partition_start"]) <= required_start + event_tolerance
                        and _parse_time(row["partition_end"]) >= end - event_tolerance
                    ),
                    None,
                )
                if selected is None:
                    raise FactorPreviewError(
                        "FACTOR_PREVIEW_RANGE_NOT_COVERED",
                        (
                            f"No READY Manifest covers {variable_name}/{instrument_id} "
                            f"from {_iso(required_start)} through {_iso(end)}."
                        ),
                        path=f"inputs.{variable_name}",
                    )
                manifest_id = str(selected["manifest_id"])
                if manifest_id not in verified:
                    try:
                        FrozenManifestData(self.store, manifest_id).verify()
                    except Exception as exc:
                        raise FactorPreviewError(
                            "FACTOR_PREVIEW_MANIFEST_DAMAGED",
                            f"Manifest physical verification failed: {manifest_id}: {exc}",
                            path=f"inputs.{variable_name}",
                        ) from exc
                    verified.add(manifest_id)
                bindings.append(self._binding(input_spec, instrument_id, selected))
        return bindings

    def _load_inputs(
        self,
        spec: FactorGraphSpec,
        bindings: list[dict[str, Any]],
        start: datetime,
        end: datetime,
    ) -> dict[str, dict[str, list[dict[str, Any]]]]:
        manifest_rows: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for manifest_id in sorted({item["manifest_id"] for item in bindings}):
            manifest_rows[manifest_id] = FrozenManifestData(
                self.store, manifest_id
            ).read_bars_by_instrument(as_of=_iso(end))
        result: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for input_spec in spec.inputs:
            variable_name = _clean(input_spec.get("variable_name"))
            frequency = _clean(input_spec.get("frequency"))
            history = int(spec.required_history.get(variable_name, 1))
            required_start = start - timedelta(
                seconds=_FREQUENCY_SECONDS[frequency] * max(0, history - 1)
            )
            result[variable_name] = {}
            for binding in [
                item for item in bindings
                if item["variable_name"] == variable_name
            ]:
                rows = manifest_rows[binding["manifest_id"]].get(
                    binding["instrument_id"], []
                )
                selected_rows = [
                    row for row in rows
                    if required_start <= _parse_time(row.get("available_time")) <= end
                ]
                if len(selected_rows) < history:
                    raise FactorPreviewError(
                        "FACTOR_PREVIEW_HISTORY_NOT_COVERED",
                        (
                            f"{variable_name}/{binding['instrument_id']} has "
                            f"{len(selected_rows)} observations; {history} are required."
                        ),
                        path=f"inputs.{variable_name}",
                    )
                result[variable_name][binding["instrument_id"]] = selected_rows
        return result

    @staticmethod
    def _trim_values(
        outputs: Mapping[str, list[dict[str, Any]]],
        start: datetime,
        end: datetime,
    ) -> list[dict[str, Any]]:
        values = []
        for instrument_id, rows in outputs.items():
            for row in rows:
                as_of = _parse_time(row.get("factor_as_of_time"))
                if start <= as_of <= end:
                    values.append({
                        "instrument_id": instrument_id,
                        "as_of_time": _iso(as_of),
                        "value": row.get("value"),
                        "quality_status": row.get("quality_status"),
                    })
        values.sort(key=lambda item: (item["as_of_time"], item["instrument_id"]))
        if len(values) > FACTOR_PREVIEW_MAX_VALUE_ROWS:
            raise FactorPreviewError(
                "FACTOR_PREVIEW_RESULT_TOO_LARGE",
                (
                    f"Preview produced {len(values)} rows; reduce the time range "
                    f"below {FACTOR_PREVIEW_MAX_VALUE_ROWS} rows."
                ),
                path="preview.time_range",
            )
        return values

    @staticmethod
    def _analysis(values: list[dict[str, Any]], spec: FactorGraphSpec) -> dict[str, Any]:
        by_instrument: list[dict[str, Any]] = []
        latest_cross_section: list[dict[str, Any]] = []
        all_numeric: list[float] = []
        for instrument_id in sorted({item["instrument_id"] for item in values}):
            rows = [item for item in values if item["instrument_id"] == instrument_id]
            valid = [item for item in rows if item["value"] is not None]
            numeric = [
                float(item["value"])
                for item in valid
                if isinstance(item["value"], (int, float, bool))
                and math.isfinite(float(item["value"]))
            ]
            all_numeric.extend(numeric)
            latest = valid[-1] if valid else None
            if latest:
                latest_cross_section.append({
                    "instrument_id": instrument_id,
                    "as_of_time": latest["as_of_time"],
                    "value": latest["value"],
                })
            by_instrument.append({
                "instrument_id": instrument_id,
                "row_count": len(rows),
                "valid_value_count": len(valid),
                "warmup_count": len(rows) - len(valid),
                "coverage_percent": round((len(valid) / len(rows) * 100.0), 2)
                if rows else 0.0,
                "latest_value": latest["value"] if latest else None,
                "latest_as_of_time": latest["as_of_time"] if latest else "",
                "minimum": min(numeric) if numeric else None,
                "maximum": max(numeric) if numeric else None,
                "mean": statistics.fmean(numeric) if numeric else None,
                "standard_deviation": statistics.pstdev(numeric)
                if len(numeric) > 1 else (0.0 if numeric else None),
            })

        def percentile(fraction: float) -> float | None:
            if not all_numeric:
                return None
            ordered = sorted(all_numeric)
            index = int(round((len(ordered) - 1) * fraction))
            return ordered[index]

        valid_count = sum(item["valid_value_count"] for item in by_instrument)
        return {
            "output_type": spec.output_type,
            "output_unit": spec.output_unit,
            "overall": {
                "row_count": len(values),
                "valid_value_count": valid_count,
                "warmup_count": len(values) - valid_count,
                "instrument_count": len(by_instrument),
                "coverage_percent": round(valid_count / len(values) * 100.0, 2)
                if values else 0.0,
                "minimum": min(all_numeric) if all_numeric else None,
                "maximum": max(all_numeric) if all_numeric else None,
                "mean": statistics.fmean(all_numeric) if all_numeric else None,
                "standard_deviation": statistics.pstdev(all_numeric)
                if len(all_numeric) > 1 else (0.0 if all_numeric else None),
            },
            "distribution": {
                "p05": percentile(0.05),
                "p25": percentile(0.25),
                "p50": percentile(0.50),
                "p75": percentile(0.75),
                "p95": percentile(0.95),
            },
            "latest_cross_section": latest_cross_section,
            "by_instrument": by_instrument,
        }

    @staticmethod
    def _row(row: Any) -> dict[str, Any]:
        return {
            "schema_version": FACTOR_PREVIEW_SCHEMA_VERSION,
            "preview_id": str(row["preview_id"]),
            "draft_id": str(row["draft_id"]),
            "project_id": str(row["project_id"]),
            "status": str(row["status"]),
            "draft_fingerprint": str(row["draft_fingerprint"]),
            "preview_fingerprint": str(row["preview_fingerprint"]),
            "universe_snapshot_id": str(row["universe_snapshot_id"]),
            "universe_fingerprint": str(row["universe_fingerprint"]),
            "time_range": {
                "start": str(row["time_start"]),
                "end": str(row["time_end"]),
            },
            "manifest_ids": json.loads(row["manifest_ids_json"] or "[]"),
            "manifest_hashes": json.loads(row["manifest_hashes_json"] or "{}"),
            "input_bindings": json.loads(row["input_bindings_json"] or "[]"),
            "engine_version": str(row["engine_version"]),
            "code_hash": str(row["code_hash"]),
            "spec_hash": str(row["spec_hash"]),
            "values": json.loads(row["values_json"] or "[]"),
            "analysis": json.loads(row["analysis_json"] or "{}"),
            "diagnostics": json.loads(row["diagnostics_json"] or "[]"),
            "validated_definition_id": str(row["validated_definition_id"] or ""),
            "created_by": str(row["created_by"]),
            "created_at": str(row["created_at"]),
        }
