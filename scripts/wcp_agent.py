#!/usr/bin/env python3
"""Developer tool for writing agents. Runs on your machine, never on a server.

  wcp_agent.py new NAME [--lang bash|python] [--author NAME] [--description TEXT]
                        [--schedule CRON]   scaffold agents/NAME
  wcp_agent.py check [NAME ...]             validate agent.toml and the script
  wcp_agent.py run NAME [--dry-run] [--json] [--engine PATH]
                                            run it against a scratch WCP_DIR
  wcp_agent.py list                         the agents in this repository
  wcp_agent.py show NAME                    one agent's manifest as JSON
  wcp_agent.py api                          the functions in lib/wcp_agent_lib.py (compat layer)

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
# The first engine release that has `ops-engine agent heartbeat|lock|log`.
# Scaffolds that call them must not run on an older engine. Set this to the
# real release number when the engine ships the helpers (docs/agent-api.md).
HELPERS_MIN_ENGINE = "0.2.0"

TOML = '''name = "{name}"
version = "0.1.0"
tier = "community"
author = "{author}"
script = "agent.sh"
schedule = "{schedule_kind}"
default_schedule = "{schedule}"
min_engine = "{min_engine}"
description = "{description}"
paths = ["/root/.wcp/agents", "/root/.wcp/logs"]
'''

# Both templates keep the contract: the heartbeat on every exit, the lock, work
# inside the declared paths and a bounded log. The shared parts are engine
# commands (`ops-engine agent ...`), so Bash and Python agents use the same.
PROLOGUE = '''set -euo pipefail

NAME="{name}"
OPS="${{OPS_ENGINE:-/usr/local/bin/ops-engine}}"
export NAME OPS

# Heartbeat on every exit; then run this script again under the agent's lock.
# A second start while one is running does nothing and exits 0.
trap '"$OPS" agent heartbeat "$NAME" --exit-code $?' EXIT
[ "${{WCP_LOCK_HELD:-}}" = "$NAME" ] || exec "$OPS" agent lock "$NAME" -- bash "$0" "$@"
'''

BASH = '''#!/usr/bin/env bash
# wcp-agent: {name}
# wcp-agent-version: 0.1.0
# wcp-agent-description: {description}
''' + PROLOGUE + '''
# WCP_DRY_RUN=1 means: change nothing outside $WCP_DIR (the helpers already
# write nothing then). TODO: replace this with the agent's work.
"$OPS" agent log "$NAME" "ran"
'''

PYTHON = '''#!/usr/bin/env bash
# wcp-agent: {name}
# wcp-agent-version: 0.1.0
# wcp-agent-description: {description}
''' + PROLOGUE + '''
python3 - <<'PY'
import os
import subprocess

ops, name = os.environ["OPS"], os.environ["NAME"]
dry_run = os.environ.get("WCP_DRY_RUN") == "1"
# TODO: replace this with the agent's work. The helpers are plain commands, so
# the Python part needs no import: heartbeat and lock are done above.
subprocess.run([ops, "agent", "log", name, "ran (dry run: %s)" % dry_run], check=True)
PY
'''

# Stands in for ops-engine during `run` when no real binary is given: it
# implements `agent heartbeat|lock|log` as the engine documents them (same
# files, exit codes and dry-run rule), answers `capabilities`, and records
# every call. Keep it in step with operations-engine docs/agent-helpers.md.
ENGINE_STUB = r'''#!/usr/bin/env python3
import fcntl, json, os, re, sys, time

args = sys.argv[1:]
with open(os.environ["WCP_STUB_CALLS"], "a") as calls:
    calls.write(" ".join(args) + "\n")
wcp = os.environ.get("WCP_DIR", "/root/.wcp")
dry = os.environ.get("WCP_DRY_RUN") == "1"


def usage(message):
    print("agent: " + message, file=sys.stderr)
    sys.exit(2)


def name_of(rest):
    if not rest or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", rest[0]):
        usage("invalid agent name")
    return rest[0]


def option(rest, flag, default=None):
    if flag in rest:
        i = rest.index(flag)
        value = rest[i + 1]
        del rest[i:i + 2]
        return value
    return default


if args[:1] == ["capabilities"]:
    print('{"protocolVersion":1,"operation":"capabilities","ok":true,"result":'
          '{"operations":["version","capabilities","doctor"]},"warnings":[],"error":null}')
elif args[:2] == ["agent", "heartbeat"]:
    rest = args[2:]
    code = int(option(rest, "--exit-code", "0"))
    name = name_of(rest)
    if not dry:
        os.makedirs(os.path.join(wcp, "agents"), exist_ok=True)
        with open(os.path.join(wcp, "agents", name + ".heartbeat"), "w") as f:
            f.write(json.dumps({"ts": int(time.time()), "exit_code": code}, separators=(",", ":")) + "\n")
elif args[:2] == ["agent", "log"]:
    rest = args[2:]
    level = option(rest, "--level", "info")
    option(rest, "--max-lines", "2000")
    name = name_of(rest)
    message = " ".join(rest[1:]) or sys.stdin.read().rstrip("\n")
    if not dry:
        os.makedirs(os.path.join(wcp, "logs"), exist_ok=True)
        with open(os.path.join(wcp, "logs", name + ".log"), "a") as f:
            f.write(json.dumps({"ts": int(time.time()), "agent": name, "level": level,
                                "msg": message[:2000]}, separators=(",", ":")) + "\n")
elif args[:2] == ["agent", "lock"]:
    rest = args[2:]
    command = []
    if "--" in rest:
        i = rest.index("--")
        rest, command = rest[:i], rest[i + 1:]
    check = "--check" in rest
    if check:
        rest.remove("--check")
    held_code = int(option(rest, "--held-exit-code", "0"))
    name = name_of(rest)
    if check == bool(command):
        usage("give either --check or a command after --")
    os.makedirs(os.path.join(wcp, "agents"), exist_ok=True)
    fd = os.open(os.path.join(wcp, "agents", name + ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit(75 if check else held_code)
    if check:
        sys.exit(0)
    os.set_inheritable(fd, True)
    os.environ["WCP_LOCK_HELD"] = name
    try:
        os.execvp(command[0], command)
    except OSError:
        sys.exit(127)
else:
    print('{"protocolVersion":1,"operation":"stub","ok":true,"result":null,"warnings":[],"error":null}')
'''

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
        schedule_kind="configurable" if schedule else "none", min_engine=HELPERS_MIN_ENGINE,
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


def run_agent(name: str, dry_run: bool = False, timeout: int = 60, engine: str = None) -> dict:
    """Run agents/<name> the way the engine would, but inside a scratch WCP_DIR
    with a stub engine (or the real binary given as `engine`), then check the
    contract. Returns a report dict."""
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
        if engine:
            stub = Path(engine).resolve()
        else:
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
            # The engine helper writes no heartbeat in a dry run, by design.
            if not dry_run:
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
    report = run_agent(args.name, dry_run=args.dry_run, timeout=args.timeout, engine=args.engine)
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
    run.add_argument("--engine", help="use this real ops-engine binary instead of the built-in stub")
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
