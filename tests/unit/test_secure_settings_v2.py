from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from services.secure_settings import load_secrets, save_secrets, strip_sensitive


class SecureSettingsV2Test(unittest.TestCase):
    def test_round_trip_and_public_redaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            secrets_path = Path(tmp) / "secrets.json"
            key_path = Path(tmp) / "legacy.key"
            original = {
                "finnhub_api_keys": ["one"],
                "active_finnhub_api_key": "one",
                "coingecko_api_key": "two",
                "llm_api_key": "three",
                "openbb_fred_api_key": "four",
                "openbb_provider_credentials": {"polygon_api_key": "five"},
            }
            save_secrets(secrets_path, key_path, original)
            stored = json.loads(secrets_path.read_text(encoding="utf-8"))
            self.assertEqual(stored["version"], 2)
            self.assertNotIn("four", secrets_path.read_text(encoding="utf-8"))
            self.assertNotIn("five", secrets_path.read_text(encoding="utf-8"))
            if os.name == "nt":
                self.assertEqual(stored["protection"], "dpapi-user")
                self.assertFalse(key_path.exists())
            self.assertEqual(load_secrets(secrets_path, key_path), original)
            public = strip_sensitive(original)
            self.assertNotIn("openbb_fred_api_key", public)
            self.assertTrue(public["has_openbb_fred_api_key"])
            self.assertTrue(public["openbb_provider_credential_status"]["polygon_api_key"])
            self.assertEqual(public["finnhub_api_key_count"], 1)


if __name__ == "__main__":
    unittest.main()
