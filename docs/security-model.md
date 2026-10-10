# Security and trust model

An agent runs as root on someone else's server. This page says what protects
operators, what an agent author can and cannot do, and where the protection
ends.

## Who is trusted with what

- **The repository and its owners.** The root of trust is this repository on
  GitHub: its protected `main` branch, mandatory review by an owner, a release
  workflow that needs manual approval, protected tags, workflows pinned to
  commit SHAs, and two-factor authentication for everyone with write access.
  There are no separate signing keys. Hashes are computed from the files in the
  tagged commit.
- **The engine.** The address it downloads agents from is compiled into it. No
  request, setting or file can change it, so a compromised panel or laptop
  cannot make a server install code from somewhere else.
- **Operators.** An operator names an agent, a release and the hash of the
  script they reviewed. They never send code.
- **Agent authors.** A contributor is not trusted by default. Their change
  must be read and approved by an owner, and `community` agents need one more
  approval per server.

## What the engine checks on install

Before anything is written, the engine verifies all of this:

1. `registry.json` is for the requested release and uses a supported format.
2. The release lists the agent, and the agent's version is not revoked.
3. The engine is at least the agent's `min_engine`.
4. The script is plain text starting with `#!`, UTF-8, within the size limit.
5. The script's SHA-256 equals the value in `registry.json` **and** the value the
   operator supplied, so what was reviewed is exactly what runs.
6. For a `community` agent, an operator approved that exact hash on that server.
   Changing one byte requires a new approval.
7. Isolation settings, if any, are valid (see the reference).
8. The agent does not use the name of a built-in agent.

Downloads are HTTPS only, size-limited and time-limited. Installation is
atomic: the script and manifest are written first, the schedule is committed
last, and any failure restores the previous state.

Approvals can be withdrawn for one hash or for all hashes of an agent.

## What an agent can do

By default, anything root can do on that server. That is why review is the
control. In practice, built-in agents:

- read system state (`/proc`, logs, the sites under `/var/www`);
- write their own logs, state and heartbeat under `/root/.wcp`;
- run other programs (for example Docker, `rclone`, `curl`);
- call back into the engine for operations that must go through it, such as
  applying a ban list.

## Off limits by rule

The contract in the repository's README is the written policy. The engine does
not stop an agent that breaks it, and a pull request that breaks it will not be
merged:

- only `sh`/`bash` or `python3`;
- no interactive input;
- no network access unless the description says so;
- touching nothing outside the declared `paths`;
- exiting non-zero on failure, taking a lock, and writing the heartbeat.

The cron table, the systemd units and the manifest belong to the engine. An
agent has no reason to edit them, and a change that does so needs a very good
explanation in its pull request.

There is no further list of banned techniques today. Owners decide each change on
its merits and may add rules over time.

## What this model does not protect against

- **A misled reviewer.** The protections make sure someone reads the script;
  they do not replace the reading. Small, readable scripts are reviewed
  properly.
- **A compromised account that can approve, or GitHub itself.** Trust is placed
  in the repository and the people with access to it.
- **A script that does more than it declares.** CI catches obvious mismatches
  between `paths` and the code, not everything. `paths` is documentation, not a
  sandbox.
- **The limits of systemd isolation.** It restricts writes and privilege gain,
  not reads or network use, and an agent with write access to
  `/root/.wcp/agents` can overwrite sibling scripts there.
- **A server that cannot reach GitHub.** It cannot install from the registry.
- **Revocation does not remove anything.** A revoked version stays installed
  and running; the operator is warned. This avoids silently stopping something
  like backups.

## Reporting a problem

Report a vulnerability privately through GitHub: **Security → Report a
vulnerability** on this repository. Do not open a public issue. An unsafe
released version is added to `revoked.json` in the next release.
