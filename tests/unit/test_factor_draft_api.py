from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import app as app_module
from services.data_platform import (
    CanonicalBarsCommitter,
    DataPlatformStore,
    ResearchControlPlane,
    UniverseService,
)


class FactorDraftApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = DataPlatformStore(Path(self.temp.name) / "metadata.db")
        self.client = app_module.app.test_client()
        self.store_patch = patch("app.get_default_store", return_value=self.store)
        self.store_patch.start()
        self.project = ResearchControlPlane(self.store).create_project(
            title="Factor API Preview",
            objective="verify required Preview lifecycle",
        )
        self.instrument_id = "crypto_spot:BINANCE:BTCUSDT"
        self.base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        rows = []
        for index in range(32):
            start = self.base + timedelta(hours=index)
            end = start + timedelta(hours=1)
            rows.append({
                "instrument_id": self.instrument_id,
                "frequency": "1h",
                "bar_start_time": start.isoformat(),
                "bar_end_time": end.isoformat(),
                "available_time": end.isoformat(),
                "ingested_at": (self.base + timedelta(days=3)).isoformat(),
                "open": 100.0 + index,
                "high": 101.0 + index,
                "low": 99.0 + index,
                "close": 100.0 + index,
                "volume": 10.0,
                "turnover": 1000.0 + index,
                "trade_count": 2,
                "bar_status": "COMPLETE",
                "source": "BINANCE",
                "source_version": "1",
                "quality_status": "PASS",
            })
        CanonicalBarsCommitter(self.store, Path(self.temp.name) / "bars").commit(
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
            as_of_time=(self.base + timedelta(hours=32)).isoformat(),
        )
        UniverseService(self.store).set_research_ref(
            project_id=self.project["project_id"],
            universe_snapshot_id=self.snapshot.universe_snapshot_id,
        )

    def tearDown(self) -> None:
        self.store_patch.stop()
        self.temp.cleanup()

    @staticmethod
    def document() -> dict:
        return {
            "schema_version": "factor_draft.v2",
            "identity": {"name": "momentum_20", "version": "1.0.0"},
            "inputs": [{
                "variable_name": "price",
                "dataset": "bars",
                "field": "close",
                "frequency": "1h",
            }],
            "parameters": [{"name": "window", "value": 20, "unit": "bars"}],
            "formula": {"source": "pct_change(price, window)"},
            "output": {"unit": "RATIO", "direction": "HIGHER_IS_BETTER"},
        }

    def test_local_ui_backup_and_save_factor_lifecycle(self) -> None:
        created_response = self.client.post(
            "/api/research/factor-drafts",
            json={
                "document": {"identity": {"name": "unfinished"}},
                "owner_project_id": self.project["project_id"],
                "library_scope": "PROJECT",
            },
        )
        self.assertEqual(201, created_response.status_code, created_response.get_json())
        created = created_response.get_json()["data"]
        self.assertEqual("DRAFT", created["state"])
        self.assertFalse(created["validation"]["can_validate"])

        updated_response = self.client.put(
            f"/api/research/factor-drafts/{created['draft_id']}",
            json={
                "document": self.document(),
                "expected_fingerprint": created["draft_fingerprint"],
            },
        )
        self.assertEqual(200, updated_response.status_code, updated_response.get_json())
        updated = updated_response.get_json()["data"]
        self.assertTrue(updated["validation"]["can_compile"])
        self.assertFalse(updated["validation"]["can_validate"])
        self.assertTrue(updated["validation"]["can_save_factor"])
        self.assertTrue(updated["validation"]["preview_required"])
        self.assertEqual(
            "close",
            updated["validation"]["compiled_factor_spec"]["formula"]["input"],
        )

        candidates_response = self.client.get(
            f"/api/research/projects/{self.project['project_id']}/factor-input-candidates"
        )
        self.assertEqual(200, candidates_response.status_code, candidates_response.get_json())
        candidates = candidates_response.get_json()["data"]
        selected = next(
            item for item in candidates["input_candidates"]
            if item["candidate_id"] == "bars.close:1h"
        )
        self.assertTrue(selected["factor_selectable"])
        self.assertEqual(1, selected["requestable_instrument_count"])
        self.assertEqual(1, selected["prepared_instrument_count"])

        requirement_response = self.client.post(
            f"/api/research/factor-drafts/{created['draft_id']}/requirements",
            json={
                "expected_fingerprint": updated["draft_fingerprint"],
                "universe_snapshot_id": self.snapshot.universe_snapshot_id,
                "start_time": (self.base + timedelta(hours=21)).isoformat(),
                "end_time": (self.base + timedelta(hours=31)).isoformat(),
            },
        )
        self.assertEqual(201, requirement_response.status_code, requirement_response.get_json())
        requirement_result = requirement_response.get_json()["data"]
        self.assertEqual(1, len(requirement_result["requirements"]))
        requirement = requirement_result["requirements"][0]
        self.assertEqual(["close"], requirement["fields"])
        self.assertEqual([self.instrument_id], requirement["instrument_ids"])
        self.assertEqual("1h", requirement["frequency"])
        self.assertEqual(
            (self.base + timedelta(hours=21)).isoformat(),
            requirement["evaluation_range"]["start"],
        )
        self.assertEqual(
            (self.base + timedelta(hours=1)).isoformat(),
            requirement["required_range"]["start"],
        )
        self.assertEqual(20, requirement["additional_history"]["observations"])
        self.assertEqual("READY", requirement_result["data_status"]["rows"][0]["status"])

        context_response = self.client.get(
            f"/api/research/factor-drafts/{created['draft_id']}/preview-context"
        )
        self.assertEqual(200, context_response.status_code, context_response.get_json())
        context = context_response.get_json()["data"]
        self.assertTrue(context["can_run_preview"], context["diagnostics"])
        preview_response = self.client.post(
            f"/api/research/factor-drafts/{created['draft_id']}/previews",
            json={
                "expected_fingerprint": updated["draft_fingerprint"],
                "universe_snapshot_id": self.snapshot.universe_snapshot_id,
                "start_time": (self.base + timedelta(hours=21)).isoformat(),
                "end_time": (self.base + timedelta(hours=31)).isoformat(),
            },
        )
        self.assertEqual(201, preview_response.status_code, preview_response.get_json())
        preview = preview_response.get_json()["data"]
        self.assertEqual("READY", preview["status"])
        self.assertGreater(preview["analysis"]["overall"]["valid_value_count"], 0)

        validated_response = self.client.post(
            f"/api/research/factor-drafts/{created['draft_id']}/validate",
            json={
                "expected_fingerprint": updated["draft_fingerprint"],
                "preview_id": preview["preview_id"],
                "preview_fingerprint": preview["preview_fingerprint"],
            },
        )
        self.assertEqual(200, validated_response.status_code, validated_response.get_json())
        validated = validated_response.get_json()["data"]
        self.assertEqual("VALIDATED", validated["draft"]["state"])
        self.assertEqual("VALIDATED", validated["definition"]["state"])
        self.assertEqual(
            validated["definition"]["definition_id"],
            validated["draft"]["validated_definition_id"],
        )
        self.assertEqual("FACTOR", validated["library_asset"]["component_type"])
        self.assertEqual(
            validated["definition"]["definition_id"],
            validated["library_asset"]["source_object_id"],
        )
        self.assertEqual(
            "factor:momentum_20",
            validated["project_reference"]["slot_key"],
        )
        self.assertEqual(
            validated["definition"]["definition_id"],
            validated["project_reference"]["definition_id"],
        )
        factor_assets = self.client.get(
            "/api/research/library?component_type=FACTOR"
        ).get_json()["data"]
        self.assertEqual(
            [validated["library_asset"]["library_asset_id"]],
            [item["library_asset_id"] for item in factor_assets],
        )
        rejected_discard = self.client.delete(
            f"/api/research/factor-drafts/{created['draft_id']}",
            json={"expected_fingerprint": validated["draft"]["draft_fingerprint"]},
        )
        self.assertEqual(400, rejected_discard.status_code)
        self.assertIn("validated Factors cannot be discarded", rejected_discard.get_json()["error"])

        removed_response = self.client.delete(
            f"/api/research/projects/{self.project['project_id']}/definition-refs/factor:momentum_20",
            json={"expected_definition_id": validated["definition"]["definition_id"]},
        )
        self.assertEqual(200, removed_response.status_code, removed_response.get_json())
        removed = removed_response.get_json()["data"]
        self.assertTrue(removed["removed"])
        self.assertTrue(removed["history_preserved"])
        refs = self.client.get(
            f"/api/research/projects/{self.project['project_id']}/definition-refs"
        ).get_json()["data"]
        self.assertNotIn("factor:momentum_20", refs)
        library_after_removal = self.client.get(
            "/api/research/library?component_type=FACTOR"
        ).get_json()["data"]
        self.assertEqual(
            [validated["library_asset"]["library_asset_id"]],
            [item["library_asset_id"] for item in library_after_removal],
        )

        listed = self.client.get("/api/research/factor-drafts").get_json()["data"]
        self.assertEqual([created["draft_id"]], [item["draft_id"] for item in listed])

    def test_validate_rejects_stale_browser_state(self) -> None:
        created = self.client.post(
            "/api/research/factor-drafts",
            json={"document": self.document()},
        ).get_json()["data"]
        changed = self.document()
        changed["parameters"][0]["value"] = 40
        updated = self.client.put(
            f"/api/research/factor-drafts/{created['draft_id']}",
            json={
                "document": changed,
                "expected_fingerprint": created["draft_fingerprint"],
            },
        ).get_json()["data"]

        stale = self.client.post(
            f"/api/research/factor-drafts/{created['draft_id']}/validate",
            json={"expected_fingerprint": created["draft_fingerprint"]},
        )
        self.assertEqual(400, stale.status_code)
        self.assertIn("FACTOR_DRAFT_STALE", stale.get_json()["error"])
        self.assertNotEqual(created["draft_fingerprint"], updated["draft_fingerprint"])

    def test_unsaved_document_can_be_checked_without_persistence(self) -> None:
        invalid = self.client.post(
            "/api/research/factor-drafts/validation",
            json={"document": {"identity": {"name": "unfinished"}}},
        )
        self.assertEqual(200, invalid.status_code, invalid.get_json())
        self.assertFalse(invalid.get_json()["data"]["can_validate"])

        valid = self.client.post(
            "/api/research/factor-drafts/validation",
            json={"document": self.document()},
        )
        self.assertEqual(200, valid.status_code, valid.get_json())
        self.assertTrue(valid.get_json()["data"]["can_compile"])
        self.assertFalse(valid.get_json()["data"]["can_validate"])
        self.assertTrue(valid.get_json()["data"]["can_save_factor"])
        self.assertTrue(valid.get_json()["data"]["preview_required"])
        self.assertEqual([], self.client.get("/api/research/factor-drafts").get_json()["data"])

    def test_local_ui_can_discard_only_the_current_draft_revision(self) -> None:
        created = self.client.post(
            "/api/research/factor-drafts",
            json={
                "document": self.document(),
                "owner_project_id": self.project["project_id"],
                "library_scope": "PROJECT",
            },
        ).get_json()["data"]

        missing_fingerprint = self.client.delete(
            f"/api/research/factor-drafts/{created['draft_id']}",
            json={},
        )
        self.assertEqual(400, missing_fingerprint.status_code)
        stale = self.client.delete(
            f"/api/research/factor-drafts/{created['draft_id']}",
            json={"expected_fingerprint": "stale"},
        )
        self.assertEqual(400, stale.status_code)
        self.assertIn("FACTOR_DRAFT_STALE", stale.get_json()["error"])

        response = self.client.delete(
            f"/api/research/factor-drafts/{created['draft_id']}",
            json={"expected_fingerprint": created["draft_fingerprint"]},
        )

        self.assertEqual(200, response.status_code, response.get_json())
        self.assertTrue(response.get_json()["data"]["discarded"])
        self.assertEqual("DISCARDED", response.get_json()["data"]["state"])
        visible = self.client.get(
            f"/api/research/factor-drafts?owner_project_id={self.project['project_id']}"
        ).get_json()["data"]
        self.assertEqual([], visible)
        discarded = self.client.get(
            f"/api/research/factor-drafts?owner_project_id={self.project['project_id']}&state=DISCARDED"
        ).get_json()["data"]
        self.assertEqual([created["draft_id"]], [item["draft_id"] for item in discarded])


if __name__ == "__main__":
    unittest.main()
