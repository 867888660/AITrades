"""
Tests for Level 2 Partition Execution Engine
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.data_platform import (
    CheckpointManager,
    DataPlatformStore,
    PartitionPlan,
    PartitionedResearchExecutor,
    ResearchPartitionPlanner,
)


class PartitionPlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.checkpoint_root = Path(self.temp.name) / "checkpoints"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_small_research_uses_legacy_mode(self) -> None:
        planner = ResearchPartitionPlanner(self.checkpoint_root)
        frozen_input = {
            "history_start": "2024-01-01",
            "history_end": "2024-12-31",
            "factors": [{"name": "momentum", "window": 20}],
            "alphas": [{"name": "alpha1"}],
        }
        strategy = planner.plan(frozen_input, bundle_hash="test123")

        self.assertEqual("LEGACY", strategy.execution_mode)
        self.assertEqual(0, len(strategy.partitions))
        self.assertLess(strategy.total_estimated_mb, 3500)

    def test_large_research_requires_partition(self) -> None:
        planner = ResearchPartitionPlanner(self.checkpoint_root)
        frozen_input = {
            "history_start": "2000-01-01",
            "history_end": "2025-12-31",
            "asset_scope": {"asset_class": "US_EQUITY"},  # Triggers 5000 universe estimate
            "factors": [
                {"name": "momentum", "window": 20},
                {"name": "value", "window": 252},
            ],
            "alphas": [{"name": "alpha1"}, {"name": "alpha2"}],
        }
        strategy = planner.plan(frozen_input, bundle_hash="test123")

        self.assertEqual("PARTITIONED", strategy.execution_mode)
        self.assertEqual(26, len(strategy.partitions))  # 2000-2025 = 26 years
        self.assertGreater(strategy.total_estimated_mb, 10000)
        self.assertLess(strategy.per_partition_peak_mb, 5000)

    def test_partition_warmup_window_calculation(self) -> None:
        planner = ResearchPartitionPlanner(self.checkpoint_root)
        frozen_input = {
            "history_start": "2010-01-01",
            "history_end": "2022-12-31",  # 13 years - should trigger partition
            "asset_scope": {"asset_class": "US_EQUITY"},  # Triggers 5000 universe estimate
            "factors": [{"name": "momentum", "window": 252}],  # 1 year window
            "alphas": [{"name": "alpha1"}],
        }
        strategy = planner.plan(frozen_input, bundle_hash="test123")

        # Should have 13 partitions (2010-2022)
        self.assertEqual("PARTITIONED", strategy.execution_mode)
        self.assertEqual(13, len(strategy.partitions))

        # First partition: warmup cannot go before history_start
        first_partition = strategy.partitions[0]
        self.assertEqual("PARTITION_2010", first_partition.partition_id)
        self.assertEqual("2010-01-01", first_partition.calendar_start)
        self.assertEqual("2010-12-31", first_partition.calendar_end)
        # For first partition, warmup_start == history_start (no earlier data)
        self.assertEqual("2010-01-01", first_partition.warmup_start)

        # Second partition should have warmup from previous year
        second_partition = strategy.partitions[1]
        self.assertEqual("PARTITION_2011", second_partition.partition_id)
        self.assertLess(second_partition.warmup_start, "2011-01-01")
        self.assertEqual("2010-12-31", second_partition.warmup_end)

    def test_partition_estimated_memory(self) -> None:
        planner = ResearchPartitionPlanner(self.checkpoint_root)
        frozen_input = {
            "history_start": "2010-01-01",
            "history_end": "2020-12-31",  # 11 years - should trigger partition
            "asset_scope": {"asset_class": "US_EQUITY"},  # Triggers 5000 universe estimate
            "factors": [{"name": "f1"}, {"name": "f2"}],
            "alphas": [{"name": "a1"}],
        }
        strategy = planner.plan(frozen_input, bundle_hash="test123")

        self.assertEqual("PARTITIONED", strategy.execution_mode)
        partition = strategy.partitions[0]
        # Should have reasonable memory estimate
        self.assertGreater(partition.estimated_mb, 100)
        self.assertLess(partition.estimated_mb, 5000)


class CheckpointManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = DataPlatformStore(Path(self.temp.name) / "metadata.db")
        self.checkpoint_root = Path(self.temp.name) / "checkpoints"
        self.manager = CheckpointManager(self.store, self.checkpoint_root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_save_and_load_checkpoint(self) -> None:
        factor_rows = [
            {"instrument_id": "AAPL", "available_time": "2020-01-01", "value": 1.5},
            {"instrument_id": "MSFT", "available_time": "2020-01-01", "value": 2.3},
        ]
        alpha_rows = [
            {"instrument_id": "AAPL", "available_time": "2020-01-01", "alpha_value": 0.6},
            {"instrument_id": "MSFT", "available_time": "2020-01-01", "alpha_value": 0.8},
        ]

        # Save checkpoint
        checkpoint = self.manager.save(
            partition_id="PARTITION_2020",
            bundle_hash="abc123def456",
            factor_rows=factor_rows,
            alpha_rows=alpha_rows,
        )

        self.assertEqual("PARTITION_2020", checkpoint.partition_id)
        self.assertEqual("abc123def456", checkpoint.bundle_hash)
        self.assertEqual(2, checkpoint.row_count)

        # Load checkpoint
        loaded = self.manager.load("PARTITION_2020", "abc123def456")
        self.assertIsNotNone(loaded)
        self.assertEqual(checkpoint.checkpoint_id, loaded.checkpoint_id)
        self.assertEqual(checkpoint.verification_hash, loaded.verification_hash)

        # Read data
        loaded_factor_rows = self.manager.read_factor_rows(loaded)
        self.assertEqual(2, len(loaded_factor_rows))
        self.assertEqual("AAPL", loaded_factor_rows[0]["instrument_id"])

    def test_load_nonexistent_checkpoint_returns_none(self) -> None:
        loaded = self.manager.load("PARTITION_2099", "nonexistent")
        self.assertIsNone(loaded)

    def test_list_completed_checkpoints(self) -> None:
        # Save multiple checkpoints for same bundle
        for year in [2020, 2021, 2022]:
            self.manager.save(
                partition_id=f"PARTITION_{year}",
                bundle_hash="abc123",
                factor_rows=[{"id": year}],
                alpha_rows=[{"id": year}],
            )

        # List all checkpoints
        checkpoints = self.manager.list_completed("abc123")
        self.assertEqual(3, len(checkpoints))
        self.assertEqual("PARTITION_2020", checkpoints[0].partition_id)
        self.assertEqual("PARTITION_2021", checkpoints[1].partition_id)
        self.assertEqual("PARTITION_2022", checkpoints[2].partition_id)

    def test_checkpoint_verification(self) -> None:
        checkpoint = self.manager.save(
            partition_id="PARTITION_2020",
            bundle_hash="test",
            factor_rows=[{"id": 1}],
            alpha_rows=[{"id": 2}],
        )

        # Verify succeeds
        loaded = self.manager.load("PARTITION_2020", "test")
        self.assertIsNotNone(loaded)

        # Corrupt the file
        factor_path = Path(checkpoint.factor_artifact_id)
        factor_path.write_bytes(b"corrupted")

        # Verification should fail, returns None
        loaded_corrupted = self.manager.load("PARTITION_2020", "test")
        self.assertIsNone(loaded_corrupted)


if __name__ == "__main__":
    unittest.main()
