#!/usr/bin/env bash
# wcp-agent: bruteforce-guard
# wcp-agent-version: 2.0.0
# wcp-agent-description: Fail2Ban-style brute-force protection for wp-login and general Caddy abuse. Bans in the ingress Caddy (a `remote_ip` block every route imports) instead of iptables, since containerized (Docker bridge + published-port) traffic is NAT'd through DOCKER-USER/FORWARD, not INPUT, so classic fail2ban bans never actually apply. The ban list is applied by the Operations Engine (`ingress.applyBans`), never by editing route files.
#
# Run modes:
#   bash bruteforce-guard.sh              - scan logs, ban offenders, expire old bans, apply
#   bash bruteforce-guard.sh --apply-only - skip detection, just re-apply current ban list
#                                            (used by the app for immediate manual unban)
#
# Exit status: 0 when the ban list is live (or the engine was busy and the next
# run will retry), 2 when the engine could not apply it (unreachable, validation
# or reload refused). The ban list file is never changed because of that.

set -euo pipefail

WCP_DIR="${WCP_DIR:-/root/.wcp}"
export WCP_DIR
trap 'ec=$?; mkdir -p "$WCP_DIR/agents"; echo "{\"ts\":$(date +%s),\"exit_code\":$ec}" > "$WCP_DIR/agents/bruteforce-guard.heartbeat"' EXIT

LOG_GLOB="${LOG_GLOB:-/var/log/caddy/ingress-*.log}"
export LOG_GLOB

APPLY_ONLY=0
[[ "${1:-}" == "--apply-only" ]] && APPLY_ONLY=1
export APPLY_ONLY

python3 - << 'PYEOF'
import glob, json, os, re, sys, time, uuid

WCP_DIR        = os.environ.get("WCP_DIR", "/root/.wcp")
LOG_GLOB       = os.environ["LOG_GLOB"]
ENGINE         = os.environ.get("OPS_ENGINE", "ops-engine")
APPLY_ONLY     = os.environ.get("APPLY_ONLY", "0") == "1"

# bruteforce-active-bans.txt and bruteforce-config.env are NOT owned by this
# script alone - src-tauri/src/commands/bruteforce.rs reads/writes them
# directly (bans.txt via a plain read + a remote `grep -v` unban command;
# config.env as the KEY=VALUE file it writes from bruteforce:config:set), so
# their on-disk formats must stay exactly as they are. Only the offsets file
# is purely internal to this script.
CONFIG_FILE  = os.path.join(WCP_DIR, "bruteforce-config.env")
OFFSETS_FILE = os.path.join(WCP_DIR, "bruteforce-offsets.json")
HITS_FILE    = os.path.join(WCP_DIR, "bruteforce-hits.json")
BANS_FILE    = os.path.join(WCP_DIR, "bruteforce-active-bans.txt")
EVENTS_FILE  = os.path.join(WCP_DIR, "bruteforce-events.jsonl")
# The ban list the engine last accepted (one IP per line); internal to this script.
APPLIED_FILE = os.path.join(WCP_DIR, "bruteforce-applied.txt")
REQUEST_FILE = os.path.join(WCP_DIR, "bruteforce-apply-request.json")
NOTIFY_FILE  = os.path.join(WCP_DIR, "notify.conf")

