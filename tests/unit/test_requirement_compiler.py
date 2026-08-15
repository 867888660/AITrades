from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from services.data_platform import DataPlatformStore, DatasetCatalogService, FactorSpec, RequirementCompiler


class RequirementCompilerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = DataPlatformStore(Path(self.temp.name) / "metadata.db")
        self.compiler = RequirementCompiler(self.store)
        self.context = {
            "instrument_ids": ["crypto_spot:BINANCE:BTCUSDT", "crypto_spot:BINANCE:ETHUSDT"],
            "data_type": "bars",
            "frequency": "1h",
            "history_start": "2026-01-01T00:00:00+00:00",
            "history_end": "2026-01-31T23:00:00+00:00",
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_compiles_merges_and_attributes_system_and_manual_dependencies(self) -> None:
        result = self.compiler.compile(
            project_id="project_a",
            factor_specs=[
                FactorSpec(name="momentum", version="1", operator="pct_change", input_field="close", window=20, frequency="1h"),
                FactorSpec(name="volatility", version="1", operator="rolling_std", input_field="volume", window=10, frequency="1h"),
            ],
            manual_requirements=[{"id": "keep_quote_volume", "fields": ["quote_volume"]}],
            context=self.context,
        )
        self.assertEqual(1, len(result.requirements))
        system = result.requirements[0]
        self.assertEqual({"close", "volume", "quote_volume"}, set(system.fields))
        self.assertEqual(20, system.lookback_value)
        self.assertEqual(3, len(result.dependency_links))
        self.assertEqual({"FACTOR_SPEC", "MANUAL"}, {item.origin_type for item in result.dependency_links})
        with self.store.connection() as conn:
            rows = conn.execute(
                "SELECT requirement_id, origin_kind, removable FROM requirement_set_items WHERE requirement_set_id = ?",
                (result.requirement_set_id,),
            ).fetchall()
        flags = {str(row[0]): (str(row[1]), int(row[2])) for row in rows}
        self.assertEqual(("SYSTEM", 0), flags[system.requirement_id])

    def test_same_compile_is_idempotent_and_changed_source_creates_new_version(self) -> None:
        first = self.compiler.compile(
            project_id="project_a",
            factor_specs=[FactorSpec(name="momentum", version="1", operator="pct_change", window=20)],
            context=self.context,
        )
        same = self.compiler.compile(
            project_id="project_a",
            factor_specs=[FactorSpec(name="momentum", version="1", operator="pct_change", window=20)],
            context=self.context,
        )
        second = self.compiler.compile(
            project_id="project_a",
            factor_specs=[FactorSpec(name="momentum", version="2", operator="pct_change", window=60)],
            context=self.context,
        )
        self.assertEqual(first.requirement_set_id, same.requirement_set_id)
        self.assertEqual(1, first.version)
        self.assertEqual(2, second.version)
        self.assertEqual("SUPERSEDED", self.compiler.get(first.requirement_set_id).status)
        self.assertEqual(second.requirement_set_id, self.compiler.get(first.requirement_set_id).superseded_by_id)

    def test_recompile_reactivates_matching_superseded_requirement_set(self) -> None:
        first = self.compiler.compile(
            project_id="project_a",
            factor_specs=[FactorSpec(name="momentum", version="1", operator="pct_change", window=20)],
            context=self.context,
        )
        second = self.compiler.compile(
            project_id="project_a",
            factor_specs=[FactorSpec(name="momentum", version="2", operator="pct_change", window=60)],
            context=self.context,
        )

        reactivated = self.compiler.compile(
            project_id="project_a",
            factor_specs=[FactorSpec(name="momentum", version="1", operator="pct_change", window=20)],
            context=self.context,
        )

        self.assertEqual(first.requirement_set_id, reactivated.requirement_set_id)
        self.assertEqual("RESOLVED", reactivated.status)
        self.assertIsNone(reactivated.superseded_by_id)
        refreshed_second = self.compiler.get(second.requirement_set_id)
        self.assertEqual("SUPERSEDED", refreshed_second.status)
        self.assertEqual(first.requirement_set_id, refreshed_second.superseded_by_id)

    def test_v4_graph_compiles_each_referenced_input_and_its_history(self) -> None:
        result = self.compiler.compile(
            project_id="project_v4",
            factor_specs=[{
                "name": "liquidity_adjusted_momentum",
                "version": "1",
                "engine_version": "factor-engine.v4",
                "inputs": [
                    {"variable_name": "price", "field": "close", "frequency": "1h"},
                    {"variable_name": "volume", "field": "quote_volume", "frequency": "1h"},
                ],
                "required_history": {"price": 21, "volume": 1},
                "minimum_observations": 21,
            }],
            context=self.context,
        )

        self.assertEqual(1, len(result.requirements))
        self.assertEqual({"close", "quote_volume"}, set(result.requirements[0].fields))
        self.assertEqual(21, result.requirements[0].lookback_value)
        self.assertEqual("2025-12-31T04:00:00+00:00", result.requirements[0].history_start)
        self.assertEqual(2, len(result.dependency_links))

    def test_v4_mixed_frequency_inputs_remain_separate_requirements(self) -> None:
        result = self.compiler.compile(
            project_id="project_v4_mixed",
            factor_specs=[{
                "name": "aligned_liquidity",
                "version": "1",
                "engine_version": "factor-engine.v4",
                "inputs": [
                    {"variable_name": "price", "dataset": "bars", "field": "close", "frequency": "1h"},
                    {"variable_name": "volume", "dataset": "bars", "field": "quote_volume", "frequency": "1d"},
                ],
                "required_history": {"price": 21, "volume": 5},
                "minimum_observations": 21,
            }],
            context=self.context,
        )

        self.assertEqual(2, len(result.requirements))
        by_frequency = {item.frequency: item for item in result.requirements}
        self.assertEqual(("close",), by_frequency["1h"].fields)
        self.assertEqual(21, by_frequency["1h"].lookback_value)
        self.assertEqual("2025-12-31T04:00:00+00:00", by_frequency["1h"].history_start)
        self.assertEqual(("quote_volume",), by_frequency["1d"].fields)
        self.assertEqual(5, by_frequency["1d"].lookback_value)
        self.assertEqual("2025-12-28T00:00:00+00:00", by_frequency["1d"].history_start)

    def test_alpha_is_part_of_the_same_set_and_attributes_factor_data(self) -> None:
        factor = {
            "definition_id": "factor_momentum",
            "name": "momentum",
            "version": "1.0.0",
            "inputs": [{
                "variable_name": "price",
                "dataset": "bars",
                "field": "close",
                "frequency": "1h",
            }],
            "required_history": {"price": 20},
        }
        alpha = {
            "definition_id": "alpha_ranked_momentum",
            "name": "ranked_momentum",
            "version": "1.0.0",
            "components": [{
                "factor_definition_id": "factor_momentum",
                "factor_version": "1.0.0",
                "weight": 1.0,
            }],
        }

        result = self.compiler.compile(
            project_id="project_alpha",
            factor_specs=[factor],
            alpha_specs=[alpha],
            context=self.context,
        )

        self.assertEqual(1, len(result.requirements))
        self.assertEqual(
            {"FACTOR_SPEC", "ALPHA_SPEC"},
            {link.origin_type for link in result.dependency_links},
        )
        self.assertTrue(any(
            source["origin_type"] == "ALPHA_SPEC"
            for source in result.source_specs
        ))

    def test_concurrent_compile_never_exposes_partial_set(self) -> None:
        def run(_: int):
            return RequirementCompiler(self.store).compile(
                project_id="project_concurrent",
                factor_specs=[FactorSpec(name="momentum", version="1", operator="pct_change", window=20)],
                context=self.context,
            )

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(run, range(8)))
        self.assertEqual(1, len({item.requirement_set_id for item in results}))
        self.assertTrue(all(len(item.requirements) == 1 and len(item.dependency_links) == 1 for item in results))

    def test_incompatible_adjustment_semantics_do_not_merge(self) -> None:
        result = self.compiler.compile(
            project_id="project_a",
            manual_requirements=[
                {"id": "raw", "fields": ["close"], "adjustment": "NONE"},
                {"id": "adjusted", "fields": ["close"], "adjustment": "SPLITS_AND_DIVIDENDS"},
            ],
            context=self.context,
        )
        self.assertEqual(2, len(result.requirements))
        self.assertEqual({"NONE", "SPLITS_AND_DIVIDENDS"}, {item.adjustment for item in result.requirements})

    def test_coverage_reports_missing_and_ready_catalog(self) -> None:
        result = self.compiler.compile(
            project_id="project_a",
            manual_requirements=[{"id": "bars", "fields": ["close"]}],
            context={**self.context, "instrument_ids": ["crypto_spot:BINANCE:BTCUSDT"]},
        )
        missing = self.compiler.coverage(result.requirement_set_id)
        self.assertEqual("GAPS_FOUND", missing["status"])
        self.assertEqual(["DATASET_MISSING"], missing["checks"][0]["reasons"])
        DatasetCatalogService(self.store).upsert_catalog({
            "dataset_id": "binance:BTCUSDT:1h", "instrument_id": "crypto_spot:BINANCE:BTCUSDT",
            "data_type": "bars", "frequency": "1h", "source": "BINANCE", "status": "READY",
            "quality_status": "PASS", "schema_version": "bars.v1", "storage_path": "unused",
            "start_time": "2025-12-31T00:00:00+00:00", "end_time": "2026-02-01T00:00:00+00:00",
            "last_complete_time": "2026-02-01T00:00:00+00:00", "row_count": 1000, "gap_count": 0,
        })
        covered = self.compiler.coverage(result.requirement_set_id)
        self.assertEqual("SATISFIED", covered["status"])
        self.assertTrue(covered["checks"][0]["satisfied"])

    def test_coverage_accepts_crsp_daily_observation_day_without_relaxing_intraday(self) -> None:
        daily = self.compiler.compile(
            project_id="project_crsp_daily_boundary",
            manual_requirements=[{"id": "daily", "fields": ["close"]}],
            context={
                "instrument_ids": ["equity:CRSP:10001"],
                "data_type": "bars",
                "frequency": "1d",
                "history_start": "2000-01-01T00:00:00+00:00",
                "history_end": "2025-12-31T23:59:59+00:00",
            },
        )
        DatasetCatalogService(self.store).upsert_catalog({
            "dataset_id": "crsp:ciz:bars",
            "instrument_id": "equity:CRSP:ALL",
            "data_type": "bars",
            "frequency": "1d",
            "source": "CRSP/CIZ",
            "status": "READY",
            "quality_status": "PASS",
            "schema_version": "bars_daily.v2",
            "storage_path": "unused",
            "start_time": "1925-12-31T00:00:00+00:00",
            "end_time": "2025-12-31T00:00:00+00:00",
            "last_complete_time": "2026-01-01T00:00:00+00:00",
            "row_count": 1000,
            "gap_count": 0,
        })
        self.assertEqual(
            "SATISFIED",
            self.compiler.coverage(daily.requirement_set_id)["status"],
        )

        intraday = self.compiler.compile(
            project_id="project_intraday_boundary",
            manual_requirements=[{"id": "hourly", "fields": ["close"]}],
            context={
                "instrument_ids": ["crypto_spot:BINANCE:BTCUSDT"],
                "data_type": "bars",
                "frequency": "1h",
                "history_start": "2025-12-31T00:00:00+00:00",
                "history_end": "2025-12-31T23:00:00+00:00",
            },
        )
        DatasetCatalogService(self.store).upsert_catalog({
            "dataset_id": "binance:BTCUSDT:partial-day",
            "instrument_id": "crypto_spot:BINANCE:BTCUSDT",
            "data_type": "bars",
            "frequency": "1h",
            "source": "BINANCE",
            "status": "READY",
            "quality_status": "PASS",
            "schema_version": "bars.v1",
            "storage_path": "unused",
            "start_time": "2025-12-31T00:00:00+00:00",
            "end_time": "2025-12-31T01:00:00+00:00",
            "last_complete_time": "2025-12-31T01:00:00+00:00",
            "row_count": 1,
            "gap_count": 0,
        })
        hourly_coverage = self.compiler.coverage(intraday.requirement_set_id)
        self.assertEqual("GAPS_FOUND", hourly_coverage["status"])
        self.assertIn("END_NOT_COVERED", hourly_coverage["checks"][0]["reasons"])


if __name__ == "__main__":
    unittest.main()
