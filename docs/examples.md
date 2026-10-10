# Examples

Three agents, from the smallest possible to a realistic one that uses the
shared library. The first and third are complete and were run on a Debian 12
server (as a normal user, with `WCP_DIR` pointing at a scratch directory) with
the checks from [Writing an agent](writing-agents.md#test-it-before-you-open-a-pull-request).
The second is an agent that already ships in this repository.

## A minimal agent

Copy `agents/example` to `agents/uptime-note` and edit both files. Every agent
needs the same four ingredients: a heartbeat on exit, a lock, work that stays
inside its declared paths, and a bounded log.

`agents/uptime-note/agent.toml`

```toml
name = "uptime-note"
version = "1.0.0"
tier = "community"
script = "agent.sh"
schedule = "configurable"
default_schedule = "0 * * * *"
min_engine = "0.1.0"
description = "Every hour, appends the load averages and uptime to a log file. No network access."
paths = ["/root/.wcp/agents", "/root/.wcp/logs"]
```

`agents/uptime-note/agent.sh`

```bash
#!/usr/bin/env bash
# wcp-agent: uptime-note
# wcp-agent-version: 1.0.0
# wcp-agent-description: Every hour, appends the load averages and uptime to a log file.
set -euo pipefail

NAME="uptime-note"
WCP_DIR="${WCP_DIR:-/root/.wcp}"
LOG_FILE="$WCP_DIR/logs/$NAME.log"
MAX_LINES=720

mkdir -p "$WCP_DIR/agents" "$WCP_DIR/logs"

# Always leave a heartbeat, even when the script fails half way.
heartbeat() {
  local code=$?
  echo "{\"ts\":$(date +%s),\"exit_code\":$code}" > "$WCP_DIR/agents/$NAME.heartbeat"
}
trap heartbeat EXIT

# A second start while one is running does nothing.
exec 9>"$WCP_DIR/agents/$NAME.lock"
flock -n 9 || exit 0

read -r load1 load5 load15 _ < /proc/loadavg
up_seconds=$(cut -d. -f1 /proc/uptime)

printf '{"ts":%s,"load1":%s,"load5":%s,"load15":%s,"uptime_seconds":%s}\n' \
  "$(date +%s)" "$load1" "$load5" "$load15" "$up_seconds" >> "$LOG_FILE"

# Keep the log bounded.
if [ "$(wc -l < "$LOG_FILE")" -gt "$MAX_LINES" ]; then
  tail -n "$MAX_LINES" "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
fi
```

What to notice:

- `${WCP_DIR:-/root/.wcp}` lets you test against a scratch directory.
- The `EXIT` trap records `$?`, so a failure is visible in the heartbeat. The
  trap is installed before anything can fail.
- `flock -n 9 || exit 0` makes a second start a no-op.
- The log is one JSON object per line and trimmed to a fixed size, so it never
  grows without limit.
- The script writes only to `paths`. It also reads `/proc/loadavg` and
  `/proc/uptime`, which every Linux system provides. List files you read from
  other places, too.
- The script is plain Bash, uses no library and needs no network.

## A realistic Bash agent: `disk-usage-report`

`agents/disk-usage-report` runs once a day, records how full the root
filesystem is and the sizes of the ten largest sites, and appends one JSON line
to its log. It is the reference for a read-mostly Bash agent and the only
agent in this repository that runs under systemd:

```toml
isolation = "systemd"
writable_paths = ["/root/.wcp/agents", "/root/.wcp/logs"]
```

Read its script for these habits:

- It reports a directory only when its name matches `^[A-Za-z0-9._-]+$`, so a
  hostile directory name can never break the JSON it prints.
- It only reads `/var/www` (`du`), so `/var/www` is in `paths` but not in
  `writable_paths`. Reading is allowed under the sandbox; writing is not.
- `SITES_ROOT` and `WCP_DIR` can be overridden, so it is testable anywhere.
- It trims its own log to 2000 lines.

## A realistic Python agent

The built-in agents that need real logic are Bash scripts that hand over to a
quoted Python heredoc. The shell part takes care of the heartbeat and the lock;
the Python part does the work and uses the shared library.

`load-watch` logs the load per CPU every five minutes and sends a notification
through the channels the operator configured when it stays above a threshold.

`agents/load-watch/agent.toml`

```toml
name = "load-watch"
version = "1.0.0"
tier = "community"
script = "agent.sh"
schedule = "configurable"
default_schedule = "*/5 * * * *"
min_engine = "0.1.0"
description = "Every five minutes, logs the load per CPU and sends a notification when it stays above a threshold. Network access only through the operator's configured notification channels."
paths = ["/root/.wcp/agents", "/root/.wcp/logs", "/root/.wcp/load-watch.conf", "/root/.wcp/notify.conf"]
```

`agents/load-watch/agent.sh`

```bash
#!/usr/bin/env bash
# wcp-agent: load-watch
# wcp-agent-version: 1.0.0
# wcp-agent-description: Every five minutes, alerts when the load per CPU stays above a threshold.
set -euo pipefail

NAME="load-watch"
WCP_DIR="${WCP_DIR:-/root/.wcp}"
export NAME WCP_DIR

mkdir -p "$WCP_DIR/agents" "$WCP_DIR/logs"
heartbeat() {
  local code=$?
  echo "{\"ts\":$(date +%s),\"exit_code\":$code}" > "$WCP_DIR/agents/$NAME.heartbeat"
}
trap heartbeat EXIT

exec 9>"$WCP_DIR/agents/$NAME.lock"
flock -n 9 || exit 0

# The quoted heredoc delimiter stops the shell from touching the Python code.
python3 - << 'PYEOF'
import os, sys, time

WCP_DIR = os.environ["WCP_DIR"]
NAME = os.environ["NAME"]
sys.path.insert(0, os.path.join(WCP_DIR, "agents"))
from wcp_agent_lib import log_entry, notify, read_json

CONF_FILE = os.path.join(WCP_DIR, "load-watch.conf")      # optional: {"threshold": 2.0}
LOG_FILE = os.path.join(WCP_DIR, "logs", NAME + ".log")
NOTIFY_FILE = os.path.join(WCP_DIR, "notify.conf")        # optional: channels the operator set up

threshold = float((read_json(CONF_FILE, {}) or {}).get("threshold", 2.0))
cpus = os.cpu_count() or 1
load5 = os.getloadavg()[1]
per_cpu = load5 / cpus
alert = per_cpu > threshold

entry = {
    "ts": int(time.time()),
    "load5": round(load5, 2),
    "cpus": cpus,
    "per_cpu": round(per_cpu, 2),
    "threshold": threshold,
    "alert": alert,
}
log_entry(LOG_FILE, entry, max_lines=2000)

if alert:
    text = f"Load is high: {per_cpu:.2f} per CPU over 5 minutes (threshold {threshold})"
    notify(NOTIFY_FILE, is_failure=True, is_success=False, summary_text=text, event_payload=entry)
PYEOF
```

What to notice:

- The delimiter is quoted (`<< 'PYEOF'`), so the shell never expands anything
  inside the Python code. Values pass in through exported environment
  variables, never by pasting them into the code.
- The agent is not given a way to change its own threshold; the operator
  writes `load-watch.conf`, and the agent only reads it. A missing or broken
  file falls back to the default (`read_json` returns the default).
- `notify` returns without doing anything when `notify.conf` does not exist,
  so the agent works on a server with no notification channels. The
  description says network access happens only through those channels, as the
  contract requires.
- With `set -e` a Python exception makes the shell exit non-zero, and the
  heartbeat records that exit code.

## Checklist before you open the pull request

- [ ] `name` equals the directory; `version` equals `# wcp-agent-version:`.
- [ ] `paths` lists everything the script reads or writes, and nothing else.
- [ ] Heartbeat on every exit, lock on start, bounded logs.
- [ ] No prompts, no stdin, no network unless the description says so.
- [ ] Every variable quoted; no `eval`; untrusted values never reach a shell
  string (use `run_argv` or arrays).
- [ ] `python3 scripts/check_agents.py` and `shellcheck` are clean.
- [ ] You ran it as root on a throw-away server, from cron or systemd, at least
  once and also in the failing case.
- [ ] The pull request says what the agent does and why it needs each path.
