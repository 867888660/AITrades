from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from services.data_platform import DataPlatformStore, ResearchArtifactMaterializer
from services.data_platform.artifact_service import content_hash_for_rows


class ArtifactMaterializationTest(unittest.TestCase):
    def test_large_content_hash_is_order_independent_without_in_memory_sort(self) -> None:
        rows = [{"index": index, "value": index % 7} for index in range(20_001)]
        expected = content_hash_for_rows(rows, schema_version="large-test.v1")
        actual = content_hash_for_rows(reversed(rows), schema_version="large-test.v1")
        self.assertEqual(expected, actual)

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

    def test_materialize_rows_streams_across_arrow_batches(self) -> None:
        import pyarrow.parquet as pq

        with tempfile.TemporaryDirectory() as temp:
            store = DataPlatformStore(Path(temp) / "metadata.db")
            materializer = ResearchArtifactMaterializer(
                store, root=Path(temp) / "artifacts"
            )
            artifact = materializer.materialize_rows(
                artifact_type="FACTOR_EVALUATION",
                logical_name="streamed",
                rows=({"index": index, "value": float(index)} for index in range(20_001)),
                schema_version="streamed.v1",
                output_folder="evaluations",
            )

            parquet = pq.ParquetFile(Path(artifact.content_uri))
            try:
                self.assertEqual(20_001, parquet.metadata.num_rows)
            finally:
                parquet.close()

    def test_alpha_row_count_is_recorded_while_streaming(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = DataPlatformStore(Path(temp) / "metadata.db")
            materializer = ResearchArtifactMaterializer(
                store, root=Path(temp) / "artifacts"
            )
            spec = SimpleNamespace(
                name="stream_alpha",
                version="v1",
                spec_hash="spec",
                engine_version="engine",
                code_hash="code",
                to_dict=lambda: {"name": "stream_alpha"},
            )
            artifact = materializer.materialize_alpha(
                spec=spec,
                signals=[{
                    "as_of_time": "2026-01-01T00:00:00+00:00",
                    "scores": {"A": 1.0, "B": 2.0},
                    "ranks": {"A": 2.0, "B": 1.0},
                    "percentiles": {"A": 0.5, "B": 1.0},
                }],
                factor_artifact_ids=["factor"],
            )

            self.assertEqual(2, artifact.metadata["row_count"])

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
