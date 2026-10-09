#!/usr/bin/env bash
# wcp-agent: metrics-agent
# wcp-agent-version: 1.1.0
# wcp-agent-description: Collects system metrics every minute, rotating into monthly files (3-month retention)

set -euo pipefail

WCP_DIR="${WCP_DIR:-/root/.wcp}"
export WCP_DIR
trap 'ec=$?; mkdir -p "$WCP_DIR/agents"; echo "{\"ts\":$(date +%s),\"exit_code\":$ec}" > "$WCP_DIR/agents/metrics-agent.heartbeat"' EXIT
METRICS_DIR="${METRICS_DIR:-$WCP_DIR/metrics}"
export METRICS_DIR

# Proc file paths - overridable for testing
PROC_STAT="${PROC_STAT:-/proc/stat}"
PROC_LOADAVG="${PROC_LOADAVG:-/proc/loadavg}"
PROC_NET_DEV="${PROC_NET_DEV:-/proc/net/dev}"
PROC_UPTIME="${PROC_UPTIME:-/proc/uptime}"
export PROC_STAT PROC_LOADAVG PROC_NET_DEV PROC_UPTIME

python3 - << 'PYEOF'
import glob, json, os, re, sys, time

METRICS_DIR   = os.environ["METRICS_DIR"]
PROC_STAT     = os.environ["PROC_STAT"]
PROC_LOADAVG  = os.environ["PROC_LOADAVG"]
PROC_NET_DEV  = os.environ["PROC_NET_DEV"]
PROC_UPTIME   = os.environ["PROC_UPTIME"]

