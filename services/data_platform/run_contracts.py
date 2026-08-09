from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping


READINESS_RULE_VERSION = "research_readiness.v1"
PREVIEW_FINGERPRINT_SCHEMA_VERSION = "research_run_inputs_preview.v1"
REASON_CODE_CATALOG_VERSION = "research_reason_codes.v1"


class _StringEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class ReadinessDimension(_StringEnum):
    DEFINITION = "DEFINITION"
    DATA = "DATA"
    AUTHORIZATION = "AUTHORIZATION"
    EXECUTION = "EXECUTION"


class ReadinessStatus(_StringEnum):
    READY = "READY"
    WARNING = "WARNING"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


class BundleLifecycleStatus(_StringEnum):
    FROZEN = "FROZEN"


class BundleIntegrityStatus(_StringEnum):
    VERIFIED = "VERIFIED"
    UNKNOWN = "UNKNOWN"
    DAMAGED = "DAMAGED"


class BundleReuseStatus(_StringEnum):
    ALLOWED = "ALLOWED"
    PROHIBITED = "PROHIBITED"


class BundleInputMode(_StringEnum):
    DEFINITIONS = "DEFINITIONS"
    PRECOMPUTED_ARTIFACTS = "PRECOMPUTED_ARTIFACTS"


class ResearchReasonCode(_StringEnum):
    # Generic/readiness aggregation.
    DIMENSION_NOT_EVALUATED = "DIMENSION_NOT_EVALUATED"
    DEFINITION_VALID = "DEFINITION_VALID"
    DATA_RESOLVED = "DATA_RESOLVED"
    AUTHORIZATION_VALID = "AUTHORIZATION_VALID"
    EXECUTION_VALID = "EXECUTION_VALID"

    # Definition.
    TRACK_DRAFT_PRESENT = "TRACK_DRAFT_PRESENT"
    REFERENCE_NOT_PINNED = "REFERENCE_NOT_PINNED"
    FACTOR_REFERENCE_UNVERSIONED = "FACTOR_REFERENCE_UNVERSIONED"
    DEFINITION_CLOSURE_INVALID = "DEFINITION_CLOSURE_INVALID"
    SPEC_HASH_MISMATCH = "SPEC_HASH_MISMATCH"

    # Data.
    FIELD_NOT_COVERED = "FIELD_NOT_COVERED"
    FREQUENCY_MISMATCH = "FREQUENCY_MISMATCH"
    REQUESTED_RANGE_NOT_COVERED = "REQUESTED_RANGE_NOT_COVERED"
    WARMUP_NOT_COVERED = "WARMUP_NOT_COVERED"
    PROVIDER_MISMATCH = "PROVIDER_MISMATCH"
    ADJUSTMENT_MISMATCH = "ADJUSTMENT_MISMATCH"
    PIT_POLICY_MISMATCH = "PIT_POLICY_MISMATCH"
    AVAILABLE_TIME_INVALID = "AVAILABLE_TIME_INVALID"
    QUALITY_POLICY_FAILED = "QUALITY_POLICY_FAILED"
    KNOWN_GAPS = "KNOWN_GAPS"
    MANIFEST_NOT_READY = "MANIFEST_NOT_READY"
    MANIFEST_PHYSICAL_VALIDATION_UNKNOWN = "MANIFEST_PHYSICAL_VALIDATION_UNKNOWN"
    MANIFEST_DAMAGED = "MANIFEST_DAMAGED"
    RESOLVER_AMBIGUOUS = "RESOLVER_AMBIGUOUS"

    # Authorization.
    GRANT_REQUIRED = "GRANT_REQUIRED"
    GRANT_EXPIRED = "GRANT_EXPIRED"
    GRANT_REVOKED = "GRANT_REVOKED"
    GRANT_SCOPE_VIOLATION = "GRANT_SCOPE_VIOLATION"
    CAPABILITY_DENIED = "CAPABILITY_DENIED"
    BUDGET_INSUFFICIENT = "BUDGET_INSUFFICIENT"
    POLICY_VERSION_MISMATCH = "POLICY_VERSION_MISMATCH"

    # Execution.
    ENGINE_VERSION_MISMATCH = "ENGINE_VERSION_MISMATCH"
    CODE_HASH_MISMATCH = "CODE_HASH_MISMATCH"
    WORKER_CAPABILITY_UNKNOWN = "WORKER_CAPABILITY_UNKNOWN"
    PROVIDER_CAPABILITY_UNAVAILABLE = "PROVIDER_CAPABILITY_UNAVAILABLE"
    EXECUTION_SEMANTICS_UNSUPPORTED = "EXECUTION_SEMANTICS_UNSUPPORTED"
    UNIVERSE_GROUP_EXECUTION_UNSUPPORTED = "UNIVERSE_GROUP_EXECUTION_UNSUPPORTED"
    BENCHMARK_NOT_CONFIGURED = "BENCHMARK_NOT_CONFIGURED"
    RESOURCE_LIMIT_EXCEEDED = "RESOURCE_LIMIT_EXCEEDED"
    QUEUE_UNAVAILABLE = "QUEUE_UNAVAILABLE"

    # Preview and atomic Run creation.
    PREVIEW_NOT_FOUND = "PREVIEW_NOT_FOUND"
    PREVIEW_STALE = "PREVIEW_STALE"
    PREVIEW_FINGERPRINT_CONFLICT = "PREVIEW_FINGERPRINT_CONFLICT"
    READINESS_NOT_READY = "READINESS_NOT_READY"
    MANIFEST_RESOLUTION_CHANGED = "MANIFEST_RESOLUTION_CHANGED"
    BUDGET_RESERVATION_FAILED = "BUDGET_RESERVATION_FAILED"
    BUNDLE_FREEZE_FAILED = "BUNDLE_FREEZE_FAILED"
    RUN_CREATE_FAILED = "RUN_CREATE_FAILED"
    IDEMPOTENCY_KEY_CONFLICT = "IDEMPOTENCY_KEY_CONFLICT"


