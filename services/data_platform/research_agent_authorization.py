from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from .research_control_plane import ResearchControlPlane
from .store import DataPlatformStore


DEFAULT_RESEARCH_OPERATIONS = (
    "UNIVERSE_CREATE",
    "UNIVERSE_SNAPSHOT_CREATE",
    "UNIVERSE_UNBIND",
    "FACTOR_CREATE",
    "FACTOR_VALIDATE",
    "ALPHA_CREATE",
    "ALPHA_VALIDATE",
    "PROJECT_PIN",
    "PROJECT_UNPIN",
    "REQUIREMENT_COMPILE",
    "REQUIREMENT_REMOVE",
    "COVERAGE_CHECK",
    "BACKFILL_CREATE",
    "PREVIEW_CREATE",
    "RUN_CREATE",
    "RUN_EXECUTE",
    "LIBRARY_ARCHIVE",
)


class ResearchAuthorizationError(PermissionError):
    def __init__(self, code: str, message: str, context: dict[str, Any] | None = None):
        self.code = code
        self.context = context or {}
        super().__init__(f"{code}: {message}")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _upper_set(values: Iterable[Any]) -> set[str]:
    return {_clean(item).upper() for item in values if _clean(item)}


def _parse_time(value: Any) -> datetime:
    parsed = datetime.fromisoformat(_clean(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class AuthorizationDecision:
    grant: dict[str, Any]
    operation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "grant_id": self.grant["grant_id"],
            "grant_version": self.grant.get("grant_version", 1),
            "policy_version": self.grant.get("policy_version", "research_policy.v1"),
            "operation": self.operation,
            "status": "ALLOWED",
        }


class ResearchAgentAuthorization:
    """Enforce the formal authorization resolved by a user-created research session."""

    def __init__(self, store: DataPlatformStore):
        self.store = store
        self.control = ResearchControlPlane(store)

    def require(
        self,
        project_id: str,
        operation: str,
        *,
        grant_id: str = "",
        providers: Iterable[Any] = (),
        intervals: Iterable[Any] = (),
        instrument_ids: Iterable[Any] = (),
        universe_definition_id: str = "",
        universe_snapshot_id: str = "",
        time_start: str = "",
        time_end: str = "",
    ) -> AuthorizationDecision:
        project_id = _clean(project_id)
        operation = _clean(operation).upper()
        if self.control.get_project(project_id) is None:
            raise ResearchAuthorizationError("RESEARCH_PROJECT_NOT_FOUND", "Research Project does not exist")
        grant = self.control.get_grant(grant_id) if _clean(grant_id) else self._latest_grant(project_id)
        if grant is None:
            raise ResearchAuthorizationError(
                "RESEARCH_GRANT_REQUIRED",
                "A human must create one Project Research Grant before Agent writes",
            )
        if _clean(grant.get("project_id")) != project_id:
            raise ResearchAuthorizationError("RESEARCH_GRANT_SCOPE_VIOLATION", "Grant belongs to another Project")
        status = _clean(grant.get("status")).upper()
        if status == "PAUSED":
            raise ResearchAuthorizationError("RESEARCH_AGENT_PAUSED", "Research Agent is paused for this Project")
        if status != "ACTIVE":
            raise ResearchAuthorizationError("RESEARCH_GRANT_INACTIVE", f"Grant status is {status or 'UNKNOWN'}")
        expires_at = _clean(grant.get("expires_at"))
        if expires_at and _parse_time(expires_at) <= datetime.now(timezone.utc):
            raise ResearchAuthorizationError("RESEARCH_GRANT_EXPIRED", "Project Research Grant has expired")

        scope = dict(grant.get("scope") or {})
        if _clean(scope.get("grant_kind")).upper() != "PROJECT_RESEARCH":
            raise ResearchAuthorizationError(
                "RESEARCH_GRANT_SCOPE_UPGRADE_REQUIRED",
                "This is a Run-only Grant; a human must create a Project Research Grant",
            )
        autonomy_level = _clean(scope.get("autonomy_level") or "AUTONOMOUS").upper()
        if autonomy_level not in {"AUTONOMOUS", "FULL_RESEARCH"}:
            raise ResearchAuthorizationError(
                "RESEARCH_AUTONOMY_DISABLED",
                f"Grant autonomy level {autonomy_level or 'NONE'} does not permit autonomous writes",
            )
        allowed_operations = _upper_set(scope.get("allowed_operations") or DEFAULT_RESEARCH_OPERATIONS)
        if operation not in allowed_operations:
            raise ResearchAuthorizationError(
                "RESEARCH_OPERATION_OUT_OF_SCOPE",
                f"{operation} is not included in allowed_operations",
            )

        allowed_providers = _upper_set(scope.get("allowed_providers") or [])
        requested_providers = _upper_set(providers)
        if allowed_providers and not requested_providers.issubset(allowed_providers):
            raise ResearchAuthorizationError(
                "RESEARCH_PROVIDER_OUT_OF_SCOPE",
                f"Requested providers {sorted(requested_providers - allowed_providers)} are not allowed",
                context={"allowed": sorted(allowed_providers), "requested": sorted(requested_providers)},
            )
        allowed_intervals = {_clean(item).lower() for item in scope.get("allowed_intervals") or [] if _clean(item)}
        requested_intervals = {_clean(item).lower() for item in intervals if _clean(item)}
        if allowed_intervals and not requested_intervals.issubset(allowed_intervals):
            raise ResearchAuthorizationError(
                "RESEARCH_FREQUENCY_OUT_OF_SCOPE",
                f"Requested frequencies {sorted(requested_intervals - allowed_intervals)} are not allowed",
                context={"allowed": sorted(allowed_intervals), "requested": sorted(requested_intervals)},
            )
        allowed_instruments = _upper_set(scope.get("allowed_instrument_ids") or [])
        requested_instruments = _upper_set(instrument_ids)
        if allowed_instruments and not requested_instruments.issubset(allowed_instruments):
            raise ResearchAuthorizationError(
                "RESEARCH_UNIVERSE_OUT_OF_SCOPE",
                f"Requested instruments {sorted(requested_instruments - allowed_instruments)} are not allowed",
                context={"allowed": sorted(allowed_instruments), "requested": sorted(requested_instruments)},
            )
        allowed_definitions = {_clean(item) for item in scope.get("allowed_universe_definition_ids") or [] if _clean(item)}
        if allowed_definitions and _clean(universe_definition_id) and _clean(universe_definition_id) not in allowed_definitions:
            raise ResearchAuthorizationError("RESEARCH_UNIVERSE_OUT_OF_SCOPE", "Universe definition is outside Grant scope")
        allowed_snapshots = {_clean(item) for item in scope.get("allowed_universe_snapshot_ids") or [] if _clean(item)}
        if allowed_snapshots and _clean(universe_snapshot_id) and _clean(universe_snapshot_id) not in allowed_snapshots:
            raise ResearchAuthorizationError("RESEARCH_UNIVERSE_OUT_OF_SCOPE", "Universe snapshot is outside Grant scope")

        allowed_start = _clean(scope.get("time_start"))
        allowed_end = _clean(scope.get("time_end"))
        if allowed_start and _clean(time_start) and _parse_time(time_start) < _parse_time(allowed_start):
            raise ResearchAuthorizationError("RESEARCH_TIME_RANGE_OUT_OF_SCOPE", "Requested start precedes Grant range")
        if allowed_end and _clean(time_end) and _parse_time(time_end) > _parse_time(allowed_end):
            raise ResearchAuthorizationError("RESEARCH_TIME_RANGE_OUT_OF_SCOPE", "Requested end exceeds Grant range")
        return AuthorizationDecision(grant=grant, operation=operation)

    def _latest_grant(self, project_id: str) -> dict[str, Any] | None:
        grants = self.control.list_grants(project_id=project_id, limit=50)
        for grant in grants:
            scope = dict(grant.get("scope") or {})
            if (
                _clean(grant.get("status")).upper() in {"ACTIVE", "PAUSED"}
                and _clean(scope.get("grant_kind")).upper() == "PROJECT_RESEARCH"
            ):
                return grant
        return grants[0] if grants else None
