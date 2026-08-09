from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.data_platform import (
    AlphaFactorCandidateResolver,
    DataPlatformStore,
    DefinitionRegistry,
    ResearchControlPlane,
)


def factor_spec(name: str, version: str = "1.0.0") -> dict:
    return {
        "name": name,
        "version": version,
        "operator": "pct_change",
        "input_field": "close",
        "window": 2,
        "frequency": "1h",
        "output_unit": "RATIO",
        "output_direction": "HIGHER_IS_BETTER",
    }


class AlphaFactorCandidateResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = DataPlatformStore(Path(self.temp.name) / "metadata.db")
        control = ResearchControlPlane(self.store)
        self.project = control.create_project(
            title="Alpha candidates",
            objective="resolve only accessible validated Factors",
        )
        self.other_project = control.create_project(
            title="Other research",
            objective="must remain isolated",
        )
        self.registry = DefinitionRegistry(self.store)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_resolve_includes_owned_and_pinned_but_excludes_other_project(self) -> None:
        owned = self.registry.create(
            "FACTOR",
            factor_spec("owned"),
            state="VALIDATED",
            owner_project_id=self.project["project_id"],
            library_scope="PROJECT",
        )
        hidden = self.registry.create(
            "FACTOR",
            factor_spec("hidden"),
            state="VALIDATED",
            owner_project_id=self.other_project["project_id"],
            library_scope="PROJECT",
        )
        global_factor = self.registry.create(
            "FACTOR",
            factor_spec("library"),
            state="VALIDATED",
            library_scope="GLOBAL",
        )
        self.registry.set_project_ref(
            project_id=self.project["project_id"],
            slot_key="factor:library",
            definition_id=global_factor.definition_id,
            definition_version=global_factor.version,
            reference_mode="PINNED",
        )

        result = AlphaFactorCandidateResolver(self.store).resolve(
            self.project["project_id"]
        )
        ids = {item["definition_id"] for item in result["factors"]}
        self.assertEqual(
            {owned.definition_id, global_factor.definition_id},
            ids,
        )
        self.assertNotIn(hidden.definition_id, ids)
        self.assertEqual(8, result["maximum_components"])
        self.assertEqual(64, len(result["candidate_fingerprint"]))

    def test_assert_rechecks_exact_version_and_access(self) -> None:
        hidden = self.registry.create(
            "FACTOR",
            factor_spec("private"),
            state="VALIDATED",
            owner_project_id=self.other_project["project_id"],
            library_scope="PROJECT",
        )
        resolver = AlphaFactorCandidateResolver(self.store)
        with self.assertRaisesRegex(ValueError, "ALPHA_FACTOR_NOT_ACCESSIBLE"):
            resolver.assert_components_accessible(
                self.project["project_id"],
                [{
                    "factor_definition_id": hidden.definition_id,
                    "factor_version": hidden.version,
                }],
            )


if __name__ == "__main__":
    unittest.main()
