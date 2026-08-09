from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from services.openbb_provider_service import (
    OpenBBProviderConfig,
    OpenBBProviderService,
    normalize_equity_adjustment,
)


class OpenBBProviderServiceTests(unittest.TestCase):
    def settings(self, **overrides):
        raw = {
            "enabled": True,
            "base_url": "http://127.0.0.1:6901",
            "default_provider": "yfinance",
            "allowed_providers": ["yfinance"],
            "timeout_sec": 10,
        }
        raw.update(overrides)
        return {"openbb_settings": raw}

    def test_config_normalizes_provider_allowlist(self):
        config = OpenBBProviderConfig.from_settings(self.settings(allowed_providers=[]))
        self.assertEqual(config.allowed_providers, ("yfinance",))

    def test_disabled_provider_refuses_data_fetch(self):
        service = OpenBBProviderService(self.settings(enabled=False))
        with self.assertRaisesRegex(ValueError, "disabled"):
            service.fetch_equity_historical({"symbol": "AAPL"})

    def test_intraday_requires_controlled_premarket_session(self):
        service = OpenBBProviderService(self.settings())
        with self.assertRaisesRegex(ValueError, "controlled 1m/5m pre-market"):
            service.fetch_equity_historical({"symbol": "AAPL", "interval": "1h"})
        with self.assertRaisesRegex(ValueError, "PREMARKET_0400_0930_ET"):
            service.fetch_equity_historical({
                "symbol": "AAPL", "interval": "5m",
                "start_date": "2026-08-01", "end_date": "2026-08-05",
            })

    @patch("services.openbb_provider_service.SESSION.get")
    def test_premarket_query_forces_extended_hours_and_preserves_session(self, get: Mock):
        response = Mock(status_code=200)
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "provider": "yfinance",
            "results": [{
                "date": "2026-08-04T04:00:00", "open": 100, "high": 101,
                "low": 99, "close": 100.5, "volume": 10,
            }],
        }
        get.return_value = response
        result = OpenBBProviderService(self.settings()).fetch_equity_historical({
            "symbol": "AAPL", "interval": "5m",
            "session": "PREMARKET_0400_0930_ET",
            "start_date": "2026-08-01", "end_date": "2026-08-05",
            "extended_hours": False,
        })
        self.assertEqual("PREMARKET_0400_0930_ET", result["session"])
        self.assertEqual("America/New_York", result["timezone"])
        self.assertTrue(get.call_args.kwargs["params"]["extended_hours"])

    def test_provider_allowlist_is_enforced(self):
        service = OpenBBProviderService(self.settings())
        with self.assertRaisesRegex(ValueError, "not allowed"):
            service.fetch_equity_historical({"symbol": "AAPL", "provider": "fmp"})

    def test_datatube_none_adjustment_maps_to_openbb_splits_only(self):
        self.assertEqual("splits_only", normalize_equity_adjustment("NONE"))
        self.assertEqual("splits_only", normalize_equity_adjustment("unadjusted"))
        self.assertEqual("splits_and_dividends", normalize_equity_adjustment("TOTAL_RETURN"))

    @patch("services.openbb_provider_service.SESSION.get")
    def test_historical_response_preserves_upstream_identity(self, get: Mock):
        response = Mock()
        response.json.return_value = {
            "provider": "yfinance",
            "results": [{"date": "2026-07-10", "open": 1, "high": 2, "low": 1, "close": 2}],
            "warnings": [],
        }
        response.raise_for_status.return_value = None
        get.return_value = response
        result = OpenBBProviderService(self.settings()).fetch_equity_historical({"symbol": "aapl"})
        self.assertEqual(result["gateway"], "openbb")
        self.assertEqual(result["upstream_provider"], "yfinance")
        self.assertEqual(result["symbol"], "AAPL")
        self.assertEqual(result["row_count"], 1)
        self.assertNotIn("manifest", result)

    @patch("services.openbb_provider_service.time.sleep")
    @patch("services.openbb_provider_service.SESSION.get")
    def test_historical_retries_transient_empty_gateway_response(self, get: Mock, sleep: Mock):
        empty = Mock(status_code=204)
        empty.raise_for_status.return_value = None
        success = Mock(status_code=200)
        success.raise_for_status.return_value = None
        success.json.return_value = {
            "provider": "yfinance",
            "results": [{"date": "2026-07-10", "open": 1, "high": 2, "low": 1, "close": 2}],
        }
        get.side_effect = [empty, success]
        result = OpenBBProviderService(self.settings()).fetch_equity_historical({"symbol": "AAPL"})
        self.assertEqual(1, result["row_count"])
        self.assertEqual(2, get.call_count)
        sleep.assert_called_once()

    @patch("services.openbb_provider_service.SESSION.get")
    def test_historical_normalizes_empty_future_range_error(self, get: Mock):
        response = Mock(status_code=422)
        response.json.return_value = {"detail": ["Out of range float values are not JSON compliant"]}
        get.return_value = response
        with self.assertRaisesRegex(ValueError, "Choose an earlier end date or use Latest available"):
            OpenBBProviderService(self.settings()).fetch_equity_historical({
                "symbol": "TSLA",
                "start_date": "2026-07-01",
                "end_date": "2026-07-24",
            })
        response.raise_for_status.assert_not_called()

    @patch("services.openbb_provider_service.SESSION.get")
    def test_latest_available_steps_back_from_incomplete_daily_row(self, get: Mock):
        incomplete = Mock(status_code=422)
        incomplete.json.return_value = {"detail": ["Out of range float values are not JSON compliant"]}
        success = Mock(status_code=200)
        success.raise_for_status.return_value = None
        success.json.return_value = {
            "provider": "yfinance",
            "results": [{"date": "2026-07-23", "open": 1, "high": 2, "low": 1, "close": 2}],
            "warnings": [],
        }
        get.side_effect = [incomplete, success]

        result = OpenBBProviderService(self.settings()).fetch_equity_historical({
            "symbol": "TSLA",
            "start_date": "2025-07-26",
            "end_date": "2026-07-24",
            "adjustment": "unadjusted",
            "latest_available": True,
        })

        self.assertEqual(1, result["row_count"])
        self.assertEqual("2026-07-23", result["resolved_end_date"])
        self.assertIn("resolved through 2026-07-23", result["warnings"][-1])
        self.assertEqual("2026-07-24", get.call_args_list[0].kwargs["params"]["end_date"])
        self.assertEqual("2026-07-23", get.call_args_list[1].kwargs["params"]["end_date"])
        self.assertEqual("splits_only", get.call_args_list[0].kwargs["params"]["adjustment"])

    @patch("services.openbb_provider_service.SESSION.get")
    def test_fred_series_uses_openbb_without_exposing_credentials(self, get: Mock):
        response = Mock()
        response.json.return_value = {"provider": "fred", "results": [{"date": "2026-07-10", "DGS10": 4.2}]}
        response.raise_for_status.return_value = None
        get.return_value = response
        result = OpenBBProviderService(self.settings()).fetch_fred_series({"symbol": "dgs10", "limit": 1})
        self.assertEqual(result["upstream_provider"], "fred")
        self.assertEqual(result["row_count"], 1)
        called = get.call_args
        self.assertNotIn("api_key", called.kwargs["params"])


if __name__ == "__main__":
    unittest.main()
