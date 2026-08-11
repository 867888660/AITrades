from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import app as app_module
from services.data_platform import DataPlatformStore


class ResearchAgentAutonomyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = DataPlatformStore(Path(self.temp.name) / "metadata.db")
        self.client = app_module.app.test_client()
        self.store_patch = patch("app.get_default_store", return_value=self.store)
        self.store_patch.start()

    def tearDown(self):
        self.store_patch.stop()
        self.temp.cleanup()

    def create_project(self) -> dict:
        response = self.client.post(
            "/api/agent/research/projects",
            json={"title": "BTC crossover research", "objective": "Evaluate BTC golden and death crosses."},
        )
        self.assertEqual(201, response.status_code, response.get_json())
        return response.get_json()["data"]

    def grant_project(self, project_id: str) -> dict:
        response = self.client.post(
            f"/api/research/projects/{project_id}/run-grants",
            json={
                "objective": "Allow autonomous BTC factor research inside strict scope.",
                "autonomy_level": "AUTONOMOUS",
                "allowed_operations": [
                    "UNIVERSE_CREATE", "UNIVERSE_SNAPSHOT_CREATE",
                    "FACTOR_CREATE", "FACTOR_VALIDATE", "ALPHA_CREATE", "ALPHA_VALIDATE",
                    "PROJECT_PIN", "REQUIREMENT_COMPILE", "COVERAGE_CHECK",
                    "BACKFILL_CREATE", "PREVIEW_CREATE", "RUN_CREATE", "RUN_EXECUTE",
                ],
                "allowed_providers": ["BINANCE"],
                "allowed_instrument_ids": ["crypto_spot:BINANCE:BTCUSDT"],
                "allowed_intervals": ["1h"],
                "time_start": "2026-01-01T00:00:00+00:00",
                "time_end": "2026-12-31T23:59:59+00:00",
                "allow_project_pin": True,
                "allowed_run_types": ["FACTOR_EVALUATION"],
                "budgets": {
                    "max_backtest_runs": 3,
                    "max_download_bytes": 0,
                    "max_runtime_seconds": 600,
                },
                "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).replace(
                    microsecond=0
                ).isoformat(),
            },
        )
        self.assertEqual(201, response.status_code, response.get_json())
        return response.get_json()["data"]

    def test_one_human_grant_allows_project_scoped_research_objects(self):
        project = self.create_project()
        project_id = project["project_id"]

        blocked = self.client.post(
            f"/api/agent/research/projects/{project_id}/definitions",
            json={"definition_type": "FACTOR", "spec": {"name": "blocked", "version": "1.0.0"}},
        )
        self.assertEqual(403, blocked.status_code)
        self.assertEqual("RESEARCH_SESSION_REQUIRED", blocked.get_json()["code"])

        grant = self.grant_project(project_id)
        universe_response = self.client.post(
            f"/api/agent/research/projects/{project_id}/universes",
            json={
                "grant_id": grant["grant_id"],
                "name": "btc_spot",
                "version": "1.0.0",
                "universe_type": "STATIC_LIST",
                "providers": ["BINANCE"],
                "parameters": {"instrument_ids": ["crypto_spot:BINANCE:BTCUSDT"]},
            },
        )
        self.assertEqual(201, universe_response.status_code, universe_response.get_json())
        universe = universe_response.get_json()["data"]
        self.assertEqual("PROJECT", universe["library_scope"])
        self.assertEqual(project_id, universe["owner_project_id"])

        factor_response = self.client.post(
            f"/api/agent/research/projects/{project_id}/definitions",
            json={
                "grant_id": grant["grant_id"],
                "definition_type": "FACTOR",
                "spec": {
                    "name": "btc_ma_cross",
                    "version": "1.0.0",
                    "operator": "ma_crossover",
                    "input_field": "close",
                    "window": 20,
                    "parameters": {"fast_window": 5},
                    "frequency": "1h",
                },
            },
        )
        self.assertEqual(201, factor_response.status_code, factor_response.get_json())
        factor = factor_response.get_json()["data"]
        self.assertEqual("PROJECT", factor["library_scope"])

        validated_response = self.client.post(
            f"/api/agent/research/projects/{project_id}/definitions/{factor['definition_id']}/validate",
            json={"grant_id": grant["grant_id"]},
        )
        self.assertEqual(200, validated_response.status_code, validated_response.get_json())
        self.assertEqual("VALIDATED", validated_response.get_json()["data"]["state"])

        pin_response = self.client.put(
            f"/api/agent/research/projects/{project_id}/definition-refs/factor:btc_ma_cross",
            json={
                "grant_id": grant["grant_id"],
                "definition_id": factor["definition_id"],
                "definition_version": factor["version"],
                "reference_mode": "PINNED",
            },
        )
        self.assertEqual(200, pin_response.status_code, pin_response.get_json())

        requirement_response = self.client.post(
            f"/api/agent/research/projects/{project_id}/requirement-sets",
            json={
                "grant_id": grant["grant_id"],
                "providers": ["BINANCE"],
                "context": {
                    "instrument_ids": ["crypto_spot:BINANCE:BTCUSDT"],
                    "data_type": "bars",
                    "frequency": "1h",
                    "history_start": "2026-04-01T00:00:00+00:00",
                    "history_end": "2026-07-01T00:00:00+00:00",
                    "adjustment": "NONE",
                    "point_in_time_policy": "AS_OF",
                },
                "factor_specs": [factor["spec"]],
            },
        )
        self.assertEqual(201, requirement_response.status_code, requirement_response.get_json())

        backfill_response = self.client.post(
            f"/api/agent/research/projects/{project_id}/backfill-tasks",
            json={
                "grant_id": grant["grant_id"],
                "symbol": "BTCUSDT",
                "instrument_id": "crypto_spot:BINANCE:BTCUSDT",
                "interval": "1h",
                "start_time": "2026-04-01T00:00:00+00:00",
                "end_time": "2026-04-02T00:00:00+00:00",
                "idempotency_key": "btc-backfill-test",
                "budget": {"download_bytes": 0, "runtime_seconds": 60},
            },
        )
        self.assertEqual(201, backfill_response.status_code, backfill_response.get_json())
        self.assertEqual("READY", backfill_response.get_json()["data"]["status"])

        visible_projects = self.client.get("/api/research/projects").get_json()["data"]
        visible_definitions = self.client.get("/api/research/definitions").get_json()["data"]
        visible_universes = self.client.get("/api/research/universes?status=").get_json()["data"]
        self.assertIn(project_id, {item["project_id"] for item in visible_projects})
        self.assertIn(factor["definition_id"], {item["definition_id"] for item in visible_definitions})
        self.assertIn(universe["universe_definition_id"], {
            item["universe_definition_id"] for item in visible_universes
        })

    def test_pause_blocks_agent_without_mutating_grant_scope(self):
        project_id = self.create_project()["project_id"]
        grant = self.grant_project(project_id)
        pause = self.client.post(
            f"/api/research/projects/{project_id}/run-grants/{grant['grant_id']}/agent-state",
            json={"paused": True},
        )
        self.assertEqual(200, pause.status_code, pause.get_json())
        self.assertEqual("PAUSED", pause.get_json()["data"]["status"])

        blocked = self.client.post(
            f"/api/agent/research/projects/{project_id}/definitions",
            json={
                "grant_id": grant["grant_id"],
                "definition_type": "FACTOR",
                "spec": {
                    "name": "btc_return",
                    "version": "1.0.0",
                    "operator": "pct_change",
                    "input_field": "close",
                    "window": 1,
                },
            },
        )
        self.assertEqual(403, blocked.status_code)
        self.assertEqual("RESEARCH_AGENT_PAUSED", blocked.get_json()["code"])

        resume = self.client.post(
            f"/api/research/projects/{project_id}/run-grants/{grant['grant_id']}/agent-state",
            json={"paused": False},
        )
        self.assertEqual(200, resume.status_code, resume.get_json())
        self.assertEqual("ACTIVE", resume.get_json()["data"]["status"])

    def test_provider_and_universe_scope_are_enforced(self):
        project_id = self.create_project()["project_id"]
        grant = self.grant_project(project_id)
        response = self.client.post(
            f"/api/agent/research/projects/{project_id}/universes",
            json={
                "grant_id": grant["grant_id"],
                "name": "eth_out_of_scope",
                "version": "1.0.0",
                "universe_type": "STATIC_LIST",
                "providers": ["KRAKEN"],
                "parameters": {"instrument_ids": ["crypto_spot:KRAKEN:ETHUSD"]},
            },
        )
        self.assertEqual(403, response.status_code)
        self.assertEqual("RESEARCH_PROVIDER_OUT_OF_SCOPE", response.get_json()["code"])

    def _create_validated_factor(self, project_id: str, grant: dict) -> dict:
        factor_response = self.client.post(
            f"/api/agent/research/projects/{project_id}/definitions",
            json={
                "grant_id": grant["grant_id"],
                "definition_type": "FACTOR",
                "spec": {
                    "name": "btc_ma_cross_alpha_input",
                    "version": "1.0.0",
                    "operator": "ma_crossover",
                    "input_field": "close",
                    "window": 20,
                    "parameters": {"fast_window": 5},
                    "frequency": "1h",
                },
            },
        )
        self.assertEqual(201, factor_response.status_code, factor_response.get_json())
        factor = factor_response.get_json()["data"]
        validated = self.client.post(
            f"/api/agent/research/projects/{project_id}/definitions/{factor['definition_id']}/validate",
            json={"grant_id": grant["grant_id"]},
        )
        self.assertEqual(200, validated.status_code, validated.get_json())
        return validated.get_json()["data"]

    def test_alpha_component_missing_factor_version_returns_400_not_403(self):
        project_id = self.create_project()["project_id"]
        grant = self.grant_project(project_id)
        factor = self._create_validated_factor(project_id, grant)

        response = self.client.post(
            f"/api/agent/research/projects/{project_id}/definitions",
            json={
                "grant_id": grant["grant_id"],
                "definition_type": "ALPHA",
                "spec": {
                    "name": "btc_alpha_missing_version",
                    "version": "1.0.0",
                    "components": [
                        {
                            "factor_definition_id": factor["definition_id"],
                            # factor_version intentionally omitted
                            "weight": 1.0,
                            "transform": "RAW",
                        }
                    ],
                },
            },
        )
        # Missing factor_version is a payload/contract error, not an
        # authorization/scope error, and must not be reported as 403.
        self.assertEqual(400, response.status_code, response.get_json())
        self.assertNotEqual("RESEARCH_DEFINITION_OUT_OF_SCOPE", response.get_json().get("code"))

    def test_alpha_component_unknown_factor_returns_400(self):
        project_id = self.create_project()["project_id"]
        grant = self.grant_project(project_id)

        response = self.client.post(
            f"/api/agent/research/projects/{project_id}/definitions",
            json={
                "grant_id": grant["grant_id"],
                "definition_type": "ALPHA",
                "spec": {
                    "name": "btc_alpha_unknown_factor",
                    "version": "1.0.0",
                    "components": [
                        {
                            "factor_definition_id": "factor_does_not_exist",
                            "factor_version": "1.0.0",
                            "weight": 1.0,
                            "transform": "RAW",
                        }
                    ],
                },
            },
        )
        self.assertEqual(400, response.status_code, response.get_json())
        self.assertNotEqual("RESEARCH_DEFINITION_OUT_OF_SCOPE", response.get_json().get("code"))

    def test_alpha_component_out_of_scope_factor_returns_403(self):
        project_id = self.create_project()["project_id"]
        grant = self.grant_project(project_id)
        factor = self._create_validated_factor(project_id, grant)

        other_project_id = self.create_project()["project_id"]
        other_grant = self.grant_project(other_project_id)

        response = self.client.post(
            f"/api/agent/research/projects/{other_project_id}/definitions",
            json={
                "grant_id": other_grant["grant_id"],
                "definition_type": "ALPHA",
                "spec": {
                    "name": "btc_alpha_cross_project",
                    "version": "1.0.0",
                    "components": [
                        {
                            "factor_definition_id": factor["definition_id"],
                            "factor_version": factor["version"],
                            "weight": 1.0,
                            "transform": "RAW",
                        }
                    ],
                },
            },
        )
        self.assertEqual(403, response.status_code, response.get_json())
        self.assertEqual("RESEARCH_DEFINITION_OUT_OF_SCOPE", response.get_json()["code"])


if __name__ == "__main__":
    unittest.main()
