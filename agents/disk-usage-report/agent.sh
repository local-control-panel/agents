#!/usr/bin/env bash
# wcp-agent: disk-usage-report
# wcp-agent-version: 1.0.0
# wcp-agent-description: Once a day, logs how full the root filesystem is and the size of the ten largest sites.
#
# Read-only apart from its own log, heartbeat and lock under WCP_DIR. Appends
# one JSON line per run to $WCP_DIR/logs/disk-usage-report.log.

set -euo pipefail

WCP_DIR="${WCP_DIR:-/root/.wcp}"
SITES_ROOT="${SITES_ROOT:-/var/www}"
NAME="disk-usage-report"
LOG_FILE="$WCP_DIR/logs/$NAME.log"
MAX_LOG_LINES=2000

mkdir -p "$WCP_DIR/agents" "$WCP_DIR/logs"
heartbeat() {
  local code=$?
  echo "{\"ts\":$(date +%s),\"exit_code\":$code}" > "$WCP_DIR/agents/$NAME.heartbeat"
}
trap heartbeat EXIT

# A second start while one is running does nothing.
exec 9>"$WCP_DIR/agents/$NAME.lock"
flock -n 9 || exit 0

read -r size used avail < <(df -PB1 / | awk 'NR==2 {print $2, $3, $4}')
pct=$(( size > 0 ? used * 100 / size : 0 ))

sites=""
if [ -d "$SITES_ROOT" ]; then
  # Only plain domain-like names are reported; anything else is skipped so the
  # JSON line can never be broken by a directory name.
  while read -r bytes path; do
    domain="${path##*/}"
    [[ "$domain" =~ ^[A-Za-z0-9._-]+$ ]] || continue
    sites+="${sites:+,}{\"domain\":\"$domain\",\"bytes\":$bytes}"
  done < <(du -sxB1 "$SITES_ROOT"/*/ 2>/dev/null | sed 's|/$||' | sort -rn | head -n 10)
fi

printf '{"ts":%s,"root_total_bytes":%s,"root_used_bytes":%s,"root_avail_bytes":%s,"root_used_pct":%s,"sites":[%s]}\n' \
  "$(date +%s)" "$size" "$used" "$avail" "$pct" "$sites" >> "$LOG_FILE"

if [ "$(wc -l < "$LOG_FILE")" -gt "$MAX_LOG_LINES" ]; then
  tail -n "$MAX_LOG_LINES" "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
fi
