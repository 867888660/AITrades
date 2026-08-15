from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services import config_loader
from services.secure_settings import (
    SecureSettingsError,
    load_secrets,
    save_secrets,
    strip_sensitive,
)


class SecureSettingsV2Test(unittest.TestCase):
    def test_round_trip_and_public_redaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            secrets_path = Path(tmp) / "secrets.json"
            key_path = Path(tmp) / "legacy.key"
            original = {
                "finnhub_api_keys": ["one"],
                "active_finnhub_api_key": "one",
                "sec_edgar_user_agent": "DataTube Research data@example.com",
                "coingecko_api_key": "two",
                "llm_api_key": "three",
                "openbb_fred_api_key": "four",
                "openbb_provider_credentials": {"polygon_api_key": "five"},
            }
            save_secrets(secrets_path, key_path, original)
            stored = json.loads(secrets_path.read_text(encoding="utf-8"))
            self.assertEqual(stored["version"], 2)
            self.assertNotIn("four", secrets_path.read_text(encoding="utf-8"))
            self.assertNotIn("data@example.com", secrets_path.read_text(encoding="utf-8"))
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

    def test_unavailable_dpapi_secrets_do_not_prevent_startup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings_path = root / "web_settings.json"
            secrets_path = root / "web_settings.secrets.json"
            key_path = root / "legacy.key"
            settings_path.write_text("{}", encoding="utf-8")
            secrets_path.write_text('{"encrypted":"dpapi:unavailable"}', encoding="utf-8")
            with (
                patch.object(config_loader, "WEB_SETTINGS_PATH", settings_path),
                patch.object(config_loader, "WEB_SETTINGS_SECRETS_PATH", secrets_path),
                patch.object(config_loader, "WEB_SETTINGS_KEY_PATH", key_path),
                patch.object(
                    config_loader,
                    "load_secrets",
                    side_effect=SecureSettingsError("cannot decrypt"),
                ),
            ):
                config_loader._settings_cache = {}
                settings = config_loader.load_web_settings()

            self.assertTrue(settings["_secrets_unavailable"])
            self.assertEqual("unavailable", settings["secrets_status"]["status"])
            config_loader._settings_cache = {}

    def test_non_secret_save_preserves_unavailable_secret_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings_path = root / "web_settings.json"
            secrets_path = root / "web_settings.secrets.json"
            key_path = root / "legacy.key"
            settings_path.write_text("{}", encoding="utf-8")
            original = '{"encrypted":"dpapi:unavailable"}'
            secrets_path.write_text(original, encoding="utf-8")
            with (
                patch.object(config_loader, "WEB_SETTINGS_PATH", settings_path),
                patch.object(config_loader, "WEB_SETTINGS_SECRETS_PATH", secrets_path),
                patch.object(config_loader, "WEB_SETTINGS_KEY_PATH", key_path),
                patch.object(
                    config_loader,
                    "load_secrets",
                    side_effect=SecureSettingsError("cannot decrypt"),
                ),
                patch.object(config_loader, "save_secrets") as save_secret_file,
                patch.object(config_loader, "load_config", return_value={}),
                patch.object(config_loader, "save_config"),
            ):
                config_loader._settings_cache = {}
                config_loader.save_web_settings({"ui_refresh_sec": 10})

            save_secret_file.assert_not_called()
            self.assertEqual(original, secrets_path.read_text(encoding="utf-8"))
            config_loader._settings_cache = {}


if __name__ == "__main__":
    unittest.main()
