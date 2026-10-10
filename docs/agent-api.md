# Agent API and command library

Status: design, with phase 1 implemented in this repository.
Audience: people who write agents, and the maintainers (including tools that
generate agents for us).

This page proposes one shared set of commands and helpers for writing agents:
what exists today, what this repository now ships, what must be added to the
operations engine and the panel, and in which order. It is written to be copied
into the project's internal documentation unchanged.

## 1. Goal

Today every agent re-implements the same things: the heartbeat trap, the lock,
a bounded log, "which sites are on this server", "call the engine and parse its
answer", "read a config file". The seven built-in agents do it seven slightly
different ways, and a new author has to copy one and hope. The goal is:

1. **One way to do each common thing**, available to a person at a keyboard and
   to a generator that writes agents for us, with the same names.
2. **Safe by default**: a dry run that cannot change anything, secrets that do
   not reach logs, engine calls that report errors instead of crashing.
3. **No new trust surface**: nothing here lets an agent do more than it can do
   today. The review model in [Security and trust model](security-model.md) is
   unchanged.

## 2. Facts that shape the design

These were checked in this repository and in the engine (`agent_lifecycle.rs`,
`agent_registry.rs`, `cli.rs`, `commands/capabilities.rs`).

| Fact | Consequence |
| --- | --- |
| An installed agent is **one file**, `/root/.wcp/agents/<name>.sh`, run with `bash` as root, from cron or systemd, with a minimal `PATH`, no TTY | A shared library cannot be "just another file in the agent's directory". Only what the engine installs next to the script is available |
| The engine installs **one** shared file, `wcp_agent_lib.py`, and it is **compiled into the engine** (`include_str!("../resources/agents/wcp_agent_lib.py")`). `lib/wcp_agent_lib.py` here was byte-identical to it | A helper added to the library in this repository reaches servers **only after the engine ships a new copy**. Until then the two copies differ (see section 8) |
| A Bash agent has no library at all | Bash gets a canonical *prologue* (the template), not an import |
| The engine is a binary, `ops-engine`, whose answers are JSON envelopes `{protocolVersion, operation, ok, result, warnings, error{code,message}}`; `capabilities` lists the operation names | Engine access is one thin wrapper plus a capability check. No new protocol is needed |
| Site facts live in root-owned manifests `/etc/operations-engine/sites/<siteId>.json` (`siteId`, `domain`, `contentRoot`, `siteUser`) and in `/var/www/<domain>/` | Reading sites works today without an engine operation, but the manifest is not yet a documented, stable contract |
| Config and secrets are plain files under `$WCP_DIR` (`notify.conf`, `backup.conf`, `rclone.conf`, `bruteforce-config.env`) written by the panel | Any agent can read every other agent's secrets. See section 4.4 |
| Five of the seven built-in agents take no lock, although the contract demands `flock -n`; the checker never verified it | Fix in the agents; the checker now warns (it cannot error without failing published agents) |
| `flock` is missing on macOS, `df -B` is GNU only | Local runs need a shim for `flock`; agents are written for Linux and cannot all run on a laptop |

## 3. The layers

```
 people / generators
        │
 A. wcp_agent.py   developer CLI           (this repo, never on a server)
        │ writes, checks, runs
 B. the prologue   heartbeat + lock + log  (template; part of every script)
        │
 C. wcp_agent_lib  Python helpers          (engine installs it next to scripts)
        │ calls
 D. ops-engine     operations, capabilities (the engine, already on the server)
        │ reads
 E. agent.toml     what an agent declares  (checked in CI and by the engine)
```

Each layer only depends on the ones below it, and layer A is the only one that
does not exist on a server.

## 4. The proposed API

Status column: **exists** (before this work), **phase 1** (implemented by the
pull request that adds this page), **engine** (needs engine or panel work,
section 9), **later**.

### 4.1 Developer commands (`python3 scripts/wcp_agent.py ...`)

| Command | Does | Status |
| --- | --- | --- |
| `new NAME [--lang bash\|python] [--author N] [--description T] [--schedule CRON]` | Scaffolds `agents/NAME/` with a valid `agent.toml` and a script that already has the heartbeat, the lock and a bounded log. Refuses bad names, existing agents, quotes in text. The result passes `check` | phase 1 |
| `check [NAME...]` | Validates `agent.toml` and the script (same code as CI) | phase 1 (wraps `check_agents.py`) |
| `run NAME [--dry-run] [--json]` | Installs the agent the way the engine does (as `<name>.sh` next to the library) into a **scratch `WCP_DIR`**, with a **stub engine** that records every call, runs it, then verifies the contract: exit code equals the heartbeat's `exit_code`, the heartbeat has exactly `ts` and `exit_code`, a second start while the lock is held exits 0. Prints files written, engine calls and the log tail | phase 1 |
| `list`, `show NAME` | The agents in this repository, one manifest as JSON | phase 1 |
| `api` | The helper functions of the shared library with their first doc line | phase 1 |
| `upgrade NAME --bump patch\|minor` | Raises `version` in `agent.toml` and the script header together | later |
| `check --against-engine VERSION` | Lists the operations and library version an agent needs against that engine release | later (needs `requires_*`, section 4.5) |
| `test NAME` | Runs scenario files (fake `/proc`, fake site tree, canned engine answers) | later |

