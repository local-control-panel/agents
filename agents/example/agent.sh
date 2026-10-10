#!/usr/bin/env bash
# wcp-agent: example
# wcp-agent-version: 0.1.0
# Template agent. Replace the body; keep the lock and the heartbeat.
# For a new agent, prefer: python3 scripts/wcp_agent.py new <name>
set -euo pipefail

NAME="example"
DIR="${WCP_DIR:-/root/.wcp}/agents"
mkdir -p "$DIR"

heartbeat() {
  local code=$?
  echo "{\"ts\":$(date +%s),\"exit_code\":$code}" >"$DIR/$NAME.heartbeat"
}
trap heartbeat EXIT

exec 9>"$DIR/$NAME.lock"
flock -n 9 || exit 0
