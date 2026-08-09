from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.data_platform import DataPlatformStore, ManifestProvenanceService, request_hash, sanitized_request


class ManifestProvenanceTest(unittest.TestCase):
    def test_request_is_redacted_and_hash_is_stable(self):
        payload = {"symbol": "AAPL", "api_key": "secret", "nested": {"authorization": "bearer"}, "grant_id": "grant_x"}
        clean = sanitized_request(payload)
        self.assertEqual(clean["api_key"], "[REDACTED]")
        self.assertEqual(clean["nested"]["authorization"], "[REDACTED]")
        self.assertNotIn("grant_id", clean)
        self.assertEqual(request_hash(payload), request_hash(dict(reversed(list(payload.items())))))

    def test_manifest_provenance_is_immutable(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DataPlatformStore(Path(tmp) / "metadata.db")
            with store.transaction(immediate=True) as conn:
                conn.execute(
                    "INSERT INTO dataset_catalog(dataset_id,instrument_id,data_type,frequency,source,status,quality_status,schema_version,storage_path,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    ("dataset", "equity:XNAS:AAPL", "bars", "1d", "OPENBB/YFINANCE", "READY", "PASS", "bars.v1", "storage", "2026-01-01T00:00:00+00:00"),
                )
                conn.execute(
                    "INSERT INTO dataset_manifests(manifest_id,dataset_id,dataset_fingerprint,manifest_version,schema_version,status,manifest_hash,created_at,committed_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    ("manifest", "dataset", "fingerprint", 1, "bars.v1", "READY", "hash", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
                )
            service = ManifestProvenanceService(store)
            first = service.record(
                manifest_id="manifest", dataset_id="dataset", gateway="OPENBB", upstream_provider="yfinance",
                endpoint="equity.price.historical", request={"symbol": "AAPL", "api_key": "secret"},
            )
            self.assertEqual(first["request"]["api_key"], "[REDACTED]")
            with self.assertRaisesRegex(ValueError, "immutable"):
                service.record(
                    manifest_id="manifest", dataset_id="dataset", gateway="OPENBB", upstream_provider="fmp",
                    endpoint="equity.price.historical", request={"symbol": "AAPL"},
                )


if __name__ == "__main__":
    unittest.main()