### 4.2 The prologue (Bash)

Every script keeps the same four ingredients, in this order. `new` writes them;
a generator should emit exactly this text.

1. `set -euo pipefail`, `NAME`, `WCP_DIR="${WCP_DIR:-/root/.wcp}"`, `mkdir -p` of
   `agents` and `logs`.
2. A `heartbeat` function and `trap heartbeat EXIT`, **before** anything can fail.
3. `exec 9>"$WCP_DIR/agents/$NAME.lock"; flock -n 9 || exit 0`.
4. A result line appended to `$WCP_DIR/logs/$NAME.log` and trimmed to a maximum
   number of lines.

Environment names an agent should honour, so it can be run anywhere:

| Variable | Meaning | Default |
| --- | --- | --- |
| `WCP_DIR` | State directory | `/root/.wcp` |
| `WCP_DRY_RUN` | `1` means change nothing outside `WCP_DIR`; skip engine mutations | unset |
| `OPS_ENGINE` | The engine binary | `ops-engine` |
| `SITES_ROOT` | Where the sites live | `/var/www` |
| `WCP_SITES_MANIFEST_DIR` | Where the engine's site manifests live | `/etc/operations-engine/sites` |

### 4.3 Library helpers (`from wcp_agent_lib import ...`)

Library API version 2 (`LIB_API_VERSION`). Everything is additive: the helpers
that existed (`run`, `run_argv`, `shell_quote`, `read_json`, `write_json`,
`log_entry`, `notify`, `database_dump_command`, `sql_digest`) are unchanged.

| Helper | Does | Status |
| --- | --- | --- |
| `wcp_dir()` | `$WCP_DIR` or `/root/.wcp` | phase 1 |
| `dry_run()` | True when `WCP_DRY_RUN=1` | phase 1 |
| `agent_file(name, suffix)`, `log_file(name)` | Standard paths for heartbeat, lock and log | phase 1 |
| `write_heartbeat(name, exit_code=0)` | Writes the heartbeat line atomically | phase 1 |
| `read_conf(path)` | Parses `KEY=VALUE` files without a shell; `{}` if missing. Result may hold secrets: never log it | phase 1 |
| `list_sites()` | Sites on this server: `site_id`, `domain`, `content_root`, `site_user`, `source`. Reads the engine manifests first, then plain domain-named directories under `SITES_ROOT`. Read-only; rejects names that are not domain-like | phase 1 |
| `engine_call(argv, timeout)` | Runs `ops-engine ...`, returns the envelope as a dict. **Never raises**: missing binary, timeout and bad output become `ENGINE_UNAVAILABLE`, `ENGINE_TIMEOUT`, `ENGINE_BAD_RESPONSE`. With `WCP_DRY_RUN=1` only `version`, `capabilities`, `doctor` and `operation status/list` run; anything else returns `{"ok": true, "dryRun": true}` without executing | phase 1 |
| `engine_operations()` | The set of operation names this engine supports (empty if unreachable), so an agent can degrade on an old engine | phase 1 |
| `emit_result(name, status, summary, **data)` | Appends `{ts, agent, status, summary, ...}` to the agent's log; `status` is `ok`, `warn`, `fail` or `skipped`. Not written in a dry run | phase 1 |
| `notify(...)` | Sends to the operator's channels | exists |
| `get_secret(name)`, `agent_config()` | Per-agent configuration and secrets from a file only that agent can read | engine (4.4) |
| `list_sites()` backed by an engine operation | Stable site facts without reading manifests directly | engine |

Why no `new-agent` or `check` inside the library: the library runs on servers,
the tools run on a developer machine. Keeping them apart keeps the file the
engine installs small and reviewable.

### 4.4 Configuration and secrets

Today an agent reads `$WCP_DIR/*.conf` directly, and all of those files are
readable by every agent (they all run as root). Nothing in this phase changes
that; it is the largest gap and it needs the engine and the panel:

