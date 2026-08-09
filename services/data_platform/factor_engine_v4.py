from __future__ import annotations

import ast
import hashlib
import json
import keyword
import math
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .factor_alpha import (
    _available_time,
    _bar_end_time,
    _bar_start_time,
    _finite_float,
    _parse_time,
)


FACTOR_ENGINE_V4_VERSION = "factor-engine.v4"
FACTOR_GRAPH_CONTRACT_VERSION = "factor_formula.v4"
FACTOR_ENGINE_V4_CODE_HASH = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

_IDENTIFIER_START = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_")
_IDENTIFIER_CONTINUE = _IDENTIFIER_START | set("0123456789")
_RESERVED_FORMULA_NAMES = {
    "time",
    "universe",
    "align",
}
_MAX_FORMULA_STATEMENTS = 32
_MAX_FORMULA_AST_NODES = 512
_MAX_FORMULA_AST_DEPTH = 32
_BINARY_SYMBOLS = {
    ast.Add: "+",
    ast.Sub: "-",
    ast.Mult: "*",
    ast.Div: "/",
}
_FREQUENCY_SECONDS = {
    "1m": 60,
    "5m": 5 * 60,
    "15m": 15 * 60,
    "1h": 60 * 60,
    "4h": 4 * 60 * 60,
    "1d": 24 * 60 * 60,
}
_FUNCTION_ALIASES = {
    "pct_change": "time.pct_change",
    "difference": "time.diff",
    "ratio": "time.ratio",
    "rolling_mean": "time.mean",
    "rolling_std": "time.std",
    "rolling_return_std": "time.return_std",
    "ema": "time.ema",
    "ma_crossover": "time.ma_crossover",
    "asof": "align.asof",
    "forward_fill": "align.forward_fill",
}
_FUNCTIONS = {
    "time.lag": {"parameters": 1, "unit": "SOURCE", "history": "window"},
    "time.diff": {"parameters": 1, "unit": "SOURCE", "history": "window"},
    "time.ratio": {"parameters": 1, "unit": "RATIO", "history": "window"},
    "time.pct_change": {"parameters": 1, "unit": "RATIO", "history": "window"},
    "time.log_return": {"parameters": 1, "unit": "RATIO", "history": "window"},
    "time.sum": {"parameters": 1, "unit": "SOURCE", "history": "window_minus_one"},
    "time.mean": {"parameters": 1, "unit": "SOURCE", "history": "window_minus_one"},
    "time.median": {"parameters": 1, "unit": "SOURCE", "history": "window_minus_one"},
    "time.min": {"parameters": 1, "unit": "SOURCE", "history": "window_minus_one"},
    "time.max": {"parameters": 1, "unit": "SOURCE", "history": "window_minus_one"},
    "time.std": {"parameters": 1, "unit": "SOURCE", "history": "window_minus_one"},
    "time.variance": {"parameters": 1, "unit": "COMPOSITE", "history": "window_minus_one"},
    "time.rank": {"parameters": 1, "unit": "PERCENTILE", "history": "window_minus_one"},
    "time.zscore": {"parameters": 1, "unit": "ZSCORE", "history": "window_minus_one"},
    "time.return_std": {"parameters": 1, "unit": "RATIO", "history": "window"},
    "time.ema": {"parameters": 1, "unit": "SOURCE", "history": "window_minus_one"},
    "time.ma_crossover": {"parameters": 2, "unit": "DISCRETE", "history": "window"},
    "universe.rank": {"parameters": 0, "unit": "PERCENTILE", "dimension": "HYBRID"},
    "universe.percentile": {"parameters": 0, "unit": "PERCENTILE", "dimension": "HYBRID"},
    "universe.zscore": {"parameters": 0, "unit": "ZSCORE", "dimension": "HYBRID"},
    "universe.demean": {"parameters": 0, "unit": "SOURCE", "dimension": "HYBRID"},
    "align.asof": {"series_arguments": 2, "parameters": 0, "unit": "SOURCE", "alignment": True},
    "align.forward_fill": {"series_arguments": 2, "parameters": 0, "unit": "SOURCE", "alignment": True},
    "greater": {"series_arguments": 2, "parameters": 0, "unit": "BOOLEAN", "comparison": True},
    "less": {"series_arguments": 2, "parameters": 0, "unit": "BOOLEAN", "comparison": True},
    "equal": {"series_arguments": 2, "parameters": 0, "unit": "BOOLEAN", "comparison": True},
    "where": {"series_arguments": 3, "parameters": 0, "unit": "SOURCE", "conditional": True},
    "is_null": {"parameters": 0, "unit": "BOOLEAN", "predicate": True},
    "is_finite": {"parameters": 0, "unit": "BOOLEAN", "predicate": True},
    "fill_null": {"series_arguments": 2, "parameters": 0, "unit": "SOURCE", "fill": True},
    "logical_and": {"series_arguments": 2, "parameters": 0, "unit": "BOOLEAN", "logical": True},
    "logical_or": {"series_arguments": 2, "parameters": 0, "unit": "BOOLEAN", "logical": True},
    "logical_not": {"parameters": 0, "unit": "BOOLEAN", "logical": True},
    "safe_divide": {"series_arguments": 2, "parameters": 0, "unit": "RATIO"},
    "abs": {"parameters": 0, "unit": "SOURCE"},
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _bars_label(value: int) -> str:
    return f"{value} {'bar' if value == 1 else 'bars'}"


def _canonical_hash(value: Mapping[str, Any]) -> str:
    material = json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _valid_identifier(value: str) -> bool:
    return bool(
        value
        and not keyword.iskeyword(value)
        and value[0] in _IDENTIFIER_START
        and all(character in _IDENTIFIER_CONTINUE for character in value[1:])
    )


@dataclass(frozen=True)
class FactorGraphSpec:
    name: str
    version: str
    source: str
    expression: dict[str, Any]
    inputs: tuple[dict[str, Any], ...]
    parameters: tuple[dict[str, Any], ...]
    dimension: str
    frequency: str
    required_history: dict[str, int]
    output_name: str = ""
    output_display_name: str = ""
    output_type: str = "NUMERIC"
    output_unit: str = "RATIO"
    output_nullability: str = "MAY_BE_NULL"
    output_direction: str = "NO_PREDEFINED_DIRECTION"
    missing_policy: str = "STRICT"
    time_alignment_policy: str = "BAR_END_AVAILABLE_TIME"
    available_after: str = "BAR_CLOSE"
    allow_incomplete_bar: bool = False
    engine_version: str = FACTOR_ENGINE_V4_VERSION
    code_hash: str = FACTOR_ENGINE_V4_CODE_HASH

    def __post_init__(self) -> None:
        if not self.name or not self.version:
            raise ValueError("factor name and version are required")
        if not self.source or not self.expression:
            raise ValueError("factor graph source and expression are required")
        if not self.inputs:
            raise ValueError("factor graph requires at least one Input")
        if self.dimension not in {"TIME_SERIES", "CROSS_SECTIONAL", "HYBRID"}:
            raise ValueError(f"unsupported factor graph dimension: {self.dimension}")
        if self.available_after not in {"BAR_CLOSE", "EVENT_AVAILABLE"}:
            raise ValueError("Factor Engine v4 requires BAR_CLOSE or EVENT_AVAILABLE availability")
        if any(int(value) < 1 for value in self.required_history.values()):
            raise ValueError("required history must be positive")

    @property
    def operator(self) -> str:
        if self.expression.get("kind") == "call":
            return str(self.expression.get("function") or "expression")
        return "expression"

    @property
    def input_field(self) -> str:
        return ",".join(str(item.get("field") or "") for item in self.inputs)

    @property
    def window(self) -> int:
        return max(self.required_history.values(), default=1)

    @property
    def required_observations(self) -> int:
        return self.window

    def to_dict(self) -> dict[str, Any]:
        projection_window = next(
            (
                int(item.get("value"))
                for item in self.parameters
                if str(item.get("name") or "") in {"window", "lookback", "periods"}
            ),
            self.window,
        )
        return {
            "name": self.name,
            "version": self.version,
            "engine_version": self.engine_version,
            "formula_contract": FACTOR_GRAPH_CONTRACT_VERSION,
            "frequency": self.frequency,
            "formula": {
                "source": self.source,
                "ast": self.expression,
                "output": self.output_name,
                "operator": self.operator,
                "input": str(self.inputs[0].get("field") or ""),
                "window": projection_window,
                "parameters": {
                    str(item.get("name")): item.get("value")
                    for item in self.parameters
                },
            },
            "inputs": [dict(item) for item in self.inputs],
            "parameters": [dict(item) for item in self.parameters],
            "dimension": self.dimension,
            "required_history": dict(self.required_history),
            "minimum_observations": self.required_observations,
            "missing_policy": self.missing_policy,
            "time_alignment_policy": self.time_alignment_policy,
            "available_after": self.available_after,
            "allow_incomplete_bar": self.allow_incomplete_bar,
            "output_type": self.output_type,
            "output_unit": self.output_unit,
            "output_name": self.output_name,
            "output_display_name": self.output_display_name,
            "output_nullability": self.output_nullability,
            "output_direction": self.output_direction,
            "code_hash": self.code_hash,
        }

    @property
    def spec_hash(self) -> str:
        return _canonical_hash(self.to_dict())

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "FactorGraphSpec":
        formula = raw.get("formula") if isinstance(raw.get("formula"), Mapping) else {}
        return cls(
            name=_clean(raw.get("name")),
            version=_clean(raw.get("version")),
            source=_clean(formula.get("source")),
            expression=dict(formula.get("ast") or {}),
            inputs=tuple(dict(item) for item in raw.get("inputs") or []),
            parameters=tuple(dict(item) for item in raw.get("parameters") or []),
            dimension=_clean(raw.get("dimension") or "TIME_SERIES").upper(),
            frequency=_clean(raw.get("frequency")),
            required_history={
                str(key): int(value)
                for key, value in dict(raw.get("required_history") or {}).items()
            },
            output_name=_clean(formula.get("output")),
            output_display_name=_clean(raw.get("output_display_name")),
            output_type=_clean(raw.get("output_type") or "NUMERIC").upper(),
            output_unit=_clean(raw.get("output_unit") or "RATIO").upper(),
            output_nullability=_clean(raw.get("output_nullability") or "MAY_BE_NULL").upper(),
            output_direction=_clean(raw.get("output_direction") or "NO_PREDEFINED_DIRECTION").upper(),
            missing_policy=_clean(raw.get("missing_policy") or "STRICT").upper(),
            time_alignment_policy=_clean(raw.get("time_alignment_policy") or "BAR_END_AVAILABLE_TIME").upper(),
            available_after=_clean(raw.get("available_after") or "BAR_CLOSE").upper(),
            allow_incomplete_bar=bool(raw.get("allow_incomplete_bar", False)),
            engine_version=_clean(raw.get("engine_version") or FACTOR_ENGINE_V4_VERSION),
            code_hash=_clean(raw.get("code_hash") or FACTOR_ENGINE_V4_CODE_HASH),
        )


@dataclass
class _TypedNode:
    expression: dict[str, Any]
    value_type: str
    dimension: str
    unit: str
    required_history: dict[str, int] = field(default_factory=dict)
    native_frequencies: dict[str, str] = field(default_factory=dict)
    frequencies: set[str] = field(default_factory=set)
    resolved: str = ""
    steps: list[str] = field(default_factory=list)
    scalar_value: Any = None


class FactorGraphCompiler:
    """Compile the controlled Factor Formula into a typed, point-in-time-safe graph."""

    @classmethod
    def inspect(
        cls,
        document: Mapping[str, Any],
        capabilities: Mapping[str, Any],
    ) -> dict[str, Any]:
        diagnostics: list[dict[str, Any]] = []

        def add(
            level: str,
            code: str,
            path: str,
            message: str,
            *,
            line: int = 0,
            column: int = 0,
        ) -> None:
            diagnostic: dict[str, Any] = {
                "level": level,
                "code": code,
                "path": path,
                "message": message,
            }
            if line:
                diagnostic["line"] = line
            if column:
                diagnostic["column"] = column
            diagnostics.append(diagnostic)

        inputs = [
            dict(item)
            for item in document.get("inputs") or []
            if isinstance(item, Mapping)
        ]
        parameters = [
            dict(item)
            for item in document.get("parameters") or []
            if isinstance(item, Mapping)
        ]
        formula = document.get("formula") if isinstance(document.get("formula"), Mapping) else {}
        source = _clean(formula.get("source"))
        feature_datasets = {
            (_clean(item.get("dataset") or "bars").lower(), _clean(item.get("id")))
            for item in capabilities.get("features") or []
            if isinstance(item, Mapping)
        }
        frequencies = {_clean(item) for item in capabilities.get("frequencies") or []}
        max_inputs = int((capabilities.get("authoring_contract") or {}).get("max_inputs") or 8)

        input_by_name: dict[str, dict[str, Any]] = {}
        if not inputs:
            add("ERROR", "INPUT_REQUIRED", "inputs", "At least one Input is required.")
        if len(inputs) > max_inputs:
            add("ERROR", "FACTOR_V4_INPUT_LIMIT", "inputs", f"Factor Engine v4 supports at most {max_inputs} Inputs.")
        for index, input_spec in enumerate(inputs):
            name = _clean(input_spec.get("variable_name"))
            path = f"inputs.{index}"
            if not _valid_identifier(name):
                add("ERROR", "INPUT_VARIABLE_INVALID", f"{path}.variable_name", "Input variable must be a valid Formula name.")
            elif name in _RESERVED_FORMULA_NAMES:
                add("ERROR", "FORMULA_NAME_RESERVED", f"{path}.variable_name", f"{name} is reserved by the Formula language.")
            elif name in input_by_name:
                add("ERROR", "INPUT_VARIABLE_DUPLICATE", f"{path}.variable_name", f"Duplicate Input variable: {name}.")
            else:
                input_by_name[name] = input_spec
            dataset = _clean(input_spec.get("dataset") or "bars").lower()
            if dataset not in {"bars", "price_history"}:
                add(
                    "ERROR",
                    "INPUT_DATASET_UNSUPPORTED",
                    f"{path}.dataset",
                    f"Engine v4 does not support Dataset: {dataset or 'missing'}.",
                )
            field_name = _clean(input_spec.get("field"))
            if (dataset, field_name) not in feature_datasets:
                add(
                    "ERROR",
                    "INPUT_FIELD_UNSUPPORTED",
                    f"{path}.field",
                    f"Unsupported {dataset}.{field_name} input field.",
                )
            frequency = _clean(input_spec.get("frequency"))
            if frequency not in frequencies:
                add("ERROR", "INPUT_FREQUENCY_UNSUPPORTED", f"{path}.frequency", f"Unsupported frequency: {frequency}.")

        parameter_by_name: dict[str, dict[str, Any]] = {}
        for index, parameter in enumerate(parameters):
            name = _clean(parameter.get("name"))
            path = f"parameters.{index}"
            if not _valid_identifier(name):
                add("ERROR", "PARAMETER_NAME_INVALID", f"{path}.name", "Parameter name must be a valid Formula name.")
            elif name in _RESERVED_FORMULA_NAMES:
                add("ERROR", "FORMULA_NAME_RESERVED", f"{path}.name", f"{name} is reserved by the Formula language.")
            elif name in input_by_name or name in parameter_by_name:
                add("ERROR", "PARAMETER_NAME_DUPLICATE", f"{path}.name", f"Duplicate Formula name: {name}.")
            else:
                parameter_by_name[name] = parameter
            if _clean(parameter.get("unit")).lower() != "bars":
                add("ERROR", "FACTOR_V4_PARAMETER_UNIT_UNSUPPORTED", f"{path}.unit", "4A window Parameters use bars.")
            value = parameter.get("value")
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                add("ERROR", "FORMULA_PARAMETER_TYPE", f"{path}.value", f"{name or 'Parameter'} must be a positive integer.")

        output_document = document.get("output") if isinstance(document.get("output"), Mapping) else {}
        requested_output = _clean(
            output_document.get("final")
            or output_document.get("final_name")
            or formula.get("output")
        )
        parsed: ast.Module | None = None
        if not source:
            add("ERROR", "FORMULA_SOURCE_REQUIRED", "formula.source", "Formula source is required.")
        else:
            try:
                parsed = ast.parse(source, mode="exec")
            except SyntaxError as exc:
                add(
                    "ERROR",
                    "FORMULA_SYNTAX_ERROR",
                    "formula.source",
                    f"Formula syntax error at line {exc.lineno or 1}, column {exc.offset or 1}.",
                    line=exc.lineno or 1,
                    column=exc.offset or 1,
                )

        typed: _TypedNode | None = None
        program: dict[str, Any] = {
            "output_name": "",
            "resolved_formula": "",
            "formula_steps": [],
            "named_results": [],
        }
        if parsed is not None and not any(item["level"] == "ERROR" for item in diagnostics):
            program = cls._compile_program(
                parsed,
                input_by_name,
                parameter_by_name,
                requested_output,
                add,
            )
            typed = program.get("typed")
            if typed is not None and len(typed.frequencies) > 1:
                add(
                    "ERROR",
                    "FACTOR_V4_FREQUENCY_ALIGNMENT_REQUIRED",
                    "formula.source",
                    "Inputs use different frequencies. Wrap the slower series with align.asof(source, reference).",
                )

        if typed is not None:
            referenced_inputs = cls._referenced_names(typed.expression, "input")
            referenced_parameters = cls._referenced_names(typed.expression, "parameter")
            for name in input_by_name:
                if name not in referenced_inputs:
                    add("WARNING", "UNUSED_INPUT", "inputs", f"Input {name} is not referenced by the Formula.")
            for name in parameter_by_name:
                if name not in referenced_parameters:
                    add("WARNING", "UNUSED_PARAMETER", "parameters", f"Parameter {name} is not referenced by the Formula.")

        compilation: dict[str, Any] | None = None
        identity = document.get("identity") if isinstance(document.get("identity"), Mapping) else {}
        if (
            typed is not None
            and _clean(identity.get("name"))
            and _clean(identity.get("version"))
            and not any(item["level"] == "ERROR" for item in diagnostics)
        ):
            advanced = document.get("advanced") if isinstance(document.get("advanced"), Mapping) else {}
            frequency = next(iter(typed.frequencies), _clean(inputs[0].get("frequency")) if inputs else "")
            input_datasets = {
                _clean(item.get("dataset") or "bars").lower()
                for item in inputs
            }
            event_time_inputs = input_datasets == {"price_history"}
            spec = FactorGraphSpec(
                name=_clean(identity.get("name")),
                version=_clean(identity.get("version")),
                source=source,
                expression=typed.expression,
                inputs=tuple(inputs),
                parameters=tuple(parameters),
                dimension=typed.dimension,
                frequency=frequency,
                required_history=typed.required_history,
                output_name=_clean(program.get("output_name")),
                output_display_name=_clean(output_document.get("display_name")),
                output_type="BOOLEAN" if typed.unit == "BOOLEAN" else "NUMERIC",
                output_unit=typed.unit,
                output_nullability="MAY_BE_NULL",
                output_direction=_clean(output_document.get("direction") or "NO_PREDEFINED_DIRECTION").upper(),
                missing_policy=_clean(advanced.get("missing_policy") or "STRICT").upper(),
                time_alignment_policy=(
                    "EVENT_TIME_AVAILABLE_TIME"
                    if event_time_inputs
                    else _clean(advanced.get("time_alignment_policy") or "BAR_END_AVAILABLE_TIME").upper()
                ),
                available_after=(
                    "EVENT_AVAILABLE"
                    if event_time_inputs
                    else _clean(advanced.get("available_after") or "BAR_CLOSE").upper()
                ),
                allow_incomplete_bar=bool(advanced.get("allow_incomplete_bar", False)),
            )
            required_max = max(typed.required_history.values(), default=1)
            compilation = {
                "source": source,
                "input_variable": next(iter(cls._referenced_names(typed.expression, "input")), ""),
                "operator": spec.operator,
                "parameter_bindings": {
                    name: {
                        "source_name": name,
                        "value": item.get("value"),
                        "unit": _clean(item.get("unit")).lower(),
                    }
                    for name, item in parameter_by_name.items()
                    if name in cls._referenced_names(typed.expression, "parameter")
                },
                "factor_spec": spec.to_dict(),
                "spec_hash": spec.spec_hash,
                "output_name": _clean(program.get("output_name")),
                "named_results": list(program.get("named_results") or []),
                "resolved_formula": _clean(program.get("resolved_formula")) or typed.resolved,
                "required_history": _bars_label(required_max),
                "required_history_by_input": {
                    name: _bars_label(value)
                    for name, value in typed.required_history.items()
                },
                "formula_meaning": " ".join(
                    f"{index}. {step}"
                    for index, step in enumerate(program.get("formula_steps") or typed.steps, start=1)
                ),
                "formula_steps": list(program.get("formula_steps") or typed.steps),
                "output_display": cls._output_display(
                    typed,
                    frequency,
                    output_name=spec.output_name,
                    display_name=spec.output_display_name,
                ),
            }

        return {
            "inputs": inputs,
            "parameters": parameters,
            "source": source,
            "diagnostics": diagnostics,
            "compilation": compilation,
        }

    @classmethod
    def compile(
        cls,
        document: Mapping[str, Any],
        capabilities: Mapping[str, Any],
    ) -> dict[str, Any]:
        result = cls.inspect(document, capabilities)
        errors = [item for item in result["diagnostics"] if item["level"] == "ERROR"]
        if errors:
            raise ValueError(f"{errors[0]['code']}: {errors[0]['message']}")
        if result["compilation"] is None:
            raise ValueError("FACTOR_V4_COMPILATION_UNAVAILABLE")
        return dict(result["compilation"]["factor_spec"])

    @staticmethod
    def _ast_depth(node: ast.AST) -> int:
        children = list(ast.iter_child_nodes(node))
        return 1 + max((FactorGraphCompiler._ast_depth(child) for child in children), default=0)

    @staticmethod
    def _result_references(node: ast.AST, result_names: set[str]) -> set[str]:
        return {
            item.id
            for item in ast.walk(node)
            if isinstance(item, ast.Name) and item.id in result_names
        }

    @classmethod
    def _compile_program(
        cls,
        parsed: ast.Module,
        inputs: Mapping[str, dict[str, Any]],
        parameters: Mapping[str, dict[str, Any]],
        requested_output: str,
        add: Any,
    ) -> dict[str, Any]:
        statements = list(parsed.body)
        if not statements:
            add("ERROR", "FORMULA_SOURCE_REQUIRED", "formula.source", "Formula source is required.")
            return {"typed": None}
        if len(statements) > _MAX_FORMULA_STATEMENTS:
            add(
                "ERROR",
                "FORMULA_STATEMENT_LIMIT",
                "formula.source",
                f"Formula supports at most {_MAX_FORMULA_STATEMENTS} statements.",
            )
            return {"typed": None}
        node_count = sum(1 for _ in ast.walk(parsed))
        if node_count > _MAX_FORMULA_AST_NODES:
            add(
                "ERROR",
                "FORMULA_COMPLEXITY_LIMIT",
                "formula.source",
                f"Formula contains {node_count} syntax nodes; the limit is {_MAX_FORMULA_AST_NODES}.",
            )
            return {"typed": None}
        if cls._ast_depth(parsed) > _MAX_FORMULA_AST_DEPTH:
            add(
                "ERROR",
                "FORMULA_NESTING_LIMIT",
                "formula.source",
                f"Formula nesting exceeds {_MAX_FORMULA_AST_DEPTH} levels.",
            )
            return {"typed": None}

        all_result_names: set[str] = set()
        for statement in statements:
            if isinstance(statement, ast.Assign):
                if (
                    len(statement.targets) == 1
                    and isinstance(statement.targets[0], ast.Name)
                ):
                    all_result_names.add(statement.targets[0].id)

        results: dict[str, _TypedNode] = {}
        result_order: list[str] = []
        result_nodes: dict[str, ast.AST] = {}
        dependencies: dict[str, set[str]] = {}
        final_expression: _TypedNode | None = None
        final_expression_node: ast.AST | None = None

        for index, statement in enumerate(statements):
            line = int(getattr(statement, "lineno", 0) or 0)
            column = int(getattr(statement, "col_offset", 0) or 0) + 1
            if isinstance(statement, ast.Assign):
                if len(statement.targets) != 1 or not isinstance(statement.targets[0], ast.Name):
                    add(
                        "ERROR",
                        "FORMULA_ASSIGNMENT_TARGET_INVALID",
                        "formula.source",
                        "Each Formula assignment must target one simple name.",
                        line=line,
                        column=column,
                    )
                    continue
                name = statement.targets[0].id
                if not _valid_identifier(name):
                    add(
                        "ERROR",
                        "FORMULA_RESULT_NAME_INVALID",
                        "formula.source",
                        f"Invalid calculated result name: {name}.",
                        line=line,
                        column=column,
                    )
                    continue
                if name in _RESERVED_FORMULA_NAMES:
                    add(
                        "ERROR",
                        "FORMULA_NAME_RESERVED",
                        "formula.source",
                        f"{name} is reserved by the Formula language.",
                        line=line,
                        column=column,
                    )
                    continue
                if name in inputs or name in parameters:
                    add(
                        "ERROR",
                        "FORMULA_RESULT_SHADOWS_INPUT",
                        "formula.source",
                        f"Calculated result {name} conflicts with an Input or Parameter.",
                        line=line,
                        column=column,
                    )
                    continue
                if name in results:
                    add(
                        "ERROR",
                        "FORMULA_RESULT_DUPLICATE",
                        "formula.source",
                        f"Calculated result {name} is assigned more than once.",
                        line=line,
                        column=column,
                    )
                    continue
                typed = cls._compile_node(
                    statement.value,
                    inputs,
                    parameters,
                    add,
                    results=results,
                    known_results=all_result_names,
                    current_result=name,
                )
                if typed is not None:
                    results[name] = typed
                    result_order.append(name)
                    result_nodes[name] = statement.value
                    dependencies[name] = cls._result_references(
                        statement.value,
                        all_result_names,
                    )
                continue
            if isinstance(statement, ast.Expr):
                if index != len(statements) - 1:
                    add(
                        "ERROR",
                        "FORMULA_FINAL_EXPRESSION_POSITION",
                        "formula.source",
                        "A bare Formula expression is allowed only on the final line.",
                        line=line,
                        column=column,
                    )
                    continue
                final_expression_node = statement.value
                final_expression = cls._compile_node(
                    statement.value,
                    inputs,
                    parameters,
                    add,
                    results=results,
                    known_results=all_result_names,
                )
                continue
            add(
                "ERROR",
                "FORMULA_STATEMENT_UNSUPPORTED",
                "formula.source",
                "Use calculated-name assignments and one optional final expression only.",
                line=line,
                column=column,
            )

        output_name = ""
        typed_output: _TypedNode | None = None
        selected_from_expression = False
        if final_expression is not None:
            if requested_output:
                if (
                    isinstance(final_expression_node, ast.Name)
                    and final_expression_node.id == requested_output
                ):
                    output_name = requested_output
                    typed_output = results.get(requested_output)
                else:
                    add(
                        "ERROR",
                        "FORMULA_OUTPUT_CONFLICT",
                        "output.final",
                        "The selected Output conflicts with the Formula's final expression.",
                    )
            else:
                typed_output = final_expression
                selected_from_expression = True
                if isinstance(final_expression_node, ast.Name) and final_expression_node.id in results:
                    output_name = final_expression_node.id
                    selected_from_expression = False
        elif requested_output:
            if requested_output not in results:
                add(
                    "ERROR",
                    "FORMULA_OUTPUT_UNKNOWN",
                    "output.final",
                    f"Final Output {requested_output} is not a calculated result.",
                )
            else:
                output_name = requested_output
                typed_output = results[requested_output]
        elif "factor" in results:
            output_name = "factor"
            typed_output = results["factor"]
        elif len(results) == 1:
            output_name = result_order[0]
            typed_output = results[output_name]
        elif results:
            add(
                "ERROR",
                "FORMULA_OUTPUT_REQUIRED",
                "output.final",
                "Choose one calculated result as the Final Output.",
            )

        reachable: set[str] = set()

        def visit(name: str) -> None:
            if name in reachable:
                return
            reachable.add(name)
            for dependency in dependencies.get(name, set()):
                if dependency in results:
                    visit(dependency)

        if output_name:
            visit(output_name)
        elif selected_from_expression and final_expression_node is not None:
            for dependency in cls._result_references(final_expression_node, set(results)):
                visit(dependency)

        for name in result_order:
            if name not in reachable:
                add(
                    "WARNING",
                    "UNUSED_FORMULA_RESULT",
                    "formula.source",
                    f"Calculated result {name} does not contribute to the Final Output.",
                )

        named_results = [
            {
                "name": name,
                "type": "Boolean" if results[name].unit == "BOOLEAN" else "Numeric",
                "unit": results[name].unit,
                "dimension": results[name].dimension,
                "resolved": results[name].resolved,
                "is_output": name == output_name,
            }
            for name in result_order
        ]
        resolved_lines = [
            f"{name} = {results[name].resolved}"
            for name in result_order
            if name in reachable
        ]
        if not resolved_lines and typed_output is not None:
            resolved_lines = [typed_output.resolved]
        elif selected_from_expression and typed_output is not None:
            resolved_lines.append(typed_output.resolved)

        formula_steps = [
            step
            for name in result_order
            if name in reachable
            for step in results[name].steps
        ]
        if selected_from_expression and typed_output is not None:
            formula_steps.extend(typed_output.steps)

        return {
            "typed": typed_output,
            "output_name": output_name,
            "resolved_formula": "\n\n".join(resolved_lines),
            "formula_steps": formula_steps,
            "named_results": named_results,
        }

    @classmethod
    def _compile_node(
        cls,
        node: ast.AST,
        inputs: Mapping[str, dict[str, Any]],
        parameters: Mapping[str, dict[str, Any]],
        add: Any,
        *,
        results: Mapping[str, _TypedNode] | None = None,
        known_results: set[str] | None = None,
        current_result: str = "",
    ) -> _TypedNode | None:
        results = results or {}
        known_results = known_results or set()
        if isinstance(node, ast.Name):
            if node.id in inputs:
                item = inputs[node.id]
                field_name = _clean(item.get("field"))
                frequency = _clean(item.get("frequency"))
                return _TypedNode(
                    expression={
                        "kind": "input",
                        "name": node.id,
                        "dataset": "bars",
                        "field": field_name,
                        "frequency": frequency,
                    },
                    value_type="SERIES",
                    dimension="TIME_SERIES",
                    unit=f"SOURCE:{node.id}",
                    required_history={node.id: 1},
                    native_frequencies={node.id: frequency},
                    frequencies={frequency},
                    resolved=f"Bars.{field_name} @ {frequency}",
                )
            if node.id in parameters:
                item = parameters[node.id]
                value = item.get("value")
                unit = _clean(item.get("unit")).lower()
                return _TypedNode(
                    expression={"kind": "parameter", "name": node.id, "value": value, "unit": unit},
                    value_type="SCALAR",
                    dimension="SCALAR",
                    unit="BARS",
                    resolved=f"{value} {unit}",
                    scalar_value=value,
                )
            if node.id in results:
                item = results[node.id]
                return _TypedNode(
                    expression=item.expression,
                    value_type=item.value_type,
                    dimension=item.dimension,
                    unit=item.unit,
                    required_history=dict(item.required_history),
                    native_frequencies=dict(item.native_frequencies),
                    frequencies=set(item.frequencies),
                    resolved=node.id,
                    scalar_value=item.scalar_value,
                )
            if node.id == current_result:
                add(
                    "ERROR",
                    "FORMULA_RESULT_SELF_REFERENCE",
                    "formula.source",
                    f"Calculated result {node.id} cannot reference itself.",
                )
            elif node.id in known_results:
                add(
                    "ERROR",
                    "FORMULA_RESULT_FORWARD_REFERENCE",
                    "formula.source",
                    f"Calculated result {node.id} must be defined before it is used.",
                )
            else:
                add("ERROR", "FORMULA_NAME_UNKNOWN", "formula.source", f"Unknown Input, Parameter, or calculated result: {node.id}.")
            return None

        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, int)
            and not isinstance(node.value, bool)
            and node.value > 0
        ):
            return _TypedNode(
                expression={
                    "kind": "literal",
                    "value": node.value,
                    "unit": "bars",
                },
                value_type="SCALAR",
                dimension="SCALAR",
                unit="BARS",
                resolved=_bars_label(node.value),
                scalar_value=node.value,
            )

        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_SYMBOLS:
            left = cls._compile_node(
                node.left,
                inputs,
                parameters,
                add,
                results=results,
                known_results=known_results,
                current_result=current_result,
            )
            right = cls._compile_node(
                node.right,
                inputs,
                parameters,
                add,
                results=results,
                known_results=known_results,
                current_result=current_result,
            )
            if left is None or right is None:
                return None
            if left.value_type != "SERIES" or right.value_type != "SERIES":
                add("ERROR", "FACTOR_V4_BINARY_TYPE", "formula.source", "Arithmetic composition requires two numeric series.")
                return None
            symbol = _BINARY_SYMBOLS[type(node.op)]
            unit = cls._binary_unit(symbol, left.unit, right.unit, add)
            if unit is None:
                return None
            return _TypedNode(
                expression={
                    "kind": "binary",
                    "operator": symbol,
                    "left": left.expression,
                    "right": right.expression,
                },
                value_type="SERIES",
                dimension=left.dimension if left.dimension == right.dimension else "HYBRID",
                unit=unit,
                required_history=cls._merge_history(left.required_history, right.required_history),
                native_frequencies={
                    **left.native_frequencies,
                    **right.native_frequencies,
                },
                frequencies=left.frequencies | right.frequencies,
                resolved=f"({left.resolved} {symbol} {right.resolved})",
                steps=[*left.steps, *right.steps, f"Combine the two series using {symbol}."],
            )

        if isinstance(node, ast.Call):
            function_name = cls._function_name(node.func)
            canonical = _FUNCTION_ALIASES.get(function_name, function_name)
            schema = _FUNCTIONS.get(canonical)
            if schema is None:
                add("ERROR", "FORMULA_FUNCTION_UNSUPPORTED", "formula.source", f"Unsupported Factor function: {function_name}.")
                return None
            if node.keywords:
                add("ERROR", "FACTOR_V4_KEYWORD_ARGUMENT_UNSUPPORTED", "formula.source", "Use positional Formula arguments.")
                return None
            compiled_args = [
                cls._compile_node(
                    item,
                    inputs,
                    parameters,
                    add,
                    results=results,
                    known_results=known_results,
                    current_result=current_result,
                )
                for item in node.args
            ]
            if any(item is None for item in compiled_args):
                return None
            arguments = [item for item in compiled_args if item is not None]
            series_count = int(schema.get("series_arguments") or 1)
            parameter_count = int(schema.get("parameters") or 0)
            if len(arguments) != series_count + parameter_count:
                add(
                    "ERROR",
                    "FORMULA_ARGUMENT_COUNT",
                    "formula.source",
                    f"{canonical} expects {series_count + parameter_count} arguments.",
                )
                return None
            if any(item.value_type != "SERIES" for item in arguments[:series_count]):
                add("ERROR", "FACTOR_V4_SERIES_ARGUMENT_REQUIRED", "formula.source", f"{canonical} requires series Input arguments.")
                return None
            if any(item.value_type != "SCALAR" for item in arguments[series_count:]):
                add("ERROR", "FACTOR_V4_PARAMETER_ARGUMENT_REQUIRED", "formula.source", f"{canonical} requires named Parameters.")
                return None
            if canonical.startswith("time.") and arguments[0].unit == "BOOLEAN":
                add(
                    "ERROR",
                    "FACTOR_V4_NUMERIC_REQUIRED",
                    "formula.source",
                    f"{canonical} requires a numeric series.",
                )
                return None
            if schema.get("alignment"):
                source_argument, reference_argument = arguments
                if len(reference_argument.frequencies) != 1:
                    add(
                        "ERROR",
                        "FACTOR_V4_ALIGNMENT_REFERENCE_REQUIRED",
                        "formula.source",
                        f"{canonical} requires a single-frequency reference series.",
                    )
                    return None
                steps = [step for item in arguments for step in item.steps]
                steps.append(cls._meaning(canonical, arguments))
                return _TypedNode(
                    expression={
                        "kind": "call",
                        "function": canonical,
                        "arguments": [item.expression for item in arguments],
                    },
                    value_type="SERIES",
                    dimension=(
                        source_argument.dimension
                        if source_argument.dimension == reference_argument.dimension
                        else "HYBRID"
                    ),
                    unit=source_argument.unit,
                    required_history=cls._merge_history(
                        source_argument.required_history,
                        reference_argument.required_history,
                    ),
                    native_frequencies={
                        **source_argument.native_frequencies,
                        **reference_argument.native_frequencies,
                    },
                    frequencies=set(reference_argument.frequencies),
                    resolved=f"{canonical}({source_argument.resolved}, {reference_argument.resolved})",
                    steps=steps,
                )
            if schema.get("comparison"):
                left, right = arguments
                if left.unit != right.unit:
                    add(
                        "ERROR",
                        "FACTOR_V4_UNIT_MISMATCH",
                        "formula.source",
                        f"{canonical} requires values with the same unit.",
                    )
                    return None
            if schema.get("conditional"):
                condition, when_true, when_false = arguments
                if condition.unit != "BOOLEAN":
                    add(
                        "ERROR",
                        "FACTOR_V4_BOOLEAN_REQUIRED",
                        "formula.source",
                        "where requires a Boolean condition as its first argument.",
                    )
                    return None
                if when_true.unit != when_false.unit:
                    add(
                        "ERROR",
                        "FACTOR_V4_UNIT_MISMATCH",
                        "formula.source",
                        "where requires matching units for its result branches.",
                    )
                    return None
            if schema.get("fill"):
                if arguments[0].unit != arguments[1].unit:
                    add(
                        "ERROR",
                        "FACTOR_V4_UNIT_MISMATCH",
                        "formula.source",
                        "fill_null requires a replacement series with the same unit.",
                    )
                    return None
            if schema.get("logical") and any(item.unit != "BOOLEAN" for item in arguments):
                add(
                    "ERROR",
                    "FACTOR_V4_BOOLEAN_REQUIRED",
                    "formula.source",
                    f"{canonical} requires Boolean series arguments.",
                )
                return None
            non_parameter_frequencies = set().union(
                *(item.frequencies for item in arguments[:series_count])
            )
            if len(non_parameter_frequencies) > 1:
                add(
                    "ERROR",
                    "FACTOR_V4_FREQUENCY_ALIGNMENT_REQUIRED",
                    "formula.source",
                    f"{canonical} arguments must share one frequency. Use align.asof(source, reference).",
                )
                return None
            history = cls._merge_history(*(item.required_history for item in arguments))
            window_value = int(arguments[-1].scalar_value) if parameter_count else 0
            if canonical == "time.ma_crossover" and int(arguments[-2].scalar_value) >= window_value:
                add(
                    "ERROR",
                    "FACTOR_V4_PARAMETER_CONSTRAINT",
                    "formula.source",
                    "time.ma_crossover requires fast_window smaller than slow_window.",
                )
                return None
            result_frequencies = set().union(
                *(item.frequencies for item in arguments)
            )
            native_frequencies = {
                name: frequency
                for item in arguments
                for name, frequency in item.native_frequencies.items()
            }
            history_mode = schema.get("history")
            if history_mode:
                evaluation_frequency = next(iter(result_frequencies), "")
                evaluation_seconds = _FREQUENCY_SECONDS.get(evaluation_frequency)
                span = window_value if history_mode == "window" else max(0, window_value - 1)
                history = {
                    name: value + cls._history_increment(
                        span,
                        evaluation_seconds,
                        _FREQUENCY_SECONDS.get(native_frequencies.get(name, "")),
                    )
                    for name, value in history.items()
                }
            dimension = str(schema.get("dimension") or arguments[0].dimension)
            unit_rule = str(schema.get("unit") or "SOURCE")
            if schema.get("conditional"):
                unit = arguments[1].unit
            else:
                unit = arguments[0].unit if unit_rule == "SOURCE" else unit_rule
            steps = [step for item in arguments for step in item.steps]
            steps.append(cls._meaning(canonical, arguments))
            return _TypedNode(
                expression={
                    "kind": "call",
                    "function": canonical,
                    "arguments": [item.expression for item in arguments],
                },
                value_type="SERIES",
                dimension=dimension,
                unit=unit,
                required_history=history,
                native_frequencies=native_frequencies,
                frequencies=result_frequencies,
                resolved=f"{canonical}({', '.join(item.resolved for item in arguments)})",
                steps=steps,
            )

        add(
            "ERROR",
            "FACTOR_V4_EXPRESSION_UNSUPPORTED",
            "formula.source",
            "Use Inputs, named Parameters, supported functions, and + - * / composition.",
        )
        return None

    @staticmethod
    def _function_name(node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            return f"{node.value.id}.{node.attr}"
        return ""

    @staticmethod
    def _merge_history(*items: Mapping[str, int]) -> dict[str, int]:
        merged: dict[str, int] = {}
        for item in items:
            for name, value in item.items():
                merged[name] = max(merged.get(name, 1), int(value))
        return merged

    @staticmethod
    def _history_increment(
        evaluation_span: int,
        evaluation_seconds: int | None,
        source_seconds: int | None,
    ) -> int:
        if evaluation_span <= 0:
            return 0
        if not evaluation_seconds or not source_seconds:
            return evaluation_span
        return math.ceil(evaluation_span * evaluation_seconds / source_seconds)

    @staticmethod
    def _binary_unit(symbol: str, left: str, right: str, add: Any) -> str | None:
        if symbol in {"+", "-"}:
            if left != right:
                add("ERROR", "FACTOR_V4_UNIT_MISMATCH", "formula.source", f"Cannot apply {symbol} to {left} and {right}.")
                return None
            return left
        if symbol == "/" and left == right:
            return "RATIO"
        return "COMPOSITE"

    @staticmethod
    def _referenced_names(expression: Mapping[str, Any], kind: str) -> set[str]:
        found: set[str] = set()
        if expression.get("kind") == kind:
            found.add(str(expression.get("name")))
        for value in expression.values():
            if isinstance(value, Mapping):
                found.update(FactorGraphCompiler._referenced_names(value, kind))
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, Mapping):
                        found.update(FactorGraphCompiler._referenced_names(item, kind))
        return found

    @staticmethod
    def _meaning(function_name: str, arguments: Sequence[_TypedNode]) -> str:
        window = arguments[-1].scalar_value if len(arguments) > 1 and arguments[-1].value_type == "SCALAR" else None
        window_label = _bars_label(window) if isinstance(window, int) else "the selected window"
        if function_name == "time.ma_crossover":
            return (
                f"Detect crossings between the {arguments[-2].scalar_value}-bar and "
                f"{arguments[-1].scalar_value}-bar moving averages."
            )
        meanings = {
            "time.lag": f"Shift the series by {window_label}.",
            "time.diff": f"Calculate the change from {window_label} earlier.",
            "time.ratio": f"Divide the current value by its value {window_label} earlier.",
            "time.pct_change": f"Calculate the percentage change over the previous {window_label} for each Instrument.",
            "time.log_return": f"Calculate the logarithmic return over the previous {window_label} for each Instrument.",
            "time.sum": f"Calculate the rolling sum over {window_label} for each Instrument.",
            "time.mean": f"Calculate the rolling mean over {window_label} for each Instrument.",
            "time.median": f"Calculate the rolling median over {window_label} for each Instrument.",
            "time.min": f"Calculate the rolling minimum over {window_label} for each Instrument.",
            "time.max": f"Calculate the rolling maximum over {window_label} for each Instrument.",
            "time.std": f"Calculate the rolling standard deviation over {window_label} for each Instrument.",
            "time.variance": f"Calculate the rolling variance over {window_label} for each Instrument.",
            "time.rank": f"Rank the latest value within its previous {window_label} for each Instrument.",
            "time.zscore": f"Standardize the latest value against its previous {window_label} for each Instrument.",
            "time.return_std": f"Calculate return volatility over {window_label} for each Instrument.",
            "time.ema": f"Calculate the exponential moving average over {window_label} for each Instrument.",
            "universe.rank": "Rank the result across the current Universe at each evaluation time.",
            "universe.percentile": "Convert the result to a percentile across the current Universe at each evaluation time.",
            "universe.zscore": "Standardize the result across the current Universe at each evaluation time.",
            "universe.demean": "Subtract the current Universe mean at each evaluation time.",
            "align.asof": "Use the latest source value that was available at each reference-series evaluation time.",
            "align.forward_fill": "Forward-fill the latest available source value onto the reference-series evaluation times.",
            "greater": "Return true where the first series is greater than the second series.",
            "less": "Return true where the first series is less than the second series.",
            "equal": "Return true where the two series are equal.",
            "where": "Choose between two result series using a Boolean condition.",
            "is_null": "Return true where the series is unavailable.",
            "is_finite": "Return true where the series contains a finite numeric value.",
            "fill_null": "Replace unavailable values with values from the replacement series.",
            "logical_and": "Return true where both Boolean series are true.",
            "logical_or": "Return true where either Boolean series is true.",
            "logical_not": "Invert the Boolean series.",
            "safe_divide": "Divide the first series by the second series while treating zero denominators as unavailable.",
            "abs": "Use the absolute value of the series.",
        }
        return meanings.get(function_name, f"Calculate {function_name}.")

    @staticmethod
    def _output_display(
        typed: _TypedNode,
        frequency: str,
        *,
        output_name: str = "",
        display_name: str = "",
    ) -> dict[str, str]:
        if typed.unit.startswith("SOURCE:"):
            unit = f"Same as {typed.unit.split(':', 1)[1]}"
        else:
            unit = {
                "RATIO": "Ratio",
                "PERCENTILE": "Percentile rank",
                "ZSCORE": "Z-score",
                "COMPOSITE": "Composite unit",
                "DISCRETE": "Discrete event",
                "BOOLEAN": "True / False",
            }.get(typed.unit, typed.unit.title())
        root_function = (
            str(typed.expression.get("function") or "")
            if typed.expression.get("kind") == "call"
            else ""
        )
        value_meaning = {
            "time.std": "Higher values indicate greater price variation over the selected window.",
            "time.return_std": "Higher values indicate greater return variation over the selected window.",
            "universe.rank": "Higher values indicate a higher relative rank in the current Universe.",
            "universe.zscore": "Positive values are above the current Universe mean; negative values are below it.",
        }.get(root_function, typed.steps[-1] if typed.steps else "Values follow the Formula.")
        return {
            "name": output_name,
            "display_name": display_name,
            "type": (
                "Boolean"
                if typed.unit == "BOOLEAN"
                else "Discrete numeric" if typed.unit == "DISCRETE" else "Numeric"
            ),
            "unit": unit,
            "evaluation": f"Every {frequency} · Bar Close",
            "dimension": {
                "TIME_SERIES": "Time Series",
                "CROSS_SECTIONAL": "Cross-sectional",
                "HYBRID": "Time Series + Cross-sectional",
            }.get(typed.dimension, typed.dimension.title()),
            "nullability": "May be unavailable during warmup or when required data is missing",
            "value_meaning": value_meaning,
        }


