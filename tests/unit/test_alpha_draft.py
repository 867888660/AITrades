from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.data_platform import (
    AlphaDraftService,
    DataPlatformStore,
    DefinitionRegistry,
    Instrument,
    InstrumentRegistry,
    ResearchControlPlane,
    UniverseService,
)


def factor_spec() -> dict:
    return {
        "name": "momentum",
        "version": "1.0.0",
        "operator": "pct_change",
        "input_field": "close",
        "window": 2,
        "frequency": "1h",
        "output_unit": "RATIO",
        "output_direction": "HIGHER_IS_BETTER",
    }


def alpha_document(factor_id: str, *, weight: float = 1.0) -> dict:
    return {
        "schema_version": "alpha_draft.v2",
        "identity": {
            "name": "momentum_alpha",
            "description": "Rank a validated momentum Factor.",
            "version": "1.0.0",
        },
        "components": [{
            "variable_name": "momentum",
            "factor_definition_id": factor_id,
            "factor_version": "1.0.0",
            "weight": weight,
            "transform": "CS_RANK",
            "ascending": True,
        }],
        "formula": {"model": "WEIGHTED_SUM"},
        "output": {
            "display_name": "Momentum score",
            "kind": "PREDICTION_SCORE",
        },
        "advanced": {
            "minimum_coverage": 0.8,
            "minimum_cross_section_size": 1,
            "missing_policy": "EXCLUDE",
            "rank_method": "AVERAGE",
            "output_scale": "PERCENTILE",
        },
    }


class AlphaDraftServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = DataPlatformStore(Path(self.temp.name) / "metadata.db")
        self.project = ResearchControlPlane(self.store).create_project(
            title="Alpha Draft",
            objective="author a project Alpha like a Factor",
        )
        instrument_id = "crypto_spot:BINANCE:BTCUSDT"
        InstrumentRegistry(self.store).register(
            Instrument(
                instrument_id=instrument_id,
                asset_class="crypto_spot",
                venue="BINANCE",
                market_type="SPOT",
                native_symbol="BTCUSDT",
            )
        )
        universe = UniverseService(self.store).create_definition(
            name="BTC",
            version="1.0.0",
            universe_type="STATIC_LIST",
            parameters={"instrument_ids": [instrument_id]},
            owner_project_id=self.project["project_id"],
            library_scope="PROJECT",
        )
        snapshot = UniverseService(self.store).resolve_snapshot(
            universe_definition_id=universe.universe_definition_id,
            as_of_time="2026-07-31T00:00:00+00:00",
        )
        UniverseService(self.store).set_research_ref(
            project_id=self.project["project_id"],
            universe_snapshot_id=snapshot.universe_snapshot_id,
        )
        self.factor = DefinitionRegistry(self.store).create(
            "FACTOR",
            factor_spec(),
            state="VALIDATED",
            owner_project_id=self.project["project_id"],
            library_scope="PROJECT",
        )
        self.service = AlphaDraftService(self.store)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_migration_capability_and_valid_project_document(self) -> None:
        with self.store.connection() as conn:
            version = conn.execute(
                "SELECT MAX(migration_version) FROM schema_migrations"
            ).fetchone()[0]
        self.assertEqual(21, version)
        contract = DefinitionRegistry.engine_capabilities()[
            "alpha"
        ]["authoring_contract"]
        self.assertEqual("alpha_draft.v2", contract["document_version"])
        inspected = self.service.inspect_project_document(
            alpha_document(self.factor.definition_id),
            self.project["project_id"],
        )
        self.assertTrue(
            inspected["definition_checks_passed"],
            inspected["diagnostics"],
        )
        self.assertEqual(
            self.factor.spec_hash,
            inspected["dependency_closure"][0]["factor_spec_hash"],
        )
        self.assertEqual(
            self.factor.definition_id,
            inspected["compiled_alpha_spec"]["components"][0][
                "factor_definition_id"
            ],
        )

    def test_incomplete_document_can_save_and_client_key_is_idempotent(self) -> None:
        first = self.service.create(
            {},
            owner_project_id=self.project["project_id"],
            client_draft_key="ui:test:alpha",
        )
        second = self.service.create(
            {"identity": {"name": "ignored retry"}},
            owner_project_id=self.project["project_id"],
            client_draft_key="ui:test:alpha",
        )
        self.assertEqual(first.draft_id, second.draft_id)
        self.assertFalse(self.service.inspect(first.draft_id)["can_preview"])

    def test_stale_update_is_rejected_and_content_change_clears_preview(self) -> None:
        draft = self.service.create(
            alpha_document(self.factor.definition_id),
            owner_project_id=self.project["project_id"],
        )
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                """
                UPDATE alpha_drafts
                SET latest_preview_id='preview-old',
                    latest_preview_fingerprint='fingerprint-old',
                    previewed_draft_fingerprint=?
                WHERE draft_id=?
                """,
                (draft.draft_fingerprint, draft.draft_id),
            )
        changed = self.service.update(
            draft.draft_id,
            alpha_document(self.factor.definition_id, weight=0.5),
            expected_fingerprint=draft.draft_fingerprint,
        )
        self.assertEqual("", changed.latest_preview_id)
        with self.assertRaisesRegex(ValueError, "ALPHA_DRAFT_STALE"):
            self.service.update(
                draft.draft_id,
                alpha_document(self.factor.definition_id),
                expected_fingerprint=draft.draft_fingerprint,
            )
        with self.store.connection() as conn:
            operations = [
                str(row["operation"])
                for row in conn.execute(
                    """
                    SELECT operation FROM research_authoring_events
                    WHERE object_id=? ORDER BY created_at,event_id
                    """,
                    (draft.draft_id,),
                ).fetchall()
            ]
        self.assertCountEqual(["CREATE", "UPDATE"], operations)

    def test_global_scope_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "ALPHA_LIBRARY_SCOPE_UNSUPPORTED",
        ):
            self.service.create(
                {},
                owner_project_id=self.project["project_id"],
                library_scope="GLOBAL",
            )


if __name__ == "__main__":
    unittest.main()
