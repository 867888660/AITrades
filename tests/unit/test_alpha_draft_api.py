from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as app_module
from services.data_platform import (
    DataPlatformStore,
    DefinitionRegistry,
    ResearchControlPlane,
)


class AlphaDraftApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = DataPlatformStore(Path(self.temp.name) / "metadata.db")
        self.project = ResearchControlPlane(self.store).create_project(
            title="Alpha Draft API",
            objective="verify the local Alpha authoring contract",
        )
        self.factor = DefinitionRegistry(self.store).create(
            "FACTOR",
            {
                "name": "api_factor",
                "version": "1.0.0",
                "operator": "pct_change",
                "input_field": "close",
                "window": 2,
                "frequency": "1h",
            },
            state="VALIDATED",
            owner_project_id=self.project["project_id"],
            library_scope="PROJECT",
        )
        self.client = app_module.app.test_client()
        self.store_patch = patch(
            "app.get_default_store",
            return_value=self.store,
        )
        self.store_patch.start()

    def tearDown(self) -> None:
        self.store_patch.stop()
        self.temp.cleanup()

    def test_candidate_and_draft_crud_routes_use_alpha_contract(self) -> None:
        candidate_response = self.client.get(
            f"/api/research/projects/{self.project['project_id']}"
            "/alpha-factor-candidates"
        )
        self.assertEqual(200, candidate_response.status_code)
        candidates = candidate_response.get_json()["data"]
        self.assertEqual(
            [self.factor.definition_id],
            [item["definition_id"] for item in candidates["factors"]],
        )

        create_response = self.client.post(
            "/api/research/alpha-drafts",
            json={
                "owner_project_id": self.project["project_id"],
                "library_scope": "PROJECT",
                "client_draft_key": "ui:api:test",
                "document": {},
            },
        )
        self.assertEqual(
            201,
            create_response.status_code,
            create_response.get_json(),
        )
        draft = create_response.get_json()["data"]
        self.assertFalse(draft["validation"]["can_preview"])
        update_response = self.client.put(
            f"/api/research/alpha-drafts/{draft['draft_id']}",
            json={
                "expected_fingerprint": draft["draft_fingerprint"],
                "document": {
                    "identity": {
                        "name": "api_alpha",
                        "version": "1.0.0",
                    },
                },
            },
        )
        self.assertEqual(200, update_response.status_code)
        updated = update_response.get_json()["data"]
        stale = self.client.put(
            f"/api/research/alpha-drafts/{draft['draft_id']}",
            json={
                "expected_fingerprint": draft["draft_fingerprint"],
                "document": {},
            },
        )
        self.assertEqual(400, stale.status_code)
        discard = self.client.delete(
            f"/api/research/alpha-drafts/{draft['draft_id']}",
            json={
                "expected_fingerprint": updated["draft_fingerprint"],
            },
        )
        self.assertEqual(200, discard.status_code)
        self.assertEqual(
            "DISCARDED",
            discard.get_json()["data"]["state"],
        )

    def test_write_route_remains_local_only(self) -> None:
        response = self.client.post(
            "/api/research/alpha-drafts",
            json={
                "owner_project_id": self.project["project_id"],
                "document": {},
            },
            environ_base={"REMOTE_ADDR": "203.0.113.9"},
        )
        self.assertEqual(403, response.status_code)


if __name__ == "__main__":
    unittest.main()
