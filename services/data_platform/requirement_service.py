from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from .models import DataRequirement
from .store import DataPlatformStore, json_dumps


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _source_list(values: Any) -> list[str]:
    if isinstance(values, str):
        values = [values]
    return list(dict.fromkeys(
        _clean(item).lower()
        for item in (values or [])
        if _clean(item)
    ))


def normalize_source_selection_policy(value: Any) -> dict[str, Any]:
    """Canonicalize source constraints while accepting legacy string policies."""
    if isinstance(value, Mapping):
        raw = dict(value)
        mode = _clean(raw.get("mode") or "AUTO").upper()
    else:
        raw = {}
        mode = _clean(value or "AUTO").upper()
    if mode not in {"AUTO", "FIXED", "PRIMARY_FALLBACK", "COMPARE"}:
        raise ValueError(f"unsupported source policy mode: {mode}")
    providers = _source_list(raw.get("providers"))
    allowed = _source_list(raw.get("allowed_sources"))
    preferred = _source_list(raw.get("preferred_sources"))
    if providers:
        if not allowed and mode in {"FIXED", "PRIMARY_FALLBACK", "COMPARE"}:
            allowed = list(providers)
        if not preferred:
            preferred = list(providers)
    if allowed:
        preferred = [item for item in preferred if item in set(allowed)]
    per_instrument: dict[str, Any] = {}
    for instrument_id, item in sorted(dict(raw.get("per_instrument") or {}).items()):
        nested = normalize_source_selection_policy(item)
        per_instrument[_clean(instrument_id)] = {
            "mode": nested["mode"],
            "allowed_sources": nested["allowed_sources"],
            "preferred_sources": nested["preferred_sources"],
        }
    return {
        "mode": mode,
        "allowed_sources": allowed,
        "preferred_sources": preferred,
        "per_instrument": per_instrument,
    }


def _canonical_payload(payload: dict[str, Any]) -> dict[str, Any]:
    instruments = sorted({_clean(item) for item in payload.get("instrument_ids", []) if _clean(item)})
    fields = sorted({_clean(item) for item in payload.get("fields", []) if _clean(item)})
    raw_policy = payload.get("source_selection_policy")
    if raw_policy is None:
        raw_policy = payload.get("source_policy") or "AUTO"
    source_selection_policy = normalize_source_selection_policy(raw_policy)
    return {
        "owner_type": _clean(payload.get("owner_type")).upper(),
        "owner_id": _clean(payload.get("owner_id")),
        "target_type": _clean(payload.get("target_type") or "INSTRUMENTS").upper(),
        "instrument_ids": instruments,
        "data_type": _clean(payload.get("data_type")).lower(),
        "frequency": _clean(payload.get("frequency")).lower(),
        "fields": fields,
        "history_mode": _clean(payload.get("history_mode") or "FIXED").upper(),
        "history_start": payload.get("history_start"),
        "history_end": payload.get("history_end"),
        "lookback_value": payload.get("lookback_value"),
        "lookback_unit": _clean(payload.get("lookback_unit")).upper(),
        "refresh_mode": _clean(payload.get("refresh_mode") or "MANUAL").upper(),
        "refresh_interval_seconds": payload.get("refresh_interval_seconds"),
        "auto_backfill": bool(payload.get("auto_backfill", True)),
        "usage_level": _clean(payload.get("usage_level") or "RESEARCH").upper(),
        "priority": int(payload.get("priority", 50)),
        "adjustment": _clean(payload.get("adjustment") or "NONE").upper(),
        "time_semantics": _clean(payload.get("time_semantics") or "BAR_END_AVAILABLE_TIME").upper(),
        "point_in_time_policy": _clean(payload.get("point_in_time_policy") or "AS_OF").upper(),
        "quality_policy": _clean(payload.get("quality_policy") or "STRICT").upper(),
        "source_policy": source_selection_policy["mode"],
        "source_selection_policy": source_selection_policy,
    }


def requirement_fingerprint(payload: dict[str, Any]) -> str:
    canonical = _canonical_payload(payload)
    return hashlib.sha256(json_dumps(canonical).encode("utf-8")).hexdigest()


