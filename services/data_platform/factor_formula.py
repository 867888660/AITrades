from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Any, Mapping

from .factor_alpha import FactorSpec
from .factor_engine_v4 import FACTOR_GRAPH_CONTRACT_VERSION, FactorGraphCompiler


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _clean(value: Any) -> str:
    return str(value or "").strip()


@dataclass(frozen=True)
class FormulaDiagnostic:
    level: str
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "level": self.level,
            "code": self.code,
            "path": self.path,
            "message": self.message,
        }


@dataclass(frozen=True)
class FactorFormulaCompilation:
    source: str
    input_variable: str
    operator: str
    parameter_bindings: dict[str, dict[str, Any]]
    factor_spec: dict[str, Any]
    spec_hash: str
    resolved_formula: str
    required_history: str
    formula_meaning: str
    output_display: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "input_variable": self.input_variable,
            "operator": self.operator,
            "parameter_bindings": self.parameter_bindings,
            "factor_spec": self.factor_spec,
            "spec_hash": self.spec_hash,
            "resolved_formula": self.resolved_formula,
            "required_history": self.required_history,
            "formula_meaning": self.formula_meaning,
            "output_display": self.output_display,
        }


class FactorFormulaCompiler:
    """Compile the deliberately small Factor Engine v3 formula surface."""

    @staticmethod
    def normalize_inputs(document: Mapping[str, Any]) -> list[dict[str, Any]]:
        raw_inputs = document.get("inputs")
        if isinstance(raw_inputs, list):
            return [dict(item) for item in raw_inputs if isinstance(item, Mapping)]
        legacy = document.get("input")
        return [dict(legacy)] if isinstance(legacy, Mapping) and legacy else []

    @staticmethod
    def normalize_parameters(document: Mapping[str, Any]) -> list[dict[str, Any]]:
        raw_parameters = document.get("parameters")
        if isinstance(raw_parameters, list):
            return [dict(item) for item in raw_parameters if isinstance(item, Mapping)]
        formula = document.get("formula")
        legacy = formula.get("parameters") if isinstance(formula, Mapping) else None
        if not isinstance(legacy, Mapping):
            return []
        return [
            {"name": str(name), "value": value, "unit": "bars"}
            for name, value in legacy.items()
        ]

    @staticmethod
    def normalize_source(
        document: Mapping[str, Any],
        inputs: list[dict[str, Any]],
        parameters: list[dict[str, Any]],
    ) -> str:
        formula = document.get("formula")
        if not isinstance(formula, Mapping):
            return ""
        source = _clean(formula.get("source"))
        if source:
            return source
        operator = _clean(formula.get("operator"))
        if not operator:
            return ""
        input_name = _clean((inputs[0] if inputs else {}).get("variable_name")) or "price"
        parameter_names = [_clean(item.get("name")) for item in parameters if _clean(item.get("name"))]
        return f"{operator}({', '.join([input_name, *parameter_names])})"

    @classmethod
    def inspect(
        cls,
        document: Mapping[str, Any],
        capabilities: Mapping[str, Any],
    ) -> dict[str, Any]:
        if _clean(capabilities.get("formula_contract")) == FACTOR_GRAPH_CONTRACT_VERSION:
            return FactorGraphCompiler.inspect(document, capabilities)
        diagnostics: list[FormulaDiagnostic] = []

        def add(level: str, code: str, path: str, message: str) -> None:
            diagnostics.append(FormulaDiagnostic(level, code, path, message))

        inputs = cls.normalize_inputs(document)
        parameters = cls.normalize_parameters(document)
        source = cls.normalize_source(document, inputs, parameters)
        fields = {
            _clean(item.get("id"))
            for item in capabilities.get("features") or []
            if isinstance(item, Mapping)
        }
        frequencies = {_clean(item) for item in capabilities.get("frequencies") or []}
        operators = {
            _clean(item.get("id")): dict(item)
            for item in capabilities.get("operator_schema") or []
            if isinstance(item, Mapping)
        }

        if not inputs:
            add("ERROR", "INPUT_REQUIRED", "inputs", "At least one Input is required.")
        if len(inputs) > 1:
            add(
                "ERROR",
                "FACTOR_V3_INPUT_LIMIT",
                "inputs",
                "Factor Engine v3 supports at most one Input. Remove extra Inputs or use a later engine.",
            )

        input_by_name: dict[str, dict[str, Any]] = {}
        for index, input_spec in enumerate(inputs):
            path = f"inputs.{index}"
            variable_name = _clean(input_spec.get("variable_name"))
            if not variable_name:
                add("ERROR", "INPUT_VARIABLE_REQUIRED", f"{path}.variable_name", "Input variable name is required.")
            elif not _IDENTIFIER.fullmatch(variable_name):
                add(
                    "ERROR",
                    "INPUT_VARIABLE_INVALID",
                    f"{path}.variable_name",
                    f"{variable_name} is not a valid Formula identifier.",
                )
            elif variable_name in input_by_name:
                add("ERROR", "INPUT_VARIABLE_DUPLICATE", f"{path}.variable_name", f"Duplicate Input variable: {variable_name}.")
            else:
                input_by_name[variable_name] = input_spec
            if _clean(input_spec.get("dataset") or "bars").lower() != "bars":
                add(
                    "ERROR",
                    "INPUT_DATASET_UNSUPPORTED",
                    f"{path}.dataset",
                    "Factor Engine v3 currently supports Bars inputs.",
                )
            field = _clean(input_spec.get("field"))
            if not field:
                add("ERROR", "INPUT_FIELD_REQUIRED", f"{path}.field", "Input field is required.")
            elif field not in fields:
                add("ERROR", "INPUT_FIELD_UNSUPPORTED", f"{path}.field", f"Unsupported input field: {field}.")
            frequency = _clean(input_spec.get("frequency"))
            if not frequency:
                add("ERROR", "INPUT_FREQUENCY_REQUIRED", f"{path}.frequency", "Input frequency is required.")
            elif frequency not in frequencies:
                add(
                    "ERROR",
                    "INPUT_FREQUENCY_UNSUPPORTED",
                    f"{path}.frequency",
                    f"Unsupported frequency: {frequency}.",
                )

        parameter_by_name: dict[str, dict[str, Any]] = {}
        for index, parameter in enumerate(parameters):
            path = f"parameters.{index}"
            name = _clean(parameter.get("name"))
            if not name:
                add("ERROR", "FORMULA_PARAMETER_NAME_REQUIRED", f"{path}.name", "Parameter name is required.")
            elif not _IDENTIFIER.fullmatch(name):
                add(
                    "ERROR",
                    "FORMULA_PARAMETER_NAME_INVALID",
                    f"{path}.name",
                    f"{name} is not a valid Formula identifier.",
                )
            elif name in parameter_by_name:
                add("ERROR", "FORMULA_PARAMETER_DUPLICATE", f"{path}.name", f"Duplicate Parameter: {name}.")
            else:
                parameter_by_name[name] = parameter
            if _clean(parameter.get("unit")).lower() != "bars":
                add(
                    "ERROR",
                    "FACTOR_V3_PARAMETER_UNIT_UNSUPPORTED",
                    f"{path}.unit",
                    "Factor Engine v3 window Parameters must use the bars unit.",
                )

        parsed: ast.Expression | None = None
        call: ast.Call | None = None
        referenced_names: list[str] = []
        operator = ""
        arguments_supported = True
        if not source:
            add("ERROR", "FORMULA_SOURCE_REQUIRED", "formula.source", "Formula source is required.")
        else:
            try:
                parsed = ast.parse(source, mode="eval")
            except SyntaxError as exc:
                location = f"line {exc.lineno or 1}, column {exc.offset or 1}"
                add(
                    "ERROR",
                    "FORMULA_SYNTAX_ERROR",
                    "formula.source",
                    f"Formula syntax error at {location}.",
                )
            if parsed is not None:
                expression = parsed.body
                if isinstance(expression, ast.BinOp):
                    add(
                        "ERROR",
                        "FACTOR_V3_COMPOSITION_UNSUPPORTED",
                        "formula.source",
                        "Factor Engine v3 does not support arithmetic or combined expressions.",
                    )
                elif not isinstance(expression, ast.Call):
                    add(
                        "ERROR",
                        "FACTOR_V3_EXPRESSION_UNSUPPORTED",
                        "formula.source",
                        "Factor Engine v3 requires one top-level function call.",
                    )
                else:
                    call = expression
                    if not isinstance(call.func, ast.Name):
                        arguments_supported = False
                        add(
                            "ERROR",
                            "FACTOR_V3_FUNCTION_REFERENCE_UNSUPPORTED",
                            "formula.source",
                            "Factor Engine v3 functions must use an unqualified function name.",
                        )
                    else:
                        operator = call.func.id
                    if call.keywords:
                        arguments_supported = False
                        add(
                            "ERROR",
                            "FACTOR_V3_KEYWORD_ARGUMENT_UNSUPPORTED",
                            "formula.source",
                            "Factor Engine v3 supports positional Input and Parameter references only.",
                        )
                    if any(isinstance(node, ast.Call) for node in call.args):
                        arguments_supported = False
                        add(
                            "ERROR",
                            "FACTOR_V3_NESTED_EXPRESSION_UNSUPPORTED",
                            "formula.source",
                            "Factor Engine v3 does not support nested or composed function calls.",
                        )
                    for node in call.args:
                        if isinstance(node, ast.Name):
                            referenced_names.append(node.id)
                        elif not isinstance(node, ast.Call):
                            arguments_supported = False
                            add(
                                "ERROR",
                                "FACTOR_V3_LITERAL_ARGUMENT_UNSUPPORTED",
                                "formula.source",
                                "Declare values as named Parameters; literal or calculated arguments are not supported.",
                            )

        operator_schema = operators.get(operator)
        if operator and operator_schema is None:
            add(
                "ERROR",
                "FORMULA_FUNCTION_UNSUPPORTED",
                "formula.source",
                f"Factor Engine v3 does not support function: {operator}.",
            )

        input_variable = referenced_names[0] if referenced_names else ""
        if call is not None and operator_schema is not None and arguments_supported:
            expected_parameters = [
                dict(item)
                for item in operator_schema.get("parameters") or []
                if isinstance(item, Mapping)
            ]
            expected_arity = 1 + len(expected_parameters)
            if len(call.args) != expected_arity:
                add(
                    "ERROR",
                    "FORMULA_ARGUMENT_COUNT",
                    "formula.source",
                    f"{operator} expects {expected_arity} arguments: one Input and {len(expected_parameters)} Parameter(s).",
                )
            if input_variable and input_variable not in input_by_name:
                add(
                    "ERROR",
                    "FORMULA_INPUT_UNKNOWN",
                    "formula.source",
                    f"Unknown Input: {input_variable}.",
                )

            for index, parameter_schema in enumerate(expected_parameters, start=1):
                if index >= len(referenced_names):
                    continue
                parameter_name = referenced_names[index]
                parameter = parameter_by_name.get(parameter_name)
                if parameter is None:
                    add(
                        "ERROR",
                        "FORMULA_PARAMETER_UNKNOWN",
                        "formula.source",
                        f"Unknown Parameter: {parameter_name}.",
                    )
                    continue
                raw = parameter.get("value")
                schema_name = _clean(parameter_schema.get("name"))
                path = f"parameters.{parameters.index(parameter)}.value"
                if parameter_schema.get("type") == "integer" and (
                    isinstance(raw, bool) or not isinstance(raw, int)
                ):
                    add("ERROR", "FORMULA_PARAMETER_TYPE", path, f"{parameter_name} must be an integer number of bars.")
                    continue
                minimum = parameter_schema.get("minimum")
                if minimum is not None and float(raw) < float(minimum):
                    add("ERROR", "FORMULA_PARAMETER_MINIMUM", path, f"{parameter_name} must be at least {minimum} bars.")
                parameter["_compiled_name"] = schema_name

        if arguments_supported and input_by_name and input_variable:
            for variable_name in input_by_name:
                if variable_name != input_variable:
                    add(
                        "WARNING",
                        "UNUSED_INPUT",
                        "inputs",
                        f"Input {variable_name} is not referenced by the Formula.",
                    )
        if call is not None and arguments_supported:
            referenced_parameters = set(referenced_names[1:])
            for name in parameter_by_name:
                if name not in referenced_parameters:
                    add(
                        "WARNING",
                        "UNUSED_PARAMETER",
                        "parameters",
                        f"Parameter {name} is not referenced by the Formula.",
                    )

        compilation: FactorFormulaCompilation | None = None
        identity = document.get("identity") if isinstance(document.get("identity"), Mapping) else {}
        identity_ready = bool(_clean(identity.get("name")) and _clean(identity.get("version")))
        if (
            identity_ready
            and not any(item.level == "ERROR" for item in diagnostics)
            and operator_schema is not None
        ):
            input_spec = input_by_name[input_variable]
            expected_parameters = [
                dict(item)
                for item in operator_schema.get("parameters") or []
                if isinstance(item, Mapping)
            ]
            parameter_bindings: dict[str, dict[str, Any]] = {}
            compiled_values: dict[str, Any] = {}
            for index, parameter_schema in enumerate(expected_parameters, start=1):
                source_name = referenced_names[index]
                parameter = parameter_by_name[source_name]
                engine_name = _clean(parameter_schema.get("name"))
                compiled_values[engine_name] = parameter.get("value")
                parameter_bindings[engine_name] = {
                    "source_name": source_name,
                    "value": parameter.get("value"),
                    "unit": _clean(parameter.get("unit")).lower(),
                }

            output = document.get("output") if isinstance(document.get("output"), Mapping) else {}
            advanced = document.get("advanced") if isinstance(document.get("advanced"), Mapping) else {}
            window = compiled_values.pop("window", 1)
            minimum_observations = advanced.get("minimum_observations")
            try:
                spec = FactorSpec(
                    name=_clean(identity.get("name")),
                    version=_clean(identity.get("version")),
                    operator=operator,
                    input_field=_clean(input_spec.get("field")),
                    window=int(window),
                    minimum_observations=int(minimum_observations) if minimum_observations not in (None, "") else None,
                    missing_policy=_clean(advanced.get("missing_policy") or "STRICT").upper(),
                    parameters=compiled_values,
                    frequency=_clean(input_spec.get("frequency")),
                    dimension=_clean(advanced.get("dimension") or "TIME_SERIES").upper(),
                    time_alignment_policy=_clean(advanced.get("time_alignment_policy") or "BAR_END_AVAILABLE_TIME").upper(),
                    available_after=_clean(advanced.get("available_after") or "BAR_CLOSE").upper(),
                    allow_incomplete_bar=bool(advanced.get("allow_incomplete_bar", False)),
                    output_unit=_clean(operator_schema.get("output_unit") or "RATIO").upper(),
                    output_direction=_clean(output.get("direction") or "NO_PREDEFINED_DIRECTION").upper(),
                )
                compilation = FactorFormulaCompilation(
                    source=source,
                    input_variable=input_variable,
                    operator=operator,
                    parameter_bindings=parameter_bindings,
                    factor_spec=spec.to_dict(),
                    spec_hash=spec.spec_hash,
                    resolved_formula=cls._resolved_formula(
                        operator,
                        input_spec,
                        expected_parameters,
                        parameter_bindings,
                    ),
                    required_history=f"{spec.required_observations} bars",
                    formula_meaning=cls._formula_meaning(
                        operator,
                        input_spec,
                        expected_parameters,
                        parameter_bindings,
                    ),
                    output_display=cls._output_display(
                        operator,
                        input_variable,
                        input_spec,
                        spec,
                    ),
                )
            except (TypeError, ValueError) as exc:
                add("ERROR", "FACTOR_SPEC_INVALID", "formula.source", str(exc))

        return {
            "inputs": inputs,
            "parameters": [
                {key: value for key, value in item.items() if key != "_compiled_name"}
                for item in parameters
            ],
            "source": source,
            "diagnostics": [item.to_dict() for item in diagnostics],
            "compilation": compilation.to_dict() if compilation else None,
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
        return dict(result["compilation"]["factor_spec"])

    @staticmethod
    def _resolved_formula(
        operator: str,
        input_spec: Mapping[str, Any],
        parameter_schema: list[dict[str, Any]],
        parameter_bindings: Mapping[str, Mapping[str, Any]],
    ) -> str:
        field = _clean(input_spec.get("field")) or "field"
        frequency = _clean(input_spec.get("frequency")) or "frequency"
        arguments = [f"Bars.{field} @ {frequency}"]
        for schema in parameter_schema:
            name = _clean(schema.get("name"))
            binding = parameter_bindings.get(name) or {}
            arguments.append(f"{binding.get('value')} {binding.get('unit') or 'bars'}")
        return f"{operator}({', '.join(arguments)})"

    @staticmethod
    def _formula_meaning(
        operator: str,
        input_spec: Mapping[str, Any],
        parameter_schema: list[dict[str, Any]],
        parameter_bindings: Mapping[str, Mapping[str, Any]],
    ) -> str:
        field = _clean(input_spec.get("field")) or "input"
        frequency = _clean(input_spec.get("frequency")) or "selected-frequency"
        values = {
            _clean(schema.get("name")): parameter_bindings.get(_clean(schema.get("name")), {}).get("value")
            for schema in parameter_schema
        }
        window = values.get("window")
        phrases = {
            "rolling_std": f"Calculate the rolling standard deviation of {field} over the previous {window} {frequency} bars.",
            "rolling_mean": f"Calculate the rolling average of {field} over the previous {window} {frequency} bars.",
            "rolling_return_std": f"Calculate return volatility from {field} over the previous {window} {frequency} bars.",
            "pct_change": f"Calculate the percentage change in {field} from {window} {frequency} bars earlier.",
            "difference": f"Calculate the difference in {field} from {window} {frequency} bars earlier.",
            "ratio": f"Calculate the ratio of current {field} to its value {window} {frequency} bars earlier.",
            "ema": f"Calculate the exponential moving average of {field} over {window} {frequency} bars.",
            "ma_crossover": (
                f"Detect crossings between the {values.get('fast_window')}-bar and "
                f"{window}-bar moving averages of {field}."
            ),
        }
        return phrases.get(operator, f"Calculate {operator} from the selected Input and Parameters.")

    @staticmethod
    def _output_display(
        operator: str,
        input_variable: str,
        input_spec: Mapping[str, Any],
        spec: FactorSpec,
    ) -> dict[str, str]:
        unit = {
            "SOURCE": f"Same as {input_variable}",
            "ABSOLUTE": f"Same as {input_variable}",
            "RATIO": "Ratio",
            "DISCRETE": "Discrete event",
        }.get(spec.output_unit, spec.output_unit.title())
        field = _clean(input_spec.get("field")) or input_variable
        meanings = {
            "rolling_std": "Higher values indicate greater price variation over the selected window.",
            "rolling_mean": f"Values represent the rolling average level of {field}.",
            "rolling_return_std": "Higher values indicate greater return variation over the selected window.",
            "pct_change": "Positive values indicate an increase; negative values indicate a decrease.",
            "difference": "Positive values are above the comparison value; negative values are below it.",
            "ratio": "Values above 1 are above the comparison value; values below 1 are below it.",
            "ema": f"Values represent the exponentially weighted average level of {field}.",
            "ma_crossover": "1 marks a golden cross, -1 a death cross, and 0 no new crossing.",
        }
        return {
            "type": "Discrete numeric" if operator == "ma_crossover" else "Numeric",
            "unit": unit,
            "evaluation": f"Every {spec.frequency} · Bar Close",
            "value_meaning": meanings.get(operator, "Values follow the selected Formula."),
        }
