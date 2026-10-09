#!/usr/bin/env bash
# wcp-agent: resource-alert
# wcp-agent-version: 1.0.0
# wcp-agent-description: CPU/RAM/Disk threshold monitor - writes alerts to /root/.wcp/resource-alerts.jsonl

set -euo pipefail

WCP_DIR="${WCP_DIR:-/root/.wcp}"
export WCP_DIR
trap 'ec=$?; mkdir -p "$WCP_DIR/agents"; echo "{\"ts\":$(date +%s),\"exit_code\":$ec}" > "$WCP_DIR/agents/resource-alert.heartbeat"' EXIT

PROC_STAT="${PROC_STAT:-/proc/stat}"
PROC_MEMINFO="${PROC_MEMINFO:-/proc/meminfo}"
export PROC_STAT PROC_MEMINFO

python3 - << 'PYEOF'
import os, re, sys, time

WCP_DIR      = os.environ.get("WCP_DIR", "/root/.wcp")
PROC_STAT    = os.environ["PROC_STAT"]
PROC_MEMINFO = os.environ["PROC_MEMINFO"]
CONF_FILE    = os.path.join(WCP_DIR, "resource-alert.conf")
LOG_FILE     = os.path.join(WCP_DIR, "resource-alerts.jsonl")
STATE_FILE   = os.path.join(WCP_DIR, "resource-alert.state")
NOTIFY_FILE  = os.path.join(WCP_DIR, "notify.conf")
MAX_LINES    = 10000

