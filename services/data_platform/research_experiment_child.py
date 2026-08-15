from __future__ import annotations

import argparse

from .research_experiment_service import ResearchExperimentService
from .store import DataPlatformStore


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Advance one Research Experiment in an isolated worker"
    )
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--experiment-id", required=True)
    args = parser.parse_args()

    store = DataPlatformStore(args.db_path)
    result = ResearchExperimentService(
        store,
        # The entire Experiment process is already inside one bounded Job
        # Object, so its formal Run should execute in this worker rather than
        # creating an unnecessary second Python process and data copy.
        isolate_run_execution=False,
        isolate_experiment_execution=False,
        defer_run_execution=True,
    ).advance(args.experiment_id)
    if not result:
        raise ValueError("Research Experiment does not exist")


if __name__ == "__main__":
    main()
