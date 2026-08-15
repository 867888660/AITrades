from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.data_platform import DataPlatformStore, ResearchControlPlane


class ResearchProjectSummaryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = DataPlatformStore(Path(self.temp.name) / "summary.db")
        self.service = ResearchControlPlane(self.store)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_lists_card_summaries_without_live_data_resolution(self) -> None:
        newest = self.service.create_project(title="Newest", objective="bulk card")
        self.service.create_project(title="Older", objective="bulk card")

        summaries = self.service.list_project_summaries(limit=500)

        self.assertEqual(2, len(summaries))
        selected = next(item for item in summaries if item["project"]["project_id"] == newest["project_id"])
        self.assertEqual({}, selected["refs"])
        self.assertIsNone(selected["universeRef"])
        self.assertEqual([], selected["factors"])
        self.assertEqual([], selected["alphas"])
        self.assertFalse(selected["dataConfigured"])
        self.assertIsNone(selected["dataStatus"])
        self.assertIsNone(selected["coverage"])


if __name__ == "__main__":
    unittest.main()
