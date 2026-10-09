#!/usr/bin/env bash
# wcp-agent: backup-restore-drill
# wcp-agent-version: 1.1.0
# wcp-agent-description: Restores the latest DB backup into a throwaway container and compares restored schema/data with the immutable snapshot

set -euo pipefail

WCP_DIR="${WCP_DIR:-/root/.wcp}"
export WCP_DIR
trap 'ec=$?; mkdir -p "$WCP_DIR/agents"; echo "{\"ts\":$(date +%s),\"exit_code\":$ec}" > "$WCP_DIR/agents/backup-restore-drill.heartbeat"' EXIT

python3 - << 'PYEOF'
import fcntl, gzip, hashlib, json, os, re, sys, tempfile, time
from pathlib import Path

WCP_DIR = Path(os.environ.get("WCP_DIR", "/root/.wcp"))
sys.path.insert(0, str(WCP_DIR / "agents"))
import base64, types
_agent_lib = types.ModuleType('wcp_agent_lib')
exec(base64.b64decode('IiIiU2hhcmVkIGhlbHBlcnMgZm9yIFdDUCBidW5kbGVkIGFnZW50IHNjcmlwdHMuCgpVcGxvYWRlZCBhbG9uZ3NpZGUgZXZlcnkgYWdlbnQgc2NyaXB0IGludG8gL3Jvb3QvLndjcC9hZ2VudHMvIHNvIGFueSBvZiB0aGVtCmNhbiBgc3lzLnBhdGguaW5zZXJ0KDAsIFdDUF9ESVIgKyAiL2FnZW50cyIpOyBpbXBvcnQgd2NwX2FnZW50X2xpYmAuCiIiIgoKaW1wb3J0IGpzb24KaW1wb3J0IG9zCmltcG9ydCBzdWJwcm9jZXNzCgoKZGVmIHNoZWxsX3F1b3RlKHMpOgogICAgIiIiU2luZ2xlLXF1b3RlIGBzYCBmb3Igc2FmZSBpbnRlcnBvbGF0aW9uIGludG8gYSBzaGVsbCBjb21tYW5kIHN0cmluZy4iIiIKICAgIHJldHVybiAiJyIgKyBzdHIocykucmVwbGFjZSgiJyIsICInXFwnJyIpICsgIiciCgoKZGVmIHJ1bihjbWQsIHRpbWVvdXQ9NjApOgogICAgIiIiUnVuIGBjbWRgIChhIHNoZWxsIHN0cmluZykgYW5kIHJldHVybiB0aGUgY29tcGxldGVkIHByb2Nlc3MuIiIiCiAgICByZXR1cm4gc3VicHJvY2Vzcy5ydW4oY21kLCBzaGVsbD1UcnVlLCBjYXB0dXJlX291dHB1dD1UcnVlLCB0ZXh0PVRydWUsIHRpbWVvdXQ9dGltZW91dCkKCgpkZWYgcnVuX2FyZ3YoYXJndiwgdGltZW91dD02MCk6CiAgICAiIiJSdW4gYGFyZ3ZgIChhIGxpc3QsIG5vIHNoZWxsKSBhbmQgcmV0dXJuIHRoZSBjb21wbGV0ZWQgcHJvY2Vzcy4KCiAgICBVc2UgdGhpcyBpbnN0ZWFkIG9mIGBydW5gIGZvciBhbnkgYXJndW1lbnQgdGhhdCBpc24ndCBmdWxseSB0cnVzdGVkCiAgICAoZS5nLiBhIGZpbGVuYW1lIHJlcG9ydGVkIGJ5IGEgY29udGFpbmVyKSAtIHRoZXJlJ3Mgbm8gc2hlbGwgdG8KICAgIHJlLXBhcnNlIGl0LCBzbyBpdCBjYW4ndCBicmVhayBvdXQgb2YgcXVvdGluZy4KICAgICIiIgogICAgcmV0dXJuIHN1YnByb2Nlc3MucnVuKGFyZ3YsIGNhcHR1cmVfb3V0cHV0PVRydWUsIHRleHQ9VHJ1ZSwgdGltZW91dD10aW1lb3V0KQoKCmRlZiByZWFkX2pzb24ocGF0aCwgZGVmYXVsdD1Ob25lKToKICAgICIiIkxvYWQgSlNPTiBmcm9tIGBwYXRoYCwgcmV0dXJuaW5nIGBkZWZhdWx0YCBpZiBtaXNzaW5nL3VucmVhZGFibGUuIiIiCiAgICB0cnk6CiAgICAgICAgd2l0aCBvcGVuKHBhdGgpIGFzIGY6CiAgICAgICAgICAgIHJldHVybiBqc29uLmxvYWQoZikKICAgIGV4Y2VwdCBFeGNlcHRpb246CiAgICAgICAgcmV0dXJuIGRlZmF1bHQKCgpkZWYgd3JpdGVfanNvbihwYXRoLCBkYXRhKToKICAgICIiIldyaXRlIGBkYXRhYCBhcyBKU09OIHRvIGBwYXRoYCwgY3JlYXRpbmcgcGFyZW50IGRpcmVjdG9yaWVzIGFzIG5lZWRlZC4iIiIKICAgIG9zLm1ha2VkaXJzKG9zLnBhdGguZGlybmFtZShwYXRoKSwgZXhpc3Rfb2s9VHJ1ZSkKICAgIHdpdGggb3BlbihwYXRoLCAidyIpIGFzIGY6CiAgICAgICAganNvbi5kdW1wKGRhdGEsIGYpCgoKZGVmIGxvZ19lbnRyeShsb2dfZmlsZSwgZW50cnksIG1heF9saW5lcz0yMDAwKToKICAgICIiIkFwcGVuZCBgZW50cnlgIGFzIGEgSlNPTiBsaW5lIHRvIGBsb2dfZmlsZWAsIHRyaW1taW5nIHRvIGBtYXhfbGluZXNgLiIiIgogICAgb3MubWFrZWRpcnMob3MucGF0aC5kaXJuYW1lKGxvZ19maWxlKSwgZXhpc3Rfb2s9VHJ1ZSkKICAgIHdpdGggb3Blbihsb2dfZmlsZSwgImEiKSBhcyBmOgogICAgICAgIGYud3JpdGUoanNvbi5kdW1wcyhlbnRyeSwgc2VwYXJhdG9ycz0oIiwiLCAiOiIpKSArICJcbiIpCiAgICB0cnk6CiAgICAgICAgd2l0aCBvcGVuKGxvZ19maWxlKSBhcyBmOgogICAgICAgICAgICBsaW5lcyA9IGYucmVhZGxpbmVzKCkKICAgICAgICBpZiBsZW4obGluZXMpID4gbWF4X2xpbmVzOgogICAgICAgICAgICB3aXRoIG9wZW4obG9nX2ZpbGUsICJ3IikgYXMgZjoKICAgICAgICAgICAgICAgIGYud3JpdGVsaW5lcyhsaW5lc1stbWF4X2xpbmVzOl0pCiAgICBleGNlcHQgRXhjZXB0aW9uOgogICAgICAgIHBhc3MKCgpkZWYgX3NoX2VzYyhzKToKICAgIHJldHVybiAiJyIgKyBzdHIocykucmVwbGFjZSgiJyIsICInXFwnJyIpICsgIiciCgoKZGVmIG5vdGlmeShub3RpZnlfZmlsZSwgaXNfZmFpbHVyZSwgaXNfc3VjY2Vzcywgc3VtbWFyeV90ZXh0LCBldmVudF9wYXlsb2FkLCB1c2VybmFtZT0iV0NQIEFnZW50Iik6CiAgICAiIiJTZW5kIGBzdW1tYXJ5X3RleHRgL2BldmVudF9wYXlsb2FkYCB0byBldmVyeSBlbmFibGVkIGNoYW5uZWwgaW4KICAgIGBub3RpZnlfZmlsZWAgd2hvc2Ugb25fZmFpbHVyZS9vbl9zdWNjZXNzIGdhdGUgbWF0Y2hlcy4gYGlzX2ZhaWx1cmVgIGFuZAogICAgYGlzX3N1Y2Nlc3NgIGFyZSBpbmRlcGVuZGVudCAobm90IGFzc3VtZWQgY29tcGxlbWVudGFyeSkgc28gY2FsbGVycyB3aG9zZQogICAgb3V0Y29tZSBjYW4gYmUgbmVpdGhlciAoZS5nLiBub3RoaW5nIHRvIGRvKSBzaW1wbHkgcGFzcyBib3RoIEZhbHNlLgogICAgIiIiCiAgICB0cnk6CiAgICAgICAgd2l0aCBvcGVuKG5vdGlmeV9maWxlKSBhcyBmOgogICAgICAgICAgICBub3RpZnlfY29uZiA9IGpzb24ubG9hZChmKQogICAgZXhjZXB0IEV4Y2VwdGlvbjoKICAgICAgICByZXR1cm4KCiAgICBmb3IgY2ggaW4gbm90aWZ5X2NvbmYuZ2V0KCJjaGFubmVscyIsIFtdKToKICAgICAgICBpZiBub3QgY2guZ2V0KCJlbmFibGVkIiwgVHJ1ZSk6CiAgICAgICAgICAgIGNvbnRpbnVlCiAgICAgICAgc2VuZCA9IChpc19mYWlsdXJlIGFuZCBjaC5nZXQoIm9uX2ZhaWx1cmUiLCBUcnVlKSkgb3IgXAogICAgICAgICAgICAgICAoaXNfc3VjY2VzcyBhbmQgY2guZ2V0KCJvbl9zdWNjZXNzIiwgRmFsc2UpKQogICAgICAgIGlmIG5vdCBzZW5kOgogICAgICAgICAgICBjb250aW51ZQoKICAgICAgICB3ZWJob29rX3VybCA9IGNoLmdldCgid2ViaG9va191cmwiLCAiIikKICAgICAgICBpZiBub3Qgd2ViaG9va191cmw6CiAgICAgICAgICAgIGNvbnRpbnVlCgogICAgICAgIGNoX3R5cGUgPSBjaC5nZXQoInR5cGUiLCAid2ViaG9vayIpCiAgICAgICAgaWYgY2hfdHlwZSA9PSAic2xhY2siOgogICAgICAgICAgICBib2R5ID0ganNvbi5kdW1wcyh7InRleHQiOiBzdW1tYXJ5X3RleHQsICJ1c2VybmFtZSI6IHVzZXJuYW1lfSkKICAgICAgICBlbGlmIGNoX3R5cGUgPT0gImRpc2NvcmQiOgogICAgICAgICAgICBib2R5ID0ganNvbi5kdW1wcyh7ImNvbnRlbnQiOiBzdW1tYXJ5X3RleHR9KQogICAgICAgIGVsc2U6CiAgICAgICAgICAgIGJvZHkgPSBqc29uLmR1bXBzKGV2ZW50X3BheWxvYWQpCgogICAgICAgIHN1YnByb2Nlc3MucnVuKAogICAgICAgICAgICBmImN1cmwgLXMgLW0gMTAgLVggUE9TVCAtSCAnQ29udGVudC1UeXBlOiBhcHBsaWNhdGlvbi9qc29uJyAiCiAgICAgICAgICAgIGYiLWQge19zaF9lc2MoYm9keSl9IHtfc2hfZXNjKHdlYmhvb2tfdXJsKX0iLAogICAgICAgICAgICBzaGVsbD1UcnVlLAogICAgICAgICAgICBjYXB0dXJlX291dHB1dD1UcnVlLAogICAgICAgICAgICB0ZXh0PVRydWUsCiAgICAgICAgICAgIHRpbWVvdXQ9MzAsCiAgICAgICAgKQoKCmRlZiBkYXRhYmFzZV9kdW1wX2NvbW1hbmQoZGJfdHlwZSwgY29udGFpbmVyLCBwYXNzd29yZCwgZGF0YWJhc2UpOgogICAgIiIiU3RhYmxlIFNRTCBvdXRwdXQgYWxsb3dzIGEgcmVzdG9yZSBkcmlsbCB0byBjb21wYXJlIHRoZSBhY3R1YWwgc25hcHNob3QuIiIiCiAgICB0YXJnZXQgPSBzaGVsbF9xdW90ZShjb250YWluZXIpCiAgICBzZWNyZXQgPSBzaGVsbF9xdW90ZShwYXNzd29yZCkKICAgIG5hbWUgPSBzaGVsbF9xdW90ZShkYXRhYmFzZSkKICAgIGlmIGRiX3R5cGUgPT0gIm1hcmlhZGIiOgogICAgICAgIHJldHVybiAoZiJkb2NrZXIgZXhlYyAtZSBNWVNRTF9QV0Q9e3NlY3JldH0ge3RhcmdldH0gbWFyaWFkYi1kdW1wIC11cm9vdCAiCiAgICAgICAgICAgICAgICBmIi0tc2luZ2xlLXRyYW5zYWN0aW9uIC0tcm91dGluZXMgLS10cmlnZ2VycyAtLXNraXAtY29tbWVudHMgIgogICAgICAgICAgICAgICAgZiItLXNraXAtZXh0ZW5kZWQtaW5zZXJ0IC0tb3JkZXItYnktcHJpbWFyeSAtLXNraXAtYWRkLWxvY2tzIC0tc2tpcC1kaXNhYmxlLWtleXMge25hbWV9IikKICAgIGlmIGRiX3R5cGUgPT0gInBvc3RncmVzIjoKICAgICAgICByZXR1cm4gKGYiZG9ja2VyIGV4ZWMgLWUgUEdQQVNTV09SRD17c2VjcmV0fSB7dGFyZ2V0fSBwZ19kdW1wIC1VIHBvc3RncmVzICIKICAgICAgICAgICAgICAgIGYiLS1uby1vd25lciAtLW5vLXByaXZpbGVnZXMgLS1pbnNlcnRzIC0tcm93cy1wZXItaW5zZXJ0PTEge25hbWV9IikKICAgIHJhaXNlIFZhbHVlRXJyb3IoIlVuc3VwcG9ydGVkIGRhdGFiYXNlIHR5cGUiKQoKCmRlZiBzcWxfZGlnZXN0KHN0cmVhbSk6CiAgICAiIiJJZ25vcmUgb25seSBwZ19kdW1wJ3MgcmFuZG9tIHBzcWwgZ3VhcmQgdG9rZW4sIHdoaWNoIGlzIG5vdCBTUUwgZGF0YS4iIiIKICAgIGltcG9ydCBoYXNobGliCiAgICBpbXBvcnQgcmUKICAgIGRpZ2VzdCA9IGhhc2hsaWIuc2hhMjU2KCkKICAgIHNpemUgPSAwCiAgICBmb3IgbGluZSBpbiBzdHJlYW06CiAgICAgICAgaWYgcmUuZnVsbG1hdGNoKHJiIlxcKD86dW4pP3Jlc3RyaWN0IFtBLVphLXowLTldK1xyP1xuPyIsIGxpbmUpOgogICAgICAgICAgICBjb250aW51ZQogICAgICAgIGRpZ2VzdC51cGRhdGUobGluZSkKICAgICAgICBzaXplICs9IGxlbihsaW5lKQogICAgaWYgc2l6ZSA9PSAwOgogICAgICAgIHJhaXNlIFZhbHVlRXJyb3IoIkVtcHR5IFNRTCBkdW1wIikKICAgIHJldHVybiBkaWdlc3QuaGV4ZGlnZXN0KCkK'), _agent_lib.__dict__)
sys.modules['wcp_agent_lib'] = _agent_lib
from wcp_agent_lib import log_entry, notify, run, shell_quote, database_dump_command, sql_digest

