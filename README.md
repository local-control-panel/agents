# Agents for the Website Control Panel

Reviewed scripts that the Website Control Panel's operations engine installs on
managed servers and runs from root cron. This repository is open so that people
can write agents; it is **not** a place to drop arbitrary code. Everything here
runs as root, so every change is reviewed by an owner.

Status: **phase 4.** The seven built-in agents (`metrics-agent`,
`resource-alert`, `backup-agent`, `bruteforce-guard`, `backup-restore-drill`,
`error-log-digest`, `cache-warmup`) live here as `official` agents, byte for
byte what the engine installs today. The engine still carries its own copies
and installs them offline; it switches to this repository after the first
release that contains them. Design:
[agent-registry brief](https://github.com/local-control-panel/docs) (private
docs repository, `operations-engine/design/agent-registry.md`).

## How trust works

- A release tag is plain semver (`1.0.0`) for the whole repository.
- On release, a workflow computes the SHA-256 of every file of every agent
  directly from the tagged commit and publishes `registry.json`.
- The engine downloads from this repository only (the address is compiled into
  it), checks every file against `registry.json`, and installs through its
  existing atomic pipeline.
- A `community` agent additionally needs an operator to approve its exact hash.
- A version listed in `revoked` is refused for new installs; installed copies
  only produce a warning in the panel.

## Layout

```
agents/<name>/agent.toml        metadata (see below)
agents/<name>/agent.sh|.py      the script
lib/wcp_agent_lib.py            shared library; agents that need it embed a copy (see below)
scripts/build_registry.py       generates registry.json (release workflow only)
scripts/check_agents.py         validates metadata and bundled libraries (runs in CI)
scripts/bundle_lib.py           re-embeds lib/wcp_agent_lib.py into the agents that carry it
tests/                          tests for the checker
revoked.json                    [{"name": "...", "version": "x.y.z"}] versions the engine refuses
```

`agents/example` is a template. It is never published.

## `agent.toml`

```toml
name = "example"                 # equals the directory name, lowercase, a-z 0-9 -
version = "0.1.0"                # the agent's own semver
tier = "community"               # "official" or "community" ("example" is never published)
script = "agent.sh"              # file in this directory
schedule = "none"                # "fixed", "configurable" or "none"
default_schedule = ""            # five cron fields, required unless schedule = "none"
min_engine = "0.0.0"
description = "What it does, in one sentence."
paths = ["/var/log/example"]     # every path the script reads or writes
```

### Running under systemd instead of cron

```toml
isolation = "systemd"                                  # default: "cron"
writable_paths = ["/root/.wcp/agents", "/root/.wcp/logs"]
```

With `isolation = "systemd"` the engine installs a `oneshot` service and a
timer (`wcp-agent-<name>.service/.timer`) from a fixed template instead of a
cron line: `ProtectSystem=strict`, `ProtectHome=read-only`, `PrivateTmp`,
`NoNewPrivileges` and the kernel/clock protections, with write access only to
`writable_paths`. The agent cannot add or change a directive. `writable_paths`
must be absolute, below `/root/.wcp/`, `/var/log/`, `/var/www/` or
`/var/lib/wcp-agent/`, and use only `A-Za-z0-9._/-`; `schedule` must not be
`none`. If the host has no systemd, or the cron schedule has no exact
`OnCalendar=` equivalent, the engine falls back to cron. This limits **writes**
and privilege gain. It does not restrict reads or the network, and an agent
granted `/root/.wcp/agents` (for its heartbeat) can still overwrite sibling
scripts there. None of the built-in agents opts in: they need Docker and broad
access.

### Bundled library

The built-in agents run Python from a heredoc, so they embed
`lib/wcp_agent_lib.py` as a base64 blob. CI decodes it and fails when it differs
from `lib/`; after editing the library run `scripts/bundle_lib.py` and bump the
agents' versions. CI also requires `# wcp-agent-version:` in the script to equal
`version` in `agent.toml`.

The declared `paths` are documentation for the reviewer and the operator and
are linted for obvious mismatches. They are **not a sandbox**.

## Contract for an agent

- `sh`/`bash` or `python3` only; no interactive input; no network access unless
  the description says so.
- Exit non-zero on failure; take a lock so a second start does nothing.
- Write `<name>.heartbeat` the way the built-in agents do.
- Touch nothing outside the declared `paths`.

## Contributing

Open a pull request. An owner must approve every change, including workflows.
See [CONTRIBUTING.md](CONTRIBUTING.md). Licensed under Apache-2.0.
