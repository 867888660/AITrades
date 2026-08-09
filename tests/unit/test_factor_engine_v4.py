from __future__ import annotations

import unittest

from services.data_platform import (
    DefinitionRegistry,
    FactorEngineV4,
    FactorGraphCompiler,
    FactorGraphSpec,
    FormalResearchRunExecutor,
)
from services.data_platform.factor_engine_v4 import _FUNCTIONS


def document(
    source: str = "universe.rank(time.pct_change(price, lookback))",
) -> dict:
    return {
        "schema_version": "factor_draft.v2",
        "identity": {"name": "momentum_rank", "version": "1.0.0"},
        "inputs": [{
            "variable_name": "price",
            "dataset": "bars",
            "field": "close",
            "frequency": "1h",
        }],
        "parameters": [{"name": "lookback", "value": 2, "unit": "bars"}],
        "formula": {"source": source},
        "output": {"direction": "NO_PREDEFINED_DIRECTION"},
        "advanced": {
            "missing_policy": "STRICT",
            "time_alignment_policy": "BAR_END_AVAILABLE_TIME",
            "available_after": "BAR_CLOSE",
        },
    }


def bars(values: list[float]) -> list[dict]:
    result = []
    for index, value in enumerate(values):
        hour = f"{index:02d}"
        next_hour = f"{index + 1:02d}"
        result.append({
            "bar_start_time": f"2026-01-01T{hour}:00:00+00:00",
            "bar_end_time": f"2026-01-01T{next_hour}:00:00+00:00",
            "available_time": f"2026-01-01T{next_hour}:00:00+00:00",
            "bar_status": "COMPLETE",
            "close": value,
            "volume": value * 10,
        })
    return result


def timed_bars(
    rows: list[tuple[str, str, str, float]],
    *,
    field: str,
) -> list[dict]:
    return [
        {
            "bar_start_time": start,
            "bar_end_time": end,
            "available_time": available,
            "bar_status": "COMPLETE",
            field: value,
        }
        for start, end, available, value in rows
    ]


