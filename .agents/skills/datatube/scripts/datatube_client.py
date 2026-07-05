#!/usr/bin/env python3
"""Small stdlib client for DataTube local APIs."""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

DEFAULT_BASE_URL = "http://127.0.0.1:5001"


def request(method: str, path: str, *, base_url: str, payload: Optional[Dict[str, Any]] = None, timeout: float = 20.0) -> Any:
    url = base_url.rstrip("/") + path
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            body = json.loads(raw)
        except Exception:
            body = raw
        raise SystemExit(json.dumps({"ok": False, "status": exc.code, "error": body}, ensure_ascii=False, indent=2))


def query_path(path: str, params: Dict[str, Any]) -> str:
    clean = {k: v for k, v in params.items() if v not in (None, "")}
    if not clean:
        return path
    return path + "?" + urllib.parse.urlencode(clean, doseq=True)


def print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def parse_json_arg(raw: str) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON payload: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit("JSON payload must be an object.")
    return value


def with_agent(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = dict(payload)
    data.setdefault("actor_type", "agent")
    data.setdefault("actor_id", "agent_strategy_assistant")
    return data


def unwrap_agent_payload(data: Any) -> Any:
    if not isinstance(data, dict):
        return data
    outer = data.get("data")
    if isinstance(outer, dict) and "data" in outer and ("actor" in outer or "read_only" in outer):
        return outer.get("data")
    return outer if outer is not None else data


def compact_equity_point(point: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(point, dict):
        return None
    return {
        key: point.get(key)
        for key in ("ts_utc", "equity", "cash", "exposure", "pnl")
        if key in point
    }


def compact_run_metrics(metrics: Any) -> Dict[str, Any]:
    if not isinstance(metrics, dict):
        return {}
    compact: Dict[str, Any] = {}
    for key, value in metrics.items():
        if key == "data_availability" and isinstance(value, dict):
            compact[key] = {
                "status": value.get("status"),
                "summary": value.get("summary"),
                "common_start": value.get("common_start"),
                "common_end": value.get("common_end"),
                "legs_count": len(value.get("legs") or []),
            }
        elif isinstance(value, (str, int, float, bool)) or value is None:
            compact[key] = value
    return compact


def summarize_backtest_detail(data: Any) -> Any:
    run = unwrap_agent_payload(data)
    if not isinstance(run, dict):
        return data

    equity = run.get("equity") if isinstance(run.get("equity"), list) else []
    orders = run.get("orders") if isinstance(run.get("orders"), list) else []
    events = run.get("events") if isinstance(run.get("events"), list) else []
    sample_meta: Dict[str, Any] = {}
    for point in reversed(equity):
        meta = point.get("meta") if isinstance(point, dict) else None
        if isinstance(meta, dict) and isinstance(meta.get("strategy_metrics"), dict):
            sample_meta = meta
            break

    metric_meta = sample_meta.get("strategy_metrics_meta") if isinstance(sample_meta, dict) else None
    raw_metrics = sample_meta.get("strategy_metrics") if isinstance(sample_meta, dict) else None
    strategy_metric_fields = []
    state_lane_fields = []
    if isinstance(metric_meta, dict):
        for key, spec in sorted(metric_meta.items()):
            kind = spec.get("kind") if isinstance(spec, dict) else ""
            panel = spec.get("panel") if isinstance(spec, dict) else ""
            if kind in {"state", "bool"} or panel == "metric_states":
                state_lane_fields.append(key)
            else:
                strategy_metric_fields.append(key)
    elif isinstance(raw_metrics, dict):
        for key, value in sorted(raw_metrics.items()):
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                strategy_metric_fields.append(key)
            else:
                state_lane_fields.append(key)

    summary: Dict[str, Any] = {
        "ok": data.get("ok", True) if isinstance(data, dict) else True,
        "compact": True,
        "note": "Use backtest-run with explicit limits, or backtest-wait --full, for raw equity/orders/events.",
        "run": {
            key: run.get(key)
            for key in (
                "run_id",
                "case_id",
                "batch_id",
                "strategy_id",
                "status",
                "started_at_utc",
                "updated_at_utc",
                "error",
            )
            if key in run
        },
        "metrics": compact_run_metrics(run.get("metrics")),
        "display_limits": run.get("display_limits") or {},
        "returned_counts": {
            "equity": len(equity),
            "orders": len(orders),
            "events": len(events),
        },
        "equity_sample": {
            "first": compact_equity_point(equity[0]) if equity else None,
            "last": compact_equity_point(equity[-1]) if equity else None,
        },
        "strategy_metric_fields": strategy_metric_fields,
        "state_lane_fields": state_lane_fields,
    }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("health")
    sub.add_parser("capabilities")
    dash = sub.add_parser("dashboard")
    dash.add_argument("--limit", type=int, default=50)

    ms = sub.add_parser("market-search")
    ms.add_argument("--q", default="")
    ms.add_argument("--category", default="")
    ms.add_argument("--sort", default="volume24h")
    ms.add_argument("--order", default="desc")
    ms.add_argument("--limit", type=int, default=20)

    bs = sub.add_parser("binance-search")
    bs.add_argument("--q", default="")
    bs.add_argument("--category", default="crypto_spot")
    bs.add_argument("--limit", type=int, default=20)

    ns = sub.add_parser("news-search")
    ns.add_argument("--q", required=True)
    ns.add_argument("--limit-per-source", type=int, default=20)

    eg = sub.add_parser("event-graph")
    eg.add_argument("--q", default="")
    eg.add_argument("--limit", type=int, default=10)

    sub.add_parser("event-status")

    ee = sub.add_parser("event-events")
    ee.add_argument("--q", default="")
    ee.add_argument("--limit", type=int, default=20)
    ee.add_argument("--include-observations", action="store_true")

    eo = sub.add_parser("event-observations")
    eo.add_argument("--q", default="")
    eo.add_argument("--event-id", default="")
    eo.add_argument("--limit", type=int, default=20)

    ec = sub.add_parser("event-core")
    ec.add_argument("--kind", choices=["events", "finance", "edges", "expressions"], default="events")
    ec.add_argument("--q", default="")
    ec.add_argument("--limit", type=int, default=20)

    ev = sub.add_parser("event-core-versions")
    ev.add_argument("--object-type", required=True)
    ev.add_argument("--object-id", required=True)
    ev.add_argument("--limit", type=int, default=20)

    ep = sub.add_parser("event-patch-validate")
    ep.add_argument("--data", required=True)

    ecr = sub.add_parser("event-change-request")
    ecr.add_argument("--data", required=True)

    ecrs = sub.add_parser("event-change-requests")
    ecrs.add_argument("--status", default="")
    ecrs.add_argument("--limit", type=int, default=50)

    ecrd = sub.add_parser("event-change-request-detail")
    ecrd.add_argument("request_id")

    act = sub.add_parser("activity")
    act.add_argument("--state", default="AI_DRAFTING")
    act.add_argument("--message", required=True)
    act.add_argument("--ref-type", default="workflow")
    act.add_argument("--ref-id", default="")
    act.add_argument("--workflow-id", default="")
    act.add_argument("--run-id", default="")

    drafts = sub.add_parser("drafts")
    drafts.add_argument("--limit", type=int, default=50)

    approvals = sub.add_parser("approvals")
    approvals.add_argument("--status", default="WAITING_HUMAN_CONFIRM")
    approvals.add_argument("--limit", type=int, default=50)

    btcases = sub.add_parser("backtest-cases")
    btcases.add_argument("--limit", type=int, default=100)

    btcasecreate = sub.add_parser("backtest-case-create")
    btcasecreate.add_argument("--data", required=True)

    btruns = sub.add_parser("backtest-runs")
    btruns.add_argument("--case-id", type=int, default=0)
    btruns.add_argument("--batch-id", default="")
    btruns.add_argument("--limit", type=int, default=100)

    btruncreate = sub.add_parser("backtest-run-create")
    btruncreate.add_argument("case_id", type=int)
    btruncreate.add_argument("--data", default="{}")

    btrun = sub.add_parser("backtest-run")
    btrun.add_argument("run_id", type=int)
    btrun.add_argument("--equity-limit", type=int, default=1000)
    btrun.add_argument("--orders-limit", type=int, default=1000)
    btrun.add_argument("--events-limit", type=int, default=300)
    btrun.add_argument("--summary", action="store_true")

    btwait = sub.add_parser("backtest-wait")
    btwait.add_argument("run_id", type=int)
    btwait.add_argument("--timeout", type=float, default=300.0)
    btwait.add_argument("--interval", type=float, default=2.0)
    btwait.add_argument("--equity-limit", type=int, default=1)
    btwait.add_argument("--orders-limit", type=int, default=1)
    btwait.add_argument("--events-limit", type=int, default=1)
    btwait.add_argument("--full", action="store_true")

    btbatches = sub.add_parser("backtest-batches")
    btbatches.add_argument("--limit", type=int, default=50)

    btbatchcreate = sub.add_parser("backtest-batch-create")
    btbatchcreate.add_argument("--data", required=True)

    btbatch = sub.add_parser("backtest-batch")
    btbatch.add_argument("batch_id")
    btbatch.add_argument("--include-runs", choices=["0", "1"], default="1")

    get = sub.add_parser("get")
    get.add_argument("path")

    post = sub.add_parser("post")
    post.add_argument("path")
    post.add_argument("--data", default="{}")

    args = parser.parse_args()
    base_url = args.base_url
    cmd = args.command

    if cmd == "health":
        data = request("GET", "/api/health", base_url=base_url)
    elif cmd == "capabilities":
        data = request("GET", "/api/agent/capabilities", base_url=base_url)
    elif cmd == "dashboard":
        data = request("GET", query_path("/api/agent/dashboard", {"limit": args.limit}), base_url=base_url)
    elif cmd == "market-search":
        data = request("GET", query_path("/api/agent/markets", {
            "q": args.q,
            "category": args.category,
            "sort": args.sort,
            "order": args.order,
            "limit": args.limit,
        }), base_url=base_url)
    elif cmd == "binance-search":
        data = request("GET", query_path("/api/binance/markets/search", {
            "q": args.q,
            "category": args.category,
            "limit": args.limit,
        }), base_url=base_url)
    elif cmd == "news-search":
        data = request("POST", "/api/agent/event-graph/news/search", base_url=base_url, payload=with_agent({
            "q": args.q,
            "limit_per_source": args.limit_per_source,
        }))
    elif cmd == "event-graph":
        data = request("GET", query_path("/api/agent/event-graph", {
            "q": args.q,
            "limit": args.limit,
        }), base_url=base_url)
    elif cmd == "event-status":
        data = request("GET", "/api/agent/event-graph/news/status", base_url=base_url)
    elif cmd == "event-events":
        data = request("GET", query_path("/api/agent/event-graph/events", {
            "q": args.q,
            "limit": args.limit,
            "include_observations": "1" if args.include_observations else "0",
        }), base_url=base_url)
    elif cmd == "event-observations":
        data = request("GET", query_path("/api/agent/event-graph/observations", {
            "q": args.q,
            "event_id": args.event_id,
            "limit": args.limit,
        }), base_url=base_url)
    elif cmd == "event-core":
        data = request("GET", query_path(f"/api/agent/event-graph/core/{args.kind}", {
            "q": args.q,
            "limit": args.limit,
        }), base_url=base_url)
    elif cmd == "event-core-versions":
        data = request("GET", query_path("/api/agent/event-graph/core/versions", {
            "object_type": args.object_type,
            "object_id": args.object_id,
            "limit": args.limit,
        }), base_url=base_url)
    elif cmd == "event-patch-validate":
        data = request("POST", "/api/agent/event-graph/patches/validate", base_url=base_url, payload=with_agent(parse_json_arg(args.data)))
    elif cmd == "event-change-request":
        data = request("POST", "/api/agent/event-graph/change-requests", base_url=base_url, payload=with_agent(parse_json_arg(args.data)))
    elif cmd == "event-change-requests":
        data = request("GET", query_path("/api/agent/event-graph/change-requests", {
            "status": args.status,
            "limit": args.limit,
        }), base_url=base_url)
    elif cmd == "event-change-request-detail":
        data = request("GET", f"/api/agent/event-graph/change-requests/{urllib.parse.quote(args.request_id)}", base_url=base_url)
    elif cmd == "activity":
        payload = with_agent({
            "state": args.state,
            "message": args.message,
            "ref_type": args.ref_type,
            "ref_id": args.ref_id or args.workflow_id,
        })
        if args.workflow_id:
            payload["workflow_id"] = args.workflow_id
        if args.run_id:
            payload["run_id"] = args.run_id
        data = request("POST", "/api/agent/activity", base_url=base_url, payload=payload)
    elif cmd == "drafts":
        data = request("GET", query_path("/api/agent/strategy-drafts", {"limit": args.limit}), base_url=base_url)
    elif cmd == "approvals":
        data = request("GET", query_path("/api/agent/approvals", {"status": args.status, "limit": args.limit}), base_url=base_url)
    elif cmd == "backtest-cases":
        data = request("GET", query_path("/api/agent/backtests/cases", {"limit": args.limit}), base_url=base_url)
    elif cmd == "backtest-case-create":
        data = request("POST", "/api/agent/backtests/cases", base_url=base_url, payload=with_agent(parse_json_arg(args.data)))
    elif cmd == "backtest-runs":
        data = request("GET", query_path("/api/agent/backtests/runs", {
            "case_id": args.case_id,
            "batch_id": args.batch_id,
            "limit": args.limit,
        }), base_url=base_url)
    elif cmd == "backtest-run-create":
        data = request("POST", f"/api/agent/backtests/cases/{args.case_id}/runs", base_url=base_url, payload=with_agent(parse_json_arg(args.data)))
    elif cmd == "backtest-run":
        data = request("GET", query_path(f"/api/agent/backtests/runs/{args.run_id}", {
            "equity_limit": args.equity_limit,
            "orders_limit": args.orders_limit,
            "events_limit": args.events_limit,
        }), base_url=base_url)
        if args.summary:
            data = summarize_backtest_detail(data)
    elif cmd == "backtest-wait":
        deadline = time.monotonic() + max(1.0, float(args.timeout))
        latest = None
        while True:
            latest = request("GET", query_path(f"/api/agent/backtests/runs/{args.run_id}", {
                "equity_limit": args.equity_limit,
                "orders_limit": args.orders_limit,
                "events_limit": args.events_limit,
            }), base_url=base_url, timeout=max(5.0, float(args.interval) + 3.0))
            envelope = (latest or {}).get("data") or {}
            run = envelope.get("data") if isinstance(envelope.get("data"), dict) else envelope
            status = str(run.get("status") or "").lower()
            if status in {"completed", "failed", "cancelled", "error"}:
                data = latest if args.full else summarize_backtest_detail(latest)
                break
            if time.monotonic() >= deadline:
                data = {
                    "ok": False,
                    "error": "timeout waiting for backtest run",
                    "latest": latest if args.full else summarize_backtest_detail(latest),
                }
                break
            time.sleep(max(0.5, float(args.interval)))
    elif cmd == "backtest-batches":
        data = request("GET", query_path("/api/agent/backtests/batches", {"limit": args.limit}), base_url=base_url)
    elif cmd == "backtest-batch-create":
        data = request("POST", "/api/agent/backtests/batches", base_url=base_url, payload=with_agent(parse_json_arg(args.data)))
    elif cmd == "backtest-batch":
        data = request("GET", query_path(f"/api/agent/backtests/batches/{urllib.parse.quote(args.batch_id)}", {
            "include_runs": args.include_runs,
        }), base_url=base_url)
    elif cmd == "get":
        path = args.path if args.path.startswith("/") else "/" + args.path
        data = request("GET", path, base_url=base_url)
    elif cmd == "post":
        path = args.path if args.path.startswith("/") else "/" + args.path
        data = request("POST", path, base_url=base_url, payload=parse_json_arg(args.data))
    else:
        raise SystemExit(f"unsupported command: {cmd}")
    print_json(data)


if __name__ == "__main__":
    main()
