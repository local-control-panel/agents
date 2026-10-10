#!/usr/bin/env python3
"""Developer tool for writing agents. Runs on your machine, never on a server.

  wcp_agent.py new NAME [--lang bash|python] [--author NAME] [--description TEXT]
                        [--schedule CRON]   scaffold agents/NAME
  wcp_agent.py check [NAME ...]             validate agent.toml and the script
  wcp_agent.py run NAME [--dry-run] [--json] run it against a scratch WCP_DIR
  wcp_agent.py list                         the agents in this repository
  wcp_agent.py show NAME                    one agent's manifest as JSON
  wcp_agent.py api                          the helpers in lib/wcp_agent_lib.py

See docs/agent-api.md.
"""
import argparse
import ast
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_agents  # noqa: E402

LIB = ROOT / "lib" / "wcp_agent_lib.py"
NAME_MAX = 64

TOML = '''name = "{name}"
version = "0.1.0"
tier = "community"
author = "{author}"
script = "agent.sh"
schedule = "{schedule_kind}"
default_schedule = "{schedule}"
min_engine = "0.1.0"
description = "{description}"
paths = ["/root/.wcp/agents", "/root/.wcp/logs"]
'''

# Both templates keep the four ingredients of the contract: the heartbeat on
# every exit, the lock, work inside the declared paths and a bounded log.
BASH = '''#!/usr/bin/env bash
# wcp-agent: {name}
# wcp-agent-version: 0.1.0
# wcp-agent-description: {description}
set -euo pipefail

NAME="{name}"
WCP_DIR="${{WCP_DIR:-/root/.wcp}}"
LOG_FILE="$WCP_DIR/logs/$NAME.log"
MAX_LINES=2000

mkdir -p "$WCP_DIR/agents" "$WCP_DIR/logs"

heartbeat() {{
  local code=$?
  echo "{{\\"ts\\":$(date +%s),\\"exit_code\\":$code}}" > "$WCP_DIR/agents/$NAME.heartbeat"
}}
trap heartbeat EXIT

exec 9>"$WCP_DIR/agents/$NAME.lock"
flock -n 9 || exit 0

# WCP_DRY_RUN=1 means: change nothing outside $WCP_DIR.
# TODO: replace this with the agent's work.
printf '{{"ts":%s,"agent":"%s","status":"ok","summary":"ran"}}\\n' "$(date +%s)" "$NAME" >> "$LOG_FILE"

if [ "$(wc -l < "$LOG_FILE")" -gt "$MAX_LINES" ]; then
  tail -n "$MAX_LINES" "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
fi
'''

PYTHON = '''#!/usr/bin/env bash
# wcp-agent: {name}
# wcp-agent-version: 0.1.0
# wcp-agent-description: {description}
set -euo pipefail

NAME="{name}"
WCP_DIR="${{WCP_DIR:-/root/.wcp}}"
export NAME WCP_DIR

mkdir -p "$WCP_DIR/agents" "$WCP_DIR/logs"

heartbeat() {{
  local code=$?
  echo "{{\\"ts\\":$(date +%s),\\"exit_code\\":$code}}" > "$WCP_DIR/agents/$NAME.heartbeat"
}}
trap heartbeat EXIT

exec 9>"$WCP_DIR/agents/$NAME.lock"
flock -n 9 || exit 0

python3 - <<'PY'
import os
import sys

sys.path.insert(0, os.path.join(os.environ.get("WCP_DIR", "/root/.wcp"), "agents"))
from wcp_agent_lib import dry_run, emit_result, list_sites

name = os.environ["NAME"]
# TODO: replace this with the agent's work. list_sites() is read-only;
# engine_call() refuses to change anything while WCP_DRY_RUN=1.
sites = list_sites()
emit_result(name, "ok", "checked %d sites" % len(sites), sites=len(sites), dry_run=dry_run())
PY
'''

# Stands in for ops-engine during `run`: answers capabilities and records
# every call, so an agent can be exercised without a server.
ENGINE_STUB = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$WCP_STUB_CALLS"
if [ "${1:-}" = "capabilities" ]; then
  echo '{"protocolVersion":1,"operation":"capabilities","ok":true,"result":{"operations":["version","capabilities","doctor"]},"warnings":[],"error":null}'
else
  echo '{"protocolVersion":1,"operation":"stub","ok":true,"result":null,"warnings":[],"error":null}'
