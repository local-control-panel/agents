# Contributing

1. Copy `agents/example` to `agents/<your-name>` and edit `agent.toml`.
2. Run `python3 scripts/check_agents.py` and `shellcheck` on your script.
3. Open a pull request. Explain what the agent does and why it needs each path.
4. Expect careful review: the script runs as root on other people's servers.
   Small, readable scripts are reviewed faster.

New agents start as `tier = "community"`. Only owners can mark one `official`.
By contributing you agree your work is licensed under Apache-2.0.
