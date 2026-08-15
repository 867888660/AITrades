from __future__ import annotations

import argparse
import json
from pathlib import Path

from .research_run_service import FormalResearchRunExecutor, ResearchRunService
from .store import DataPlatformStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Execute one frozen formal Research Run")
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    store = DataPlatformStore(args.db_path)
    run = ResearchRunService(store).get(args.run_id)
    if run is None or str(run.get("status")) != "RUNNING":
        raise ValueError("isolated Research Run is not in RUNNING state")
    bundle = ResearchRunService(store).get_bundle(str(run.get("bundle_id") or ""))
    if bundle is None:
        raise ValueError("isolated Research Run has no Frozen Bundle")
    run["frozen_input"] = dict(bundle["canonical_payload"])
    output = FormalResearchRunExecutor(store).execute(run)
    Path(args.output).write_text(
        json.dumps(output, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
