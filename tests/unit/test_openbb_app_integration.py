from __future__ import annotations

import unittest
from unittest.mock import patch

from app import _group_latency_status, app


class OpenBBAppIntegrationTests(unittest.TestCase):
    def test_disabled_provider_does_not_degrade_group(self):
        result = _group_latency_status([
            {"key": "finnhub", "ok": True, "status": "good", "latency_ms": 20},
            {"key": "openbb", "ok": False, "status": "disabled", "latency_ms": None},
        ])
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "good")
        self.assertEqual(result["disabled"], 1)

    def test_capabilities_route_reuses_settings(self):
        with patch("app.load_web_settings", return_value={"openbb_settings": {"enabled": False}}):
            response = app.test_client().get("/api/research/data/providers/openbb")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["data"]["gateway"], "openbb")
        self.assertFalse(body["data"]["enabled"])


if __name__ == "__main__":
    unittest.main()
