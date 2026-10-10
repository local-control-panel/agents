# Writing agents

An *agent* is a small, reviewed script that the Website Control Panel's
operations engine installs on a managed server and runs on a schedule as root.
These pages explain how to write one and how it gets from a pull request onto a
server.

| Page | Read it to learn |
| --- | --- |
| [Writing an agent](writing-agents.md) | What an agent is, how it runs, the rules it must follow, how to test it and how it is published |
| [`agent.toml` reference](agent-toml-reference.md) | Every metadata field, its allowed values and what checks it |
| [Security and trust model](security-model.md) | Who is trusted with what, what the engine enforces, what it does not |
| [Examples](examples.md) | A minimal agent, a realistic Bash agent and a realistic Python agent |

If you have never written one, read *Writing an agent*, then copy
`agents/example` and follow the examples.
