#!/usr/bin/env python3
"""Verify the MA crossover Factor on a physically validated BTCUSDT Manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq


DEFAULT_BASE_URL = "http://127.0.0.1:5001"
DEFAULT_INSTRUMENT = "crypto_spot:BINANCE:BTCUSDT"


def api_get(base_url: str, path: str, timeout: float) -> Any:
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        method="GET",
        headers={"Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8", "replace"))
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError(f"DataTube API request failed: {path}")
    return payload.get("data")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_partition(repo: Path, file_uri: str) -> Path:
    path = (repo / file_uri).resolve()
    try:
        path.relative_to(repo)
    except ValueError as exc:
        raise RuntimeError(f"Manifest partition escapes repository root: {file_uri}") from exc
    if not path.is_file():
        raise RuntimeError(f"Manifest partition is missing: {file_uri}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--instrument", default=DEFAULT_INSTRUMENT)
    parser.add_argument("--frequency", default="1h")
    parser.add_argument("--fast-window", type=int, default=5)
    parser.add_argument("--slow-window", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--event-limit", type=int, default=12)
    args = parser.parse_args()

    repo = Path(args.repo).expanduser().resolve()
    if not (repo / "services" / "data_platform").is_dir():
        raise SystemExit(f"DataTube repository not found: {repo}")
    sys.path.insert(0, str(repo))

    from services.data_platform import FactorEngine, FactorSpec

    catalog = api_get(args.base_url, "/api/research/data/catalog", args.timeout)
    candidates = [
        item
        for item in catalog or []
        if item.get("instrument_id") == args.instrument
        and item.get("frequency") == args.frequency
        and item.get("data_type") == "bars"
        and item.get("status") == "READY"
    ]
    if not candidates:
        raise SystemExit(f"No READY Dataset found for {args.instrument} {args.frequency}")
    dataset = max(candidates, key=lambda item: str(item.get("updated_at") or ""))
    manifest_id = str(dataset.get("latest_manifest_id") or "")
    if not manifest_id:
        raise SystemExit("Dataset does not expose latest_manifest_id")

    manifest_path = "/api/research/data/manifests/" + urllib.parse.quote(manifest_id) + "?verify=1"
    manifest = api_get(args.base_url, manifest_path, args.timeout)
    physical = manifest.get("physical_validation") or {}
    if manifest.get("status") != "READY" or physical.get("status") != "PASS":
        raise SystemExit(f"Manifest is not physically ready: {manifest_id}")

    rows: list[dict[str, Any]] = []
    checked_partitions: list[dict[str, Any]] = []
    for partition in manifest.get("partitions") or []:
        path = resolve_partition(repo, str(partition["file_uri"]))
        expected_checksum = str(partition.get("checksum") or "").removeprefix("sha256:")
        actual_checksum = sha256_file(path)
        if expected_checksum and actual_checksum != expected_checksum:
            raise SystemExit(f"Partition checksum mismatch: {partition['partition_id']}")
        partition_rows = pq.ParquetFile(path).read().to_pylist()
        rows.extend(item for item in partition_rows if item.get("instrument_id") == args.instrument)
        checked_partitions.append(
            {
                "partition_id": partition.get("partition_id"),
                "rows": len(partition_rows),
                "checksum": "PASS",
            }
        )

    rows.sort(key=lambda item: str(item.get("bar_start_time") or ""))
    if len(rows) < args.slow_window + 1:
        raise SystemExit(
            f"Insufficient observations: need {args.slow_window + 1}, found {len(rows)}"
        )

    spec = FactorSpec(
        name=f"btc_sma_{args.fast_window}_{args.slow_window}_cross",
        version="1.0.0",
        operator="ma_crossover",
        input_field="close",
        window=args.slow_window,
        parameters={"fast_window": args.fast_window},
        frequency=args.frequency,
        missing_policy="STRICT",
        output_unit="SIGNAL",
        output_direction="HIGHER_IS_BETTER",
    )
    values = FactorEngine().compute(spec, {args.instrument: rows})[args.instrument]
    closes = {str(item["bar_start_time"]): float(item["close"]) for item in rows}
    events = [
        {
            "event_time": item["event_time"],
            "available_time": item["available_time"],
            "signal": "GOLDEN_CROSS" if item["value"] == 1.0 else "DEATH_CROSS",
            "value": item["value"],
            "close": closes[item["event_time"]],
        }
        for item in values
        if item["value"] in {1.0, -1.0}
    ]
    golden_count = sum(item["value"] == 1.0 for item in events)
    death_count = sum(item["value"] == -1.0 for item in events)
    close_values = [float(item["close"]) for item in rows]
    fast_average = sum(close_values[-args.fast_window:]) / args.fast_window
    slow_average = sum(close_values[-args.slow_window:]) / args.slow_window

    report = {
        "ok": True,
        "factor": {
            "name": spec.name,
            "version": spec.version,
            "engine_version": spec.engine_version,
            "operator": spec.operator,
            "input_field": spec.input_field,
            "fast_window": args.fast_window,
            "slow_window": args.slow_window,
            "frequency": spec.frequency,
            "output_contract": {
                "GOLDEN_CROSS": 1,
                "DEATH_CROSS": -1,
                "NO_CROSS": 0,
            },
            "required_observations": spec.required_observations,
            "spec_hash": spec.spec_hash,
        },
        "data": {
            "dataset_id": dataset.get("dataset_id"),
            "manifest_id": manifest_id,
            "manifest_hash": manifest.get("manifest_hash"),
            "physical_validation": physical,
            "partitions": checked_partitions,
            "observations": len(rows),
            "start_time": rows[0]["bar_start_time"],
            "end_time": rows[-1]["bar_end_time"],
        },
        "result": {
            "golden_cross_count": golden_count,
            "death_cross_count": death_count,
            "total_cross_count": len(events),
            "latest_cross_events": events[-max(1, args.event_limit):],
            "latest_fast_ma": fast_average,
            "latest_slow_ma": slow_average,
            "latest_regime": "FAST_ABOVE_SLOW" if fast_average > slow_average else "FAST_BELOW_SLOW",
            "latest_close": close_values[-1],
        },
        "safety": {
            "runtime_writes_performed": False,
            "strategy_created_or_submitted": False,
            "virtual_or_live_trade_executed": False,
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