sys.path.insert(0, os.path.join(WCP_DIR, "agents"))
import base64, types
_agent_lib = types.ModuleType('wcp_agent_lib')
exec(base64.b64decode('IiIiU2hhcmVkIGhlbHBlcnMgZm9yIFdDUCBidW5kbGVkIGFnZW50IHNjcmlwdHMuCgpVcGxvYWRlZCBhbG9uZ3NpZGUgZXZlcnkgYWdlbnQgc2NyaXB0IGludG8gL3Jvb3QvLndjcC9hZ2VudHMvIHNvIGFueSBvZiB0aGVtCmNhbiBgc3lzLnBhdGguaW5zZXJ0KDAsIFdDUF9ESVIgKyAiL2FnZW50cyIpOyBpbXBvcnQgd2NwX2FnZW50X2xpYmAuCiIiIgoKaW1wb3J0IGpzb24KaW1wb3J0IG9zCmltcG9ydCBzdWJwcm9jZXNzCgoKZGVmIHNoZWxsX3F1b3RlKHMpOgogICAgIiIiU2luZ2xlLXF1b3RlIGBzYCBmb3Igc2FmZSBpbnRlcnBvbGF0aW9uIGludG8gYSBzaGVsbCBjb21tYW5kIHN0cmluZy4iIiIKICAgIHJldHVybiAiJyIgKyBzdHIocykucmVwbGFjZSgiJyIsICInXFwnJyIpICsgIiciCgoKZGVmIHJ1bihjbWQsIHRpbWVvdXQ9NjApOgogICAgIiIiUnVuIGBjbWRgIChhIHNoZWxsIHN0cmluZykgYW5kIHJldHVybiB0aGUgY29tcGxldGVkIHByb2Nlc3MuIiIiCiAgICByZXR1cm4gc3VicHJvY2Vzcy5ydW4oY21kLCBzaGVsbD1UcnVlLCBjYXB0dXJlX291dHB1dD1UcnVlLCB0ZXh0PVRydWUsIHRpbWVvdXQ9dGltZW91dCkKCgpkZWYgcnVuX2FyZ3YoYXJndiwgdGltZW91dD02MCk6CiAgICAiIiJSdW4gYGFyZ3ZgIChhIGxpc3QsIG5vIHNoZWxsKSBhbmQgcmV0dXJuIHRoZSBjb21wbGV0ZWQgcHJvY2Vzcy4KCiAgICBVc2UgdGhpcyBpbnN0ZWFkIG9mIGBydW5gIGZvciBhbnkgYXJndW1lbnQgdGhhdCBpc24ndCBmdWxseSB0cnVzdGVkCiAgICAoZS5nLiBhIGZpbGVuYW1lIHJlcG9ydGVkIGJ5IGEgY29udGFpbmVyKSAtIHRoZXJlJ3Mgbm8gc2hlbGwgdG8KICAgIHJlLXBhcnNlIGl0LCBzbyBpdCBjYW4ndCBicmVhayBvdXQgb2YgcXVvdGluZy4KICAgICIiIgogICAgcmV0dXJuIHN1YnByb2Nlc3MucnVuKGFyZ3YsIGNhcHR1cmVfb3V0cHV0PVRydWUsIHRleHQ9VHJ1ZSwgdGltZW91dD10aW1lb3V0KQoKCmRlZiByZWFkX2pzb24ocGF0aCwgZGVmYXVsdD1Ob25lKToKICAgICIiIkxvYWQgSlNPTiBmcm9tIGBwYXRoYCwgcmV0dXJuaW5nIGBkZWZhdWx0YCBpZiBtaXNzaW5nL3VucmVhZGFibGUuIiIiCiAgICB0cnk6CiAgICAgICAgd2l0aCBvcGVuKHBhdGgpIGFzIGY6CiAgICAgICAgICAgIHJldHVybiBqc29uLmxvYWQoZikKICAgIGV4Y2VwdCBFeGNlcHRpb246CiAgICAgICAgcmV0dXJuIGRlZmF1bHQKCgpkZWYgd3JpdGVfanNvbihwYXRoLCBkYXRhKToKICAgICIiIldyaXRlIGBkYXRhYCBhcyBKU09OIHRvIGBwYXRoYCwgY3JlYXRpbmcgcGFyZW50IGRpcmVjdG9yaWVzIGFzIG5lZWRlZC4iIiIKICAgIG9zLm1ha2VkaXJzKG9zLnBhdGguZGlybmFtZShwYXRoKSwgZXhpc3Rfb2s9VHJ1ZSkKICAgIHdpdGggb3BlbihwYXRoLCAidyIpIGFzIGY6CiAgICAgICAganNvbi5kdW1wKGRhdGEsIGYpCgoKZGVmIGxvZ19lbnRyeShsb2dfZmlsZSwgZW50cnksIG1heF9saW5lcz0yMDAwKToKICAgICIiIkFwcGVuZCBgZW50cnlgIGFzIGEgSlNPTiBsaW5lIHRvIGBsb2dfZmlsZWAsIHRyaW1taW5nIHRvIGBtYXhfbGluZXNgLiIiIgogICAgb3MubWFrZWRpcnMob3MucGF0aC5kaXJuYW1lKGxvZ19maWxlKSwgZXhpc3Rfb2s9VHJ1ZSkKICAgIHdpdGggb3Blbihsb2dfZmlsZSwgImEiKSBhcyBmOgogICAgICAgIGYud3JpdGUoanNvbi5kdW1wcyhlbnRyeSwgc2VwYXJhdG9ycz0oIiwiLCAiOiIpKSArICJcbiIpCiAgICB0cnk6CiAgICAgICAgd2l0aCBvcGVuKGxvZ19maWxlKSBhcyBmOgogICAgICAgICAgICBsaW5lcyA9IGYucmVhZGxpbmVzKCkKICAgICAgICBpZiBsZW4obGluZXMpID4gbWF4X2xpbmVzOgogICAgICAgICAgICB3aXRoIG9wZW4obG9nX2ZpbGUsICJ3IikgYXMgZjoKICAgICAgICAgICAgICAgIGYud3JpdGVsaW5lcyhsaW5lc1stbWF4X2xpbmVzOl0pCiAgICBleGNlcHQgRXhjZXB0aW9uOgogICAgICAgIHBhc3MKCgpkZWYgX3NoX2VzYyhzKToKICAgIHJldHVybiAiJyIgKyBzdHIocykucmVwbGFjZSgiJyIsICInXFwnJyIpICsgIiciCgoKZGVmIG5vdGlmeShub3RpZnlfZmlsZSwgaXNfZmFpbHVyZSwgaXNfc3VjY2Vzcywgc3VtbWFyeV90ZXh0LCBldmVudF9wYXlsb2FkLCB1c2VybmFtZT0iV0NQIEFnZW50Iik6CiAgICAiIiJTZW5kIGBzdW1tYXJ5X3RleHRgL2BldmVudF9wYXlsb2FkYCB0byBldmVyeSBlbmFibGVkIGNoYW5uZWwgaW4KICAgIGBub3RpZnlfZmlsZWAgd2hvc2Ugb25fZmFpbHVyZS9vbl9zdWNjZXNzIGdhdGUgbWF0Y2hlcy4gYGlzX2ZhaWx1cmVgIGFuZAogICAgYGlzX3N1Y2Nlc3NgIGFyZSBpbmRlcGVuZGVudCAobm90IGFzc3VtZWQgY29tcGxlbWVudGFyeSkgc28gY2FsbGVycyB3aG9zZQogICAgb3V0Y29tZSBjYW4gYmUgbmVpdGhlciAoZS5nLiBub3RoaW5nIHRvIGRvKSBzaW1wbHkgcGFzcyBib3RoIEZhbHNlLgogICAgIiIiCiAgICB0cnk6CiAgICAgICAgd2l0aCBvcGVuKG5vdGlmeV9maWxlKSBhcyBmOgogICAgICAgICAgICBub3RpZnlfY29uZiA9IGpzb24ubG9hZChmKQogICAgZXhjZXB0IEV4Y2VwdGlvbjoKICAgICAgICByZXR1cm4KCiAgICBmb3IgY2ggaW4gbm90aWZ5X2NvbmYuZ2V0KCJjaGFubmVscyIsIFtdKToKICAgICAgICBpZiBub3QgY2guZ2V0KCJlbmFibGVkIiwgVHJ1ZSk6CiAgICAgICAgICAgIGNvbnRpbnVlCiAgICAgICAgc2VuZCA9IChpc19mYWlsdXJlIGFuZCBjaC5nZXQoIm9uX2ZhaWx1cmUiLCBUcnVlKSkgb3IgXAogICAgICAgICAgICAgICAoaXNfc3VjY2VzcyBhbmQgY2guZ2V0KCJvbl9zdWNjZXNzIiwgRmFsc2UpKQogICAgICAgIGlmIG5vdCBzZW5kOgogICAgICAgICAgICBjb250aW51ZQoKICAgICAgICB3ZWJob29rX3VybCA9IGNoLmdldCgid2ViaG9va191cmwiLCAiIikKICAgICAgICBpZiBub3Qgd2ViaG9va191cmw6CiAgICAgICAgICAgIGNvbnRpbnVlCgogICAgICAgIGNoX3R5cGUgPSBjaC5nZXQoInR5cGUiLCAid2ViaG9vayIpCiAgICAgICAgaWYgY2hfdHlwZSA9PSAic2xhY2siOgogICAgICAgICAgICBib2R5ID0ganNvbi5kdW1wcyh7InRleHQiOiBzdW1tYXJ5X3RleHQsICJ1c2VybmFtZSI6IHVzZXJuYW1lfSkKICAgICAgICBlbGlmIGNoX3R5cGUgPT0gImRpc2NvcmQiOgogICAgICAgICAgICBib2R5ID0ganNvbi5kdW1wcyh7ImNvbnRlbnQiOiBzdW1tYXJ5X3RleHR9KQogICAgICAgIGVsc2U6CiAgICAgICAgICAgIGJvZHkgPSBqc29uLmR1bXBzKGV2ZW50X3BheWxvYWQpCgogICAgICAgIHN1YnByb2Nlc3MucnVuKAogICAgICAgICAgICBmImN1cmwgLXMgLW0gMTAgLVggUE9TVCAtSCAnQ29udGVudC1UeXBlOiBhcHBsaWNhdGlvbi9qc29uJyAiCiAgICAgICAgICAgIGYiLWQge19zaF9lc2MoYm9keSl9IHtfc2hfZXNjKHdlYmhvb2tfdXJsKX0iLAogICAgICAgICAgICBzaGVsbD1UcnVlLAogICAgICAgICAgICBjYXB0dXJlX291dHB1dD1UcnVlLAogICAgICAgICAgICB0ZXh0PVRydWUsCiAgICAgICAgICAgIHRpbWVvdXQ9MzAsCiAgICAgICAgKQoKCmRlZiBkYXRhYmFzZV9kdW1wX2NvbW1hbmQoZGJfdHlwZSwgY29udGFpbmVyLCBwYXNzd29yZCwgZGF0YWJhc2UpOgogICAgIiIiU3RhYmxlIFNRTCBvdXRwdXQgYWxsb3dzIGEgcmVzdG9yZSBkcmlsbCB0byBjb21wYXJlIHRoZSBhY3R1YWwgc25hcHNob3QuIiIiCiAgICB0YXJnZXQgPSBzaGVsbF9xdW90ZShjb250YWluZXIpCiAgICBzZWNyZXQgPSBzaGVsbF9xdW90ZShwYXNzd29yZCkKICAgIG5hbWUgPSBzaGVsbF9xdW90ZShkYXRhYmFzZSkKICAgIGlmIGRiX3R5cGUgPT0gIm1hcmlhZGIiOgogICAgICAgIHJldHVybiAoZiJkb2NrZXIgZXhlYyAtZSBNWVNRTF9QV0Q9e3NlY3JldH0ge3RhcmdldH0gbWFyaWFkYi1kdW1wIC11cm9vdCAiCiAgICAgICAgICAgICAgICBmIi0tc2luZ2xlLXRyYW5zYWN0aW9uIC0tcm91dGluZXMgLS10cmlnZ2VycyAtLXNraXAtY29tbWVudHMgIgogICAgICAgICAgICAgICAgZiItLXNraXAtZXh0ZW5kZWQtaW5zZXJ0IC0tb3JkZXItYnktcHJpbWFyeSAtLXNraXAtYWRkLWxvY2tzIC0tc2tpcC1kaXNhYmxlLWtleXMge25hbWV9IikKICAgIGlmIGRiX3R5cGUgPT0gInBvc3RncmVzIjoKICAgICAgICByZXR1cm4gKGYiZG9ja2VyIGV4ZWMgLWUgUEdQQVNTV09SRD17c2VjcmV0fSB7dGFyZ2V0fSBwZ19kdW1wIC1VIHBvc3RncmVzICIKICAgICAgICAgICAgICAgIGYiLS1uby1vd25lciAtLW5vLXByaXZpbGVnZXMgLS1pbnNlcnRzIC0tcm93cy1wZXItaW5zZXJ0PTEge25hbWV9IikKICAgIHJhaXNlIFZhbHVlRXJyb3IoIlVuc3VwcG9ydGVkIGRhdGFiYXNlIHR5cGUiKQoKCmRlZiBzcWxfZGlnZXN0KHN0cmVhbSk6CiAgICAiIiJJZ25vcmUgb25seSBwZ19kdW1wJ3MgcmFuZG9tIHBzcWwgZ3VhcmQgdG9rZW4sIHdoaWNoIGlzIG5vdCBTUUwgZGF0YS4iIiIKICAgIGltcG9ydCBoYXNobGliCiAgICBpbXBvcnQgcmUKICAgIGRpZ2VzdCA9IGhhc2hsaWIuc2hhMjU2KCkKICAgIHNpemUgPSAwCiAgICBmb3IgbGluZSBpbiBzdHJlYW06CiAgICAgICAgaWYgcmUuZnVsbG1hdGNoKHJiIlxcKD86dW4pP3Jlc3RyaWN0IFtBLVphLXowLTldK1xyP1xuPyIsIGxpbmUpOgogICAgICAgICAgICBjb250aW51ZQogICAgICAgIGRpZ2VzdC51cGRhdGUobGluZSkKICAgICAgICBzaXplICs9IGxlbihsaW5lKQogICAgaWYgc2l6ZSA9PSAwOgogICAgICAgIHJhaXNlIFZhbHVlRXJyb3IoIkVtcHR5IFNRTCBkdW1wIikKICAgIHJldHVybiBkaWdlc3QuaGV4ZGlnZXN0KCkK'), _agent_lib.__dict__)
sys.modules['wcp_agent_lib'] = _agent_lib
from wcp_agent_lib import log_entry as _log_entry, notify as _notify, read_json, run_argv, write_json

