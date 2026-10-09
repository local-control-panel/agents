#!/usr/bin/env bash
# wcp-agent: backup-agent
# wcp-agent-version: 1.1.1
# wcp-agent-description: DB + file backups to S3/B2/R2 with Slack/Discord/webhook notifications

set -euo pipefail

WCP_DIR="${WCP_DIR:-/root/.wcp}"
export WCP_DIR
trap 'ec=$?; mkdir -p "$WCP_DIR/agents"; echo "{\"ts\":$(date +%s),\"exit_code\":$ec}" > "$WCP_DIR/agents/backup-agent.heartbeat"' EXIT

python3 - "$@" << 'PYEOF'
import argparse, fcntl, gzip, hashlib, json, os, re, shutil, sys, time, uuid

SAFE_DB_NAME = re.compile(r"^[A-Za-z0-9_-]+$")

WCP_DIR    = os.environ.get("WCP_DIR", "/root/.wcp")
BACKUP_DIR = os.path.join(WCP_DIR, "backups")
LOG_FILE   = os.path.join(WCP_DIR, "logs", "backup-agent.log")
CONF_FILE  = os.path.join(WCP_DIR, "backup.conf")
NOTIFY_FILE = os.path.join(WCP_DIR, "notify.conf")
RCLONE_CONF = os.path.join(WCP_DIR, "rclone.conf")

