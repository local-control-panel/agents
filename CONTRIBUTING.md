# Contributing

0. Read [docs/writing-agents.md](docs/writing-agents.md).
1. Copy `agents/example` to `agents/<your-name>` and edit `agent.toml`.
2. Run `python3 scripts/check_agents.py` and `shellcheck` on your script.
3. Open a pull request. Explain what the agent does and why it needs each path.
4. Expect careful review: the script runs as root on other people's servers.
   Small, readable scripts are reviewed faster.

New agents start as `tier = "community"`. Only owners can mark one `official`.
Put your own name in `author`. An agent is first-party only when its author is
`Website Control Panel`, which is for agents the maintainers wrote; a pull
request that uses that name for someone else's agent will not be merged. Every other
author is third-party, and the panel tells operators the agent was reviewed by
the maintainers but is not made by them.
By contributing you agree your work is licensed under Apache-2.0.
