#!/usr/bin/env python3
"""Sync .agents/skills/datatube → skills/datatube (generated publish artifact).

Run from repo root: python scripts/sync_skill.py
"""
import shutil
import sys
from pathlib import Path

# Force UTF-8 stdout on Windows to handle checkmark/emoji output
if sys.stdout.encoding.lower() not in {"utf-8", "utf8"}:
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def main():
    repo = Path(__file__).parent.parent
    src = repo / ".agents" / "skills" / "datatube"
    dst = repo / "skills" / "datatube"

    if not src.is_dir():
        print(f"ERROR: source not found: {src}", file=sys.stderr)
        return 1

    print(f"Syncing {src} → {dst}")
    if dst.exists():
        shutil.rmtree(dst)

    def ignore(_, names):
        return {"__pycache__", ".pytest_cache"} | {
            name for name in names if name.startswith("_tmp_") and name.endswith(".json")
        }

    shutil.copytree(src, dst, ignore=ignore, dirs_exist_ok=False)

    # Remove any stale _tmp_*.json or __pycache__ that snuck in
    for pattern in ["**/_tmp_*.json", "**/__pycache__"]:
        for item in dst.glob(pattern):
            if item.is_file():
                item.unlink()
            elif item.is_dir():
                shutil.rmtree(item)

    print(f"✓ Synced to {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
