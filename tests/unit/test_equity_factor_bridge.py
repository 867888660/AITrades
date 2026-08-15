from __future__ import annotations

import unittest

from services.data_platform import DefinitionRegistry, FactorEngineV4, FactorGraphCompiler, FactorGraphSpec
from services.data_platform.equity_factor_bridge import project_factor_rows


class EquityFactorBridgeTest(unittest.TestCase):
    def test_raw_sec_ttm_is_emitted_only_after_four_quarters_are_available(self) -> None:
        rows = [
            {
                "instrument_id": "equity:CRSP:10001",
                "concept": "NetIncomeLoss",
                "value": value,
                "period_start": start,
                "period_end": end,
                "form": "10-Q",
                "unit": "USD",
                "accession_number": f"000{index}",
                "available_time": available,
            }
            for index, (start, end, available, value) in enumerate(
                (
                    ("2024-01-01", "2024-03-31", "2024-05-01T20:00:00+00:00", 10),
                    ("2024-04-01", "2024-06-30", "2024-08-01T20:00:00+00:00", 20),
                    ("2024-07-01", "2024-09-30", "2024-11-01T20:00:00+00:00", 30),
                    ("2024-10-01", "2024-12-31", "2025-02-01T20:00:00+00:00", 40),
                ),
                start=1,
            )
        ]

        projected = project_factor_rows("fundamentals", "net_income_ttm", rows)

        self.assertEqual(1, len(projected))
        self.assertEqual(100.0, projected[0]["net_income_ttm"])
        self.assertEqual("2025-02-01T20:00:00+00:00", projected[0]["available_time"])

    def test_raw_sec_projection_ignores_non_usd_values(self) -> None:
        rows = [
            {
                "instrument_id": "equity:CRSP:10001",
                "concept": "Assets",
                "unit": unit,
                "value": value,
                "period_end": "2024-12-31",
                "form": "10-K",
                "available_time": "2025-02-01T20:00:00+00:00",
            }
            for unit, value in (("EUR", 999.0), ("USD", 100.0))
        ]

        projected = project_factor_rows("fundamentals", "assets", rows)

        self.assertEqual(100.0, projected[-1]["assets"])

    def test_cash_dividends_align_to_daily_axis_without_future_events(self) -> None:
        document = {
            "schema_version": "factor_draft.v2",
            "identity": {"name": "dividend_365d", "version": "1.0.0"},
            "inputs": [
                {
                    "variable_name": "dividend",
                    "dataset": "corporate_actions",
                    "field": "cash_dividend",
                    "frequency": "event",
                },
                {
                    "variable_name": "price",
                    "dataset": "bars",
                    "field": "close",
                    "frequency": "1d",
                },
            ],
            "parameters": [],
            "formula": {"source": "financial.trailing_365d_sum(dividend, price)"},
            "output": {"direction": "HIGHER_IS_BETTER"},
            "advanced": {
                "missing_policy": "STRICT",
                "time_alignment_policy": "BAR_END_AVAILABLE_TIME",
                "available_after": "BAR_CLOSE",
            },
        }
        compiled = FactorGraphCompiler.compile(
            document,
            DefinitionRegistry.engine_capabilities()["factor"],
        )
        instrument_id = "equity:CRSP:10001"
        events = [
            {
                "event_time": "2024-01-15T21:00:00+00:00",
                "available_time": "2024-01-15T21:00:00+00:00",
                "cash_dividend": 1.0,
            },
            {
                "event_time": "2025-01-10T21:00:00+00:00",
                "available_time": "2025-01-10T21:00:00+00:00",
                "cash_dividend": 2.0,
            },
        ]
        daily = [
            {
                "bar_start_time": day + "T14:30:00+00:00",
                "bar_end_time": day + "T21:00:00+00:00",
                "available_time": day + "T21:00:00+00:00",
                "bar_status": "COMPLETE",
                "close": 10.0,
            }
            for day in ("2025-01-01", "2025-01-12", "2025-01-16")
        ]

        output = FactorEngineV4().compute(
            FactorGraphSpec.from_dict(compiled),
            {
                "dividend": {instrument_id: events},
                "price": {instrument_id: daily},
            },
        )

        self.assertEqual([1.0, 3.0, 2.0], [row["value"] for row in output[instrument_id]])


if __name__ == "__main__":
    unittest.main()
