"""Start the isolated OpenBB API with credentials from DataTube encrypted settings."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.config_loader import BASE_DIR, load_web_settings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("run",))
    args = parser.parse_args()
    settings = load_web_settings()
    openbb = settings.get("openbb_settings") if isinstance(settings.get("openbb_settings"), dict) else {}
    base_url = str(openbb.get("base_url") or "http://127.0.0.1:6901")
    from urllib.parse import urlparse

    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 6901
    executable = BASE_DIR / ".openbb-venv" / "Scripts" / "openbb-api.exe"
    if not executable.is_file():
        raise FileNotFoundError("isolated OpenBB API is not installed")
    env = os.environ.copy()
    fred_key = str(settings.get("openbb_fred_api_key") or "").strip()
    if fred_key:
        env["FRED_API_KEY"] = fred_key
    command = [str(executable), "--host", host, "--port", str(port)]
    raise SystemExit(subprocess.run(command, cwd=BASE_DIR, env=env, check=False).returncode)


if __name__ == "__main__":
    main()
