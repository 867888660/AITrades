from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _parse_utc(value: str | datetime | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(_clean(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def uses_daily_observation_endpoint(
    *,
    data_type: Any,
    frequency: Any,
    source: Any = "",
    schema_version: Any = "",
    time_semantics: Any = "",
) -> bool:
    """Return whether range end names the last observed daily record.

    Canonical ``bars.v1`` manifests store the exclusive ``bar_end_time`` as
    their range end. CRSP ``bars_daily.v2`` manifests intentionally store the
    last trading day's ``bar_start_time``/event label instead. CRSP daily
    valuation manifests likewise store an ``event_time`` date label. Those
    observation-style daily datasets compare end coverage at calendar-day
    granularity without weakening canonical or intraday bar checks.
    """

    normalized_data_type = _clean(data_type).lower()
    if _clean(frequency).lower() != "1d":
        return False
    if normalized_data_type == "equity_valuation_daily":
        return True
    if normalized_data_type != "bars":
        return False
    schema = _clean(schema_version).lower()
    source_name = _clean(source).lower()
    semantics = _clean(time_semantics).upper()
    return schema.startswith("bars_daily.") or (
        source_name.startswith("crsp") and semantics == "SOURCE_AVAILABLE_TIME"
    )


def range_end_covers_requirement(
    *,
    actual_end: str | datetime | None,
    required_end: str | datetime | None,
    data_type: Any,
    frequency: Any,
    source: Any = "",
    schema_version: Any = "",
    time_semantics: Any = "",
) -> bool:
    """Compare range ends without weakening intraday coverage checks."""

    required = _parse_utc(required_end)
    if required is None:
        return True
    actual = _parse_utc(actual_end)
    if actual is None:
        return False
    if uses_daily_observation_endpoint(
        data_type=data_type,
        frequency=frequency,
        source=source,
        schema_version=schema_version,
        time_semantics=time_semantics,
    ):
        actual_day: date = actual.date()
        required_day: date = required.date()
        return actual_day >= required_day
    return actual >= required
