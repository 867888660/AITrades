from __future__ import annotations

import argparse
import json
import socket
import time

from services.data_platform import BinanceBackfillTaskExecutor, BinanceBackfillWorker, get_default_store


def main() -> None:
    parser = argparse.ArgumentParser(description="Run approved Binance research backfill tasks.")
    parser.add_argument("--once", action="store_true", help="Process at most one READY task and exit.")
    parser.add_argument("--worker-id", default=f"binance-backfill-{socket.gethostname().lower()}")
    parser.add_argument("--lease-seconds", type=int, default=300)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args()

    worker = BinanceBackfillWorker(
        BinanceBackfillTaskExecutor(get_default_store()),
        args.worker_id,
    )
    lease_seconds = max(30, args.lease_seconds)
    if args.once:
        print(json.dumps(worker.run_once(lease_seconds=lease_seconds), ensure_ascii=False, default=str))
        return
    while True:
        result = worker.run_once(lease_seconds=lease_seconds)
        if result["status"] != "IDLE":
            print(json.dumps(result, ensure_ascii=False, default=str), flush=True)
        time.sleep(max(0.25, args.poll_seconds))


if __name__ == "__main__":
    main()
