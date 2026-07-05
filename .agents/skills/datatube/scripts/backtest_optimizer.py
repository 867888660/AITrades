#!/usr/bin/env python3
"""Parameter-sweep helper for DataTube backtests.

This script is intentionally conservative: it creates local historical backtest
runs and analyzes them, but it never writes live strategy parameters.
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from datatube_client import (
    DEFAULT_BASE_URL,
    query_path,
    request,
    summarize_backtest_detail,
    unwrap_agent_payload,
    with_agent,
)

TERMINAL_STATUSES = {"completed", "failed", "cancelled", "error"}
ESSENTIAL_METRIC_KEYS = {
    "total_return",
    "max_drawdown",
    "sharpe",
    "orders",
    "equity_points",
    "initial_equity",
    "final_equity",
    "period_start",
    "period_end",
    "strategy_code",
    "symbol",
    "workspace_strategy_id",
}


def parse_json_source(raw: str) -> Dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        raise SystemExit("--spec is required")
    if text == "-":
        text = sys.stdin.read().strip()
    if text.startswith("{"):
        data = json.loads(text)
    else:
        data = json.loads(Path(text).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("spec must be a JSON object")
    return data


def print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def as_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def clamp_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(low, min(parsed, high))


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def make_batch_id(prefix: str = "btopt") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{prefix}_{stamp}_{uuid.uuid4().hex[:8]}"


def generate_param_sets(spec: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    base_params = spec.get("base_params") if isinstance(spec.get("base_params"), dict) else {}
    search_space = spec.get("search_space") if isinstance(spec.get("search_space"), dict) else {}
    max_runs = clamp_int(spec.get("max_runs"), 30, 1, 500)
    mode = str(spec.get("mode") or "grid").strip().lower()

    if not search_space:
        return [dict(base_params)], {
            "mode": mode,
            "truncated": False,
            "total_combinations": 1,
            "used_combinations": 1,
            "note": "No search_space supplied; running base_params only.",
        }

    keys = sorted(search_space.keys())
    value_lists = [as_list(search_space[key]) for key in keys]
    total = 1
    for values in value_lists:
        total *= max(1, len(values))

    generated: List[Dict[str, Any]] = []
    for combo in itertools.product(*value_lists):
        params = dict(base_params)
        params.update({key: combo[idx] for idx, key in enumerate(keys)})
        generated.append(params)
        if len(generated) >= max_runs:
            break

    return generated, {
        "mode": mode,
        "search_keys": keys,
        "truncated": total > len(generated),
        "total_combinations": total,
        "used_combinations": len(generated),
        "max_runs": max_runs,
    }


def scalar_metrics(metrics: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in metrics.items()
        if key in ESSENTIAL_METRIC_KEYS
        and (isinstance(value, (str, int, float, bool)) or value is None)
    }


def score_result(metrics: Dict[str, Any], spec: Dict[str, Any]) -> Dict[str, Any]:
    objective = str(spec.get("objective") or "risk_adjusted").strip().lower()
    total_return = to_float(metrics.get("total_return"))
    max_drawdown = to_float(metrics.get("max_drawdown"))
    sharpe = to_float(metrics.get("sharpe"))
    orders = to_int(metrics.get("orders"))
    equity_points = to_int(metrics.get("equity_points"))

    drawdown_abs = abs(max_drawdown)
    max_orders = clamp_int(spec.get("max_orders"), 0, 0, 1000000)
    overtrade_penalty = 0.0
    if max_orders and orders > max_orders:
        overtrade_penalty = ((orders - max_orders) / max(max_orders, 1)) * 0.02

    if objective == "return":
        score = total_return - drawdown_abs * 0.25 - overtrade_penalty
    elif objective in {"drawdown", "low_drawdown"}:
        score = -drawdown_abs + total_return * 0.25 - overtrade_penalty
    else:
        drawdown_weight = to_float(spec.get("drawdown_weight"), 0.75)
        sharpe_weight = to_float(spec.get("sharpe_weight"), 0.02)
        score = total_return - drawdown_abs * drawdown_weight + sharpe * sharpe_weight - overtrade_penalty

    return {
        "score": score,
        "objective": objective,
        "total_return": total_return,
        "max_drawdown": max_drawdown,
        "sharpe": sharpe,
        "orders": orders,
        "equity_points": equity_points,
        "overtrade_penalty": overtrade_penalty,
    }


def unwrap_run_id(response: Any) -> int:
    run = unwrap_agent_payload(response)
    if not isinstance(run, dict):
        return 0
    return to_int(run.get("run_id"))


def get_run_summary(base_url: str, run_id: int, limits: Dict[str, int]) -> Dict[str, Any]:
    data = request("GET", query_path(f"/api/agent/backtests/runs/{run_id}", {
        "equity_limit": limits["equity"],
        "orders_limit": limits["orders"],
        "events_limit": limits["events"],
    }), base_url=base_url, timeout=30.0)
    summary = summarize_backtest_detail(data)
    return summary if isinstance(summary, dict) else {"ok": False, "raw": summary}


def create_runs(spec: Dict[str, Any], base_url: str, param_sets: List[Dict[str, Any]], batch_id: str, batch_name: str) -> List[Dict[str, Any]]:
    case_ids = spec.get("case_ids") if isinstance(spec.get("case_ids"), list) else []
    case_ids = [to_int(case_id) for case_id in case_ids if to_int(case_id) > 0]
    if not case_ids:
        case_id = to_int(spec.get("case_id"))
        if case_id > 0:
            case_ids = [case_id]
    if not case_ids:
        raise SystemExit("spec requires case_id or case_ids")

    strategy_id = to_int(spec.get("strategy_id"))
    strategy_code = str(spec.get("strategy_code") or "").strip()
    run_mode = str(spec.get("run_mode") or "async").strip().lower()
    if run_mode not in {"sync", "async"}:
        run_mode = "async"

    created: List[Dict[str, Any]] = []
    for case_id in case_ids:
        for param_index, params in enumerate(param_sets):
            payload: Dict[str, Any] = {
                "params": params,
                "run_mode": run_mode,
                "batch_id": batch_id,
                "batch_name": batch_name,
            }
            if strategy_id:
                payload["strategy_id"] = strategy_id
            if strategy_code:
                payload["strategy_code"] = strategy_code
            if spec.get("auto_download") is not None:
                payload["auto_download"] = bool(spec.get("auto_download"))
            response = request(
                "POST",
                f"/api/agent/backtests/cases/{case_id}/runs",
                base_url=base_url,
                payload=with_agent(payload),
                timeout=60.0,
            )
            run_id = unwrap_run_id(response)
            created.append({
                "case_id": case_id,
                "param_index": param_index,
                "run_id": run_id,
                "params": params,
            })
    return created


def wait_for_runs(base_url: str, created: List[Dict[str, Any]], spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    timeout_s = to_float(spec.get("timeout"), 900.0)
    interval_s = max(0.5, to_float(spec.get("interval"), 2.0))
    limits = {
        "equity": clamp_int(spec.get("detail_equity_limit"), 1, 1, 10000),
        "orders": clamp_int(spec.get("detail_orders_limit"), 1, 1, 10000),
        "events": clamp_int(spec.get("detail_events_limit"), 1, 1, 2000),
    }
    deadline = time.monotonic() + max(1.0, timeout_s)
    pending = {item["run_id"] for item in created if item.get("run_id")}
    summaries: Dict[int, Dict[str, Any]] = {}

    while pending:
        for run_id in list(pending):
            summary = get_run_summary(base_url, int(run_id), limits)
            summaries[int(run_id)] = summary
            run_info = summary.get("run") if isinstance(summary.get("run"), dict) else {}
            status = str(run_info.get("status") or "").lower()
            if status in TERMINAL_STATUSES:
                pending.discard(int(run_id))
        if not pending:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(interval_s)

    results: List[Dict[str, Any]] = []
    for item in created:
        run_id = int(item.get("run_id") or 0)
        summary = summaries.get(run_id) or get_run_summary(base_url, run_id, limits)
        results.append({**item, "summary": summary})
    return results


def analyze_results(results: List[Dict[str, Any]], spec: Dict[str, Any], batch_id: str, batch_name: str, plan: Dict[str, Any]) -> Dict[str, Any]:
    analyzed: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for item in results:
        summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
        run_info = summary.get("run") if isinstance(summary.get("run"), dict) else {}
        metrics = summary.get("metrics") if isinstance(summary.get("metrics"), dict) else {}
        status = str(run_info.get("status") or "").lower()
        scored = score_result(metrics, spec)
        row = {
            "run_id": item.get("run_id"),
            "case_id": item.get("case_id"),
            "param_index": item.get("param_index"),
            "status": status,
            "params": item.get("params") or {},
            "score": scored["score"],
            "score_detail": scored,
            "metrics": scalar_metrics(metrics),
            "strategy_metric_fields": summary.get("strategy_metric_fields") or [],
            "state_lane_fields": summary.get("state_lane_fields") or [],
        }
        if status == "completed":
            analyzed.append(row)
        else:
            rejected.append(row)

    analyzed.sort(key=lambda row: to_float(row.get("score")), reverse=True)
    by_return = sorted(analyzed, key=lambda row: to_float((row.get("metrics") or {}).get("total_return")), reverse=True)
    by_drawdown = sorted(analyzed, key=lambda row: abs(to_float((row.get("metrics") or {}).get("max_drawdown"))))
    top = analyzed[:5]

    missing_strategy_metrics = [
        row.get("run_id")
        for row in analyzed
        if not row.get("strategy_metric_fields") and not row.get("state_lane_fields")
    ]

    def leader_ref(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not row:
            return None
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        return {
            "run_id": row.get("run_id"),
            "case_id": row.get("case_id"),
            "param_index": row.get("param_index"),
            "score": row.get("score"),
            "total_return": metrics.get("total_return"),
            "max_drawdown": metrics.get("max_drawdown"),
            "sharpe": metrics.get("sharpe"),
            "orders": metrics.get("orders"),
        }

    return {
        "ok": True,
        "kind": "backtest_optimization_report",
        "batch_id": batch_id,
        "batch_name": batch_name,
        "plan": plan,
        "summary": {
            "runs_created": len(results),
            "completed": len(analyzed),
            "rejected": len(rejected),
            "objective": str(spec.get("objective") or "risk_adjusted"),
            "missing_strategy_metrics_run_ids": missing_strategy_metrics,
        },
        "best_score": leader_ref(top[0] if top else None),
        "best_return": leader_ref(by_return[0] if by_return else None),
        "lowest_drawdown": leader_ref(by_drawdown[0] if by_drawdown else None),
        "top_candidates": top,
        "rejected_runs": rejected,
        "next_round": {
            "base_params": (top[0].get("params") if top else None),
            "seed_params": [row.get("params") for row in top[:3]],
            "note": "Use these as candidate research parameters only. Do not apply to live trading without human confirmation.",
        },
    }


def analyze_existing_batch(base_url: str, batch_id: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    limit = clamp_int(spec.get("limit"), 100, 1, 500)
    data = request("GET", query_path("/api/agent/backtests/runs", {
        "batch_id": batch_id,
        "limit": limit,
    }), base_url=base_url, timeout=30.0)
    runs = unwrap_agent_payload(data)
    if not isinstance(runs, list):
        raise SystemExit("batch run list response was not a list")
    created = []
    for idx, run in enumerate(runs):
        if not isinstance(run, dict):
            continue
        snapshot = run.get("case_snapshot") if isinstance(run.get("case_snapshot"), dict) else {}
        created.append({
            "case_id": run.get("case_id"),
            "param_index": idx,
            "run_id": run.get("run_id"),
            "params": snapshot.get("run_params") if isinstance(snapshot.get("run_params"), dict) else {},
        })
    results = wait_for_runs(base_url, created, spec)
    return analyze_results(results, spec, batch_id, batch_id, {
        "mode": "analyze_existing_batch",
        "used_combinations": len(created),
        "truncated": len(created) >= limit,
    })


def write_optional_output(data: Dict[str, Any], out_path: str) -> None:
    if not out_path:
        return
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def run_command(args: argparse.Namespace) -> Dict[str, Any]:
    spec = parse_json_source(args.spec)
    param_sets, plan = generate_param_sets(spec)
    batch_id = str(spec.get("batch_id") or make_batch_id()).strip()
    batch_name = str(spec.get("batch_name") or f"AI Backtest Optimization {batch_id}").strip()
    plan.update({
        "case_id": spec.get("case_id"),
        "case_ids": spec.get("case_ids"),
        "batch_id": batch_id,
        "batch_name": batch_name,
    })
    if args.dry_run:
        return {
            "ok": True,
            "kind": "backtest_optimization_plan",
            "plan": plan,
            "param_sets": param_sets,
        }
    created = create_runs(spec, args.base_url, param_sets, batch_id, batch_name)
    results = wait_for_runs(args.base_url, created, spec)
    return analyze_results(results, spec, batch_id, batch_name, plan)


def analyze_command(args: argparse.Namespace) -> Dict[str, Any]:
    spec = parse_json_source(args.spec) if args.spec else {}
    return analyze_existing_batch(args.base_url, args.batch_id, spec)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="create parameter-sweep backtest runs and analyze them")
    run.add_argument("--spec", required=True, help="JSON object or path to JSON spec")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--out", default="")

    analyze = sub.add_parser("analyze", help="analyze an existing backtest batch")
    analyze.add_argument("batch_id")
    analyze.add_argument("--spec", default="", help="optional JSON object or path with objective/limits")
    analyze.add_argument("--out", default="")

    args = parser.parse_args()
    if args.command == "run":
        data = run_command(args)
    elif args.command == "analyze":
        data = analyze_command(args)
    else:
        raise SystemExit(f"unsupported command: {args.command}")
    write_optional_output(data, getattr(args, "out", ""))
    print_json(data)


if __name__ == "__main__":
    main()
