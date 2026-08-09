from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from services import history_data_service
from services.data_platform.definition_registry import DefinitionRegistry
from services.data_platform.portfolio import PortfolioSpec
from services.data_platform.store import DataPlatformStore
from services.library_alpha_backtest_adapter import LibraryAlphaHistoryBacktestAdapter
from services.history_data_service import _case_compatibility
from services.strategy_runtime_service import (
    LEGACY_HISTORY_ENGINE,
    LIBRARY_ALPHA_HISTORY_ENGINE,
    StrategyRuntimeCompiler,
)


class StrategyRuntimeBacktestTests(unittest.TestCase):
    def test_legacy_strategy_compiles_to_existing_history_engine(self):
        runtime = StrategyRuntimeCompiler().compile({
            "strategy_id": 7,
            "strategy_name": "Legacy",
            "strategy_code": "Stragy_Fllow_Truth",
            "signal_source": {
                "type": "LEGACY_STRATEGY_CODE",
                "strategy_code": "Stragy_Fllow_Truth",
            },
            "input_json": {},
        }, params={"entry_edge": "0.1"}).to_dict()
        self.assertEqual(runtime["engine"], LEGACY_HISTORY_ENGINE)
        self.assertEqual(runtime["strategy_code"], "Stragy_Fllow_Truth")
        self.assertEqual(runtime["params"]["entry_edge"], "0.1")
        self.assertEqual(len(runtime["runtime_hash"]), 64)

    def test_library_alpha_strategy_compiles_separate_execution_semantics(self):
        source = {
            "type": "LIBRARY_ALPHA",
            "library_asset_id": "library_alpha_1",
            "library_asset_version": 1,
            "alpha_definition_id": "alpha_1",
            "alpha_version": "1.0.0",
            "alpha_spec_hash": "alpha-hash",
            "factor_closure": [{
                "library_asset_id": "library_factor_1",
                "factor_definition_id": "factor_1",
                "factor_version": "1.0.0",
                "factor_spec_hash": "factor-hash",
            }],
        }
        with patch(
            "services.strategy_runtime_service.resolve_library_alpha_source",
            return_value={**source, "status": "REFERENCE_READY"},
        ):
            runtime = StrategyRuntimeCompiler().compile({
                "strategy_id": 8,
                "strategy_name": "Alpha",
                "strategy_code": "",
                "signal_source": source,
                "input_json": {},
            }, portfolio_spec={"top_n": 1, "rebalance_frequency": "EVERY_SIGNAL"}).to_dict()
        self.assertEqual(runtime["engine"], LIBRARY_ALPHA_HISTORY_ENGINE)
        self.assertEqual(runtime["execution_spec"]["order_submission"], "NEXT_BAR_OPEN")
        self.assertEqual(runtime["portfolio_spec"]["top_n"], 1)
        self.assertEqual(runtime["live_execution_status"], "NOT_CONNECTED")
        self.assertEqual(runtime["backtest_status"], "READY")

    def test_library_alpha_adapter_executes_factor_alpha_portfolio_and_costs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DataPlatformStore(Path(temp_dir) / "metadata.db")
            registry = DefinitionRegistry(store)
            factor = registry.create(
                "FACTOR",
                {
                    "name": "return_1",
                    "version": "1.0.0",
                    "operator": "pct_change",
                    "input_field": "close",
                    "window": 1,
                    "frequency": "1h",
                },
                state="VALIDATED",
            )
            alpha = registry.create(
                "ALPHA",
                {
                    "name": "return_alpha",
                    "version": "1.0.0",
                    "components": [{
                        "factor_definition_id": factor.definition_id,
                        "factor_version": factor.version,
                        "weight": 1.0,
                        "transform": "RAW",
                        "ascending": True,
                    }],
                    "minimum_coverage": 1.0,
                    "minimum_cross_section_size": 2,
                    "missing_policy": "EXCLUDE",
                    "rank_method": "AVERAGE",
                    "output_scale": "PERCENTILE",
                },
                state="VALIDATED",
            )
            runtime = {
                "signal_source_type": "LIBRARY_ALPHA",
                "runtime_hash": "a" * 64,
                "signal_source": {
                    "library_asset_id": "library_alpha_test",
                    "library_asset_version": 1,
                    "alpha_definition_id": alpha.definition_id,
                    "alpha_version": alpha.version,
                    "alpha_spec_hash": alpha.spec_hash,
                    "factor_closure": [{
                        "library_asset_id": "library_factor_test",
                        "library_asset_version": 1,
                        "factor_definition_id": factor.definition_id,
                        "factor_version": factor.version,
                        "factor_spec_hash": factor.spec_hash,
                    }],
                },
                "portfolio_spec": PortfolioSpec(top_n=1, rebalance_frequency="EVERY_SIGNAL").to_dict(),
                "execution_spec": {
                    "fee_bps": 2,
                    "slippage_bps": 5,
                },
            }
            bars = {
                "crypto_spot:binance:AAAUSDT": self._bars([100, 101, 103, 104, 108]),
                "crypto_spot:binance:BBBUSDT": self._bars([100, 99, 98, 101, 100]),
            }
            output = LibraryAlphaHistoryBacktestAdapter(store).execute(
                runtime,
                bars_by_instrument=bars,
                frequency="1h",
                case_id=42,
                initial_cash=10_000,
            )
            self.assertGreater(len(output.alpha_signals), 0)
            self.assertGreater(len(output.portfolio_targets), 0)
            self.assertGreater(len(output.result.orders), 0)
            self.assertEqual(output.lineage["history_case_id"], 42)
            self.assertEqual(output.lineage["strategy_runtime_hash"], "a" * 64)
            self.assertEqual(output.result.execution_spec["order_submission"], "NEXT_BAR_OPEN")
            self.assertEqual(output.result.metrics["instrument_count"], 2)
            self.assertGreater(output.result.metrics["fees"], 0)
            self.assertGreater(output.result.metrics["slippage_cost"], 0)

    def test_history_case_compatibility_accepts_alpha_without_strategy_code(self):
        legs = [
            {
                "source": "binance",
                "venue": "binance",
                "asset_class": "crypto_spot",
                "symbol": "AAAUSDT",
                "instrument_id": "crypto_spot:binance:AAAUSDT",
                "interval": "1h",
            },
            {
                "source": "binance",
                "venue": "binance",
                "asset_class": "crypto_spot",
                "symbol": "BBBUSDT",
                "instrument_id": "crypto_spot:binance:BBBUSDT",
                "interval": "1h",
            },
        ]
        strategy = {
            "strategy_code": "",
            "signal_source": {"type": "LIBRARY_ALPHA"},
            "legs": legs,
        }
        with patch("services.history_data_service._get_coverage_cached", return_value={"count": 100}):
            result = _case_compatibility(legs, strategy)
        self.assertEqual(result["severity"], "ok")
        self.assertFalse(any("strategy_code" in item["message"] for item in result["issues"]))

    def test_history_runner_exposes_registered_strategy_selection(self):
        source = (Path(__file__).parents[2] / "static" / "history_workspace.js").read_text(encoding="utf-8")
        self.assertIn('/api/registry/strategies', source)
        self.assertIn('signalSourceType === "LIBRARY_ALPHA"', source)
        self.assertIn('strategy_id: source.strategyId', source)

    def test_history_run_dispatches_library_alpha_and_persists_lineage(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = DataPlatformStore(root / "metadata.db")
            registry = DefinitionRegistry(store)
            factor = registry.create(
                "FACTOR",
                {
                    "name": "return_1",
                    "version": "1.0.0",
                    "operator": "pct_change",
                    "input_field": "close",
                    "window": 1,
                    "frequency": "1h",
                },
                state="VALIDATED",
            )
            alpha = registry.create(
                "ALPHA",
                {
                    "name": "return_alpha",
                    "version": "1.0.0",
                    "components": [{
                        "factor_definition_id": factor.definition_id,
                        "factor_version": factor.version,
                        "weight": 1.0,
                        "transform": "RAW",
                        "ascending": True,
                    }],
                    "minimum_coverage": 1.0,
                    "minimum_cross_section_size": 2,
                },
                state="VALIDATED",
            )
            source = {
                "type": "LIBRARY_ALPHA",
                "library_asset_id": "library_alpha_history_test",
                "library_asset_version": 1,
                "alpha_definition_id": alpha.definition_id,
                "alpha_version": alpha.version,
                "alpha_spec_hash": alpha.spec_hash,
                "factor_closure": [{
                    "library_asset_id": "library_factor_history_test",
                    "library_asset_version": 1,
                    "factor_definition_id": factor.definition_id,
                    "factor_version": factor.version,
                    "factor_spec_hash": factor.spec_hash,
                }],
            }
            legs = [
                {"source": "binance", "venue": "binance", "asset_class": "crypto_spot", "symbol": "AAAUSDT", "instrument_id": "crypto_spot:binance:AAAUSDT", "interval": "1h"},
                {"source": "binance", "venue": "binance", "asset_class": "crypto_spot", "symbol": "BBBUSDT", "instrument_id": "crypto_spot:binance:BBBUSDT", "interval": "1h"},
            ]
            strategy = {
                "strategy_id": 88,
                "strategy_name": "History Alpha",
                "strategy_code": "",
                "signal_source": source,
                "input_json": {"portfolio_spec": {"top_n": 1, "rebalance_frequency": "EVERY_SIGNAL"}},
                "legs": legs,
            }
            history_path = root / "history.db"
            with patch.object(history_data_service, "HISTORY_DB_PATH", history_path):
                conn = history_data_service._connect()
                try:
                    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
                    for index in range(30):
                        moment = start + timedelta(hours=index)
                        open_ms = int(moment.timestamp() * 1000)
                        for symbol, close in (("AAAUSDT", 100 + index * 2), ("BBBUSDT", 100 + (index % 4))):
                            conn.execute(
                                """INSERT INTO binance_klines(
                                       symbol, interval, open_time_ms, open_time_utc, open, high, low, close,
                                       volume, close_time_ms, fetched_at_utc
                                   ) VALUES (?, '1h', ?, ?, ?, ?, ?, ?, 1000, ?, ?)""",
                                (
                                    symbol,
                                    open_ms,
                                    moment.isoformat(),
                                    float(close),
                                    float(close) * 1.01,
                                    float(close) * 0.99,
                                    float(close),
                                    open_ms + 3_600_000 - 1,
                                    moment.isoformat(),
                                ),
                            )
                    conn.commit()
                finally:
                    conn.close()

                with (
                    patch("services.strategy_registry_service.get_strategy", return_value=strategy),
                    patch("services.strategy_runtime_service.resolve_library_alpha_source", return_value={**source, "status": "REFERENCE_READY"}),
                    patch("services.library_alpha_backtest_adapter.get_default_store", return_value=store),
                ):
                    case = history_data_service.create_backtest_case({
                        "case_name": "Alpha history integration",
                        "strategy_id": 88,
                        "legs": legs,
                        "params": {"initial_cash": 10_000, "min_points": 20},
                        "data_window": {
                            "start": start.isoformat(),
                            "end": (start + timedelta(hours=29)).isoformat(),
                            "strict": True,
                        },
                    })
                    run = history_data_service.create_backtest_run(case["case_id"], {"run_mode": "sync"})

            self.assertEqual(run["status"], "completed", run)
            self.assertEqual(run["metrics"]["signal_source_type"], "LIBRARY_ALPHA")
            self.assertEqual(run["metrics"]["lineage"]["data_identity_mode"], "HISTORY_CASE_SNAPSHOT")
            self.assertEqual(run["metrics"]["lineage"]["dataset_manifest_ids"], [])
            self.assertGreater(len(run["orders"]), 0)
            self.assertEqual(run["case_snapshot"]["run_strategy_runtime"]["engine"], LIBRARY_ALPHA_HISTORY_ENGINE)

    @staticmethod
    def _bars(closes):
        rows = []
        for index, close in enumerate(closes):
            start = f"2026-01-01T0{index}:00:00+00:00"
            end = f"2026-01-01T0{index + 1}:00:00+00:00"
            rows.append({
                "event_time": start,
                "bar_start_time": start,
                "bar_end_time": end,
                "available_time": end,
                "bar_status": "COMPLETE",
                "open": float(close),
                "high": float(close) * 1.01,
                "low": float(close) * 0.99,
                "close": float(close),
                "volume": 1000.0,
            })
        return rows


if __name__ == "__main__":
    unittest.main()
