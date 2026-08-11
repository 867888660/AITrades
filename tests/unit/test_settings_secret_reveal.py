import unittest
from unittest.mock import patch

import app as app_module


class SettingsSecretRevealApiTest(unittest.TestCase):
    def setUp(self):
        app_module.app.config.update(TESTING=True)
        self.client = app_module.app.test_client()
        self.settings = {
            "finnhub_api_keys": ["first", "second"],
            "active_finnhub_api_key": "second",
            "coingecko_api_key": "coin",
            "llm_api_key": "llm",
            "openbb_provider_credentials": {
                "polygon_api_key": "polygon",
                "fred_api_key": "fred",
            },
        }

    def reveal(self, field, remote_addr="127.0.0.1"):
        with patch.object(app_module, "load_web_settings", return_value=self.settings):
            return self.client.post(
                "/api/settings/secrets/reveal",
                json={"field": field},
                environ_base={"REMOTE_ADDR": remote_addr},
            )

    def test_reveals_one_allowed_scalar_without_caching(self):
        response = self.reveal("coingecko_api_key")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["value"], "coin")
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(response.headers["Pragma"], "no-cache")

    def test_reveals_multivalue_and_openbb_credentials(self):
        finnhub = self.reveal("finnhub_api_keys")
        polygon = self.reveal("openbb_provider_credentials.polygon_api_key")

        self.assertEqual(finnhub.get_json()["data"]["value"], ["first", "second"])
        self.assertEqual(polygon.get_json()["data"]["value"], "polygon")

    def test_rejects_unknown_or_nonlocal_requests(self):
        unknown = self.reveal("wallet_addresses")
        remote = self.reveal("llm_api_key", remote_addr="192.0.2.10")

        self.assertEqual(unknown.status_code, 400)
        self.assertEqual(remote.status_code, 403)
        self.assertNotIn("llm", remote.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
