from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.data_platform import DataPlatformStore, UniverseService


class FakeFrozenManifest:
    def __init__(self, manifest_id: str, rows: dict[str, list[dict[str, object]]]):
        self.manifest_id = manifest_id
        self._rows = rows

    def read_bars_by_instrument(self, *, as_of: str | None = None) -> dict[str, list[dict[str, object]]]:
        return self._rows


class UniverseServiceTest(unittest.TestCase):
    def test_static_snapshot_is_idempotent_and_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service = UniverseService(DataPlatformStore(Path(temp) / "metadata.db"))
            definition = service.create_definition(
                name="static-five",
                version="1.0.0",
                universe_type="STATIC_LIST",
                parameters={"instrument_ids": ["B", "A", "A"]},
            )
            repeat = service.create_definition(
                name="static-five",
                version="1.0.0",
                universe_type="STATIC_LIST",
                parameters={"instrument_ids": ["A", "B"]},
            )
            self.assertEqual(definition.universe_definition_id, repeat.universe_definition_id)
            snapshot = service.resolve_snapshot(
                universe_definition_id=definition.universe_definition_id,
                as_of_time="2026-01-01T00:00:00+00:00",
            )
            same = service.resolve_snapshot(
                universe_definition_id=definition.universe_definition_id,
                as_of_time="2026-01-01T00:00:00+00:00",
            )
            self.assertEqual(snapshot.universe_snapshot_id, same.universe_snapshot_id)
            self.assertEqual(("A", "B"), snapshot.actual_instrument_ids)
            with self.assertRaisesRegex(ValueError, "immutable"):
                service.create_definition(
                    name="static-five",
                    version="1.0.0",
                    universe_type="STATIC_LIST",
                    parameters={"instrument_ids": ["A", "C"]},
                )

    def test_top_n_turnover_uses_only_available_lookback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service = UniverseService(DataPlatformStore(Path(temp) / "metadata.db"))
            definition = service.create_definition(
                name="turnover-top-two",
                version="1.0.0",
                universe_type="TOP_N_BY_TURNOVER",
                parameters={
                    "candidate_instrument_ids": ["A", "B", "C"],
                    "top_n": 2,
                    "lookback_bars": 2,
                },
            )
            rows = {
                "A": [
                    {"available_time": "2026-01-01T00:00:00+00:00", "turnover": 10},
                    {"available_time": "2026-01-01T01:00:00+00:00", "turnover": 20},
                    {"available_time": "2026-01-01T03:00:00+00:00", "turnover": 1000},
                ],
                "B": [
                    {"available_time": "2026-01-01T00:00:00+00:00", "turnover": 30},
                    {"available_time": "2026-01-01T01:00:00+00:00", "turnover": 30},
                ],
                "C": [
                    {"available_time": "2026-01-01T00:00:00+00:00", "turnover": 40},
                    {"available_time": "2026-01-01T01:00:00+00:00", "turnover": 40},
                ],
            }
            snapshot = service.resolve_snapshot(
                universe_definition_id=definition.universe_definition_id,
                as_of_time="2026-01-01T02:00:00+00:00",
                manifests=[FakeFrozenManifest("manifest_test", rows)],
            )
            self.assertEqual(("B", "C"), snapshot.actual_instrument_ids)
            self.assertEqual(15.0, snapshot.selection_inputs["turnover_average"]["A"])


if __name__ == "__main__":
    unittest.main()