sys.path.insert(0, os.path.join(os.environ.get("WCP_DIR", "/root/.wcp"), "agents"))
import base64, types
_agent_lib = types.ModuleType('wcp_agent_lib')
exec(base64.b64decode('IiIiU2hhcmVkIGhlbHBlcnMgZm9yIFdDUCBidW5kbGVkIGFnZW50IHNjcmlwdHMuCgpVcGxvYWRlZCBhbG9uZ3NpZGUgZXZlcnkgYWdlbnQgc2NyaXB0IGludG8gL3Jvb3QvLndjcC9hZ2VudHMvIHNvIGFueSBvZiB0aGVtCmNhbiBgc3lzLnBhdGguaW5zZXJ0KDAsIFdDUF9ESVIgKyAiL2FnZW50cyIpOyBpbXBvcnQgd2NwX2FnZW50X2xpYmAuCiIiIgoKaW1wb3J0IGpzb24KaW1wb3J0IG9zCmltcG9ydCBzdWJwcm9jZXNzCgoKZGVmIHNoZWxsX3F1b3RlKHMpOgogICAgIiIiU2luZ2xlLXF1b3RlIGBzYCBmb3Igc2FmZSBpbnRlcnBvbGF0aW9uIGludG8gYSBzaGVsbCBjb21tYW5kIHN0cmluZy4iIiIKICAgIHJldHVybiAiJyIgKyBzdHIocykucmVwbGFjZSgiJyIsICInXFwnJyIpICsgIiciCgoKZGVmIHJ1bihjbWQsIHRpbWVvdXQ9NjApOgogICAgIiIiUnVuIGBjbWRgIChhIHNoZWxsIHN0cmluZykgYW5kIHJldHVybiB0aGUgY29tcGxldGVkIHByb2Nlc3MuIiIiCiAgICByZXR1cm4gc3VicHJvY2Vzcy5ydW4oY21kLCBzaGVsbD1UcnVlLCBjYXB0dXJlX291dHB1dD1UcnVlLCB0ZXh0PVRydWUsIHRpbWVvdXQ9dGltZW91dCkKCgpkZWYgcnVuX2FyZ3YoYXJndiwgdGltZW91dD02MCk6CiAgICAiIiJSdW4gYGFyZ3ZgIChhIGxpc3QsIG5vIHNoZWxsKSBhbmQgcmV0dXJuIHRoZSBjb21wbGV0ZWQgcHJvY2Vzcy4KCiAgICBVc2UgdGhpcyBpbnN0ZWFkIG9mIGBydW5gIGZvciBhbnkgYXJndW1lbnQgdGhhdCBpc24ndCBmdWxseSB0cnVzdGVkCiAgICAoZS5nLiBhIGZpbGVuYW1lIHJlcG9ydGVkIGJ5IGEgY29udGFpbmVyKSAtIHRoZXJlJ3Mgbm8gc2hlbGwgdG8KICAgIHJlLXBhcnNlIGl0LCBzbyBpdCBjYW4ndCBicmVhayBvdXQgb2YgcXVvdGluZy4KICAgICIiIgogICAgcmV0dXJuIHN1YnByb2Nlc3MucnVuKGFyZ3YsIGNhcHR1cmVfb3V0cHV0PVRydWUsIHRleHQ9VHJ1ZSwgdGltZW91dD10aW1lb3V0KQoKCmRlZiByZWFkX2pzb24ocGF0aCwgZGVmYXVsdD1Ob25lKToKICAgICIiIkxvYWQgSlNPTiBmcm9tIGBwYXRoYCwgcmV0dXJuaW5nIGBkZWZhdWx0YCBpZiBtaXNzaW5nL3VucmVhZGFibGUuIiIiCiAgICB0cnk6CiAgICAgICAgd2l0aCBvcGVuKHBhdGgpIGFzIGY6CiAgICAgICAgICAgIHJldHVybiBqc29uLmxvYWQoZikKICAgIGV4Y2VwdCBFeGNlcHRpb246CiAgICAgICAgcmV0dXJuIGRlZmF1bHQKCgpkZWYgd3JpdGVfanNvbihwYXRoLCBkYXRhKToKICAgICIiIldyaXRlIGBkYXRhYCBhcyBKU09OIHRvIGBwYXRoYCwgY3JlYXRpbmcgcGFyZW50IGRpcmVjdG9yaWVzIGFzIG5lZWRlZC4iIiIKICAgIG9zLm1ha2VkaXJzKG9zLnBhdGguZGlybmFtZShwYXRoKSwgZXhpc3Rfb2s9VHJ1ZSkKICAgIHdpdGggb3BlbihwYXRoLCAidyIpIGFzIGY6CiAgICAgICAganNvbi5kdW1wKGRhdGEsIGYpCgoKZGVmIGxvZ19lbnRyeShsb2dfZmlsZSwgZW50cnksIG1heF9saW5lcz0yMDAwKToKICAgICIiIkFwcGVuZCBgZW50cnlgIGFzIGEgSlNPTiBsaW5lIHRvIGBsb2dfZmlsZWAsIHRyaW1taW5nIHRvIGBtYXhfbGluZXNgLiIiIgogICAgb3MubWFrZWRpcnMob3MucGF0aC5kaXJuYW1lKGxvZ19maWxlKSwgZXhpc3Rfb2s9VHJ1ZSkKICAgIHdpdGggb3Blbihsb2dfZmlsZSwgImEiKSBhcyBmOgogICAgICAgIGYud3JpdGUoanNvbi5kdW1wcyhlbnRyeSwgc2VwYXJhdG9ycz0oIiwiLCAiOiIpKSArICJcbiIpCiAgICB0cnk6CiAgICAgICAgd2l0aCBvcGVuKGxvZ19maWxlKSBhcyBmOgogICAgICAgICAgICBsaW5lcyA9IGYucmVhZGxpbmVzKCkKICAgICAgICBpZiBsZW4obGluZXMpID4gbWF4X2xpbmVzOgogICAgICAgICAgICB3aXRoIG9wZW4obG9nX2ZpbGUsICJ3IikgYXMgZjoKICAgICAgICAgICAgICAgIGYud3JpdGVsaW5lcyhsaW5lc1stbWF4X2xpbmVzOl0pCiAgICBleGNlcHQgRXhjZXB0aW9uOgogICAgICAgIHBhc3MKCgpkZWYgX3NoX2VzYyhzKToKICAgIHJldHVybiAiJyIgKyBzdHIocykucmVwbGFjZSgiJyIsICInXFwnJyIpICsgIiciCgoKZGVmIG5vdGlmeShub3RpZnlfZmlsZSwgaXNfZmFpbHVyZSwgaXNfc3VjY2Vzcywgc3VtbWFyeV90ZXh0LCBldmVudF9wYXlsb2FkLCB1c2VybmFtZT0iV0NQIEFnZW50Iik6CiAgICAiIiJTZW5kIGBzdW1tYXJ5X3RleHRgL2BldmVudF9wYXlsb2FkYCB0byBldmVyeSBlbmFibGVkIGNoYW5uZWwgaW4KICAgIGBub3RpZnlfZmlsZWAgd2hvc2Ugb25fZmFpbHVyZS9vbl9zdWNjZXNzIGdhdGUgbWF0Y2hlcy4gYGlzX2ZhaWx1cmVgIGFuZAogICAgYGlzX3N1Y2Nlc3NgIGFyZSBpbmRlcGVuZGVudCAobm90IGFzc3VtZWQgY29tcGxlbWVudGFyeSkgc28gY2FsbGVycyB3aG9zZQogICAgb3V0Y29tZSBjYW4gYmUgbmVpdGhlciAoZS5nLiBub3RoaW5nIHRvIGRvKSBzaW1wbHkgcGFzcyBib3RoIEZhbHNlLgogICAgIiIiCiAgICB0cnk6CiAgICAgICAgd2l0aCBvcGVuKG5vdGlmeV9maWxlKSBhcyBmOgogICAgICAgICAgICBub3RpZnlfY29uZiA9IGpzb24ubG9hZChmKQogICAgZXhjZXB0IEV4Y2VwdGlvbjoKICAgICAgICByZXR1cm4KCiAgICBmb3IgY2ggaW4gbm90aWZ5X2NvbmYuZ2V0KCJjaGFubmVscyIsIFtdKToKICAgICAgICBpZiBub3QgY2guZ2V0KCJlbmFibGVkIiwgVHJ1ZSk6CiAgICAgICAgICAgIGNvbnRpbnVlCiAgICAgICAgc2VuZCA9IChpc19mYWlsdXJlIGFuZCBjaC5nZXQoIm9uX2ZhaWx1cmUiLCBUcnVlKSkgb3IgXAogICAgICAgICAgICAgICAoaXNfc3VjY2VzcyBhbmQgY2guZ2V0KCJvbl9zdWNjZXNzIiwgRmFsc2UpKQogICAgICAgIGlmIG5vdCBzZW5kOgogICAgICAgICAgICBjb250aW51ZQoKICAgICAgICB3ZWJob29rX3VybCA9IGNoLmdldCgid2ViaG9va191cmwiLCAiIikKICAgICAgICBpZiBub3Qgd2ViaG9va191cmw6CiAgICAgICAgICAgIGNvbnRpbnVlCgogICAgICAgIGNoX3R5cGUgPSBjaC5nZXQoInR5cGUiLCAid2ViaG9vayIpCiAgICAgICAgaWYgY2hfdHlwZSA9PSAic2xhY2siOgogICAgICAgICAgICBib2R5ID0ganNvbi5kdW1wcyh7InRleHQiOiBzdW1tYXJ5X3RleHQsICJ1c2VybmFtZSI6IHVzZXJuYW1lfSkKICAgICAgICBlbGlmIGNoX3R5cGUgPT0gImRpc2NvcmQiOgogICAgICAgICAgICBib2R5ID0ganNvbi5kdW1wcyh7ImNvbnRlbnQiOiBzdW1tYXJ5X3RleHR9KQogICAgICAgIGVsc2U6CiAgICAgICAgICAgIGJvZHkgPSBqc29uLmR1bXBzKGV2ZW50X3BheWxvYWQpCgogICAgICAgIHN1YnByb2Nlc3MucnVuKAogICAgICAgICAgICBmImN1cmwgLXMgLW0gMTAgLVggUE9TVCAtSCAnQ29udGVudC1UeXBlOiBhcHBsaWNhdGlvbi9qc29uJyAiCiAgICAgICAgICAgIGYiLWQge19zaF9lc2MoYm9keSl9IHtfc2hfZXNjKHdlYmhvb2tfdXJsKX0iLAogICAgICAgICAgICBzaGVsbD1UcnVlLAogICAgICAgICAgICBjYXB0dXJlX291dHB1dD1UcnVlLAogICAgICAgICAgICB0ZXh0PVRydWUsCiAgICAgICAgICAgIHRpbWVvdXQ9MzAsCiAgICAgICAgKQoKCmRlZiBkYXRhYmFzZV9kdW1wX2NvbW1hbmQoZGJfdHlwZSwgY29udGFpbmVyLCBwYXNzd29yZCwgZGF0YWJhc2UpOgogICAgIiIiU3RhYmxlIFNRTCBvdXRwdXQgYWxsb3dzIGEgcmVzdG9yZSBkcmlsbCB0byBjb21wYXJlIHRoZSBhY3R1YWwgc25hcHNob3QuIiIiCiAgICB0YXJnZXQgPSBzaGVsbF9xdW90ZShjb250YWluZXIpCiAgICBzZWNyZXQgPSBzaGVsbF9xdW90ZShwYXNzd29yZCkKICAgIG5hbWUgPSBzaGVsbF9xdW90ZShkYXRhYmFzZSkKICAgIGlmIGRiX3R5cGUgPT0gIm1hcmlhZGIiOgogICAgICAgIHJldHVybiAoZiJkb2NrZXIgZXhlYyAtZSBNWVNRTF9QV0Q9e3NlY3JldH0ge3RhcmdldH0gbWFyaWFkYi1kdW1wIC11cm9vdCAiCiAgICAgICAgICAgICAgICBmIi0tc2luZ2xlLXRyYW5zYWN0aW9uIC0tcm91dGluZXMgLS10cmlnZ2VycyAtLXNraXAtY29tbWVudHMgIgogICAgICAgICAgICAgICAgZiItLXNraXAtZXh0ZW5kZWQtaW5zZXJ0IC0tb3JkZXItYnktcHJpbWFyeSAtLXNraXAtYWRkLWxvY2tzIC0tc2tpcC1kaXNhYmxlLWtleXMge25hbWV9IikKICAgIGlmIGRiX3R5cGUgPT0gInBvc3RncmVzIjoKICAgICAgICByZXR1cm4gKGYiZG9ja2VyIGV4ZWMgLWUgUEdQQVNTV09SRD17c2VjcmV0fSB7dGFyZ2V0fSBwZ19kdW1wIC1VIHBvc3RncmVzICIKICAgICAgICAgICAgICAgIGYiLS1uby1vd25lciAtLW5vLXByaXZpbGVnZXMgLS1pbnNlcnRzIC0tcm93cy1wZXItaW5zZXJ0PTEge25hbWV9IikKICAgIHJhaXNlIFZhbHVlRXJyb3IoIlVuc3VwcG9ydGVkIGRhdGFiYXNlIHR5cGUiKQoKCmRlZiBzcWxfZGlnZXN0KHN0cmVhbSk6CiAgICAiIiJJZ25vcmUgb25seSBwZ19kdW1wJ3MgcmFuZG9tIHBzcWwgZ3VhcmQgdG9rZW4sIHdoaWNoIGlzIG5vdCBTUUwgZGF0YS4iIiIKICAgIGltcG9ydCBoYXNobGliCiAgICBpbXBvcnQgcmUKICAgIGRpZ2VzdCA9IGhhc2hsaWIuc2hhMjU2KCkKICAgIHNpemUgPSAwCiAgICBmb3IgbGluZSBpbiBzdHJlYW06CiAgICAgICAgaWYgcmUuZnVsbG1hdGNoKHJiIlxcKD86dW4pP3Jlc3RyaWN0IFtBLVphLXowLTldK1xyP1xuPyIsIGxpbmUpOgogICAgICAgICAgICBjb250aW51ZQogICAgICAgIGRpZ2VzdC51cGRhdGUobGluZSkKICAgICAgICBzaXplICs9IGxlbihsaW5lKQogICAgaWYgc2l6ZSA9PSAwOgogICAgICAgIHJhaXNlIFZhbHVlRXJyb3IoIkVtcHR5IFNRTCBkdW1wIikKICAgIHJldHVybiBkaWdlc3QuaGV4ZGlnZXN0KCkK'), _agent_lib.__dict__)
sys.modules['wcp_agent_lib'] = _agent_lib
from wcp_agent_lib import run

