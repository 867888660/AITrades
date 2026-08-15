from __future__ import annotations

from typing import Any, Mapping, Sequence

from .definition_registry import ResearchDefinition
from .factor_alpha import FactorEngine, FactorSpec
from .factor_engine_v4 import (
    FACTOR_ENGINE_V4_VERSION,
    FactorEngineV4,
    FactorGraphSpec,
)
from .equity_factor_bridge import (
    field_is_available,
    is_sparse_dataset,
    physical_data_types,
    project_factor_rows,
)


class FactorDefinitionExecutor:
    """Execute one immutable Factor definition using Formal Run semantics."""

    def execute(
        self,
        definition: ResearchDefinition,
        *,
        manifest_inputs: Sequence[Mapping[str, Any]],
        bars_by_instrument: Mapping[str, Sequence[Mapping[str, Any]]],
        allowed_instruments: set[str],
    ) -> tuple[Any, dict[str, list[dict[str, Any]]]]:
        spec = self.spec_for(definition)
        if isinstance(spec, FactorGraphSpec):
            values = FactorEngineV4().compute(
                spec,
                self.bind_factor_inputs(
                    spec,
                    manifest_inputs,
                    allowed_instruments,
                ),
            )
            return spec, values
        values = FactorEngine().compute(spec, bars_by_instrument)
        return spec, values

    @staticmethod
    def spec_for(definition: ResearchDefinition) -> Any:
        if definition.definition_type != "FACTOR":
            raise ValueError(
                "FactorDefinitionExecutor requires a FACTOR definition"
            )
        if definition.engine_version == FACTOR_ENGINE_V4_VERSION:
            return FactorGraphSpec.from_dict(definition.spec)
        formula = definition.spec["formula"]
        return FactorSpec(
            name=definition.name,
            version=definition.version,
            operator=formula["operator"],
            input_field=formula["input"],
            window=int(formula["window"]),
            minimum_observations=int(
                definition.spec.get("minimum_observations") or 1
            ),
            missing_policy=definition.spec.get("missing_policy", "STRICT"),
            parameters=dict(formula.get("parameters") or {}),
            frequency=definition.spec.get("frequency", ""),
            dimension=definition.spec.get("dimension", "TIME_SERIES"),
            time_alignment_policy=definition.spec.get(
                "time_alignment_policy",
                "BAR_END_AVAILABLE_TIME",
            ),
            available_after=definition.spec.get(
                "available_after",
                "BAR_CLOSE",
            ),
            allow_incomplete_bar=bool(
                definition.spec.get("allow_incomplete_bar", False)
            ),
            output_unit=definition.spec.get("output_unit", "RATIO"),
            output_direction=definition.spec.get(
                "output_direction",
                "NO_PREDEFINED_DIRECTION",
            ),
        )

    @staticmethod
    def bind_factor_inputs(
        spec: Any,
        manifest_inputs: Sequence[Mapping[str, Any]],
        allowed_instruments: set[str],
    ) -> dict[str, dict[str, list[dict[str, Any]]]]:
        bound: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for input_spec in spec.inputs:
            variable_name = str(input_spec.get("variable_name") or "")
            frequency = str(input_spec.get("frequency") or "").lower()
            field = str(input_spec.get("field") or "").lower()
            dataset = str(input_spec.get("dataset") or "bars").lower()
            candidates: dict[
                str,
                list[tuple[str, list[dict[str, Any]]]],
            ] = {}
            for source in manifest_inputs:
                if str(source["frequency"]).lower() != frequency:
                    continue
                physical_type = str(source.get("data_type") or "").lower()
                if physical_type and physical_type not in physical_data_types(dataset):
                    continue
                source_fields = {
                    str(item).lower() for item in source["fields"]
                }
                for instrument_id, rows in source["rows"].items():
                    if instrument_id not in allowed_instruments:
                        continue
                    if not field_is_available(
                        dataset,
                        field,
                        physical_data_type=physical_type or dataset,
                        catalog_fields=source_fields,
                    ) and not any(field in row for row in rows[:1]):
                        continue
                    projected = project_factor_rows(dataset, field, rows)
                    candidates.setdefault(instrument_id, []).append(
                        (
                            str(source["manifest_id"]),
                            projected,
                        )
                    )
            missing = sorted(allowed_instruments - set(candidates))
            if missing and not is_sparse_dataset(dataset):
                raise ValueError(
                    f"Frozen Manifests do not supply Input {variable_name} "
                    f"({field} @ {frequency}) for Universe members: {missing}"
                )
            ambiguous = {
                instrument_id: [manifest_id for manifest_id, _ in items]
                for instrument_id, items in candidates.items()
                if len(items) != 1
            }
            if ambiguous:
                raise ValueError(
                    f"Frozen Manifests ambiguously bind Input "
                    f"{variable_name}: {ambiguous}"
                )
            bound[variable_name] = {
                instrument_id: candidates.get(instrument_id, [("", [])])[0][1]
                for instrument_id in allowed_instruments
            }
        return bound
