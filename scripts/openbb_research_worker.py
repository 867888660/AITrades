"""Run approved OpenBB research export tasks through the existing control plane."""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.config_loader import load_web_settings
from services.data_platform import OpenBBResearchTaskExecutor, OpenBBResearchWorker, get_default_store


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker-id", default=f"openbb-worker-{socket.gethostname().lower()}")
    parser.add_argument("--lease-seconds", type=int, default=300)
    parser.add_argument("--continuous", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    args = parser.parse_args()
    executor = OpenBBResearchTaskExecutor(get_default_store(), load_web_settings())
    worker = OpenBBResearchWorker(executor, args.worker_id)
    if not args.continuous:
        print(json.dumps(worker.run_once(lease_seconds=max(30, args.lease_seconds)), ensure_ascii=False, default=str))
        return
    poll_seconds = max(0.5, min(300.0, args.poll_seconds))
    print(json.dumps({"status": "STARTED", "worker_id": args.worker_id, "poll_seconds": poll_seconds}, ensure_ascii=False))
    try:
        while True:
            result = worker.run_once(lease_seconds=max(30, args.lease_seconds))
            if result["status"] == "EXECUTED":
                print(json.dumps(result, ensure_ascii=False, default=str))
            else:
                time.sleep(poll_seconds)
    except KeyboardInterrupt:
        print(json.dumps({"status": "STOPPED", "worker_id": args.worker_id}, ensure_ascii=False))


if __name__ == "__main__":
    main()
