from __future__ import annotations

import unittest

from services.history_data_service import _backtest_workspace_only_strategy_id


class BacktestWorkspaceVisibilityTests(unittest.TestCase):
    def test_synthetic_workspace_strategy_is_classified_as_workspace_only(self) -> None:
        run = {
            "run_id": 52,
            "case_snapshot": {
                "case_name": "Case 2026-06-29 08:05:36",
                "run_strategy_id": 82,
                "run_strategy_snapshot": {
                    "strategy_id": 82,
                    "strategy_name": "Case 2026-06-29 08:05:36 / run 52",
                },
                "workspace_imported_at": "2026-07-05T02:34:50+00:00",
            },
            "metrics": {
                "workspace_strategy_id": 82,
                "workspace_imported_at": "2026-07-05T02:34:50+00:00",
            },
        }

        self.assertEqual(82, _backtest_workspace_only_strategy_id(run))

    def test_existing_monitoring_strategy_import_is_not_workspace_only(self) -> None:
        run = {
            "run_id": 77,
            "case_snapshot": {
                "case_name": "Existing strategy case",
                "run_strategy_id": 12,
                "workspace_strategy_created": False,
            },
            "metrics": {
                "workspace_strategy_id": 12,
                "workspace_strategy_created": False,
                "workspace_imported_at": "2026-07-24T00:00:00+00:00",
            },
        }

        self.assertEqual(0, _backtest_workspace_only_strategy_id(run))

    def test_plain_backtest_strategy_reference_is_not_workspace_only(self) -> None:
        run = {
            "run_id": 78,
            "strategy_id": 15,
            "case_snapshot": {"case_name": "Ordinary backtest"},
            "metrics": {},
        }

        self.assertEqual(0, _backtest_workspace_only_strategy_id(run))


if __name__ == "__main__":
    unittest.main()
