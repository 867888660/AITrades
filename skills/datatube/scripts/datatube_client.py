#!/usr/bin/env python3
"""Small stdlib client for DataTube local APIs."""
from __future__ import annotations

import argparse
import json
import sys
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

    sub.add_parser("event-graph")

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
        data = request("POST", "/api/agent/event-graph/news/search", base_url=base_url, payload={
            "actor_type": "agent",
            "actor_id": "agent_strategy_assistant",
            "q": args.q,
            "limit_per_source": args.limit_per_source,
        })
    elif cmd == "event-graph":
        data = request("GET", "/api/agent/event-graph", base_url=base_url)
    elif cmd == "activity":
        payload = {
            "actor_type": "agent",
            "actor_id": "agent_strategy_assistant",
            "state": args.state,
            "message": args.message,
            "ref_type": args.ref_type,
            "ref_id": args.ref_id or args.workflow_id,
        }
        if args.workflow_id:
            payload["workflow_id"] = args.workflow_id
        if args.run_id:
            payload["run_id"] = args.run_id
        data = request("POST", "/api/agent/activity", base_url=base_url, payload=payload)
    elif cmd == "drafts":
        data = request("GET", query_path("/api/agent/strategy-drafts", {"limit": args.limit}), base_url=base_url)
    elif cmd == "approvals":
        data = request("GET", query_path("/api/agent/approvals", {"status": args.status, "limit": args.limit}), base_url=base_url)
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
