# Writing an agent

## What an agent is

An agent is one script plus a small metadata file. The engine on the managed
server installs the script under `/root/.wcp/agents/` and runs it as **root**
on a schedule, through cron or, if the agent asks for it and the server
supports it, a sandboxed systemd timer. Agents are for work that belongs on the
server itself: collecting metrics, watching a log, producing a daily report,
warming a cache.

An agent is not a plugin for the control panel. It cannot add screens or call
panel APIs. It runs, does its job, writes files, and exits.

Everything in this repository is reviewed by an owner before it is merged and
again before it is released, because the script runs with full privileges on
other people's servers. Read [Security and trust model](security-model.md)
before you start.

## Anatomy

```
agents/<name>/agent.toml     metadata (see the reference)
agents/<name>/agent.sh       the script
```

The name is lowercase letters, digits and `-`, at most 64 characters, and is
the same as the directory name. Pick a name that says what the agent does
(`disk-usage-report`, not `helper`).

A script must:

- start with a `#!` line (the engine refuses anything else);
- be UTF-8 text without NUL bytes and at most 256 KiB;
- carry a header comment whose version equals `version` in `agent.toml`:

  ```bash
  #!/usr/bin/env bash
  # wcp-agent: my-agent
  # wcp-agent-version: 1.0.0
  # wcp-agent-description: One sentence about what it does.
  ```

  CI fails when `# wcp-agent-version:` and `version` differ.

Only the file named by `script` is installed on the server. Other files in the
agent's directory are published and hashed, but they are not copied to the
server, so an agent must be a single self-contained script.

## How the engine runs your script

