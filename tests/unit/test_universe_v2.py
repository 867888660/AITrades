from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.data_platform import DataPlatformStore, SharedUniverseService
from services.data_platform.shared_universe_service import UniverseResolutionError
from services.data_platform.universe_v2 import (
    UNIVERSE_ENGINE_VERSION,
    UniverseFieldRegistry,
    UniverseMembershipEngine,
    UniverseV2Compiler,
    universe_v2_capabilities,
)


class UniverseV2CompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.compiler = UniverseV2Compiler()

    def test_dynamic_surface_normalizes_five_modules_and_compiles_requirements(self) -> None:
        compiled = self.compiler.compile({
            "type": "DYNAMIC",
            "base": "equity:CRSP:ALL",
            "filters": [
                ["security_type", "=", "COMMON_STOCK"],
                ["price", ">=", 5],
                ["market_cap", ">=", 300_000_000],
                ["adv20", ">=", 5_000_000],
            ],
            "rank": {"field": "market_cap", "order": "desc"},
            "select": {"method": "TOP_N", "value": 1500},
            "rebalance": "monthly",
        })

        definition = compiled["definition"]
        self.assertEqual("DYNAMIC", definition["type"])
        self.assertEqual("equity:CRSP:ALL", definition["base"]["ref"])
        self.assertEqual("GTE", definition["filters"][1]["operator"])
        self.assertEqual("price_usd", definition["filters"][1]["field"])
        self.assertEqual("market_cap_usd", definition["rank"]["field"])
        self.assertEqual("MONTHLY", definition["rebalance"])
        self.assertEqual(UNIVERSE_ENGINE_VERSION, compiled["engine_version"])
        requirements = {item["data_type"]: item for item in compiled["requirements"]}
        self.assertEqual(["close", "volume"], requirements["bars"]["fields"])
        self.assertEqual(20, requirements["bars"]["warmup_bars"])
        self.assertEqual(["market_cap"], requirements["equity_valuation_daily"]["fields"])
        self.assertTrue(all("time_semantics" not in item for item in requirements.values()))
        self.assertEqual("INSTRUMENT_ID_ASC", compiled["policies"]["tie_break"])

    def test_invalid_operator_and_unregistered_field_are_rejected(self) -> None:
        base = {"type": "DYNAMIC", "base": "equity:CRSP:ALL"}
        with self.assertRaisesRegex(ValueError, "not supported"):
            self.compiler.compile({**base, "filters": [["security_type", ">", "COMMON_STOCK"]]})
        with self.assertRaisesRegex(ValueError, "not registered"):
            self.compiler.compile({**base, "filters": [["raw_sql", "=", "x"]]})

    def test_public_capabilities_expose_three_types_and_versioned_fields(self) -> None:
        capabilities = universe_v2_capabilities()
        self.assertEqual(["STATIC", "DYNAMIC", "COMPOSITE"], capabilities["product_types"])
        self.assertEqual(
            ["BASE", "FILTER", "RANK", "SELECT", "REBALANCE"],
            capabilities["dynamic_modules"],
        )
        market_cap = capabilities["dynamic_point_in_time_filters"][0]
        self.assertEqual("market_cap_usd", market_cap["field"])
        self.assertTrue(market_cap["pit_safe"])
        self.assertEqual("CATALOG_DEPENDENT", market_cap["coverage"])
        self.assertEqual(
            "equity_valuation_daily",
            UniverseFieldRegistry.default().require("market_cap").source_data_type,
        )
        self.assertEqual(
            {
                "market_cap_usd", "pe_ttm", "pb_mrq", "roe_ttm", "fcf_yield_ttm",
            },
            {item["field"] for item in capabilities["dynamic_point_in_time_filters"]},
        )
        for field in ("pe_ttm", "pb_mrq", "roe_ttm", "fcf_yield_ttm"):
            self.assertEqual("FORMAL_PIPELINE", capabilities["field_execution_status"][field])

    def test_fundamental_ratio_compiles_physical_pit_dependencies(self) -> None:
        compiled = self.compiler.compile({
            "type": "DYNAMIC",
            "base": "equity:CRSP:ALL",
            "filters": [
                ["pe_ttm", "<=", 25],
                ["roe_ttm", ">=", 0.15],
                ["fcf_yield_ttm", ">=", 0.03],
            ],
        })
        requirements = {item["data_type"]: item for item in compiled["requirements"]}
        self.assertEqual(["market_cap"], requirements["equity_valuation_daily"]["fields"])
        self.assertEqual(
            ["capex_ttm", "equity", "net_income_ttm", "operating_cash_flow_ttm"],
            requirements["fundamentals_pit"]["fields"],
        )
        self.assertEqual(
            "FILED_OR_ACCEPTED_AT",
            requirements["fundamentals_pit"]["point_in_time_policy"],
        )


class UniverseMembershipEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.compiler = UniverseV2Compiler()
        self.engine = UniverseMembershipEngine()

    @staticmethod
    def _row(available_time: str, value: float) -> dict[str, object]:
        return {"event_time": available_time, "available_time": available_time, "value": value}

    def test_materialization_uses_only_available_values_and_excludes_missing(self) -> None:
        compiled = self.compiler.compile({
            "type": "DYNAMIC",
            "base": "equity:CRSP:ALL",
            "filters": [["price_usd", ">=", 5], ["market_cap_usd", ">=", 100_000_000]],
            "rank": {"field": "market_cap_usd", "order": "DESC"},
            "select": {"method": "TOP_N", "value": 2},
            "rebalance": "MONTHLY",
        })
        rows = {
            "price_usd": {
                "A": [self._row("2026-01-30T20:00:00Z", 6)],
                "B": [self._row("2026-01-30T20:00:00Z", 4)],
                # C has no price and must be excluded rather than zero-filled.
            },
            "market_cap_usd": {
                "A": [
                    self._row("2026-01-30T20:00:00Z", 200_000),
                    # This row is after the decision and must not leak backward.
                    self._row("2026-02-01T20:00:00Z", 50_000),
                ],
                "B": [self._row("2026-01-30T20:00:00Z", 300_000)],
                "C": [self._row("2026-01-30T20:00:00Z", 400_000)],
            },
        }
        result = self.engine.materialize(
            compiled,
            base_membership=["A", "B", "C"],
            field_rows=rows,
            schedule=[{
                "decision_time": "2026-01-31T21:00:00Z",
                "effective_time": "2026-02-02T14:30:00Z",
            }],
            manifest_ids=["manifest_bars", "manifest_valuation"],
            end_time="2026-03-01T00:00:00Z",
        )

        self.assertEqual(["A"], result["actual_instrument_ids"])
        self.assertEqual(["A"], result["timeline"][0]["instrument_ids"])
        self.assertEqual(1, result["timeline"][0]["missing_by_field"]["price_usd"])
        self.assertEqual(
            "2026-03-01T00:00:00+00:00",
            result["membership_segments"]["A"][0]["eligible_to_exclusive"],
        )

    def test_rank_tie_break_and_buffer_are_deterministic(self) -> None:
        compiled = self.compiler.compile({
            "type": "DYNAMIC", "base": "equity:CRSP:ALL",
            "rank": {"field": "market_cap_usd", "order": "DESC"},
            "select": {
                "method": "TOP_N", "value": 2,
                "buffer": {"entry": 1, "exit": 3},
            },
            "rebalance": "MONTHLY",
        })
        rows = {"market_cap_usd": {
            "A": [self._row("2026-01-01T00:00:00Z", 300), self._row("2026-02-01T00:00:00Z", 300)],
            "B": [self._row("2026-01-01T00:00:00Z", 200), self._row("2026-02-01T00:00:00Z", 100)],
            "C": [self._row("2026-01-01T00:00:00Z", 100), self._row("2026-02-01T00:00:00Z", 200)],
        }}
        result = self.engine.materialize(
            compiled,
            base_membership=["C", "B", "A"],
            field_rows=rows,
            schedule=[
                "2026-01-31T00:00:00Z",
                "2026-02-28T00:00:00Z",
            ],
            manifest_ids=["manifest_valuation"],
            end_time="2026-03-31T00:00:00Z",
        )
        self.assertEqual(["A", "B"], result["timeline"][0]["instrument_ids"])
        # C rose to rank 2, but entry=1 and incumbent B remains inside exit=3.
        self.assertEqual(["A", "B"], result["timeline"][1]["instrument_ids"])
        replay = self.engine.materialize(
            compiled,
            base_membership=["A", "B", "C"],
            field_rows=rows,
            schedule=["2026-01-31T00:00:00Z", "2026-02-28T00:00:00Z"],
            manifest_ids=["manifest_valuation"],
            end_time="2026-03-31T00:00:00Z",
        )
        self.assertEqual(result["fingerprint"], replay["fingerprint"])

    def test_dynamic_membership_refuses_unfrozen_inputs(self) -> None:
        compiled = self.compiler.compile({
            "type": "DYNAMIC", "base": "equity:CRSP:ALL",
            "filters": [["market_cap_usd", ">=", 1]],
        })
        with self.assertRaisesRegex(ValueError, "frozen manifest_ids"):
            self.engine.materialize(
                compiled, base_membership=["A"], field_rows={},
                schedule=["2026-01-31T00:00:00Z"], manifest_ids=[],
            )


class SharedUniverseV2CompatibilityTests(unittest.TestCase):
    def test_dynamic_authoring_previews_but_cannot_masquerade_as_static_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service = SharedUniverseService(DataPlatformStore(Path(temp) / "metadata.db"))
            definition = {
                "name": "Liquid US Stocks",
                "type": "DYNAMIC",
                "base": "equity:CRSP:ALL",
                "filters": [["market_cap_usd", ">=", 300_000_000]],
                "rank": {"field": "market_cap_usd", "order": "DESC"},
                "select": {"method": "TOP_N", "value": 1500},
                "rebalance": "MONTHLY",
            }
            preview = service.preview(definition)
            self.assertEqual("REQUIRES_FROZEN_DATA", preview["status"])
            self.assertEqual("dynamic_set", preview["definition"]["type"])
            self.assertEqual(
                UNIVERSE_ENGINE_VERSION,
                preview["metadata"]["compiled_contract"]["engine_version"],
            )
            with self.assertRaises(UniverseResolutionError) as raised:
                service.create(definition)
            self.assertEqual(
                "DYNAMIC_UNIVERSE_REQUIRES_FROZEN_EVALUATION",
                raised.exception.code,
            )


if __name__ == "__main__":
    unittest.main()
