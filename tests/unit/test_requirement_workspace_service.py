from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from services.data_platform import (
    DataPlatformStore,
    DatasetCatalogService,
    DefinitionRegistry,
    DeterministicManifestResolver,
    Instrument,
    InstrumentRegistry,
    RequirementWorkspaceService,
    RequirementMaintenanceService,
    ResearchDataCapabilityService,
    ResearchControlPlane,
    ResearchLibraryService,
    SharedUniverseService,
    default_requirement_spec,
    make_instrument_id,
)
from services.data_platform.manifest_resolver import _manifest_covers_required_start


class RequirementWorkspaceServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = DataPlatformStore(Path(self.temp.name) / "metadata.db")
        self.project = ResearchControlPlane(self.store).create_project(
            title="Requirement workspace", objective="Verify independent authoring domains"
        )
        self.service = RequirementWorkspaceService(self.store)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_research_can_combine_multiple_requirement_cards(self) -> None:
        first = default_requirement_spec("Price bars")
        second = default_requirement_spec("Volume bars")
        first["data"]["fields"] = ["close"]
        second["data"]["fields"] = ["volume"]
        second["time"]["start"] = "2025-01-01"

        self.service.create_research_requirement(self.project["project_id"], {"spec": first})
        self.service.create_research_requirement(self.project["project_id"], {"spec": second})

        items = self.service.list_project_items(self.project["project_id"], include_derived=False)
        self.assertEqual(2, len(items))
        compiled = self.service.compile_project(self.project["project_id"])
        requirement_set = self.service.compiler.get(compiled["requirement_set_id"])
        self.assertEqual(1, len(requirement_set.requirements))
        self.assertEqual({"close", "volume"}, set(requirement_set.requirements[0].fields))
        self.assertTrue(requirement_set.requirements[0].history_start.startswith("2025-01-01"))

    def test_alpha_dependency_closure_loads_its_validated_factor(self) -> None:
        factor = DefinitionRegistry(self.store).create(
            "FACTOR",
            {
                "name": "alpha_input",
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

        factors = self.service._factor_specs(
            self.project["project_id"],
            alpha_specs=[{
                "components": [{
                    "factor_definition_id": factor.definition_id,
                    "factor_version": factor.version,
                }],
            }],
        )

        self.assertEqual([factor.definition_id], [
            item["definition_id"] for item in factors
        ])

    def test_research_new_creates_one_library_requirement_and_one_reference(self) -> None:
        item = self.service.create_research_requirement(
            self.project["project_id"], {"spec": default_requirement_spec("Reusable bars")}
        )

        assets = self.service.list_library_assets()
        self.assertEqual(1, len(assets))
        self.assertEqual(assets[0]["library_asset_id"], item["library_asset_id"])
        self.assertEqual("LIBRARY", item["origin"])
        self.assertEqual(1, assets[0]["usage_count"])

    def test_backend_maintenance_schedules_standalone_library_data_idempotently(self) -> None:
        asset = self.service.create_library_requirement(
            {"spec": default_requirement_spec("Backend maintained bars")}
        )
        maintenance = RequirementMaintenanceService(self.store)

        first = maintenance.run_once()
        second = maintenance.run_once()
        tasks = ResearchControlPlane(self.store).list_tasks(
            project_id="project_system_requirement_maintenance"
        )

        self.assertGreaterEqual(first["scheduled"], 1)
        self.assertGreaterEqual(second["scheduled"], 1)
        self.assertEqual(1, len(tasks))
        self.assertEqual("READY", tasks[0]["status"])
        self.assertEqual(
            "SYSTEM_REQUIREMENT_MAINTENANCE",
            tasks[0]["input"]["authorization_mode"],
        )
        self.assertEqual(asset["library_asset_id"], tasks[0]["input"]["library_asset_id"])
        live_status = self.service.get_library_asset(asset["library_asset_id"])["data_status"]
        self.assertEqual("QUEUED", live_status["status"])
        self.assertEqual(
            "Waiting for the background data worker",
            live_status["preparation"]["phase"],
        )

    def test_shared_edit_updates_every_research_and_save_as_changes_only_current_reference(self) -> None:
        second = ResearchControlPlane(self.store).create_project(
            title="Second Research", objective="Reuse the same Requirement"
        )
        first_item = self.service.create_research_requirement(
            self.project["project_id"], {"spec": default_requirement_spec("Shared bars")}
        )
        second_item = self.service.add_library_to_research(
            second["project_id"], first_item["library_asset_id"]
        )

        shared = first_item["spec"]
        shared["data"]["frequency"] = "4h"
        self.service.update_research_requirement(
            self.project["project_id"], first_item["ref_id"], {"spec": shared}
        )
        self.assertEqual(
            "4h", self.service.get_project_item(second["project_id"], second_item["ref_id"])["spec"]["data"]["frequency"]
        )

        private = self.service.get_project_item(self.project["project_id"], first_item["ref_id"])["spec"]
        private["name"] = "Shared bars custom"
        private["data"]["frequency"] = "1d"
        replaced = self.service.save_as_for_project(
            self.project["project_id"], first_item["ref_id"], {"spec": private}
        )
        self.assertNotEqual(replaced["library_asset_id"], second_item["library_asset_id"])
        self.assertEqual("1d", replaced["spec"]["data"]["frequency"])
        self.assertEqual(
            "4h", self.service.get_project_item(second["project_id"], second_item["ref_id"])["spec"]["data"]["frequency"]
        )

    def test_remove_reference_preserves_library_requirement(self) -> None:
        item = self.service.create_research_requirement(
            self.project["project_id"], {"spec": default_requirement_spec("Keep in Library")}
        )
        asset_id = item["library_asset_id"]

        self.service.remove_project_item(self.project["project_id"], item["ref_id"])

        self.assertIsNotNone(self.service.get_library_asset(asset_id))
        self.assertEqual(0, self.service.get_library_asset(asset_id)["usage_count"])

    def test_archive_hides_project_without_deleting_protected_record(self) -> None:
        project_id = self.project["project_id"]
        self.service.archive_project(project_id)
        self.assertEqual([], ResearchControlPlane(self.store).list_projects())
        archived = ResearchControlPlane(self.store).list_projects(include_archived=True)
        self.assertEqual(project_id, archived[0]["project_id"])
        self.assertEqual("ARCHIVED", archived[0]["summary_state"])

    def test_data_status_distinguishes_partial_from_not_prepared(self) -> None:
        spec = default_requirement_spec("Coverage")
        spec["scope"]["instruments"]["include"] = ["BTCUSDT", "ETCUSDT"]
        spec["time"]["start"] = "2025-01-01"
        self.service.create_research_requirement(self.project["project_id"], {"spec": spec})
        DatasetCatalogService(self.store).upsert_catalog({
            "dataset_id": "binance:BTCUSDT:1h", "instrument_id": "crypto_spot:BINANCE:BTCUSDT",
            "data_type": "bars", "frequency": "1h", "source": "BINANCE", "status": "READY",
            "quality_status": "PASS", "schema_version": "bars.v1", "storage_path": "unused",
            "start_time": "2026-01-01T00:00:00+00:00", "end_time": "2026-02-01T00:00:00+00:00",
            "last_complete_time": "2026-02-01T00:00:00+00:00", "row_count": 10, "gap_count": 0,
        })
        with patch(
            "services.data_platform.requirement_workspace_service.get_binance_spot_symbol_status",
            return_value={"status": "TRADING"},
        ):
            status = self.service.data_status(self.project["project_id"])
        by_symbol = {row["instrument_id"].split(":")[-1]: row for row in status["rows"]}
        self.assertEqual("PREPARING", by_symbol["BTCUSDT"]["status"])
        self.assertEqual("PARTIAL", by_symbol["BTCUSDT"]["raw_status"])
        self.assertEqual("PREPARING", by_symbol["ETCUSDT"]["status"])
        self.assertEqual("NOT_PREPARED", by_symbol["ETCUSDT"]["raw_status"])
        self.assertTrue(by_symbol["ETCUSDT"]["can_prepare"])

    def test_provider_history_floor_is_applied_automatically_per_research(self) -> None:
        instrument_id = "polymarket_binary:POLYMARKET:123456789"
        spec = default_requirement_spec("Polymarket history")
        spec["target"] = {"scope": "MANUAL_INSTRUMENTS", "universe_id": ""}
        spec["scope"].update({
            "provider": "POLYMARKET",
            "gateway": "DATATUBE",
            "market": "BINARY",
            "asset_type": "POLYMARKET_BINARY",
            "instruments": {
                "type": "STATIC_LIST",
                "include": [instrument_id],
            },
        })
        spec["time"]["start"] = "2025-07-26"
        spec["data"].update({
            "dataset_type": "PRICE_HISTORY",
            "frequency": "1d",
            "fields": ["price"],
        })
        item = self.service.create_research_requirement(
            self.project["project_id"],
            {"spec": spec},
        )
        task_input = {
            "library_asset_id": item["library_asset_id"],
            "instrument_id": instrument_id,
            "interval": "1d",
            "start_time": "2025-07-26T00:00:00+00:00",
        }
        task_output = {
            "availability_adjustment": {
                "code": "DATA_AVAILABLE_AFTER_REQUEST_START",
                "requested_start": "2025-07-26T00:00:00+00:00",
                "available_from": "2026-01-05T00:00:20+00:00",
            },
        }
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                """INSERT INTO research_tasks(
                       task_id, project_id, plan_version, workflow_run_id,
                       task_type, logical_key, status, idempotency_key,
                       input_json, created_at, finished_at, output_json
                   ) VALUES (?, ?, 1, ?, 'POLYMARKET_PRICE_HISTORY_EXPORT',
                             ?, 'SUCCEEDED', ?, ?, ?, ?, ?)""",
                (
                    "task_provider_floor",
                    self.project["project_id"],
                    "workflow_provider_floor",
                    "polymarket:123456789:1d",
                    "provider-floor",
                    json.dumps(task_input),
                    "2026-07-31T00:00:00+00:00",
                    "2026-07-31T00:01:00+00:00",
                    json.dumps(task_output),
                ),
            )

        def resolve_current(requirement_set_id: str, **_kwargs):
            requirement = self.service.compiler.get(
                requirement_set_id
            ).requirements[0]
            resolved = Mock()
            resolved.to_dict.return_value = {
                "bindings": [{
                    "requirement_id": requirement.requirement_id,
                    "instrument_id": instrument_id,
                    "dataset_id": "polymarket-history",
                    "manifest_id": "manifest-polymarket-history",
                    "source": "POLYMARKET",
                    "range": {
                        "start": "2026-01-05T00:00:20+00:00",
                        "end": "2026-07-31T00:00:00+00:00",
                    },
                }],
                "checks": [],
            }
            return resolved

        with patch.object(
            DeterministicManifestResolver,
            "resolve",
            side_effect=resolve_current,
        ):
            status = self.service.data_status(self.project["project_id"])

        row = status["rows"][0]
        self.assertEqual("READY", row["status"])
        self.assertEqual(
            "2026-01-05T00:00:20+00:00",
            row["required_range"]["start"],
        )
        self.assertEqual(
            "2025-07-26",
            self.service.get_library_asset(item["library_asset_id"])["spec"]["time"]["start"],
        )
        project_item = self.service.get_project_item(
            self.project["project_id"],
            item["ref_id"],
        )
        self.assertTrue(project_item["overrides"]["availability_starts"])
        self.assertEqual(
            "2026-01-05T00:00:20+00:00",
            row["automatic_adjustments"][0]["available_from"],
        )

    def test_latest_requirement_rejects_a_stale_ready_binding(self) -> None:
        spec = default_requirement_spec("Moving latest")
        spec["scope"]["instruments"]["include"] = ["BTCUSDT"]
        self.service.create_research_requirement(self.project["project_id"], {"spec": spec})
        requirement_set = self.service.compiler.get(
            self.service.compile_project(self.project["project_id"])["requirement_set_id"]
        )
        requirement = requirement_set.requirements[0]
        resolution = Mock()
        resolution.to_dict.return_value = {
            "bindings": [{
                "requirement_id": requirement.requirement_id,
                "instrument_id": "crypto_spot:BINANCE:BTCUSDT",
                "dataset_id": "stale-bars",
                "manifest_id": "stale-manifest",
                "range": {
                    "start": "2025-01-01T00:00:00+00:00",
                    "end": "2025-12-31T23:00:00+00:00",
                },
            }],
            "checks": [],
        }
        with patch.object(DeterministicManifestResolver, "resolve", return_value=resolution):
            status = self.service.data_status(self.project["project_id"])
        row = status["rows"][0]
        self.assertEqual("PREPARING", row["status"])
        self.assertEqual("PARTIAL", row["raw_status"])
        self.assertTrue(row["required_range"]["resolved_end"])
        self.assertIn("latest completed interval", row["reason"])

    def test_equity_latest_accepts_recent_provider_close_and_weekend_start(self) -> None:
        required_start = datetime(2025, 7, 26, tzinfo=timezone.utc)
        first_session = datetime(2025, 7, 28, tzinfo=timezone.utc)
        self.assertTrue(_manifest_covers_required_start(
            "equity:XNAS:TSLA", "1d", required_start, first_session,
        ))
        self.assertFalse(_manifest_covers_required_start(
            "equity:XNAS:TSLA", "1d",
            datetime(2025, 7, 25, tzinfo=timezone.utc),
            first_session,
        ))
        self.assertTrue(_manifest_covers_required_start(
            "equity:XNAS:AAPL", "1d",
            datetime(2021, 1, 1, tzinfo=timezone.utc),
            datetime(2021, 1, 4, tzinfo=timezone.utc),
        ))

        spec = default_requirement_spec("TSLA latest")
        spec["scope"].update({
            "provider": "YFINANCE", "gateway": "OPENBB", "market": "XNAS", "asset_type": "EQUITY",
            "instruments": {"type": "STATIC_LIST", "include": ["EQUITY:XNAS:TSLA"]},
        })
        spec["time"]["start"] = "2025-07-26"
        spec["data"].update({"dataset_type": "BARS", "frequency": "1d"})
        self.service.create_research_requirement(self.project["project_id"], {"spec": spec})
        requirement_set = self.service.compiler.get(
            ResearchLibraryService(self.store).get_requirement_ref(self.project["project_id"])["requirement_set_id"]
        )
        requirement = requirement_set.requirements[0]
        resolution = Mock()
        resolution.to_dict.return_value = {
            "bindings": [{
                "requirement_id": requirement.requirement_id,
                "instrument_id": "equity:XNAS:TSLA",
                "dataset_id": "openbb:yfinance:equity:XNAS:TSLA:bars:1d:splits_only",
                "manifest_id": "manifest_tsla",
                "range": {
                    "start": "2025-07-28T00:00:00+00:00",
                    "end": "2026-07-24T00:00:00+00:00",
                },
            }],
            "checks": [],
        }
        with (
            patch.object(DeterministicManifestResolver, "resolve", return_value=resolution),
            patch(
                "services.data_platform.requirement_workspace_service._latest_completed_time",
                return_value="2026-07-25T23:59:59.999000+00:00",
            ),
        ):
            row = self.service.data_status(self.project["project_id"])["rows"][0]
        self.assertEqual("READY", row["status"])
        self.assertEqual("READY", row["raw_status"])
        self.assertEqual("2026-07-24T00:00:00+00:00", row["required_range"]["resolved_end"])

    def test_data_status_preserves_openbb_provider_instead_of_venue(self) -> None:
        spec = default_requirement_spec("US equity daily")
        spec["scope"].update({
            "provider": "YFINANCE", "gateway": "OPENBB", "market": "XNAS", "asset_type": "EQUITY",
            "instruments": {"type": "STATIC_LIST", "include": ["EQUITY:XNAS:AAPL"]},
        })
        spec["data"].update({"dataset_type": "BARS", "frequency": "1d"})
        self.service.create_research_requirement(self.project["project_id"], {"spec": spec})
        with patch.object(ResearchDataCapabilityService, "can_prepare", return_value=True):
            row = self.service.data_status(self.project["project_id"])["rows"][0]
        self.assertEqual("YFINANCE", row["provider"])
        self.assertEqual("PREPARING", row["status"])
        self.assertTrue(row["can_prepare"])

    def test_library_mixed_binance_availability_is_an_error(self) -> None:
        spec = default_requirement_spec("Mixed availability")
        spec["scope"]["instruments"]["include"] = ["BTCUSDT", "ETCBNB"]
        binding = {
            "dataset_id": "binance:BTCUSDT:1h",
            "range": {"start": "2025-01-01T00:00:00+00:00", "end": "2099-01-01T00:00:00+00:00"},
        }

        def resolve_one(_resolver, _requirement, instrument_id, **_kwargs):
            return (binding, []) if instrument_id.endswith("BTCUSDT") else (None, [])

        with (
            patch.object(DeterministicManifestResolver, "_resolve_one", autospec=True, side_effect=resolve_one),
            patch.object(
                ResearchDataCapabilityService,
                "can_prepare",
                side_effect=lambda instrument_id, _data_type, _frequency: instrument_id.endswith("BTCUSDT"),
            ),
        ):
            status = self.service.library_data_status(spec)

        self.assertEqual("FAILED", status["status"])
        by_symbol = {row["instrument_id"].split(":")[-1]: row for row in status["rows"]}
        self.assertEqual("READY", by_symbol["BTCUSDT"]["status"])
        self.assertEqual("UNAVAILABLE", by_symbol["ETCBNB"]["status"])

    def _create_shared_universe(self, symbols: list[str]) -> tuple[SharedUniverseService, dict]:
        registry = InstrumentRegistry(self.store)
        instrument_ids = []
        for symbol in symbols:
            instrument_id = make_instrument_id("crypto_spot", "BINANCE", symbol)
            registry.register(Instrument(
                instrument_id=instrument_id,
                asset_class="crypto_spot",
                venue="BINANCE",
                market_type="SPOT",
                native_symbol=symbol,
            ), aliases=[("binance", symbol)])
            instrument_ids.append(instrument_id)
        service = SharedUniverseService(self.store)
        universe = service.create({
            "name": "Tracked Universe",
            "type": "instrument_set",
            "members": instrument_ids,
        }, project_id=self.project["project_id"])
        return service, universe

    def test_universe_target_stores_reference_and_auto_reconciles_new_members(self) -> None:
        universe_service, universe = self._create_shared_universe(["BTCUSDT", "ETHUSDT"])
        suggestion = self.service.suggest_for_universe(self.project["project_id"], universe["universe_id"])
        spec = suggestion["spec"]
        self.assertEqual("SPECIFIC_UNIVERSE", spec["target"]["scope"])
        self.assertEqual(universe["universe_id"], spec["target"]["universe_id"])
        self.assertEqual([], spec["scope"]["instruments"]["include"])

        item = self.service.create_research_requirement(self.project["project_id"], {"spec": spec})
        effective = self.service.compiler.get(
            ResearchLibraryService(self.store).get_requirement_ref(self.project["project_id"])["requirement_set_id"]
        )
        self.assertEqual(2, len(effective.requirements[0].instrument_ids))

        sol_id = make_instrument_id("crypto_spot", "BINANCE", "SOLUSDT")
        InstrumentRegistry(self.store).register(Instrument(
            instrument_id=sol_id,
            asset_class="crypto_spot",
            venue="BINANCE",
            market_type="SPOT",
            native_symbol="SOLUSDT",
        ), aliases=[("binance", "SOLUSDT")])
        updated = universe_service.update(
            universe["universe_id"],
            {
                **universe["definition"],
                "members": [*universe["definition"]["members"], sol_id],
            },
            expected_current_revision_id=universe["current_revision_id"],
            current_project_id=self.project["project_id"],
        )
        self.assertTrue(universe_service.list_project(self.project["project_id"])[0]["requirements_stale_at"])
        with patch(
            "services.data_platform.requirement_workspace_service.get_binance_spot_symbol_status",
            return_value={"status": "TRADING"},
        ):
            reconciled = self.service.reconcile_project(
                self.project["project_id"], universe_id=updated["universe_id"]
            )
        self.assertTrue(reconciled["auto_updated"])
        self.assertEqual([sol_id], reconciled["changes"]["added"])
        compiled = self.service.compiler.get(reconciled["requirement_set_id"])
        all_ids = {value for requirement in compiled.requirements for value in requirement.instrument_ids}
        self.assertIn(sol_id, all_ids)
        self.assertEqual(item["ref_id"], reconciled["requirement_ref_id"])
        self.assertFalse(universe_service.list_project(self.project["project_id"])[0]["requirements_stale_at"])

    def test_legacy_static_requirement_needs_attention_instead_of_creating_copy(self) -> None:
        _universe_service, universe = self._create_shared_universe(["BTCUSDT", "ETHUSDT"])
        legacy = default_requirement_spec("Legacy fixed members")
        legacy["scope"]["instruments"]["include"] = ["BTCUSDT"]
        item = self.service.create_research_requirement(self.project["project_id"], {"spec": legacy})
        result = self.service.reconcile_project(
            self.project["project_id"], universe_id=universe["universe_id"]
        )
        self.assertEqual("ATTENTION", result["status"])
        self.assertEqual(item["ref_id"], result["requirement_ref_id"])
        self.assertEqual(["crypto_spot:BINANCE:ETHUSDT"], result["changes"]["added"])
        self.assertIn("fixed Instrument list", result["reasons"][0])


if __name__ == "__main__":
    unittest.main()
