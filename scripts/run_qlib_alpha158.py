from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from integrations.qlib import Alpha158ImportService
from services.data_platform.store import DataPlatformStore


def _manifest_ids(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        result.extend(item.strip() for item in value.split(",") if item.strip())
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compute Qlib Alpha158-compatible factors without VWAP from frozen DataTube Manifests."
    )
    parser.add_argument("--manifest-id", action="append", required=True)
    parser.add_argument("--input-bundle-id", default="")
    parser.add_argument("--start-time", default="")
    parser.add_argument("--end-time", default="")
    parser.add_argument("--output-root", default="")
    parser.add_argument("--metadata-db", default="")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        store = DataPlatformStore(args.metadata_db or None)
        result = Alpha158ImportService(
            store,
            output_root=args.output_root or None,
        ).run(
            manifest_ids=_manifest_ids(args.manifest_id),
            input_bundle_id=args.input_bundle_id,
            start_time=args.start_time,
            end_time=args.end_time,
            force=args.force,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {"status": "FAILED", "error": str(exc), "error_type": type(exc).__name__},
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