sys.path.insert(0, os.path.join(WCP_DIR, "agents"))
import base64, types
_agent_lib = types.ModuleType('wcp_agent_lib')
exec(base64.b64decode('IiIiU2hhcmVkIGhlbHBlcnMgZm9yIFdDUCBidW5kbGVkIGFnZW50IHNjcmlwdHMuCgpVcGxvYWRlZCBhbG9uZ3NpZGUgZXZlcnkgYWdlbnQgc2NyaXB0IGludG8gL3Jvb3QvLndjcC9hZ2VudHMvIHNvIGFueSBvZiB0aGVtCmNhbiBgc3lzLnBhdGguaW5zZXJ0KDAsIFdDUF9ESVIgKyAiL2FnZW50cyIpOyBpbXBvcnQgd2NwX2FnZW50X2xpYmAuCiIiIgoKaW1wb3J0IGpzb24KaW1wb3J0IG9zCmltcG9ydCBzdWJwcm9jZXNzCgoKZGVmIHNoZWxsX3F1b3RlKHMpOgogICAgIiIiU2luZ2xlLXF1b3RlIGBzYCBmb3Igc2FmZSBpbnRlcnBvbGF0aW9uIGludG8gYSBzaGVsbCBjb21tYW5kIHN0cmluZy4iIiIKICAgIHJldHVybiAiJyIgKyBzdHIocykucmVwbGFjZSgiJyIsICInXFwnJyIpICsgIiciCgoKZGVmIHJ1bihjbWQsIHRpbWVvdXQ9NjApOgogICAgIiIiUnVuIGBjbWRgIChhIHNoZWxsIHN0cmluZykgYW5kIHJldHVybiB0aGUgY29tcGxldGVkIHByb2Nlc3MuIiIiCiAgICByZXR1cm4gc3VicHJvY2Vzcy5ydW4oY21kLCBzaGVsbD1UcnVlLCBjYXB0dXJlX291dHB1dD1UcnVlLCB0ZXh0PVRydWUsIHRpbWVvdXQ9dGltZW91dCkKCgpkZWYgcnVuX2FyZ3YoYXJndiwgdGltZW91dD02MCk6CiAgICAiIiJSdW4gYGFyZ3ZgIChhIGxpc3QsIG5vIHNoZWxsKSBhbmQgcmV0dXJuIHRoZSBjb21wbGV0ZWQgcHJvY2Vzcy4KCiAgICBVc2UgdGhpcyBpbnN0ZWFkIG9mIGBydW5gIGZvciBhbnkgYXJndW1lbnQgdGhhdCBpc24ndCBmdWxseSB0cnVzdGVkCiAgICAoZS5nLiBhIGZpbGVuYW1lIHJlcG9ydGVkIGJ5IGEgY29udGFpbmVyKSAtIHRoZXJlJ3Mgbm8gc2hlbGwgdG8KICAgIHJlLXBhcnNlIGl0LCBzbyBpdCBjYW4ndCBicmVhayBvdXQgb2YgcXVvdGluZy4KICAgICIiIgogICAgcmV0dXJuIHN1YnByb2Nlc3MucnVuKGFyZ3YsIGNhcHR1cmVfb3V0cHV0PVRydWUsIHRleHQ9VHJ1ZSwgdGltZW91dD10aW1lb3V0KQoKCmRlZiByZWFkX2pzb24ocGF0aCwgZGVmYXVsdD1Ob25lKToKICAgICIiIkxvYWQgSlNPTiBmcm9tIGBwYXRoYCwgcmV0dXJuaW5nIGBkZWZhdWx0YCBpZiBtaXNzaW5nL3VucmVhZGFibGUuIiIiCiAgICB0cnk6CiAgICAgICAgd2l0aCBvcGVuKHBhdGgpIGFzIGY6CiAgICAgICAgICAgIHJldHVybiBqc29uLmxvYWQoZikKICAgIGV4Y2VwdCBFeGNlcHRpb246CiAgICAgICAgcmV0dXJuIGRlZmF1bHQKCgpkZWYgd3JpdGVfanNvbihwYXRoLCBkYXRhKToKICAgICIiIldyaXRlIGBkYXRhYCBhcyBKU09OIHRvIGBwYXRoYCwgY3JlYXRpbmcgcGFyZW50IGRpcmVjdG9yaWVzIGFzIG5lZWRlZC4iIiIKICAgIG9zLm1ha2VkaXJzKG9zLnBhdGguZGlybmFtZShwYXRoKSwgZXhpc3Rfb2s9VHJ1ZSkKICAgIHdpdGggb3BlbihwYXRoLCAidyIpIGFzIGY6CiAgICAgICAganNvbi5kdW1wKGRhdGEsIGYpCgoKZGVmIGxvZ19lbnRyeShsb2dfZmlsZSwgZW50cnksIG1heF9saW5lcz0yMDAwKToKICAgICIiIkFwcGVuZCBgZW50cnlgIGFzIGEgSlNPTiBsaW5lIHRvIGBsb2dfZmlsZWAsIHRyaW1taW5nIHRvIGBtYXhfbGluZXNgLiIiIgogICAgb3MubWFrZWRpcnMob3MucGF0aC5kaXJuYW1lKGxvZ19maWxlKSwgZXhpc3Rfb2s9VHJ1ZSkKICAgIHdpdGggb3Blbihsb2dfZmlsZSwgImEiKSBhcyBmOgogICAgICAgIGYud3JpdGUoanNvbi5kdW1wcyhlbnRyeSwgc2VwYXJhdG9ycz0oIiwiLCAiOiIpKSArICJcbiIpCiAgICB0cnk6CiAgICAgICAgd2l0aCBvcGVuKGxvZ19maWxlKSBhcyBmOgogICAgICAgICAgICBsaW5lcyA9IGYucmVhZGxpbmVzKCkKICAgICAgICBpZiBsZW4obGluZXMpID4gbWF4X2xpbmVzOgogICAgICAgICAgICB3aXRoIG9wZW4obG9nX2ZpbGUsICJ3IikgYXMgZjoKICAgICAgICAgICAgICAgIGYud3JpdGVsaW5lcyhsaW5lc1stbWF4X2xpbmVzOl0pCiAgICBleGNlcHQgRXhjZXB0aW9uOgogICAgICAgIHBhc3MKCgpkZWYgX3NoX2VzYyhzKToKICAgIHJldHVybiAiJyIgKyBzdHIocykucmVwbGFjZSgiJyIsICInXFwnJyIpICsgIiciCgoKZGVmIG5vdGlmeShub3RpZnlfZmlsZSwgaXNfZmFpbHVyZSwgaXNfc3VjY2Vzcywgc3VtbWFyeV90ZXh0LCBldmVudF9wYXlsb2FkLCB1c2VybmFtZT0iV0NQIEFnZW50Iik6CiAgICAiIiJTZW5kIGBzdW1tYXJ5X3RleHRgL2BldmVudF9wYXlsb2FkYCB0byBldmVyeSBlbmFibGVkIGNoYW5uZWwgaW4KICAgIGBub3RpZnlfZmlsZWAgd2hvc2Ugb25fZmFpbHVyZS9vbl9zdWNjZXNzIGdhdGUgbWF0Y2hlcy4gYGlzX2ZhaWx1cmVgIGFuZAogICAgYGlzX3N1Y2Nlc3NgIGFyZSBpbmRlcGVuZGVudCAobm90IGFzc3VtZWQgY29tcGxlbWVudGFyeSkgc28gY2FsbGVycyB3aG9zZQogICAgb3V0Y29tZSBjYW4gYmUgbmVpdGhlciAoZS5nLiBub3RoaW5nIHRvIGRvKSBzaW1wbHkgcGFzcyBib3RoIEZhbHNlLgogICAgIiIiCiAgICB0cnk6CiAgICAgICAgd2l0aCBvcGVuKG5vdGlmeV9maWxlKSBhcyBmOgogICAgICAgICAgICBub3RpZnlfY29uZiA9IGpzb24ubG9hZChmKQogICAgZXhjZXB0IEV4Y2VwdGlvbjoKICAgICAgICByZXR1cm4KCiAgICBmb3IgY2ggaW4gbm90aWZ5X2NvbmYuZ2V0KCJjaGFubmVscyIsIFtdKToKICAgICAgICBpZiBub3QgY2guZ2V0KCJlbmFibGVkIiwgVHJ1ZSk6CiAgICAgICAgICAgIGNvbnRpbnVlCiAgICAgICAgc2VuZCA9IChpc19mYWlsdXJlIGFuZCBjaC5nZXQoIm9uX2ZhaWx1cmUiLCBUcnVlKSkgb3IgXAogICAgICAgICAgICAgICAoaXNfc3VjY2VzcyBhbmQgY2guZ2V0KCJvbl9zdWNjZXNzIiwgRmFsc2UpKQogICAgICAgIGlmIG5vdCBzZW5kOgogICAgICAgICAgICBjb250aW51ZQoKICAgICAgICB3ZWJob29rX3VybCA9IGNoLmdldCgid2ViaG9va191cmwiLCAiIikKICAgICAgICBpZiBub3Qgd2ViaG9va191cmw6CiAgICAgICAgICAgIGNvbnRpbnVlCgogICAgICAgIGNoX3R5cGUgPSBjaC5nZXQoInR5cGUiLCAid2ViaG9vayIpCiAgICAgICAgaWYgY2hfdHlwZSA9PSAic2xhY2siOgogICAgICAgICAgICBib2R5ID0ganNvbi5kdW1wcyh7InRleHQiOiBzdW1tYXJ5X3RleHQsICJ1c2VybmFtZSI6IHVzZXJuYW1lfSkKICAgICAgICBlbGlmIGNoX3R5cGUgPT0gImRpc2NvcmQiOgogICAgICAgICAgICBib2R5ID0ganNvbi5kdW1wcyh7ImNvbnRlbnQiOiBzdW1tYXJ5X3RleHR9KQogICAgICAgIGVsc2U6CiAgICAgICAgICAgIGJvZHkgPSBqc29uLmR1bXBzKGV2ZW50X3BheWxvYWQpCgogICAgICAgIHN1YnByb2Nlc3MucnVuKAogICAgICAgICAgICBmImN1cmwgLXMgLW0gMTAgLVggUE9TVCAtSCAnQ29udGVudC1UeXBlOiBhcHBsaWNhdGlvbi9qc29uJyAiCiAgICAgICAgICAgIGYiLWQge19zaF9lc2MoYm9keSl9IHtfc2hfZXNjKHdlYmhvb2tfdXJsKX0iLAogICAgICAgICAgICBzaGVsbD1UcnVlLAogICAgICAgICAgICBjYXB0dXJlX291dHB1dD1UcnVlLAogICAgICAgICAgICB0ZXh0PVRydWUsCiAgICAgICAgICAgIHRpbWVvdXQ9MzAsCiAgICAgICAgKQoKCmRlZiBkYXRhYmFzZV9kdW1wX2NvbW1hbmQoZGJfdHlwZSwgY29udGFpbmVyLCBwYXNzd29yZCwgZGF0YWJhc2UpOgogICAgIiIiU3RhYmxlIFNRTCBvdXRwdXQgYWxsb3dzIGEgcmVzdG9yZSBkcmlsbCB0byBjb21wYXJlIHRoZSBhY3R1YWwgc25hcHNob3QuIiIiCiAgICB0YXJnZXQgPSBzaGVsbF9xdW90ZShjb250YWluZXIpCiAgICBzZWNyZXQgPSBzaGVsbF9xdW90ZShwYXNzd29yZCkKICAgIG5hbWUgPSBzaGVsbF9xdW90ZShkYXRhYmFzZSkKICAgIGlmIGRiX3R5cGUgPT0gIm1hcmlhZGIiOgogICAgICAgIHJldHVybiAoZiJkb2NrZXIgZXhlYyAtZSBNWVNRTF9QV0Q9e3NlY3JldH0ge3RhcmdldH0gbWFyaWFkYi1kdW1wIC11cm9vdCAiCiAgICAgICAgICAgICAgICBmIi0tc2luZ2xlLXRyYW5zYWN0aW9uIC0tcm91dGluZXMgLS10cmlnZ2VycyAtLXNraXAtY29tbWVudHMgIgogICAgICAgICAgICAgICAgZiItLXNraXAtZXh0ZW5kZWQtaW5zZXJ0IC0tb3JkZXItYnktcHJpbWFyeSAtLXNraXAtYWRkLWxvY2tzIC0tc2tpcC1kaXNhYmxlLWtleXMge25hbWV9IikKICAgIGlmIGRiX3R5cGUgPT0gInBvc3RncmVzIjoKICAgICAgICByZXR1cm4gKGYiZG9ja2VyIGV4ZWMgLWUgUEdQQVNTV09SRD17c2VjcmV0fSB7dGFyZ2V0fSBwZ19kdW1wIC1VIHBvc3RncmVzICIKICAgICAgICAgICAgICAgIGYiLS1uby1vd25lciAtLW5vLXByaXZpbGVnZXMgLS1pbnNlcnRzIC0tcm93cy1wZXItaW5zZXJ0PTEge25hbWV9IikKICAgIHJhaXNlIFZhbHVlRXJyb3IoIlVuc3VwcG9ydGVkIGRhdGFiYXNlIHR5cGUiKQoKCmRlZiBzcWxfZGlnZXN0KHN0cmVhbSk6CiAgICAiIiJJZ25vcmUgb25seSBwZ19kdW1wJ3MgcmFuZG9tIHBzcWwgZ3VhcmQgdG9rZW4sIHdoaWNoIGlzIG5vdCBTUUwgZGF0YS4iIiIKICAgIGltcG9ydCBoYXNobGliCiAgICBpbXBvcnQgcmUKICAgIGRpZ2VzdCA9IGhhc2hsaWIuc2hhMjU2KCkKICAgIHNpemUgPSAwCiAgICBmb3IgbGluZSBpbiBzdHJlYW06CiAgICAgICAgaWYgcmUuZnVsbG1hdGNoKHJiIlxcKD86dW4pP3Jlc3RyaWN0IFtBLVphLXowLTldK1xyP1xuPyIsIGxpbmUpOgogICAgICAgICAgICBjb250aW51ZQogICAgICAgIGRpZ2VzdC51cGRhdGUobGluZSkKICAgICAgICBzaXplICs9IGxlbihsaW5lKQogICAgaWYgc2l6ZSA9PSAwOgogICAgICAgIHJhaXNlIFZhbHVlRXJyb3IoIkVtcHR5IFNRTCBkdW1wIikKICAgIHJldHVybiBkaWdlc3QuaGV4ZGlnZXN0KCkK'), _agent_lib.__dict__)
sys.modules['wcp_agent_lib'] = _agent_lib
from wcp_agent_lib import log_entry as _log_entry, notify as _notify, run, database_dump_command, sql_digest

