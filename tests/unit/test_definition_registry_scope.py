from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.data_platform import (
    DataPlatformStore,
    DefinitionRegistry,
    ResearchControlPlane,
)


def factor_spec(*, name: str = "momentum_12_1", window: int = 12) -> dict:
    return {
        "name": name,
        "version": "experiment.same-candidate",
        "operator": "pct_change",
        "input_field": "close",
        "window": window,
        "frequency": "1d",
    }


class DefinitionRegistryScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "metadata.db"
        self.store = DataPlatformStore(self.path)
        control = ResearchControlPlane(self.store)
        self.first = control.create_project(title="First", objective="first")
        self.second = control.create_project(title="Second", objective="second")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_project_identity_is_idempotent_locally_and_isolated_across_projects(self) -> None:
        registry = DefinitionRegistry(self.store)
        first = registry.create(
            "FACTOR",
            factor_spec(),
            state="VALIDATED",
            owner_project_id=self.first["project_id"],
            library_scope="PROJECT",
        )
        repeated = registry.create(
            "FACTOR",
            factor_spec(),
            state="VALIDATED",
            owner_project_id=self.first["project_id"],
            library_scope="PROJECT",
        )
        second = registry.create(
            "FACTOR",
            factor_spec(),
            state="VALIDATED",
            owner_project_id=self.second["project_id"],
            library_scope="PROJECT",
        )

        self.assertEqual(first.definition_id, repeated.definition_id)
        self.assertNotEqual(first.definition_id, second.definition_id)
        self.assertEqual(self.first["project_id"], first.owner_project_id)
        self.assertEqual(self.second["project_id"], second.owner_project_id)
        registry.set_project_ref(
            project_id=self.first["project_id"],
            slot_key="factor:researcher_candidate",
            definition_id=first.definition_id,
            definition_version=first.version,
            reference_mode="PINNED",
        )
        registry.set_project_ref(
            project_id=self.second["project_id"],
            slot_key="factor:researcher_candidate",
            definition_id=second.definition_id,
            definition_version=second.version,
            reference_mode="PINNED",
        )

        with self.assertRaisesRegex(ValueError, "immutable definition version"):
            registry.create(
                "FACTOR",
                factor_spec(window=20),
                state="VALIDATED",
                owner_project_id=self.second["project_id"],
                library_scope="PROJECT",
            )

    def test_matching_global_is_reused_but_different_global_spec_does_not_block_project(self) -> None:
        registry = DefinitionRegistry(self.store)
        shared = registry.create(
            "FACTOR",
            factor_spec(name="shared_factor"),
            state="VALIDATED",
            library_scope="GLOBAL",
        )
        reused = registry.create(
            "FACTOR",
            factor_spec(name="shared_factor"),
            state="VALIDATED",
            owner_project_id=self.first["project_id"],
            library_scope="PROJECT",
        )
        private = registry.create(
            "FACTOR",
            factor_spec(name="shared_factor", window=20),
            state="VALIDATED",
            owner_project_id=self.second["project_id"],
            library_scope="PROJECT",
        )

        self.assertEqual(shared.definition_id, reused.definition_id)
        self.assertNotEqual(shared.definition_id, private.definition_id)
        self.assertEqual("PROJECT", private.library_scope)
        self.assertEqual(self.second["project_id"], private.owner_project_id)

    def test_scope_migration_preserves_definitions_and_project_refs(self) -> None:
        registry = DefinitionRegistry(self.store)
        definition = registry.create(
            "FACTOR",
            factor_spec(name="preserved_factor"),
            state="VALIDATED",
            owner_project_id=self.first["project_id"],
            library_scope="PROJECT",
        )
        registry.set_project_ref(
            project_id=self.first["project_id"],
            slot_key="factor:preserved",
            definition_id=definition.definition_id,
            definition_version=definition.version,
            reference_mode="PINNED",
        )
        with self.store.transaction(immediate=True) as conn:
            conn.execute("DELETE FROM schema_migrations WHERE migration_version=30")

        migrated = DataPlatformStore(self.path)
        migrated_registry = DefinitionRegistry(migrated)
        preserved = migrated_registry.get(definition.definition_id)
        refs = migrated_registry.list_project_refs(self.first["project_id"])

        self.assertIsNotNone(preserved)
        self.assertEqual(self.first["project_id"], preserved.owner_project_id)
        self.assertEqual(definition.definition_id, refs["factor:preserved"]["definition_id"])
        with migrated.connection() as conn:
            migration = conn.execute(
                "SELECT migration_name FROM schema_migrations WHERE migration_version=30"
            ).fetchone()
            indexes = {
                str(row["name"])
                for row in conn.execute("PRAGMA index_list(research_definitions)").fetchall()
            }
        self.assertEqual("project_scoped_definition_identity", migration["migration_name"])
        self.assertIn("uq_research_definitions_global_identity", indexes)
        self.assertIn("uq_research_definitions_project_identity", indexes)


if __name__ == "__main__":
    unittest.main()
