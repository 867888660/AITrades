from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.data_platform import (
    DataPlatformStore,
    DefinitionRegistry,
    Instrument,
    InstrumentRegistry,
    RequirementWorkspaceService,
    ResearchControlPlane,
    ResearchLibraryService,
    SharedUniverseService,
    default_requirement_spec,
    make_instrument_id,
)


class LibraryArchiveLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = DataPlatformStore(Path(self.temp.name) / "archive.db")
        self.project = ResearchControlPlane(self.store).create_project(
            title="Archive lifecycle", objective="Preserve pinned Research references"
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_in_use_universe_can_leave_library_without_breaking_research(self) -> None:
        instrument_id = make_instrument_id("crypto_spot", "BINANCE", "BTCUSDT")
        InstrumentRegistry(self.store).register(
            Instrument(
                instrument_id=instrument_id,
                asset_class="crypto_spot",
                venue="BINANCE",
                market_type="SPOT",
                native_symbol="BTCUSDT",
            ),
            aliases=[("binance", "BTCUSDT")],
        )
        service = SharedUniverseService(self.store)
        universe = service.create(
            {"name": "BTC only", "type": "instrument_set", "members": [instrument_id]},
            project_id=self.project["project_id"],
        )

        archived = service.archive(universe["universe_id"])

        self.assertEqual(1, archived["archived_research_count"])
        self.assertTrue(archived["references_preserved"])
        self.assertEqual([], service.list())
        self.assertEqual(
            universe["universe_id"],
            service.list_project(self.project["project_id"])[0]["universe_id"],
        )

    def test_in_use_requirement_can_leave_library_without_breaking_research(self) -> None:
        service = RequirementWorkspaceService(self.store)
        item = service.create_research_requirement(
            self.project["project_id"],
            {"spec": default_requirement_spec("Pinned bars")},
        )

        archived = service.archive_library_requirement(item["library_asset_id"])

        self.assertEqual(1, archived["archived_research_count"])
        self.assertTrue(archived["references_preserved"])
        self.assertEqual([], service.list_library_assets(include_data_status=False))
        self.assertEqual(
            item["library_asset_id"],
            service.get_project_item(self.project["project_id"], item["ref_id"])["library_asset_id"],
        )

    def test_in_use_factor_can_leave_library_without_breaking_pinned_ref(self) -> None:
        registry = DefinitionRegistry(self.store)
        definition = registry.create(
            "FACTOR",
            {
                "name": "momentum",
                "version": "1.0.0",
                "operator": "pct_change",
                "input_field": "close",
                "window": 20,
                "frequency": "1d",
            },
            state="VALIDATED",
            owner_project_id=self.project["project_id"],
            library_scope="PROJECT",
        )
        registry.set_project_ref(
            project_id=self.project["project_id"],
            slot_key="factor:primary",
            definition_id=definition.definition_id,
            definition_version=definition.version,
            reference_mode="PINNED",
        )
        library = ResearchLibraryService(self.store)
        asset = library.publish_definition(
            definition_id=definition.definition_id,
            project_id=self.project["project_id"],
        )

        archived = library.archive(asset["library_asset_id"])

        self.assertEqual(1, archived["archived_research_count"])
        self.assertTrue(archived["references_preserved"])
        self.assertEqual([], library.list(component_type="FACTOR"))
        self.assertEqual(
            definition.definition_id,
            registry.list_project_refs(self.project["project_id"])["factor:primary"]["definition_id"],
        )


if __name__ == "__main__":
    unittest.main()