class FactorEngineV4Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.capabilities = DefinitionRegistry.engine_capabilities()["factor"]

    def test_nested_time_and_universe_formula_compiles_to_typed_graph(self) -> None:
        result = FactorGraphCompiler.inspect(document(), self.capabilities)
        compilation = result["compilation"]
        spec = compilation["factor_spec"]

        self.assertEqual([], result["diagnostics"])
        self.assertEqual("factor-engine.v4", spec["engine_version"])
        self.assertEqual("factor_formula.v4", spec["formula_contract"])
        self.assertEqual("HYBRID", spec["dimension"])
        self.assertEqual({"price": 3}, spec["required_history"])
        self.assertEqual("PERCENTILE", spec["output_unit"])
        self.assertEqual(
            "universe.rank(time.pct_change(Bars.close @ 1h, 2 bars))",
            compilation["resolved_formula"],
        )
        self.assertEqual(
            "universe.rank",
            spec["formula"]["ast"]["function"],
        )
        self.assertEqual(
            "time.pct_change",
            spec["formula"]["ast"]["arguments"][0]["function"],
        )

    def test_polymarket_price_history_compiles_and_executes_with_event_time(self) -> None:
        value = document("time.pct_change(price, lookback)")
        value["inputs"][0].update({
            "dataset": "price_history",
            "field": "price",
        })
        compiled = FactorGraphCompiler.compile(value, self.capabilities)
        spec = FactorGraphSpec.from_dict(compiled)
        rows = [{
            "instrument_id": "polymarket_binary:POLYMARKET:yes-token",
            "event_time": f"2026-01-01T0{index}:00:00+00:00",
            "available_time": f"2026-01-01T0{index}:00:00+00:00",
            "price": price,
        } for index, price in enumerate((0.40, 0.45, 0.50, 0.60))]

        computed = FactorEngineV4().compute(
            spec,
            {"polymarket_binary:POLYMARKET:yes-token": rows},
        )

        self.assertEqual("EVENT_TIME_AVAILABLE_TIME", spec.time_alignment_policy)
        self.assertEqual("EVENT_AVAILABLE", spec.available_after)
        self.assertIsNotNone(computed["polymarket_binary:POLYMARKET:yes-token"][-1]["value"])
        self.assertEqual(
            rows[-1]["event_time"],
            computed["polymarket_binary:POLYMARKET:yes-token"][-1]["available_time"],
        )

    def test_capability_function_schema_matches_the_compiler(self) -> None:
        capability_ids = {
            item["id"]
            for item in self.capabilities["function_schema"]
        }

        self.assertEqual(set(_FUNCTIONS), capability_ids)

    def test_multiple_inputs_and_arithmetic_compile_when_frequencies_match(self) -> None:
        value = document("safe_divide(volume, price) + time.pct_change(price, lookback)")
        value["inputs"].append({
            "variable_name": "volume",
            "dataset": "bars",
            "field": "volume",
            "frequency": "1h",
        })
        result = FactorGraphCompiler.inspect(value, self.capabilities)

        self.assertFalse([item for item in result["diagnostics"] if item["level"] == "ERROR"])
        self.assertEqual({"volume": 1, "price": 3}, result["compilation"]["factor_spec"]["required_history"])
        self.assertEqual("expression", result["compilation"]["factor_spec"]["formula"]["operator"])

    def test_named_results_compile_to_one_explicit_output(self) -> None:
        value = document(
            "returns = time.pct_change(price, 1)\n"
            "fast_vol = time.std(returns, 2)\n"
            "slow_vol = time.std(returns, 3)\n"
            "ratio = safe_divide(fast_vol, slow_vol)"
        )
        value["parameters"] = []
        value["output"]["final"] = "ratio"
        value["output"]["display_name"] = "Volatility Ratio"

        result = FactorGraphCompiler.inspect(value, self.capabilities)
        compilation = result["compilation"]
        spec = FactorGraphSpec.from_dict(compilation["factor_spec"])
        values = FactorEngineV4().compute(
            spec,
            {"A": bars([1, 2, 3, 6, 8, 12])},
        )

        self.assertFalse([item for item in result["diagnostics"] if item["level"] == "ERROR"])
        self.assertEqual("ratio", compilation["output_name"])
        self.assertEqual("ratio", compilation["factor_spec"]["formula"]["output"])
        self.assertEqual("Volatility Ratio", compilation["factor_spec"]["output_display_name"])
        self.assertEqual({"price": 4}, compilation["factor_spec"]["required_history"])
        self.assertEqual(
            ["returns", "fast_vol", "slow_vol", "ratio"],
            [item["name"] for item in compilation["named_results"]],
        )
        self.assertIn("returns = time.pct_change(Bars.close @ 1h, 1 bar)", compilation["resolved_formula"])
        self.assertIsNotNone(values["A"][-1]["value"])

    def test_named_results_require_output_when_no_factor_name_exists(self) -> None:
        value = document(
            "short = time.mean(price, 2)\n"
            "long = time.mean(price, 3)"
        )
        value["parameters"] = []

        result = FactorGraphCompiler.inspect(value, self.capabilities)

        self.assertIn(
            "FORMULA_OUTPUT_REQUIRED",
            {item["code"] for item in result["diagnostics"]},
        )
        self.assertIsNone(result["compilation"])

    def test_named_result_forward_reference_is_rejected(self) -> None:
        value = document(
            "factor = time.std(returns, 2)\n"
            "returns = time.pct_change(price, 1)"
        )
        value["parameters"] = []

        result = FactorGraphCompiler.inspect(value, self.capabilities)

        self.assertIn(
            "FORMULA_RESULT_FORWARD_REFERENCE",
            {item["code"] for item in result["diagnostics"]},
        )
        self.assertIsNone(result["compilation"])

    def test_single_expression_accepts_inline_window_literal(self) -> None:
        value = document("time.std(time.pct_change(price, 1), 20)")
        value["parameters"] = []

        result = FactorGraphCompiler.inspect(value, self.capabilities)

        self.assertFalse([item for item in result["diagnostics"] if item["level"] == "ERROR"])
        self.assertEqual({"price": 21}, result["compilation"]["factor_spec"]["required_history"])
        self.assertEqual("21 bars", result["compilation"]["required_history"])

    def test_mixed_frequency_inputs_require_explicit_alignment(self) -> None:
        value = document("safe_divide(price, volume)")
        value["inputs"].append({
            "variable_name": "volume",
            "dataset": "bars",
            "field": "volume",
            "frequency": "1d",
        })
        result = FactorGraphCompiler.inspect(value, self.capabilities)

        self.assertIn(
            "FACTOR_V4_FREQUENCY_ALIGNMENT_REQUIRED",
            {item["code"] for item in result["diagnostics"]},
        )
        self.assertIsNone(result["compilation"])

    def test_explicit_asof_alignment_compiles_to_reference_frequency(self) -> None:
        value = document("safe_divide(price, align.asof(volume, price))")
        value["inputs"].append({
            "variable_name": "volume",
            "dataset": "bars",
            "field": "volume",
            "frequency": "1d",
        })
        result = FactorGraphCompiler.inspect(value, self.capabilities)

        self.assertFalse([item for item in result["diagnostics"] if item["level"] == "ERROR"])
        self.assertEqual("1h", result["compilation"]["factor_spec"]["frequency"])
        self.assertEqual(
            "align.asof",
            result["compilation"]["factor_spec"]["formula"]["ast"]["arguments"][1]["function"],
        )
        self.assertIn(
            "latest source value",
            result["compilation"]["formula_meaning"],
        )

    def test_history_inference_converts_window_across_aligned_frequencies(self) -> None:
        value = document("time.mean(align.asof(volume, price), lookback)")
        value["parameters"][0]["value"] = 20
        value["inputs"].append({
            "variable_name": "volume",
            "dataset": "bars",
            "field": "volume",
            "frequency": "1d",
        })
        result = FactorGraphCompiler.inspect(value, self.capabilities)

        self.assertEqual(
            {"price": 20, "volume": 2},
            result["compilation"]["factor_spec"]["required_history"],
        )

    def test_asof_alignment_uses_only_values_available_by_reference_time(self) -> None:
        value = document("align.asof(volume, price)")
        value["inputs"].append({
            "variable_name": "volume",
            "dataset": "bars",
            "field": "volume",
            "frequency": "1d",
        })
        compiled = FactorGraphCompiler.compile(value, self.capabilities)
        spec = FactorGraphSpec.from_dict(compiled)
        price_rows = timed_bars([
            ("2026-01-01T00:00:00+00:00", "2026-01-01T01:00:00+00:00", "2026-01-01T01:00:00+00:00", 100),
            ("2026-01-01T01:00:00+00:00", "2026-01-01T02:00:00+00:00", "2026-01-01T02:00:00+00:00", 101),
            ("2026-01-01T02:00:00+00:00", "2026-01-01T03:00:00+00:00", "2026-01-01T03:00:00+00:00", 102),
            ("2026-01-01T03:00:00+00:00", "2026-01-01T04:00:00+00:00", "2026-01-01T04:00:00+00:00", 103),
        ], field="close")
        volume_rows = timed_bars([
            ("2025-12-31T00:00:00+00:00", "2026-01-01T02:00:00+00:00", "2026-01-01T02:30:00+00:00", 10),
            ("2026-01-01T02:00:00+00:00", "2026-01-01T04:00:00+00:00", "2026-01-01T04:30:00+00:00", 20),
        ], field="volume")

        values = FactorEngineV4().compute(
            spec,
            {
                "price": {"A": price_rows},
                "volume": {"A": volume_rows},
            },
        )

        self.assertEqual([None, None, 10.0, 10.0], [item["value"] for item in values["A"]])
        self.assertEqual(
            "2026-01-01T04:00:00+00:00",
            values["A"][-1]["available_time"],
        )

    def test_conditional_graph_compiles_and_executes(self) -> None:
        value = document(
            "where(greater(price, time.mean(price, lookback)), "
            "price, time.mean(price, lookback))"
        )
        compiled = FactorGraphCompiler.compile(value, self.capabilities)
        spec = FactorGraphSpec.from_dict(compiled)
        values = FactorEngineV4().compute(spec, {"A": bars([1, 3, 2, 5])})

        self.assertEqual([None, 3.0, 2.5, 5.0], [item["value"] for item in values["A"]])
        self.assertEqual("TIME_SERIES", spec.dimension)

    def test_extended_time_and_universe_functions_execute(self) -> None:
        formulas = (
            "time.log_return(price, lookback)",
            "time.sum(price, lookback)",
            "time.median(price, lookback)",
            "time.min(price, lookback)",
            "time.max(price, lookback)",
            "time.variance(price, lookback)",
            "time.rank(price, lookback)",
            "time.zscore(price, lookback)",
            "universe.percentile(price)",
            "universe.demean(price)",
        )
        for source in formulas:
            with self.subTest(source=source):
                compiled = FactorGraphCompiler.compile(
                    document(source),
                    self.capabilities,
                )
                spec = FactorGraphSpec.from_dict(compiled)
                values = FactorEngineV4().compute(
                    spec,
                    {
                        "A": bars([1, 2, 4, 8]),
                        "B": bars([2, 3, 5, 9]),
                    },
                )
                self.assertIsNotNone(values["A"][-1]["value"])

    def test_conditional_predicates_and_logic_execute(self) -> None:
        source = (
            "where("
            "logical_and(is_finite(price), logical_not(is_null(price))), "
            "fill_null(price, price), "
            "price"
            ")"
        )
        spec = FactorGraphSpec.from_dict(
            FactorGraphCompiler.compile(document(source), self.capabilities)
        )
        values = FactorEngineV4().compute(spec, {"A": bars([1, 2, 3])})

        self.assertEqual([1.0, 2.0, 3.0], [item["value"] for item in values["A"]])

    def test_boolean_predicate_infers_boolean_output(self) -> None:
        result = FactorGraphCompiler.inspect(
            document("is_finite(time.mean(price, lookback))"),
            self.capabilities,
        )

        self.assertFalse([item for item in result["diagnostics"] if item["level"] == "ERROR"])
        self.assertEqual("BOOLEAN", result["compilation"]["factor_spec"]["output_type"])
        self.assertEqual("BOOLEAN", result["compilation"]["factor_spec"]["output_unit"])
        self.assertEqual("Boolean", result["compilation"]["output_display"]["type"])
        self.assertEqual("True / False", result["compilation"]["output_display"]["unit"])

    def test_formal_run_binds_each_input_to_its_manifest_frequency(self) -> None:
        value = document("align.asof(volume, price)")
        value["inputs"].append({
            "variable_name": "volume",
            "dataset": "bars",
            "field": "volume",
            "frequency": "1d",
        })
        spec = FactorGraphSpec.from_dict(
            FactorGraphCompiler.compile(value, self.capabilities)
        )
        hourly = bars([100, 101])
        daily = bars([10, 20])

        bound = FormalResearchRunExecutor._bind_factor_inputs(
            spec,
            [
                {"manifest_id": "hourly", "frequency": "1h", "fields": {"close"}, "rows": {"A": hourly}},
                {"manifest_id": "daily", "frequency": "1d", "fields": {"volume"}, "rows": {"A": daily}},
            ],
            {"A"},
        )

        self.assertEqual(hourly, bound["price"]["A"])
        self.assertEqual(daily, bound["volume"]["A"])

    def test_graph_engine_executes_nested_cross_sectional_rank(self) -> None:
        compiled = FactorGraphCompiler.compile(document(), self.capabilities)
        spec = FactorGraphSpec.from_dict(compiled)
        values = FactorEngineV4().compute(
            spec,
            {
                "A": bars([100, 100, 120, 130]),
                "B": bars([100, 100, 105, 110]),
            },
        )

        self.assertIsNone(values["A"][1]["value"])
        self.assertEqual(1.0, values["A"][2]["value"])
        self.assertEqual(0.0, values["B"][2]["value"])
        self.assertEqual("PASS", values["A"][2]["quality_status"])
        self.assertEqual(
            values["A"][2]["bar_end_time"],
            values["A"][2]["available_time"],
        )


if __name__ == "__main__":
    unittest.main()
