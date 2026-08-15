from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.data_platform import (
    DataPlatformStore,
    DatasetCatalogService,
    FactorInputCandidateResolver,
    Instrument,
    InstrumentRegistry,
    ResearchControlPlane,
    UniverseService,
    make_instrument_id,
)


class FactorInputCandidateResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = DataPlatformStore(Path(self.temp.name) / "metadata.db")
        self.project = ResearchControlPlane(self.store).create_project(
            title="Input Candidate Contract",
            objective="resolve Factor Inputs from the current Universe",
        )
        registry = InstrumentRegistry(self.store)
        self.instrument_ids = []
        for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
            instrument_id = make_instrument_id("crypto_spot", "BINANCE", symbol)
            registry.register(
                Instrument(
                    instrument_id=instrument_id,
                    asset_class="crypto_spot",
                    venue="BINANCE",
                    market_type="SPOT",
                    native_symbol=symbol,
                    base_asset=symbol.removesuffix("USDT"),
                    quote_asset="USDT",
                ),
                aliases=[("binance", symbol)],
            )
            self.instrument_ids.append(instrument_id)
        universe = UniverseService(self.store).create_definition(
            name="Three Spot Assets",
            version="1.0.0",
            universe_type="STATIC_LIST",
            parameters={"instrument_ids": self.instrument_ids},
            owner_project_id=self.project["project_id"],
            library_scope="PROJECT",
        )
        snapshot = UniverseService(self.store).resolve_snapshot(
            universe_definition_id=universe.universe_definition_id,
            as_of_time="2026-07-24T00:00:00+00:00",
        )
        UniverseService(self.store).set_research_ref(
            project_id=self.project["project_id"],
            universe_snapshot_id=snapshot.universe_snapshot_id,
        )
        DatasetCatalogService(self.store).upsert_catalog({
            "dataset_id": "binance:BTCUSDT:1h",
            "instrument_id": self.instrument_ids[0],
            "data_type": "bars",
            "frequency": "1h",
            "source": "BINANCE",
            "status": "READY",
            "quality_status": "PASS",
            "schema_version": "bars.v1",
            "storage_path": "unused",
            "fields": ["close", "volume", "quote_volume", "trade_count"],
        })

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_resolves_requestable_and_prepared_as_separate_states(self) -> None:
        resolver = FactorInputCandidateResolver(self.store, settings={})
        resolver.capabilities.can_prepare = (
            lambda instrument_id, data_type, frequency:
            data_type == "bars" and frequency in {"1h", "1d"}
        )
        result = resolver.resolve_project(self.project["project_id"])

        self.assertEqual(3, result["universe"]["member_count"])
        self.assertEqual("Crypto Spot", result["instrument_summary"]["asset_type"])
        self.assertEqual("BINANCE", result["instrument_summary"]["venue"])
        self.assertEqual("USDT", result["instrument_summary"]["quote_currency"])
        self.assertEqual(3, result["instrument_summary"]["provider_id_matches"])

        by_id = {item["candidate_id"]: item for item in result["input_candidates"]}
        close = by_id["bars.close:1h"]
        self.assertEqual(3, close["requestable_instrument_count"])
        self.assertEqual(1, close["prepared_instrument_count"])
        self.assertTrue(close["factor_selectable"])

        self.assertEqual(["bars"], [item["id"] for item in result["datasets"]])
        self.assertEqual(
            {"open", "high", "low", "close", "volume", "quote_volume", "trade_count"},
            {item["id"] for item in result["datasets"][0]["fields"]},
        )

        selected = resolver.assert_inputs_selectable(
            self.project["project_id"],
            [{"variable_name": "price", "dataset": "bars", "field": "close", "frequency": "1h"}],
        )
        self.assertEqual("bars.close:1h", selected["selected_inputs"][0]["candidate_id"])
        with self.assertRaisesRegex(ValueError, "FACTOR_INPUT_CANDIDATE_UNAVAILABLE"):
            resolver.assert_inputs_selectable(
                self.project["project_id"],
                [{"variable_name": "price", "dataset": "bars", "field": "close", "frequency": "3m"}],
            )

    def _project_with_instrument(self, instrument: Instrument, alias: tuple[str, str]) -> str:
        project = ResearchControlPlane(self.store).create_project(
            title=f"{instrument.asset_class} Inputs",
            objective="verify asset-driven candidate resolution",
        )
        InstrumentRegistry(self.store).register(instrument, aliases=[alias])
        universe = UniverseService(self.store).create_definition(
            name=f"{instrument.asset_class} Universe",
            version="1.0.0",
            universe_type="STATIC_LIST",
            parameters={"instrument_ids": [instrument.instrument_id]},
            owner_project_id=project["project_id"],
            library_scope="PROJECT",
        )
        snapshot = UniverseService(self.store).resolve_snapshot(
            universe_definition_id=universe.universe_definition_id,
            as_of_time="2026-07-24T00:00:00+00:00",
        )
        UniverseService(self.store).set_research_ref(
            project_id=project["project_id"],
            universe_snapshot_id=snapshot.universe_snapshot_id,
        )
        return project["project_id"]

    def test_polymarket_resolves_price_history_instead_of_fake_bars(self) -> None:
        instrument_id = "polymarket_binary:POLYMARKET:yes-token"
        project_id = self._project_with_instrument(
            Instrument(
                instrument_id=instrument_id,
                asset_class="polymarket_binary",
                venue="POLYMARKET",
                market_type="BINARY",
                native_symbol="yes-token",
                condition_id="condition-1",
                outcome_side="YES",
            ),
            ("polymarket", "yes-token"),
        )

        result = FactorInputCandidateResolver(
            self.store,
            settings={},
        ).resolve_project(project_id)

        self.assertEqual(["price_history"], [item["id"] for item in result["datasets"]])
        self.assertEqual(["price"], [item["id"] for item in result["datasets"][0]["fields"]])
        self.assertEqual(6, result["selectable_candidate_count"])
        self.assertTrue(all(
            item["dataset"] == "price_history" and item["field"] == "price"
            for item in result["input_candidates"]
        ))
        self.assertFalse(result["diagnostics"])

    def test_equity_exposes_daily_ohlcv_but_blocks_when_openbb_is_offline(self) -> None:
        project_id = self._project_with_instrument(
            Instrument(
                instrument_id="equity:XNAS:TSLA",
                asset_class="equity",
                venue="XNAS",
                market_type="EQUITY",
                native_symbol="TSLA",
                currency="USD",
            ),
            ("yfinance", "TSLA"),
        )
        resolver = FactorInputCandidateResolver(
            self.store,
            settings={
                "openbb_settings": {
                    "enabled": True,
                    "base_url": "http://127.0.0.1:1",
                    "allowed_providers": ["yfinance"],
                },
            },
            base_dir=Path(self.temp.name),
        )

        result = resolver.resolve_project(project_id)

        bars = result["datasets"][0]
        self.assertEqual({"open", "high", "low", "close", "volume"}, {
            item["id"] for item in bars["fields"]
        })
        self.assertEqual({"1d"}, {
            item["frequency"]
            for item in result["input_candidates"]
        })
        self.assertEqual(0, result["selectable_candidate_count"])
        self.assertEqual("UNAVAILABLE", bars["provider_status"]["status"])
        self.assertEqual("INPUT_PROVIDER_UNAVAILABLE", result["diagnostics"][0]["code"])
        with self.assertRaisesRegex(ValueError, "INPUT_PROVIDER_UNAVAILABLE"):
            resolver.assert_inputs_selectable(
                project_id,
                [{"variable_name": "price", "dataset": "bars", "field": "close", "frequency": "1d"}],
            )

    def test_crsp_all_catalog_rows_expose_pit_valuation_dividend_and_sec_inputs(self) -> None:
        project_id = self._project_with_instrument(
            Instrument(
                instrument_id="equity:CRSP:10001",
                asset_class="equity",
                venue="CRSP",
                market_type="EQUITY",
                native_symbol="10001",
                currency="USD",
            ),
            ("crsp:permno", "10001"),
        )
        catalog = DatasetCatalogService(self.store)
        for payload in (
            {
                "dataset_id": "crsp:all:valuation",
                "data_type": "equity_valuation_daily",
                "frequency": "1d",
                "fields": ["market_cap", "shares_outstanding"],
                "source": "CRSP",
            },
            {
                "dataset_id": "crsp:all:actions",
                "data_type": "corporate_actions",
                "frequency": "event",
                "fields": ["cash_dividend", "price_factor", "share_factor"],
                "source": "CRSP",
            },
            {
                "dataset_id": "sec:all:pit",
                "data_type": "fundamentals_pit",
                "frequency": "event",
                "fields": ["concept", "value", "available_time"],
                "source": "SEC",
            },
        ):
            catalog.upsert_catalog({
                **payload,
                "instrument_id": "equity:CRSP:ALL",
                "status": "READY",
                "quality_status": "PASS",
                "schema_version": "equity_pit.v1",
                "storage_path": "unused",
                "point_in_time_policy": "AS_OF",
            })

        result = FactorInputCandidateResolver(self.store, settings={}).resolve_project(project_id)
        by_id = {item["candidate_id"]: item for item in result["input_candidates"]}

        self.assertTrue(by_id["equity_valuation_daily.market_cap:1d"]["factor_selectable"])
        self.assertTrue(by_id["corporate_actions.cash_dividend:event"]["factor_selectable"])
        self.assertTrue(by_id["fundamentals.net_income_ttm:event"]["factor_selectable"])
        self.assertEqual(1, by_id["fundamentals.net_income_ttm:event"]["prepared_instrument_count"])


if __name__ == "__main__":
    unittest.main()