class RemediationCode(_StringEnum):
    NONE = "NONE"
    OPEN_DEFINITION = "OPEN_DEFINITION"
    VALIDATE_AND_PIN = "VALIDATE_AND_PIN"
    RECOMPILE_REQUIREMENTS = "RECOMPILE_REQUIREMENTS"
    CREATE_BACKFILL_TASK = "CREATE_BACKFILL_TASK"
    SELECT_PROVIDER = "SELECT_PROVIDER"
    REQUEST_SCOPE_EXPANSION = "REQUEST_SCOPE_EXPANSION"
    REQUEST_BUDGET_INCREASE = "REQUEST_BUDGET_INCREASE"
    RENEW_GRANT = "RENEW_GRANT"
    RETRY_PHYSICAL_VALIDATION = "RETRY_PHYSICAL_VALIDATION"
    REGENERATE_PREVIEW = "REGENERATE_PREVIEW"
    REVIEW_EXECUTION_SPEC = "REVIEW_EXECUTION_SPEC"
    CONTACT_OPERATOR = "CONTACT_OPERATOR"
    OPEN_AUDIT = "OPEN_AUDIT"


_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
_STATUS_PRIORITY = {
    ReadinessStatus.READY: 0,
    ReadinessStatus.WARNING: 1,
    ReadinessStatus.UNKNOWN: 2,
    ReadinessStatus.BLOCKED: 3,
}

_PREVIEW_REQUIRED_KEYS = {
    "definition_closure": {
        "project_version",
        "universe_definition_id",
        "universe_definition_version",
        "universe_snapshot_id",
        "factor_definitions",
        "alpha_definitions",
        "requirement_set_id",
    },
    "data_resolution_closure": {
        "resolved_manifest_ids",
        "resolver_version",
        "source_selection_policy_version",
    },
    "execution_closure": {
        "evaluation_spec_hash",
        "portfolio_spec_hash",
        "execution_spec_hash",
        "engine_version",
        "code_hash",
        "readiness_rule_version",
    },
    "authorization_closure": {
        "grant_id",
        "grant_version",
        "policy_version",
    },
}


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _as_dimension(value: ReadinessDimension | str) -> ReadinessDimension:
    return value if isinstance(value, ReadinessDimension) else ReadinessDimension(str(value).upper())


