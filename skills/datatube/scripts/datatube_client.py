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
from pathlib import Path
from typing import Any, Dict, Optional

# Force UTF-8 stdout to handle Chinese labels in capabilities/worker-status
# responses on Windows; without this, GBK consoles mangle UTF-8 bytes.
if sys.stdout.encoding.lower() not in {"utf-8", "utf8"}:
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

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
    source = raw
    if not raw.lstrip().startswith(("{", "[")):
        path = Path(raw)
        if path.is_file():
            source = path.read_text(encoding="utf-8-sig")
    try:
        value = json.loads(source)
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

    snapshot = run.get("case_snapshot") if isinstance(run.get("case_snapshot"), dict) else {}
    runtime = snapshot.get("run_strategy_runtime") if isinstance(snapshot.get("run_strategy_runtime"), dict) else {}
    source = runtime.get("signal_source") if isinstance(runtime.get("signal_source"), dict) else {}
    metrics = run.get("metrics") if isinstance(run.get("metrics"), dict) else {}
    lineage = metrics.get("lineage") if isinstance(metrics.get("lineage"), dict) else {}

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
        "metrics": compact_run_metrics(metrics),
        "runtime": {
            key: value
            for key, value in {
                "schema_version": runtime.get("schema_version"),
                "signal_source_type": runtime.get("signal_source_type") or metrics.get("signal_source_type"),
                "engine": runtime.get("engine") or metrics.get("backtest_engine"),
                "runtime_hash": runtime.get("runtime_hash") or metrics.get("strategy_runtime_hash"),
                "library_asset_id": source.get("library_asset_id"),
                "alpha_definition_id": source.get("alpha_definition_id"),
                "data_identity_mode": runtime.get("data_identity_mode") or lineage.get("data_identity_mode"),
                "dataset_manifest_ids": lineage.get("dataset_manifest_ids"),
            }.items()
            if value not in (None, "")
        },
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
    caps = sub.add_parser("capabilities")
    caps.add_argument("--section", default="", help="comma-separated sections: research,strategy,backtest,eventgraph,market,all")
    dash = sub.add_parser("dashboard")
    dash.add_argument("--limit", type=int, default=50)

    strategies = sub.add_parser("strategies")
    strategies.add_argument("--limit", type=int, default=100)
    strategies.add_argument("--sync-stats", action="store_true")

    strategy = sub.add_parser("strategy")
    strategy.add_argument("strategy_id", type=int)

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

    research_projects = sub.add_parser("research-projects")
    research_projects.add_argument("--limit", type=int, default=100)

    research_sessions = sub.add_parser("research-sessions")
    research_sessions.add_argument("--status", default="")
    research_sessions.add_argument("--project-id", default="")
    research_sessions.add_argument("--limit", type=int, default=100)

    research_start = sub.add_parser("research-start")
    research_start.add_argument("--data", required=True)

    research_resume = sub.add_parser("research-resume")
    research_resume.add_argument("anchor_type")
    research_resume.add_argument("anchor_id")
    research_resume.add_argument("--data", default="{}")

    research_session = sub.add_parser("research-session")
    research_session.add_argument("session_id")

    research_session_status = sub.add_parser("research-session-status")
    research_session_status.add_argument("session_id")
    research_session_status.add_argument("status")
    research_session_status.add_argument("--message", default="")

    research_session_continue = sub.add_parser("research-session-continue")
    research_session_continue.add_argument("session_id")

    research_need_human = sub.add_parser("research-session-need-human")
    research_need_human.add_argument("session_id")
    research_need_human.add_argument("--data", required=True)

    research_answer = sub.add_parser("research-session-answer")
    research_answer.add_argument("session_id")
    research_answer.add_argument("answer")

    research_iteration = sub.add_parser("research-iteration-create")
    research_iteration.add_argument("session_id")
    research_iteration.add_argument("--data", required=True)

    research_iteration_complete = sub.add_parser("research-iteration-complete")
    research_iteration_complete.add_argument("iteration_id")
    research_iteration_complete.add_argument("--data", required=True)

    research_project_create = sub.add_parser("research-project-create")
    research_project_create.add_argument("--data", required=True)

    research_universe_create = sub.add_parser("research-universe-create")
    research_universe_create.add_argument("project_id")
    research_universe_create.add_argument("--data", required=True)

    research_snapshot_create = sub.add_parser("research-snapshot-create")
    research_snapshot_create.add_argument("project_id")
    research_snapshot_create.add_argument("universe_definition_id")
    research_snapshot_create.add_argument("--data", required=True)

    research_definition_create = sub.add_parser("research-definition-create")
    research_definition_create.add_argument("project_id")
    research_definition_create.add_argument("--data", required=True)

    research_definition_validate = sub.add_parser("research-definition-validate")
    research_definition_validate.add_argument("project_id")
    research_definition_validate.add_argument("definition_id")
    research_definition_validate.add_argument("--data", default="{}")

    research_pin = sub.add_parser("research-pin")
    research_pin.add_argument("project_id")
    research_pin.add_argument("slot_key")
    research_pin.add_argument("--data", required=True)

    research_unpin = sub.add_parser("research-unpin")
    research_unpin.add_argument("project_id")
    research_unpin.add_argument("slot_key")
    research_unpin.add_argument("--data", required=True)

    research_universe_unbind = sub.add_parser("research-universe-unbind")
    research_universe_unbind.add_argument("project_id")
    research_universe_unbind.add_argument("universe_id")
    research_universe_unbind.add_argument("--data", default="{}")

    research_universe_ref_remove = sub.add_parser("research-universe-ref-remove")
    research_universe_ref_remove.add_argument("project_id")
    research_universe_ref_remove.add_argument("--data", default="{}")

    research_requirements = sub.add_parser("research-requirements-compile")
    research_requirements.add_argument("project_id")
    research_requirements.add_argument("--data", required=True)

    research_requirement_remove = sub.add_parser("research-requirement-remove")
    research_requirement_remove.add_argument("project_id")
    research_requirement_remove.add_argument("ref_id")
    research_requirement_remove.add_argument("--data", default="{}")

    research_library_archive = sub.add_parser("research-library-archive")
    research_library_archive.add_argument("project_id")
    research_library_archive.add_argument("library_asset_id")
    research_library_archive.add_argument("--data", default="{}")

    research_backfill = sub.add_parser("research-backfill-create")
    research_backfill.add_argument("project_id")
    research_backfill.add_argument("--data", required=True)

    research_preview = sub.add_parser("research-preview-create")
    research_preview.add_argument("project_id")
    research_preview.add_argument("--data", required=True)

    research_run = sub.add_parser("research-run-create")
    research_run.add_argument("project_id")
    research_run.add_argument("--data", required=True)

    research_execute = sub.add_parser("research-run-execute")
    research_execute.add_argument("project_id")
    research_execute.add_argument("--data", default="{}")

    inspection_traces = sub.add_parser("inspection-traces")
    inspection_traces.add_argument("--q", default="")
    inspection_traces.add_argument("--subject-type", default="")
    inspection_traces.add_argument("--subject-id", default="")
    inspection_traces.add_argument("--status", default="")
    inspection_traces.add_argument("--cursor", default="")
    inspection_traces.add_argument("--limit", type=int, default=50)

    inspection_trace = sub.add_parser("inspection-trace")
    inspection_trace.add_argument("trace_id")

    inspection_events = sub.add_parser("inspection-events")
    inspection_events.add_argument("trace_id")
    inspection_events.add_argument("--event-kind", default="")
    inspection_events.add_argument("--status", default="")
    inspection_events.add_argument("--severity", default="")
    inspection_events.add_argument("--q", default="")
    inspection_events.add_argument("--cursor", type=int, default=0)
    inspection_events.add_argument("--limit", type=int, default=100)

    inspection_event = sub.add_parser("inspection-event")
    inspection_event.add_argument("event_id")

    inspection_search = sub.add_parser("inspection-search")
    inspection_search.add_argument("trace_id")
    inspection_search.add_argument("query")
    inspection_search.add_argument("--limit", type=int, default=50)

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
        data = request("GET", query_path("/api/agent/capabilities", {"section": args.section}), base_url=base_url)
    elif cmd == "dashboard":
        data = request("GET", query_path("/api/agent/dashboard", {"limit": args.limit}), base_url=base_url)
    elif cmd == "strategies":
        data = request("GET", query_path("/api/agent/strategies", {
            "limit": args.limit,
            "sync_stats": "1" if args.sync_stats else "0",
        }), base_url=base_url)
    elif cmd == "strategy":
        data = request("GET", f"/api/agent/strategies/{args.strategy_id}", base_url=base_url)
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
    elif cmd == "research-projects":
        data = request("GET", query_path("/api/agent/research/projects", {"limit": args.limit}), base_url=base_url)
    elif cmd == "research-sessions":
        data = request("GET", query_path("/api/agent/research/sessions", {
            "status": args.status, "project_id": args.project_id, "limit": args.limit,
        }), base_url=base_url)
    elif cmd == "research-start":
        payload = parse_json_arg(args.data)
        payload["entry_mode"] = "START"
        data = request("POST", "/api/agent/research/sessions", base_url=base_url, payload=with_agent(payload))
    elif cmd == "research-resume":
        payload = parse_json_arg(args.data)
        payload.update({"entry_mode": "RESUME", "anchor_type": args.anchor_type, "anchor_id": args.anchor_id})
        data = request("POST", "/api/agent/research/sessions", base_url=base_url, payload=with_agent(payload))
    elif cmd == "research-session":
        data = request("GET", f"/api/agent/research/sessions/{urllib.parse.quote(args.session_id)}", base_url=base_url)
    elif cmd == "research-session-status":
        data = request(
            "POST", f"/api/agent/research/sessions/{urllib.parse.quote(args.session_id)}/status",
            base_url=base_url, payload=with_agent({"status": args.status, "message": args.message}),
        )
    elif cmd == "research-session-continue":
        data = request(
            "POST", f"/api/agent/research/sessions/{urllib.parse.quote(args.session_id)}/continue",
            base_url=base_url, payload=with_agent({}),
        )
    elif cmd == "research-session-need-human":
        data = request(
            "POST", f"/api/agent/research/sessions/{urllib.parse.quote(args.session_id)}/need-human",
            base_url=base_url, payload=with_agent(parse_json_arg(args.data)),
        )
    elif cmd == "research-session-answer":
        data = request(
            "POST", f"/api/agent/research/sessions/{urllib.parse.quote(args.session_id)}/answer",
            base_url=base_url, payload={"actor_type": "human", "actor_id": "local_user", "answer": args.answer},
        )
    elif cmd == "research-iteration-create":
        data = request(
            "POST", f"/api/agent/research/sessions/{urllib.parse.quote(args.session_id)}/iterations",
            base_url=base_url, payload=with_agent(parse_json_arg(args.data)),
        )
    elif cmd == "research-iteration-complete":
        data = request(
            "POST", f"/api/agent/research/iterations/{urllib.parse.quote(args.iteration_id)}/complete",
            base_url=base_url, payload=with_agent(parse_json_arg(args.data)),
        )
    elif cmd == "research-project-create":
        data = request("POST", "/api/agent/research/projects", base_url=base_url, payload=with_agent(parse_json_arg(args.data)))
    elif cmd == "research-universe-create":
        data = request(
            "POST", f"/api/agent/research/projects/{urllib.parse.quote(args.project_id)}/universes",
            base_url=base_url, payload=with_agent(parse_json_arg(args.data)),
        )
    elif cmd == "research-snapshot-create":
        data = request(
            "POST",
            f"/api/agent/research/projects/{urllib.parse.quote(args.project_id)}/universes/{urllib.parse.quote(args.universe_definition_id)}/snapshots",
            base_url=base_url, payload=with_agent(parse_json_arg(args.data)),
        )
    elif cmd == "research-definition-create":
        data = request(
            "POST", f"/api/agent/research/projects/{urllib.parse.quote(args.project_id)}/definitions",
            base_url=base_url, payload=with_agent(parse_json_arg(args.data)),
        )
    elif cmd == "research-definition-validate":
        data = request(
            "POST",
            f"/api/agent/research/projects/{urllib.parse.quote(args.project_id)}/definitions/{urllib.parse.quote(args.definition_id)}/validate",
            base_url=base_url, payload=with_agent(parse_json_arg(args.data)),
        )
    elif cmd == "research-pin":
        data = request(
            "PUT",
            f"/api/agent/research/projects/{urllib.parse.quote(args.project_id)}/definition-refs/{urllib.parse.quote(args.slot_key, safe='')}",
            base_url=base_url, payload=with_agent(parse_json_arg(args.data)),
        )
    elif cmd == "research-unpin":
        data = request(
            "DELETE",
            f"/api/agent/research/projects/{urllib.parse.quote(args.project_id)}/definition-refs/{urllib.parse.quote(args.slot_key, safe='')}",
            base_url=base_url, payload=with_agent(parse_json_arg(args.data)),
        )
    elif cmd == "research-universe-unbind":
        data = request(
            "DELETE",
            f"/api/agent/research/projects/{urllib.parse.quote(args.project_id)}/universes/{urllib.parse.quote(args.universe_id)}",
            base_url=base_url, payload=with_agent(parse_json_arg(args.data)),
        )
    elif cmd == "research-universe-ref-remove":
        data = request(
            "DELETE",
            f"/api/agent/research/projects/{urllib.parse.quote(args.project_id)}/universe-ref",
            base_url=base_url, payload=with_agent(parse_json_arg(args.data)),
        )
    elif cmd == "research-requirement-remove":
        data = request(
            "DELETE",
            f"/api/agent/research/projects/{urllib.parse.quote(args.project_id)}/requirements/items/{urllib.parse.quote(args.ref_id)}",
            base_url=base_url, payload=with_agent(parse_json_arg(args.data)),
        )
    elif cmd == "research-library-archive":
        data = request(
            "DELETE",
            f"/api/agent/research/projects/{urllib.parse.quote(args.project_id)}/library/{urllib.parse.quote(args.library_asset_id)}/archive",
            base_url=base_url, payload=with_agent(parse_json_arg(args.data)),
        )
    elif cmd == "research-requirements-compile":
        data = request(
            "POST", f"/api/agent/research/projects/{urllib.parse.quote(args.project_id)}/requirement-sets",
            base_url=base_url, payload=with_agent(parse_json_arg(args.data)),
        )
    elif cmd == "research-backfill-create":
        data = request(
            "POST", f"/api/agent/research/projects/{urllib.parse.quote(args.project_id)}/backfill-tasks",
            base_url=base_url, payload=with_agent(parse_json_arg(args.data)),
        )
    elif cmd == "research-preview-create":
        data = request(
            "POST", f"/api/agent/research/projects/{urllib.parse.quote(args.project_id)}/run-input-previews",
            base_url=base_url, payload=with_agent(parse_json_arg(args.data)),
        )
    elif cmd == "research-run-create":
        data = request(
            "POST", f"/api/agent/research/projects/{urllib.parse.quote(args.project_id)}/runs",
            base_url=base_url, payload=with_agent(parse_json_arg(args.data)),
        )
    elif cmd == "research-run-execute":
        data = request(
            "POST", f"/api/agent/research/projects/{urllib.parse.quote(args.project_id)}/run-worker/run-once",
            base_url=base_url, payload=with_agent(parse_json_arg(args.data)), timeout=300.0,
        )
    elif cmd == "inspection-traces":
        data = request("GET", query_path("/api/agent/inspection/traces", {
            "q": args.q,
            "subject_type": args.subject_type,
            "subject_id": args.subject_id,
            "status": args.status,
            "cursor": args.cursor,
            "limit": args.limit,
        }), base_url=base_url)
    elif cmd == "inspection-trace":
        data = request(
            "GET", f"/api/agent/inspection/traces/{urllib.parse.quote(args.trace_id)}",
            base_url=base_url,
        )
    elif cmd == "inspection-events":
        data = request("GET", query_path(
            f"/api/agent/inspection/traces/{urllib.parse.quote(args.trace_id)}/events",
            {
                "event_kind": args.event_kind,
                "status": args.status,
                "severity": args.severity,
                "q": args.q,
                "cursor": args.cursor,
                "limit": args.limit,
            },
        ), base_url=base_url)
    elif cmd == "inspection-event":
        data = request(
            "GET", f"/api/agent/inspection/events/{urllib.parse.quote(args.event_id)}",
            base_url=base_url,
        )
    elif cmd == "inspection-search":
        data = request("GET", query_path(
            f"/api/agent/inspection/traces/{urllib.parse.quote(args.trace_id)}/search",
            {"q": args.query, "limit": args.limit},
        ), base_url=base_url)
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