os.makedirs(BACKUP_DIR, exist_ok=True)
parser = argparse.ArgumentParser()
parser.add_argument("--job-id")
args = parser.parse_args()
# ponytail: serialize all backup jobs; per-job locks only if throughput requires it.
lock = open(os.path.join(BACKUP_DIR, ".lock"), "a")
try:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    sys.exit("Another backup is running; retry after it completes")


def sh_esc(s):
    return "'" + str(s).replace("'", "'\\''") + "'"


def run_cmd(cmd, timeout=3600):
    return run("bash -o pipefail -c " + sh_esc(cmd), timeout=timeout)


def log_entry(entry):
    _log_entry(LOG_FILE, entry)


try:
    with open(CONF_FILE) as f:
        conf = json.load(f)
except Exception as e:
    print(f"ERROR: Cannot read {CONF_FILE}: {e}", file=sys.stderr)
    sys.exit(1)

jobs = conf.get("jobs", [])
if args.job_id is not None:
    jobs = [job for job in jobs if str(job.get("id")) == args.job_id and job.get("enabled", True)]
    if not jobs:
        sys.exit("Unknown or disabled backup job")
succeeded = 0
failed = 0
job_results = []

for job in jobs:
    if not job.get("enabled", True):
        continue

    job_id   = str(job.get("id", ""))
    job_name = job.get("name", job_id)
    job_type = job.get("job_type", "db")
    remote_name = job["remote_name"]
    bucket      = job["bucket"]
    prefix      = job.get("prefix", "backups").strip("/")
    remote_dest = f"{remote_name}:{bucket}/{prefix}" if prefix else f"{remote_name}:{bucket}"
    remote_ret  = int(job.get("remote_retention_days", 30))

    start_ts  = time.time()
    status    = "ok"
    error_msg = None
    size_bytes = 0

    generation = str(time.time_ns()) + "-" + uuid.uuid4().hex
    namespace = hashlib.sha256(job_id.encode()).hexdigest()
    job_dir = os.path.join(BACKUP_DIR, namespace)
    staging = os.path.join(job_dir, generation + ".partial")
    payload_dir = os.path.join(staging, "data")
    remote_root = remote_dest + "/jobs/" + namespace
    remote_snapshot = remote_root + "/" + generation
    manifest = {"version": 2, "job_id": job_id, "job_type": job_type, "created": int(start_ts), "databases": [], "verified": True, "restore_verified": False}
    try:
        os.makedirs(payload_dir)
        if job_type == "db":
            db_type   = job.get("db_type", "mariadb")
            manifest["db_type"] = db_type
            container = job.get("container", db_type)
            password  = job.get("db_password", "")
            database  = job.get("database", "*")
            local_ret = int(job.get("local_retention_days", 7))

            if database == "*":
                if db_type == "mariadb":
                    r = run_cmd(
                        f"docker exec {sh_esc(container)} mariadb -uroot -p{sh_esc(password)} "
                        f"-N -e 'SHOW DATABASES'"
                    )
                    skip = {"information_schema", "performance_schema", "mysql", "sys"}
                    dbs = [d.strip() for d in r.stdout.splitlines() if d.strip() and d.strip() not in skip]
                else:
                    r = run_cmd(
                        f"docker exec -e PGPASSWORD={sh_esc(password)} {sh_esc(container)} psql -U postgres -At "
                        f"-c 'SELECT datname FROM pg_database WHERE datistemplate = false'"
                    )
                    dbs = [d.strip() for d in r.stdout.splitlines() if d.strip()]

                if r.returncode != 0:
                    raise RuntimeError("Database discovery failed")
                if not dbs:
                    raise RuntimeError("Database discovery returned no databases")
                # Auto-discovered names become part of a backup file path
                # below; postgres in particular allows near-arbitrary quoted
                # identifiers, so anything outside a safe charset (which
                # could otherwise write outside BACKUP_DIR) is dropped
                # rather than dumped.
                safe = [d for d in dbs if SAFE_DB_NAME.match(d)]
                for d in set(dbs) - set(safe):
                    print(f"[ERR] skipping unsafe database name {d!r}", file=sys.stderr)
                if len(safe) != len(dbs):
                    raise RuntimeError("Database coverage incomplete: unsupported database name")
                dbs = safe
            else:
                dbs = [database]

            for db in dbs:
                if not SAFE_DB_NAME.fullmatch(db):
                    raise RuntimeError("Unsupported database name")
                ts_str = time.strftime("%Y%m%d_%H%M%S")
                fname  = f"{db}_{ts_str}.sql.gz"
                fpath  = os.path.join(payload_dir, fname)

                cmd = database_dump_command(db_type, container, password, db) + f" | gzip > {sh_esc(fpath)}"

                r = run_cmd(cmd)
                if r.returncode != 0:
                    raise RuntimeError(f"Dump failed for {db}: {r.stderr.strip()[:300]}")

                r = run_cmd(f"gzip -t {sh_esc(fpath)}")
                if r.returncode != 0:
                    raise RuntimeError(f"Integrity check failed for {fname}")

                with gzip.open(fpath, "rb") as dump:
                    sql_hash = sql_digest(dump)
                with open(fpath, "rb") as artifact:
                    hasher = hashlib.sha256()
                    for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
                        hasher.update(chunk)
                    digest = hasher.hexdigest()
                manifest["databases"].append({"database": db, "file": fname, "sha256": digest, "sql_sha256": sql_hash})
                size_bytes += os.path.getsize(fpath)

        elif job_type == "files":
            src_path = job.get("path", "")
            excludes = job.get("exclude", [])
            exclude_args = " ".join(f"--exclude {sh_esc(e)}" for e in excludes)
            if not os.path.isdir(src_path):
                raise RuntimeError("Backup source directory does not exist")
            # ponytail: full snapshots require local disk equal to source size; add incremental storage only when capacity requires it.
            r = run_cmd(f"rclone --config {sh_esc(RCLONE_CONF)} copy {sh_esc(src_path)} {sh_esc(payload_dir)} {exclude_args}")
            if r.returncode != 0:
                raise RuntimeError("File snapshot failed")
        else:
            raise RuntimeError("Unsupported backup job type")

        # Publish the completion marker only after a full content comparison.
        for action in ("copy", "check --download"):
            r = run_cmd(f"rclone --config {sh_esc(RCLONE_CONF)} {action} {sh_esc(payload_dir)} {sh_esc(remote_snapshot + '/data')}")
            if r.returncode != 0:
                raise RuntimeError("Snapshot upload/verification failed")
        manifest["files"] = {}
        for root, _, names in os.walk(payload_dir):
            for name in names:
                path = os.path.join(root, name)
                hasher = hashlib.sha256()
                with open(path, "rb") as f:
                    for chunk in iter(lambda: f.read(1024 * 1024), b""):
                        hasher.update(chunk)
                manifest["files"][os.path.relpath(path, payload_dir)] = hasher.hexdigest()
        marker = os.path.join(staging, "manifest.json")
        with open(marker, "w") as f:
            json.dump(manifest, f)
        r = run_cmd(f"rclone --config {sh_esc(RCLONE_CONF)} copyto {sh_esc(marker)} {sh_esc(remote_snapshot + '/manifest.json')}")
        if r.returncode != 0:
            raise RuntimeError("Snapshot publication failed")
        os.rename(staging, os.path.join(job_dir, generation))

        # Retention is bounded to completed generations of this job, never file mtimes.
        if remote_ret > 0:
            r = run_cmd(f"rclone --config {sh_esc(RCLONE_CONF)} lsf --dirs-only {sh_esc(remote_root)}")
            if r.returncode != 0:
                raise RuntimeError("Snapshot retention listing failed")
            candidates = []
            for name in r.stdout.splitlines():
                name = name.rstrip("/")
                if not re.fullmatch(r"[0-9]+-[0-9a-f]{32}", name) or name == generation:
                    continue
                if int(name.split("-")[0]) / 1_000_000_000 >= start_ts - remote_ret * 86400:
                    continue
                target = remote_root + "/" + name
                old = run_cmd(f"rclone --config {sh_esc(RCLONE_CONF)} cat {sh_esc(target + '/manifest.json')}")
                if old.returncode != 0:
                    continue  # Incomplete uploads are never retention candidates.
                data = json.loads(old.stdout)
                if data.get("job_id") != job_id or data.get("verified") is not True:
                    continue
                candidates.append((name, data))
            restored = [name for name, data in candidates if data.get("restore_verified") is True]
            protected = max(restored) if restored else None
            for name, data in candidates:
                if job_type == "db" and (protected is None or name == protected):
                    continue
                target = remote_root + "/" + name
                r = run_cmd(f"rclone --config {sh_esc(RCLONE_CONF)} purge {sh_esc(target)}")
                if r.returncode != 0:
                    raise RuntimeError("Snapshot retention failed")
        local_ret = int(job.get("local_retention_days", 7))
        local_restored = []
        for name in os.listdir(job_dir):
            if re.fullmatch(r"[0-9]+-[0-9a-f]{32}", name):
                with open(os.path.join(job_dir, name, "manifest.json")) as f:
                    if json.load(f).get("restore_verified") is True:
                        local_restored.append(name)
        protected_local = max(local_restored) if local_restored else None
        for name in os.listdir(job_dir):
            if job_type == "db" and (protected_local is None or name == protected_local):
                continue
            if name == generation or not re.fullmatch(r"[0-9]+-[0-9a-f]{32}", name):
                continue
            if local_ret > 0 and int(name.split("-")[0]) / 1_000_000_000 < start_ts - local_ret * 86400:
                shutil.rmtree(os.path.join(job_dir, name))

    except Exception as e:
        shutil.rmtree(staging, ignore_errors=True)
        status    = "error"
        error_msg = str(e)
        failed   += 1
        print(f"[ERR] {job_name}: {error_msg}", file=sys.stderr)
    else:
        succeeded += 1
        dur = int(time.time() - start_ts)
        print(f"[OK]  {job_name} ({dur}s)")

    result: dict = {
        "ts":         int(time.time()),
        "job_id":     job_id,
        "job_name":   job_name,
        "job_type":   job_type,
        "status":     status,
        "size_bytes": size_bytes,
        "duration_s": int(time.time() - start_ts),
    }
    if error_msg:
        result["error"] = error_msg
    log_entry(result)
    job_results.append(result)


# ── Notifications ─────────────────────────────────────────────────────────────
total = len(job_results)
summary_text = f"Backup: {succeeded}/{total} succeeded"
if failed > 0:
    summary_text += f", {failed} failed"

_notify(
    NOTIFY_FILE,
    is_failure=failed > 0,
    is_success=succeeded > 0,
    summary_text=summary_text,
    event_payload={
        "event":     "backup_complete",
        "timestamp": int(time.time()),
        "summary":   {"total": total, "succeeded": succeeded, "failed": failed},
        "jobs":      job_results,
    },
    username="WCP Backups",
)

print(f"Backup done: {succeeded} succeeded, {failed} failed")
sys.exit(1 if failed > 0 else 0)
PYEOF