def _as_status(value: ReadinessStatus | str) -> ReadinessStatus:
    return value if isinstance(value, ReadinessStatus) else ReadinessStatus(str(value).upper())


def _as_code(value: ResearchReasonCode | str) -> str:
    code = str(_enum_value(value) or "").strip().upper()
    if not _CODE_PATTERN.fullmatch(code):
        raise ValueError(f"invalid stable reason code: {value!r}")
    return code


def _as_remediation(value: RemediationCode | str) -> str:
    return _as_code(str(_enum_value(value) or RemediationCode.NONE.value))


def _json_safe(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        converted = [_json_safe(item) for item in value]
        return sorted(converted, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def aggregate_readiness_status(statuses: Iterable[ReadinessStatus | str]) -> ReadinessStatus:
    normalized = [_as_status(item) for item in statuses]
    if not normalized:
        return ReadinessStatus.UNKNOWN
    return max(normalized, key=lambda item: _STATUS_PRIORITY[item])


@dataclass(frozen=True)
class ReadinessCheck:
    code: ResearchReasonCode | str
    dimension: ReadinessDimension | str
    status: ReadinessStatus | str
    object_ref: str = ""
    required: Mapping[str, Any] = field(default_factory=dict)
    actual: Mapping[str, Any] = field(default_factory=dict)
    remediation_code: RemediationCode | str = RemediationCode.NONE
    message: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _as_code(self.code))
        object.__setattr__(self, "dimension", _as_dimension(self.dimension))
        object.__setattr__(self, "status", _as_status(self.status))
        object.__setattr__(self, "object_ref", str(self.object_ref or "").strip())
        object.__setattr__(self, "required", dict(self.required or {}))
        object.__setattr__(self, "actual", dict(self.actual or {}))
        object.__setattr__(self, "remediation_code", _as_remediation(self.remediation_code))
        object.__setattr__(self, "message", str(self.message or "").strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": str(self.code),
            "dimension": str(self.dimension),
            "status": str(self.status),
            "object_ref": self.object_ref,
            "required": _json_safe(self.required),
            "actual": _json_safe(self.actual),
            "remediation_code": str(self.remediation_code),
            "message": self.message,
        }


@dataclass(frozen=True)
class ReadinessDimensionResult:
    dimension: ReadinessDimension
    status: ReadinessStatus
    checks: tuple[ReadinessCheck, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension.value,
            "status": self.status.value,
            "checks": [item.to_dict() for item in self.checks],
        }


@dataclass(frozen=True)
class ReadinessReport:
    rule_version: str
    dimensions: tuple[ReadinessDimensionResult, ...]
    overall: ReadinessStatus
    reason_code_catalog_version: str = REASON_CODE_CATALOG_VERSION

    @classmethod
    def build(
        cls,
        checks: Iterable[ReadinessCheck],
        *,
        rule_version: str = READINESS_RULE_VERSION,
    ) -> "ReadinessReport":
        grouped: dict[ReadinessDimension, list[ReadinessCheck]] = {
            dimension: [] for dimension in ReadinessDimension
        }
        for check in checks:
            if not isinstance(check, ReadinessCheck):
                raise TypeError("readiness report requires ReadinessCheck items")
            grouped[check.dimension].append(check)
        results: list[ReadinessDimensionResult] = []
        for dimension in ReadinessDimension:
            items = grouped[dimension]
            if not items:
                items = [
                    ReadinessCheck(
                        code=ResearchReasonCode.DIMENSION_NOT_EVALUATED,
                        dimension=dimension,
                        status=ReadinessStatus.UNKNOWN,
                        object_ref=dimension.value,
                        remediation_code=RemediationCode.CONTACT_OPERATOR,
                        message=f"{dimension.value} readiness has not been evaluated",
                    )
                ]
            results.append(
                ReadinessDimensionResult(
                    dimension=dimension,
                    status=aggregate_readiness_status(item.status for item in items),
                    checks=tuple(items),
                )
            )
        return cls(
            rule_version=str(rule_version or "").strip() or READINESS_RULE_VERSION,
            dimensions=tuple(results),
            overall=aggregate_readiness_status(item.status for item in results),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_version": self.rule_version,
            "reason_code_catalog_version": self.reason_code_catalog_version,
            "dimensions": {item.dimension.value: item.to_dict() for item in self.dimensions},
            "overall": {"status": self.overall.value},
        }


@dataclass(frozen=True)
class PreviewFingerprint:
    value: str
    material: Mapping[str, Any]
    schema_version: str = PREVIEW_FINGERPRINT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "preview_fingerprint": self.value,
            "material": _json_safe(self.material),
        }