class DataRequirementService:
    def __init__(self, store: DataPlatformStore):
        self.store = store

    def create(self, payload: dict[str, Any], *, requirement_id: str | None = None) -> DataRequirement:
        canonical = _canonical_payload(payload)
        if not canonical["data_type"]:
            raise ValueError("data_type is required")
        if not canonical["instrument_ids"]:
            raise ValueError("at least one instrument_id is required")
        if not canonical["frequency"]:
            raise ValueError("frequency is required")
        if canonical["history_mode"] == "FIXED" and not canonical["history_start"]:
            raise ValueError("FIXED requirements require history_start")
        fingerprint = requirement_fingerprint(canonical)
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        requirement_id = _clean(requirement_id) or f"req_{uuid.uuid4().hex}"
        with self.store.transaction(immediate=True) as conn:
            existing = conn.execute(
                "SELECT requirement_id FROM data_requirements WHERE requirement_fingerprint = ?",
                (fingerprint,),
            ).fetchone()
            if existing:
                requirement_id = str(existing[0])
            else:
                conn.execute(
                    """
                    INSERT INTO data_requirements(
                        requirement_id, owner_type, owner_id, target_type,
                        instrument_list_json, data_type, frequency, fields_json,
                        history_mode, history_start, history_end, lookback_value,
                        lookback_unit, refresh_mode, refresh_interval_seconds,
                        auto_backfill, usage_level, priority, status,
                        requirement_fingerprint, created_at, updated_at,
                        adjustment, time_semantics, point_in_time_policy, quality_policy, source_policy,
                        source_selection_policy_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        requirement_id,
                        canonical["owner_type"],
                        canonical["owner_id"],
                        canonical["target_type"],
                        json_dumps(canonical["instrument_ids"]),
                        canonical["data_type"],
                        canonical["frequency"],
                        json_dumps(canonical["fields"]),
                        canonical["history_mode"],
                        canonical["history_start"],
                        canonical["history_end"],
                        canonical["lookback_value"],
                        canonical["lookback_unit"],
                        canonical["refresh_mode"],
                        canonical["refresh_interval_seconds"],
                        int(canonical["auto_backfill"]),
                        canonical["usage_level"],
                        canonical["priority"],
                        "ACTIVE",
                        fingerprint,
                        now,
                        now,
                        canonical["adjustment"],
                        canonical["time_semantics"],
                        canonical["point_in_time_policy"],
                        canonical["quality_policy"],
                        canonical["source_policy"],
                        json_dumps(canonical["source_selection_policy"]),
                    ),
                )
        return self.get(requirement_id)  # type: ignore[return-value]

    def get(self, requirement_id: str) -> Optional[DataRequirement]:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM data_requirements WHERE requirement_id = ?",
                (_clean(requirement_id),),
            ).fetchone()
        if row is None:
            return None
        return self._from_row(row)

    def list(self, *, owner_type: str = "", owner_id: str = "", status: str = "") -> list[DataRequirement]:
        clauses = []
        params: list[str] = []
        if _clean(owner_type):
            clauses.append("owner_type = ?")
            params.append(_clean(owner_type).upper())
        if _clean(owner_id):
            clauses.append("owner_id = ?")
            params.append(_clean(owner_id))
        if _clean(status):
            clauses.append("status = ?")
            params.append(_clean(status).upper())
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.store.connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM data_requirements{where} ORDER BY created_at DESC",
                params,
            ).fetchall()
        return [self._from_row(row) for row in rows]

    @staticmethod
    def _from_row(row: Any) -> DataRequirement:
        source_selection_policy: Any = str(row["source_policy"])
        if "source_selection_policy_json" in row.keys():
            stored_policy = json.loads(row["source_selection_policy_json"] or "{}")
            if stored_policy:
                source_selection_policy = stored_policy
        return DataRequirement(
            requirement_id=str(row["requirement_id"]),
            owner_type=str(row["owner_type"]),
            owner_id=str(row["owner_id"]),
            target_type=str(row["target_type"]),
            instrument_ids=tuple(json.loads(row["instrument_list_json"] or "[]")),
            data_type=str(row["data_type"]),
            frequency=str(row["frequency"]),
            fields=tuple(json.loads(row["fields_json"] or "[]")),
            history_mode=str(row["history_mode"]),
            history_start=row["history_start"],
            history_end=row["history_end"],
            lookback_value=row["lookback_value"],
            lookback_unit=str(row["lookback_unit"]),
            refresh_mode=str(row["refresh_mode"]),
            refresh_interval_seconds=row["refresh_interval_seconds"],
            auto_backfill=bool(row["auto_backfill"]),
            usage_level=str(row["usage_level"]),
            priority=int(row["priority"]),
            status=str(row["status"]),
            requirement_fingerprint=str(row["requirement_fingerprint"]),
            adjustment=str(row["adjustment"]),
            time_semantics=str(row["time_semantics"]),
            point_in_time_policy=str(row["point_in_time_policy"]),
            quality_policy=str(row["quality_policy"]),
            source_policy=str(row["source_policy"]),
            source_selection_policy=normalize_source_selection_policy(
                source_selection_policy
            ),
        )
