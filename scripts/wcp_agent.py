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
requires_helpers = ["result"]
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
# One result line per run: what the panel shows. status is ok, warn, fail or
# skipped; never put a secret in the summary or in --data.
"$OPS" agent result emit "$NAME" --status ok --summary "TODO: say what this run found"
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
subprocess.run([ops, "agent", "result", "emit", name, "--status", "ok",
                "--summary", "TODO: say what this run found"], check=True)
PY
'''

# Stands in for ops-engine during `run` when no real binary is given: it
# implements `agent heartbeat|lock|log|result|config|site|tool|version` as the engine
# documents them (same files, exit codes and dry-run rule), answers
# `capabilities`, and records every call. Keep it in step with operations-engine
# docs/agent-helpers.md.
ENGINE_STUB = r"""#!/usr/bin/env python3
import fcntl, json, os, re, stat, subprocess, sys, time

args = sys.argv[1:]
with open(os.environ["WCP_STUB_CALLS"], "a") as calls:
    calls.write(" ".join(args) + "\n")
wcp = os.environ.get("WCP_DIR", "/root/.wcp")
dry = os.environ.get("WCP_DRY_RUN") == "1"
HELPERS = ["heartbeat", "lock", "log", "result", "config", "site", "version", "tool"]
SECRET_WORDS = ("password", "passwd", "secret", "token", "apikey", "api_key", "credential", "private_key")


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


def options(rest, flag):
    values = []
    while flag in rest:
        values.append(option(rest, flag))
    return values


def flag(rest, name):
    if name in rest:
        rest.remove(name)
        return True
    return False


def envelope(operation, result):
    print(json.dumps({"protocolVersion": 1, "operation": operation, "ok": True, "result": result,
                      "warnings": [], "error": None}, separators=(",", ":")))


def append_log(name, line, max_lines=2000):
    os.makedirs(os.path.join(wcp, "logs"), exist_ok=True)
    path = os.path.join(wcp, "logs", name + ".log")
    with open(path, "a") as f:
        f.write(json.dumps(line, separators=(",", ":")) + "\n")
    with open(path) as f:
        lines = f.read().splitlines()
    if len(lines) > max_lines:
        with open(path, "w") as f:
            f.write("\n".join(lines[-max_lines:]) + "\n")


def conf_values(name):
    path = os.path.join(wcp, "agents", name + ".conf")
    if not os.path.exists(path):
        return None
    info = os.stat(path)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
        print("agent: the agent configuration must be a regular file owned by the running user "
              "and not writable by others", file=sys.stderr)
        sys.exit(1)
    values = {}
    for number, raw in enumerate(open(path).read().splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        key, sep, value = raw.partition("=")
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", key):
            print("agent: line %d is not KEY=VALUE" % number, file=sys.stderr)
            sys.exit(1)
        values[key] = value
    return values


INSTALLERS = {"wp-cli": "tool.install", "rclone": "backup.installRclone", "docker": "system.installDocker"}


def tool_state(tool):
    state = {"tool": tool, "present": False, "version": None, "path": None, "installer": INSTALLERS[tool]}
    if tool == "wp-cli":
        base = os.environ.get("WCP_TOOLS_DIR") or "/var/lib/wcp/tools"
        current = os.path.join(base, "wp-cli", "current")
        try:
            version = os.readlink(current)
        except OSError:
            return state
        phar = os.path.join(current, "wp.phar")
        if version and "/" not in version and not version.startswith(".") and os.path.isfile(phar):
            state.update(present=True, version=version, path=phar)
        return state
    dirs = (os.environ.get("WCP_TOOL_BIN_DIRS") or "").split(":") if os.environ.get("WCP_TOOL_BIN_DIRS") else (
        ["/usr/bin", "/usr/local/bin", "/bin", "/snap/bin"])
    for directory in dirs:
        path = os.path.join(directory, tool)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            state.update(present=True, path=path)
            try:
                out = subprocess.run([path, "version" if tool == "rclone" else "--version"],
                                     capture_output=True, text=True, timeout=10).stdout.splitlines()
                word = out[0].split()[1 if tool == "rclone" else 2].rstrip(",") if out else ""
                word = word[1:] if word.startswith("v") else word
                if re.fullmatch(r"[A-Za-z0-9.+_:~-]{1,64}", word):
                    state["version"] = word
            except (OSError, IndexError, subprocess.SubprocessError):
                pass
            break
    return state


def tool_allowed(tool):
    path = os.path.join(wcp, "allow-tool-ensure")
    try:
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
            return False
        return tool in [line.strip() for line in open(path).read().splitlines()]
    except OSError:
        return False


def sites():
    manifests = os.environ.get("WCP_SITES_MANIFEST_DIR") or "/etc/operations-engine/sites"
    root = os.environ.get("SITES_ROOT") or "/var/www"
    found = {}
    domain = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*")
    try:
        names = sorted(os.listdir(manifests))
    except OSError:
        names = []
    for file in names:
        try:
            data = json.load(open(os.path.join(manifests, file)))
            if file != data["siteId"] + ".json" or not domain.fullmatch(data["domain"]):
                continue
            found.setdefault(data["domain"], {
                "siteId": data["siteId"], "domain": data["domain"], "siteUser": data["siteUser"],
                "contentRoot": data["contentRoot"], "source": "manifest"})
        except (OSError, ValueError, KeyError, TypeError):
            continue
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        entries = []
    for entry in entries:
        path = os.path.join(root, entry)
        if "." in entry and domain.fullmatch(entry) and os.path.isdir(path) and not os.path.islink(path):
            found.setdefault(entry, {"siteId": None, "domain": entry, "siteUser": None,
                                     "contentRoot": path, "source": "filesystem"})
    return [found[k] for k in sorted(found)]


if args[:1] == ["capabilities"]:
    print('{"protocolVersion":1,"operation":"capabilities","ok":true,"result":'
          '{"operations":["version","capabilities","doctor","site.list"],"features":{"agentHelpers":'
          + json.dumps(HELPERS, separators=(",", ":")) + '}},"warnings":[],"error":null}')
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
    max_lines = int(option(rest, "--max-lines", "2000"))
    name = name_of(rest)
    message = " ".join(rest[1:]) or sys.stdin.read().rstrip("\n")
    if not dry:
        append_log(name, {"ts": int(time.time()), "agent": name, "level": level,
                          "msg": message[:2000]}, max_lines)
elif args[:3] == ["agent", "result", "emit"]:
    rest = args[3:]
    as_json = flag(rest, "--json")
    status = option(rest, "--status")
    summary = option(rest, "--summary")
    pairs = options(rest, "--data")
    data_json = option(rest, "--data-json")
    max_lines = int(option(rest, "--max-lines", "2000"))
    name = name_of(rest)
    if status not in ("ok", "warn", "fail", "skipped"):
        usage("--status must be ok, warn, fail or skipped")
    if summary is None or not summary.strip():
        usage("--summary must not be empty")
    data = {}
    if data_json is not None:
        try:
            data = json.loads(data_json)
        except ValueError:
            data = None
        if not isinstance(data, dict):
            usage("--data-json must be a JSON object")
    for pair in pairs:
        if "=" not in pair:
            usage("--data takes KEY=VALUE")
        key, _, value = pair.partition("=")
        data[key] = value
    for key in data:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", key) or any(w in key.lower() for w in SECRET_WORDS):
            usage("invalid or secret-looking data key: " + key)
    if len(json.dumps(data, separators=(",", ":"))) > 4096:
        usage("result data is larger than 4096 bytes")
    line = {"ts": int(time.time()), "agent": name, "status": status, "summary": summary[:500], "data": data}
    if not dry:
        append_log(name, line, max_lines)
    if as_json:
        envelope("agent.result.emit", {"agent": name, "written": not dry, "dryRun": dry, "line": line})
elif args[:3] == ["agent", "config", "get"]:
    rest = args[3:]
    as_json = flag(rest, "--json")
    default = option(rest, "--default")
    name = name_of(rest)
    key = rest[1] if len(rest) > 1 else ""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", key):
        usage("invalid config key")
    values = conf_values(name) or {}
    if key in values:
        value, source = values[key], "file"
    elif default is not None:
        value, source = default, "default"
    else:
        print("agent: %s is not set" % key, file=sys.stderr)
        sys.exit(1)
    if as_json:
        envelope("agent.config.get", {"agent": name, "key": key, "value": value, "source": source})
    else:
        print(value)
elif args[:3] == ["agent", "config", "list"]:
    rest = args[3:]
    as_json = flag(rest, "--json")
    name = name_of(rest)
    keys = sorted(conf_values(name) or {})
    if as_json:
        envelope("agent.config.list", {"agent": name, "keys": keys})
    elif keys:
        print("\n".join(keys))
elif args[:3] == ["agent", "site", "list"]:
    found = sites()
    if "--json" in args:
        envelope("agent.site.list", {"sites": found})
    elif found:
        print("\n".join(site["domain"] for site in found))
elif args[:3] in (["agent", "tool", "status"], ["agent", "tool", "ensure"]):
    rest = args[3:]
    as_json = flag(rest, "--json")
    tool = rest[0] if rest else ""
    if tool not in ("wp-cli", "rclone", "docker"):
        usage("tool must be one of wp-cli, rclone, docker")
    state = tool_state(tool)
    if args[2] == "status":
        if as_json:
            envelope("agent.tool.status", state)
        elif state["present"] and state["version"]:
            print(state["version"])
        sys.exit(0 if state["present"] else 1)
    allowed = tool_allowed(tool)
    outcome = "present" if state["present"] else ("wouldInstall" if allowed and dry else "notAllowed")
    if (not state["present"] and allowed and not dry) or outcome == "notAllowed":
        message = ("the stub engine installs nothing" if allowed else
                   "%s is missing and the operator has not allowed installing it" % tool)
        if as_json:
            print(json.dumps({"protocolVersion": 1, "operation": "agent.tool.ensure", "ok": False,
                              "result": None, "warnings": [],
                              "error": {"code": "DEPENDENCY_UNAVAILABLE", "message": message}},
                             separators=(",", ":")))
        print("agent.tool.ensure: " + message, file=sys.stderr)
        sys.exit(1)
    if as_json:
        envelope("agent.tool.ensure", {"tool": tool, "outcome": outcome, "dryRun": dry,
                                       "allowed": allowed, "state": state})
elif args[:2] == ["agent", "version"]:
    if "--json" in args:
        envelope("agent.version", {"helperApi": 1, "helpers": HELPERS, "engineVersion": "stub"})
    else:
        print("\n".join(["agent-helpers 1"] + HELPERS))
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
        result = None
        for line in reversed(logs.get(f"{name}.log", [])):
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict) and "status" in entry:
                result = entry
                break
        return {"agent": name, "ok": not problems, "dry_run": dry_run, "exit_code": code,
                "heartbeat": beat, "result": result, "lock_ok": lock_ok, "files": files,
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
        if report.get("result"):
            print(f"  result: {report['result']['status']}: {report['result']['summary']}")
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
