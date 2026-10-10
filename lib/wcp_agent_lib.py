"""Shared helpers for WCP bundled agent scripts.

Uploaded alongside every agent script into /root/.wcp/agents/ so any of them
can `sys.path.insert(0, WCP_DIR + "/agents"); import wcp_agent_lib`.

The library is shared by all agents and installing any one agent rewrites the
shared file, so every change must stay backward compatible with the agents
already installed.
"""

import json
import os
import subprocess
import time


def shell_quote(s):
    """Single-quote `s` for safe interpolation into a shell command string."""
    return "'" + str(s).replace("'", "'\\''") + "'"


def run(cmd, timeout=60):
    """Run `cmd` (a shell string) and return the completed process."""
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)


def run_argv(argv, timeout=60):
    """Run `argv` (a list, no shell) and return the completed process.

    Use this instead of `run` for any argument that isn't fully trusted
    (e.g. a filename reported by a container) - there's no shell to
    re-parse it, so it can't break out of quoting.
    """
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def read_json(path, default=None):
    """Load JSON from `path`, returning `default` if missing/unreadable."""
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path, data):
    """Write `data` as JSON to `path`, creating parent directories as needed."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f)


def log_entry(log_file, entry, max_lines=2000):
    """Append `entry` as a JSON line to `log_file`, trimming to `max_lines`."""
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    with open(log_file, "a") as f:
        f.write(json.dumps(entry, separators=(",", ":")) + "\n")
    try:
        with open(log_file) as f:
            lines = f.readlines()
        if len(lines) > max_lines:
            with open(log_file, "w") as f:
                f.writelines(lines[-max_lines:])
    except Exception:
        pass


def _sh_esc(s):
    return "'" + str(s).replace("'", "'\\''") + "'"


def notify(notify_file, is_failure, is_success, summary_text, event_payload, username="WCP Agent"):
    """Send `summary_text`/`event_payload` to every enabled channel in
    `notify_file` whose on_failure/on_success gate matches. `is_failure` and
    `is_success` are independent (not assumed complementary) so callers whose
    outcome can be neither (e.g. nothing to do) simply pass both False.
    """
    try:
        with open(notify_file) as f:
            notify_conf = json.load(f)
    except Exception:
        return

    for ch in notify_conf.get("channels", []):
        if not ch.get("enabled", True):
            continue
        send = (is_failure and ch.get("on_failure", True)) or \
               (is_success and ch.get("on_success", False))
        if not send:
            continue

        webhook_url = ch.get("webhook_url", "")
        if not webhook_url:
            continue

        ch_type = ch.get("type", "webhook")
        if ch_type == "slack":
            body = json.dumps({"text": summary_text, "username": username})
        elif ch_type == "discord":
            body = json.dumps({"content": summary_text})
        else:
            body = json.dumps(event_payload)

        subprocess.run(
            f"curl -s -m 10 -X POST -H 'Content-Type: application/json' "
            f"-d {_sh_esc(body)} {_sh_esc(webhook_url)}",
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
        )


def database_dump_command(db_type, container, password, database):
    """Stable SQL output allows a restore drill to compare the actual snapshot."""
    target = shell_quote(container)
    secret = shell_quote(password)
    name = shell_quote(database)
    if db_type == "mariadb":
        return (f"docker exec -e MYSQL_PWD={secret} {target} mariadb-dump -uroot "
                f"--single-transaction --routines --triggers --skip-comments "
                f"--skip-extended-insert --order-by-primary --skip-add-locks --skip-disable-keys {name}")
    if db_type == "postgres":
        return (f"docker exec -e PGPASSWORD={secret} {target} pg_dump -U postgres "
                f"--no-owner --no-privileges --inserts --rows-per-insert=1 {name}")
    raise ValueError("Unsupported database type")


def sql_digest(stream):
    """Ignore only pg_dump's random psql guard token, which is not SQL data."""
    import hashlib
    import re
    digest = hashlib.sha256()
    size = 0
    for line in stream:
        if re.fullmatch(rb"\\(?:un)?restrict [A-Za-z0-9]+\r?\n?", line):
            continue
        digest.update(line)
        size += len(line)
    if size == 0:
        raise ValueError("Empty SQL dump")
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Agent API, library version 2. Everything below is additive: nothing above
# changes. The engine writes this file next to the agents, so a helper below
# reaches a server only once the engine ships a copy that contains it. See
# docs/agent-api.md.
# ---------------------------------------------------------------------------

LIB_API_VERSION = 2
DEFAULT_WCP_DIR = "/root/.wcp"
DEFAULT_SITES_MANIFEST_DIR = "/etc/operations-engine/sites"
DEFAULT_SITES_ROOT = "/var/www"
# Engine commands that never change anything; the only ones that still run
# when WCP_DRY_RUN=1.
_READ_ONLY_ENGINE = (("version",), ("capabilities",), ("doctor",),
                     ("operation", "status"), ("operation", "list"))
RESULT_STATUSES = ("ok", "warn", "fail", "skipped")
_DOMAIN = None


def wcp_dir():
    """The agents' state directory: $WCP_DIR, or /root/.wcp."""
    return os.environ.get("WCP_DIR", DEFAULT_WCP_DIR)


def dry_run():
    """True when the agent was started with WCP_DRY_RUN=1: it must not change
    anything outside its scratch state and must not call mutating engine
    operations. `engine_call` already honours this."""
    return os.environ.get("WCP_DRY_RUN") == "1"


def agent_file(name, suffix):
    """Path of `<WCP_DIR>/agents/<name>.<suffix>` (heartbeat, lock, ...)."""
    return os.path.join(wcp_dir(), "agents", "%s.%s" % (name, suffix))