backup_dir = WCP_DIR / "backups"
backup_dir.mkdir(parents=True, exist_ok=True)
lock = open(backup_dir / ".lock", "a")
try:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    sys.exit("A backup or restore drill is already running")


def checked(command):
    result = run("bash -o pipefail -c " + shell_quote(command), timeout=1800)
    if result.returncode:
        raise RuntimeError(f"Restore verification command failed (exit {result.returncode})")
    return result.stdout.strip()


conf_path = WCP_DIR / "backup.conf"
if not conf_path.exists():
    sys.exit(0)
with open(conf_path) as f:
    jobs = [j for j in json.load(f).get("jobs", []) if j.get("job_type", "db") == "db" and j.get("enabled", True)]
if not jobs:
    sys.exit(0)
state_path = WCP_DIR / "restore-drill.state"
try:
    previous = json.loads(state_path.read_text())["last_index"]
except (OSError, ValueError, KeyError):
    previous = -1
index = (previous + 1) % len(jobs)
state_path.write_text(json.dumps({"last_index": index}))
job = jobs[index]
job_id = str(job["id"])
start = time.time()
result = {"ts": int(start), "job_id": job_id, "job_name": job.get("name", job_id),
          "database": job.get("database", "*"), "status": "error", "databases_verified": []}
try:
    root = backup_dir / hashlib.sha256(job_id.encode()).hexdigest()
    markers = sorted(root.glob("*/manifest.json"))
    markers = [p for p in markers if re.fullmatch(r"[0-9]+-[0-9a-f]{32}", p.parent.name)]
    if not markers:
        raise RuntimeError("No completed snapshot manifest; legacy dumps require manual verification")
    marker = markers[-1]
    manifest = json.loads(marker.read_text())
    entries = manifest["databases"]
    if manifest.get("job_id") != job_id or not entries:
        raise RuntimeError("Invalid or empty snapshot coverage")
    if job.get("database", "*") != "*" and [e["database"] for e in entries] != [job["database"]]:
        raise RuntimeError("Snapshot database coverage mismatch")
    db_type = job.get("db_type", "mariadb")
    image = checked("docker inspect --format '{{.Config.Image}}' " + shell_quote(job["container"]))
    if not image:
        raise RuntimeError("Database image is unknown")
    for entry in entries:
        database = entry["database"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", database) or Path(entry["file"]).name != entry["file"]:
            raise RuntimeError("Invalid snapshot path/database")
        dump = marker.parent / "data" / entry["file"]
        with open(dump, "rb") as f:
            digest = hashlib.sha256()
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != entry["sha256"]:
            raise RuntimeError("Snapshot checksum mismatch")
        with gzip.open(dump, "rb") as f:
            if sql_digest(f) != entry["sql_sha256"]:
                raise RuntimeError("SQL snapshot checksum mismatch")
        name = "wcp-drill-" + os.urandom(12).hex()
        password = os.urandom(24).hex()
        try:
            env = "MARIADB_ROOT_PASSWORD" if db_type == "mariadb" else "POSTGRES_PASSWORD"
            db_env = "MARIADB_DATABASE" if db_type == "mariadb" else "POSTGRES_DB"
            checked(f"docker run -d --name {name} -e {env}={password} -e {db_env}={shell_quote(database)} {shell_quote(image)}")
            for attempt in range(30):
                probe = (f"docker exec -e MYSQL_PWD={password} {name} mariadb -uroot {shell_quote(database)} -e 'SELECT 1'" if db_type == "mariadb"
                         else f"docker exec -e PGPASSWORD={password} {name} psql -v ON_ERROR_STOP=1 -U postgres {shell_quote(database)} -c 'SELECT 1'")
                if run(probe, timeout=5).returncode == 0:
                    break
                time.sleep(1)
            else:
                raise RuntimeError("Throwaway database did not become ready")
            client = (f"docker exec -i -e MYSQL_PWD={password} {name} mariadb -uroot {shell_quote(database)}" if db_type == "mariadb"
                      else f"docker exec -i -e PGPASSWORD={password} {name} psql -v ON_ERROR_STOP=1 -U postgres {shell_quote(database)}")
            checked(f"gunzip -c {shell_quote(str(dump))} | {client}")
            with tempfile.TemporaryDirectory(dir=backup_dir) as temp:
                restored = Path(temp) / "restored.sql"
                checked(database_dump_command(db_type, name, password, database) + " > " + shell_quote(str(restored)))
                with open(restored, "rb") as f:
                    if sql_digest(f) != entry["sql_sha256"]:
                        raise RuntimeError("Restored schema/data differs from the SQL snapshot")
            result["databases_verified"].append(database)
        finally:
            checked(f"docker rm -f {name}")
    manifest["restore_verified"] = True
    marker.write_text(json.dumps(manifest))
    prefix = job.get("prefix", "backups").strip("/")
    remote = f"{job['remote_name']}:{job['bucket']}" + ("/" + prefix if prefix else "")
    remote += "/jobs/" + root.name + "/" + marker.parent.name + "/manifest.json"
    checked("rclone --config " + shell_quote(str(WCP_DIR / "rclone.conf")) + " copyto " + shell_quote(str(marker)) + " " + shell_quote(remote))
    result["status"] = "ok"
except Exception as e:
    result["error"] = str(e)
result["duration_s"] = int(time.time() - start)
log_entry(str(WCP_DIR / "logs/restore-drill.log"), result)
notify(str(WCP_DIR / "notify.conf"), is_failure=result["status"] != "ok", is_success=result["status"] == "ok",
       summary_text=f"Restore drill: {result['status']}", event_payload=result, username="WCP Restore Drill")
sys.exit(0 if result["status"] == "ok" else 1)
PYEOF
