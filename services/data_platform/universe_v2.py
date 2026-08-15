from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence


UNIVERSE_V2_SCHEMA_VERSION = "universe-definition.v2"
UNIVERSE_V2_COMPILED_SCHEMA_VERSION = "universe-compiled.v2"
UNIVERSE_MEMBERSHIP_SCHEMA_VERSION = "universe-membership-timeline.v2"
UNIVERSE_ENGINE_VERSION = "universe-engine.v2"

UNIVERSE_TYPES = {"STATIC", "DYNAMIC", "COMPOSITE"}
SELECT_METHODS = {"ALL", "TOP_N", "BOTTOM_N", "PERCENTILE"}
REBALANCE_FREQUENCIES = {"DAILY", "WEEKLY", "MONTHLY", "QUARTERLY", "YEARLY"}
COMPOSITE_OPERATORS = {"UNION", "INTERSECTION", "DIFFERENCE"}

_OPERATOR_ALIASES = {
    "=": "EQ", "==": "EQ", "EQ": "EQ",
    "!=": "NE", "<>": "NE", "NE": "NE",
    ">": "GT", "GT": "GT",
    ">=": "GTE", "GTE": "GTE",
    "<": "LT", "LT": "LT",
    "<=": "LTE", "LTE": "LTE",
    "IN": "IN", "NOT_IN": "NOT_IN",
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _parse_time(value: Any) -> datetime:
    text = _clean(value)
    if not text:
        raise ValueError("time is required")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class UniverseFieldRequirement:
    data_type: str
    frequency: str
    fields: tuple[str, ...]
    time_semantics: str
    point_in_time_policy: str = "AS_OF"
    warmup_bars: int = 0

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["fields"] = list(self.fields)
        return result


@dataclass(frozen=True)
class UniverseFieldContract:
    field_id: str
    display_name: str
    value_type: str
    unit: str
    source_data_type: str
    source_fields: tuple[str, ...]
    source_unit: str = ""
    unit_scale: float = 1.0
    filterable: bool = True
    rankable: bool = True
    operators: tuple[str, ...] = ("EQ", "NE", "GT", "GTE", "LT", "LTE")
    asset_classes: tuple[str, ...] = ("equity",)
    frequency: str = "1d"
    point_in_time_policy: str = "AS_OF_LATEST_AVAILABLE"
    missing_policy: str = "EXCLUDE"
    warmup_bars: int = 0
    calculation: str = "SOURCE"
    contract_version: str = "1"
    formal_pipeline: bool = False
    requirements: tuple[UniverseFieldRequirement, ...] = ()

    def requirement_specs(self) -> tuple[UniverseFieldRequirement, ...]:
        if self.requirements:
            return self.requirements
        return (UniverseFieldRequirement(
            data_type=self.source_data_type,
            frequency=self.frequency,
            fields=self.source_fields,
            time_semantics="SOURCE_AVAILABLE_TIME",
            point_in_time_policy="AS_OF",
            warmup_bars=self.warmup_bars,
        ),)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        for key in ("source_fields", "operators", "asset_classes"):
            result[key] = list(result[key])
        result["requirements"] = [item.to_dict() for item in self.requirement_specs()]
        result["pit_safe"] = self.point_in_time_policy != "NONE"
        return result


class UniverseFieldRegistry:
    """Versioned allow-list for fields usable by the small Universe language.

    The registry is deliberately code-owned. A Universe definition can refer to
    registered fields but cannot embed SQL or arbitrary formulas.
    """

    def __init__(
        self,
        contracts: Iterable[UniverseFieldContract] | None = None,
        aliases: Mapping[str, str] | None = None,
    ):
        values = tuple(contracts or self._default_contracts())
        self._contracts = {item.field_id: item for item in values}
        if len(self._contracts) != len(values):
            raise ValueError("Universe field IDs must be unique")
        default_aliases = {
            "price": "price_usd",
            "close": "price_usd",
            "market_cap": "market_cap_usd",
            "adv20": "adv20_usd",
            "adv60": "adv60_usd",
            "volatility": "volatility_60d",
            "pe": "pe_ttm",
            "pb": "pb_mrq",
            "roe": "roe_ttm",
            "fcf_yield": "fcf_yield_ttm",
        }
        default_aliases.update({str(key).lower(): str(value) for key, value in (aliases or {}).items()})
        self._aliases = default_aliases

    @staticmethod
    def _default_contracts() -> tuple[UniverseFieldContract, ...]:
        numeric = ("EQ", "NE", "GT", "GTE", "LT", "LTE")
        return (
            UniverseFieldContract(
                field_id="market_cap_usd", display_name="Market Cap", value_type="NUMBER",
                unit="USD", source_data_type="equity_valuation_daily",
                source_fields=("market_cap_usd",), source_unit="USD_THOUSANDS", unit_scale=1000.0,
                operators=numeric, formal_pipeline=True,
                requirements=(UniverseFieldRequirement(
                    "equity_valuation_daily", "1d", ("market_cap",),
                    "SOURCE_AVAILABLE_TIME",
                ),),
            ),
            UniverseFieldContract(
                field_id="price_usd", display_name="Price", value_type="NUMBER", unit="USD",
                source_data_type="bars", source_fields=("close",), operators=numeric,
            ),
            UniverseFieldContract(
                field_id="adv20_usd", display_name="20-day Average Dollar Volume",
                value_type="NUMBER", unit="USD", source_data_type="bars",
                source_fields=("close", "volume"), operators=numeric, warmup_bars=20,
                calculation="MEAN(ABS(close)*volume,20)",
            ),
            UniverseFieldContract(
                field_id="adv60_usd", display_name="60-day Average Dollar Volume",
                value_type="NUMBER", unit="USD", source_data_type="bars",
                source_fields=("close", "volume"), operators=numeric, warmup_bars=60,
                calculation="MEAN(ABS(close)*volume,60)",
            ),
            UniverseFieldContract(
                field_id="volatility_60d", display_name="60-day Volatility",
                value_type="NUMBER", unit="RATIO", source_data_type="bars",
                source_fields=("close",), operators=numeric, warmup_bars=61,
                calculation="STDDEV(RETURN(close),60)",
            ),
            UniverseFieldContract(
                field_id="pe_ttm", display_name="P/E TTM", value_type="NUMBER", unit="RATIO",
                source_data_type="calculated_pit",
                source_fields=("market_cap_usd", "net_income_ttm"), operators=numeric,
                calculation="market_cap_usd/net_income_ttm; net_income_ttm>0",
                contract_version="2", formal_pipeline=True,
                requirements=(
                    UniverseFieldRequirement(
                        "equity_valuation_daily", "1d", ("market_cap",),
                        "SOURCE_AVAILABLE_TIME",
                    ),
                    UniverseFieldRequirement(
                        "fundamentals_pit", "event", ("net_income_ttm",),
                        "SOURCE_AVAILABLE_TIME", "FILED_OR_ACCEPTED_AT",
                    ),
                ),
            ),
            UniverseFieldContract(
                field_id="pb_mrq", display_name="P/B MRQ", value_type="NUMBER", unit="RATIO",
                source_data_type="calculated_pit",
                source_fields=("market_cap_usd", "equity"), operators=numeric,
                calculation="market_cap_usd/equity; equity>0",
                contract_version="2", formal_pipeline=True,
                requirements=(
                    UniverseFieldRequirement(
                        "equity_valuation_daily", "1d", ("market_cap",),
                        "SOURCE_AVAILABLE_TIME",
                    ),
                    UniverseFieldRequirement(
                        "fundamentals_pit", "event", ("equity",),
                        "SOURCE_AVAILABLE_TIME", "FILED_OR_ACCEPTED_AT",
                    ),
                ),
            ),
            UniverseFieldContract(
                field_id="roe_ttm", display_name="ROE TTM", value_type="NUMBER", unit="RATIO",
                source_data_type="calculated_pit",
                source_fields=("net_income_ttm", "equity"), operators=numeric,
                calculation="net_income_ttm/equity; equity>0",
                contract_version="2", formal_pipeline=True,
                requirements=(UniverseFieldRequirement(
                    "fundamentals_pit", "event", ("net_income_ttm", "equity"),
                    "SOURCE_AVAILABLE_TIME", "FILED_OR_ACCEPTED_AT",
                ),),
            ),
            UniverseFieldContract(
                field_id="fcf_yield_ttm", display_name="FCF Yield TTM",
                value_type="NUMBER", unit="RATIO", source_data_type="calculated_pit",
                source_fields=("operating_cash_flow_ttm", "capex_ttm", "market_cap_usd"),
                operators=numeric,
                calculation="(operating_cash_flow_ttm-capex_ttm)/market_cap_usd; market_cap_usd>0",
                contract_version="2", formal_pipeline=True,
                requirements=(
                    UniverseFieldRequirement(
                        "equity_valuation_daily", "1d", ("market_cap",),
                        "SOURCE_AVAILABLE_TIME",
                    ),
                    UniverseFieldRequirement(
                        "fundamentals_pit", "event",
                        ("operating_cash_flow_ttm", "capex_ttm"),
                        "SOURCE_AVAILABLE_TIME", "FILED_OR_ACCEPTED_AT",
                    ),
                ),
            ),
            UniverseFieldContract(
                field_id="security_type", display_name="Security Type", value_type="STRING", unit="",
                source_data_type="equity_security_master", source_fields=("security_type",),
                rankable=False, operators=("EQ", "NE", "IN", "NOT_IN"), warmup_bars=0,
            ),
            UniverseFieldContract(
                field_id="primary_exchange", display_name="Primary Exchange", value_type="STRING", unit="",
                source_data_type="equity_security_master", source_fields=("primary_exchange",),
                rankable=False, operators=("EQ", "NE", "IN", "NOT_IN"), warmup_bars=0,
            ),
            UniverseFieldContract(
                field_id="listing_age_days", display_name="Listing Age", value_type="NUMBER", unit="DAY",
                source_data_type="equity_security_master", source_fields=("valid_from",),
                rankable=False, operators=("GT", "GTE", "LT", "LTE"),
                calculation="DECISION_DATE-valid_from",
            ),
        )

    @classmethod
    def default(cls) -> "UniverseFieldRegistry":
        return cls()

    def resolve_id(self, field: Any) -> str:
        value = _clean(field).lower()
        return self._aliases.get(value, value)

    def get(self, field: Any) -> UniverseFieldContract | None:
        return self._contracts.get(self.resolve_id(field))

    def require(self, field: Any) -> UniverseFieldContract:
        contract = self.get(field)
        if contract is None:
            raise ValueError(f"Universe field is not registered: {_clean(field)}")
        return contract

    def list(self) -> list[UniverseFieldContract]:
        return [self._contracts[key] for key in sorted(self._contracts)]

    def capabilities(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self.list()]


class UniverseV2Compiler:
    def __init__(self, registry: UniverseFieldRegistry | None = None):
        self.registry = registry or UniverseFieldRegistry.default()

    def normalize(self, definition: Mapping[str, Any]) -> dict[str, Any]:
        raw = dict(definition or {})
        kind = _clean(raw.get("type") or raw.get("universe_type") or "DYNAMIC").upper()
        aliases = {
            "STATIC_LIST": "STATIC", "INSTRUMENT_SET": "STATIC",
            "DYNAMIC_RULE": "DYNAMIC", "DYNAMIC_RULE_UNIVERSE": "DYNAMIC",
            "DYNAMIC_SET": "DYNAMIC", "COMPOSITE_SET": "COMPOSITE",
        }
        kind = aliases.get(kind, kind)
        if kind not in UNIVERSE_TYPES:
            raise ValueError(f"unsupported Universe v2 type: {kind}")
        metadata = {
            key: raw[key]
            for key in ("name", "description", "tags", "extensions")
            if key in raw
        }
        if kind == "STATIC":
            members = raw.get("instruments")
            if members is None:
                members = raw.get("members")
            if members is None:
                members = dict(raw.get("parameters") or {}).get("instrument_ids")
            normalized_members = sorted({_clean(item) for item in members or [] if _clean(item)})
            if not normalized_members:
                raise ValueError("STATIC Universe requires instruments")
            return {
                "schema_version": UNIVERSE_V2_SCHEMA_VERSION,
                "type": "STATIC", "instruments": normalized_members, **metadata,
            }
        if kind == "COMPOSITE":
            expression = dict(raw.get("expression") or {})
            operator = _clean(raw.get("operator") or expression.get("operator") or "UNION").upper()
            if operator not in COMPOSITE_OPERATORS:
                raise ValueError(f"unsupported Composite operator: {operator}")
            raw_inputs = raw.get("inputs") or expression.get("inputs") or []
            inputs: list[dict[str, str]] = []
            for item in raw_inputs:
                value = dict(item) if isinstance(item, Mapping) else {"universe_id": item}
                universe_id = _clean(value.get("universe_id") or value.get("ref"))
                if not universe_id:
                    raise ValueError("Composite input requires universe_id")
                normalized = {"universe_id": universe_id}
                revision_id = _clean(value.get("revision_id"))
                if revision_id:
                    normalized["revision_id"] = revision_id
                inputs.append(normalized)
            if len(inputs) < 2:
                raise ValueError("COMPOSITE Universe requires at least two inputs")
            return {
                "schema_version": UNIVERSE_V2_SCHEMA_VERSION,
                "type": "COMPOSITE", "operator": operator, "inputs": inputs, **metadata,
            }

        base_raw = raw.get("base")
        base = {"ref": _clean(base_raw)} if not isinstance(base_raw, Mapping) else {
            "ref": _clean(base_raw.get("ref") or base_raw.get("base_id"))
        }
        if not base["ref"]:
            raise ValueError("DYNAMIC Universe requires base.ref")
        if isinstance(base_raw, Mapping) and _clean(base_raw.get("revision_id")):
            base["revision_id"] = _clean(base_raw.get("revision_id"))
        filters = [self._normalize_filter(item) for item in raw.get("filters") or []]
        rank_raw = raw.get("rank")
        rank: dict[str, str] | None = None
        if rank_raw:
            if not isinstance(rank_raw, Mapping):
                raise ValueError("rank must be an object")
            contract = self.registry.require(rank_raw.get("field"))
            if not contract.rankable:
                raise ValueError(f"Universe field is not rankable: {contract.field_id}")
            order = _clean(rank_raw.get("order") or "DESC").upper()
            if order not in {"ASC", "DESC"}:
                raise ValueError("rank.order must be ASC or DESC")
            rank = {"field": contract.field_id, "order": order}
        select = self._normalize_select(raw.get("select") or {"method": "ALL"})
        if select["method"] != "ALL" and rank is None:
            raise ValueError(f"{select['method']} selection requires rank")
        rebalance = _clean(raw.get("rebalance") or "MONTHLY").upper()
        if rebalance not in REBALANCE_FREQUENCIES:
            raise ValueError(f"unsupported Universe rebalance frequency: {rebalance}")
        result: dict[str, Any] = {
            "schema_version": UNIVERSE_V2_SCHEMA_VERSION,
            "type": "DYNAMIC", "base": base, "filters": filters,
            "select": select, "rebalance": rebalance, **metadata,
        }
        if rank is not None:
            result["rank"] = rank
        return result

    def compile(self, definition: Mapping[str, Any]) -> dict[str, Any]:
        normalized = self.normalize(definition)
        field_ids: list[str] = []
        if normalized["type"] == "DYNAMIC":
            field_ids.extend(item["field"] for item in normalized["filters"])
            if normalized.get("rank"):
                field_ids.append(normalized["rank"]["field"])
        contracts = [self.registry.require(field_id) for field_id in sorted(set(field_ids))]
        requirements_by_key: dict[tuple[str, str, str, str], set[str]] = {}
        warmup_by_key: dict[tuple[str, str, str, str], int] = {}
        for contract in contracts:
            for requirement in contract.requirement_specs():
                key = (
                    requirement.data_type, requirement.frequency,
                    requirement.time_semantics, requirement.point_in_time_policy,
                )
                requirements_by_key.setdefault(key, set()).update(requirement.fields)
                warmup_by_key[key] = max(
                    warmup_by_key.get(key, 0), requirement.warmup_bars,
                )
        requirements = [
            {
                "data_type": data_type,
                "frequency": frequency,
                "fields": sorted(fields),
                "warmup_bars": warmup_by_key[(
                    data_type, frequency, time_semantics, point_in_time_policy,
                )],
                "point_in_time_policy": point_in_time_policy,
                "quality_policy": "STRICT",
            }
            for (
                data_type, frequency, time_semantics, point_in_time_policy,
            ), fields in sorted(requirements_by_key.items())
        ]
        # Universe v2 authoring is provider-neutral. RequirementCompiler freezes
        # the provider-specific time semantics later from the execution context
        # (for example CRSP SOURCE_AVAILABLE_TIME versus OpenBB bar-end time).
        schedule = self._schedule_profile(normalized)
        material = {
            "schema_version": UNIVERSE_V2_COMPILED_SCHEMA_VERSION,
            "engine_version": UNIVERSE_ENGINE_VERSION,
            "definition": normalized,
            "field_contracts": [item.to_dict() for item in contracts],
            "requirements": requirements,
            "membership_schedule": schedule,
            "policies": {
                "filter_join": "AND",
                "missing": "EXCLUDE",
                "as_of": "LATEST_AVAILABLE",
                "tie_break": "INSTRUMENT_ID_ASC",
                "hard_invalid_membership": "REMOVE_IMMEDIATELY",
            },
        }
        return {**material, "fingerprint": _fingerprint(material)}

    def _normalize_filter(self, raw: Any) -> dict[str, Any]:
        if isinstance(raw, (list, tuple)):
            if len(raw) != 3:
                raise ValueError("Universe filter shorthand must contain field, operator, value")
            field, operator, value = raw
        elif isinstance(raw, Mapping):
            field = raw.get("field")
            operator = raw.get("operator") or raw.get("op")
            value = raw.get("value")
        else:
            raise ValueError("Universe filter must be an object or a three-item array")
        contract = self.registry.require(field)
        if not contract.filterable:
            raise ValueError(f"Universe field is not filterable: {contract.field_id}")
        normalized_operator = _OPERATOR_ALIASES.get(_clean(operator).upper())
        if normalized_operator is None or normalized_operator not in contract.operators:
            raise ValueError(
                f"operator {_clean(operator)} is not supported for Universe field {contract.field_id}"
            )
        if normalized_operator in {"IN", "NOT_IN"}:
            if not isinstance(value, (list, tuple, set)) or not value:
                raise ValueError(f"{normalized_operator} requires a non-empty value list")
            normalized_value: Any = sorted({_clean(item) for item in value if _clean(item)})
        elif contract.value_type == "NUMBER":
            try:
                normalized_value = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Universe field {contract.field_id} requires a numeric value") from exc
            if not math.isfinite(normalized_value):
                raise ValueError(f"Universe field {contract.field_id} requires a finite value")
        else:
            normalized_value = _clean(value)
            if not normalized_value:
                raise ValueError(f"Universe field {contract.field_id} requires a value")
        return {"field": contract.field_id, "operator": normalized_operator, "value": normalized_value}

    @staticmethod
    def _normalize_select(raw: Any) -> dict[str, Any]:
        if isinstance(raw, str):
            raw = {"method": raw}
        if not isinstance(raw, Mapping):
            raise ValueError("select must be an object")
        method = _clean(raw.get("method") or "ALL").upper()
        if method not in SELECT_METHODS:
            raise ValueError(f"unsupported Universe selection method: {method}")
        result: dict[str, Any] = {"method": method}
        if method in {"TOP_N", "BOTTOM_N"}:
            value = int(raw.get("value") or 0)
            if value < 1:
                raise ValueError(f"{method} requires a positive value")
            result["value"] = value
            if raw.get("buffer") is not None:
                buffer = dict(raw.get("buffer") or {})
                entry, exit_value = int(buffer.get("entry") or 0), int(buffer.get("exit") or 0)
                if entry < 1 or exit_value < entry:
                    raise ValueError("selection buffer requires 1 <= entry <= exit")
                if not (entry <= value <= exit_value):
                    raise ValueError("selection buffer must satisfy entry <= value <= exit")
                result["buffer"] = {"entry": entry, "exit": exit_value}
        elif method == "PERCENTILE":
            values = list(raw.get("range") or [])
            if len(values) != 2:
                raise ValueError("PERCENTILE requires range [start, end]")
            start, end = float(values[0]), float(values[1])
            if not (0.0 <= start < end <= 1.0):
                raise ValueError("PERCENTILE range must satisfy 0 <= start < end <= 1")
            result["range"] = [start, end]
        return result

    @staticmethod
    def _schedule_profile(definition: Mapping[str, Any]) -> dict[str, str]:
        if definition.get("type") != "DYNAMIC":
            return {}
        base_ref = _clean(dict(definition.get("base") or {}).get("ref")).lower()
        if base_ref.startswith("crypto"):
            calendar = "24X7"
            decision_time = "UTC_DAY_END"
            effective_time = "NEXT_UTC_DAY"
        else:
            calendar = "PRIMARY_LISTING_CALENDAR"
            decision_time = "SESSION_CLOSE"
            effective_time = "NEXT_SESSION_OPEN"
        return {
            "frequency": _clean(definition.get("rebalance")).upper(),
            "calendar": calendar,
            "decision_time": decision_time,
            "information_cutoff": "DECISION_TIME",
            "effective_time": effective_time,
        }


class UniverseMembershipEngine:
    """Pure PIT evaluator for a compiled DYNAMIC Universe.

    Callers provide already-frozen field rows and the exact decision/effective
    schedule. This class never discovers or silently substitutes live data.
    """

    def __init__(self, registry: UniverseFieldRegistry | None = None):
        self.registry = registry or UniverseFieldRegistry.default()

    def materialize(
        self,
        compiled: Mapping[str, Any],
        *,
        base_membership: Mapping[str, Any] | Sequence[str],
        field_rows: Mapping[str, Mapping[str, Sequence[Mapping[str, Any]]]],
        schedule: Sequence[Mapping[str, Any] | str],
        manifest_ids: Sequence[str],
        end_time: str = "",
    ) -> dict[str, Any]:
        definition = dict(compiled.get("definition") or {})
        if compiled.get("engine_version") != UNIVERSE_ENGINE_VERSION or definition.get("type") != "DYNAMIC":
            raise ValueError("UniverseMembershipEngine requires a compiled DYNAMIC Universe v2 definition")
        compiled_material = {key: value for key, value in compiled.items() if key != "fingerprint"}
        if not _clean(compiled.get("fingerprint")) or _fingerprint(compiled_material) != compiled.get("fingerprint"):
            raise ValueError("compiled Universe fingerprint is missing or invalid")
        frozen_manifests = sorted({_clean(item) for item in manifest_ids if _clean(item)})
        if (definition.get("filters") or definition.get("rank")) and not frozen_manifests:
            raise ValueError("dynamic Universe membership requires frozen manifest_ids")
        points = self._normalize_schedule(schedule)
        if not points:
            raise ValueError("dynamic Universe membership requires at least one schedule point")
        timeline: list[dict[str, Any]] = []
        previous: list[str] = []
        for point in points:
            decision = _parse_time(point["decision_time"])
            candidates = sorted(
                instrument_id for instrument_id in self._base_ids(base_membership)
                if self._base_contains(base_membership, instrument_id, decision)
            )
            values: dict[str, dict[str, Any]] = {}
            missing_by_field: dict[str, int] = {}
            for field_id in self._referenced_fields(definition):
                contract = self.registry.require(field_id)
                values[field_id] = {}
                rows_for_field = field_rows.get(field_id) or {}
                for instrument_id in candidates:
                    value = self._latest_value(
                        rows_for_field.get(instrument_id) or (), contract, decision
                    )
                    if value is None:
                        missing_by_field[field_id] = missing_by_field.get(field_id, 0) + 1
                    else:
                        values[field_id][instrument_id] = value
            eligible = list(candidates)
            filter_steps: list[dict[str, Any]] = []
            for rule in definition.get("filters") or []:
                before = len(eligible)
                field_values = values.get(rule["field"], {})
                eligible = [
                    instrument_id for instrument_id in eligible
                    if instrument_id in field_values
                    and self._compare(field_values[instrument_id], rule["operator"], rule["value"])
                ]
                filter_steps.append({
                    "field": rule["field"], "operator": rule["operator"],
                    "before": before, "after": len(eligible), "excluded": before - len(eligible),
                })
            ranked = self._rank(eligible, definition.get("rank"), values)
            selected = self._select(ranked, definition["select"], previous)
            timeline.append({
                "decision_time": point["decision_time"],
                "effective_time": point["effective_time"],
                "base_count": len(candidates),
                "eligible_count": len(eligible),
                "selected_count": len(selected),
                "instrument_ids": selected,
                "missing_by_field": missing_by_field,
                "filter_steps": filter_steps,
            })
            previous = selected
        final_end = _parse_time(end_time) if _clean(end_time) else _parse_time(points[-1]["effective_time"]) + timedelta(microseconds=1)
        if final_end <= _parse_time(points[-1]["effective_time"]):
            raise ValueError("Universe end_time must follow the last effective_time")
        segments = self._segments(timeline, final_end)
        actual = sorted({item for row in timeline for item in row["instrument_ids"]})
        material = {
            "schema_version": UNIVERSE_MEMBERSHIP_SCHEMA_VERSION,
            "engine_version": UNIVERSE_ENGINE_VERSION,
            "compiled_fingerprint": _clean(compiled.get("fingerprint")),
            "manifest_ids": frozen_manifests,
            "actual_instrument_ids": actual,
            "timeline": timeline,
            "membership_segments": segments,
            "policies": dict(compiled.get("policies") or {}),
        }
        return {**material, "fingerprint": _fingerprint(material)}

    @staticmethod
    def _normalize_schedule(values: Sequence[Mapping[str, Any] | str]) -> list[dict[str, str]]:
        normalized = []
        for item in values:
            raw = {"decision_time": item, "effective_time": item} if isinstance(item, str) else dict(item)
            decision = _parse_time(raw.get("decision_time"))
            effective = _parse_time(raw.get("effective_time") or raw.get("decision_time"))
            if effective < decision:
                raise ValueError("Universe effective_time must not precede decision_time")
            normalized.append({"decision_time": decision.isoformat(), "effective_time": effective.isoformat()})
        normalized.sort(key=lambda item: (_parse_time(item["effective_time"]), _parse_time(item["decision_time"])))
        if len({item["effective_time"] for item in normalized}) != len(normalized):
            raise ValueError("Universe schedule effective_time values must be unique")
        return normalized

    @staticmethod
    def _base_ids(base: Mapping[str, Any] | Sequence[str]) -> list[str]:
        if isinstance(base, Mapping):
            return [str(item) for item in base]
        return [str(item) for item in base]

    @staticmethod
    def _base_contains(base: Mapping[str, Any] | Sequence[str], instrument_id: str, decision: datetime) -> bool:
        if not isinstance(base, Mapping):
            return instrument_id in base
        raw = base.get(instrument_id)
        if raw in (None, True):
            return raw is True
        segments = raw if isinstance(raw, list) else [raw]
        for segment in segments:
            if not isinstance(segment, Mapping):
                continue
            start_raw = segment.get("eligible_from_time") or segment.get("eligible_from")
            end_raw = segment.get("eligible_to_exclusive") or segment.get("eligible_to")
            start = _parse_time(start_raw) if start_raw else datetime.min.replace(tzinfo=timezone.utc)
            end = _parse_time(end_raw) if end_raw else datetime.max.replace(tzinfo=timezone.utc)
            if segment.get("eligible_to") and not segment.get("eligible_to_exclusive") and len(_clean(end_raw)) == 10:
                end += timedelta(days=1)
            if start <= decision < end:
                return True
        return False

    @staticmethod
    def _referenced_fields(definition: Mapping[str, Any]) -> list[str]:
        values = [str(item["field"]) for item in definition.get("filters") or []]
        if definition.get("rank"):
            values.append(str(definition["rank"]["field"]))
        return sorted(set(values))

    @staticmethod
    def _latest_value(
        rows: Sequence[Mapping[str, Any]], contract: UniverseFieldContract, decision: datetime
    ) -> Any | None:
        candidates: list[tuple[datetime, datetime, int, Mapping[str, Any]]] = []
        for index, row in enumerate(rows):
            available_raw = row.get("available_time")
            if not available_raw:
                continue
            available = _parse_time(available_raw)
            if available > decision:
                continue
            event = _parse_time(row.get("event_time") or available_raw)
            candidates.append((available, event, index, row))
        if not candidates:
            return None
        row = max(candidates, key=lambda item: (item[0], item[1], item[2]))[3]
        raw = row.get("value")
        if raw is None and contract.calculation != "SOURCE":
            raw = row.get(contract.field_id)
        if raw is None and contract.calculation == "SOURCE":
            for source_field in contract.source_fields:
                if row.get(source_field) is not None:
                    raw = row[source_field]
                    break
        if raw is None:
            return None
        if contract.value_type == "NUMBER":
            try:
                value = float(raw) * contract.unit_scale
            except (TypeError, ValueError):
                return None
            return value if math.isfinite(value) else None
        return _clean(raw) or None

    @staticmethod
    def _compare(actual: Any, operator: str, expected: Any) -> bool:
        if operator == "EQ": return actual == expected
        if operator == "NE": return actual != expected
        if operator == "GT": return actual > expected
        if operator == "GTE": return actual >= expected
        if operator == "LT": return actual < expected
        if operator == "LTE": return actual <= expected
        if operator == "IN": return actual in expected
        if operator == "NOT_IN": return actual not in expected
        raise ValueError(f"unsupported Universe operator: {operator}")

    @staticmethod
    def _rank(
        eligible: Sequence[str], rank: Mapping[str, Any] | None,
        values: Mapping[str, Mapping[str, Any]],
    ) -> list[str]:
        if not rank:
            return sorted(eligible)
        field_values = values.get(str(rank["field"]), {})
        ranked = [item for item in eligible if item in field_values]
        ranked.sort()
        ranked.sort(key=lambda item: field_values[item], reverse=str(rank["order"]) == "DESC")
        return ranked

    @staticmethod
    def _select(ranked: Sequence[str], select: Mapping[str, Any], previous: Sequence[str]) -> list[str]:
        method = str(select["method"])
        if method == "ALL":
            return list(ranked)
        if method == "PERCENTILE":
            start, end = select["range"]
            left = int(math.floor(float(start) * len(ranked)))
            right = int(math.ceil(float(end) * len(ranked)))
            return list(ranked[left:right])
        oriented = list(ranked) if method == "TOP_N" else list(reversed(ranked))
        target = min(int(select["value"]), len(oriented))
        buffer = select.get("buffer")
        if not buffer or not previous:
            return oriented[:target]
        positions = {instrument_id: index + 1 for index, instrument_id in enumerate(oriented)}
        keep = {
            instrument_id for instrument_id in previous
            if positions.get(instrument_id, math.inf) <= int(buffer["exit"])
        }
        enter = {
            instrument_id for instrument_id in oriented
            if instrument_id not in keep and positions[instrument_id] <= int(buffer["entry"])
        }
        selected = keep | enter
        for instrument_id in oriented:
            if len(selected) >= target:
                break
            selected.add(instrument_id)
        return [instrument_id for instrument_id in oriented if instrument_id in selected][:target]

    @staticmethod
    def _segments(timeline: Sequence[Mapping[str, Any]], end_time: datetime) -> dict[str, list[dict[str, str]]]:
        result: dict[str, list[dict[str, str]]] = {}
        for index, row in enumerate(timeline):
            start = _parse_time(row["effective_time"])
            end = _parse_time(timeline[index + 1]["effective_time"]) if index + 1 < len(timeline) else end_time
            if end <= start:
                continue
            for instrument_id in row["instrument_ids"]:
                values = result.setdefault(str(instrument_id), [])
                if values and _parse_time(values[-1]["eligible_to_exclusive"]) == start:
                    values[-1]["eligible_to_exclusive"] = end.isoformat()
                else:
                    values.append({
                        "eligible_from_time": start.isoformat(),
                        "eligible_to_exclusive": end.isoformat(),
                    })
        return result


def universe_v2_capabilities() -> dict[str, Any]:
    registry = UniverseFieldRegistry.default()
    fields = registry.capabilities()
    dynamic_filters = []
    for item in fields:
        # This legacy capability key means "wired into the current formal
        # pipeline", not merely registered for v2 authoring.
        if not item["filterable"] or not item["formal_pipeline"]:
            continue
        dynamic_filters.append({
            "field": item["field_id"],
            "operators": item["operators"],
            "filterable": item["filterable"],
            "rankable": item["rankable"],
            "pit_safe": item["pit_safe"],
            "coverage": "CATALOG_DEPENDENT",
            "as_of_policy": "LATEST_AVAILABLE",
            "missing_policy": item["missing_policy"],
            "requires_frozen_formal_evaluation": True,
            **({"bounds": ["minimum", "maximum"]} if item["value_type"] == "NUMBER" else {}),
        })
    dynamic_filters.sort(key=lambda item: (item["field"] != "market_cap_usd", item["field"]))
    return {
        "schema_version": UNIVERSE_V2_SCHEMA_VERSION,
        "engine_version": UNIVERSE_ENGINE_VERSION,
        "product_types": ["STATIC", "DYNAMIC", "COMPOSITE"],
        "dynamic_modules": ["BASE", "FILTER", "RANK", "SELECT", "REBALANCE"],
        "identity_mode": "HISTORICAL_EQUITY_PIT",
        "selection_methods": ["ALL_ELIGIBLE"],
        "authoring_selection_methods": ["ALL", "TOP_N", "BOTTOM_N", "PERCENTILE"],
        "rebalance_frequencies": sorted(REBALANCE_FREQUENCIES),
        "composite_operators": sorted(COMPOSITE_OPERATORS),
        "field_registry": fields,
        "field_execution_status": {
            item["field_id"]: (
                "FORMAL_PIPELINE" if item["formal_pipeline"]
                else "REGISTERED_NOT_YET_BOUND"
            )
            for item in fields
        },
        "dynamic_point_in_time_filters": dynamic_filters,
        "filter_join": "AND",
        "tie_break": "INSTRUMENT_ID_ASC",
        "missing_policy": "EXCLUDE",
        "dynamic_authoring": "VALIDATE_AND_COMPILE",
        "dynamic_persistence": "REQUIRES_FROZEN_FORMAL_EVALUATION",
        "forbidden_shortcut": "whole-period mean or median field values",
    }
