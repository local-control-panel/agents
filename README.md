# Agents for the Website Control Panel

Reviewed scripts that the Website Control Panel's operations engine installs on
managed servers and runs from root cron. This repository is open so that people
can write agents; it is **not** a place to drop arbitrary code. Everything here
runs as root, so every change is reviewed by an owner.

Status: **phase 1.** The repository, its protections and the release pipeline
exist. The seven built-in agents still ship inside the engine and are not
moved here yet. The engine cannot install from this repository until
`agent.installFromRegistry` is released. Design:
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
lib/wcp_agent_lib.py            shared library (not yet used)
scripts/build_registry.py       generates registry.json (release workflow only)
scripts/check_agents.py         validates metadata (runs in CI)
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
