from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from services.data_platform import (
    AlphaDraftService,
    AlphaPreviewError,
    AlphaPreviewService,
    CanonicalBarsCommitter,
    DataPlatformStore,
    DefinitionRegistry,
    Instrument,
    InstrumentRegistry,
    ResearchControlPlane,
    ResearchLibraryService,
    UniverseService,
)


def alpha_document(factor_id: str) -> dict:
    return {
        "schema_version": "alpha_draft.v2",
        "identity": {
            "name": "preview_alpha",
            "version": "1.0.0",
        },
        "components": [{
            "variable_name": "momentum",
            "factor_definition_id": factor_id,
            "factor_version": "1.0.0",
            "weight": 1.0,
            "transform": "CS_RANK",
            "ascending": True,
        }],
        "formula": {"model": "WEIGHTED_SUM"},
        "advanced": {
            "minimum_coverage": 1.0,
            "minimum_cross_section_size": 2,
            "missing_policy": "EXCLUDE",
            "rank_method": "AVERAGE",
            "output_scale": "PERCENTILE",
        },
    }


class AlphaPreviewServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = DataPlatformStore(root / "metadata.db")
        self.project = ResearchControlPlane(self.store).create_project(
            title="Alpha Preview",
            objective="preview a pinned Factor combination",
        )
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        instrument_ids = [
            "crypto_spot:BINANCE:BTCUSDT",
            "crypto_spot:BINANCE:ETHUSDT",
        ]
        registry = InstrumentRegistry(self.store)
        for offset, instrument_id in enumerate(instrument_ids):
            symbol = instrument_id.rsplit(":", 1)[-1]
            registry.register(
                Instrument(
                    instrument_id=instrument_id,
                    asset_class="crypto_spot",
                    venue="BINANCE",
                    market_type="SPOT",
                    native_symbol=symbol,
                )
            )
            rows = []
            for index in range(12):
                start = base + timedelta(hours=index)
                end = start + timedelta(hours=1)
                close = 100.0 + offset * 20 + index * (offset + 1)
                rows.append({
                    "instrument_id": instrument_id,
                    "frequency": "1h",
                    "bar_start_time": start.isoformat(),
                    "bar_end_time": end.isoformat(),
                    "available_time": end.isoformat(),
                    "ingested_at": (
                        base + timedelta(days=2)
                    ).isoformat(),
                    "open": close - 0.5,
                    "high": close + 1,
                    "low": close - 1,
                    "close": close,
                    "volume": 10 + index,
                    "turnover": close * (10 + index),
                    "trade_count": 2,
                    "bar_status": "COMPLETE",
                    "source": "BINANCE",
                    "source_version": "1",
                    "quality_status": "PASS",
                })
            CanonicalBarsCommitter(
                self.store,
                root / f"bars-{symbol}",
            ).commit(
                dataset_id=f"binance:{symbol}:1h",
                instrument_id=instrument_id,
                asset_class="crypto_spot",
                venue="BINANCE",
                frequency="1h",
                source="BINANCE",
                source_version="1",
                rows=rows,
            )
        universe = UniverseService(self.store).create_definition(
            name="BTC ETH",
            version="1.0.0",
            universe_type="STATIC_LIST",
            parameters={"instrument_ids": instrument_ids},
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
        self.factor = DefinitionRegistry(self.store).create(
            "FACTOR",
            {
                "name": "momentum",
                "version": "1.0.0",
                "operator": "pct_change",
                "input_field": "close",
                "window": 2,
                "frequency": "1h",
                "output_unit": "RATIO",
                "output_direction": "HIGHER_IS_BETTER",
            },
            state="VALIDATED",
            owner_project_id=self.project["project_id"],
            library_scope="PROJECT",
        )
        ResearchLibraryService(self.store).publish_definition(
            definition_id=self.factor.definition_id,
            project_id=self.project["project_id"],
        )
        self.drafts = AlphaDraftService(self.store)
        self.previews = AlphaPreviewService(self.store)
        self.draft = self.drafts.create(
            alpha_document(self.factor.definition_id),
            owner_project_id=self.project["project_id"],
        )
        self.request = {
            "expected_fingerprint": self.draft.draft_fingerprint,
            "universe_snapshot_id": self.snapshot.universe_snapshot_id,
            "start_time": (
                base + timedelta(hours=4)
            ).isoformat(),
            "end_time": (
                base + timedelta(hours=10)
            ).isoformat(),
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_preview_and_validate_pin_the_full_dependency_closure(self) -> None:
        context = self.previews.context(self.draft.draft_id)
        self.assertTrue(
            context["can_run_preview"],
            context["diagnostics"],
        )
        self.assertEqual(2, len(context["candidate_manifest_ids"]))
        requirement = self.previews.compile_requirements(
            self.draft.draft_id,
            self.request,
        )
        self.assertEqual("ALPHA_PREVIEW", requirement["reference"]["scope"])
        self.assertIsNone(
            ResearchLibraryService(self.store).get_requirement_ref(
                self.project["project_id"]
            )
        )
        preview = self.previews.create(
            self.draft.draft_id,
            self.request,
        )
        repeated = self.previews.create(
            self.draft.draft_id,
            self.request,
        )
        self.assertEqual(preview["preview_id"], repeated["preview_id"])
        self.assertEqual("READY", preview["status"])
        self.assertGreater(
            preview["analysis"]["overall"]["valid_value_count"],
            0,
        )
        self.assertEqual(
            self.factor.spec_hash,
            preview["factor_refs"][0]["factor_spec_hash"],
        )
        inspected = self.drafts.inspect(self.draft.draft_id)
        self.assertTrue(inspected["can_validate"])
        validated, definition = self.drafts.validate(
            self.draft.draft_id,
            expected_fingerprint=self.draft.draft_fingerprint,
            preview_id=preview["preview_id"],
            preview_fingerprint=preview["preview_fingerprint"],
        )
        self.assertEqual("VALIDATED", validated.state)
        self.assertEqual("ALPHA", definition.definition_type)
        self.assertEqual(
            self.factor.definition_id,
            definition.spec["components"][0][
                "factor_definition_id"
            ],
        )
        with self.store.connection() as conn:
            events = [
                (str(row["object_type"]), str(row["operation"]))
                for row in conn.execute(
                    """
                    SELECT object_type,operation
                    FROM research_authoring_events
                    WHERE project_id=?
                    ORDER BY created_at,event_id
                    """,
                    (self.project["project_id"],),
                ).fetchall()
            ]
        self.assertIn(("ALPHA_PREVIEW", "PREVIEW"), events)
        self.assertIn(("ALPHA_DRAFT", "VALIDATE"), events)

    def test_validate_without_preview_is_blocked(self) -> None:
        with self.assertRaisesRegex(
            AlphaPreviewError,
            "ALPHA_PREVIEW_REQUIRED",
        ):
            self.drafts.validate(
                self.draft.draft_id,
                expected_fingerprint=self.draft.draft_fingerprint,
                preview_id="",
                preview_fingerprint="",
            )


if __name__ == "__main__":
    unittest.main()