def build_preview_fingerprint(
    *,
    definition_closure: Mapping[str, Any],
    data_resolution_closure: Mapping[str, Any],
    execution_closure: Mapping[str, Any],
    authorization_closure: Mapping[str, Any],
    schema_version: str = PREVIEW_FINGERPRINT_SCHEMA_VERSION,
) -> PreviewFingerprint:
    closures = {
        "definition_closure": dict(definition_closure),
        "data_resolution_closure": dict(data_resolution_closure),
        "execution_closure": dict(execution_closure),
        "authorization_closure": dict(authorization_closure),
    }
    for closure_name, required_keys in _PREVIEW_REQUIRED_KEYS.items():
        missing = sorted(required_keys - set(closures[closure_name]))
        if missing:
            raise ValueError(f"{closure_name} is missing required preview fields: {', '.join(missing)}")
    material = {
        "schema_version": str(schema_version or "").strip() or PREVIEW_FINGERPRINT_SCHEMA_VERSION,
        **closures,
    }
    value = hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()
    return PreviewFingerprint(value=value, material=material, schema_version=material["schema_version"])


def is_preview_stale(expected_fingerprint: str, current: PreviewFingerprint) -> bool:
    expected = str(expected_fingerprint or "").strip()
    if not expected:
        raise ValueError("expected preview fingerprint is required")
    return expected != current.value


class IdempotencyConflictError(ValueError):
    code = ResearchReasonCode.IDEMPOTENCY_KEY_CONFLICT.value

    def __init__(self, idempotency_key: str, existing_fingerprint: str, requested_fingerprint: str):
        self.idempotency_key = idempotency_key
        self.existing_fingerprint = existing_fingerprint
        self.requested_fingerprint = requested_fingerprint
        super().__init__(
            f"{self.code}: idempotency_key {idempotency_key!r} was reused with a different preview fingerprint"
        )


def enforce_idempotent_run_request(
    *,
    idempotency_key: str,
    existing_preview_fingerprint: str,
    requested_preview_fingerprint: str,
) -> str:
    key = str(idempotency_key or "").strip()
    existing = str(existing_preview_fingerprint or "").strip()
    requested = str(requested_preview_fingerprint or "").strip()
    if not key or not existing or not requested:
        raise ValueError("idempotency_key and both preview fingerprints are required")
    if existing != requested:
        raise IdempotencyConflictError(key, existing, requested)
    return existing


