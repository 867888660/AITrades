from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from services.data_platform import (
    CanonicalBarsCommitter,
    CanonicalDatasetCommitter,
    DataPlatformStore,
    FactorDraftService,
    FactorPreviewError,
    FactorPreviewService,
    Instrument,
    InstrumentRegistry,
    PolymarketHistoryPreparer,
    ResearchControlPlane,
    ResearchLibraryService,
    UniverseService,
)


def factor_document(window: int = 2) -> dict:
    return {
        "schema_version": "factor_draft.v2",
        "identity": {"name": "preview_return", "version": "1.0.0"},
        "inputs": [{
            "variable_name": "price",
            "dataset": "bars",
            "field": "close",
            "frequency": "1h",
        }],
        "parameters": [{"name": "window", "value": window, "unit": "bars"}],
        "formula": {"source": "time.pct_change(price, window)"},
        "output": {"direction": "NO_PREDEFINED_DIRECTION"},
        "advanced": {
            "missing_policy": "STRICT",
            "time_alignment_policy": "BAR_END_AVAILABLE_TIME",
            "available_after": "BAR_CLOSE",
        },
    }


class FactorPreviewServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = DataPlatformStore(root / "metadata.db")
        self.project = ResearchControlPlane(self.store).create_project(
            title="Factor Preview",
            objective="verify Factor Draft preview lifecycle",
        )
        self.instrument_id = "crypto_spot:BINANCE:BTCUSDT"
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        rows = []
        for index in range(12):
            start = base + timedelta(hours=index)
            end = start + timedelta(hours=1)
            close = 100.0 + index
            rows.append({
                "instrument_id": self.instrument_id,
                "frequency": "1h",
                "bar_start_time": start.isoformat(),
                "bar_end_time": end.isoformat(),
                "available_time": end.isoformat(),
                "ingested_at": (base + timedelta(days=2)).isoformat(),
                "open": close - 0.5,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": 10.0 + index,
                "turnover": close * (10.0 + index),
                "trade_count": 2,
                "bar_status": "COMPLETE",
                "source": "BINANCE",
                "source_version": "1",
                "quality_status": "PASS",
            })
        self.committed = CanonicalBarsCommitter(
            self.store, root / "bars"
        ).commit(
            dataset_id="binance:BTCUSDT:1h",
            instrument_id=self.instrument_id,
            asset_class="crypto_spot",
            venue="BINANCE",
            frequency="1h",
            source="BINANCE",
            source_version="1",
            rows=rows,
        )
        universe = UniverseService(self.store).create_definition(
            name="BTC",
            version="1.0.0",
            universe_type="STATIC_LIST",
            parameters={"instrument_ids": [self.instrument_id]},
            owner_project_id=self.project["project_id"],
            library_scope="PROJECT",
        )
        self.snapshot = UniverseService(self.store).resolve_snapshot(
            universe_definition_id=universe.universe_definition_id,
            as_of_time=(base + timedelta(hours=12)).isoformat(),
        )
        UniverseService(self.store).set_research_ref(
            project_id=self.project["project_id"],
            universe_snapshot_id=self.snapshot.universe_snapshot_id,
        )
        self.drafts = FactorDraftService(self.store)
        self.previews = FactorPreviewService(self.store)
        self.draft = self.drafts.create(
            factor_document(),
            owner_project_id=self.project["project_id"],
            library_scope="PROJECT",
        )
        self.request = {
            "expected_fingerprint": self.draft.draft_fingerprint,
            "universe_snapshot_id": self.snapshot.universe_snapshot_id,
            "start_time": (base + timedelta(hours=4)).isoformat(),
            "end_time": (base + timedelta(hours=10)).isoformat(),
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_preview_pins_snapshot_manifest_range_and_real_values(self) -> None:
        before = self.drafts.inspect(self.draft.draft_id)
        self.assertTrue(before["can_preview"])
        self.assertFalse(before["can_validate"])
        self.assertEqual("NOT_RUN", before["preview_status"])

        context = self.previews.context(self.draft.draft_id)
        self.assertTrue(context["can_run_preview"], context["diagnostics"])
        self.assertEqual(
            self.snapshot.universe_snapshot_id,
            context["universe"]["universe_snapshot_id"],
        )
        self.assertEqual(
            [self.committed["manifest"].manifest_id],
            context["candidate_manifest_ids"],
        )

        preview = self.previews.create(self.draft.draft_id, self.request)
        repeated = self.previews.create(self.draft.draft_id, self.request)
        self.assertEqual(preview["preview_id"], repeated["preview_id"])
        self.assertEqual("READY", preview["status"])
        self.assertEqual(
            [self.committed["manifest"].manifest_id],
            preview["manifest_ids"],
        )
        self.assertGreater(preview["analysis"]["overall"]["valid_value_count"], 0)
        self.assertTrue(any(item["value"] is not None for item in preview["values"]))

        ready = self.drafts.inspect(self.draft.draft_id)
        self.assertTrue(ready["can_validate"])
        self.assertEqual("READY", ready["preview_status"])
        validated, definition = self.drafts.validate(
            self.draft.draft_id,
            expected_fingerprint=self.draft.draft_fingerprint,
            preview_id=preview["preview_id"],
            preview_fingerprint=preview["preview_fingerprint"],
        )
        self.assertEqual("VALIDATED", validated.state)
        linked = self.previews.get(preview["preview_id"])
        self.assertEqual(definition.definition_id, linked["validated_definition_id"])

    def test_draft_change_invalidates_the_previous_preview(self) -> None:
        preview = self.previews.create(self.draft.draft_id, self.request)
        changed = factor_document(window=3)
        updated = self.drafts.update(
            self.draft.draft_id,
            changed,
            expected_fingerprint=self.draft.draft_fingerprint,
        )
        inspected = self.drafts.inspect(updated.draft_id)
        self.assertFalse(inspected["can_validate"])
        self.assertEqual("NOT_RUN", inspected["preview_status"])
        with self.assertRaisesRegex(FactorPreviewError, "FACTOR_PREVIEW_STALE"):
            self.drafts.validate(
                updated.draft_id,
                expected_fingerprint=updated.draft_fingerprint,
                preview_id=preview["preview_id"],
                preview_fingerprint=preview["preview_fingerprint"],
            )

    def test_preview_rejects_a_range_without_compiled_history(self) -> None:
        bad = dict(self.request)
        bad["start_time"] = "2026-01-01T00:00:00+00:00"
        with self.assertRaisesRegex(
            FactorPreviewError,
            "FACTOR_PREVIEW_RANGE_NOT_COVERED",
        ):
            self.previews.create(self.draft.draft_id, bad)


class PolymarketFactorPreviewServiceTests(unittest.TestCase):
    def test_price_history_runs_through_requirement_manifest_and_engine(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = DataPlatformStore(root / "metadata.db")
            project = ResearchControlPlane(store).create_project(
                title="Polymarket Factor Preview",
                objective="verify outcome price history end to end",
            )
            instrument_id = "polymarket_binary:POLYMARKET:yes-token"
            InstrumentRegistry(store).register(
                Instrument(
                    instrument_id=instrument_id,
                    asset_class="polymarket_binary",
                    venue="POLYMARKET",
                    market_type="BINARY",
                    native_symbol="yes-token",
                    condition_id="condition-1",
                    outcome_side="YES",
                ),
                aliases=[("polymarket", "yes-token")],
            )
            base = datetime(2026, 1, 1, tzinfo=timezone.utc)
            rows = [{
                "event_time": (base + timedelta(hours=index)).isoformat(),
                "available_time": (base + timedelta(hours=index)).isoformat(),
                "price": 0.35 + index * 0.02,
                "condition_id": "condition-1",
                "token_id": "yes-token",
            } for index in range(12)]
            committed = PolymarketHistoryPreparer(
                store,
                output_root=root / "canonical",
            )._commit(
                instrument_id=instrument_id,
                token_id="yes-token",
                condition_id="condition-1",
                frequency="1h",
                rows=rows,
            )
            universe = UniverseService(store).create_definition(
                name="One outcome",
                version="1.0.0",
                universe_type="STATIC_LIST",
                parameters={"instrument_ids": [instrument_id]},
                owner_project_id=project["project_id"],
                library_scope="PROJECT",
            )
            snapshot = UniverseService(store).resolve_snapshot(
                universe_definition_id=universe.universe_definition_id,
                as_of_time=(base + timedelta(hours=12)).isoformat(),
            )
            UniverseService(store).set_research_ref(
                project_id=project["project_id"],
                universe_snapshot_id=snapshot.universe_snapshot_id,
            )
            document = factor_document()
            document["inputs"][0].update({
                "dataset": "price_history",
                "field": "price",
            })
            drafts = FactorDraftService(store)
            draft = drafts.create(
                document,
                owner_project_id=project["project_id"],
                library_scope="PROJECT",
            )
            previews = FactorPreviewService(store)
            request = {
                "expected_fingerprint": draft.draft_fingerprint,
                "universe_snapshot_id": snapshot.universe_snapshot_id,
                "start_time": (base + timedelta(hours=4)).isoformat(),
                "end_time": (base + timedelta(hours=10)).isoformat(),
            }

            requirement = previews.compile_requirements(draft.draft_id, request)
            result = previews.create(draft.draft_id, request)

            self.assertEqual("FACTOR_PREVIEW", requirement["reference"]["scope"])
            self.assertIsNone(
                ResearchLibraryService(store).get_requirement_ref(
                    project["project_id"]
                )
            )
            self.assertEqual(
                "price_history",
                requirement["requirements"][0]["dataset"],
            )
            self.assertEqual(["price"], requirement["requirements"][0]["fields"])
            self.assertEqual(
                [committed["manifest"]["manifest_id"]],
                result["manifest_ids"],
            )
            self.assertGreater(
                result["analysis"]["overall"]["valid_value_count"],
                0,
            )


class CrspFactorPreviewServiceTests(unittest.TestCase):
    def test_daily_collection_manifest_covers_its_last_observation_day(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = DataPlatformStore(root / "metadata.db")
            project = ResearchControlPlane(store).create_project(
                title="CRSP Factor Preview",
                objective="verify CRSP daily endpoint and physical semantics",
            )
            instrument_id = "equity:CRSP:10001"
            base = datetime(2026, 1, 1, tzinfo=timezone.utc)
            rows = []
            for index in range(4):
                start = base + timedelta(days=index)
                end = start + timedelta(days=1)
                close = 100.0 + index
                rows.append({
                    "security_id": "crsp:permno:10001",
                    "instrument_id": instrument_id,
                    "frequency": "1d",
                    "bar_start_time": start.isoformat(),
                    "bar_end_time": end.isoformat(),
                    "available_time": end.isoformat(),
                    "open": close - 0.5,
                    "high": close + 1.0,
                    "low": close - 1.0,
                    "close": close,
                    "volume": 1000.0,
                    "bar_status": "COMPLETE",
                    "source": "CRSP/CIZ",
                    "quality_status": "PASS",
                })
            committed = CanonicalDatasetCommitter(
                store,
                root / "canonical",
            ).commit(
                dataset_id="crsp:ciz:bars",
                instrument_id="equity:CRSP:ALL",
                data_type="bars",
                frequency="1d",
                source="CRSP/CIZ",
                source_version="test",
                schema_version="bars_daily.v2",
                rows=rows,
                event_time_field="bar_start_time",
                adjustment="CRSP_FIELDS",
            )
            universe = UniverseService(store).create_definition(
                name="CRSP member",
                version="1.0.0",
                universe_type="STATIC_LIST",
                parameters={"instrument_ids": [instrument_id]},
                owner_project_id=project["project_id"],
                library_scope="PROJECT",
            )
            snapshot = UniverseService(store).resolve_snapshot(
                universe_definition_id=universe.universe_definition_id,
                as_of_time=(base + timedelta(days=5)).isoformat(),
            )
            UniverseService(store).set_research_ref(
                project_id=project["project_id"],
                universe_snapshot_id=snapshot.universe_snapshot_id,
            )
            document = factor_document(window=1)
            document["inputs"][0]["frequency"] = "1d"
            drafts = FactorDraftService(store)
            draft = drafts.create(
                document,
                owner_project_id=project["project_id"],
                library_scope="PROJECT",
            )
            preview = FactorPreviewService(store).create(
                draft.draft_id,
                {
                    "expected_fingerprint": draft.draft_fingerprint,
                    "universe_snapshot_id": snapshot.universe_snapshot_id,
                    "start_time": (base + timedelta(days=1)).isoformat(),
                    "end_time": (base + timedelta(days=3, hours=23, minutes=59)).isoformat(),
                },
            )

            self.assertEqual("READY", preview["status"])
            self.assertEqual(
                [committed["manifest"].manifest_id],
                preview["manifest_ids"],
            )


if __name__ == "__main__":
    unittest.main()
