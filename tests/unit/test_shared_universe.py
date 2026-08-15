from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.data_platform import (
    DataPlatformStore,
    Instrument,
    InstrumentRegistry,
    ResearchControlPlane,
    SharedUniverseService,
    UniverseService,
    UniverseConflictError,
    UniverseResolutionError,
    UniverseSharedImpactError,
    make_instrument_id,
)


class SharedUniverseServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = DataPlatformStore(Path(self.temp.name) / "metadata.db")
        registry = InstrumentRegistry(self.store)
        self.instruments = []
        for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"):
            instrument_id = make_instrument_id("crypto_spot", "BINANCE", symbol)
            registry.register(
                Instrument(
                    instrument_id=instrument_id,
                    asset_class="crypto_spot",
                    venue="BINANCE",
                    market_type="SPOT",
                    native_symbol=symbol,
                ),
                aliases=[("binance", symbol)],
            )
            self.instruments.append(instrument_id)
        control = ResearchControlPlane(self.store)
        self.first = control.create_project(title="First", objective="Shared Universe", created_by="test")
        self.second = control.create_project(title="Second", objective="Shared Universe", created_by="test")
        self.service = SharedUniverseService(self.store)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_shared_edit_requires_confirmation_and_keeps_history(self) -> None:
        created = self.service.create(
            {"name": "Crypto Set", "type": "instrument_set", "members": self.instruments[:2]},
            project_id=self.first["project_id"],
        )
        self.service.bind(
            project_id=self.second["project_id"], universe_id=created["universe_id"], role="PRIMARY"
        )
        with self.assertRaises(UniverseSharedImpactError):
            self.service.update(
                created["universe_id"],
                {"name": "Crypto Set", "type": "instrument_set", "members": self.instruments[:3]},
                expected_current_revision_id=created["current_revision_id"],
                current_project_id=self.first["project_id"],
            )
        updated = self.service.update(
            created["universe_id"],
            {"name": "Crypto Set", "type": "instrument_set", "members": self.instruments[:3]},
            expected_current_revision_id=created["current_revision_id"],
            current_project_id=self.first["project_id"],
            confirm_shared=True,
        )
        self.assertEqual(created["universe_id"], updated["universe_id"])
        self.assertNotEqual(created["current_revision_id"], updated["current_revision_id"])
        self.assertEqual(3, updated["current_resolution"]["member_count"])
        self.assertEqual(2, len(self.service.history(created["universe_id"])))
        for binding in self.service.list_project(self.second["project_id"]):
            self.assertEqual(updated["current_revision_id"], binding["current_revision_id"])
            self.assertTrue(binding["requirements_stale_at"])
        with self.assertRaises(UniverseConflictError):
            self.service.update(
                created["universe_id"], updated["definition"],
                expected_current_revision_id=created["current_revision_id"], confirm_shared=True,
            )

    def test_copy_creates_isolated_identity_and_can_replace_primary(self) -> None:
        original = self.service.create(
            {"name": "Original", "type": "instrument_set", "members": self.instruments[:2]},
            project_id=self.first["project_id"],
        )
        copied = self.service.copy(
            original["universe_id"], name="Independent Copy", project_id=self.first["project_id"],
            replace_primary=True,
        )
        self.assertNotEqual(original["universe_id"], copied["universe_id"])
        bindings = self.service.list_project(self.first["project_id"])
        self.assertEqual([copied["universe_id"]], [item["universe_id"] for item in bindings])
        changed = self.service.update(
            copied["universe_id"],
            {"name": "Independent Copy", "type": "instrument_set", "members": self.instruments[:3]},
            expected_current_revision_id=copied["current_revision_id"],
            current_project_id=self.first["project_id"],
        )
        self.assertEqual(3, changed["current_resolution"]["member_count"])
        self.assertEqual(2, self.service.get(original["universe_id"])["current_resolution"]["member_count"])

    def test_legacy_project_owned_universe_is_bound_once_and_not_resurrected(self) -> None:
        legacy = UniverseService(self.store)
        definition = legacy.create_definition(
            name="Legacy BTC",
            version="1.0.0",
            universe_type="STATIC_LIST",
            parameters={"instrument_ids": [self.instruments[0]]},
            owner_project_id=self.first["project_id"],
            library_scope="PROJECT",
        )
        legacy.resolve_snapshot(
            universe_definition_id=definition.universe_definition_id,
            as_of_time="2026-08-01T00:00:00+00:00",
        )

        migrated = SharedUniverseService(self.store)
        bindings = migrated.list_project(self.first["project_id"])
        self.assertEqual(1, len(bindings))
        self.assertEqual("Legacy BTC", bindings[0]["name"])
        self.assertEqual("PRIMARY", bindings[0]["role"])
        self.assertEqual(1, bindings[0]["current_resolution"]["member_count"])

        migrated.remove_binding(
            project_id=self.first["project_id"], universe_id=bindings[0]["universe_id"]
        )
        self.assertEqual([], SharedUniverseService(self.store).list_project(self.first["project_id"]))

    def test_legacy_bare_instrument_is_preserved_without_registry_validation(self) -> None:
        self.assertEqual("AAPL", self.service._normalize_instrument("AAPL", validate=False))

    def test_composite_and_multi_leg_resolution(self) -> None:
        first = self.service.create({
            "name": "Set A", "type": "instrument_set", "members": self.instruments[:3],
        })
        second = self.service.create({
            "name": "Set B", "type": "instrument_set", "members": self.instruments[1:],
        })
        composite = self.service.create({
            "name": "Intersection",
            "type": "composite_set",
            "expression": {
                "operator": "intersection",
                "inputs": [{"universe_id": first["universe_id"]}, {"universe_id": second["universe_id"]}],
            },
        })
        self.assertEqual(self.instruments[1:3], composite["current_resolution"]["instrument_ids"])
        pairs = self.service.create({
            "name": "Pairs",
            "type": "multi_leg_set",
            "legs": [
                {"id": "leg_1", "source_universe_id": first["universe_id"]},
                {"id": "leg_2", "source_universe_id": first["universe_id"]},
            ],
            "combination": {
                "mode": "unordered_combination",
                "allow_same_instrument": False,
                "treat_reversed_as_same": True,
                "max_combinations": 10,
            },
        })
        self.assertEqual(3, pairs["current_resolution"]["combination_count"])
        self.assertEqual(3, pairs["current_resolution"]["member_count"])
        with self.assertRaises(UniverseResolutionError) as raised:
            self.service.preview({
                **pairs["definition"],
                "name": "Too Many Pairs",
                "combination": {**pairs["definition"]["combination"], "max_combinations": 2},
            })
        self.assertEqual("UNIVERSE_COMBINATION_LIMIT_EXCEEDED", raised.exception.code)

    def test_benchmark_set_freezes_normalized_weights_and_effective_date(self) -> None:
        benchmark = self.service.create({
            "name": "CSI300 Sample",
            "type": "benchmark_set",
            "benchmark": {
                "benchmark_id": "000300.SH",
                "provider": "OPENBB",
                "effective_at": "2026-06-15",
            },
            "constituents": [
                {"instrument_id": self.instruments[0], "weight": 2},
                {"instrument_id": self.instruments[1], "weight": 1},
            ],
        })
        resolution = benchmark["current_resolution"]
        self.assertEqual("benchmark_set", benchmark["type"])
        self.assertAlmostEqual(1.0, sum(resolution["instrument_weights"].values()))
        self.assertAlmostEqual(2 / 3, resolution["instrument_weights"][self.instruments[0]])
        self.assertEqual(
            "2026-06-15",
            resolution["metadata"]["benchmark"]["effective_at"],
        )
        legacy_snapshot = self.service.legacy.get_snapshot(
            resolution["legacy_snapshot_id"]
        )
        self.assertEqual(
            resolution["instrument_weights"],
            legacy_snapshot.selection_inputs["instrument_weights"],
        )

    def test_yaml_round_trip_preserves_extensions(self) -> None:
        definition = {
            "name": "Script Set", "description": "Round trip", "type": "instrument_set",
            "members": self.instruments[:2], "extensions": {"custom_policy": "keep"},
        }
        script = self.service.render_script(definition)
        parsed = self.service.parse_script(script)
        self.assertEqual(definition["extensions"], parsed["extensions"])
        self.assertEqual(self.instruments[:2], parsed["members"])

    def test_manual_groups_do_not_require_source_universes(self) -> None:
        manual = self.service.create({
            "name": "Manual Pairs",
            "type": "multi_leg_set",
            "legs": [{"id": "leg_1", "name": "Leg 1"}, {"id": "leg_2", "name": "Leg 2"}],
            "combination": {
                "mode": "manual",
                "allow_same_instrument": False,
                "treat_reversed_as_same": False,
                "max_combinations": 10,
            },
            "manual_tuples": [self.instruments[:2], [self.instruments[0], self.instruments[2]]],
        })
        self.assertEqual(2, manual["current_resolution"]["combination_count"])
        self.assertEqual(3, manual["current_resolution"]["member_count"])
        self.assertNotIn("source_universe_id", manual["definition"]["legs"][0])

    def test_ordered_groups_preserve_direction_and_support_repetition(self) -> None:
        source = self.service.create({
            "name": "Ordered Source", "type": "instrument_set", "members": self.instruments[:2],
        })
        base = {
            "name": "Ordered Pairs",
            "type": "multi_leg_set",
            "legs": [
                {"id": "leg_1", "source_universe_id": source["universe_id"]},
                {"id": "leg_2", "source_universe_id": source["universe_id"]},
            ],
            "combination": {
                "mode": "permutation",
                "allow_same_instrument": False,
                "treat_reversed_as_same": False,
                "max_combinations": 10,
            },
        }
        ordered = self.service.create(base)
        self.assertEqual(2, ordered["current_resolution"]["combination_count"])
        repeated = self.service.preview({
            **base,
            "name": "Ordered Pairs With Repetition",
            "combination": {**base["combination"], "allow_same_instrument": True},
        })
        self.assertEqual(4, repeated["combination_count"])


if __name__ == "__main__":
    unittest.main()