- An agent declares what it reads in `agent.toml` (`config_keys`, section 4.5).
- The panel writes **one file per agent**, `$WCP_DIR/agents/<name>.conf`, mode
  `0600`, through a new engine operation; the engine validates keys against the
  declared list.
- `read_conf()` already parses that format. `agent_config()` would only fix the
  path, so the call is the same on day one and after the engine work.
- Rules for agents (and for the reviewer): never print a secret, never put one
  in `emit_result` data, never pass one on a command line (use an environment
  variable or a file, as `database_dump_command` does).

### 4.5 `agent.toml` additions (proposed, not enforced yet)

| Field | Meaning | Checked by |
| --- | --- | --- |
| `requires_ops = ["site.deploy", ...]` | Engine operations the agent calls | Engine at install (against `capabilities`); the panel shows it |
| `requires_tools = ["rclone", "docker"]` | Programs that must exist | Engine at install: `ok`, or a clear "install X first" |
| `min_lib = 2` | Library API version the script needs | Engine at install |
| `config_keys = ["WEBHOOK_URL"]` | Keys the agent reads from its own config file | Panel form and engine |
| `network = ["hooks.slack.com"]` | Hosts it may contact (documentation now) | Reviewer |

`min_engine` already exists and stays the coarse gate. Until the engine reads
the new fields, they are optional documentation and `check_agents.py` does not
know them (an unknown key is ignored by the engine today, so adding them early
is harmless, but they would give a false sense of enforcement; do not add them
before the engine checks them).

### 4.6 Capabilities and installing tools

- *Check capabilities*: `engine_operations()` / `ops-engine capabilities` exist.
  An agent calls an optional operation only when it is in the set.
- *Install tools*: an agent must not run `apt`, `curl | sh` or `docker pull`
  itself. The engine already owns the installs it supports
  (`backup.installRclone`, `system.installDocker`, `dbTool.converge`). The API
  is therefore declarative: `requires_tools` in the manifest and one generic
  engine operation `tool.ensure` (section 9) that the **operator** triggers
  from the panel. An agent that finds its tool missing exits non-zero with a
  one-line reason, writes `emit_result(..., "fail", ...)` and leaves the fix to
  the operator.

### 4.7 Result and exit conventions

| Situation | Exit code | `emit_result` status |
| --- | --- | --- |
| Did the work | 0 | `ok` |
| Did the work, with something worth attention | 0 | `warn` |
| Nothing to do (feature off, no sites) | 0 | `skipped` |
| Failed | non-zero | `fail` (then the heartbeat says so) |
| Another run holds the lock | 0, nothing written | none |

The heartbeat says whether the agent ran; the log line says what it found. The
panel needs only these two files.

## 5. Existing and missing, in one list

Exists before this work: the contract, `agent.toml`, `check_agents.py`, the
registry and release pipeline, the library with nine helpers, `WCP_DIR` and the
other overrides in the built-in agents, the engine envelope, `capabilities`,
`agent.install`, `agent.installFromRegistry`, `agent.approve`, `agent.remove`.

Does not exist: a scaffolder, a local runner, a dry-run convention, helpers for
sites, engine calls, results and configuration, any check that the heartbeat
or the lock is really there (the checker only warns now), per-agent secrets, a
read-only engine operation for sites, declarative requirements.

## 6. Compatibility rules

- The library is **additive only**. A helper is never renamed or changed in a
  way an installed agent can notice. `LIB_API_VERSION` goes up when helpers are
  added.
- `check_agents.py` now verifies that every name an agent imports from
  `wcp_agent_lib` exists in `lib/wcp_agent_lib.py`, which catches typos and
  removed helpers. It cannot tell whether the **engine's** copy is new enough;
  that is what `min_lib` is for (section 4.5).
- **Do not publish an agent that imports a phase 1 helper until the engine
  release that ships the library with it** (section 9, item E1). Until then,
  use the helpers only for local runs, or inline the few lines you need.
- A Bash agent never depends on the library.

## 7. Phase 1: implemented in this repository

| Piece | File |
| --- | --- |
| Developer CLI | `scripts/wcp_agent.py` |
| Library helpers (API version 2) | `lib/wcp_agent_lib.py` |
| Checker: library names exist, `# wcp-agent:` header equals the name, warnings for a missing heartbeat or lock | `scripts/check_agents.py` |
| Tests (scaffold, run harness, library helpers, checker) | `tests/test_wcp_agent.py` |
| `example` honours `WCP_DIR` and writes a valid JSON heartbeat | `agents/example/agent.sh` |
| CI runs the unit tests | `.github/workflows/ci.yml` |
| Guides point to `new` and `run` | `README.md`, `CONTRIBUTING.md`, `docs/` |

No engine or panel code changes. No published agent changes.

## 8. Risks and findings