os.makedirs(METRICS_DIR, exist_ok=True)


def run_cmd(cmd, timeout=10):
    return run(cmd, timeout=timeout)


# ── CPU (via /proc/stat diff over 0.5s) ─────────────────────────────────────

def read_cpu_stat():
    with open(PROC_STAT) as f:
        for line in f:
            if line.startswith("cpu "):
                fields = line.split()
                nums = [int(x) for x in fields[1:8]] + [0] * max(0, 7 - (len(fields) - 1))
                total = sum(nums[:7])
                idle = nums[3] + nums[4]
                return total, idle
    return 0, 0


t1, i1 = read_cpu_stat()
time.sleep(0.5)
t2, i2 = read_cpu_stat()
dt = t2 - t1
di = i2 - i1
cpu = round((dt - di) * 100 / dt, 1) if dt > 0 else 0.0

# ── RAM (MB) ─────────────────────────────────────────────────────────────────

free_out = run_cmd("free -m").stdout.splitlines()
ram_used, ram_total = 0, 0
if len(free_out) > 1:
    parts = free_out[1].split()
    ram_used = int(parts[2])
    ram_total = int(parts[1])

# ── Disk / (GB) ───────────────────────────────────────────────────────────────

df_out = run_cmd("df -BG /").stdout.splitlines()
disk_used, disk_total = 0, 0
if len(df_out) > 1:
    parts = df_out[1].split()
    disk_used = int(re.sub(r"[^0-9]", "", parts[2]))
    disk_total = int(re.sub(r"[^0-9]", "", parts[1]))

