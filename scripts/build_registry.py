#!/usr/bin/env python3
"""Generate registry.json from the checked-out tag. Used only by the release
workflow. Hashes are computed from the files on disk, never typed by hand.

usage: build_registry.py <release-tag> <commit-sha>
"""
import hashlib
import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(tag: str, commit: str) -> int:
    agents = {}
    for directory in sorted((ROOT / "agents").iterdir()):
        if not directory.is_dir():
            continue
        meta = tomllib.loads((directory / "agent.toml").read_text())
        if meta["tier"] == "example":
            continue
        files = {
            str(p.relative_to(ROOT)): sha256(p)
            for p in sorted(directory.rglob("*"))
            if p.is_file()
        }
        agents[meta["name"]] = {
            "version": meta["version"],
            "tier": meta["tier"],
            "schedule": meta["schedule"],
            "defaultSchedule": meta["default_schedule"],
            "minEngine": meta["min_engine"],
            "description": meta["description"],
            "paths": meta["paths"],
            "files": files,
        }
    revoked_file = ROOT / "revoked.json"
    revoked = json.loads(revoked_file.read_text()) if revoked_file.is_file() else []
    registry = {
        "schema": 1,
        "release": tag,
        "commit": commit,
        "agents": agents,
        "revoked": revoked,
    }
    (ROOT / "registry.json").write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
