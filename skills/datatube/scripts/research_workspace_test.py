#!/usr/bin/env python3
"""Deterministic, non-production-write checks for the DataTube Research workspace."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable


DEFAULT_BASE_URL = "http://127.0.0.1:5001"
EXPECTED_WORKSPACE_LABELS = (
    "Research",
    "Library",
    "Runs",
    "Data Catalog",
    "Agent Monitor",
    "Approvals",
    "Settings",
)
FORBIDDEN_WORKSPACE_LABELS = (
    "Research Projects",
    "Research Project",
    "Research Library",
    "Project Workspace",
)
EXPECTED_RUN_CONTRACT_LABELS = (
    "Factor Evaluation",
    "Alpha Evaluation",
    "Research Backtest",
    "Legacy Hybrid Run",
    "Alpha Evaluation boundary",
    "Research Backtest boundary",
)


def find_repo(explicit: str) -> Path | None:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    candidates.extend((Path.cwd(), *Path.cwd().parents))
    candidates.extend(Path(__file__).resolve().parents)
    seen: set[str] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)
        if (
            (resolved / "app.py").is_file()
            and (resolved / "services" / "data_platform").is_dir()
            and (resolved / "tests").is_dir()
        ):
            return resolved
    return None


def http_get(base_url: str, path: str, timeout: float, *, json_body: bool = True) -> Any:
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        method="GET",
        headers={"Accept": "application/json" if json_body else "text/html"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8", "replace")
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}: {path}")
        return json.loads(body) if json_body else body


def output_tail(text: str, limit: int = 2400) -> str:
    return (text or "").strip()[-limit:]


def run_process(command: list[str], repo: Path, timeout: float = 180.0) -> dict[str, Any]:
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=repo,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    combined = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
    details: dict[str, Any] = {
        "command": command,
        "exit_code": completed.returncode,
        "output_tail": output_tail(combined),
        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
    }
    match = re.search(r"Ran\s+(\d+)\s+tests?", combined)
    if match:
        details["tests_ran"] = int(match.group(1))
    return details


def execute_check(
    results: list[dict[str, Any]],
    code: str,
    action: Callable[[], dict[str, Any] | None],
) -> None:
    started = time.perf_counter()
    try:
        details = action() or {}
        results.append(
            {
                "code": code,
                "status": "PASS",
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                "details": details,
            }
        )
    except Exception as exc:
        results.append(
            {
                "code": code,
                "status": "FAIL",
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                "error": f"{type(exc).__name__}: {exc}",
            }
        )


def online_checks(results: list[dict[str, Any]], base_url: str, timeout: float) -> None:
    def health() -> dict[str, Any]:
        payload = http_get(base_url, "/api/health", timeout)
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise RuntimeError("health endpoint did not return ok=true")
        return {"ok": True}

    def capabilities() -> dict[str, Any]:
        payload = http_get(base_url, "/api/agent/capabilities", timeout)
        data = payload.get("data", payload) if isinstance(payload, dict) else {}
        if not isinstance(data, dict) or data.get("enabled") is not True:
            raise RuntimeError("Agent capability service is not enabled")
        allow = [str(item) for item in data.get("allow") or []]
        research_capabilities = [item for item in allow if item.startswith("research.")]
        return {
            "agent_id": data.get("agent_id"),
            "enabled": True,
            "research_capabilities": research_capabilities,
            "production_research_writes_allowed": bool(research_capabilities),
        }

    def dashboard() -> dict[str, Any]:
        payload = http_get(base_url, "/api/agent/dashboard?limit=10", timeout)
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise RuntimeError("Agent dashboard did not return ok=true")
        return {"ok": True}

    def research_json(path: str) -> Callable[[], dict[str, Any]]:
        def check() -> dict[str, Any]:
            payload = http_get(base_url, path, timeout)
            return {
                "response_type": type(payload).__name__,
                "top_level_fields": sorted(payload.keys()) if isinstance(payload, dict) else [],
            }

        return check

    def workspace_html() -> dict[str, Any]:
        html = http_get(base_url, "/research", timeout, json_body=False)
        javascript = http_get(
            base_url, "/static/research_workspace_simple.js", timeout, json_body=False
        )
        missing = [label for label in EXPECTED_WORKSPACE_LABELS if label not in html]
        if missing:
            raise RuntimeError(f"missing workspace labels: {', '.join(missing)}")
        exposed = [
            label
            for label in FORBIDDEN_WORKSPACE_LABELS
            if re.search(rf"\b{re.escape(label)}\b", html)
        ]
        if exposed:
            raise RuntimeError(f"forbidden workspace labels exposed: {', '.join(exposed)}")
        missing_run_labels = [
            label for label in EXPECTED_RUN_CONTRACT_LABELS if label not in javascript
        ]
        if missing_run_labels:
            raise RuntimeError(
                f"missing Research Run contract labels: {', '.join(missing_run_labels)}"
            )
        return {
            "labels": list(EXPECTED_WORKSPACE_LABELS),
            "forbidden_labels": list(FORBIDDEN_WORKSPACE_LABELS),
            "run_contract_labels": list(EXPECTED_RUN_CONTRACT_LABELS),
            "bytes": len(html.encode("utf-8")),
            "javascript_bytes": len(javascript.encode("utf-8")),
        }

    execute_check(results, "RUNTIME_HEALTH", health)
    execute_check(results, "AGENT_CAPABILITIES", capabilities)
    execute_check(results, "AGENT_DASHBOARD", dashboard)
    execute_check(
        results,
        "RESEARCH_ENGINE_CAPABILITIES",
        research_json("/api/research/engine-capabilities"),
    )
    execute_check(results, "RESEARCH_DEFINITIONS_READ", research_json("/api/research/definitions"))
    execute_check(results, "RESEARCH_LIBRARY_READ", research_json("/api/research/library"))
    execute_check(results, "RESEARCH_PROJECTS_READ", research_json("/api/research/projects"))
    execute_check(
        results,
        "RESEARCH_PREVIEWS_READ",
        research_json("/api/research/run-input-previews"),
    )
    execute_check(results, "RESEARCH_RUNS_READ", research_json("/api/research/runs"))
    execute_check(results, "RESEARCH_WORKSPACE_HTML", workspace_html)


def suite_checks(results: list[dict[str, Any]], repo: Path | None) -> None:
    if repo is None:
        results.append(
            {
                "code": "REPOSITORY_DISCOVERY",
                "status": "FAIL",
                "error": "DataTube repository not found; pass --repo explicitly.",
            }
        )
        return

    results.append(
        {
            "code": "REPOSITORY_DISCOVERY",
            "status": "PASS",
            "details": {"repo": str(repo)},
        }
    )

    node = shutil.which("node")
    if node:
        details = run_process([node, "--check", "static/research_workspace_simple.js"], repo)
        results.append(
            {
                "code": "JAVASCRIPT_SYNTAX",
                "status": "PASS" if details["exit_code"] == 0 else "FAIL",
                "details": details,
            }
        )
    else:
        results.append(
            {
                "code": "JAVASCRIPT_SYNTAX",
                "status": "SKIP",
                "reason": "node executable is unavailable",
            }
        )

    commands = (
        (
            "PYTHON_COMPILE",
            [sys.executable, "-m", "compileall", "-q", "app.py", "services/data_platform"],
        ),
        (
            "UNIT_TESTS",
            [sys.executable, "-m", "unittest", "discover", "-s", "tests/unit"],
        ),
        (
            "INTEGRATION_TESTS",
            [sys.executable, "-m", "unittest", "discover", "-s", "tests/integration"],
        ),
        (
            "FAILURE_INJECTION_TESTS",
            [sys.executable, "-m", "unittest", "discover", "-s", "tests/failure_injection"],
        ),
    )
    for code, command in commands:
        try:
            details = run_process(command, repo)
            results.append(
                {
                    "code": code,
                    "status": "PASS" if details["exit_code"] == 0 else "FAIL",
                    "details": details,
                }
            )
        except subprocess.TimeoutExpired as exc:
            results.append(
                {
                    "code": code,
                    "status": "FAIL",
                    "error": f"timed out after {exc.timeout}s",
                }
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("online", "suite", "all"), default="all")
    parser.add_argument("--repo", default="")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    repo = find_repo(args.repo)

    if args.mode in {"online", "all"}:
        online_checks(results, args.base_url, args.timeout)
    if args.mode in {"suite", "all"}:
        suite_checks(results, repo)

    failed = [item for item in results if item["status"] == "FAIL"]
    skipped = [item for item in results if item["status"] == "SKIP"]
    report = {
        "ok": not failed,
        "mode": args.mode,
        "repo": str(repo) if repo else None,
        "base_url": args.base_url if args.mode in {"online", "all"} else None,
        "summary": {
            "total": len(results),
            "passed": sum(item["status"] == "PASS" for item in results),
            "failed": len(failed),
            "skipped": len(skipped),
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        },
        "checks": results,
        "safety": {
            "runtime_writes_performed": False,
            "human_grant_created": False,
            "strategy_created_or_submitted": False,
            "virtual_or_live_trade_executed": False,
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