sys.path.insert(0, os.path.join(WCP_DIR, "agents"))
import base64, types
_agent_lib = types.ModuleType('wcp_agent_lib')
exec(base64.b64decode('IiIiU2hhcmVkIGhlbHBlcnMgZm9yIFdDUCBidW5kbGVkIGFnZW50IHNjcmlwdHMuCgpVcGxvYWRlZCBhbG9uZ3NpZGUgZXZlcnkgYWdlbnQgc2NyaXB0IGludG8gL3Jvb3QvLndjcC9hZ2VudHMvIHNvIGFueSBvZiB0aGVtCmNhbiBgc3lzLnBhdGguaW5zZXJ0KDAsIFdDUF9ESVIgKyAiL2FnZW50cyIpOyBpbXBvcnQgd2NwX2FnZW50X2xpYmAuCiIiIgoKaW1wb3J0IGpzb24KaW1wb3J0IG9zCmltcG9ydCBzdWJwcm9jZXNzCgoKZGVmIHNoZWxsX3F1b3RlKHMpOgogICAgIiIiU2luZ2xlLXF1b3RlIGBzYCBmb3Igc2FmZSBpbnRlcnBvbGF0aW9uIGludG8gYSBzaGVsbCBjb21tYW5kIHN0cmluZy4iIiIKICAgIHJldHVybiAiJyIgKyBzdHIocykucmVwbGFjZSgiJyIsICInXFwnJyIpICsgIiciCgoKZGVmIHJ1bihjbWQsIHRpbWVvdXQ9NjApOgogICAgIiIiUnVuIGBjbWRgIChhIHNoZWxsIHN0cmluZykgYW5kIHJldHVybiB0aGUgY29tcGxldGVkIHByb2Nlc3MuIiIiCiAgICByZXR1cm4gc3VicHJvY2Vzcy5ydW4oY21kLCBzaGVsbD1UcnVlLCBjYXB0dXJlX291dHB1dD1UcnVlLCB0ZXh0PVRydWUsIHRpbWVvdXQ9dGltZW91dCkKCgpkZWYgcnVuX2FyZ3YoYXJndiwgdGltZW91dD02MCk6CiAgICAiIiJSdW4gYGFyZ3ZgIChhIGxpc3QsIG5vIHNoZWxsKSBhbmQgcmV0dXJuIHRoZSBjb21wbGV0ZWQgcHJvY2Vzcy4KCiAgICBVc2UgdGhpcyBpbnN0ZWFkIG9mIGBydW5gIGZvciBhbnkgYXJndW1lbnQgdGhhdCBpc24ndCBmdWxseSB0cnVzdGVkCiAgICAoZS5nLiBhIGZpbGVuYW1lIHJlcG9ydGVkIGJ5IGEgY29udGFpbmVyKSAtIHRoZXJlJ3Mgbm8gc2hlbGwgdG8KICAgIHJlLXBhcnNlIGl0LCBzbyBpdCBjYW4ndCBicmVhayBvdXQgb2YgcXVvdGluZy4KICAgICIiIgogICAgcmV0dXJuIHN1YnByb2Nlc3MucnVuKGFyZ3YsIGNhcHR1cmVfb3V0cHV0PVRydWUsIHRleHQ9VHJ1ZSwgdGltZW91dD10aW1lb3V0KQoKCmRlZiByZWFkX2pzb24ocGF0aCwgZGVmYXVsdD1Ob25lKToKICAgICIiIkxvYWQgSlNPTiBmcm9tIGBwYXRoYCwgcmV0dXJuaW5nIGBkZWZhdWx0YCBpZiBtaXNzaW5nL3VucmVhZGFibGUuIiIiCiAgICB0cnk6CiAgICAgICAgd2l0aCBvcGVuKHBhdGgpIGFzIGY6CiAgICAgICAgICAgIHJldHVybiBqc29uLmxvYWQoZikKICAgIGV4Y2VwdCBFeGNlcHRpb246CiAgICAgICAgcmV0dXJuIGRlZmF1bHQKCgpkZWYgd3JpdGVfanNvbihwYXRoLCBkYXRhKToKICAgICIiIldyaXRlIGBkYXRhYCBhcyBKU09OIHRvIGBwYXRoYCwgY3JlYXRpbmcgcGFyZW50IGRpcmVjdG9yaWVzIGFzIG5lZWRlZC4iIiIKICAgIG9zLm1ha2VkaXJzKG9zLnBhdGguZGlybmFtZShwYXRoKSwgZXhpc3Rfb2s9VHJ1ZSkKICAgIHdpdGggb3BlbihwYXRoLCAidyIpIGFzIGY6CiAgICAgICAganNvbi5kdW1wKGRhdGEsIGYpCgoKZGVmIGxvZ19lbnRyeShsb2dfZmlsZSwgZW50cnksIG1heF9saW5lcz0yMDAwKToKICAgICIiIkFwcGVuZCBgZW50cnlgIGFzIGEgSlNPTiBsaW5lIHRvIGBsb2dfZmlsZWAsIHRyaW1taW5nIHRvIGBtYXhfbGluZXNgLiIiIgogICAgb3MubWFrZWRpcnMob3MucGF0aC5kaXJuYW1lKGxvZ19maWxlKSwgZXhpc3Rfb2s9VHJ1ZSkKICAgIHdpdGggb3Blbihsb2dfZmlsZSwgImEiKSBhcyBmOgogICAgICAgIGYud3JpdGUoanNvbi5kdW1wcyhlbnRyeSwgc2VwYXJhdG9ycz0oIiwiLCAiOiIpKSArICJcbiIpCiAgICB0cnk6CiAgICAgICAgd2l0aCBvcGVuKGxvZ19maWxlKSBhcyBmOgogICAgICAgICAgICBsaW5lcyA9IGYucmVhZGxpbmVzKCkKICAgICAgICBpZiBsZW4obGluZXMpID4gbWF4X2xpbmVzOgogICAgICAgICAgICB3aXRoIG9wZW4obG9nX2ZpbGUsICJ3IikgYXMgZjoKICAgICAgICAgICAgICAgIGYud3JpdGVsaW5lcyhsaW5lc1stbWF4X2xpbmVzOl0pCiAgICBleGNlcHQgRXhjZXB0aW9uOgogICAgICAgIHBhc3MKCgpkZWYgX3NoX2VzYyhzKToKICAgIHJldHVybiAiJyIgKyBzdHIocykucmVwbGFjZSgiJyIsICInXFwnJyIpICsgIiciCgoKZGVmIG5vdGlmeShub3RpZnlfZmlsZSwgaXNfZmFpbHVyZSwgaXNfc3VjY2Vzcywgc3VtbWFyeV90ZXh0LCBldmVudF9wYXlsb2FkLCB1c2VybmFtZT0iV0NQIEFnZW50Iik6CiAgICAiIiJTZW5kIGBzdW1tYXJ5X3RleHRgL2BldmVudF9wYXlsb2FkYCB0byBldmVyeSBlbmFibGVkIGNoYW5uZWwgaW4KICAgIGBub3RpZnlfZmlsZWAgd2hvc2Ugb25fZmFpbHVyZS9vbl9zdWNjZXNzIGdhdGUgbWF0Y2hlcy4gYGlzX2ZhaWx1cmVgIGFuZAogICAgYGlzX3N1Y2Nlc3NgIGFyZSBpbmRlcGVuZGVudCAobm90IGFzc3VtZWQgY29tcGxlbWVudGFyeSkgc28gY2FsbGVycyB3aG9zZQogICAgb3V0Y29tZSBjYW4gYmUgbmVpdGhlciAoZS5nLiBub3RoaW5nIHRvIGRvKSBzaW1wbHkgcGFzcyBib3RoIEZhbHNlLgogICAgIiIiCiAgICB0cnk6CiAgICAgICAgd2l0aCBvcGVuKG5vdGlmeV9maWxlKSBhcyBmOgogICAgICAgICAgICBub3RpZnlfY29uZiA9IGpzb24ubG9hZChmKQogICAgZXhjZXB0IEV4Y2VwdGlvbjoKICAgICAgICByZXR1cm4KCiAgICBmb3IgY2ggaW4gbm90aWZ5X2NvbmYuZ2V0KCJjaGFubmVscyIsIFtdKToKICAgICAgICBpZiBub3QgY2guZ2V0KCJlbmFibGVkIiwgVHJ1ZSk6CiAgICAgICAgICAgIGNvbnRpbnVlCiAgICAgICAgc2VuZCA9IChpc19mYWlsdXJlIGFuZCBjaC5nZXQoIm9uX2ZhaWx1cmUiLCBUcnVlKSkgb3IgXAogICAgICAgICAgICAgICAoaXNfc3VjY2VzcyBhbmQgY2guZ2V0KCJvbl9zdWNjZXNzIiwgRmFsc2UpKQogICAgICAgIGlmIG5vdCBzZW5kOgogICAgICAgICAgICBjb250aW51ZQoKICAgICAgICB3ZWJob29rX3VybCA9IGNoLmdldCgid2ViaG9va191cmwiLCAiIikKICAgICAgICBpZiBub3Qgd2ViaG9va191cmw6CiAgICAgICAgICAgIGNvbnRpbnVlCgogICAgICAgIGNoX3R5cGUgPSBjaC5nZXQoInR5cGUiLCAid2ViaG9vayIpCiAgICAgICAgaWYgY2hfdHlwZSA9PSAic2xhY2siOgogICAgICAgICAgICBib2R5ID0ganNvbi5kdW1wcyh7InRleHQiOiBzdW1tYXJ5X3RleHQsICJ1c2VybmFtZSI6IHVzZXJuYW1lfSkKICAgICAgICBlbGlmIGNoX3R5cGUgPT0gImRpc2NvcmQiOgogICAgICAgICAgICBib2R5ID0ganNvbi5kdW1wcyh7ImNvbnRlbnQiOiBzdW1tYXJ5X3RleHR9KQogICAgICAgIGVsc2U6CiAgICAgICAgICAgIGJvZHkgPSBqc29uLmR1bXBzKGV2ZW50X3BheWxvYWQpCgogICAgICAgIHN1YnByb2Nlc3MucnVuKAogICAgICAgICAgICBmImN1cmwgLXMgLW0gMTAgLVggUE9TVCAtSCAnQ29udGVudC1UeXBlOiBhcHBsaWNhdGlvbi9qc29uJyAiCiAgICAgICAgICAgIGYiLWQge19zaF9lc2MoYm9keSl9IHtfc2hfZXNjKHdlYmhvb2tfdXJsKX0iLAogICAgICAgICAgICBzaGVsbD1UcnVlLAogICAgICAgICAgICBjYXB0dXJlX291dHB1dD1UcnVlLAogICAgICAgICAgICB0ZXh0PVRydWUsCiAgICAgICAgICAgIHRpbWVvdXQ9MzAsCiAgICAgICAgKQoKCmRlZiBkYXRhYmFzZV9kdW1wX2NvbW1hbmQoZGJfdHlwZSwgY29udGFpbmVyLCBwYXNzd29yZCwgZGF0YWJhc2UpOgogICAgIiIiU3RhYmxlIFNRTCBvdXRwdXQgYWxsb3dzIGEgcmVzdG9yZSBkcmlsbCB0byBjb21wYXJlIHRoZSBhY3R1YWwgc25hcHNob3QuIiIiCiAgICB0YXJnZXQgPSBzaGVsbF9xdW90ZShjb250YWluZXIpCiAgICBzZWNyZXQgPSBzaGVsbF9xdW90ZShwYXNzd29yZCkKICAgIG5hbWUgPSBzaGVsbF9xdW90ZShkYXRhYmFzZSkKICAgIGlmIGRiX3R5cGUgPT0gIm1hcmlhZGIiOgogICAgICAgIHJldHVybiAoZiJkb2NrZXIgZXhlYyAtZSBNWVNRTF9QV0Q9e3NlY3JldH0ge3RhcmdldH0gbWFyaWFkYi1kdW1wIC11cm9vdCAiCiAgICAgICAgICAgICAgICBmIi0tc2luZ2xlLXRyYW5zYWN0aW9uIC0tcm91dGluZXMgLS10cmlnZ2VycyAtLXNraXAtY29tbWVudHMgIgogICAgICAgICAgICAgICAgZiItLXNraXAtZXh0ZW5kZWQtaW5zZXJ0IC0tb3JkZXItYnktcHJpbWFyeSAtLXNraXAtYWRkLWxvY2tzIC0tc2tpcC1kaXNhYmxlLWtleXMge25hbWV9IikKICAgIGlmIGRiX3R5cGUgPT0gInBvc3RncmVzIjoKICAgICAgICByZXR1cm4gKGYiZG9ja2VyIGV4ZWMgLWUgUEdQQVNTV09SRD17c2VjcmV0fSB7dGFyZ2V0fSBwZ19kdW1wIC1VIHBvc3RncmVzICIKICAgICAgICAgICAgICAgIGYiLS1uby1vd25lciAtLW5vLXByaXZpbGVnZXMgLS1pbnNlcnRzIC0tcm93cy1wZXItaW5zZXJ0PTEge25hbWV9IikKICAgIHJhaXNlIFZhbHVlRXJyb3IoIlVuc3VwcG9ydGVkIGRhdGFiYXNlIHR5cGUiKQoKCmRlZiBzcWxfZGlnZXN0KHN0cmVhbSk6CiAgICAiIiJJZ25vcmUgb25seSBwZ19kdW1wJ3MgcmFuZG9tIHBzcWwgZ3VhcmQgdG9rZW4sIHdoaWNoIGlzIG5vdCBTUUwgZGF0YS4iIiIKICAgIGltcG9ydCBoYXNobGliCiAgICBpbXBvcnQgcmUKICAgIGRpZ2VzdCA9IGhhc2hsaWIuc2hhMjU2KCkKICAgIHNpemUgPSAwCiAgICBmb3IgbGluZSBpbiBzdHJlYW06CiAgICAgICAgaWYgcmUuZnVsbG1hdGNoKHJiIlxcKD86dW4pP3Jlc3RyaWN0IFtBLVphLXowLTldK1xyP1xuPyIsIGxpbmUpOgogICAgICAgICAgICBjb250aW51ZQogICAgICAgIGRpZ2VzdC51cGRhdGUobGluZSkKICAgICAgICBzaXplICs9IGxlbihsaW5lKQogICAgaWYgc2l6ZSA9PSAwOgogICAgICAgIHJhaXNlIFZhbHVlRXJyb3IoIkVtcHR5IFNRTCBkdW1wIikKICAgIHJldHVybiBkaWdlc3QuaGV4ZGlnZXN0KCkK'), _agent_lib.__dict__)
sys.modules['wcp_agent_lib'] = _agent_lib
from wcp_agent_lib import log_entry as _log_entry, notify as _notify, read_json, run, write_json

