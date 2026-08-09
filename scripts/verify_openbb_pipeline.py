"""Controlled local verification for the OpenBB -> bars.v1 -> Manifest pipeline."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.config_loader import load_web_settings
from services.data_platform import FrozenManifestData, OpenBBEquityHistoryAdapter


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument("--venue", choices=("XNAS", "XNYS"), default="XNAS")
    parser.add_argument("--provider", default="")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--adjustment", choices=("unadjusted", "splits_only", "splits_and_dividends"), default="splits_only")
    args = parser.parse_args()
    adapter = OpenBBEquityHistoryAdapter(load_web_settings())
    result = adapter.export({
        "symbol": args.symbol,
        "venue": args.venue,
        "provider": args.provider or None,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "adjustment": args.adjustment,
    })
    verification = FrozenManifestData(adapter.store, result["manifest"].manifest_id).verify()
    print({
        "dataset_id": result["dataset_id"],
        "manifest_id": result["manifest"].manifest_id,
        "row_count": result["row_count"],
        "source": f"OPENBB/{result['upstream_provider'].upper()}",
        "verification": verification,
    })


if __name__ == "__main__":
    main()
