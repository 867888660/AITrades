from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as app_module
from services import agent_interface_service, inspection_service


class InspectionApiTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "inspection-api.sqlite3"
        self.patchers = [
            patch.object(inspection_service, "_strategy_db_path", return_value=self.db_path),
            patch.object(agent_interface_service, "_strategy_db_path", return_value=self.db_path),
        ]
        for item in self.patchers:
            item.start()
        self.client = app_module.app.test_client()
        inspection_service.emit_event(
            trace_id="trace_api_test",
            subject_type="research_run",
            subject_id="research_run_api_test",
            trace_title="API inspection fixture",
            trace_status="succeeded",
            event_id="evt_api_test",
            event_kind="validation",
            title="Validate API trace",
            status="succeeded",
            severity="warning",
            operation="inspection.contract.validate",
            metadata={"finding": "contract fixture"},
        )

    def tearDown(self):
        for item in reversed(self.patchers):
            item.stop()
        self.temp_dir.cleanup()

    def test_progressive_read_endpoints(self):
        query = "actor_type=human&actor_id=local_user"
        traces_response = self.client.get(f"/api/agent/inspection/traces?{query}")
        self.assertEqual(traces_response.status_code, 200, traces_response.get_json())
        traces = traces_response.get_json()["data"]
        self.assertEqual([item["trace_id"] for item in traces["items"]], ["trace_api_test"])

        trace_response = self.client.get(f"/api/agent/inspection/traces/trace_api_test?{query}")
        self.assertEqual(trace_response.status_code, 200, trace_response.get_json())
        self.assertEqual(trace_response.get_json()["data"]["warning_count"], 1)

        events_response = self.client.get(
            f"/api/agent/inspection/traces/trace_api_test/events?severity=warning&{query}"
        )
        self.assertEqual(events_response.status_code, 200, events_response.get_json())
        event_index = events_response.get_json()["data"]["items"]
        self.assertEqual([item["event_id"] for item in event_index], ["evt_api_test"])
        self.assertNotIn("metadata", event_index[0])

        event_response = self.client.get(f"/api/agent/inspection/events/evt_api_test?{query}")
        self.assertEqual(event_response.status_code, 200, event_response.get_json())
        self.assertEqual(event_response.get_json()["data"]["metadata"]["finding"], "contract fixture")

        search_response = self.client.get(
            f"/api/agent/inspection/traces/trace_api_test/search?q=fixture&{query}"
        )
        self.assertEqual(search_response.status_code, 200, search_response.get_json())
        self.assertEqual(search_response.get_json()["data"]["items"][0]["event_id"], "evt_api_test")

    def test_capabilities_publish_inspection_contract(self):
        response = self.client.get("/api/agent/capabilities?section=inspection")
        self.assertEqual(response.status_code, 200, response.get_json())
        contract = response.get_json()["data"]["inspection_capabilities"]
        self.assertEqual(contract["schema_version"], "inspection.event.v1")
        self.assertFalse(contract["agent_delete_allowed"])


if __name__ == "__main__":
    unittest.main()
