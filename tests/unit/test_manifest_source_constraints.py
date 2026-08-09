from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from services.data_platform import (
    CanonicalBarsCommitter,
    DataPlatformStore,
    DeterministicManifestResolver,
    RequirementCompiler,
)


class ManifestSourceConstraintTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = DataPlatformStore(root / "metadata.db")
        self.instrument_id = "equity:XNAS:TSLA"
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for source in ("BINANCE", "YFINANCE"):
            rows = [{
                "instrument_id": self.instrument_id,
                "frequency": "1d",
                "bar_start_time": (start + timedelta(days=index)).isoformat(),
                "bar_end_time": (start + timedelta(days=index + 1)).isoformat(),
                "available_time": (start + timedelta(days=index + 1)).isoformat(),
                "ingested_at": "2026-01-04T00:00:00+00:00",
                "open": 100 + index,
                "high": 101 + index,
                "low": 99 + index,
                "close": 100 + index,
                "volume": 1000,
                "turnover": 100000,
                "trade_count": 10,
                "bar_status": "COMPLETE",
                "source": source,
                "source_version": "1",
                "quality_status": "PASS",
            } for index in range(3)]
            CanonicalBarsCommitter(self.store, root / source.lower()).commit(
                dataset_id=f"{source.lower()}:TSLA:1d",
                instrument_id=self.instrument_id,
                asset_class="equity",
                venue="XNAS",
                frequency="1d",
                source=source,
                source_version="1",
                rows=rows,
            )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_requirement_source_policy_is_immutable_and_enforced(self) -> None:
        requirement_set = RequirementCompiler(self.store).compile(
            project_id="source_policy_project",
            manual_requirements=[{
                "id": "tsla_yfinance",
                "fields": ["close"],
                "source_selection_policy": {
                    "mode": "FIXED",
                    "allowed_sources": ["yfinance"],
                    "preferred_sources": ["yfinance"],
                },
            }],
            context={
                "instrument_ids": [self.instrument_id],
                "data_type": "bars",
                "frequency": "1d",
                "history_start": "2026-01-01T00:00:00+00:00",
                "history_end": "2026-01-03T00:00:00+00:00",
                "source_policy": "AUTO",
            },
        )
        requirement = requirement_set.requirements[0]
        self.assertEqual(["yfinance"], requirement.source_selection_policy["allowed_sources"])
        resolution = DeterministicManifestResolver(self.store).resolve(
            requirement_set.requirement_set_id,
            verify_physical=False,
        )
        self.assertTrue(resolution.ready, resolution.to_dict())
        self.assertEqual("YFINANCE", resolution.bindings[0]["source"])
        conflict = DeterministicManifestResolver(self.store).resolve(
            requirement_set.requirement_set_id,
            source_selection_policy={
                "mode": "FIXED",
                "allowed_sources": ["binance"],
                "preferred_sources": ["binance"],
            },
            verify_physical=False,
        )
        self.assertFalse(conflict.ready)
        self.assertTrue(any(
            str(item.code) == "PROVIDER_MISMATCH"
            for item in conflict.checks
        ))
