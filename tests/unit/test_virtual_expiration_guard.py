import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from services.polymarket_service import _virtual_total_pnl_from_account
from services.strategy_chart_service import _apply_virtual_account_pnl_to_rows
from services.virtual_context_builder import _market_status
from services.virtual_execution import _leg_market_expiry_reason, execute_actions


class VirtualExpirationGuardTests(unittest.TestCase):
    def test_context_marks_market_expired_even_when_snapshot_says_open(self) -> None:
        status = _market_status(
            {"status": "open"},
            "2026-07-31T23:59:00Z",
            now_utc=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )

        self.assertEqual(status, "expired")

    def test_execution_guard_uses_leg_end_time(self) -> None:
        reason = _leg_market_expiry_reason(
            {
                "L0_AssetClass": "polymarket_binary",
                "L0_MarketStatus": "open",
                "L0_EndTime": "2026-07-31T23:59:00Z",
            },
            0,
            now_utc=datetime(2026, 8, 4, tzinfo=timezone.utc),
        )

        self.assertEqual(reason, "market_expired")

    def test_expired_buy_is_recorded_as_skipped_without_execution(self) -> None:
        connection = MagicMock()
        use_data = {
            "L0_AssetClass": "polymarket_binary",
            "L0_MarketStatus": "expired",
            "L0_EndTime": "2026-07-31T23:59:00Z",
        }

        with (
            patch("services.virtual_execution.ds_connect", return_value=connection),
            patch("services.virtual_execution._upsert_account"),
            patch("services.virtual_execution._mark_account_equity"),
            patch("services.virtual_execution._execute_buy") as execute_buy,
            patch("services.virtual_execution.write_action_event_conn") as write_event,
        ):
            orders, errors = execute_actions(
                strategy_id=86,
                strategy_bankroll=100.0,
                actions=[{"type": "BUY", "leg": 0, "side": "No", "qty": 50000}],
                use_data=use_data,
                tick_id=1,
            )

        self.assertEqual(orders, 0)
        self.assertEqual(errors, [])
        execute_buy.assert_not_called()
        self.assertEqual(write_event.call_args.kwargs["status"], "skipped")
        self.assertEqual(write_event.call_args.kwargs["reason"], "market_expired")

    def test_expired_summary_values_open_position_at_cost(self) -> None:
        strategy = {
            "mode": "Virtual",
            "raw": {"Enddate": "2026-07-31T23:59:00Z"},
        }
        positions = [
            {
                "side": "NO",
                "qty": 54052.29152738451,
                "avg": 0.001,
                "bid": 0.11,
                "ask": 0.001,
            }
        ]
        account = {
            "initial_cash": 100.0,
            "cash": 0.0,
            "realized_pnl": -38.47937286002108,
            "total_fees_paid": 7.468335612594436,
        }

        with patch("services.polymarket_service._virtual_expiry_account_snapshot", return_value=None):
            _virtual_total_pnl_from_account(strategy, 86, positions, account=account)

        self.assertEqual(strategy["pnl_source"], "virtual_account_expired_cost_fallback")
        self.assertAlmostEqual(strategy["virtual_equity"], 54.052291527384504)
        self.assertAlmostEqual(strategy["strategy_pnl"], -45.947708472615496)

    def test_expired_summary_uses_orders_before_deadline_when_available(self) -> None:
        strategy = {
            "mode": "Virtual",
            "raw": {"Enddate": "2026-07-31T23:59:00Z"},
        }
        account = {
            "initial_cash": 100.0,
            "cash": 0.0,
            "realized_pnl": -38.47937286002108,
            "total_fees_paid": 7.468335612594436,
        }
        expiry_snapshot = {
            "cash": 50.84064327485381,
            "open_cost": 0.0,
            "equity": 50.84064327485381,
            "positions": {},
            "included_orders": 8,
            "cutoff_at": "2026-07-31T23:59:00+00:00",
        }

        with patch(
            "services.polymarket_service._virtual_expiry_account_snapshot",
            return_value=expiry_snapshot,
        ):
            _virtual_total_pnl_from_account(strategy, 86, [], account=account)

        self.assertEqual(strategy["pnl_source"], "virtual_account_expired_order_cutoff")
        self.assertAlmostEqual(strategy["virtual_equity"], 50.84064327485381)
        self.assertAlmostEqual(strategy["strategy_pnl"], -49.15935672514619)
        self.assertEqual(strategy["virtual_expiry_included_orders"], 8)
        self.assertEqual(strategy["yes_qty"], 0.0)
        self.assertEqual(strategy["no_qty"], 0.0)

    def test_chart_ignores_fills_after_expiration_and_freezes_last_valid_pnl(self) -> None:
        account = {"initial_cash": 100.0}
        orders = [
            {
                "id": 1,
                "leg_index": 0,
                "action": "BUY",
                "side": "YES",
                "qty": 10.0,
                "price": 0.5,
                "gross_notional": 5.0,
                "fee_rate": 0.0,
                "fee": 0.0,
                "net_cash_change": -5.0,
                "liquidity_role": "taker",
                "status": "filled",
                "reason": "entry",
                "created_at_utc": "2026-07-31T23:58:00Z",
            },
            {
                "id": 2,
                "leg_index": 0,
                "action": "BUY",
                "side": "NO",
                "qty": 50000.0,
                "price": 0.001,
                "gross_notional": 50.0,
                "fee_rate": 0.05,
                "fee": 2.4975,
                "net_cash_change": -52.4975,
                "liquidity_role": "taker",
                "status": "filled",
                "reason": "expired_fill",
                "created_at_utc": "2026-08-04T00:04:10Z",
            },
        ]
        connection = MagicMock()
        connection.execute.side_effect = [
            MagicMock(fetchone=MagicMock(return_value=account)),
            MagicMock(fetchall=MagicMock(return_value=orders)),
        ]
        rows = [
            {
                "ts": "2026-07-31T23:58:00Z",
                "market_0_yes_bid": 0.6,
                "market_0_yes_ask": 0.61,
            },
            {
                "ts": "2026-08-04T00:05:00Z",
                "market_0_yes_bid": 0.999,
                "market_0_yes_ask": 0.89,
                "market_0_no_bid": 0.11,
                "market_0_no_ask": 0.001,
            },
        ]
        detail = {
            "mode": "Virtual",
            "row_id": 86,
            "strategy_bankroll": 100.0,
            "raw": {"Enddate": "2026-07-31T23:59:00Z"},
        }

        with patch("services.strategy_chart_service.strategy_data_source.connect", return_value=connection):
            _apply_virtual_account_pnl_to_rows(detail, rows, chart_interval_seconds=60)

        self.assertAlmostEqual(rows[0]["strategy_pnl"], 1.0)
        self.assertAlmostEqual(rows[-1]["strategy_pnl"], 1.0)
        self.assertEqual(rows[-1]["no_qty"], 0.0)
        self.assertEqual(rows[-1]["pnl_source"], "virtual_order_replay_expired_frozen")


if __name__ == "__main__":
    unittest.main()
