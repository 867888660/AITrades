from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pyarrow.parquet as pq

from services.data_platform.canonical_dataset import CanonicalDatasetCommitter
from services.data_platform.data_client import FrozenManifestData
from services.data_platform.store import DataPlatformStore


class CanonicalDatasetCommitterTest(unittest.TestCase):
    def test_sparse_partitions_share_schema_and_declare_coverage_without_fake_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = DataPlatformStore(root / "metadata.db")
            result = CanonicalDatasetCommitter(store, root / "canonical").commit(
                dataset_id="derived:test:sparse",
                instrument_id="equity:CRSP:ALL",
                data_type="sparse_panel",
                frequency="1d",
                source="DATATUBE_DERIVED",
                source_version="test.v1",
                schema_version="sparse_panel.v1",
                rows=[
                    {
                        "instrument_id": "equity:CRSP:10001",
                        "event_time": "2024-01-31T00:00:00+00:00",
                        "available_time": "2024-02-01T00:00:00+00:00",
                        "metric": None,
                    },
                    {
                        "instrument_id": "equity:CRSP:10001",
                        "event_time": "2024-02-29T00:00:00+00:00",
                        "available_time": "2024-03-01T00:00:00+00:00",
                        "metric": 1.0,
                    },
                ],
                coverage_start_time="2024-01-01T00:00:00+00:00",
                coverage_end_time="2024-03-01T23:59:59+00:00",
                metadata={"source_manifest_ids": ["manifest_source"]},
            )

            manifest = result["manifest"]
            schemas = [pq.ParquetFile(item.file_uri).schema_arrow for item in manifest.partitions]

            self.assertEqual(2, result["row_count"])
            self.assertTrue(all(schema.field("metric").type == schemas[0].field("metric").type for schema in schemas))
            self.assertEqual("double", str(schemas[0].field("metric").type))
            self.assertEqual("2024-01-01T00:00:00+00:00", manifest.partitions[0].start_time)
            self.assertEqual("2024-03-01T23:59:59+00:00", manifest.partitions[-1].end_time)
            self.assertEqual("PASS", FrozenManifestData(store, manifest.manifest_id).verify()["status"])


if __name__ == "__main__":
    unittest.main()