@dataclass(frozen=True)
class HistoricalAuthorizationEvidence:
    grant_id: str
    grant_version: str
    scope_snapshot: Mapping[str, Any]
    policy_version: str
    authorization_check_result: Mapping[str, Any]

    def __post_init__(self) -> None:
        for field_name in ("grant_id", "grant_version", "policy_version"):
            value = str(getattr(self, field_name) or "").strip()
            if not value:
                raise ValueError(f"{field_name} is required for historical authorization evidence")
            object.__setattr__(self, field_name, value)
        object.__setattr__(self, "scope_snapshot", dict(self.scope_snapshot or {}))
        object.__setattr__(self, "authorization_check_result", dict(self.authorization_check_result or {}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "grant_id": self.grant_id,
            "grant_version": self.grant_version,
            "scope_snapshot": _json_safe(self.scope_snapshot),
            "policy_version": self.policy_version,
            "authorization_check_result": _json_safe(self.authorization_check_result),
        }


@dataclass(frozen=True)
class FrozenBundleStatus:
    lifecycle_status: BundleLifecycleStatus = BundleLifecycleStatus.FROZEN
    integrity_status: BundleIntegrityStatus = BundleIntegrityStatus.UNKNOWN
    reuse_status: BundleReuseStatus = BundleReuseStatus.ALLOWED
    reuse_reason_code: str = ""

    def __post_init__(self) -> None:
        lifecycle = self.lifecycle_status if isinstance(self.lifecycle_status, BundleLifecycleStatus) else BundleLifecycleStatus(str(self.lifecycle_status).upper())
        integrity = self.integrity_status if isinstance(self.integrity_status, BundleIntegrityStatus) else BundleIntegrityStatus(str(self.integrity_status).upper())
        reuse = self.reuse_status if isinstance(self.reuse_status, BundleReuseStatus) else BundleReuseStatus(str(self.reuse_status).upper())
        reason = str(self.reuse_reason_code or "").strip().upper()
        if reuse == BundleReuseStatus.PROHIBITED and not reason:
            raise ValueError("reuse_reason_code is required when Bundle reuse is PROHIBITED")
        if integrity == BundleIntegrityStatus.DAMAGED and reuse != BundleReuseStatus.PROHIBITED:
            raise ValueError("a DAMAGED Bundle must have reuse_status PROHIBITED")
        if reason:
            _as_code(reason)
        object.__setattr__(self, "lifecycle_status", lifecycle)
        object.__setattr__(self, "integrity_status", integrity)
        object.__setattr__(self, "reuse_status", reuse)
        object.__setattr__(self, "reuse_reason_code", reason)

    def to_dict(self) -> dict[str, str]:
        return {
            "lifecycle_status": self.lifecycle_status.value,
            "integrity_status": self.integrity_status.value,
            "reuse_status": self.reuse_status.value,
            "reuse_reason_code": self.reuse_reason_code,
        }


def _validate_definition_refs(items: Iterable[Mapping[str, Any]], kind: str) -> tuple[dict[str, Any], ...]:
    result = []
    identity_key = f"{kind}_definition_id"
    for item in items:
        value = dict(item)
        missing = [key for key in (identity_key, "version", "spec_hash") if not str(value.get(key) or "").strip()]
        if missing:
            raise ValueError(f"{kind} definition reference is missing: {', '.join(missing)}")
        result.append(value)
    return tuple(result)


@dataclass(frozen=True)
class BundleInputClosure:
    run_type: str
    input_mode: BundleInputMode
    exact_manifest_ids: tuple[str, ...]
    universe_snapshot_id: str
    requirement_set_id: str
    universe_id: str = ""
    universe_revision_id: str = ""
    universe_resolution_id: str = ""
    resolved_instrument_tuples: tuple[tuple[str, ...], ...] = ()
    resolved_instrument_weights: Mapping[str, float] = field(default_factory=dict)
    universe_resolution_metadata: Mapping[str, Any] = field(default_factory=dict)
    factor_definitions: tuple[Mapping[str, Any], ...] = ()
    alpha_definitions: tuple[Mapping[str, Any], ...] = ()
    input_factor_artifact_ids: tuple[str, ...] = ()
    input_alpha_artifact_ids: tuple[str, ...] = ()
    evaluation_spec_hash: str = ""
    portfolio_spec_hash: str = ""
    execution_spec_hash: str = ""
    engine_version: str = ""
    code_hash: str = ""
    resolver_version: str = ""
    source_selection_policy_version: str = ""
    readiness_rule_version: str = READINESS_RULE_VERSION

    def __post_init__(self) -> None:
        run_type = str(self.run_type or "").strip().upper()
        if not run_type:
            raise ValueError("run_type is required")
        object.__setattr__(self, "run_type", run_type)
        mode = self.input_mode if isinstance(self.input_mode, BundleInputMode) else BundleInputMode(str(self.input_mode).upper())
        object.__setattr__(self, "input_mode", mode)
        manifests = tuple(sorted({str(item).strip() for item in self.exact_manifest_ids if str(item).strip()}))
        if not manifests:
            raise ValueError("exact_manifest_ids are required")
        object.__setattr__(self, "exact_manifest_ids", manifests)
        for field_name in ("universe_snapshot_id", "requirement_set_id", "engine_version", "code_hash", "resolver_version", "source_selection_policy_version", "readiness_rule_version"):
            value = str(getattr(self, field_name) or "").strip()
            if not value:
                raise ValueError(f"{field_name} is required")
            object.__setattr__(self, field_name, value)
        for field_name in ("universe_id", "universe_revision_id", "universe_resolution_id"):
            object.__setattr__(self, field_name, str(getattr(self, field_name) or "").strip())
        tuples = tuple(
            tuple(str(value).strip() for value in row if str(value).strip())
            for row in self.resolved_instrument_tuples
        )
        object.__setattr__(self, "resolved_instrument_tuples", tuples)
        weights = {
            str(instrument_id).strip(): float(weight)
            for instrument_id, weight in dict(self.resolved_instrument_weights or {}).items()
            if str(instrument_id).strip()
        }
        if any(weight < 0 for weight in weights.values()):
            raise ValueError("resolved_instrument_weights cannot contain negative values")
        object.__setattr__(self, "resolved_instrument_weights", weights)
        object.__setattr__(
            self,
            "universe_resolution_metadata",
            _json_safe(dict(self.universe_resolution_metadata or {})),
        )
        factor_definitions = _validate_definition_refs(self.factor_definitions, "factor")
        alpha_definitions = _validate_definition_refs(self.alpha_definitions, "alpha")
        object.__setattr__(self, "factor_definitions", factor_definitions)
        object.__setattr__(self, "alpha_definitions", alpha_definitions)
        factor_artifacts = tuple(sorted({str(item).strip() for item in self.input_factor_artifact_ids if str(item).strip()}))
        alpha_artifacts = tuple(sorted({str(item).strip() for item in self.input_alpha_artifact_ids if str(item).strip()}))
        object.__setattr__(self, "input_factor_artifact_ids", factor_artifacts)
        object.__setattr__(self, "input_alpha_artifact_ids", alpha_artifacts)
        if mode == BundleInputMode.DEFINITIONS and not (factor_definitions or alpha_definitions):
            raise ValueError("DEFINITIONS input mode requires a Factor or Alpha definition reference")
        if mode == BundleInputMode.PRECOMPUTED_ARTIFACTS and not (factor_artifacts or alpha_artifacts):
            raise ValueError("PRECOMPUTED_ARTIFACTS input mode requires an input Factor or Alpha artifact")

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_type": self.run_type,
            "input_mode": self.input_mode.value,
            "exact_manifest_ids": list(self.exact_manifest_ids),
            "universe_snapshot_id": self.universe_snapshot_id,
            "universe_id": self.universe_id,
            "universe_revision_id": self.universe_revision_id,
            "universe_resolution_id": self.universe_resolution_id,
            "resolved_instrument_tuples": [list(row) for row in self.resolved_instrument_tuples],
            "resolved_instrument_weights": dict(self.resolved_instrument_weights),
            "universe_resolution_metadata": _json_safe(self.universe_resolution_metadata),
            "requirement_set_id": self.requirement_set_id,
            "factor_definitions": _json_safe(self.factor_definitions),
            "alpha_definitions": _json_safe(self.alpha_definitions),
            "input_factor_artifact_ids": list(self.input_factor_artifact_ids),
            "input_alpha_artifact_ids": list(self.input_alpha_artifact_ids),
            "evaluation_spec_hash": self.evaluation_spec_hash,
            "portfolio_spec_hash": self.portfolio_spec_hash,
            "execution_spec_hash": self.execution_spec_hash,
            "engine_version": self.engine_version,
            "code_hash": self.code_hash,
            "resolver_version": self.resolver_version,
            "source_selection_policy_version": self.source_selection_policy_version,
            "readiness_rule_version": self.readiness_rule_version,
        }