fi
"""


# macOS has no flock(1). The shim implements `flock -n FD` for scripts under
# test; it is used only when the real command is missing.
FLOCK_SHIM = """#!/usr/bin/env bash
exec python3 -c 'import fcntl, sys
fd = int(sys.argv[-1])
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    sys.exit(1)' "$@"
"""


def fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


def toml_string(value: str) -> str:
    if any(ord(c) < 32 or c in '"\\' for c in value):
        raise ValueError("quotes, backslashes and control characters are not allowed here")
    return value


def cmd_new(args) -> int:
    name = args.name
    if not check_agents.NAME.match(name) or len(name) > NAME_MAX:
        return fail(f"name must match a-z0-9- and be at most {NAME_MAX} characters")
    target = ROOT / "agents" / name
    if target.exists():
        return fail(f"agents/{name} already exists")
    schedule = args.schedule or ""
    if schedule and not check_agents.CRON.match(schedule):
        return fail("schedule must be five cron fields or @daily and the like")
    try:
        author = toml_string(args.author)
        description = toml_string(args.description or f"TODO: describe what {name} does, in one sentence.")
        schedule = toml_string(schedule)
    except ValueError as exc:
        return fail(str(exc))
    values = dict(
        name=name, author=author, description=description, schedule=schedule,
        schedule_kind="configurable" if schedule else "none",
    )
    target.mkdir(parents=True)
    (target / "agent.toml").write_text(TOML.format(**values))
    (target / "agent.sh").write_text((PYTHON if args.lang == "python" else BASH).format(**values))
    (target / "agent.sh").chmod(0o755)
    errors = check_agents.check(target)
    if errors:
        shutil.rmtree(target)
        return fail("; ".join(errors))
    print(f"created agents/{name}. Next: edit it, then\n"
          f"  python3 scripts/wcp_agent.py run {name} --dry-run\n"
          f"  python3 scripts/wcp_agent.py check {name}")
    return 0


def agent_dirs(names):
    base = ROOT / "agents"
    if names:
        return [base / n for n in names]
    return [d for d in sorted(base.iterdir()) if d.is_dir()]


def cmd_check(args) -> int:
    errors = []
    for directory in agent_dirs(args.names):
        errors += check_agents.check(directory)
    for error in errors:
        print(error, file=sys.stderr)
    if not errors:
        print("ok")
    return 1 if errors else 0


def cmd_list(_args) -> int:
    for directory in agent_dirs([]):
        meta = tomllib.loads((directory / "agent.toml").read_text())
        print(f"{meta['name']:28} {meta['version']:8} {meta['tier']:10} {meta['schedule']:13} "
              f"{meta.get('author', '-')}")
    return 0


def cmd_show(args) -> int:
    path = ROOT / "agents" / args.name / "agent.toml"
    if not path.is_file():
        return fail(f"no agent {args.name}")
    print(json.dumps(tomllib.loads(path.read_text()), indent=2, sort_keys=True))
    return 0


def cmd_api(_args) -> int:
    tree = ast.parse(LIB.read_text())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            summary = (ast.get_docstring(node) or "").split("\n")[0]
            print(f"{node.name}({ast.unparse(node.args)})\n    {summary}")
    return 0


def run_agent(name: str, dry_run: bool = False, timeout: int = 60) -> dict:
    """Run agents/<name> the way the engine would, but inside a scratch WCP_DIR
    with a stub engine, then check the contract. Returns a report dict."""
    directory = ROOT / "agents" / name
    errors = check_agents.check(directory)
    if errors:
        return {"agent": name, "ok": False, "problems": errors}
    meta = tomllib.loads((directory / "agent.toml").read_text())
    scratch = Path(tempfile.mkdtemp(prefix=f"wcp-agent-{name}-"))
    try:
        agents_dir = scratch / "agents"
        agents_dir.mkdir()
        (scratch / "logs").mkdir()
        (scratch / "sites").mkdir()
        (scratch / "manifests").mkdir()
        # The engine installs the script as <name>.sh next to the library.
        installed = agents_dir / f"{name}.sh"
        shutil.copy(directory / meta["script"], installed)
        installed.chmod(0o755)
        shutil.copy(LIB, agents_dir / "wcp_agent_lib.py")
        stub = scratch / "ops-engine"
        stub.write_text(ENGINE_STUB)
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
        calls = scratch / "engine-calls.log"
        shim_dir = scratch / "bin"
        shim_dir.mkdir()
        if shutil.which("flock") is None:
            shim = shim_dir / "flock"
            shim.write_text(FLOCK_SHIM)
            shim.chmod(0o755)
        env = dict(os.environ)
        env.update(WCP_DIR=str(scratch), SITES_ROOT=str(scratch / "sites"),
                   WCP_SITES_MANIFEST_DIR=str(scratch / "manifests"),
                   OPS_ENGINE=str(stub), WCP_STUB_CALLS=str(calls))
        env["PATH"] = f"{shim_dir}{os.pathsep}{env.get('PATH', '')}"
        env.pop("WCP_DRY_RUN", None)
        if dry_run:
            env["WCP_DRY_RUN"] = "1"
        problems = []
        try:
            proc = subprocess.run(["bash", str(installed)], env=env, stdin=subprocess.DEVNULL,
                                  capture_output=True, text=True, timeout=timeout)
            code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired:
            code, stdout, stderr = None, "", ""
            problems.append(f"did not finish within {timeout} seconds")
        heartbeat = agents_dir / f"{name}.heartbeat"
        beat = None
        try:
            beat = json.loads(heartbeat.read_text())
        except (OSError, ValueError):
            problems.append("no valid heartbeat written on exit")
        if beat is not None and (not isinstance(beat, dict) or set(beat) != {"ts", "exit_code"}
                                 or beat["exit_code"] != code):
            problems.append(f"heartbeat {beat} does not match exit code {code}")
        # A second start while the lock is held must do nothing and succeed.
        lock_ok = None
        lock = agents_dir / f"{name}.lock"
        if lock.exists():
            import fcntl
            with open(lock, "w") as held:
                fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                try:
                    again = subprocess.run(["bash", str(installed)], env=env,
                                           stdin=subprocess.DEVNULL, capture_output=True,
                                           text=True, timeout=timeout)
                    lock_ok = again.returncode == 0
                except subprocess.TimeoutExpired:
                    lock_ok = False
            if not lock_ok:
                problems.append("a second start while the lock is held did not exit 0")
        else:
            problems.append("no lock file created (contract rule 4)")
        files = sorted(str(p.relative_to(scratch)) for p in scratch.rglob("*")
                       if p.is_file() and p.name not in {"ops-engine", "wcp_agent_lib.py", f"{name}.sh", "flock"})
        engine_calls = calls.read_text().splitlines() if calls.exists() else []
        logs = {}
        for log in (scratch / "logs").glob("*.log"):
            logs[log.name] = log.read_text().splitlines()[-5:]
        if code not in (0, None) and not dry_run:
            problems.append(f"exited with {code}")
        return {"agent": name, "ok": not problems, "dry_run": dry_run, "exit_code": code,
                "heartbeat": beat, "lock_ok": lock_ok, "files": files,
                "engine_calls": engine_calls, "logs": logs, "problems": problems,
                "stdout": stdout[-2000:], "stderr": stderr[-2000:]}
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def cmd_run(args) -> int:
    if not (ROOT / "agents" / args.name).is_dir():
        return fail(f"no agent {args.name}")
    report = run_agent(args.name, dry_run=args.dry_run, timeout=args.timeout)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"{report['agent']}: {'ok' if report['ok'] else 'FAILED'}"
              f" (exit {report.get('exit_code')}, dry-run {report.get('dry_run')})")
        for key in ("files", "engine_calls"):
            for item in report.get(key, []):
                print(f"  {key}: {item}")
        for file, lines in report.get("logs", {}).items():
            for line in lines:
                print(f"  {file}: {line}")
        for problem in report["problems"]:
            print(f"  problem: {problem}", file=sys.stderr)
        if report.get("stderr"):
            print(report["stderr"], file=sys.stderr)
    return 0 if report["ok"] else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    new = sub.add_parser("new", help="scaffold a new agent")
    new.add_argument("name")
    new.add_argument("--lang", choices=("bash", "python"), default="bash")
    new.add_argument("--author", default="Your Name")
    new.add_argument("--description")
    new.add_argument("--schedule", help="five cron fields; without it the agent has no schedule")
    new.set_defaults(func=cmd_new)
    check = sub.add_parser("check", help="validate agents")
    check.add_argument("names", nargs="*")
    check.set_defaults(func=cmd_check)
    run = sub.add_parser("run", help="run an agent against a scratch WCP_DIR and check the contract")
    run.add_argument("name")
    run.add_argument("--dry-run", action="store_true", help="set WCP_DRY_RUN=1")
    run.add_argument("--json", action="store_true")
    run.add_argument("--timeout", type=int, default=60)
    run.set_defaults(func=cmd_run)
    sub.add_parser("list", help="list agents").set_defaults(func=cmd_list)
    show = sub.add_parser("show", help="print one manifest as JSON")
    show.add_argument("name")
    show.set_defaults(func=cmd_show)
    sub.add_parser("api", help="list the shared library helpers").set_defaults(func=cmd_api)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
