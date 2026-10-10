#!/usr/bin/env python3
"""Validate every agents/*/agent.toml. Exit 1 on the first problem list."""
import base64
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
CRON = re.compile(r"^(@(hourly|daily|weekly|monthly|yearly)|([^\s]+\s+){4}[^\s]+)$")
TIERS = {"official", "community", "example"}
# An agent whose author is exactly this name is first-party. Any other author
# is third-party. The engine and the panel hold the same constant. The checker
# cannot tell who really wrote an agent: the maintainers verify the name when
# they review the pull request that adds it.
FIRST_PARTY_AUTHORS = {"Website Control Panel"}
AUTHOR_MAX = 100
SCHEDULES = {"fixed", "configurable", "none"}
ISOLATIONS = {"cron", "systemd"}
# Mirrors the engine's agent_systemd::valid_writable_path: a path strictly below
# one of these directories, made of plain components.
SAFE_PATH = re.compile(r"^/(root/\.wcp|var/log|var/www|var/lib/wcp-agent)(/[A-Za-z0-9._-]+)+$")
LIB_BLOB = re.compile(r"b64decode\('([A-Za-z0-9+/=]+)'\), _agent_lib\.__dict__")
REQUIRED = ["name", "version", "tier", "author", "script", "schedule", "default_schedule",
            "min_engine", "description", "paths"]


def check(directory: Path, lib_text: str) -> list[str]:
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
    author = meta.get("author")
    if (
        not isinstance(author, str)
        or not author
        or author != author.strip()
        or len(author) > AUTHOR_MAX
        or any(ord(c) < 32 or ord(c) == 127 for c in author)
    ):
        errors.append(
            f"{directory.name}: author must be a non-empty name of at most {AUTHOR_MAX} "
            "characters, without surrounding spaces or control characters"
        )
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
    isolation = meta.get("isolation", "cron")
    writable = meta.get("writable_paths")
    if isolation not in ISOLATIONS:
        errors.append(f"{directory.name}: isolation must be one of {sorted(ISOLATIONS)}")
    elif isolation == "cron" and writable is not None:
        errors.append(f"{directory.name}: writable_paths only applies to isolation = systemd")
    elif isolation == "systemd" and (
        not isinstance(writable, list)
        or not all(isinstance(p, str) and SAFE_PATH.fullmatch(p) and "." not in p.split("/") and ".." not in p.split("/") for p in writable)
    ):
        # The engine writes these into a unit file: absolute, no spaces or quotes.
        errors.append(
            f"{directory.name}: a systemd agent needs writable_paths, a list of absolute "
            "paths below /root/.wcp, /var/log, /var/www or /var/lib/wcp-agent, using only A-Za-z0-9._-"
        )
    if script.is_file():
        errors += check_script(directory.name, meta, script.read_text(), lib_text)
    return errors


def check_script(name: str, meta: dict, text: str, lib_text: str) -> list[str]:
    """The script header must agree with agent.toml, and a bundled copy of the
    shared library must be the lib/ file. This replaces the pinned-digest test
    the engine and the panel used to keep for the built-in agents."""
    errors = []
    header = re.search(r"^# wcp-agent-version: (\S+)$", text, re.M)
    if not header or header.group(1) != meta["version"]:
        errors.append(f"{name}: '# wcp-agent-version' must equal version in agent.toml")
    blobs = LIB_BLOB.findall(text)
    if len(blobs) > 1:
        errors.append(f"{name}: the library is embedded {len(blobs)} times")
    if blobs and base64.b64decode(blobs[0]).decode() != lib_text:
        errors.append(f"{name}: embedded library differs from lib/wcp_agent_lib.py (run scripts/bundle_lib.py)")
    return errors


def main() -> int:
    errors = []
    lib_text = (ROOT / "lib" / "wcp_agent_lib.py").read_text()
    for directory in sorted((ROOT / "agents").iterdir()):
        if directory.is_dir():
            errors += check(directory, lib_text)
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