os.makedirs(WCP_DIR, exist_ok=True)
open(LOG_FILE, "a").close()

conf = read_json(CONF_FILE, {}) or {}
cpu_pct = int(conf.get("cpu_pct", 90))
ram_pct = int(conf.get("ram_pct", 90))
disk_pct = int(conf.get("disk_pct", 90))
cooldown_min = int(conf.get("cooldown_min", 30))

now = int(time.time())
cooldown_sec = cooldown_min * 60

state = read_json(STATE_FILE, {}) or {}
last_cpu = int(state.get("last_cpu", 0))
last_ram = int(state.get("last_ram", 0))
last_disk = int(state.get("last_disk", 0))


def emit(metric, value, threshold, mount=None):
    entry = {"metric": metric, "value": value, "threshold": threshold, "ts": now}
    if mount is not None:
        entry["mount"] = mount
    _log_entry(LOG_FILE, entry, max_lines=MAX_LINES)

    summary = f"Resource alert: {metric} at {value}% (threshold {threshold}%)"
    if mount is not None:
        summary += f" on {mount}"
    _notify(
        NOTIFY_FILE,
        is_failure=True,
        is_success=False,
        summary_text=summary,
        event_payload={"event": "resource_alert", **entry},
        username="WCP Resource Alert",
    )


