from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Execute one durable history backtest in an isolated worker"
    )
    parser.add_argument("--run-id", required=True, type=int)
    args = parser.parse_args()

    # Import inside the child so the Web process never imports or executes the
    # strategy engine on behalf of this task.
    from services.history_data_service import _execute_backtest_run

    _execute_backtest_run(int(args.run_id))


if __name__ == "__main__":
    main()
