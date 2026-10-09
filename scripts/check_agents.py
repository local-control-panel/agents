#!/usr/bin/env python3
"""Validate every agents/*/agent.toml. Exit 1 on the first problem list."""
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
CRON = re.compile(r"^(@(hourly|daily|weekly|monthly|yearly)|([^\s]+\s+){4}[^\s]+)$")
TIERS = {"official", "community", "example"}
SCHEDULES = {"fixed", "configurable", "none"}
REQUIRED = ["name", "version", "tier", "script", "schedule", "default_schedule",
            "min_engine", "description", "paths"]


def check(directory: Path) -> list[str]:
    errors = []
    toml_path = directory / "agent.toml"
    if not toml_path.is_file():
        return [f"{directory.name}: agent.toml missing"]
    meta = tomllib.loads(toml_path.read_text())
    for key in REQUIRED:
        if key not in meta:
            errors.append(f"{directory.name}: missing key {key}")
    if errors:
        return errors
    if meta["name"] != directory.name or not NAME.match(meta["name"]):
        errors.append(f"{directory.name}: name must equal the directory and match a-z0-9-")
    for key in ("version", "min_engine"):
        if not SEMVER.match(meta[key]):
            errors.append(f"{directory.name}: {key} must be x.y.z")
    if meta["tier"] not in TIERS:
        errors.append(f"{directory.name}: tier must be one of {sorted(TIERS)}")
    if meta["schedule"] not in SCHEDULES:
        errors.append(f"{directory.name}: schedule must be one of {sorted(SCHEDULES)}")
    elif meta["schedule"] == "none":
        if meta["default_schedule"]:
            errors.append(f"{directory.name}: default_schedule must be empty when schedule = none")
    elif not CRON.match(meta["default_schedule"]):
        errors.append(f"{directory.name}: default_schedule is not a cron expression")
    script = directory / meta["script"]
    if not script.is_file() or script.parent != directory:
        errors.append(f"{directory.name}: script {meta['script']} not found in the directory")
    if not isinstance(meta["paths"], list) or not all(
        isinstance(p, str) and p.startswith("/") for p in meta["paths"]
    ):
        errors.append(f"{directory.name}: paths must be a list of absolute paths")
    elif any(".." in p.split("/") for p in meta["paths"]):
        errors.append(f"{directory.name}: paths must not contain ..")
    return errors


def main() -> int:
    errors = []
    for directory in sorted((ROOT / "agents").iterdir()):
        if directory.is_dir():
            errors += check(directory)
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