@dataclass(frozen=True)
class _SeriesFrame:
    values: dict[str, list[float | bool | None]]
    rows: dict[str, list[Mapping[str, Any]]]
    frequency: str


class FactorEngineV4:
    """Execute a compiled Factor graph over complete, point-in-time-safe Bars."""

    def compute(
        self,
        spec: FactorGraphSpec,
        bars: Mapping[str, Any],
    ) -> dict[str, list[dict[str, Any]]]:
        rows_by_input = self._normalize_input_rows(spec, bars)
        frames: dict[str, _SeriesFrame] = {}
        expected_instruments: set[str] | None = None
        for input_spec in spec.inputs:
            variable_name = str(input_spec.get("variable_name") or "")
            frequency = str(input_spec.get("frequency") or "")
            raw_by_instrument = rows_by_input.get(variable_name) or {}
            rows_by_instrument = {
                str(instrument_id): self._validate_rows(
                    variable_name,
                    str(instrument_id),
                    raw_rows,
                    spec,
                    dataset=str(input_spec.get("dataset") or "bars").lower(),
                )
                for instrument_id, raw_rows in raw_by_instrument.items()
            }
            instruments = set(rows_by_instrument)
            if expected_instruments is None:
                expected_instruments = instruments
            elif instruments != expected_instruments:
                raise ValueError(
                    f"Factor Engine v4 requires every Input to cover the same Universe: {variable_name}"
                )
            frames[variable_name] = _SeriesFrame(
                values={
                    instrument_id: [
                        _finite_float(row.get(str(input_spec.get("field") or "")))
                        for row in instrument_rows
                    ]
                    for instrument_id, instrument_rows in rows_by_instrument.items()
                },
                rows=rows_by_instrument,
                frequency=frequency,
            )
        evaluated = self._evaluate(spec.expression, frames)
        if not isinstance(evaluated, _SeriesFrame):
            raise ValueError("compiled Factor graph does not produce a series")
        self._validate_universe_axis(evaluated)
        if evaluated.frequency != spec.frequency:
            raise ValueError(
                f"compiled Factor output frequency {evaluated.frequency} does not match {spec.frequency}"
            )
        result: dict[str, list[dict[str, Any]]] = {}
        for instrument_id, instrument_rows in evaluated.rows.items():
            output: list[dict[str, Any]] = []
            for index, row in enumerate(instrument_rows):
                value = evaluated.values[instrument_id][index]
                output.append({
                    "instrument_id": instrument_id,
                    "event_time": _bar_start_time(row),
                    "bar_start_time": _bar_start_time(row),
                    "bar_end_time": _bar_end_time(row),
                    "factor_as_of_time": _available_time(row),
                    "available_time": _available_time(row),
                    "factor_name": spec.name,
                    "factor_version": spec.version,
                    "value": value,
                    "quality_status": "PASS" if value is not None else "WARMUP",
                })
            result[instrument_id] = output
        return result

    @staticmethod
    def _normalize_input_rows(
        spec: FactorGraphSpec,
        bars: Mapping[str, Any],
    ) -> dict[str, Mapping[str, Sequence[Mapping[str, Any]]]]:
        input_names = [str(item.get("variable_name") or "") for item in spec.inputs]
        if input_names and all(
            name in bars and isinstance(bars.get(name), Mapping)
            for name in input_names
        ):
            return {
                name: bars[name]
                for name in input_names
            }
        return {name: bars for name in input_names}

    @staticmethod
    def _validate_rows(
        variable_name: str,
        instrument_id: str,
        raw_rows: Sequence[Mapping[str, Any]],
        spec: FactorGraphSpec,
        *,
        dataset: str,
    ) -> list[Mapping[str, Any]]:
        rows = sorted(raw_rows, key=_bar_start_time)
        seen: set[str] = set()
        for row in rows:
            start = _bar_start_time(row)
            if start in seen:
                raise ValueError(
                    f"duplicate observation timestamp for {variable_name}/{instrument_id}: {start}"
                )
            seen.add(start)
            if (
                dataset == "bars"
                and
                str(row.get("bar_status") or "COMPLETE").upper() != "COMPLETE"
                and not spec.allow_incomplete_bar
            ):
                raise ValueError(
                    f"incomplete bar is not allowed for {variable_name}/{instrument_id}: {start}"
                )
            end = _bar_end_time(row)
            available = _available_time(row)
            if _parse_time(end) < _parse_time(start):
                raise ValueError(
                    f"observation end time precedes event time for {variable_name}/{instrument_id}: {start}"
                )
            if _parse_time(available) < _parse_time(end):
                raise ValueError(
                    f"available_time precedes observation end time for {variable_name}/{instrument_id}: {start}"
                )
        return rows

    def _evaluate(
        self,
        expression: Mapping[str, Any],
        inputs: Mapping[str, _SeriesFrame],
    ) -> _SeriesFrame | int:
        kind = expression.get("kind")
        if kind == "input":
            variable_name = str(expression.get("name") or "")
            if variable_name not in inputs:
                raise ValueError(f"compiled Factor Input is unavailable: {variable_name}")
            return inputs[variable_name]
        if kind in {"parameter", "literal"}:
            return int(expression.get("value"))
        if kind == "binary":
            left = self._evaluate(dict(expression["left"]), inputs)
            right = self._evaluate(dict(expression["right"]), inputs)
            if not isinstance(left, _SeriesFrame) or not isinstance(right, _SeriesFrame):
                raise ValueError("compiled binary expression is invalid")
            return self._binary(str(expression.get("operator")), left, right)
        if kind == "call":
            function_name = str(expression.get("function"))
            arguments = [
                self._evaluate(dict(item), inputs)
                for item in expression.get("arguments") or []
            ]
            if function_name.startswith("time."):
                series = arguments[0]
                if not isinstance(series, _SeriesFrame):
                    raise ValueError(f"compiled {function_name} arguments are invalid")
                if function_name == "time.ma_crossover":
                    fast_window, slow_window = arguments[1], arguments[2]
                    if not isinstance(fast_window, int) or not isinstance(slow_window, int):
                        raise ValueError("compiled time.ma_crossover windows are invalid")
                    return _SeriesFrame(
                        values={
                            instrument_id: self._ma_crossover(values, fast_window, slow_window)
                            for instrument_id, values in series.values.items()
                        },
                        rows=series.rows,
                        frequency=series.frequency,
                    )
                window = arguments[1]
                if not isinstance(window, int):
                    raise ValueError(f"compiled {function_name} window is invalid")
                return _SeriesFrame(
                    values={
                        instrument_id: self._time_function(function_name, values, window)
                        for instrument_id, values in series.values.items()
                    },
                    rows=series.rows,
                    frequency=series.frequency,
                )
            if function_name.startswith("universe."):
                series = arguments[0]
                if not isinstance(series, _SeriesFrame):
                    raise ValueError(f"compiled {function_name} argument is invalid")
                return self._universe_function(function_name, series)
            if function_name in {"align.asof", "align.forward_fill"}:
                source, reference = arguments
                if not isinstance(source, _SeriesFrame) or not isinstance(reference, _SeriesFrame):
                    raise ValueError(f"compiled {function_name} arguments are invalid")
                return self._align_asof(source, reference)
            if function_name == "safe_divide":
                left, right = arguments
                if not isinstance(left, _SeriesFrame) or not isinstance(right, _SeriesFrame):
                    raise ValueError("compiled safe_divide arguments are invalid")
                return self._binary("/", left, right)
            if function_name == "abs":
                series = arguments[0]
                if not isinstance(series, _SeriesFrame):
                    raise ValueError("compiled abs argument is invalid")
                return self._map_values(
                    series,
                    lambda value: None if value is None else abs(float(value)),
                )
            if function_name in {
                "greater", "less", "equal", "where", "is_null", "is_finite",
                "fill_null", "logical_and", "logical_or", "logical_not",
            }:
                return self._conditional(function_name, arguments)
        raise ValueError("compiled Factor graph contains an unsupported node")

    @staticmethod
    def _same_axis(left: _SeriesFrame, right: _SeriesFrame) -> None:
        if left.frequency != right.frequency or set(left.rows) != set(right.rows):
            raise ValueError("Factor series require explicit frequency alignment")
        for instrument_id in left.rows:
            left_axis = [
                (_bar_start_time(row), _available_time(row))
                for row in left.rows[instrument_id]
            ]
            right_axis = [
                (_bar_start_time(row), _available_time(row))
                for row in right.rows[instrument_id]
            ]
            if left_axis != right_axis:
                raise ValueError("Factor series require explicit point-in-time alignment")

    @classmethod
    def _binary(
        cls,
        operator: str,
        left: _SeriesFrame,
        right: _SeriesFrame,
    ) -> _SeriesFrame:
        cls._same_axis(left, right)
        result: dict[str, list[float | None]] = {}
        for instrument_id in left.values:
            values: list[float | None] = []
            for left_value, right_value in zip(
                left.values[instrument_id],
                right.values[instrument_id],
            ):
                if left_value is None or right_value is None:
                    values.append(None)
                elif operator == "+":
                    values.append(float(left_value) + float(right_value))
                elif operator == "-":
                    values.append(float(left_value) - float(right_value))
                elif operator == "*":
                    values.append(float(left_value) * float(right_value))
                elif operator == "/":
                    values.append(
                        None if float(right_value) == 0 else float(left_value) / float(right_value)
                    )
                else:
                    raise ValueError(f"unsupported compiled binary operator: {operator}")
            result[instrument_id] = values
        return _SeriesFrame(values=result, rows=left.rows, frequency=left.frequency)

    @staticmethod
    def _map_values(series: _SeriesFrame, transform: Any) -> _SeriesFrame:
        return _SeriesFrame(
            values={
                instrument_id: [transform(value) for value in values]
                for instrument_id, values in series.values.items()
            },
            rows=series.rows,
            frequency=series.frequency,
        )

    @classmethod
    def _align_asof(
        cls,
        source: _SeriesFrame,
        reference: _SeriesFrame,
    ) -> _SeriesFrame:
        if set(source.rows) != set(reference.rows):
            raise ValueError("Alignment source and reference must cover the same Universe")
        output: dict[str, list[float | bool | None]] = {}
        for instrument_id, reference_rows in reference.rows.items():
            source_rows = source.rows[instrument_id]
            source_values = source.values[instrument_id]
            source_available = [_parse_time(_available_time(row)) for row in source_rows]
            cursor = -1
            aligned: list[float | bool | None] = []
            for reference_row in reference_rows:
                cutoff = _parse_time(_available_time(reference_row))
                while cursor + 1 < len(source_rows) and source_available[cursor + 1] <= cutoff:
                    cursor += 1
                aligned.append(None if cursor < 0 else source_values[cursor])
            output[instrument_id] = aligned
        return _SeriesFrame(
            values=output,
            rows=reference.rows,
            frequency=reference.frequency,
        )

    @staticmethod
    def _time_function(
        function_name: str,
        values: Sequence[float | bool | None],
        window: int,
    ) -> list[float | None]:
        output: list[float | None] = []
        previous_ema: float | None = None
        for index, current in enumerate(values):
            value: float | None = None
            if function_name in {
                "time.lag", "time.diff", "time.ratio", "time.pct_change", "time.log_return",
            }:
                if index >= window and current is not None:
                    previous = values[index - window]
                    if previous is not None:
                        if function_name == "time.lag":
                            value = float(previous)
                        elif function_name == "time.diff":
                            value = float(current) - float(previous)
                        elif float(previous) != 0:
                            ratio = float(current) / float(previous)
                            if function_name == "time.pct_change":
                                value = ratio - 1.0
                            elif function_name == "time.log_return":
                                value = math.log(ratio) if ratio > 0 else None
                            else:
                                value = ratio
            elif function_name == "time.return_std":
                if index >= window:
                    price_window = values[index - window:index + 1]
                    if all(item not in (None, 0) for item in price_window):
                        returns = [
                            float(price_window[item]) / float(price_window[item - 1]) - 1.0
                            for item in range(1, len(price_window))
                        ]
                        value = statistics.pstdev(returns) if len(returns) > 1 else 0.0
            elif index + 1 >= window:
                sample = values[index - window + 1:index + 1]
                if all(item is not None for item in sample):
                    numeric = [float(item) for item in sample if item is not None]
                    if function_name == "time.sum":
                        value = sum(numeric)
                    elif function_name == "time.mean":
                        value = statistics.fmean(numeric)
                    elif function_name == "time.median":
                        value = statistics.median(numeric)
                    elif function_name == "time.min":
                        value = min(numeric)
                    elif function_name == "time.max":
                        value = max(numeric)
                    elif function_name == "time.std":
                        value = statistics.pstdev(numeric) if len(numeric) > 1 else 0.0
                    elif function_name == "time.variance":
                        value = statistics.pvariance(numeric) if len(numeric) > 1 else 0.0
                    elif function_name == "time.rank":
                        matches = [
                            position
                            for position, candidate in enumerate(sorted(numeric))
                            if candidate == numeric[-1]
                        ]
                        average_position = statistics.fmean(matches)
                        value = 0.5 if len(numeric) == 1 else average_position / (len(numeric) - 1)
                    elif function_name == "time.zscore":
                        mean = statistics.fmean(numeric)
                        deviation = statistics.pstdev(numeric) if len(numeric) > 1 else 0.0
                        value = 0.0 if deviation == 0 else (numeric[-1] - mean) / deviation
                    elif function_name == "time.ema":
                        alpha = 2.0 / (window + 1.0)
                        value = (
                            alpha * float(current) + (1.0 - alpha) * previous_ema
                            if previous_ema is not None and current is not None
                            else statistics.fmean(numeric)
                        )
                        previous_ema = value
            output.append(value)
        return output

    @staticmethod
    def _ma_crossover(
        values: Sequence[float | None],
        fast_window: int,
        slow_window: int,
    ) -> list[float | None]:
        if fast_window < 1 or slow_window < 1 or fast_window >= slow_window:
            raise ValueError("time.ma_crossover requires 0 < fast_window < slow_window")
        output: list[float | None] = []
        for index in range(len(values)):
            if index < slow_window:
                output.append(None)
                continue
            windows = (
                values[index - slow_window:index],
                values[index - slow_window + 1:index + 1],
                values[index - fast_window:index],
                values[index - fast_window + 1:index + 1],
            )
            if any(any(value is None for value in window) for window in windows):
                output.append(None)
                continue
            previous_slow, current_slow, previous_fast, current_fast = windows
            previous_difference = statistics.fmean(float(value) for value in previous_fast) - statistics.fmean(
                float(value) for value in previous_slow
            )
            current_difference = statistics.fmean(float(value) for value in current_fast) - statistics.fmean(
                float(value) for value in current_slow
            )
            if previous_difference <= 0 < current_difference:
                output.append(1.0)
            elif previous_difference >= 0 > current_difference:
                output.append(-1.0)
            else:
                output.append(0.0)
        return output

    @staticmethod
    def _universe_function(
        function_name: str,
        series: _SeriesFrame,
    ) -> _SeriesFrame:
        FactorEngineV4._validate_universe_axis(series)
        instruments = list(series.values)
        length = len(next(iter(series.values.values()), []))
        output = {instrument_id: [None] * length for instrument_id in instruments}
        for index in range(length):
            available = [
                (instrument_id, series.values[instrument_id][index])
                for instrument_id in instruments
                if series.values[instrument_id][index] is not None
            ]
            if not available:
                continue
            numeric = [float(value) for _, value in available if value is not None]
            if function_name == "universe.zscore":
                mean = statistics.fmean(numeric)
                deviation = statistics.pstdev(numeric) if len(numeric) > 1 else 0.0
                for instrument_id, value in available:
                    output[instrument_id][index] = 0.0 if deviation == 0 else (float(value) - mean) / deviation
                continue
            if function_name == "universe.demean":
                mean = statistics.fmean(numeric)
                for instrument_id, value in available:
                    output[instrument_id][index] = float(value) - mean
                continue
            sorted_values = sorted(numeric)
            for instrument_id, value in available:
                matches = [position for position, candidate in enumerate(sorted_values) if candidate == float(value)]
                average_position = statistics.fmean(matches)
                output[instrument_id][index] = 0.5 if len(sorted_values) == 1 else average_position / (len(sorted_values) - 1)
        return _SeriesFrame(values=output, rows=series.rows, frequency=series.frequency)

    @classmethod
    def _conditional(
        cls,
        function_name: str,
        arguments: Sequence[_SeriesFrame | int],
    ) -> _SeriesFrame:
        series_arguments = [item for item in arguments if isinstance(item, _SeriesFrame)]
        if not series_arguments:
            raise ValueError(f"compiled {function_name} arguments are invalid")
        base = series_arguments[0]
        for item in series_arguments[1:]:
            cls._same_axis(base, item)
        output: dict[str, list[float | bool | None]] = {}
        for instrument_id in base.values:
            argument_values = [item.values[instrument_id] for item in series_arguments]
            result: list[float | bool | None] = []
            for values in zip(*argument_values):
                if function_name == "is_null":
                    result.append(values[0] is None)
                elif function_name == "is_finite":
                    result.append(
                        values[0] is not None and math.isfinite(float(values[0]))
                    )
                elif function_name == "fill_null":
                    result.append(values[0] if values[0] is not None else values[1])
                elif function_name == "where":
                    result.append(
                        None
                        if values[0] is None
                        else values[1] if bool(values[0]) else values[2]
                    )
                elif function_name in {"greater", "less", "equal"}:
                    if values[0] is None or values[1] is None:
                        result.append(None)
                    elif function_name == "greater":
                        result.append(float(values[0]) > float(values[1]))
                    elif function_name == "less":
                        result.append(float(values[0]) < float(values[1]))
                    else:
                        result.append(float(values[0]) == float(values[1]))
                elif function_name == "logical_not":
                    result.append(None if values[0] is None else not bool(values[0]))
                elif function_name in {"logical_and", "logical_or"}:
                    if values[0] is None or values[1] is None:
                        result.append(None)
                    elif function_name == "logical_and":
                        result.append(bool(values[0]) and bool(values[1]))
                    else:
                        result.append(bool(values[0]) or bool(values[1]))
                else:
                    raise ValueError(f"unsupported compiled conditional function: {function_name}")
            output[instrument_id] = result
        return _SeriesFrame(values=output, rows=base.rows, frequency=base.frequency)

    @staticmethod
    def _validate_universe_axis(series: _SeriesFrame) -> None:
        lengths = {len(rows) for rows in series.rows.values()}
        if len(lengths) > 1:
            raise ValueError("Factor Engine v4 requires aligned bar counts across the Universe")
        axes = {
            tuple(
                (_bar_start_time(row), _available_time(row))
                for row in instrument_rows
            )
            for instrument_rows in series.rows.values()
        }
        if len(axes) > 1:
            raise ValueError("Factor Engine v4 requires aligned point-in-time axes across the Universe")