def log_file(name):
    """Path of the agent's own log, `<WCP_DIR>/logs/<name>.log`."""
    return os.path.join(wcp_dir(), "logs", "%s.log" % name)


def write_heartbeat(name, exit_code=0, now=None):
    """Write the heartbeat line the panel reads, atomically."""
    path = agent_file(name, "heartbeat")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    line = json.dumps({"ts": int(time.time() if now is None else now),
                       "exit_code": int(exit_code)}, separators=(",", ":"))
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(line + "\n")
    os.replace(tmp, path)
    return path


def read_conf(path, default=None):
    """Parse a KEY=VALUE file (comments with #, optional surrounding quotes).

    Returns a dict, or `default` ({} if not given) when the file is missing or
    unreadable. Values are never evaluated by a shell. Do not log the result:
    these files can hold secrets.
    """
    result = {} if default is None else default
    try:
        with open(path) as f:
            lines = f.read().splitlines()
    except OSError:
        return result
    conf = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[7:].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            conf[key] = value
    return conf


def _domain_like(name):
    global _DOMAIN
    if _DOMAIN is None:
        import re
        _DOMAIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    return bool(_DOMAIN.match(name)) and ".." not in name


def list_sites(manifest_dir=None, sites_root=None):
    """The sites on this server, as dicts with `site_id`, `domain`,
    `content_root`, `site_user` and `source`.

    Reads the engine's site manifests first (`WCP_SITES_MANIFEST_DIR`, default
    /etc/operations-engine/sites). Sites that have no manifest but a plain
    domain-named directory under `SITES_ROOT` (default /var/www) are added with
    `site_id` None and source "filesystem". Missing directories give an empty
    list. Sorted by domain. Read-only.
    """
    manifest_dir = manifest_dir or os.environ.get("WCP_SITES_MANIFEST_DIR", DEFAULT_SITES_MANIFEST_DIR)
    sites_root = sites_root or os.environ.get("SITES_ROOT", DEFAULT_SITES_ROOT)
    sites = {}
    try:
        names = sorted(os.listdir(manifest_dir))
    except OSError:
        names = []
    for name in names:
        if not name.endswith(".json"):
            continue
        data = read_json(os.path.join(manifest_dir, name), None)
        if not isinstance(data, dict) or not isinstance(data.get("domain"), str):
            continue
        if not _domain_like(data["domain"]):
            continue
        sites[data["domain"]] = {
            "site_id": data.get("siteId"),
            "domain": data["domain"],
            "content_root": data.get("contentRoot"),
            "site_user": data.get("siteUser"),
            "source": "manifest",
        }
    try:
        entries = sorted(os.listdir(sites_root))
    except OSError:
        entries = []
    for entry in entries:
        if entry in sites or not _domain_like(entry):
            continue
        if os.path.isdir(os.path.join(sites_root, entry)):
            sites[entry] = {"site_id": None, "domain": entry,
                            "content_root": os.path.join(sites_root, entry),
                            "site_user": None, "source": "filesystem"}
    return [sites[k] for k in sorted(sites)]


def engine_call(argv, timeout=60):
    """Run `ops-engine <argv...>` (binary from $OPS_ENGINE) and return its
    response envelope as a dict. Never raises: a missing binary, a timeout or
    unparsable output become `{"ok": False, "error": {"code": ...}}` with the
    codes ENGINE_UNAVAILABLE, ENGINE_TIMEOUT or ENGINE_BAD_RESPONSE.

    With WCP_DRY_RUN=1 only read-only commands (version, capabilities, doctor,
    operation status/list) are executed. Any other command is not run and
    returns `{"ok": True, "dryRun": True, "result": None}`.
    """
    argv = [str(a) for a in argv]
    if dry_run() and not any(tuple(argv[:len(p)]) == p for p in _READ_ONLY_ENGINE):
        return {"ok": True, "dryRun": True, "operation": " ".join(argv[:2]),
                "result": None, "warnings": [], "error": None}
    engine = os.environ.get("OPS_ENGINE", "ops-engine")
    try:
        proc = run_argv([engine] + argv, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        return {"ok": False, "result": None, "error": {"code": "ENGINE_TIMEOUT", "message": str(exc)}}
    except OSError as exc:
        return {"ok": False, "result": None, "error": {"code": "ENGINE_UNAVAILABLE", "message": str(exc)}}
    try:
        response = json.loads(proc.stdout)
        if not isinstance(response, dict):
            raise ValueError("not an object")
        return response
    except ValueError:
        message = (proc.stderr or proc.stdout or "no output").strip()[:500]
        return {"ok": False, "result": None, "error": {"code": "ENGINE_BAD_RESPONSE", "message": message}}


def engine_operations():
    """The set of operation names this engine supports (empty when the engine
    cannot be reached). Lets an agent degrade instead of failing on an old
    engine."""
    response = engine_call(["capabilities"], timeout=15)
    result = response.get("result") if response.get("ok") else None
    ops = result.get("operations") if isinstance(result, dict) else None
    return set(ops) if isinstance(ops, list) else set()


def emit_result(name, status, summary, max_lines=2000, **data):
    """Append one result line to the agent's own log and return it.

    The line is `{"ts", "agent", "status", "summary", ...data}`; `status` is
    one of ok, warn, fail or skipped. Do not put secrets in `data`. Not
    written when WCP_DRY_RUN=1 (the line is still returned).
    """
    if status not in RESULT_STATUSES:
        raise ValueError("status must be one of %s" % (RESULT_STATUSES,))
    entry = {"ts": int(time.time()), "agent": name, "status": status, "summary": str(summary)[:500]}
    for key, value in data.items():
        entry.setdefault(key, value)
    if not dry_run():
        log_entry(log_file(name), entry, max_lines=max_lines)
    return entry
