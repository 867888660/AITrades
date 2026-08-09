from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from services.data_platform import DataPlatformStore, ResearchArtifactMaterializer


class ArtifactMaterializationTest(unittest.TestCase):
    def test_concurrent_same_artifact_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = DataPlatformStore(Path(temp) / "metadata.db")
            materializer = ResearchArtifactMaterializer(store, root=Path(temp) / "artifacts")

            def create() -> str:
                return materializer.materialize_rows(
                    artifact_type="FACTOR_EVALUATION",
                    logical_name="coverage",
                    rows=[{"name": "coverage", "value": 1.0}],
                    schema_version="factor-evaluation.v1",
                    output_folder="evaluations",
                    identity_context={"spec": "same"},
                ).artifact_id

            with ThreadPoolExecutor(max_workers=4) as pool:
                artifact_ids = list(pool.map(lambda _: create(), range(8)))
            self.assertEqual(1, len(set(artifact_ids)))

    def test_zero_order_backtest_can_be_materialized(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = DataPlatformStore(Path(temp) / "metadata.db")
            materializer = ResearchArtifactMaterializer(store, root=Path(temp) / "artifacts")
            result = SimpleNamespace(
                metrics={
                    "portfolio_spec_hash": "portfolio",
                    "execution_spec_hash": "execution",
                    "engine_version": "test-engine",
                    "code_hash": "test-code",
                    "random_seed": 0,
                },
                dataset_manifest_ids=(),
                universe_snapshot_ids=(),
                factor_artifact_ids=(),
                alpha_artifact_ids=(),
                orders=(),
                equity_curve=(),
                drawdown_curve=(),
                execution_spec={},
            )
            artifacts = materializer.materialize_backtest(
                logical_name="zero-orders",
                result=result,
                portfolio_target_artifact_id="portfolio-target-test",
            )
            self.assertEqual("BACKTEST_ORDERS", artifacts["orders"].artifact_type)
            self.assertEqual(0, artifacts["orders"].metadata["order_count"])
            self.assertEqual("POSITION_SERIES", artifacts["positions"].artifact_type)
            self.assertEqual("EQUITY_SERIES", artifacts["equity"].artifact_type)
            self.assertEqual("DRAWDOWN_SERIES", artifacts["drawdown"].artifact_type)
            self.assertEqual(
                {"positions", "equity", "drawdown", "orders", "result"},
                set(artifacts),
            )


if __name__ == "__main__":
    unittest.main()
