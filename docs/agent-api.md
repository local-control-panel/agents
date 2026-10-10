# Agent API and helper commands

Status: phases 1, 2a and 2b are implemented. Phase 1 (developer CLI) is in this
repository; `ops-engine agent heartbeat`, `lock`, `log` (2a, operations-engine
PR #75) and `result`, `config`, `site`, `version` (2b, PR #77) are in the engine
(`docs/agent-helpers.md` there). Nothing has been released yet: no engine
version contains the helpers. Revision 2: the shared helpers are
**Rust subcommands of the engine**, not a Python library.
Audience: people who write agents, and the maintainers (including tools that
generate agents for us).

This page defines one shared set of commands for writing agents: what exists,
what the engine provides, what is still to be built and in which order. It is
written to be copied into the project's internal documentation unchanged.

## 1. Goal

Every agent re-implements the same things: the heartbeat trap, the lock, a
bounded log, "which sites are on this server", "call the engine and parse its
answer", "read a config file". The seven built-in agents do it seven slightly
different ways, and a new author has to copy one and hope. The goal is:

1. **One way to do each common thing**, with the same name for a person at a
   keyboard, a Bash agent, a Python agent and a generator that writes agents
   for us.
2. **Safe by default**: a dry run that cannot change anything, secrets that do
   not reach logs, engine calls that report errors instead of crashing.
3. **No new trust surface**: nothing here lets an agent do more than it can do
   today. The review model in [Security and trust model](security-model.md) is
   unchanged.

## 2. Why Rust subcommands instead of a library

Revision 1 of this design put the helpers in `wcp_agent_lib.py`. That does not
work for the thing we need most:

| Fact | Consequence |
| --- | --- |
| An installed agent is **one file**, `/root/.wcp/agents/<name>.sh`, run with `bash` as root from cron or systemd, with a minimal `PATH` (cron: `/usr/bin:/bin`) and no TTY | A Bash agent cannot `source` a library file, and cannot use a Python module without a heredoc. Only a **command** is shared by Bash and Python alike |
| The engine installs one shared file, `wcp_agent_lib.py`, **compiled into the engine** (`include_str!`). A helper added in this repository reaches a server only with the next engine release, as a second copy that can drift | The library is a second release channel with a drift problem. A subcommand is part of the binary that is already on the server and is versioned with it |
| `ops-engine` is a root-owned binary at `/usr/local/bin/ops-engine`, already trusted by the panel; its answers are JSON envelopes `{protocolVersion, operation, ok, result, warnings, error{code,message}}` | Helpers get typed argument parsing, tests, `O_NOFOLLOW` file handling and name validation once, in Rust, instead of in each script |
| The heartbeat and the lock are the two things the panel and the contract depend on, and Bash is where they are most often wrong (five of seven built-in agents take no lock) | A one-line `ops-engine agent lock` is harder to get wrong than five lines of `exec 9>`/`flock` |
| `flock(1)` is missing on macOS, `df -B` is GNU only | Helpers do not depend on `flock(1)`; the local runner still needs Linux for agents that call GNU tools |

Consequences: **phase 2 is `ops-engine agent <sub>`**. The Python library is
kept only as a thin **compatibility layer** for agents that are already
published (section 7). New helpers are never added to it.

## 3. The layers

```
 people / generators
        │
 A. wcp_agent.py   developer CLI           (this repo, never on a server)
        │ writes, checks, runs
 B. the agent      a single script         (Bash, or Bash + Python heredoc)
        │ calls
 C. ops-engine agent <sub>   helper commands   (Rust, in the engine)
        │                                       wcp_agent_lib.py: frozen compat layer
 D. ops-engine <operation>   typed operations  (the engine, already on the server)
        │ reads
 E. agent.toml     what an agent declares  (checked in CI and by the engine)
```

Layer C is new. Each layer depends only on the ones below it; only A does not
exist on a server. A later Rust port of A is possible (section 9) but not
needed.

## 4. The `ops-engine agent` helper commands

### 4.1 Conventions (all helpers)

| Topic | Rule |
| --- | --- |
| Not protocol operations | They run inside an agent's process tree. They do not appear in `capabilities.operations` and do not take `--request-file`. Existing `agent.install`/`remove`/... are unchanged |
| Output | **stdout is empty on success** (a script's stdout is the script's). `--json` prints exactly one protocol envelope on stdout. Diagnostics go to stderr, one line, prefixed with the operation name |
| Exit codes | `0` success; `1` runtime failure (I/O, engine unreachable); `2` invalid input (bad name, bad flag; same as the argument parser); `75` (`EX_TEMPFAIL`) only for `lock --check` on a held lock; `126`/`127` command cannot be run / not found (`lock --`). `lock --` otherwise returns the command's own code |
| State | Everything lives under `$WCP_DIR` (default `/root/.wcp`) in the files the contract already uses: `agents/<name>.heartbeat`, `agents/<name>.lock`, `logs/<name>.log`. Old hand-written prologues and the helpers interoperate (same `flock`, same JSON) |
| Names | `NAME` must be an agent name (`a-z0-9-`, at most 64, not starting with `-`); anything else exits 2 and touches no file |
| Files | Created with `O_NOFOLLOW`; lock `0600`, heartbeat and log `0644`; heartbeat is replaced atomically |
| Dry run | `WCP_DRY_RUN=1` means *change nothing a real run would leave behind*. Helpers that write results (`heartbeat`, `log`, `result emit`) write nothing and report `"written": false, "dryRun": true`; the lock is still taken (it protects, it does not record); read-only helpers work normally; helpers that would change the system (`tool ensure`) only report what they would do. It is a development aid, **not a sandbox** |
| Secrets | Helpers never print secret values unless the call is explicitly `config get KEY`; `result emit` and `log` text is the caller's responsibility: never log a secret |
| Binary path | Cron's `PATH` has no `/usr/local/bin`. Scripts use `OPS="${OPS_ENGINE:-/usr/local/bin/ops-engine}"`. The engine will export `OPS_ENGINE` and `WCP_DIR` in the cron line/unit (E8) |
| Old engine | An engine without the helper exits 2 with a usage error. Gate with `min_engine` (section 8); the scaffold sets it |
| Environment | `WCP_DIR`, `WCP_DRY_RUN`, `OPS_ENGINE` (path to the binary), plus the existing `SITES_ROOT`. `lock --` also sets `WCP_LOCK_HELD=<name>` for the command |

### 4.2 Surface

Status: **done** (merged, not yet in an engine release: 2a is operations-engine
PR #75, 2b is PR #77), **later**.

| Command | Does | Status |
| --- | --- | --- |
| `agent heartbeat NAME [--exit-code N] [--json]` | Writes `agents/NAME.heartbeat` atomically as `{"ts":<unix seconds>,"exit_code":N}`. Use in an `EXIT` trap with `$?` | done |
| `agent lock NAME -- CMD...` | Takes the agent's lock without blocking, then **`exec`s** CMD with the lock descriptor inherited, so the lock lasts exactly as long as CMD and the exit code is CMD's. **Lock already held: CMD is not started, exit 0** (`--held-exit-code N` to change, `--json` to say so). Sets `WCP_LOCK_HELD=NAME` so a script can re-run itself under the wrapper | done |
| `agent lock NAME --check` | Probe only: exit 0 free, 75 held. For tests and the panel | done |
| `agent log NAME [--level debug\|info\|warn\|error] [--max-lines N] [MSG...]` | Appends `{"ts","agent","level","msg"}` (one JSON line, message cut at 2000 characters) to `logs/NAME.log` and keeps the last N lines (default 2000, at most 100000), under a lock on the file. Message from stdin when no argument or `-` | done |
| `agent result emit NAME --status ok\|warn\|fail\|skipped --summary TEXT [--data KEY=VALUE]... [--data-json JSON]` | Appends the **result line** `{"ts","agent","status","summary","data":{...}}` the panel shows (summary cut at 500 characters, `data` is an object, at most 4 KiB, secrets forbidden) to the same log. Not written in a dry run; echoed with `--json`. A data key containing `password`, `passwd`, `secret`, `token`, `apikey`, `api_key`, `credential` or `private_key` is refused (exit 2) | done (2b) |
| `agent config get NAME KEY [--default V] [--json]` | Reads KEY from the agent's own file `agents/NAME.conf` (`KEY=VALUE`, no shell evaluation); prints the value; exit 1 if missing and no default. `agent config list NAME` prints key names only (never values). The file must be a regular file owned by the running user and not writable by others (`O_NOFOLLOW`, at most 64 KiB); a malformed line fails with exit 1 and is never echoed. The shared `*.conf` files that built-in agents read directly (JSON) are **not** consulted: the engine does not know which agent uses which, so an agent that wants the new call reads `agents/NAME.conf`, written by `agent configure` (phase 4) | done (2b) |
| `agent site list [--json]` | The sites on this server: `siteId`, `domain`, `siteUser`, `contentRoot`, `source`. Read-only; reads the engine's own manifests plus dot-named directories under `/var/www` that have none (`siteId` null, `source` `filesystem`). The runtime is not recorded in a manifest, so it is not reported (the panel knows it). Plain output is one domain per line, so `while read` works; `--json` is the full envelope. The same data is the protocol operation `site.list` | done (2b) |
| `agent tool status NAME` / `agent tool ensure NAME` | `status`: is the allow-listed tool (rclone, docker, wp-cli, ...) present, at what version; exit 0/1. `ensure`: converge it using the engine's existing installers; a dry run only reports. An agent calls `ensure` only if the **operator** enabled it; by default an agent that misses a tool exits non-zero with one line and `result emit --status fail` (E5) | later |
| `agent run NAME [--dry-run] [--json]` | Starts an installed agent once with the same wrapper cron uses and returns its heartbeat and last result line; for "try it" in the panel (E7) | later |
| `agent version [--json]` / a `capabilities` feature `agentHelpers: ["heartbeat", ...]` | Prints `agent-helpers N` then the helper names, one per line; lets an agent or the developer CLI discover which helpers this engine has. `ops-engine agent help` lists every subcommand | done (2b) |

Why `lock` wraps a command instead of "acquire and return": a lock must outlive
the process that takes it, and a child process cannot hand a descriptor back to
its shell. Exec-ing the command keeps one process, the right exit code, signal
delivery, and the lock released by the kernel when it exits or crashes.

### 4.3 What an agent looks like

Bash (the whole prologue is three lines):

```bash
#!/usr/bin/env bash
# wcp-agent: my-agent
# wcp-agent-version: 1.0.0
# wcp-agent-description: ...
set -euo pipefail
NAME="my-agent"
OPS="${OPS_ENGINE:-/usr/local/bin/ops-engine}"

trap '"$OPS" agent heartbeat "$NAME" --exit-code $?' EXIT
[ "${WCP_LOCK_HELD:-}" = "$NAME" ] || exec "$OPS" agent lock "$NAME" -- bash "$0" "$@"

"$OPS" agent log "$NAME" "started"
# ... the work ...
"$OPS" agent result emit "$NAME" --status ok --summary "checked 3 sites"
```

A Python agent keeps the same Bash prologue and calls the same commands from its
heredoc with `subprocess.run([os.environ["OPS"], "agent", ...])`; it needs no
import for them.

### 4.4 Configuration and secrets

Today an agent reads `$WCP_DIR/*.conf` directly, and every agent (all run as
root) can read all of them. That is the largest gap, and it needs the engine and
the panel:

- An agent declares what it reads in `agent.toml` (`config_keys`, section 8).
- The panel writes **one file per agent**, `$WCP_DIR/agents/<name>.conf`, mode
  `0600`, through a new engine operation (E6); the engine validates the keys.
- `agent config get` is the only read path agents should use, so the call is the
  same on day one and after E6.
- Rules for agents and reviewers: never print a secret, never put one in
  `result emit` data or in a log line, never pass one on a command line.

### 4.5 Result and exit conventions

| Situation | Exit code | `result emit` status |
| --- | --- | --- |
| Did the work | 0 | `ok` |
| Did the work, with something worth attention | 0 | `warn` |
| Nothing to do (feature off, no sites) | 0 | `skipped` |
| Failed | non-zero | `fail` (then the heartbeat says so) |
| Another run holds the lock | 0, nothing written | none |

The heartbeat says whether the agent ran; the result line says what it found.
The panel needs only these two.

## 5. Developer commands (`python3 scripts/wcp_agent.py ...`)

Unchanged in purpose; they never run on a server.

| Command | Does | Status |
| --- | --- | --- |
| `new NAME [--lang bash\|python] [--author N] [--description T] [--schedule CRON]` | Scaffolds `agents/NAME/` with a valid `agent.toml` and a script whose prologue is the three lines of section 4.3. Result passes `check` | done (uses `ops-engine agent`) |
| `check [NAME...]` | Validates `agent.toml` and the script (same code as CI). The advice about heartbeat and lock accepts both the helper commands and the plain `trap`/`flock` form | done |
| `run NAME [--dry-run] [--json] [--engine PATH]` | Installs the agent the way the engine does into a **scratch `WCP_DIR`**, runs it with `OPS_ENGINE` pointing at a **stub engine** that implements `agent heartbeat\|lock\|log` faithfully and records every call (or at a real binary with `--engine`), then verifies the contract: heartbeat equals the exit code, a second start while the lock is held exits 0. Prints files written, engine calls and the log tail | done |
| `list`, `show NAME`, `api` | The agents in this repository, one manifest as JSON, the compatibility library's functions | done |
| `upgrade NAME --bump patch\|minor` | Raises `version` in `agent.toml` and the script header together | later |
| `check --against-engine VERSION` | Lists the helpers and operations an agent needs against that engine release | later |
| `test NAME` | Runs scenario files (fake `/proc`, fake site tree, canned engine answers) | later |

## 6. Existing and missing

Exists: the contract, `agent.toml`, `check_agents.py`, the registry and release
pipeline, `WCP_DIR` and the other overrides in the built-in agents, the engine
envelope, `capabilities`, `agent.install`, `agent.installFromRegistry`,
`agent.approve`, `agent.remove`, the developer CLI, and (engine PRs #75 and
#77) `agent heartbeat`, `lock`, `log`, `result emit`, `config get|list`,
`site list`, `version`, the `site.list` operation, and the explicit `WCP_DIR`,
`OPS_ENGINE` and `PATH` in every agent cron line and systemd unit (E8, PR #76).

Does not exist yet: `tool` subcommands, `agent run`, per-agent secrets, declarative requirements in
`agent.toml`, anything the built-in agents use of the above (they still carry
their own prologue; migrating them needs a version bump each).

## 7. The Python library: a frozen compatibility layer

- `lib/wcp_agent_lib.py` keeps the nine helpers published agents import
  (`run`, `run_argv`, `shell_quote`, `read_json`, `write_json`, `log_entry`,
  `notify`, `database_dump_command`, `sql_digest`). They are **frozen**: never
  renamed or changed in a way an installed agent can notice.
- **No new helper is added.** New shared behaviour is an `ops-engine agent`
  subcommand. If Python wants a nicer call, it is at most a three-line wrapper
  over the subcommand with no logic of its own.
- The phase 1 additions (`wcp_dir`, `dry_run`, `write_heartbeat`, `read_conf`,
  `list_sites`, `engine_call`, `engine_operations`, `emit_result`,
  `LIB_API_VERSION` 2) never reached a server and no published agent used
  them. They were **removed** in phase 2b; the file is again byte-identical to
  the engine's compiled copy, and a test pins the nine frozen names.
- The engine's compiled copy of the file keeps shipping as long as any
  published agent imports it. Dropping the copy altogether is a later,
  separate decision.
- `check_agents.py` still verifies that every name an agent imports from
  `wcp_agent_lib` exists in `lib/wcp_agent_lib.py`.

## 8. `agent.toml` additions and compatibility

| Field | Meaning | Checked by |
| --- | --- | --- |
| `min_engine` | Existing field. An agent that calls `ops-engine agent ...` sets it to **the engine release that ships those helpers**. Scaffolds set it (`HELPERS_MIN_ENGINE` in `scripts/wcp_agent.py`); the number is fixed when that engine release is cut | Engine at install (exists) |
| `requires_ops = ["site.deploy", ...]` | Engine operations the agent calls | Engine at install, against `capabilities` (E4) |
| `requires_helpers = ["result", "site"]` | Helper groups used beyond the first three, until `min_engine` is enough | Engine at install (E4) |
| `requires_tools = ["rclone", "docker"]` | Programs that must exist | Engine at install: ok, or "install X first" |
| `config_keys = ["WEBHOOK_URL"]` | Keys the agent reads with `config get` | Panel form and engine (E6) |
| `network = ["hooks.slack.com"]` | Hosts it may contact (documentation now) | Reviewer |

Do not add the new fields before the engine checks them: an unknown key is
ignored today, so they would only give a false sense of enforcement.

Compatibility rules: helper behaviour (arguments, exit codes, the three file
formats) is additive-only once released, like the protocol; a changed meaning
is a new subcommand. The heartbeat and log line shapes are read by the panel and
change only together with it.

## 9. Risks and findings

1. **Engine too old.** An agent using the helpers fails at once on an engine
   without them (usage error, exit 2, repeated every run). `min_engine` is the
   gate, and the scaffold sets it; until the engine release exists, publish no
   agent that uses the helpers.
2. **Five built-in agents took no lock** (`bruteforce-guard`, `cache-warmup`,
   `error-log-digest`, `metrics-agent`, `resource-alert`). Fixed with the plain
   `exec 9>`/`flock -n 9` form (a patch version bump each), which uses the same
   lock file as `agent lock`; `check_agents.py` now treats a missing lock as an
   error. They can move to `agent lock` once an engine release has it.
3. **Minimal `PATH`.** Cron does not have `/usr/local/bin`; scripts use
   `OPS_ENGINE` with the absolute default, and E8 makes the engine export it.
4. **The dry run is cooperative.** It is honoured by the helpers and by agents
   that check `WCP_DRY_RUN`. Never present it as a sandbox.
5. **systemd sandbox.** Agents with `ProtectSystem=strict` can write only their
   declared paths. The helpers write only `$WCP_DIR/agents` and `$WCP_DIR/logs`,
   which the units already allow; a new helper that needs another path must say
   so here first.
6. **A background process started by the command inherits the lock** and keeps
   it after the agent exits. Same as the old `exec 9>` pattern; agents must not
   daemonise.
7. **Rust port of the developer CLI.** `wcp_agent.py` could become
   `ops-engine agent dev ...` or a separate crate. It is optional: it runs on a
   laptop, nothing depends on it being Rust, and a port buys a single binary
   at the price of shipping a dev tool inside the server binary. Revisit only
   if people ask for it or the stub engine in the runner keeps drifting from
   the real helpers (the runner's `--engine PATH` is the cheaper fix).

## 10. Work outside this repository

Engine (`operations-engine`):

- **H1. `agent heartbeat|lock|log`** (done, PR #75).
- **H2. `agent result emit`, `agent config get|list`, `agent site list`**, and
  the discovery feature in `capabilities`/`agent version` (done, PR #77).
- **H3. `agent tool status|ensure`** (was E5; reuses `backup.installRclone`,
  `system.installDocker`, `tool.install`...).
- **E3. `agent.list` / `agent.status`** (installed agents, version, schedule,
  last heartbeat); the panel reads files today.
- **E4. Install-time checks** for `requires_*` against `capabilities` and
  `doctor`; refuse with a stable error code.
- **E6. `agent.configure`.** Writes `$WCP_DIR/agents/<name>.conf` (`0600`) from
  validated pairs; secrets never appear in responses or logs.
- **E7. `agent run`** (manual run, optional dry run).
- **E8. Per-agent cron line / systemd unit environment** (done, PR #76). The
  engine writes `WCP_DIR`, `OPS_ENGINE` and a `PATH` with `/usr/local/bin` into
  every agent cron line and `Environment=` into every sandboxed unit; a line
  written by an older engine gets them when the agent is installed again.
- Release: bump the crate version when the helpers ship so `min_engine` can
  name them.

Panel (`website-control-panel`):

- **P1. Show a result.** Read each agent's last result line next to the
  heartbeat; render `ok`, `warn`, `fail`, `skipped`.
- **P2. Agent configuration form** from `config_keys`, writing through E6; no
  secret shown after saving.
- **P3. Requirements view** from `requires_*`, with an "install tool" button
  calling `tool ensure`.
- **P4. Registry browsing, approval and install** shows `author`, `requires_*`
  and `network`.
- The panel's engine pin must include the release that ships the helpers
  before the panel offers an agent that needs them.

## 11. Phased plan

| Phase | Content | Where | Depends on |
| --- | --- | --- | --- |
| 1 (done) | Developer CLI, scaffolds, checker improvements, tests, docs | agents | nothing |
| 2a (done) | `agent heartbeat`, `lock`, `log`; scaffolds and the runner use them; this design | engine, agents | phase 1 |
| 2b (done) | `result emit`, `config get\|list`, `site list` (and `site.list`), discovery; compat library frozen and its phase 1 additions removed; explicit environment in cron lines and units (E8) | engine, agents | 2a |
| 3 | `tool status\|ensure`; `requires_tools`, `requires_ops`, `min_engine` checked at install (E4) | engine, agents | 2b |
| 4 | `agent run` with a dry run (E7), `agent configure` (E6); scenario `test`, optional Rust port of the developer CLI | engine, agents | 3 |
| Release | Cut an engine release that contains the helpers, set `HELPERS_MIN_ENGINE`, move the five built-in agents to `agent lock`; then the panel (P1 to P4) | engine, agents, panel | 2b to 4, decision pending |

## 12. Using it

```bash
python3 scripts/wcp_agent.py new uptime-note --schedule "0 * * * *" \
    --author "Your Name" --description "Every hour, appends the load to a log."
python3 scripts/wcp_agent.py run uptime-note --dry-run
python3 scripts/wcp_agent.py check
python3 -m unittest discover tests
```

`run` prints the files the agent wrote inside the scratch `WCP_DIR`, the engine
calls it made, and the tail of its log. Compare the files with `paths` in
`agent.toml`: the reviewer will. To exercise the real helpers, build the engine
and pass `--engine target/debug/ops-engine`.