os.makedirs(WCP_DIR, exist_ok=True)
open(BANS_FILE, "a").close()
open(EVENTS_FILE, "a").close()
if not os.path.exists(OFFSETS_FILE):
    write_json(OFFSETS_FILE, {})
if not os.path.exists(HITS_FILE):
    write_json(HITS_FILE, [])

now = int(time.time())

# ── Config (defaults, overridden by bruteforce-config.env if present) ───────

def read_env_file(path):
    kv = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                kv[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return kv


def get_bool(kv, key, default):
    v = kv.get(key)
    return default if v is None else v == "true"


def get_int(kv, key, default):
    try:
        return int(kv[key])
    except (KeyError, ValueError):
        return default


kv = read_env_file(CONFIG_FILE)
WP_LOGIN_ENABLED = get_bool(kv, "WP_LOGIN_ENABLED", True)
WP_LOGIN_MAX_RETRIES = get_int(kv, "WP_LOGIN_MAX_RETRIES", 5)
WP_LOGIN_FINDTIME_MIN = get_int(kv, "WP_LOGIN_FINDTIME_MIN", 10)
WP_LOGIN_BANTIME_MIN = get_int(kv, "WP_LOGIN_BANTIME_MIN", 60)
CADDY_ENABLED = get_bool(kv, "CADDY_ENABLED", True)
CADDY_MAX_RETRIES = get_int(kv, "CADDY_MAX_RETRIES", 20)
CADDY_FINDTIME_MIN = get_int(kv, "CADDY_FINDTIME_MIN", 10)
CADDY_BANTIME_MIN = get_int(kv, "CADDY_BANTIME_MIN", 60)

IPV4_RE = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")
IPV6_CHARS_RE = re.compile(r"^[0-9a-fA-F:]+$")


def is_valid_ip(ip):
    # client_ip/remote_ip is normally the raw TCP peer address (never
    # attacker-controlled), but if trusted-proxies is ever misconfigured to
    # trust a header-spoofable upstream, it could reflect an arbitrary
    # X-Forwarded-For value instead. The engine validates the list again, but
    # anything that isn't a real IP is rejected before it reaches the
    # hits/bans files.
    m = IPV4_RE.match(ip)
    if m:
        return all(int(o) <= 255 for o in m.groups())
    return ":" in ip and bool(IPV6_CHARS_RE.match(ip))


def read_bans():
    bans = []
    with open(BANS_FILE) as f:
        for line in f:
            parts = line.split()
            if len(parts) == 4:
                bans.append({"ip": parts[0], "jail": parts[1],
                             "banned_at": int(parts[2]), "expires_at": int(parts[3])})
    return bans


def write_bans(bans):
    with open(BANS_FILE, "w") as f:
        for b in bans:
            f.write(f"{b['ip']} {b['jail']} {b['banned_at']} {b['expires_at']}\n")


def log_event(entry):
    _log_entry(EVENTS_FILE, entry)


bans = read_bans()
banned_ip_set = {b["ip"] for b in bans}


def ban_ip(ip, jail, banmin, hits):
    if ip in banned_ip_set:
        return
    expires = now + banmin * 60
    bans.append({"ip": ip, "jail": jail, "banned_at": now, "expires_at": expires})
    banned_ip_set.add(ip)
    write_bans(bans)
    log_event({"ts": now, "action": "ban", "ip": ip, "jail": jail, "hits": hits})
    _notify(
        NOTIFY_FILE,
        is_failure=True,
        is_success=False,
        summary_text=f"Banned {ip} ({jail} jail, {hits} hits)",
        event_payload={"event": "bruteforce_ban", "ts": now, "ip": ip, "jail": jail, "hits": hits},
        username="WCP Bruteforce Guard",
    )


if not APPLY_ONLY:
    # ── 1. Tail new ingress access-log lines ─────────────────────────────────
    # /var/log/caddy is a host bind mount of the ingress container, so the
    # logs are read directly (one ingress-<domain>.log per site).
    offsets = read_json(OFFSETS_FILE, {}) or {}
    hits = read_json(HITS_FILE, []) or []

    for logfile in sorted(glob.glob(LOG_GLOB)):
        key = logfile
        prev_offset = int(offsets.get(key, 0))
        try:
            cur_size = os.stat(logfile).st_size
        except OSError:
            continue

        if cur_size < prev_offset:
            prev_offset = 0

        if cur_size > prev_offset:
            try:
                with open(logfile, "rb") as f:
                    f.seek(prev_offset)
                    new_lines = f.read(cur_size - prev_offset).decode("utf-8", "replace")
            except OSError:
                continue
            # A partial last line is read again next time.
            if not new_lines.endswith("\n"):
                keep = new_lines.rfind("\n") + 1
                cur_size = prev_offset + len(new_lines[:keep].encode("utf-8"))
                new_lines = new_lines[:keep]
            for line in new_lines.splitlines():
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                req = entry.get("request", {}) if isinstance(entry, dict) else {}
                ip = req.get("client_ip") or req.get("remote_ip")
                if not ip or not is_valid_ip(ip):
                    continue
                method = req.get("method")
                uri = req.get("uri") or ""
                status = entry.get("status")

                if WP_LOGIN_ENABLED and method == "POST" and "wp-login.php" in uri:
                    hits.append({"ts": now, "jail": "wp_login", "ip": ip})
                elif CADDY_ENABLED and status in (401, 403, 404):
                    hits.append({"ts": now, "jail": "caddy", "ip": ip})

        offsets[key] = cur_size

    write_json(OFFSETS_FILE, offsets)

    # ── 2. Prune hits older than the widest configured find-time window ─────
    max_find_min = max(WP_LOGIN_FINDTIME_MIN, CADDY_FINDTIME_MIN)
    cutoff = now - max_find_min * 60
    hits = [h for h in hits if h["ts"] >= cutoff]
    write_json(HITS_FILE, hits)

    # ── 3. Evaluate thresholds per jail, ban offenders ───────────────────────
    def counts_for(jail, find_min):
        jail_cutoff = now - find_min * 60
        counts = {}
        for h in hits:
            if h["jail"] == jail and h["ts"] >= jail_cutoff:
                counts[h["ip"]] = counts.get(h["ip"], 0) + 1
        return counts

    if WP_LOGIN_ENABLED:
        for ip, count in counts_for("wp_login", WP_LOGIN_FINDTIME_MIN).items():
            if count >= WP_LOGIN_MAX_RETRIES:
                ban_ip(ip, "wp_login", WP_LOGIN_BANTIME_MIN, count)

    if CADDY_ENABLED:
        for ip, count in counts_for("caddy", CADDY_FINDTIME_MIN).items():
            if count >= CADDY_MAX_RETRIES:
                ban_ip(ip, "caddy", CADDY_BANTIME_MIN, count)

# ── 4. Expire old bans ────────────────────────────────────────────────────────
bans = read_bans()
active, expired = [], []
for b in bans:
    (active if b["expires_at"] >= now else expired).append(b)
for b in expired:
    log_event({"ts": now, "action": "expire", "ip": b["ip"], "jail": b["jail"]})
write_bans(active)

# ── 5. Hand the ban list to the Operations Engine ──────────────────────────
# The engine owns every route file and the ingress reload: it takes the stack
# lock, validates the new list, swaps it in, reloads Caddy and puts the old list
# back if the reload refuses it. This script never touches a Caddyfile.
banned_ips = sorted({b["ip"] for b in active})


def read_applied():
    try:
        with open(APPLIED_FILE) as f:
            return sorted({l.strip() for l in f if l.strip()})
    except FileNotFoundError:
        return None


def apply_failed(code, message):
    log_event({"ts": now, "action": "apply_failed", "code": code, "message": message[:200]})
    print(f"bruteforce-guard: ingress.applyBans failed: {code}: {message}", file=sys.stderr)
    sys.exit(2)


# Cron runs only call the engine when the list differs from the last accepted
# one; --apply-only (a manual unban) always does.
if APPLY_ONLY or read_applied() != banned_ips:
    with open(os.open(REQUEST_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump({"bans": banned_ips}, f)
    try:
        proc = run_argv([ENGINE, "ingress", "apply-bans",
                         "--request-file", REQUEST_FILE,
                         "--request-id", str(uuid.uuid4())], timeout=180)
    except (OSError, ValueError) as exc:
        apply_failed("ENGINE_UNAVAILABLE", str(exc))
    except Exception as exc:
        apply_failed("ENGINE_TIMEOUT", str(exc))
    try:
        response = json.loads(proc.stdout)
    except ValueError:
        apply_failed("ENGINE_BAD_RESPONSE", (proc.stderr or proc.stdout or "no output").strip())
    if response.get("ok"):
        with open(APPLIED_FILE, "w") as f:
            f.write("".join(f"{ip}\n" for ip in banned_ips))
    else:
        error = response.get("error") or {}
        if error.get("code") == "CONFLICT":
            # Another configuration change holds the lock: not a failure of
            # the ban list. The next run applies it.
            print("bruteforce-guard: ingress busy, will retry", file=sys.stderr)
        else:
            apply_failed(error.get("code") or "UNKNOWN", error.get("message") or "")
PYEOF
