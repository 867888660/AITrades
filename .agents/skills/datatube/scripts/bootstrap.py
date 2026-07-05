#!/usr/bin/env python3
"""Bootstrap DataTube without depending on the DataTube runtime.

This script is intentionally stdlib-only. It can install or locate the runtime,
create a virtual environment, install dependencies, copy example configs, start
the local Flask app, stop a process it started, and report JSON status.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5001
DEFAULT_BASE_URL = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}"
DEFAULT_REPO_URL = "https://github.com/867888660/AITrades.git"
PID_FILE = ".datatube/datatube.pid"


def emit(payload: Dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        status = payload.get("status") or payload.get("ok") or "unknown"
        print(f"DataTube bootstrap: {status}")
        for item in payload.get("messages", []):
            print(f"- {item}")


def ok(**extra: Any) -> Dict[str, Any]:
    return {"ok": True, **extra}


def fail(message: str, **extra: Any) -> Dict[str, Any]:
    return {"ok": False, "status": "failed", "error": message, **extra}


def http_json(path: str, base_url: str = DEFAULT_BASE_URL, timeout: float = 2.5) -> Dict[str, Any]:
    url = base_url.rstrip("/") + path
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    return json.loads(raw) if raw else {}


def is_port_open(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def find_runtime_root(start: Path) -> Optional[Path]:
    current = start.resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "app.py").exists() and (candidate / "requirements.txt").exists():
            return candidate
        runtime = candidate / "runtime"
        if (runtime / "app.py").exists() and (runtime / "requirements.txt").exists():
            return runtime
    env_root = os.environ.get("DATATUBE_RUNTIME_DIR")
    if env_root:
        path = Path(env_root).expanduser().resolve()
        if (path / "app.py").exists():
            return path
    return None


def default_install_root() -> Path:
    raw = os.environ.get("DATATUBE_HOME")
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.home() / ".datatube" / "runtime").resolve()


def clone_runtime(repo_url: str, dest: Path) -> Dict[str, Any]:
    if not shutil.which("git"):
        return fail("git is not available; clone DataTube manually or install git.", destination=str(dest))
    if dest.exists() and any(dest.iterdir()):
        return ok(status="runtime_exists", runtime_root=str(dest))
    dest.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "clone", "--depth", "1", repo_url, str(dest)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return fail("git clone failed", stderr=result.stderr.strip(), destination=str(dest))
    return ok(status="runtime_cloned", runtime_root=str(dest))


def venv_python(runtime_root: Path) -> Path:
    if os.name == "nt":
        return runtime_root / ".venv" / "Scripts" / "python.exe"
    return runtime_root / ".venv" / "bin" / "python"


def ensure_venv(runtime_root: Path) -> Dict[str, Any]:
    py = venv_python(runtime_root)
    messages: List[str] = []
    if not py.exists():
        result = subprocess.run([sys.executable, "-m", "venv", str(runtime_root / ".venv")], cwd=str(runtime_root), capture_output=True, text=True)
        if result.returncode != 0:
            return fail("failed to create virtual environment", stderr=result.stderr.strip())
        messages.append("created .venv")
    req = runtime_root / "requirements.txt"
    if req.exists():
        result = subprocess.run([str(py), "-m", "pip", "install", "-r", str(req)], cwd=str(runtime_root), capture_output=True, text=True)
        if result.returncode != 0:
            return fail("failed to install requirements", stderr=result.stderr.strip())
        messages.append("installed requirements.txt")
    return ok(status="venv_ready", python=str(py), messages=messages)


def copy_if_missing(src: Path, dst: Path) -> Optional[str]:
    if src.exists() and not dst.exists():
        shutil.copy2(src, dst)
        return f"created {dst.name} from {src.name}"
    return None


def ensure_configs(runtime_root: Path) -> List[str]:
    messages: List[str] = []
    for src_name, dst_name in [
        ("config.example.json", "config.json"),
        ("web_settings.example.json", "web_settings.json"),
    ]:
        msg = copy_if_missing(runtime_root / src_name, runtime_root / dst_name)
        if msg:
            messages.append(msg)
    for dirname in ["Data", "strategy_metrics_dbs", ".datatube"]:
        (runtime_root / dirname).mkdir(parents=True, exist_ok=True)
    return messages


def runtime_status(runtime_root: Optional[Path], base_url: str) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "status": "unknown",
        "runtime_root": str(runtime_root) if runtime_root else None,
        "base_url": base_url,
        "port_open": is_port_open(),
        "health": None,
        "capabilities": None,
    }
    try:
        payload["health"] = http_json("/api/health", base_url=base_url)
    except Exception as exc:  # noqa: BLE001
        payload["health_error"] = str(exc)
    try:
        payload["capabilities"] = http_json("/api/agent/capabilities", base_url=base_url)
    except Exception as exc:  # noqa: BLE001
        payload["capabilities_error"] = str(exc)
    payload["ok"] = bool(payload.get("health"))
    payload["status"] = "running" if payload["ok"] else "not_running"
    return payload


def ensure_runtime(args: argparse.Namespace, script_dir: Path) -> Dict[str, Any]:
    runtime_root = find_runtime_root(script_dir)
    messages: List[str] = []
    if runtime_root is None:
        repo_url = args.repo_url or os.environ.get("DATATUBE_REPO_URL") or DEFAULT_REPO_URL
        if not repo_url:
            return fail(
                "DataTube runtime not found. Pass --repo-url or set DATATUBE_REPO_URL.",
                manual_command=f"python scripts/bootstrap.py ensure --repo-url {DEFAULT_REPO_URL} --json",
            )
        clone = clone_runtime(repo_url, default_install_root())
        if not clone.get("ok"):
            return clone
        runtime_root = Path(str(clone["runtime_root"]))
        messages.append(str(clone.get("status")))

    venv = ensure_venv(runtime_root)
    if not venv.get("ok"):
        return venv
    messages.extend(venv.get("messages", []))
    messages.extend(ensure_configs(runtime_root))
    return ok(status="ready", runtime_root=str(runtime_root), python=venv.get("python"), messages=messages)


def read_pid(runtime_root: Path) -> Optional[int]:
    path = runtime_root / PID_FILE
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def write_pid(runtime_root: Path, pid: int) -> None:
    path = runtime_root / PID_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(pid), encoding="utf-8")


def start_runtime(args: argparse.Namespace, script_dir: Path) -> Dict[str, Any]:
    ensured = ensure_runtime(args, script_dir)
    if not ensured.get("ok"):
        return ensured
    runtime_root = Path(str(ensured["runtime_root"]))
    base_url = args.base_url
    status = runtime_status(runtime_root, base_url)
    if status.get("ok"):
        return ok(status="already_running", runtime_root=str(runtime_root), base_url=base_url, messages=["DataTube is already responding."])

    py = venv_python(runtime_root)
    if not py.exists():
        return fail("virtual environment python not found", runtime_root=str(runtime_root))
    log_path = runtime_root / ".datatube" / "server.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = open(log_path, "ab", buffering=0)
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)  # type: ignore[attr-defined]
    proc = subprocess.Popen(
        [str(py), "app.py"],
        cwd=str(runtime_root),
        stdout=log,
        stderr=log,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags,
        start_new_session=(os.name != "nt"),
    )
    write_pid(runtime_root, proc.pid)
    deadline = time.time() + args.wait_seconds
    last_status: Dict[str, Any] = {}
    while time.time() < deadline:
        time.sleep(0.8)
        last_status = runtime_status(runtime_root, base_url)
        if last_status.get("ok"):
            return ok(
                status="started",
                runtime_root=str(runtime_root),
                base_url=base_url,
                pid=proc.pid,
                log=str(log_path),
                health=last_status.get("health"),
            )
    return fail("DataTube did not become healthy before timeout", pid=proc.pid, log=str(log_path), last_status=last_status)


def stop_runtime(args: argparse.Namespace, script_dir: Path) -> Dict[str, Any]:
    runtime_root = find_runtime_root(script_dir)
    if runtime_root is None:
        return fail("DataTube runtime not found.")
    pid = read_pid(runtime_root)
    if not pid:
        return ok(status="no_pid", messages=["No bootstrap-managed PID file found."])
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return ok(status="not_running", pid=pid)
    except Exception as exc:  # noqa: BLE001
        return fail("failed to stop process", pid=pid, error=str(exc))
    return ok(status="stopped", pid=pid)


def doctor(args: argparse.Namespace, script_dir: Path) -> Dict[str, Any]:
    runtime_root = find_runtime_root(script_dir)
    checks: Dict[str, Any] = {
        "runtime_found": runtime_root is not None,
        "runtime_root": str(runtime_root) if runtime_root else None,
        "python": sys.executable,
        "git_available": bool(shutil.which("git")),
        "port_open": is_port_open(),
    }
    if runtime_root:
        checks["app_py"] = (runtime_root / "app.py").exists()
        checks["requirements"] = (runtime_root / "requirements.txt").exists()
        checks["venv_python"] = venv_python(runtime_root).exists()
        checks["config_json"] = (runtime_root / "config.json").exists()
        checks["web_settings_json"] = (runtime_root / "web_settings.json").exists()
        checks["status"] = runtime_status(runtime_root, args.base_url)
    return ok(status="doctor_complete", checks=checks)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["ensure", "status", "start", "stop", "doctor", "repair", "update"])
    parser.add_argument("--repo-url", default="", help="Git repository URL for the DataTube runtime if it is not adjacent to this skill.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--wait-seconds", type=int, default=25)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    runtime_root = find_runtime_root(script_dir)
    if args.command == "ensure":
        result = ensure_runtime(args, script_dir)
    elif args.command == "status":
        result = runtime_status(runtime_root, args.base_url)
    elif args.command == "start":
        result = start_runtime(args, script_dir)
    elif args.command == "stop":
        result = stop_runtime(args, script_dir)
    elif args.command in {"doctor", "repair", "update"}:
        result = doctor(args, script_dir)
        if args.command == "repair":
            ensured = ensure_runtime(args, script_dir)
            result["repair"] = ensured
        if args.command == "update":
            result["message"] = "Update is reserved for tagged releases; run git pull manually for v1.0 development builds."
    else:
        result = fail("unsupported command")
    emit(result, args.json)
    if not result.get("ok"):
        sys.exit(1)


if __name__ == "__main__":
    main()
