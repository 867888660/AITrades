from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.data_platform import (
    DataPlatformStore,
    RequirementWorkspaceService,
    default_requirement_spec,
)


class RequirementLibraryLoadingTest(unittest.TestCase):
    def test_metadata_listing_does_not_resolve_live_data_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = DataPlatformStore(Path(temporary_directory) / "metadata.db")
            service = RequirementWorkspaceService(store)
            created = service.create_library_requirement(
                {"spec": default_requirement_spec("Fast Library metadata")}
            )

            with patch.object(
                service,
                "library_data_status",
                side_effect=AssertionError("live status must remain lazy"),
            ):
                assets = service.list_library_assets(include_data_status=False)

            self.assertEqual([created["library_asset_id"]], [item["library_asset_id"] for item in assets])
            self.assertNotIn("data_status", assets[0])


if __name__ == "__main__":
    unittest.main()