# ── CPU - use /proc/stat for a 1-second sample ───────────────────────────────

def read_cpu_fields():
    with open(PROC_STAT) as f:
        for line in f:
            if line.startswith("cpu "):
                fields = line.split()[1:]
                idle = int(fields[3]) if len(fields) > 3 else 0
                total = sum(int(x) for x in fields)
                return total, idle
    return 0, 0


total1, idle1 = read_cpu_fields()
time.sleep(1)
total2, idle2 = read_cpu_fields()
d_total = total2 - total1
d_idle = idle2 - idle1
cpu_val = (d_total - d_idle) * 100 // d_total if d_total != 0 else 0

if cpu_val >= cpu_pct and now - last_cpu >= cooldown_sec:
    emit("cpu", cpu_val, cpu_pct)
    last_cpu = now

# ── RAM - from /proc/meminfo ─────────────────────────────────────────────────

mem_total = mem_avail = None
with open(PROC_MEMINFO) as f:
    for line in f:
        if line.startswith("MemTotal:"):
            mem_total = int(line.split()[1])
        elif line.startswith("MemAvailable:"):
            mem_avail = int(line.split()[1])

if mem_total is not None and mem_total > 0 and mem_avail is not None:
    ram_used = mem_total - mem_avail
    ram_val = ram_used * 100 // mem_total
    if ram_val >= ram_pct and now - last_ram >= cooldown_sec:
        emit("ram", ram_val, ram_pct)
        last_ram = now

# ── Disk - check all non-tmpfs mountpoints, alert on worst ──────────────────

r = run("df -x tmpfs -x devtmpfs -x overlay --output=source,size,used,avail,pcent,target", timeout=30)
worst_disk = 0
worst_mount = ""
for line in r.stdout.splitlines()[1:]:
    parts = line.split()
    if len(parts) < 6:
        continue
    pct_str = parts[4].rstrip("%")
    if not pct_str.isdigit():
        continue
    pct = int(pct_str)
    if pct > worst_disk:
        worst_disk = pct
        worst_mount = parts[5]

if worst_disk >= disk_pct and now - last_disk >= cooldown_sec:
    emit("disk", worst_disk, disk_pct, mount=worst_mount)
    last_disk = now

# ── Persist updated state ────────────────────────────────────────────────────

write_json(STATE_FILE, {"last_cpu": last_cpu, "last_ram": last_ram, "last_disk": last_disk})
PYEOF
