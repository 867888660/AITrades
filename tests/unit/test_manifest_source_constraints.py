from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from services.data_platform import (
    CanonicalBarsCommitter,
    DataPlatformStore,
    DeterministicManifestResolver,
    RequirementCompiler,
    UniverseService,
)
from services.data_platform.canonical_dataset import CanonicalDatasetCommitter


class ManifestSourceConstraintTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.root = root
        self.store = DataPlatformStore(root / "metadata.db")
        self.instrument_id = "equity:XNAS:TSLA"
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for source in ("BINANCE", "YFINANCE"):
            rows = [{
                "instrument_id": self.instrument_id,
                "frequency": "1d",
                "bar_start_time": (start + timedelta(days=index)).isoformat(),
                "bar_end_time": (start + timedelta(days=index + 1)).isoformat(),
                "available_time": (start + timedelta(days=index + 1)).isoformat(),
                "ingested_at": "2026-01-04T00:00:00+00:00",
                "open": 100 + index,
                "high": 101 + index,
                "low": 99 + index,
                "close": 100 + index,
                "volume": 1000,
                "turnover": 100000,
                "trade_count": 10,
                "bar_status": "COMPLETE",
                "source": source,
                "source_version": "1",
                "quality_status": "PASS",
            } for index in range(3)]
            CanonicalBarsCommitter(self.store, root / source.lower()).commit(
                dataset_id=f"{source.lower()}:TSLA:1d",
                instrument_id=self.instrument_id,
                asset_class="equity",
                venue="XNAS",
                frequency="1d",
                source=source,
                source_version="1",
                rows=rows,
            )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_requirement_source_policy_is_immutable_and_enforced(self) -> None:
        requirement_set = RequirementCompiler(self.store).compile(
            project_id="source_policy_project",
            manual_requirements=[{
                "id": "tsla_yfinance",
                "fields": ["close"],
                "source_selection_policy": {
                    "mode": "FIXED",
                    "allowed_sources": ["yfinance"],
                    "preferred_sources": ["yfinance"],
                },
            }],
            context={
                "instrument_ids": [self.instrument_id],
                "data_type": "bars",
                "frequency": "1d",
                "history_start": "2026-01-01T00:00:00+00:00",
                "history_end": "2026-01-03T00:00:00+00:00",
                "source_policy": "AUTO",
            },
        )
        requirement = requirement_set.requirements[0]
        self.assertEqual(["yfinance"], requirement.source_selection_policy["allowed_sources"])
        resolution = DeterministicManifestResolver(self.store).resolve(
            requirement_set.requirement_set_id,
            verify_physical=False,
        )
        self.assertTrue(resolution.ready, resolution.to_dict())
        self.assertEqual("YFINANCE", resolution.bindings[0]["source"])
        conflict = DeterministicManifestResolver(self.store).resolve(
            requirement_set.requirement_set_id,
            source_selection_policy={
                "mode": "FIXED",
                "allowed_sources": ["binance"],
                "preferred_sources": ["binance"],
            },
            verify_physical=False,
        )
        self.assertFalse(conflict.ready)
        self.assertTrue(any(
            str(item.code) == "PROVIDER_MISMATCH"
            for item in conflict.checks
        ))

    def test_bare_equity_none_requirement_accepts_openbb_split_adjustment(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        rows = [{
            "instrument_id": "AAPL",
            "frequency": "1d",
            "bar_start_time": (start + timedelta(days=index)).isoformat(),
            "bar_end_time": (start + timedelta(days=index + 1)).isoformat(),
            "available_time": (start + timedelta(days=index + 1)).isoformat(),
            "ingested_at": "2026-01-04T00:00:00+00:00",
            "open": 100 + index,
            "high": 101 + index,
            "low": 99 + index,
            "close": 100 + index,
            "volume": 1000,
            "turnover": 100000,
            "trade_count": 10,
            "bar_status": "COMPLETE",
            "source": "OPENBB/YFINANCE",
            "source_version": "1",
            "quality_status": "PASS",
        } for index in range(3)]
        CanonicalBarsCommitter(self.store, self.root / "openbb").commit(
            dataset_id="openbb:yfinance:AAPL:bars:1d:splits_only",
            instrument_id="AAPL",
            asset_class="equity",
            venue="XNAS",
            frequency="1d",
            source="OPENBB/YFINANCE",
            source_version="1",
            rows=rows,
            adjustment="SPLITS_ONLY",
        )
        requirement_set = RequirementCompiler(self.store).compile(
            project_id="bare_equity_project",
            manual_requirements=[{"id": "aapl", "fields": ["close"], "adjustment": "NONE"}],
            context={
                "instrument_ids": ["AAPL"],
                "data_type": "bars",
                "frequency": "1d",
                "history_start": "2026-01-01T00:00:00+00:00",
                "history_end": "2026-01-03T00:00:00+00:00",
                "adjustment": "NONE",
            },
        )

        resolution = DeterministicManifestResolver(self.store).resolve(
            requirement_set.requirement_set_id,
            verify_physical=False,
        )

        self.assertTrue(resolution.ready, resolution.to_dict())

    def test_daily_crsp_valuation_manifest_covers_same_calendar_end_date(self) -> None:
        instrument_id = "equity:CRSP:10001"
        CanonicalDatasetCommitter(self.store, self.root / "crsp-valuation").commit(
            dataset_id="crsp:ciz:equity_valuation_daily",
            instrument_id="equity:CRSP:ALL",
            data_type="equity_valuation_daily",
            frequency="1d",
            source="CRSP/CIZ",
            source_version="test",
            schema_version="equity_valuation_daily.v1",
            rows=[{
                "instrument_id": instrument_id,
                "event_time": "2025-12-31T00:00:00+00:00",
                "available_time": "2026-01-01T00:00:00+00:00",
                "market_cap": 1_000_000.0,
            }],
            event_time_field="event_time",
            adjustment="NONE",
        )
        universe = UniverseService(self.store).create_definition(
            name="crsp-pit-market-cap",
            version="1.0.0",
            universe_type="HISTORICAL_EQUITY_PIT",
            parameters={
                "history_start": "2025-12-31",
                "history_end": "2025-12-31",
                "point_in_time_filters": [{
                    "field": "market_cap_usd",
                    "minimum": 100_000_000,
                }],
            },
        )
        requirement_set = RequirementCompiler(self.store).compile(
            project_id="crsp_valuation_day_boundary",
            universe_requirements=UniverseService.data_requirements(universe),
            context={
                "instrument_ids": [instrument_id],
                "data_type": "equity_valuation_daily",
                "frequency": "1d",
                "history_start": "2025-12-31T00:00:00+00:00",
                "history_end": "2025-12-31T23:59:59+00:00",
                "adjustment": "NONE",
                "time_semantics": "SOURCE_AVAILABLE_TIME",
                "source_selection_policy": {
                    "mode": "FIXED",
                    "allowed_sources": ["crsp/ciz"],
                    "preferred_sources": ["crsp/ciz"],
                },
            },
        )

        resolution = DeterministicManifestResolver(self.store).resolve(
            requirement_set.requirement_set_id,
            verify_physical=False,
        )

        self.assertTrue(resolution.ready, resolution.to_dict())

    def test_raw_sec_manifest_covers_logical_universe_fundamental_fields(self) -> None:
        instrument_id = "equity:CRSP:10001"
        CanonicalDatasetCommitter(self.store, self.root / "sec-fundamentals").commit(
            dataset_id="sec:companyfacts:all:fundamentals_pit",
            instrument_id="equity:CRSP:ALL",
            data_type="fundamentals_pit",
            frequency="event",
            source="SEC/COMPANYFACTS",
            source_version="test",
            schema_version="fundamentals_pit.v1",
            rows=[
                {
                    "instrument_id": instrument_id,
                    "concept": "NetIncomeLoss",
                    "value": 10_000_000.0,
                    "unit": "USD",
                    "period_start": "2023-01-01",
                    "period_end": "2023-03-31",
                    "event_time": "2023-01-01T00:00:00+00:00",
                    "available_time": "2023-05-01T20:00:00+00:00",
                    "form": "10-Q",
                },
                {
                    "instrument_id": instrument_id,
                    "concept": "StockholdersEquity",
                    "value": 200_000_000.0,
                    "unit": "USD",
                    "period_end": "2025-12-31",
                    "event_time": "2025-12-31T23:59:59+00:00",
                    "available_time": "2025-12-31T23:59:59+00:00",
                    "form": "10-Q",
                },
            ],
            event_time_field="event_time",
            adjustment="NONE",
            point_in_time_policy="FILED_OR_ACCEPTED_AT",
        )
        universe = UniverseService(self.store).create_definition(
            name="crsp-pit-roe",
            version="1.0.0",
            universe_type="HISTORICAL_EQUITY_PIT",
            parameters={
                "history_start": "2025-01-01",
                "history_end": "2025-12-31",
                "point_in_time_filters": [{"field": "roe_ttm", "minimum": 0.15}],
            },
        )
        requirement_set = RequirementCompiler(self.store).compile(
            project_id="sec_fundamental_universe",
            universe_requirements=UniverseService.data_requirements(universe),
            context={
                "instrument_ids": [instrument_id],
                "data_type": "bars",
                "frequency": "1d",
                "history_start": "2025-01-01T00:00:00+00:00",
                "history_end": "2025-12-31T23:59:59+00:00",
                "adjustment": "NONE",
            },
        )

        resolution = DeterministicManifestResolver(self.store).resolve(
            requirement_set.requirement_set_id,
            verify_physical=False,
        )

        self.assertTrue(resolution.ready, resolution.to_dict())
        self.assertEqual("fundamentals_pit", requirement_set.requirements[0].data_type)
        self.assertEqual(["equity", "net_income_ttm"], list(requirement_set.requirements[0].fields))