# ── Load ──────────────────────────────────────────────────────────────────────

with open(PROC_LOADAVG) as f:
    load_fields = f.read().split()
load1, load5, load15 = float(load_fields[0]), float(load_fields[1]), float(load_fields[2])

# ── Net (cumulative bytes since boot) ────────────────────────────────────────

net_rx = net_tx = 0
with open(PROC_NET_DEV) as f:
    lines = f.readlines()
for line in lines[2:]:
    parts = line.split()
    if len(parts) < 10:
        continue
    net_rx += int(parts[1])
    net_tx += int(parts[9])

# ── Containers ────────────────────────────────────────────────────────────────

def container_count(argv):
    r = run_cmd(argv)
    if r.returncode != 0:
        return 0
    return len([l for l in r.stdout.splitlines() if l.strip()])


containers_running = container_count("docker ps -q 2>/dev/null")
containers_total = container_count("docker ps -aq 2>/dev/null")
containers_stopped = containers_total - containers_running

# ── Uptime (seconds) ──────────────────────────────────────────────────────────

with open(PROC_UPTIME) as f:
    uptime_sec = int(float(f.read().split()[0]))

# ── Write ─────────────────────────────────────────────────────────────────────

ts = int(time.time())
month = time.strftime("%Y-%m")
out_file = os.path.join(METRICS_DIR, f"metrics-{month}.jsonl")

entry = {
    "ts": ts, "cpu": cpu, "ru": ram_used, "rt": ram_total,
    "du": disk_used, "dt": disk_total, "l1": load1, "l5": load5, "l15": load15,
    "rx": net_rx, "tx": net_tx, "cr": containers_running, "cs": containers_stopped,
    "up": uptime_sec,
}
with open(out_file, "a") as f:
    f.write(json.dumps(entry, separators=(",", ":")) + "\n")

# ── Rotate: keep only last 3 months ──────────────────────────────────────────

now = time.localtime()
cutoff_year, cutoff_month = now.tm_year, now.tm_mon - 3
while cutoff_month <= 0:
    cutoff_month += 12
    cutoff_year -= 1
cutoff = f"{cutoff_year:04d}-{cutoff_month:02d}"

for f in glob.glob(os.path.join(METRICS_DIR, "metrics-*.jsonl")):
    m = re.search(r"metrics-(.*)\.jsonl$", os.path.basename(f))
    if m and m.group(1) < cutoff:
        os.remove(f)
PYEOF
