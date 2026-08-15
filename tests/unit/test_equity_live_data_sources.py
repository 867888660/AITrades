from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import app as app_module
from services.data_source_connection_service import (
    DataSourceConnectionError,
    DataSourceConnectionService,
)
from services.sec_edgar_service import (
    fetch_sec_company_facts,
    fetch_sec_submissions,
    normalize_cik,
    normalize_sec_user_agent,
)


class SecEdgarServiceTest(unittest.TestCase):
    def response(self, payload: dict) -> Mock:
        response = Mock()
        response.json.return_value = payload
        response.raise_for_status.return_value = None
        return response

    def test_normalizes_cik_and_requires_contact_email(self) -> None:
        self.assertEqual("0000320193", normalize_cik("CIK320193"))
        self.assertEqual(
            "DataTube Research data@example.com",
            normalize_sec_user_agent("DataTube Research data@example.com"),
        )
        with self.assertRaises(ValueError):
            normalize_cik("AAPL")
        with self.assertRaises(ValueError):
            normalize_sec_user_agent("DataTube without contact")

    @patch("services.sec_edgar_service.SESSION.get")
    def test_submissions_returns_bounded_connection_summary(self, get: Mock) -> None:
        get.return_value = self.response({
            "name": "Apple Inc.",
            "filings": {"recent": {
                "form": ["10-Q"],
                "accessionNumber": ["0000320193-26-000001"],
                "filingDate": ["2026-08-01"],
            }},
        })
        result = fetch_sec_submissions(
            "320193", user_agent="DataTube Research data@example.com"
        )
        self.assertEqual("Apple Inc.", result["entity_name"])
        self.assertEqual("10-Q", result["latest_filing"]["form"])
        self.assertEqual(
            "DataTube Research data@example.com",
            get.call_args.kwargs["headers"]["User-Agent"],
        )

    @patch("services.sec_edgar_service.SESSION.get")
    def test_company_facts_selects_latest_filed_value(self, get: Mock) -> None:
        get.return_value = self.response({
            "entityName": "Apple Inc.",
            "facts": {"us-gaap": {"Assets": {
                "label": "Assets",
                "description": "Total assets",
                "units": {"USD": [
                    {"val": 100, "end": "2025-12-31", "filed": "2026-01-20", "form": "10-K", "accn": "old"},
                    {"val": 120, "end": "2026-06-30", "filed": "2026-08-01", "form": "10-Q", "accn": "new"},
                ]},
            }}},
        })
        result = fetch_sec_company_facts(
            "320193",
            user_agent="DataTube Research data@example.com",
            concepts=["Assets"],
        )
        self.assertEqual(120, result["facts"][0]["value"])
        self.assertEqual("2026-08-01", result["facts"][0]["filed"])
        self.assertIn("filed date", result["point_in_time_note"])


class DataSourceConnectionServiceTest(unittest.TestCase):
    @patch("services.data_source_connection_service.fetch_finance_quotes")
    def test_finnhub_quote_and_connection_test(self, fetch: Mock) -> None:
        fetch.return_value = {
            "ok": True,
            "data": [{"symbol": "AAPL", "price": 200.0}],
            "errors": [],
        }
        service = DataSourceConnectionService({"active_finnhub_api_key": "secret"})
        result = service.test("finnhub")
        self.assertTrue(result["ok"])
        self.assertTrue(result["detail"]["price_available"])
        self.assertNotIn("secret", str(result))

    @patch("services.data_source_connection_service.fetch_finance_quotes")
    def test_finnhub_rejects_empty_upstream_result(self, fetch: Mock) -> None:
        fetch.return_value = {"ok": True, "data": [{"symbol": "AAPL", "price": None}]}
        service = DataSourceConnectionService({"active_finnhub_api_key": "secret"})
        with self.assertRaises(DataSourceConnectionError):
            service.equity_quotes(["AAPL"])

    def test_quote_request_is_bounded_and_validated(self) -> None:
        service = DataSourceConnectionService({"active_finnhub_api_key": "secret"})
        with self.assertRaises(ValueError):
            service.equity_quotes(["AAPL?token=other"])
        with self.assertRaises(ValueError):
            service.equity_quotes([f"S{index}" for index in range(21)])

    @patch("services.data_source_connection_service.fetch_sec_submissions")
    def test_sec_connection_uses_saved_user_agent(self, fetch: Mock) -> None:
        fetch.return_value = {
            "cik": "0000320193",
            "entity_name": "Apple Inc.",
            "latest_filing": {"form": "10-Q"},
        }
        service = DataSourceConnectionService({
            "sec_edgar_user_agent": "DataTube Research data@example.com"
        })
        result = service.test("SEC")
        self.assertEqual("Apple Inc.", result["detail"]["entity_name"])
        self.assertEqual(
            "DataTube Research data@example.com",
            fetch.call_args.kwargs["user_agent"],
        )


class DataSourceConnectionApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = app_module.app.test_client()

    @patch.object(app_module.DataSourceConnectionService, "test")
    @patch.object(app_module, "load_web_settings", return_value={})
    def test_connection_route(self, _settings: Mock, test_connection: Mock) -> None:
        test_connection.return_value = {"source_id": "SEC", "ok": True, "latency_ms": 1}
        response = self.client.post("/api/data-sources/SEC/test", json={})
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.get_json()["data"]["ok"])

    @patch.object(app_module.DataSourceConnectionService, "equity_quotes")
    @patch.object(app_module, "load_web_settings", return_value={})
    def test_quote_route(self, _settings: Mock, quotes: Mock) -> None:
        quotes.return_value = {"ok": True, "data": [{"symbol": "AAPL", "price": 200}]}
        response = self.client.get("/api/data-sources/equity/quotes?symbols=AAPL")
        self.assertEqual(200, response.status_code)
        self.assertEqual("AAPL", response.get_json()["data"]["data"][0]["symbol"])

    @patch.object(app_module.DataSourceConnectionService, "sec_company_facts")
    @patch.object(app_module, "load_web_settings", return_value={})
    def test_company_facts_route(self, _settings: Mock, company_facts: Mock) -> None:
        company_facts.return_value = {"cik": "0000320193", "facts": []}
        response = self.client.get(
            "/api/data-sources/sec/company-facts/320193?concepts=Assets"
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual("0000320193", response.get_json()["data"]["cik"])
        company_facts.assert_called_once_with("320193", concepts=["Assets"])


if __name__ == "__main__":
    unittest.main()
