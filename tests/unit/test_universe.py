from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.data_platform import DataPlatformStore, EquitySecurityMasterService, UniverseService
from services.data_platform.universe_service import UniverseMembershipIndex


class FakeFrozenManifest:
    def __init__(self, manifest_id: str, rows: dict[str, list[dict[str, object]]]):
        self.manifest_id = manifest_id
        self._rows = rows

    def read_bars_by_instrument(self, *, as_of: str | None = None) -> dict[str, list[dict[str, object]]]:
        return self._rows


class UniverseServiceTest(unittest.TestCase):
    @staticmethod
    def _fundamental_rows(*, include_capex: bool = True) -> list[dict[str, object]]:
        quarters = [
            ("2024-01-01", "2024-03-31", "2024-05-01T20:00:00+00:00"),
            ("2024-04-01", "2024-06-30", "2024-08-01T20:00:00+00:00"),
            ("2024-07-01", "2024-09-30", "2024-11-01T20:00:00+00:00"),
            ("2024-10-01", "2024-12-31", "2025-02-01T20:00:00+00:00"),
        ]
        rows: list[dict[str, object]] = []
        concepts = [
            ("NetIncomeLoss", 10_000_000.0),
            ("NetCashProvidedByUsedInOperatingActivities", 15_000_000.0),
        ]
        if include_capex:
            concepts.append(("PaymentsToAcquirePropertyPlantAndEquipment", 5_000_000.0))
        for index, (period_start, period_end, available_time) in enumerate(quarters, start=1):
            for concept, value in concepts:
                rows.append({
                    "concept": concept,
                    "value": value,
                    "unit": "USD",
                    "period_start": period_start,
                    "period_end": period_end,
                    "event_time": period_end,
                    "available_time": available_time,
                    "form": "10-Q",
                    "accession_number": f"q{index}-{concept}",
                })
        rows.append({
            "concept": "StockholdersEquity",
            "value": 200_000_000.0,
            "unit": "USD",
            "period_end": "2024-12-31",
            "event_time": "2024-12-31",
            "available_time": "2025-02-01T20:00:00+00:00",
            "form": "10-Q",
            "accession_number": "q4-equity",
        })
        # This later filing is outside the Snapshot cutoff and must not alter
        # the historical result.
        rows.append({
            "concept": "StockholdersEquity",
            "value": 1.0,
            "unit": "USD",
            "period_end": "2025-03-31",
            "event_time": "2025-03-31",
            "available_time": "2025-04-15T20:00:00+00:00",
            "form": "10-Q",
            "accession_number": "future-equity",
        })
        return rows

    def test_market_cap_filter_materializes_pit_membership_from_frozen_valuation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = DataPlatformStore(Path(temp) / "metadata.db")
            EquitySecurityMasterService(store).upsert({
                "permno": 10001,
                "security_type": "EQTY",
                "share_type": "COM",
                "primary_exchange": "N",
                "valid_from": "2025-01-01",
                "valid_to": "2025-01-31",
            })
            service = UniverseService(store)
            definition = service.create_definition(
                name="dynamic-market-cap",
                version="1.0.0",
                universe_type="HISTORICAL_EQUITY_PIT",
                parameters={
                    "history_start": "2025-01-01",
                    "history_end": "2025-01-31",
                    "point_in_time_filters": [{
                        "field": "market_cap_usd",
                        "minimum": 100_000_000,
                        "maximum": 500_000_000,
                    }],
                },
            )
            snapshot = service.resolve_snapshot(
                universe_definition_id=definition.universe_definition_id,
                as_of_time="2025-01-31T23:59:59+00:00",
            )
            effective = service.materialize_dynamic_membership(snapshot, [{
                "manifest_id": "manifest_valuation",
                "data_type": "equity_valuation_daily",
                "rows": {"equity:CRSP:10001": [
                    {
                        "event_time": "2025-01-02T00:00:00+00:00",
                        "available_time": "2025-01-02T22:00:00+00:00",
                        "market_cap": 50_000,
                    },
                    {
                        "event_time": "2025-01-03T00:00:00+00:00",
                        "available_time": "2025-01-03T22:00:00+00:00",
                        "market_cap": 200_000,
                    },
                    {
                        "event_time": "2025-01-05T00:00:00+00:00",
                        "available_time": "2025-01-05T22:00:00+00:00",
                        "market_cap": 600_000,
                    },
                ]},
            }])

            index = UniverseMembershipIndex(effective)
            self.assertFalse(index.contains("equity:CRSP:10001", "2025-01-03T21:59:59+00:00"))
            self.assertTrue(index.contains("equity:CRSP:10001", "2025-01-04T20:00:00+00:00"))
            self.assertFalse(index.contains("equity:CRSP:10001", "2025-01-06T20:00:00+00:00"))
            self.assertEqual(
                ["market_cap"],
                service.data_requirements(definition)[0]["fields"],
            )
            self.assertEqual(
                "SOURCE_AVAILABLE_TIME",
                service.data_requirements(definition)[0]["time_semantics"],
            )
            self.assertEqual(
                ["manifest_valuation"],
                effective.selection_inputs["dynamic_membership_source_manifest_ids"],
            )

    def test_fundamental_filters_are_pit_bound_and_missing_values_are_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = DataPlatformStore(Path(temp) / "metadata.db")
            master = EquitySecurityMasterService(store)
            for permno in (10001, 10002):
                master.upsert({
                    "permno": permno,
                    "security_type": "EQTY",
                    "share_type": "COM",
                    "primary_exchange": "N",
                    "valid_from": "2024-01-01",
                    "valid_to": "2025-03-31",
                })
            service = UniverseService(store)
            definition = service.create_definition(
                name="broad-good-fundamentals",
                version="1.0.0",
                universe_type="HISTORICAL_EQUITY_PIT",
                parameters={
                    "history_start": "2024-01-01",
                    "history_end": "2025-03-31",
                    "point_in_time_filters": [
                        {"field": "market_cap_usd", "minimum": 300_000_000},
                        {"field": "roe_ttm", "minimum": 0.15},
                        {"field": "pe_ttm", "maximum": 25},
                        {"field": "pb_mrq", "maximum": 3},
                        {"field": "fcf_yield_ttm", "minimum": 0.05},
                    ],
                },
            )
            requirements = {item["data_type"]: item for item in service.data_requirements(definition)}
            self.assertEqual(["market_cap"], requirements["equity_valuation_daily"]["fields"])
            self.assertEqual(
                ["capex_ttm", "equity", "net_income_ttm", "operating_cash_flow_ttm"],
                requirements["fundamentals_pit"]["fields"],
            )
            snapshot = service.resolve_snapshot(
                universe_definition_id=definition.universe_definition_id,
                as_of_time="2025-03-31T23:59:59+00:00",
            )
            effective = service.materialize_dynamic_membership(snapshot, [
                {
                    "manifest_id": "manifest_valuation",
                    "data_type": "equity_valuation_daily",
                    "rows": {
                        "equity:CRSP:10001": [{
                            "event_time": "2025-01-02T00:00:00+00:00",
                            "available_time": "2025-01-02T22:00:00+00:00",
                            "market_cap": 400_000,
                        }],
                        "equity:CRSP:10002": [{
                            "event_time": "2025-01-02T00:00:00+00:00",
                            "available_time": "2025-01-02T22:00:00+00:00",
                            "market_cap": 400_000,
                        }],
                    },
                },
                {
                    "manifest_id": "manifest_fundamentals",
                    "data_type": "fundamentals_pit",
                    "rows": {
                        "equity:CRSP:10001": self._fundamental_rows(),
                        "equity:CRSP:10002": self._fundamental_rows(include_capex=False),
                    },
                },
            ])
            index = UniverseMembershipIndex(effective)
            self.assertFalse(index.contains("equity:CRSP:10001", "2025-02-01T19:59:59+00:00"))
            self.assertTrue(index.contains("equity:CRSP:10001", "2025-02-02T00:00:00+00:00"))
            self.assertFalse(index.contains("equity:CRSP:10002", "2025-02-02T00:00:00+00:00"))
            self.assertEqual(
                ["manifest_fundamentals", "manifest_valuation"],
                effective.selection_inputs["dynamic_membership_source_manifest_ids"],
            )

    def test_historical_equity_snapshot_preserves_listing_and_delisting_intervals(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = DataPlatformStore(Path(temp) / "metadata.db")
            master = EquitySecurityMasterService(store)
            master.upsert({
                "permno": 10001,
                "security_type": "EQTY",
                "share_type": "COM",
                "primary_exchange": "N",
                "valid_from": "2000-01-01",
                "valid_to": "2005-06-30",
            })
            master.upsert({
                "permno": 10002,
                "security_type": "EQTY",
                "share_type": "NS",
                "primary_exchange": "Q",
                "valid_from": "2005-07-01",
                "valid_to": "",
            })
            service = UniverseService(store)
            definition = service.create_definition(
                name="crsp-history",
                version="1.0.0",
                universe_type="HISTORICAL_EQUITY_PIT",
                parameters={
                    "history_start": "2005-01-01",
                    "history_end": "2005-12-31",
                    "minimum_listing_age_days": 30,
                },
            )

            snapshot = service.resolve_snapshot(
                universe_definition_id=definition.universe_definition_id,
                as_of_time="2005-12-31T23:59:59+00:00",
            )

            self.assertEqual(
                ("equity:CRSP:10001", "equity:CRSP:10002"),
                snapshot.actual_instrument_ids,
            )
            intervals = snapshot.selection_inputs["membership_intervals"]
            self.assertEqual("2005-06-30", intervals["equity:CRSP:10001"]["eligible_to"])
            self.assertEqual("2005-07-31", intervals["equity:CRSP:10002"]["eligible_from"])
            self.assertTrue(snapshot.selection_inputs["dynamic_membership"])

    def test_static_snapshot_is_idempotent_and_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service = UniverseService(DataPlatformStore(Path(temp) / "metadata.db"))
            definition = service.create_definition(
                name="static-five",
                version="1.0.0",
                universe_type="STATIC_LIST",
                parameters={"instrument_ids": ["B", "A", "A"]},
            )
            repeat = service.create_definition(
                name="static-five",
                version="1.0.0",
                universe_type="STATIC_LIST",
                parameters={"instrument_ids": ["A", "B"]},
            )
            self.assertEqual(definition.universe_definition_id, repeat.universe_definition_id)
            snapshot = service.resolve_snapshot(
                universe_definition_id=definition.universe_definition_id,
                as_of_time="2026-01-01T00:00:00+00:00",
            )
            same = service.resolve_snapshot(
                universe_definition_id=definition.universe_definition_id,
                as_of_time="2026-01-01T00:00:00+00:00",
            )
            self.assertEqual(snapshot.universe_snapshot_id, same.universe_snapshot_id)
            self.assertEqual(("A", "B"), snapshot.actual_instrument_ids)
            with self.assertRaisesRegex(ValueError, "immutable"):
                service.create_definition(
                    name="static-five",
                    version="1.0.0",
                    universe_type="STATIC_LIST",
                    parameters={"instrument_ids": ["A", "C"]},
                )

    def test_top_n_turnover_uses_only_available_lookback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service = UniverseService(DataPlatformStore(Path(temp) / "metadata.db"))
            definition = service.create_definition(
                name="turnover-top-two",
                version="1.0.0",
                universe_type="TOP_N_BY_TURNOVER",
                parameters={
                    "candidate_instrument_ids": ["A", "B", "C"],
                    "top_n": 2,
                    "lookback_bars": 2,
                },
            )
            rows = {
                "A": [
                    {"available_time": "2026-01-01T00:00:00+00:00", "turnover": 10},
                    {"available_time": "2026-01-01T01:00:00+00:00", "turnover": 20},
                    {"available_time": "2026-01-01T03:00:00+00:00", "turnover": 1000},
                ],
                "B": [
                    {"available_time": "2026-01-01T00:00:00+00:00", "turnover": 30},
                    {"available_time": "2026-01-01T01:00:00+00:00", "turnover": 30},
                ],
                "C": [
                    {"available_time": "2026-01-01T00:00:00+00:00", "turnover": 40},
                    {"available_time": "2026-01-01T01:00:00+00:00", "turnover": 40},
                ],
            }
            snapshot = service.resolve_snapshot(
                universe_definition_id=definition.universe_definition_id,
                as_of_time="2026-01-01T02:00:00+00:00",
                manifests=[FakeFrozenManifest("manifest_test", rows)],
            )
            self.assertEqual(("B", "C"), snapshot.actual_instrument_ids)
            self.assertEqual(15.0, snapshot.selection_inputs["turnover_average"]["A"])


if __name__ == "__main__":
    unittest.main()
