from __future__ import annotations

import calendar
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from .data_client import FrozenManifestData
from .models import DataRequirement
from .requirement_compiler import RequirementCompiler
from .requirement_service import normalize_source_selection_policy
from .run_contracts import (
    ReadinessCheck,
    ReadinessDimension,
    ReadinessStatus,
    RemediationCode,
    ResearchReasonCode,
)
from .store import DataPlatformStore


MANIFEST_RESOLVER_VERSION = "deterministic_manifest_resolver.v2"
SOURCE_SELECTION_POLICY_VERSION = "source_selection_policy.v2"

_CANONICAL_BAR_FIELDS = {
    "instrument_id", "event_time", "bar_start_time", "bar_end_time", "available_time",
    "open", "high", "low", "close", "volume", "quote_volume", "trade_count", "bar_status",
}
_FREQUENCY_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14_400,
    "1d": 86_400,
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _observed_holiday(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (occurrence - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last = date(year, month, calendar.monthrange(year, month)[1])
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _easter_sunday(year: int) -> date:
    """Return Gregorian Easter using the Anonymous Gregorian algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    month_offset = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * month_offset) // 451
    month = (h + month_offset - 7 * m + 114) // 31
    day = (h + month_offset - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def _is_us_equity_session(day: date) -> bool:
    if day.weekday() >= 5:
        return False
    year = day.year
    holidays = {
        _observed_holiday(date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),   # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),   # Presidents Day
        _easter_sunday(year) - timedelta(days=2),
        _last_weekday(year, 5, 0),     # Memorial Day
        _observed_holiday(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),   # Labor Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
        _observed_holiday(date(year, 12, 25)),
    }
    if year >= 2022:
        holidays.add(_observed_holiday(date(year, 6, 19)))
    return day not in holidays


def _manifest_covers_required_start(
    instrument_id: str,
    frequency: str,
    required_start: datetime,
    manifest_start: datetime | None,
) -> bool:
    if manifest_start is None:
        return False
    if manifest_start <= required_start:
        return True
    instrument = instrument_id.lower()
    if not instrument.startswith(("equity:xnas:", "equity:xnys:")) or frequency.lower() != "1d":
        return False
    cursor = required_start.date()
    manifest_date = manifest_start.date()
    while cursor < manifest_date:
        if _is_us_equity_session(cursor):
            return False
        cursor += timedelta(days=1)
    return True


@dataclass(frozen=True)
class ManifestResolution:
    requirement_set_id: str
    resolver_version: str
    source_selection_policy_version: str
    exact_manifest_ids: tuple[str, ...]
    bindings: tuple[dict[str, Any], ...]
    checks: tuple[ReadinessCheck, ...]

    @property
    def ready(self) -> bool:
        return not any(item.status in {ReadinessStatus.BLOCKED, ReadinessStatus.UNKNOWN} for item in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement_set_id": self.requirement_set_id,
            "resolver_version": self.resolver_version,
            "source_selection_policy_version": self.source_selection_policy_version,
            "exact_manifest_ids": list(self.exact_manifest_ids),
            "bindings": list(self.bindings),
            "ready": self.ready,
            "checks": [item.to_dict() for item in self.checks],
        }


class DeterministicManifestResolver:
    """Resolve requirements from semantic eligibility, then deterministic ranking.

    A manifest's recency is only the final tie-breaker after identity, source,
    fields, time range, quality, PIT/availability, and physical integrity pass.
    """

    def __init__(self, store: DataPlatformStore):
        self.store = store
        self.compiler = RequirementCompiler(store)

    def resolve(
        self,
        requirement_set_id: str,
        *,
        source_selection_policy: dict[str, Any] | None = None,
        verify_physical: bool = True,
    ) -> ManifestResolution:
        requirement_set = self.compiler.get(_clean(requirement_set_id))
        if requirement_set is None:
            raise ValueError("requirement set not found")
        policy = dict(source_selection_policy or {})
        policy_version = _clean(policy.get("version") or SOURCE_SELECTION_POLICY_VERSION)
        run_policy = normalize_source_selection_policy(policy)
        bindings: list[dict[str, Any]] = []
        checks: list[ReadinessCheck] = []

        for requirement in requirement_set.requirements:
            instruments = tuple(sorted(requirement.instrument_ids))
            if not instruments:
                checks.append(self._blocked(
                    ResearchReasonCode.DEFINITION_CLOSURE_INVALID,
                    requirement.requirement_id,
                    {"instrument_ids": "non-empty"},
                    {"instrument_ids": []},
                    RemediationCode.RECOMPILE_REQUIREMENTS,
                    "Requirement has no resolved instruments",
                ))
                continue
            for instrument_id in instruments:
                preferred_sources, allowed_sources, conflict = self._effective_source_policy(
                    requirement.source_selection_policy,
                    run_policy,
                    instrument_id,
                )
                if conflict:
                    checks.append(self._blocked(
                        ResearchReasonCode.PROVIDER_MISMATCH,
                        f"{instrument_id}:{requirement.data_type}:{requirement.frequency}",
                        {
                            "requirement_source_policy": requirement.source_selection_policy,
                            "run_source_policy": run_policy,
                        },
                        {"allowed_sources": []},
                        RemediationCode.SELECT_PROVIDER,
                        "Run Source Policy conflicts with the immutable Requirement Source Policy",
                    ))
                    continue
                binding, item_checks = self._resolve_one(
                    requirement,
                    instrument_id,
                    preferred_sources=preferred_sources,
                    allowed_sources=allowed_sources,
                    verify_physical=verify_physical,
                )
                checks.extend(item_checks)
                if binding:
                    bindings.append(binding)

        exact = tuple(sorted({item["manifest_id"] for item in bindings}))
        if bindings and not any(item.status in {ReadinessStatus.BLOCKED, ReadinessStatus.UNKNOWN} for item in checks):
            checks.append(ReadinessCheck(
                code=ResearchReasonCode.DATA_RESOLVED,
                dimension=ReadinessDimension.DATA,
                status=ReadinessStatus.READY,
                object_ref=requirement_set.requirement_set_id,
                required={"requirement_count": len(requirement_set.requirements)},
                actual={"manifest_ids": list(exact), "binding_count": len(bindings)},
                message="All effective requirements resolved to exact verified Manifests",
            ))
        return ManifestResolution(
            requirement_set_id=requirement_set.requirement_set_id,
            resolver_version=MANIFEST_RESOLVER_VERSION,
            source_selection_policy_version=policy_version,
            exact_manifest_ids=exact,
            bindings=tuple(bindings),
            checks=tuple(checks),
        )

    @staticmethod
    def _effective_source_policy(
        requirement_policy: dict[str, Any] | None,
        run_policy: dict[str, Any] | None,
        instrument_id: str,
    ) -> tuple[list[str], set[str], bool]:
        def scoped(value: dict[str, Any] | None) -> tuple[list[str], set[str]]:
            normalized = normalize_source_selection_policy(value or {})
            allowed = set(normalized["allowed_sources"])
            preferred = list(normalized["preferred_sources"])
            item = (normalized.get("per_instrument") or {}).get(instrument_id)
            if item:
                nested = normalize_source_selection_policy(item)
                nested_allowed = set(nested["allowed_sources"])
                if allowed and nested_allowed:
                    allowed &= nested_allowed
                elif nested_allowed:
                    allowed = nested_allowed
                preferred = list(dict.fromkeys([
                    *nested["preferred_sources"],
                    *preferred,
                ]))
            return preferred, allowed

        requirement_preferred, requirement_allowed = scoped(requirement_policy)
        run_preferred, run_allowed = scoped(run_policy)
        conflict = bool(requirement_allowed and run_allowed and not (requirement_allowed & run_allowed))
        if requirement_allowed and run_allowed:
            allowed = requirement_allowed & run_allowed
        else:
            allowed = requirement_allowed or run_allowed
        preferred = list(dict.fromkeys([*run_preferred, *requirement_preferred]))
        if allowed:
            preferred = [item for item in preferred if item in allowed]
        return preferred, allowed, conflict

    def _resolve_one(
        self,
        requirement: DataRequirement,
        instrument_id: str,
        *,
        preferred_sources: list[str],
        allowed_sources: set[str],
        verify_physical: bool,
    ) -> tuple[dict[str, Any] | None, list[ReadinessCheck]]:
        checks: list[ReadinessCheck] = []
        object_ref = f"{instrument_id}:{requirement.data_type}:{requirement.frequency}"
        with self.store.connection() as conn:
            rows = conn.execute(
                """
                SELECT c.*, m.manifest_id, m.manifest_version, m.manifest_hash,
                       m.status AS manifest_status, m.schema_version AS manifest_schema_version,
                       m.committed_at,
                       MIN(p.start_time) AS partition_start,
                       MAX(p.end_time) AS partition_end,
                       SUM(CASE WHEN p.quality_status != 'PASS' THEN 1 ELSE 0 END) AS bad_partitions
                FROM dataset_catalog c
                JOIN dataset_manifests m ON m.dataset_id=c.dataset_id
                JOIN dataset_partitions p ON p.manifest_id=m.manifest_id
                WHERE c.instrument_id=? AND lower(c.data_type)=? AND lower(c.frequency)=?
                  AND c.status='READY' AND m.status='READY'
                GROUP BY m.manifest_id
                """,
                (instrument_id, requirement.data_type.lower(), requirement.frequency.lower()),
            ).fetchall()

        if not rows:
            return None, [self._blocked(
                ResearchReasonCode.MANIFEST_NOT_READY, object_ref,
                {"instrument_id": instrument_id, "data_type": requirement.data_type, "frequency": requirement.frequency},
                {"candidate_count": 0}, RemediationCode.CREATE_BACKFILL_TASK,
                "No READY Manifest matches instrument, data type, and frequency",
            )]

        eligible: list[Any] = []
        failure_checks: list[ReadinessCheck] = []
        for row in rows:
            source = str(row["source"]).lower()
            if allowed_sources and source not in allowed_sources:
                failure_checks.append(self._blocked(
                    ResearchReasonCode.PROVIDER_MISMATCH, object_ref,
                    {"allowed_sources": sorted(allowed_sources)}, {"source": row["source"]},
                    RemediationCode.SELECT_PROVIDER, "Candidate source is outside Source Policy",
                ))
                continue
            fields = set(json.loads(row["fields_json"] or "[]"))
            if not fields and "bar" in str(row["schema_version"]).lower():
                fields = set(_CANONICAL_BAR_FIELDS)
            missing_fields = sorted({item.lower() for item in requirement.fields} - {item.lower() for item in fields})
            if missing_fields:
                failure_checks.append(self._blocked(
                    ResearchReasonCode.FIELD_NOT_COVERED, object_ref,
                    {"fields": list(requirement.fields)}, {"fields": sorted(fields), "missing": missing_fields},
                    RemediationCode.CREATE_BACKFILL_TASK, "Candidate Manifest does not cover required fields",
                ))
                continue
            range_start = _parse_time(row["partition_start"] or row["start_time"])
            range_end = _parse_time(row["partition_end"] or row["end_time"])
            required_start = _parse_time(requirement.history_start)
            required_end = _parse_time(requirement.history_end)
            event_history = (
                requirement.data_type.lower() == "price_history"
                and requirement.time_semantics.upper() == "EVENT_TIME_AVAILABLE_TIME"
            )
            event_tolerance = timedelta(
                seconds=_FREQUENCY_SECONDS.get(requirement.frequency.lower(), 0)
            )
            if event_history and required_start and (
                not range_start
                or range_start > required_start + event_tolerance
            ):
                failure_checks.append(self._blocked(
                    ResearchReasonCode.REQUESTED_RANGE_NOT_COVERED, object_ref,
                    {"start_time": requirement.history_start}, {"start_time": row["partition_start"] or row["start_time"]},
                    RemediationCode.CREATE_BACKFILL_TASK, "Event-history Manifest does not reach the requested start",
                ))
                continue
            if event_history and required_end and (
                not range_end
                or range_end < required_end - event_tolerance
            ):
                failure_checks.append(self._blocked(
                    ResearchReasonCode.REQUESTED_RANGE_NOT_COVERED, object_ref,
                    {"end_time": requirement.history_end}, {"end_time": row["partition_end"] or row["end_time"]},
                    RemediationCode.CREATE_BACKFILL_TASK, "Event-history Manifest does not reach the requested end",
                ))
                continue
            if required_start and not event_history and not _manifest_covers_required_start(
                instrument_id, requirement.frequency, required_start, range_start,
            ):
                failure_checks.append(self._blocked(
                    ResearchReasonCode.WARMUP_NOT_COVERED, object_ref,
                    {"start_time": requirement.history_start}, {"start_time": row["partition_start"] or row["start_time"]},
                    RemediationCode.CREATE_BACKFILL_TASK, "Manifest does not cover the compiled warmup start",
                ))
                continue
            if required_end and not event_history and (not range_end or range_end < required_end):
                failure_checks.append(self._blocked(
                    ResearchReasonCode.REQUESTED_RANGE_NOT_COVERED, object_ref,
                    {"end_time": requirement.history_end}, {"end_time": row["partition_end"] or row["end_time"]},
                    RemediationCode.CREATE_BACKFILL_TASK, "Manifest does not cover the requested end time",
                ))
                continue
            required_adjustment = requirement.adjustment.upper()
            actual_adjustment = str(row["adjustment"]).upper()
            # OpenBB's canonical contract maps NONE/unadjusted equities to
            # split-adjusted bars.  Compare the same canonical semantics the
            # provider adapter uses instead of leaving an automatically
            # prepared dataset permanently unresolved.
            equity_unadjusted_compatible = (
                instrument_id.lower().startswith("equity:")
                and required_adjustment in {"NONE", "UNADJUSTED"}
                and actual_adjustment in {"NONE", "UNADJUSTED", "SPLITS_ONLY"}
            )
            if required_adjustment != actual_adjustment and not equity_unadjusted_compatible:
                failure_checks.append(self._blocked(
                    ResearchReasonCode.ADJUSTMENT_MISMATCH, object_ref,
                    {"adjustment": requirement.adjustment}, {"adjustment": row["adjustment"]},
                    RemediationCode.CREATE_BACKFILL_TASK, "Adjustment semantics do not match",
                ))
                continue
            if requirement.point_in_time_policy.upper() != str(row["point_in_time_policy"]).upper():
                failure_checks.append(self._blocked(
                    ResearchReasonCode.PIT_POLICY_MISMATCH, object_ref,
                    {"point_in_time_policy": requirement.point_in_time_policy},
                    {"point_in_time_policy": row["point_in_time_policy"]},
                    RemediationCode.CREATE_BACKFILL_TASK, "Point-in-time policy does not match",
                ))
                continue
            if requirement.time_semantics.upper() != str(row["time_semantics"]).upper():
                failure_checks.append(self._blocked(
                    ResearchReasonCode.AVAILABLE_TIME_INVALID, object_ref,
                    {"time_semantics": requirement.time_semantics}, {"time_semantics": row["time_semantics"]},
                    RemediationCode.CREATE_BACKFILL_TASK, "Available-time semantics do not match",
                ))
                continue
            if requirement.quality_policy.upper() == "STRICT" and (
                str(row["quality_status"]).upper() != "PASS" or int(row["bad_partitions"] or 0) > 0
            ):
                failure_checks.append(self._blocked(
                    ResearchReasonCode.QUALITY_POLICY_FAILED, object_ref,
                    {"quality_policy": "STRICT"},
                    {"quality_status": row["quality_status"], "bad_partitions": int(row["bad_partitions"] or 0)},
                    RemediationCode.CREATE_BACKFILL_TASK, "Manifest failed strict quality policy",
                ))
                continue
            if int(row["gap_count"] or 0) > 0 and requirement.quality_policy.upper() == "STRICT":
                failure_checks.append(self._blocked(
                    ResearchReasonCode.KNOWN_GAPS, object_ref,
                    {"gap_count": 0}, {"gap_count": int(row["gap_count"])},
                    RemediationCode.CREATE_BACKFILL_TASK, "Dataset has known gaps",
                ))
                continue
            if verify_physical:
                try:
                    FrozenManifestData(self.store, str(row["manifest_id"])).verify()
                except Exception as exc:
                    failure_checks.append(self._blocked(
                        ResearchReasonCode.MANIFEST_DAMAGED, object_ref,
                        {"integrity": "VERIFIED"}, {"manifest_id": row["manifest_id"], "error": str(exc)},
                        RemediationCode.RETRY_PHYSICAL_VALIDATION, "Manifest physical verification failed",
                    ))
                    continue
            eligible.append(row)

        if not eligible:
            # Return the full structured explanation; duplicates are useful when
            # multiple providers fail for different deterministic reasons.
            return None, failure_checks

        def rank(row: Any) -> tuple[Any, ...]:
            source = str(row["source"]).lower()
            source_rank = preferred_sources.index(source) if source in preferred_sources else len(preferred_sources)
            quality_rank = 0 if str(row["quality_status"]).upper() == "PASS" else 1
            return (
                source_rank,
                quality_rank,
                int(row["gap_count"] or 0),
                -int(row["manifest_version"]),
                str(row["dataset_id"]),
                str(row["manifest_id"]),
            )

        selected = sorted(eligible, key=rank)[0]
        binding = {
            "requirement_id": requirement.requirement_id,
            "instrument_id": instrument_id,
            "dataset_id": str(selected["dataset_id"]),
            "manifest_id": str(selected["manifest_id"]),
            "manifest_hash": str(selected["manifest_hash"]),
            "source": str(selected["source"]),
            "schema_version": str(selected["manifest_schema_version"]),
            "range": {
                "start": selected["partition_start"] or selected["start_time"],
                "end": selected["partition_end"] or selected["end_time"],
            },
            "selection_rank": list(rank(selected)),
        }
        return binding, checks

    @staticmethod
    def _blocked(
        code: ResearchReasonCode,
        object_ref: str,
        required: dict[str, Any],
        actual: dict[str, Any],
        remediation: RemediationCode,
        message: str,
    ) -> ReadinessCheck:
        return ReadinessCheck(
            code=code,
            dimension=ReadinessDimension.DATA,
            status=ReadinessStatus.BLOCKED,
            object_ref=object_ref,
            required=required,
            actual=actual,
            remediation_code=remediation,
            message=message,
        )
