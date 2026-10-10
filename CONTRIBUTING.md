# Contributing

0. Read [docs/writing-agents.md](docs/writing-agents.md).
1. Copy `agents/example` to `agents/<your-name>` and edit `agent.toml`.
2. Run `python3 scripts/check_agents.py` and `shellcheck` on your script.
3. Open a pull request. Explain what the agent does and why it needs each path.
4. Expect careful review: the script runs as root on other people's servers.
   Small, readable scripts are reviewed faster.

New agents start as `tier = "community"`. Only owners can mark one `official`.
An agent you contribute is published as `third-party`: the panel tells operators
that it was reviewed by the maintainers but is not made by them. Only owners can
add a name to `first-party.json`, and `agent.toml` cannot set `origin`.
By contributing you agree your work is licensed under Apache-2.0.