| Fact | Detail |
| --- | --- |
| Installed as | `/root/.wcp/agents/<name>.sh`, mode `0755`, whatever the source file is called |
| Started as | `bash '/root/.wcp/agents/<name>.sh'` (cron) or `/usr/bin/env bash '…'` (systemd) |
| User | `root` |
| Interpreter | Always `bash`. A `#!/usr/bin/env python3` line is **not** used. To write Python, wrap it in a Bash script (see [the Python example](examples.md#a-realistic-python-agent)) |
| Working directory and environment | Whatever cron or systemd provides: no login shell, a minimal `PATH`. Use absolute paths for anything unusual and do not rely on your own shell profile |
| State directory | `$WCP_DIR`, which is `/root/.wcp` unless the environment says otherwise |
| Arguments | None from the schedule. Your script may accept flags for manual runs, as `cache-warmup --domain` does |
| Installed library | `/root/.wcp/agents/wcp_agent_lib.py`, always written next to the script (see below) |

The engine records the agent in `/root/.wcp/agents/manifest.json` with its
version and install time. Installing a newer version of an agent replaces the
script and keeps the schedule the operator chose.

## The contract

Every agent must:

1. **Never read from standard input** and never prompt. There is no terminal.
2. **Exit non-zero on failure** and zero on success. The exit code is what the
   panel shows as the last result.
3. **Write a heartbeat on every exit**, success or failure, to
   `$WCP_DIR/agents/<name>.heartbeat`, as one line of JSON:

   ```json
   {"ts":1760000000,"exit_code":0}
   ```

   `ts` is the Unix time in seconds. Use an `EXIT` trap so a crash still
   writes it:

   ```bash
   heartbeat() {
     local code=$?
     echo "{\"ts\":$(date +%s),\"exit_code\":$code}" > "$WCP_DIR/agents/$NAME.heartbeat"
   }
   trap heartbeat EXIT
   ```
4. **Take a lock** so a second start while one is running does nothing:

   ```bash
   exec 9>"$WCP_DIR/agents/$NAME.lock"
   flock -n 9 || exit 0
   ```
5. **Touch only the paths it declares** in `paths`. Declare every path the
   script reads or writes; reviewers and operators read this list to decide
   whether to trust the agent.
6. **Use no network unless the description says so.** If you must call out,
   say which hosts and why in `description`.

Beyond the contract, good agents also:

- **Keep their own logs bounded.** Rotate or trim anything the agent appends
  to; the built-in agents keep a fixed number of lines.
- **Treat all input as untrusted.** File names, log lines, container output
  and sitemap contents can all be attacker-controlled. Quote every variable,
  pass arguments as lists rather than building shell strings, and never
  `eval` anything you read.

When the engine removes an agent it deletes the script, the cron line or
timer, the manifest entry, and the agent's `.heartbeat` and `.lock` files.
Files you create elsewhere (logs, state files) stay, so name them clearly and
keep them small.

## Files an agent commonly uses

These are the locations the built-in agents use. They are conventions, not
guarantees: a file may be absent, so handle that case.

| Path | Purpose |
| --- | --- |
| `$WCP_DIR/agents/<name>.heartbeat`, `.lock` | Required heartbeat and lock |
| `$WCP_DIR/logs/<name>.log` | The agent's own log, one JSON object per line |
| `$WCP_DIR/notify.conf` | The notification channels (webhook, Slack, Discord) the operator configured. May not exist |
| `/var/www/<domain>/` | The sites on the server |

The panel lists agents it knows about by reading each
`$WCP_DIR/agents/*.heartbeat` and the manifest. Showing agents that come from
this repository in the panel's interface is not part of this guide yet; see
[Not yet specified](#not-yet-specified).

## Schedules

`schedule` in `agent.toml` says who decides when the agent runs:

- `fixed`: always `default_schedule`. Use it for loops that only make sense at
  one cadence, such as a per-minute collector.
- `configurable`: starts at `default_schedule`, and the operator may choose
  another five-field cron expression.
- `none`: the engine adds no schedule. Something else starts the agent, for
  example the panel running it on demand.

Write the schedule as five cron fields (`0 6 * * *`). The engine also accepts
`@hourly`, `@daily`, `@midnight`, `@weekly`, `@monthly`, `@yearly` and
`@annually`.

Keep the work well inside the interval. If a run is still going when the next
one starts, your lock makes the second one exit, so a run that routinely takes
longer than its interval silently skips runs.

## The shared Python library

`wcp_agent_lib.py` holds small helpers the built-in agents share. The engine
writes it next to every agent it installs, so a Python agent can import it:

```python
import os, sys
sys.path.insert(0, os.path.join(os.environ.get("WCP_DIR", "/root/.wcp"), "agents"))
from wcp_agent_lib import log_entry, read_json, write_json
```

| Helper | Does |
| --- | --- |
| `run(cmd, timeout=60)` | Runs a shell string, returns the completed process |
| `run_argv(argv, timeout=60)` | Runs an argument list without a shell. Prefer this whenever any part is not fully trusted |
| `shell_quote(s)` | Single-quotes a value for a shell string |
| `read_json(path, default=None)` | Loads JSON, returns `default` if missing or unreadable |
| `write_json(path, data)` | Writes JSON, creating parent directories |
| `log_entry(log_file, entry, max_lines=2000)` | Appends a JSON line and trims the file to `max_lines` |
| `notify(notify_file, is_failure, is_success, summary_text, event_payload, username="WCP Agent")` | Sends a message to the enabled channels in `notify.conf` whose failure/success gate matches. Does nothing when the file is missing |
| `database_dump_command(...)`, `sql_digest(...)` | Backup helpers used by the backup agents |

The library is shared by all agents and installing any agent rewrites the
file, so helpers keep their behaviour. If you want zero coupling, do not use
it: a plain Bash agent needs none of it.

## Running under systemd

An agent may ask to run from a sandboxed systemd timer instead of cron:

```toml
isolation = "systemd"
writable_paths = ["/root/.wcp/agents", "/root/.wcp/logs"]
```

The engine then installs a one-shot service and a timer from a fixed template
(`wcp-agent-<name>.service` and `.timer`). The service runs with
`ProtectSystem=strict`, `ProtectHome=read-only`, `NoNewPrivileges`,
`PrivateTmp`, `PrivateDevices`, and the kernel and clock protections. It can
write only to `writable_paths`. The agent cannot add or change a directive.

Know the limits before you rely on it:

- It restricts **writes** and privilege gain. Reads and network access are
  unchanged.
- An agent that is allowed to write `/root/.wcp/agents` (it needs that for its
  heartbeat and lock) can still overwrite its sibling scripts there.
- `wcp_agent_lib.py` is mounted read-only inside the service.
- If the server has no systemd, or the cron schedule has no exact
  `OnCalendar=` equivalent, the engine uses a cron line instead.
- `schedule` must not be `none`.

Docker-based agents and agents that need broad access should stay on cron.
Of the agents in this repository, only `disk-usage-report` uses systemd.

## Test it before you open a pull request

The quickest way is the developer CLI (see [Agent API and commands](agent-api.md)):
`python3 scripts/wcp_agent.py run my-agent --dry-run` installs the agent into a
scratch directory with a stub engine, runs it twice (the second time with the
lock held) and checks the heartbeat. The manual steps below show what it does.

The built-in agents honour `WCP_DIR`, and several honour other overrides such as
`SITES_ROOT`. Do the same, and you can run your agent against a scratch
directory without touching a real server:

```bash
export WCP_DIR=$(mktemp -d)
mkdir -p "$WCP_DIR/agents" "$WCP_DIR/logs"
bash agents/my-agent/agent.sh; echo "exit: $?"
cat "$WCP_DIR/agents/my-agent.heartbeat"
```

Start a second copy while the first is still running (add a temporary `sleep`
to the script) to confirm the lock makes it exit quietly. Force a failure and
check that the exit code and the heartbeat both say so.

Then run the same checks CI runs:

```bash
python3 scripts/check_agents.py          # validates agent.toml and the script header
python3 -m unittest discover tests       # tests for the checker
shellcheck agents/my-agent/agent.sh      # needs shellcheck installed
python3 scripts/build_registry.py dev "$(git rev-parse HEAD)" && python3 -m json.tool registry.json >/dev/null
rm registry.json                         # generated, never committed
```

For a Python-in-Bash agent, also run `python3 -m py_compile` on the extracted
Python, or simply run the whole script.

Finally, try it for real on a throw-away server or virtual machine as root, with
the real `cron` or systemd environment. A script that works in your shell can
fail under cron because of the smaller `PATH` and the missing terminal.

You cannot point an engine at a fork or a branch: the engine downloads only from
this repository's releases. Real end-to-end installs therefore start after your
agent has been merged and released.

## How an agent gets from a pull request to a server

1. **You open a pull request** that adds `agents/<name>/`. Explain what the
   agent does and why it needs each path. New agents start as
   `tier = "community"`. Only owners can mark one `official`. Put
   your own name in `author`. `Website Control Panel` is reserved for agents the
   maintainers wrote, and a pull request that uses it for someone else's agent is
   not merged.
2. **CI** validates `agent.toml`, runs ShellCheck and checks that the registry
   builds.
3. **An owner reviews and merges.** Everything needs owner approval, including
   workflows.
4. **An owner starts the Release workflow** with a version such as `1.2.0`. It
   needs manual approval in the `release` environment. It computes the SHA-256
   of every file of every agent from the tagged commit and publishes
   `registry.json` with the release. The tag is one version for the whole
   repository.
5. **An operator installs it.** The request names only
   the agent, the release tag and the script's SHA-256. The engine downloads
   `registry.json` and the script from this repository's release (the address
   is compiled into the engine), and installs only if:
   - the registry belongs to that release and lists the agent;
   - the agent version is not in `revoked`;
   - the engine is at least `min_engine`;
   - the script hashes to the registry value **and** to the hash the operator
     reviewed;
   - for a `community` agent, an operator approved exactly that hash on that
     server first.
6. The install itself is atomic: files first, the schedule last, and a failure
   at any step restores the previous state.

To ship a fix, change the script, raise `version` (and the header), and go
through the same steps. An agent version found to be unsafe goes into
`revoked.json`; the next release refuses it for new installs and operators who
already have it installed are warned. Nothing is removed from a server
automatically.

## Not yet specified

These points are not defined by the engine or the repository today. Do not
depend on them.

- **Run time limits.** The engine sets no time limit on a run.
- **A minimum library version.** There is no field that says which version of
  `wcp_agent_lib.py` an agent needs.
- **Showing registry agents in the panel.** The panel lists the seven built-in
  agents. Browsing, approving and installing agents from this repository
  through the panel's interface is still being built. The engine operations
  (`agent.installFromRegistry`, `agent.approve`, `agent.unapprove`) exist.
- **Agents not in this repository.** An operator's own local script is not
  supported yet.
