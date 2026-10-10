#!/usr/bin/env python3
"""Validate every agents/*/agent.toml. Exit 1 on the first problem list."""
import ast
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
# The library is installed as a file next to the scripts; a script reaches it
# through this path entry, never through an inlined copy.
LIB_PATH = re.compile(r"^sys\.path\.insert\(0, .*agents.*\)$", re.M)
LIB_FILE = ROOT / "lib" / "wcp_agent_lib.py"
REQUIRED = ["name", "version", "tier", "author", "script", "schedule", "default_schedule",
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
        errors += check_script(directory.name, meta, script.read_text())
    return errors


def check_script(name: str, meta: dict, text: str) -> list[str]:
    """The script header must agree with agent.toml, and the shared library is
    imported from the installed file, never embedded in the script."""
    errors = []
    header = re.search(r"^# wcp-agent-version: (\S+)$", text, re.M)
    if not header or header.group(1) != meta["version"]:
        errors.append(f"{name}: '# wcp-agent-version' must equal version in agent.toml")
    if "b64decode" in text:
        errors.append(f"{name}: no base64 blobs; import wcp_agent_lib from the installed file")
    if re.search(r"\bwcp_agent_lib\b", text) and not re.search(r"^from wcp_agent_lib import ", text, re.M):
        errors.append(f"{name}: use 'from wcp_agent_lib import ...'")
    if re.search(r"^from wcp_agent_lib import ", text, re.M) and not LIB_PATH.search(text):
        errors.append(f"{name}: put the agents directory on sys.path before importing wcp_agent_lib")
    header_name = re.search(r"^# wcp-agent: (\S+)$", text, re.M)
    if not header_name or header_name.group(1) != meta["name"]:
        errors.append(f"{name}: '# wcp-agent' must equal name in agent.toml")
    # Contract rule 4: a second start while one runs must do nothing. Both the
    # plain form (`flock -n 9`), the engine helper (`ops-engine agent lock`) and
    # a non-blocking `fcntl.flock` in Python (the backup agents) all count. Every
    # published agent has one, so it is an error.
    if not re.search(r"^\s*flock -n \d+|\bagent\s+lock\b|\bfcntl\.flock\(.*LOCK_NB", text, re.M):
        errors.append(f"{name}: no lock taken with 'flock -n' or 'ops-engine agent lock' (contract rule 4)")
    known = library_names()
    for imported in re.findall(r"^from wcp_agent_lib import (.+)$", text, re.M):
        for item in imported.split("#")[0].replace("(", "").replace(")", "").split(","):
            item = item.strip().split(" as ")[0].strip()
            if item and known and item not in known:
                errors.append(f"{name}: wcp_agent_lib has no '{item}'")
    return errors


def advise(name: str, text: str) -> list[str]:
    """Contract points that are not (yet) errors, because some agents already
    published do not meet them. New agents should."""
    notes = []
    # Both the plain form (trap) and the engine helper (`ops-engine agent
    # heartbeat`, docs/agent-api.md) satisfy the contract.
    if "heartbeat" not in text:
        notes.append(f"{name}: no heartbeat written on exit (contract rule 3)")
    return notes


def library_names() -> set[str]:
    """Public names defined at the top level of lib/wcp_agent_lib.py."""
    try:
        tree = ast.parse(LIB_FILE.read_text())
    except (OSError, SyntaxError):
        return set()
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return {n for n in names if not n.startswith("_")}


def main() -> int:
    errors = []
    for directory in sorted((ROOT / "agents").iterdir()):
        if directory.is_dir():
            errors += check(directory)
    for directory in sorted((ROOT / "agents").iterdir()):
        script = directory / "agent.sh"
        if directory.is_dir() and script.is_file():
            for note in advise(directory.name, script.read_text()):
                print(f"warning: {note}", file=sys.stderr)
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
