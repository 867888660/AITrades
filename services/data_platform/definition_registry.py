from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from .factor_alpha import (
    ALPHA_ENGINE_VERSION,
    FACTOR_ALPHA_CODE_HASH,
    FACTOR_ENGINE_VERSION,
    AlphaComponent,
    AlphaSpec,
    FactorSpec,
)
from .factor_engine_v4 import (
    FACTOR_ENGINE_V4_CODE_HASH,
    FACTOR_ENGINE_V4_VERSION,
    FACTOR_GRAPH_CONTRACT_VERSION,
    FactorGraphSpec,
)
from .store import DataPlatformStore, json_dumps, utc_now


DEFINITION_STATES = {"DRAFT", "VALIDATED", "SUPERSEDED", "ARCHIVED"}
REFERENCE_MODES = {"PINNED", "TRACK_DRAFT"}
DEFINITION_TYPES = {"FACTOR", "ALPHA"}


@dataclass(frozen=True)
class ResearchDefinition:
    definition_id: str
    definition_type: str
    name: str
    version: str
    state: str
    spec: dict[str, Any]
    spec_hash: str
    engine_version: str
    code_hash: str
    created_by: str
    created_at: str
    owner_project_id: str = ""
    library_scope: str = "GLOBAL"
    validated_at: str | None = None
    superseded_by_id: str | None = None
    archived_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(json_dumps(value).encode("utf-8")).hexdigest()


