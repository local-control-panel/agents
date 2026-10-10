# `agent.toml` reference

Every agent has an `agent.toml` next to its script. `scripts/check_agents.py`
validates it in CI and again in the release workflow, and the engine checks the
published values again before installing.

```toml
name = "disk-usage-report"
version = "1.0.0"
tier = "community"
script = "agent.sh"
schedule = "configurable"
default_schedule = "0 6 * * *"
min_engine = "0.1.0"
description = "Once a day, logs how full the root filesystem is."
paths = ["/root/.wcp/agents", "/root/.wcp/logs", "/var/www"]
isolation = "systemd"
writable_paths = ["/root/.wcp/agents", "/root/.wcp/logs"]
```

## Required fields

| Field | Type | Rules |
| --- | --- | --- |
| `name` | string | Equals the directory name. Lowercase letters, digits and `-`; starts with a letter or digit; at most 64 characters. Cannot be the name of a built-in agent when installing through the registry |
| `version` | string | The agent's own version, `x.y.z` (digits only). Raise it for every change to the script. The script header `# wcp-agent-version:` must match |
| `tier` | string | `official`, `community`, or `example`. New agents use `community`; only owners set `official`. `example` marks the template and is never published |
| `script` | string | File name of the script inside the agent's directory (a plain name, no `/`, not starting with `.`) |
| `schedule` | string | `fixed`, `configurable` or `none`. See below |
| `default_schedule` | string | Five cron fields (or `@hourly`, `@daily`, …). Required unless `schedule = "none"`, in which case it must be empty `""` |
| `min_engine` | string | Oldest engine version that can install the agent, `x.y.z`. The engine refuses to install on an older one |
| `description` | string | One sentence a reviewer and an operator can read to know what the agent does, including any network access |
| `paths` | list of strings | Every absolute path the script reads or writes. No `..` |

### `schedule`

| Value | Meaning |
| --- | --- |
| `fixed` | Runs on `default_schedule`; an operator cannot change it |
| `configurable` | Starts at `default_schedule`; an operator may choose another schedule at install time |
| `none` | The engine adds no schedule. The agent is started by something else |

Asking to change the schedule of a `fixed` or `none` agent is refused.

### `tier`

| Tier | Install requires |
| --- | --- |
| `official` | The release it came from. The engine checks the hash against `registry.json` |
| `community` | The same, **plus** an operator's approval of the exact script hash on that server |

### `paths`

`paths` is documentation for the reviewer and the operator, and CI lints it for
obvious problems. It is **not a sandbox**: the script can still touch other
paths, and reviewers read it against the code to catch that. Keep it accurate
and as small as possible.

## Optional fields: systemd isolation

| Field | Type | Rules |
| --- | --- | --- |
| `isolation` | string | `cron` (the default) or `systemd` |
| `writable_paths` | list of strings | Required with `isolation = "systemd"`, not allowed with `cron`. Absolute paths **strictly below** `/root/.wcp/`, `/var/log/`, `/var/www/` or `/var/lib/wcp-agent/`; each component uses only `A-Za-z0-9._-`; no `.` or `..` components; at most 16 entries and 200 characters each. No path component may be a symbolic link at install time |

With `isolation = "systemd"` the schedule must not be `none`. See
[Running under systemd](writing-agents.md#running-under-systemd) for what the
sandbox does and does not do.

## What the published registry contains

On release, `registry.json` records, per agent: `version`, `tier`, `schedule`,
`defaultSchedule`, `minEngine`, `script`, `description`, `paths`, the isolation
settings when present, and the SHA-256 of every file in the agent's directory.
It also records the release tag, the commit and the list of revoked
`{name, version}` pairs. These come straight from the tagged commit and are
never typed by hand.

## Revoking a version

`revoked.json` at the repository root lists versions the engine must not
install:

```json
[{"name": "my-agent", "version": "1.0.1"}]
```

Both keys are required and nothing else. A revoked version is refused for new
installs; servers that already run it only show a warning.
