# Contributing

0. Read [docs/writing-agents.md](docs/writing-agents.md).
1. Copy `agents/example` to `agents/<your-name>` and edit `agent.toml`.
2. Run `python3 scripts/check_agents.py` and `shellcheck` on your script.
3. Open a pull request. Explain what the agent does and why it needs each path.
4. Expect careful review: the script runs as root on other people's servers.
   Small, readable scripts are reviewed faster.

New agents start as `tier = "community"`. Only owners can mark one `official`.
An agent you contribute is `third-party`: leave `origin` out or set
`origin = "third-party"`. The panel tells operators that it was reviewed by the
maintainers but is not made by them. A pull request that claims
`origin = "first-party"` for an agent the maintainers did not write will not be merged.
By contributing you agree your work is licensed under Apache-2.0.