class DefinitionRegistry:
    """Immutable Factor/Alpha definitions plus explicit project references."""

    def __init__(self, store: DataPlatformStore):
        self.store = store

    @staticmethod
    def engine_capabilities() -> dict[str, Any]:
        integer_window = {
            "name": "window",
            "label": "窗口",
            "type": "integer",
            "minimum": 1,
            "default": 20,
            "required": True,
        }
        operator_schema = [
            {
                "id": "pct_change",
                "label": "区间收益率",
                "signature": "pct_change(field, window)",
                "description": "当前值相对 window 根 K 线前的百分比变化。",
                "parameters": [integer_window],
                "output_type": "NUMBER",
                "output_unit": "RATIO",
                "warmup": "window + 1",
                "pit_safe": True,
                "required_fields": ["selected_input_field"],
            },
            {
                "id": "difference",
                "label": "区间差值",
                "signature": "difference(field, window)",
                "description": "当前值减去 window 根 K 线前的值。",
                "parameters": [integer_window],
                "output_type": "NUMBER",
                "output_unit": "ABSOLUTE",
                "warmup": "window + 1",
                "pit_safe": True,
                "required_fields": ["selected_input_field"],
            },
            {
                "id": "ratio",
                "label": "区间比值",
                "signature": "ratio(field, window)",
                "description": "当前值除以 window 根 K 线前的值。",
                "parameters": [integer_window],
                "output_type": "NUMBER",
                "output_unit": "RATIO",
                "warmup": "window + 1",
                "pit_safe": True,
                "required_fields": ["selected_input_field"],
            },
            {
                "id": "rolling_mean",
                "label": "滚动均值",
                "signature": "rolling_mean(field, window)",
                "description": "最近 window 根完整 K 线的算术平均值。",
                "parameters": [integer_window],
                "output_type": "NUMBER",
                "output_unit": "SOURCE",
                "warmup": "window",
                "pit_safe": True,
                "required_fields": ["selected_input_field"],
            },
            {
                "id": "rolling_std",
                "label": "滚动标准差",
                "signature": "rolling_std(field, window)",
                "description": "最近 window 根完整 K 线的总体标准差。",
                "parameters": [integer_window],
                "output_type": "NUMBER",
                "output_unit": "SOURCE",
                "warmup": "window",
                "pit_safe": True,
                "required_fields": ["selected_input_field"],
            },
            {
                "id": "rolling_return_std",
                "label": "滚动收益波动率",
                "signature": "rolling_return_std(field, window)",
                "description": "最近 window 个区间收益率的总体标准差。",
                "parameters": [integer_window],
                "output_type": "NUMBER",
                "output_unit": "RATIO",
                "warmup": "window + 1",
                "pit_safe": True,
                "required_fields": ["selected_input_field"],
            },
            {
                "id": "ema",
                "label": "指数移动平均",
                "signature": "ema(field, window)",
                "description": "以简单均值初始化的确定性指数移动平均。",
                "parameters": [integer_window],
                "output_type": "NUMBER",
                "output_unit": "SOURCE",
                "warmup": "window",
                "pit_safe": True,
                "required_fields": ["selected_input_field"],
            },
            {
                "id": "ma_crossover",
                "label": "均线金叉 / 死叉",
                "signature": "ma_crossover(field, fast_window, slow_window)",
                "description": "快线上穿慢线输出 1，下穿输出 -1，其余输出 0。",
                "parameters": [
                    {
                        "name": "fast_window",
                        "label": "快线窗口",
                        "type": "integer",
                        "minimum": 1,
                        "default": 5,
                        "required": True,
                        "constraint": "fast_window < window",
                    },
                    {
                        **integer_window,
                        "label": "慢线窗口",
                        "default": 20,
                    },
                ],
                "output_type": "SIGNAL",
                "output_unit": "DISCRETE",
                "output_values": {"golden_cross": 1, "death_cross": -1, "otherwise": 0},
                "warmup": "slow_window + 1",
                "pit_safe": True,
                "required_fields": ["selected_input_field"],
            },
        ]
        graph_function_schema = [
            {
                "id": "time.pct_change",
                "label": "Percentage Change",
                "category": "Over Time",
                "signature": "time.pct_change(series, periods)",
                "description": "Calculate percentage change for each Instrument over time.",
                "parameters": [integer_window],
                "output_type": "NUMBER",
                "output_unit": "RATIO",
                "warmup": "window + 1",
                "pit_safe": True,
            },
            {
                "id": "time.diff",
                "label": "Difference",
                "category": "Over Time",
                "signature": "time.diff(series, periods)",
                "description": "Calculate the change from a previous value.",
                "parameters": [integer_window],
                "output_type": "NUMBER",
                "output_unit": "SOURCE",
                "warmup": "window + 1",
                "pit_safe": True,
            },
            {
                "id": "time.mean",
                "label": "Rolling Mean",
                "category": "Over Time",
                "signature": "time.mean(series, window)",
                "description": "Calculate a rolling mean for each Instrument.",
                "parameters": [integer_window],
                "output_type": "NUMBER",
                "output_unit": "SOURCE",
                "warmup": "window",
                "pit_safe": True,
            },
            {
                "id": "time.std",
                "label": "Rolling Standard Deviation",
                "category": "Over Time",
                "signature": "time.std(series, window)",
                "description": "Calculate rolling dispersion for each Instrument.",
                "parameters": [integer_window],
                "output_type": "NUMBER",
                "output_unit": "SOURCE",
                "warmup": "window",
                "pit_safe": True,
            },
            {
                "id": "time.return_std",
                "label": "Return Volatility",
                "category": "Over Time",
                "signature": "time.return_std(series, window)",
                "description": "Calculate rolling return volatility.",
                "parameters": [integer_window],
                "output_type": "NUMBER",
                "output_unit": "RATIO",
                "warmup": "window + 1",
                "pit_safe": True,
            },
            {
                "id": "time.ema",
                "label": "Exponential Moving Average",
                "category": "Over Time",
                "signature": "time.ema(series, window)",
                "description": "Calculate an exponential moving average.",
                "parameters": [integer_window],
                "output_type": "NUMBER",
                "output_unit": "SOURCE",
                "warmup": "window",
                "pit_safe": True,
            },
            {
                "id": "universe.rank",
                "label": "Universe Rank",
                "category": "Across Universe",
                "signature": "universe.rank(series)",
                "description": "Rank values across the current Universe at each evaluation time.",
                "parameters": [],
                "output_type": "NUMBER",
                "output_unit": "PERCENTILE",
                "warmup": "input warmup",
                "pit_safe": True,
            },
            {
                "id": "time.ma_crossover",
                "label": "Moving Average Crossover",
                "category": "Over Time",
                "signature": "time.ma_crossover(series, fast_window, slow_window)",
                "description": "Detect crossings between fast and slow moving averages.",
                "parameters": [
                    {
                        "name": "fast_window",
                        "label": "Fast window",
                        "type": "integer",
                        "minimum": 1,
                        "default": 5,
                        "required": True,
                    },
                    {
                        **integer_window,
                        "label": "Slow window",
                    },
                ],
                "output_type": "SIGNAL",
                "output_unit": "DISCRETE",
                "warmup": "slow_window + 1",
                "pit_safe": True,
            },
            {
                "id": "universe.zscore",
                "label": "Universe Z-score",
                "category": "Across Universe",
                "signature": "universe.zscore(series)",
                "description": "Standardize values across the current Universe.",
                "parameters": [],
                "output_type": "NUMBER",
                "output_unit": "ZSCORE",
                "warmup": "input warmup",
                "pit_safe": True,
            },
        ]
        graph_function_schema.extend([
            {
                "id": function_id,
                "label": label,
                "category": category,
                "signature": signature,
                "description": description,
                "parameters": [integer_window] if uses_window else [],
                "series_arguments": series_arguments,
                "output_type": output_type,
                "output_unit": output_unit,
                "warmup": warmup,
                "pit_safe": True,
            }
            for (
                function_id, label, category, signature, description,
                uses_window, series_arguments, output_type, output_unit, warmup,
            ) in [
                ("time.lag", "Lag", "Over Time", "time.lag(series, periods)", "Shift a series by a number of bars.", True, 1, "NUMBER", "SOURCE", "window + 1"),
                ("time.ratio", "Historical Ratio", "Over Time", "time.ratio(series, periods)", "Divide the latest value by an earlier value.", True, 1, "NUMBER", "RATIO", "window + 1"),
                ("time.log_return", "Log Return", "Over Time", "time.log_return(series, periods)", "Calculate logarithmic return over time.", True, 1, "NUMBER", "RATIO", "window + 1"),
                ("time.sum", "Rolling Sum", "Over Time", "time.sum(series, window)", "Calculate a rolling sum for each Instrument.", True, 1, "NUMBER", "SOURCE", "window"),
                ("time.median", "Rolling Median", "Over Time", "time.median(series, window)", "Calculate a rolling median for each Instrument.", True, 1, "NUMBER", "SOURCE", "window"),
                ("time.min", "Rolling Minimum", "Over Time", "time.min(series, window)", "Calculate a rolling minimum for each Instrument.", True, 1, "NUMBER", "SOURCE", "window"),
                ("time.max", "Rolling Maximum", "Over Time", "time.max(series, window)", "Calculate a rolling maximum for each Instrument.", True, 1, "NUMBER", "SOURCE", "window"),
                ("time.variance", "Rolling Variance", "Over Time", "time.variance(series, window)", "Calculate rolling variance for each Instrument.", True, 1, "NUMBER", "COMPOSITE", "window"),
                ("time.rank", "Rolling Rank", "Over Time", "time.rank(series, window)", "Rank the latest value against its own rolling history.", True, 1, "NUMBER", "PERCENTILE", "window"),
                ("time.zscore", "Rolling Z-score", "Over Time", "time.zscore(series, window)", "Standardize the latest value against its own rolling history.", True, 1, "NUMBER", "ZSCORE", "window"),
                ("universe.percentile", "Universe Percentile", "Across Universe", "universe.percentile(series)", "Convert values to percentiles across the current Universe.", False, 1, "NUMBER", "PERCENTILE", "input warmup"),
                ("universe.demean", "Universe Demean", "Across Universe", "universe.demean(series)", "Subtract the current Universe mean.", False, 1, "NUMBER", "SOURCE", "input warmup"),
                ("align.asof", "As-of Alignment", "Alignment", "align.asof(source, reference)", "Use the latest source value available at each reference time.", False, 2, "NUMBER", "SOURCE", "source warmup"),
                ("align.forward_fill", "Forward-fill Alignment", "Alignment", "align.forward_fill(source, reference)", "Forward-fill available source values onto the reference time axis.", False, 2, "NUMBER", "SOURCE", "source warmup"),
                ("greater", "Greater Than", "Conditional", "greater(left, right)", "Compare two numeric series.", False, 2, "BOOLEAN", "BOOLEAN", "input warmup"),
                ("less", "Less Than", "Conditional", "less(left, right)", "Compare two numeric series.", False, 2, "BOOLEAN", "BOOLEAN", "input warmup"),
                ("equal", "Equal", "Conditional", "equal(left, right)", "Compare two numeric series.", False, 2, "BOOLEAN", "BOOLEAN", "input warmup"),
                ("where", "Where", "Conditional", "where(condition, when_true, when_false)", "Choose between two series using a Boolean condition.", False, 3, "NUMBER", "SOURCE", "input warmup"),
                ("is_null", "Is Null", "Conditional", "is_null(series)", "Identify unavailable values.", False, 1, "BOOLEAN", "BOOLEAN", "input warmup"),
                ("is_finite", "Is Finite", "Conditional", "is_finite(series)", "Identify finite numeric values.", False, 1, "BOOLEAN", "BOOLEAN", "input warmup"),
                ("fill_null", "Fill Null", "Conditional", "fill_null(series, replacement)", "Replace unavailable values from another aligned series.", False, 2, "NUMBER", "SOURCE", "input warmup"),
                ("logical_and", "Logical And", "Conditional", "logical_and(left, right)", "Combine two Boolean series.", False, 2, "BOOLEAN", "BOOLEAN", "input warmup"),
                ("logical_or", "Logical Or", "Conditional", "logical_or(left, right)", "Combine two Boolean series.", False, 2, "BOOLEAN", "BOOLEAN", "input warmup"),
                ("logical_not", "Logical Not", "Conditional", "logical_not(series)", "Invert a Boolean series.", False, 1, "BOOLEAN", "BOOLEAN", "input warmup"),
                ("safe_divide", "Safe Divide", "Math", "safe_divide(numerator, denominator)", "Divide two series while treating zero denominators as unavailable.", False, 2, "NUMBER", "RATIO", "input warmup"),
                ("abs", "Absolute Value", "Math", "abs(series)", "Use the absolute value of a series.", False, 1, "NUMBER", "SOURCE", "input warmup"),
            ]
        ])
        features = [
            {
                "id": field,
                "label": label,
                "data_type": "NUMBER",
                "dataset": "bars",
                "available_after": "BAR_CLOSE",
                "pit_safe": True,
            }
            for field, label in (
                ("open", "开盘价"),
                ("high", "最高价"),
                ("low", "最低价"),
                ("close", "收盘价"),
                ("volume", "成交量"),
                ("quote_volume", "计价资产成交额"),
                ("trade_count", "成交笔数"),
            )
        ]
        features.append({
            "id": "price",
            "label": "Outcome Price",
            "data_type": "NUMBER",
            "dataset": "price_history",
            "available_after": "EVENT_AVAILABLE",
            "pit_safe": True,
        })
        return {
            "factor": {
                "engine_version": FACTOR_ENGINE_V4_VERSION,
                "compatible_engine_versions": [FACTOR_ENGINE_VERSION],
                "operators": [item["id"] for item in graph_function_schema],
                "operator_schema": graph_function_schema,
                "function_schema": graph_function_schema,
                "function_categories": [
                    "Math",
                    "Over Time",
                    "Across Universe",
                    "Conditional",
                    "Alignment",
                ],
                "planned_function_categories": [
                    "Within Group",
                    "Financial",
                ],
                "binary_operators": ["+", "-", "*", "/"],
                "operator_parameters": {
                    "ma_crossover": {
                        "fast_window": "positive integer smaller than window",
                        "window": "slow moving-average window",
                        "output": {"golden_cross": 1, "death_cross": -1, "otherwise": 0},
                    },
                },
                "authoring_contract": {
                    "document_version": "factor_draft.v2",
                    "input_model": "VARIABLE_LIST",
                    "max_inputs": 8,
                    "parameter_model": "NAMED_LIST",
                    "parameter_units": ["bars"],
                    "formula_source_editable": True,
                    "function_picker_role": "BROWSE_AND_AUTOCOMPLETE",
                    "input_catalog_role": "SUPPORTED_FIELDS_WITH_EXPLICIT_CONFIRMATION",
                    "autocomplete_triggers": ["@", "identifier"],
                    "supports_nested_expressions": True,
                    "supports_composed_expressions": True,
                    "supports_multiple_inputs": True,
                    "supports_named_results": True,
                    "supports_multiline_formula": True,
                    "supports_scalar_window_literals": True,
                    "final_output_model": "EXPLICIT_NAMED_RESULT_OR_SINGLE_EXPRESSION",
                    "max_formula_statements": 32,
                    "max_formula_ast_nodes": 512,
                    "max_formula_ast_depth": 32,
                    "frequency_alignment": "EXPLICIT_ASOF_4B",
                    "alignment_functions": ["align.asof", "align.forward_fill"],
                    "validation_flow": [
                        "AUTO_BACKUP",
                        "SAVE_DRAFT",
                        "RUN_PREVIEW",
                        "VALIDATE_FACTOR",
                    ],
                    "preview_role": "REQUIRED_VALIDATION_EVIDENCE",
                    "preview_contract": "factor_preview.v1",
                },
                "dimensions": ["TIME_SERIES", "CROSS_SECTIONAL", "HYBRID"],
                "available_after": ["BAR_CLOSE", "EVENT_AVAILABLE"],
                "missing_policies": ["STRICT", "SKIP"],
                "features": features,
                "frequencies": ["1m", "5m", "15m", "1h", "4h", "1d"],
                "output_directions": [
                    "NO_PREDEFINED_DIRECTION",
                    "HIGHER_IS_BETTER",
                    "LOWER_IS_BETTER",
                    "EVENT_SIGNAL",
                ],
                "time_alignment_policy": "BAR_END_AVAILABLE_TIME",
                "allow_incomplete_bar_default": False,
                "requirement_contract": {
                    "data_type": "bars",
                    "fields": "all_referenced_input_fields",
                    "warmup": "compiled graph required_history",
                    "adjustment": "NONE",
                    "point_in_time_policy": "AS_OF",
                    "quality_policy": "STRICT",
                },
                "formula_contract": FACTOR_GRAPH_CONTRACT_VERSION,
            },
            "alpha": {
                "engine_version": ALPHA_ENGINE_VERSION,
                "authoring_contract": {
                    "document_version": "alpha_draft.v2",
                    "input_model": "PINNED_FACTOR_LIST",
                    "max_components": 8,
                    "formula_model": "WEIGHTED_COMPONENT_SUM",
                    "formula_source_editable": False,
                    "supports_negative_weights": True,
                    "supports_transforms": ["RAW", "CS_RANK"],
                    "validation_flow": [
                        "AUTO_BACKUP",
                        "SAVE_DRAFT",
                        "RUN_PREVIEW",
                        "VALIDATE_ALPHA",
                    ],
                    "preview_role": "REQUIRED_VALIDATION_EVIDENCE",
                    "preview_contract": "alpha_preview.v1",
                },
                "transforms": ["CS_RANK", "RAW"],
                "transform_schema": [
                    {
                        "id": "CS_RANK",
                        "label": "横截面百分位",
                        "signature": "cs_rank(pinned_factor, ascending)",
                        "output_type": "PERCENTILE",
                        "pit_safe": True,
                    },
                    {
                        "id": "RAW",
                        "label": "原始值",
                        "signature": "raw(pinned_factor)",
                        "output_type": "NUMBER",
                        "pit_safe": True,
                    },
                ],
                "rank_methods": ["AVERAGE"],
                "output_scales": ["PERCENTILE"],
                "missing_policies": ["EXCLUDE"],
                "component_contract": {
                    "required_reference": ["factor_definition_id", "factor_version"],
                    "parameters": [
                        {"name": "weight", "type": "number", "required": True},
                        {"name": "transform", "type": "enum", "values": ["CS_RANK", "RAW"]},
                        {"name": "ascending", "type": "boolean", "default": True},
                    ],
                },
                "settings_schema": [
                    {
                        "name": "minimum_coverage",
                        "label": "最低覆盖率",
                        "type": "number",
                        "minimum": 0.01,
                        "maximum": 1,
                        "default": 1,
                    },
                    {
                        "name": "minimum_cross_section_size",
                        "label": "最小横截面数量",
                        "type": "integer",
                        "minimum": 1,
                        "default": 2,
                    },
                ],
                "requirement_contract": {
                    "factor_references": "PINNED_VALIDATED_ONLY",
                    "universe_snapshot": "REQUIRED_FOR_RUN",
                    "coverage": "minimum_coverage",
                    "point_in_time_policy": "AS_OF",
                },
                "formula_contract": "alpha_formula.v2",
            },
            "code_hash": FACTOR_ALPHA_CODE_HASH,
        }

    def _normalize_factor(self, raw: dict[str, Any]) -> tuple[dict[str, Any], str, str, str, str]:
        formula = raw.get("formula") if isinstance(raw.get("formula"), dict) else {}
        if (
            _clean(raw.get("engine_version")) == FACTOR_ENGINE_V4_VERSION
            or _clean(raw.get("formula_contract")) == FACTOR_GRAPH_CONTRACT_VERSION
            or isinstance(formula.get("ast"), dict)
        ):
            spec = FactorGraphSpec.from_dict(raw)
            canonical = spec.to_dict()
            return canonical, spec.name, spec.version, spec.engine_version, spec.code_hash
        spec = FactorSpec(
            name=_clean(raw.get("name")),
            version=_clean(raw.get("version")),
            operator=_clean(raw.get("operator") or formula.get("operator")),
            input_field=_clean(raw.get("input_field") or formula.get("input") or "close"),
            window=int(raw.get("window") or formula.get("window") or 1),
            minimum_observations=raw.get("minimum_observations"),
            missing_policy=_clean(raw.get("missing_policy") or "STRICT"),
            parameters=dict(raw.get("parameters") or formula.get("parameters") or {}),
            frequency=_clean(raw.get("frequency")),
            dimension=_clean(raw.get("dimension") or "TIME_SERIES"),
            time_alignment_policy=_clean(raw.get("time_alignment_policy") or "BAR_END_AVAILABLE_TIME"),
            available_after=_clean(raw.get("available_after") or "BAR_CLOSE"),
            allow_incomplete_bar=bool(raw.get("allow_incomplete_bar", False)),
            output_unit=_clean(raw.get("output_unit") or "RATIO"),
            output_direction=_clean(raw.get("output_direction") or "NO_PREDEFINED_DIRECTION"),
        )
        canonical = spec.to_dict()
        return canonical, spec.name, spec.version, spec.engine_version, spec.code_hash

    def _normalize_alpha(self, raw: dict[str, Any]) -> tuple[dict[str, Any], str, str, str, str]:
        components: list[AlphaComponent] = []
        normalized_refs: list[dict[str, Any]] = []
        for item in raw.get("components") or []:
            ref_id = _clean(item.get("factor_definition_id"))
            ref_version = _clean(item.get("factor_version") or item.get("version"))
            if not ref_id or not ref_version:
                raise ValueError("Alpha components require factor_definition_id and factor_version")
            factor = self.get(ref_id, version=ref_version)
            if factor is None or factor.definition_type != "FACTOR":
                raise ValueError(f"Factor definition not found: {ref_id}@{ref_version}")
            if factor.state != "VALIDATED":
                raise ValueError(f"Alpha may only pin a VALIDATED Factor: {ref_id}@{ref_version}")
            component = AlphaComponent(
                factor_name=factor.name,
                weight=float(item.get("weight", 0.0)),
                transform=_clean(item.get("transform") or "CS_RANK"),
                ascending=bool(item.get("ascending", True)),
            )
            components.append(component)
            normalized_refs.append({
                "factor_definition_id": factor.definition_id,
                "factor_version": factor.version,
                "factor_spec_hash": factor.spec_hash,
                "factor_name": factor.name,
                "weight": component.weight,
                "transform": component.transform.upper(),
                "ascending": component.ascending,
            })
        spec = AlphaSpec(
            name=_clean(raw.get("name")),
            version=_clean(raw.get("version")),
            components=tuple(components),
            minimum_coverage=float(raw.get("minimum_coverage", 1.0)),
            universe_snapshot_id=_clean(raw.get("universe_snapshot_id")),
            minimum_cross_section_size=int(raw.get("minimum_cross_section_size", 1)),
            missing_policy=_clean(raw.get("missing_policy") or "EXCLUDE"),
            rank_method=_clean(raw.get("rank_method") or "AVERAGE"),
            output_scale=_clean(raw.get("output_scale") or "PERCENTILE"),
        )
        canonical = spec.to_dict()
        canonical["components"] = normalized_refs
        return canonical, spec.name, spec.version, spec.engine_version, spec.code_hash

    def create(
        self,
        definition_type: str,
        spec: dict[str, Any],
        *,
        state: str = "DRAFT",
        created_by: str = "local_user",
        definition_id: str = "",
        owner_project_id: str = "",
        library_scope: str = "GLOBAL",
    ) -> ResearchDefinition:
        definition_type = _clean(definition_type).upper()
        state = _clean(state).upper() or "DRAFT"
        if definition_type not in DEFINITION_TYPES:
            raise ValueError(f"unsupported definition type: {definition_type}")
        if state not in {"DRAFT", "VALIDATED"}:
            raise ValueError("new definitions must be DRAFT or VALIDATED")
        if definition_type == "FACTOR":
            canonical, name, version, engine_version, code_hash = self._normalize_factor(dict(spec))
        else:
            canonical, name, version, engine_version, code_hash = self._normalize_alpha(dict(spec))
        spec_hash = _canonical_hash(canonical)
        now = utc_now()
        definition_id = _clean(definition_id) or f"{definition_type.lower()}_{uuid.uuid4().hex}"
        library_scope = _clean(library_scope).upper() or "GLOBAL"
        owner_project_id = _clean(owner_project_id)
        if library_scope not in {"PROJECT", "GLOBAL"}:
            raise ValueError("library_scope must be PROJECT or GLOBAL")
        if library_scope == "PROJECT" and not owner_project_id:
            raise ValueError("PROJECT definitions require owner_project_id")
        with self.store.transaction(immediate=True) as conn:
            if owner_project_id and conn.execute(
                "SELECT 1 FROM research_projects WHERE project_id=?", (owner_project_id,)
            ).fetchone() is None:
                raise ValueError("research project not found")
            existing = conn.execute(
                "SELECT definition_id FROM research_definitions WHERE definition_type=? AND name=? AND version=?",
                (definition_type, name, version),
            ).fetchone()
            if existing:
                found = self.get(str(existing[0]))
                if found and found.spec_hash == spec_hash:
                    return found
                raise ValueError(f"immutable definition version already exists: {definition_type} {name}@{version}")
            conn.execute(
                """
                INSERT INTO research_definitions(
                    definition_id, definition_type, name, version, state, spec_json,
                    spec_hash, engine_version, code_hash, created_by, created_at, validated_at,
                    owner_project_id, library_scope
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    definition_id, definition_type, name, version, state,
                    json_dumps(canonical), spec_hash, engine_version, code_hash,
                    _clean(created_by) or "local_user", now, now if state == "VALIDATED" else None,
                    owner_project_id, library_scope,
                ),
            )
        return self.get(definition_id)  # type: ignore[return-value]

    def validate(self, definition_id: str) -> ResearchDefinition:
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            row = conn.execute(
                "SELECT state FROM research_definitions WHERE definition_id=?", (_clean(definition_id),)
            ).fetchone()
            if row is None:
                raise ValueError("definition not found")
            if str(row[0]) in {"SUPERSEDED", "ARCHIVED"}:
                raise ValueError("a superseded or archived definition cannot be validated")
            conn.execute(
                "UPDATE research_definitions SET state='VALIDATED', validated_at=COALESCE(validated_at, ?) WHERE definition_id=?",
                (now, _clean(definition_id)),
            )
        return self.get(definition_id)  # type: ignore[return-value]

    def get(self, definition_id: str, *, version: str = "") -> ResearchDefinition | None:
        sql = "SELECT * FROM research_definitions WHERE definition_id=?"
        params: list[Any] = [_clean(definition_id)]
        if _clean(version):
            sql += " AND version=?"
            params.append(_clean(version))
        with self.store.connection() as conn:
            row = conn.execute(sql, params).fetchone()
        return self._from_row(row) if row else None

    def list(self, *, definition_type: str = "", state: str = "", limit: int = 200) -> list[ResearchDefinition]:
        clauses: list[str] = []
        params: list[Any] = []
        if _clean(definition_type):
            clauses.append("definition_type=?")
            params.append(_clean(definition_type).upper())
        if _clean(state):
            clauses.append("state=?")
            params.append(_clean(state).upper())
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(int(limit), 1000)))
        with self.store.connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM research_definitions{where} ORDER BY created_at DESC LIMIT ?", params
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def set_project_ref(
        self,
        *,
        project_id: str,
        slot_key: str,
        definition_id: str,
        definition_version: str,
        reference_mode: str,
        library_asset_id: str = "",
    ) -> dict[str, Any]:
        mode = _clean(reference_mode).upper()
        if mode not in REFERENCE_MODES:
            raise ValueError("reference_mode must be PINNED or TRACK_DRAFT")
        definition = self.get(definition_id, version=definition_version)
        if definition is None:
            raise ValueError("definition version not found")
        if mode == "PINNED" and definition.state != "VALIDATED":
            raise ValueError("PINNED references require a VALIDATED definition")
        library_asset_id = _clean(library_asset_id)
        now = utc_now()
        with self.store.transaction(immediate=True) as conn:
            if conn.execute("SELECT 1 FROM research_projects WHERE project_id=?", (_clean(project_id),)).fetchone() is None:
                raise ValueError("research project not found")
            if library_asset_id:
                asset = conn.execute(
                    "SELECT component_type,source_object_id FROM research_library_assets WHERE library_asset_id=?",
                    (library_asset_id,),
                ).fetchone()
                if asset is None or str(asset[0]) != definition.definition_type or str(asset[1]) != definition.definition_id:
                    raise ValueError("Library asset does not match the selected component")
            elif definition.library_scope == "PROJECT" and definition.owner_project_id != _clean(project_id):
                raise ValueError("component belongs to another Research")
            conn.execute(
                """
                INSERT INTO project_definition_refs(
                    project_id, slot_key, definition_type, definition_id,
                    definition_version, reference_mode, created_at, updated_at, library_asset_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, slot_key) DO UPDATE SET
                    definition_type=excluded.definition_type,
                    definition_id=excluded.definition_id,
                    definition_version=excluded.definition_version,
                    reference_mode=excluded.reference_mode,
                    library_asset_id=excluded.library_asset_id,
                    updated_at=excluded.updated_at
                """,
                (
                    _clean(project_id), _clean(slot_key), definition.definition_type,
                    definition.definition_id, definition.version, mode, now, now,
                    library_asset_id or None,
                ),
            )
        return self.list_project_refs(project_id)[_clean(slot_key)]

    def list_project_refs(self, project_id: str) -> dict[str, dict[str, Any]]:
        with self.store.connection() as conn:
            rows = conn.execute(
                """
                SELECT r.*, d.name, d.state, d.spec_hash, d.engine_version, d.code_hash, d.spec_json,
                       d.owner_project_id, d.library_scope,
                       a.library_asset_id AS published_asset_id, a.asset_version AS library_version
                FROM project_definition_refs r
                JOIN research_definitions d ON d.definition_id=r.definition_id AND d.version=r.definition_version
                LEFT JOIN research_library_assets a ON a.library_asset_id=r.library_asset_id
                WHERE r.project_id=? ORDER BY r.definition_type, r.slot_key
                """,
                (_clean(project_id),),
            ).fetchall()
        return {
            str(row["slot_key"]): {
                "slot_key": str(row["slot_key"]),
                "definition_type": str(row["definition_type"]),
                "definition_id": str(row["definition_id"]),
                "definition_version": str(row["definition_version"]),
                "reference_mode": str(row["reference_mode"]),
                "name": str(row["name"]),
                "state": str(row["state"]),
                "spec_hash": str(row["spec_hash"]),
                "engine_version": str(row["engine_version"]),
                "code_hash": str(row["code_hash"]),
                "spec": json.loads(row["spec_json"]),
                "owner_project_id": str(row["owner_project_id"] or ""),
                "library_scope": str(row["library_scope"] or "GLOBAL"),
                "origin": "LIBRARY" if row["published_asset_id"] else "RESEARCH",
                "library_asset_id": str(row["published_asset_id"] or ""),
                "library_version": int(row["library_version"]) if row["library_version"] is not None else None,
                "updated_at": str(row["updated_at"]),
            }
            for row in rows
        }

    def remove_project_ref(
        self,
        *,
        project_id: str,
        slot_key: str,
        expected_definition_id: str,
    ) -> dict[str, Any]:
        project_id = _clean(project_id)
        slot_key = _clean(slot_key)
        expected_definition_id = _clean(expected_definition_id)
        if not expected_definition_id:
            raise ValueError("expected_definition_id is required")
        with self.store.transaction(immediate=True) as conn:
            row = conn.execute(
                """
                SELECT r.definition_id, r.definition_type, d.name
                FROM project_definition_refs r
                JOIN research_definitions d ON d.definition_id=r.definition_id
                WHERE r.project_id=? AND r.slot_key=?
                """,
                (project_id, slot_key),
            ).fetchone()
            if row is None:
                raise ValueError("Research component reference not found")
            definition_id = str(row["definition_id"])
            definition_type = str(row["definition_type"])
            if definition_id != expected_definition_id:
                raise ValueError("RESEARCH_REFERENCE_STALE: component reference changed")
            dependents: list[dict[str, str]] = []
            if definition_type == "FACTOR":
                alpha_rows = conn.execute(
                    """
                    SELECT r.slot_key, d.definition_id, d.name, d.spec_json
                    FROM project_definition_refs r
                    JOIN research_definitions d ON d.definition_id=r.definition_id
                    WHERE r.project_id=? AND r.definition_type='ALPHA'
                    """,
                    (project_id,),
                ).fetchall()
                for alpha in alpha_rows:
                    spec = json.loads(alpha["spec_json"] or "{}")
                    if any(
                        _clean(item.get("factor_definition_id")) == definition_id
                        for item in spec.get("components", [])
                    ):
                        dependents.append({
                            "slot_key": str(alpha["slot_key"]),
                            "definition_id": str(alpha["definition_id"]),
                            "name": str(alpha["name"]),
                        })
            if dependents:
                names = ", ".join(item["name"] for item in dependents)
                raise ValueError(
                    f"FACTOR_REFERENCE_IN_USE: remove or replace dependent Alpha first: {names}"
                )
            cursor = conn.execute(
                """
                DELETE FROM project_definition_refs
                WHERE project_id=? AND slot_key=? AND definition_id=?
                """,
                (project_id, slot_key, expected_definition_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("RESEARCH_REFERENCE_STALE: component reference changed")
        return {
            "removed": True,
            "project_id": project_id,
            "slot_key": slot_key,
            "definition_id": expected_definition_id,
            "definition_type": definition_type,
            "name": str(row["name"]),
            "history_preserved": True,
        }

    def impact(self, definition_id: str) -> dict[str, Any]:
        definition = self.get(definition_id)
        if definition is None:
            raise ValueError("definition not found")
        with self.store.connection() as conn:
            project_rows = conn.execute(
                "SELECT project_id,slot_key,reference_mode FROM project_definition_refs WHERE definition_id=?",
                (definition.definition_id,),
            ).fetchall()
            artifact_rows = conn.execute(
                "SELECT artifact_id,artifact_type,logical_name,created_by_run_id FROM research_artifacts WHERE spec_hash=?",
                (definition.spec_hash,),
            ).fetchall()
            alpha_rows = conn.execute(
                "SELECT definition_id,name,version,spec_json FROM research_definitions WHERE definition_type='ALPHA'"
            ).fetchall()
        alpha_dependents = []
        for row in alpha_rows:
            spec = json.loads(row["spec_json"] or "{}")
            if any(item.get("factor_definition_id") == definition.definition_id for item in spec.get("components", [])):
                alpha_dependents.append({
                    "definition_id": str(row["definition_id"]), "name": str(row["name"]), "version": str(row["version"])
                })
        projects = [dict(row) for row in project_rows]
        artifacts = [dict(row) for row in artifact_rows]
        run_ids = sorted({str(item["created_by_run_id"]) for item in artifacts if item["created_by_run_id"]})
        return {
            "definition": definition.to_dict(),
            "project_references": projects,
            "alpha_dependents": alpha_dependents,
            "historical_artifacts": artifacts,
            "historical_run_ids": run_ids,
            "change_effects": {
                "warmup_and_requirements": definition.definition_type == "FACTOR",
                "coverage_recheck": True,
                "alpha_revalidation": bool(alpha_dependents),
                "historical_runs_mutated": False,
                "new_version_required": True,
            },
        }

    @staticmethod
    def _from_row(row: Any) -> ResearchDefinition:
        return ResearchDefinition(
            definition_id=str(row["definition_id"]),
            definition_type=str(row["definition_type"]),
            name=str(row["name"]),
            version=str(row["version"]),
            state=str(row["state"]),
            spec=json.loads(row["spec_json"]),
            spec_hash=str(row["spec_hash"]),
            engine_version=str(row["engine_version"]),
            code_hash=str(row["code_hash"]),
            created_by=str(row["created_by"]),
            created_at=str(row["created_at"]),
            owner_project_id=str(row["owner_project_id"] or ""),
            library_scope=str(row["library_scope"] or "GLOBAL"),
            validated_at=row["validated_at"],
            superseded_by_id=row["superseded_by_id"],
            archived_at=row["archived_at"],
        )
