#!/usr/bin/env python3
"""Validate every agents/*/agent.toml and its script. Exit 1 when any is invalid.

The rules live in the operations engine (`ops-engine agent validate`), the same
code that decides what the engine installs, so this checker and an installing
server never disagree. This file parses the TOML, calls the engine and prints
what it says. Without an engine binary it fails (exit 2); it never falls back
to rules of its own.

usage: check_agents.py [--engine PATH] [NAME ...]
The engine is taken from --engine, then $OPS_ENGINE, then `ops-engine` on PATH.
"""
import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB_FILE = ROOT / "lib" / "wcp_agent_lib.py"
ENGINE_ENV = "OPS_ENGINE"


class EngineError(Exception):
    """The engine is missing or did not give a usable answer. Nothing was
    validated; callers must not treat this as a pass."""


def find_engine(explicit=None):
    """The engine binary: `explicit`, then $OPS_ENGINE, then `ops-engine` on PATH."""
    candidate = explicit or os.environ.get(ENGINE_ENV) or shutil.which("ops-engine")
    if candidate and os.access(candidate, os.X_OK) and Path(candidate).is_file():
        return str(Path(candidate).resolve())
    return None


def need_engine(explicit=None):
    engine = find_engine(explicit)
    if not engine:
        raise EngineError(
            "no ops-engine binary found: pass --engine PATH or set OPS_ENGINE "
            "(build one from the operations-engine repository, see CONTRIBUTING.md)"
        )
    return engine


def ask_engine(directory: Path, meta: dict, engine: str) -> dict:
    """The engine's `agent.validate` result for one agent: valid, agent,
    problems, warnings."""
    with tempfile.TemporaryDirectory() as scratch:
        manifest = Path(scratch) / "manifest.json"
        manifest.write_text(json.dumps(meta))
        try:
            done = subprocess.run(
                [engine, "agent", "validate", "--directory", str(directory),
                 "--manifest-file", str(manifest)],
                capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise EngineError(f"{directory.name}: cannot run the engine: {error}") from None
    try:
        answer = json.loads(done.stdout)
    except ValueError:
        answer = None
    if not isinstance(answer, dict) or answer.get("operation") != "agent.validate":
        raise EngineError(
            f"{directory.name}: this engine does not answer 'agent validate' "
            f"(exit {done.returncode}); use a newer engine"
        )
    if not answer.get("ok"):
        message = (answer.get("error") or {}).get("message", "unknown reason")
        raise EngineError(f"{directory.name}: the engine could not validate it: {message}")
    result = answer.get("result")
    if (not isinstance(result, dict) or not isinstance(result.get("valid"), bool)
            or not isinstance(result.get("problems"), list)
            or not isinstance(result.get("warnings"), list)):
        raise EngineError(f"{directory.name}: the engine's answer has an unexpected shape")
    if result["valid"] != (not result["problems"]):
        raise EngineError(f"{directory.name}: the engine's answer contradicts itself")
    return result


def library_errors(name: str, text: str) -> list[str]:
    """Names imported from wcp_agent_lib must exist in this repository's
    lib/wcp_agent_lib.py. The engine cannot see that file, so it is checked here."""
    errors = []
    known = library_names()
    for imported in re.findall(r"^from wcp_agent_lib import (.+)$", text, re.M):
        for item in imported.split("#")[0].replace("(", "").replace(")", "").split(","):
            item = item.strip().split(" as ")[0].strip()
            if item and known and item not in known:
                errors.append(f"{name}: wcp_agent_lib has no '{item}'")
    return errors


def check_full(directory: Path, engine: str) -> tuple[list[str], list[str]]:
    """(errors, warnings) for one agent directory, as printable lines."""
    name = directory.name
    toml_path = directory / "agent.toml"
    if not toml_path.is_file():
        return [f"{name}: agent.toml missing"], []
    try:
        meta = tomllib.loads(toml_path.read_text())
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        return [f"{name}: agent.toml is not valid TOML: {error}"], []
    result = ask_engine(directory, meta, engine)
    errors = [f"{name}: {p.get('message', p.get('code'))}" for p in result["problems"]]
    warnings = [f"{name}: {w.get('message', w.get('code'))}" for w in result["warnings"]]
    script = meta.get("script")
    if isinstance(script, str) and (directory / script).is_file() and "/" not in script:
        errors += library_errors(name, (directory / script).read_text())
    return errors, warnings


def check(directory: Path, engine: str = None) -> list[str]:
    return check_full(directory, need_engine(engine))[0]


def advise(directory: Path, engine: str = None) -> list[str]:
    return check_full(directory, need_engine(engine))[1]


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


def check_all(directories, engine: str = None) -> int:
    """Prints errors to stderr and warnings as 'warning: ...'; the exit code."""
    try:
        binary = need_engine(engine)
        errors, warnings = [], []
        for directory in directories:
            found = check_full(directory, binary)
            errors += found[0]
            warnings += found[1]
    except EngineError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--engine", help="the ops-engine binary (default: $OPS_ENGINE, then PATH)")
    parser.add_argument("names", nargs="*", help="agents to check (default: all)")
    args = parser.parse_args(argv)
    base = ROOT / "agents"
    directories = [base / n for n in args.names] or [d for d in sorted(base.iterdir()) if d.is_dir()]
    return check_all(directories, args.engine)


if __name__ == "__main__":
    sys.exit(main())
