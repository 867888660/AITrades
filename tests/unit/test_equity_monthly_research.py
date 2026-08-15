from __future__ import annotations

import unittest

from services.data_platform.equity_monthly_research import (
    project_fundamental_metric_events,
)


class EquityMonthlyResearchTests(unittest.TestCase):
    def test_ttm_uses_prior_annual_plus_current_ytd_minus_comparable_ytd(self) -> None:
        instrument_id = "equity:CRSP:10001"

        def fact(
            concept: str,
            value: float,
            start: str,
            end: str,
            form: str,
            available: str,
        ) -> dict:
            return {
                "instrument_id": instrument_id,
                "concept": concept,
                "value": value,
                "period_start": start,
                "period_end": end,
                "form": form,
                "unit": "USD",
                "available_time": available,
            }

        rows = [
            fact("NetIncomeLoss", 100, "2023-01-01", "2023-12-31", "10-K", "2024-02-20T21:00:00+00:00"),
            fact("NetIncomeLoss", 20, "2023-01-01", "2023-03-31", "10-Q", "2024-05-05T21:00:00+00:00"),
            fact("NetIncomeLoss", 30, "2024-01-01", "2024-03-31", "10-Q", "2024-05-05T21:00:00+00:00"),
            fact("NetCashProvidedByUsedInOperatingActivities", 120, "2023-01-01", "2023-12-31", "10-K", "2024-02-20T21:00:00+00:00"),
            fact("NetCashProvidedByUsedInOperatingActivities", 25, "2023-01-01", "2023-03-31", "10-Q", "2024-05-05T21:00:00+00:00"),
            fact("NetCashProvidedByUsedInOperatingActivities", 40, "2024-01-01", "2024-03-31", "10-Q", "2024-05-05T21:00:00+00:00"),
            fact("StockholdersEquity", 250, "", "2023-12-31", "10-K", "2024-02-20T21:00:00+00:00"),
            fact("StockholdersEquity", 275, "", "2024-03-31", "10-Q", "2024-05-05T21:00:00+00:00"),
        ]

        events = project_fundamental_metric_events(rows)

        self.assertEqual(2, len(events))
        self.assertEqual(100, events[0]["net_income_ttm"])
        self.assertEqual(120, events[0]["operating_cash_flow_ttm"])
        self.assertEqual(250, events[0]["shareholders_equity"])
        self.assertEqual(110, events[1]["net_income_ttm"])
        self.assertEqual(135, events[1]["operating_cash_flow_ttm"])
        self.assertEqual(275, events[1]["shareholders_equity"])
        self.assertEqual("2024-05-05T21:00:00+00:00", events[1]["available_time"])

    def test_fundamental_projection_excludes_non_usd_facts(self) -> None:
        rows = [
            {
                "instrument_id": "equity:CRSP:10001",
                "concept": "NetIncomeLoss",
                "unit": unit,
                "value": value,
                "period_start": "2023-01-01",
                "period_end": "2023-12-31",
                "form": "10-K",
                "available_time": "2024-02-20T21:00:00+00:00",
            }
            for unit, value in (("CAD", -999.0), ("USD", 100.0))
        ]

        events = project_fundamental_metric_events(rows)

        self.assertEqual(100.0, events[0]["net_income_ttm"])


if __name__ == "__main__":
    unittest.main()
