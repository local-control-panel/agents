#!/usr/bin/env bash
# wcp-agent: example
# wcp-agent-version: 0.1.0
# Template agent. Replace the body; keep the lock and the heartbeat.
set -euo pipefail

NAME="example"
DIR="/root/.wcp/agents"

exec 9>"$DIR/$NAME.lock"
flock -n 9 || exit 0

date +%s >"$DIR/$NAME.heartbeat"
