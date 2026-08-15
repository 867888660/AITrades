from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.data_source_definitions import openbb_equity_provider_sequence
from services.data_source_management_service import (
    DataSourceManagementService,
    DataSourceRoutingConflict,
)
from services.data_platform import DataPlatformStore, RequirementMaintenanceService


class DataSourceManagementServiceTest(unittest.TestCase):
    def settings(self) -> dict:
        return {
            "active_finnhub_api_key": "finnhub-secret-value",
            "sec_edgar_user_agent": "DataTube Research data@example.com",
            "openbb_provider_credentials": {"polygon_api_key": "polygon-secret-value"},
            "openbb_settings": {
                "enabled": True,
                "base_url": "http://127.0.0.1:6901",
                "default_provider": "yfinance",
                "allowed_providers": ["yfinance", "polygon", "tiingo"],
            },
            "data_source_settings": {
                "mode": "HYBRID",
                "version": 3,
                "priority_orders": {
                    "EQUITY:1D:BARS": [
                        "OPENBB:POLYGON", "OPENBB:YFINANCE", "OPENBB:TIINGO"
                    ],
                },
            },
        }

    def test_describe_centralizes_sources_without_returning_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            site_packages = Path(temp) / ".openbb-venv" / "Lib" / "site-packages"
            for provider in ("yfinance", "polygon", "tiingo"):
                (site_packages / f"openbb_{provider}-1.0.0.dist-info").mkdir(parents=True)
            with patch(
                "services.data_platform.data_capability_service.ResearchDataCapabilityService._tcp_online",
                return_value=True,
            ):
                result = DataSourceManagementService(
                    self.settings(), base_dir=Path(temp)
                ).describe()
        sources = {item["source_id"]: item for item in result["sources"]}
        self.assertEqual("ready", sources["FINNHUB"]["runtime_status"])
        self.assertEqual("ready", sources["SEC"]["runtime_status"])
        self.assertTrue(sources["SEC"]["test_supported"])
        self.assertEqual(["company-facts"], sources["SEC"]["query_operations"])
        self.assertEqual("ready", sources["OPENBB:POLYGON"]["runtime_status"])
        self.assertEqual("credential_required", sources["OPENBB:TIINGO"]["runtime_status"])
        self.assertNotIn("polygon-secret-value", str(result))
        self.assertNotIn("finnhub-secret-value", str(result))
        self.assertNotIn("data@example.com", str(result))
        equity = next(item for item in result["routing_policies"] if item["policy_key"] == "EQUITY:1D:BARS")
        self.assertEqual("OPENBB:POLYGON", equity["order"][0])

    def test_update_is_versioned_and_transactional(self) -> None:
        original = self.settings()
        latest = dict(original)
        saved_payloads: list[dict] = []

        def saver(payload: dict) -> dict:
            saved_payloads.append(payload)
            latest.update(payload)
            return dict(latest)

        service = DataSourceManagementService(
            original, base_dir=Path("."), settings_saver=saver,
            settings_loader=lambda: dict(latest),
        )
        result = service.update_routing({
            "expected_version": 3,
            "mode": "MANUAL",
            "priority_orders": {
                "EQUITY:1D:BARS": ["OPENBB:YFINANCE", "OPENBB:POLYGON"],
            },
        })
        self.assertEqual(4, result["version"])
        self.assertEqual("MANUAL", saved_payloads[0]["data_source_settings"]["mode"])
        with self.assertRaises(DataSourceRoutingConflict):
            service.update_routing({"expected_version": 3})

    def test_saved_credential_requires_explicit_activation(self) -> None:
        settings = self.settings()
        settings["openbb_settings"] = {
            **settings["openbb_settings"],
            "allowed_providers": ["yfinance"],
        }
        with tempfile.TemporaryDirectory() as temp:
            site_packages = Path(temp) / ".openbb-venv" / "Lib" / "site-packages"
            (site_packages / "openbb_polygon-1.0.0.dist-info").mkdir(parents=True)
            with patch(
                "services.data_platform.data_capability_service.ResearchDataCapabilityService._tcp_online",
                return_value=True,
            ):
                result = DataSourceManagementService(settings, base_dir=temp).describe()
        polygon = next(item for item in result["sources"] if item["provider_id"] == "POLYGON")
        self.assertEqual("activation_required", polygon["runtime_status"])
        self.assertTrue(polygon["credential_configured"])
        self.assertTrue(polygon["can_activate"])

    def test_activate_provider_preserves_other_openbb_settings(self) -> None:
        latest = self.settings()
        latest["openbb_settings"] = {
            **latest["openbb_settings"],
            "allowed_providers": ["yfinance"],
        }

        def saver(payload: dict) -> dict:
            latest.update(payload)
            return dict(latest)

        service = DataSourceManagementService(
            latest,
            base_dir=Path("."),
            settings_saver=saver,
            settings_loader=lambda: dict(latest),
        )
        saved = service.activate_openbb_provider("polygon")
        self.assertTrue(saved["openbb_settings"]["enabled"])
        self.assertEqual(
            ["yfinance", "polygon"],
            saved["openbb_settings"]["allowed_providers"],
        )
        self.assertEqual(
            "http://127.0.0.1:6901", saved["openbb_settings"]["base_url"]
        )

    def test_global_sequence_respects_allowed_and_manual_order(self) -> None:
        sequence = openbb_equity_provider_sequence(self.settings())
        self.assertEqual(["polygon", "yfinance", "tiingo"], sequence)

    def test_auto_requirement_uses_whole_request_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = DataPlatformStore(Path(temp) / "metadata.db")
            service = RequirementMaintenanceService(store)
            row = {
                "instrument_id": "equity:XNAS:AAPL",
                "frequency": "1d",
                "data_type": "bars",
                "required_range": {
                    "start": "2025-01-01T00:00:00+00:00",
                    "end": "2025-12-31T23:59:59+00:00",
                },
                "provider": "AUTO",
                "adjustment": "SPLITS_ONLY",
                "source_selection_policy": {"mode": "AUTO"},
            }
            with patch(
                "services.data_platform.requirement_maintenance_service.load_web_settings",
                return_value=self.settings(),
            ):
                task = service._task_spec(row)
        self.assertEqual("PRIMARY_FALLBACK", task["input"]["source_policy"]["mode"])
        self.assertEqual(
            ["polygon", "yfinance", "tiingo"],
            task["input"]["source_policy"]["providers"],
        )

    def test_crsp_daily_requirement_never_routes_to_openbb(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service = RequirementMaintenanceService(
                DataPlatformStore(Path(temp) / "metadata.db")
            )
            task = service._task_spec({
                "instrument_id": "equity:CRSP:10001",
                "frequency": "1d",
                "data_type": "bars",
                "required_range": {
                    "start": "2025-01-01T00:00:00+00:00",
                    "end": "2025-12-31T23:59:59+00:00",
                },
                "provider": "CRSP/CIZ",
                "adjustment": "CRSP_FIELDS",
                "source_selection_policy": {
                    "mode": "FIXED",
                    "preferred_sources": ["crsp/ciz"],
                },
            })
        self.assertIsNone(task)


if __name__ == "__main__":
    unittest.main()