1. **Two copies of the library.** The engine compiles in its own copy. Until the
   engine syncs, `lib/wcp_agent_lib.py` here is ahead of it. Nothing breaks (the
   additions are unused by any published agent), but a human must not assume a
   helper is on servers. Proposal E1 makes the engine take the file from this
   repository's release, with a CI check that the hashes match.
2. **Five built-in agents take no lock** (`bruteforce-guard`, `cache-warmup`,
   `error-log-digest`, `metrics-agent`, `resource-alert`), although the contract
   says every agent must. `check_agents.py` prints a warning for each. Fixing
   them changes scripts that run every minute and needs a version bump and the
   engine's copy changed in step; it is deliberately out of scope here.
3. **The dry run is cooperative.** `WCP_DRY_RUN=1` is honoured by the helpers
   and by agents that check it. It is a development aid, not a sandbox, and the
   documentation must never present it as one.
4. **Site manifest format.** `list_sites()` reads fields that the engine
   documents as "proposed". It tolerates missing keys and falls back to
   `/var/www`, but it should move to an engine operation (E2).
5. **macOS.** `run` supplies a `flock` shim, but agents that call GNU tools
   still need Linux (a container or the Lima VMs).

## 9. Work needed outside this repository

Engine (`operations-engine`):

- **E1. Ship the library from the registry.** Install `wcp_agent_lib.py` from
  the agents release (hash-checked like scripts), or at least sync the compiled
  copy from this repository and fail the engine's CI if they differ. Expose the
  installed `LIB_API_VERSION` in `agent.list`/`doctor`.
- **E2. `site.list` (read-only).** Returns `siteId`, `domain`, `siteUser`,
  `contentRoot`, runtime type for every enrolled site. Lets agents stop reading
  manifests.
- **E3. `agent.list` / `agent.status`.** Installed agents with version, author,
  schedule, last heartbeat. The panel reads files today.
- **E4. Install-time checks for `requires_ops`, `requires_tools`, `min_lib`**
  against `capabilities`, `doctor` and the library version; refuse with a stable
  error code.
- **E5. `tool.ensure`.** One generic, allow-listed operation that converges a
  named tool (rclone, docker, wp-cli, ...), reusing the existing installers.
- **E6. `agent.configure`.** Writes `$WCP_DIR/agents/<name>.conf` (`0600`) from
  validated key/value pairs; secrets never appear in responses or logs.
- **E7. `agent.run` (manual run, optional `--dry-run`).** Starts an installed
  agent once, with `WCP_DRY_RUN` if asked, and returns the result line. Needed
  for "try it" in the panel.
- **E8. Per-agent systemd/cron environment.** Put `WCP_DIR` and `OPS_ENGINE` in
  the unit and the cron line explicitly instead of relying on defaults.

Panel (`website-control-panel`):

- **P1. Show a result.** Read each agent's last `emit_result` line next to the
  heartbeat; render `ok`, `warn`, `fail`, `skipped`.
- **P2. Agent configuration form** from `config_keys`, writing through E6; no
  secret ever shown after saving.
- **P3. Requirements view** from `requires_*`, with an "install tool" button
  that calls E5.
- **P4. Registry browsing, approval and install** (already planned) should show
  `author`, `requires_*` and `network`.

## 10. Phased plan

| Phase | Content | Where | Depends on |
| --- | --- | --- | --- |
| 1 (done here) | CLI (`new`, `check`, `run`, `list`, `show`, `api`), prologue templates, library API 2, checker improvements, tests, docs | agents repo | nothing |
| 2 | E1 (library from the registry), E4 (install-time checks), manifest fields `requires_ops`, `requires_tools`, `min_lib` become real and are enforced by `check_agents.py`; fix the five agents' missing lock; `upgrade` command | engine, agents | phase 1 merged |
| 3 | E2, E3, E5, E6 and `get_secret`/`agent_config`; `list_sites()` moves to E2; P1 to P3 | engine, panel, agents | phase 2 |
| 4 | E7 (manual run and dry run from the panel), scenario-based `test` command, a second library flavour for Bash if people ask for one (a small sourced file the engine installs) | all | phase 3 |

## 11. Using it

```bash
python3 scripts/wcp_agent.py new uptime-note --schedule "0 * * * *" \
    --author "Your Name" --description "Every hour, appends the load to a log."
python3 scripts/wcp_agent.py run uptime-note --dry-run
python3 scripts/wcp_agent.py check
python3 -m unittest discover tests
```

`run` prints the files the agent wrote inside the scratch `WCP_DIR`, the engine
calls it made, and the tail of its log. Compare the files with `paths` in
`agent.toml`: the reviewer will.
