"""Start the isolated OpenBB API with credentials from DataTube encrypted settings."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import subprocess
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.config_loader import BASE_DIR, load_web_settings
from services.data_source_definitions import OPENBB_CREDENTIAL_ENV


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
    credentials = settings.get("openbb_provider_credentials")
    credentials = dict(credentials) if isinstance(credentials, dict) else {}
    legacy_fred_key = str(settings.get("openbb_fred_api_key") or "").strip()
    if legacy_fred_key and not credentials.get("fred_api_key"):
        credentials["fred_api_key"] = legacy_fred_key
    for credential_key, environment_name in OPENBB_CREDENTIAL_ENV.items():
        secret = str(credentials.get(credential_key) or "").strip()
        if secret:
            env[environment_name] = secret
    runtime_marker = BASE_DIR / ".datatube" / "openbb-runtime.json"
    runtime_marker.parent.mkdir(parents=True, exist_ok=True)
    marker_payload = {
        "schema_version": "openbb.runtime.v1",
        "state": "starting",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "process_id": os.getpid(),
        "allowed_providers": sorted({
            str(item).strip().lower()
            for item in openbb.get("allowed_providers", [])
            if str(item).strip()
        }),
        "credential_keys_loaded": sorted(
            key for key in OPENBB_CREDENTIAL_ENV if credentials.get(key)
        ),
    }
    marker_payload["allowed_providers"] = marker_payload["allowed_providers"] or [
        str(openbb.get("default_provider") or "yfinance").strip().lower()
    ]
    runtime_marker.write_text(
        json.dumps(marker_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    command = [str(executable), "--host", host, "--port", str(port)]
    return_code = subprocess.run(command, cwd=BASE_DIR, env=env, check=False).returncode
    marker_payload.update({
        "state": "stopped",
        "stopped_at": datetime.now(timezone.utc).isoformat(),
        "return_code": return_code,
    })
    runtime_marker.write_text(
        json.dumps(marker_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    raise SystemExit(return_code)


if __name__ == "__main__":
    main()
