"""Tests for scripts/wcp_agent.py and the agent API in lib/wcp_agent_lib.py.
Run: python3 -m unittest discover tests"""
import contextlib
import io
import json
import os
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "lib"))
import check_agents  # noqa: E402
import wcp_agent  # noqa: E402
import wcp_agent_lib as lib  # noqa: E402


class Scaffold(unittest.TestCase):
    def setUp(self):
        # Scaffold into a scratch copy of the layout so the real agents/ stays clean.
        self.root = Path(tempfile.mkdtemp())
        (self.root / "agents").mkdir()
        self.patches = [mock.patch.object(wcp_agent, "ROOT", self.root)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def new(self, name, *extra):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return wcp_agent.main(["new", name, *extra])

    def test_bash_scaffold_passes_the_checker_and_the_contract(self):
        self.assertEqual(self.new("demo-bash", "--schedule", "0 * * * *"), 0)
        self.assertEqual(check_agents.check(self.root / "agents" / "demo-bash"), [])
        report = wcp_agent.run_agent("demo-bash")
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["heartbeat"]["exit_code"], 0)
        self.assertTrue(report["lock_ok"])

    def test_python_scaffold_uses_the_engine_helpers_in_dry_run(self):
        self.assertEqual(self.new("demo-py", "--lang", "python"), 0)
        self.assertEqual(check_agents.check(self.root / "agents" / "demo-py"), [])
        report = wcp_agent.run_agent("demo-py", dry_run=True)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["logs"], {})  # dry run writes no log line
        self.assertIsNone(report["heartbeat"])  # ...and no heartbeat

    def test_refuses_bad_or_existing_names_and_bad_text(self):
        self.assertEqual(self.new("Bad_Name"), 1)
        self.assertEqual(self.new("ok-name"), 0)
        self.assertEqual(self.new("ok-name"), 1)
        self.assertEqual(self.new("quote", "--author", 'A"B'), 1)
        self.assertFalse((self.root / "agents" / "quote").exists())
        self.assertEqual(self.new("bad-cron", "--schedule", "often"), 1)

    def test_a_failing_agent_is_reported(self):
        self.new("broken")
        script = self.root / "agents" / "broken" / "agent.sh"
        script.write_text(script.read_text() + "exit 3\n")
        report = wcp_agent.run_agent("broken")
        self.assertFalse(report["ok"])
        self.assertEqual(report["heartbeat"]["exit_code"], 3)

    def test_an_agent_without_a_heartbeat_is_reported(self):
        self.new("silent")
        script = self.root / "agents" / "silent" / "agent.sh"
        script.write_text(script.read_text().replace("trap '\"$OPS\" agent heartbeat \"$NAME\" --exit-code $?' EXIT", ""))
        report = wcp_agent.run_agent("silent")
        self.assertFalse(report["ok"])
        self.assertTrue(any("heartbeat" in p for p in report["problems"]), report)


class EngineHelpers(unittest.TestCase):
    """The runner's stub engine must behave like the real helpers."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        stub = self.dir / "ops-engine"
        stub.write_text(wcp_agent.ENGINE_STUB)
        stub.chmod(0o755)
        self.stub = str(stub)
        self.env = dict(os.environ, WCP_DIR=str(self.dir), WCP_STUB_CALLS=str(self.dir / "calls"))
        self.env.pop("WCP_DRY_RUN", None)

    def call(self, *args, **env):
        import subprocess
        return subprocess.run([self.stub, *args], env=dict(self.env, **env), capture_output=True,
                              text=True, stdin=subprocess.DEVNULL)

    def test_heartbeat_and_log(self):
        self.assertEqual(self.call("agent", "heartbeat", "demo", "--exit-code", "4").returncode, 0)
        beat = json.loads((self.dir / "agents" / "demo.heartbeat").read_text())
        self.assertEqual(beat["exit_code"], 4)
        self.assertEqual(self.call("agent", "log", "demo", "--level", "warn", "a", "b").returncode, 0)
        line = json.loads((self.dir / "logs" / "demo.log").read_text())
        self.assertEqual((line["level"], line["msg"]), ("warn", "a b"))

    def test_dry_run_writes_nothing_and_bad_names_exit_2(self):
        self.call("agent", "heartbeat", "demo", WCP_DRY_RUN="1")
        self.call("agent", "log", "demo", "x", WCP_DRY_RUN="1")
        self.assertFalse((self.dir / "agents").exists() or (self.dir / "logs").exists())
        self.assertEqual(self.call("agent", "heartbeat", "../x").returncode, 2)

    def test_lock_wrapper_runs_holds_and_skips(self):
        out = self.call("agent", "lock", "demo", "--", "sh", "-c", "echo $WCP_LOCK_HELD; exit 5")
        self.assertEqual((out.returncode, out.stdout), (5, "demo\n"))
        self.assertEqual(self.call("agent", "lock", "demo", "--check").returncode, 0)
        import fcntl
        with open(self.dir / "agents" / "demo.lock", "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.call("agent", "lock", "demo", "--check").returncode, 75)
            self.assertEqual(self.call("agent", "lock", "demo", "--", "false").returncode, 0)
            self.assertEqual(
                self.call("agent", "lock", "demo", "--held-exit-code", "9", "--", "false").returncode, 9)


REAL_ENGINE = os.environ.get("WCP_REAL_ENGINE")


@unittest.skipUnless(REAL_ENGINE and os.access(REAL_ENGINE, os.X_OK),
                     "set WCP_REAL_ENGINE to a built ops-engine to run against it")
class RealEngine(unittest.TestCase):
    """The scaffolds against the engine's own helpers, not the stub."""

    def test_scaffolds_pass_the_contract_with_the_real_engine(self):
        root = Path(tempfile.mkdtemp())
        (root / "agents").mkdir()
        with mock.patch.object(wcp_agent, "ROOT", root), \
                contextlib.redirect_stdout(io.StringIO()):
            for lang in ("bash", "python"):
                self.assertEqual(wcp_agent.main(["new", f"real-{lang}", "--lang", lang]), 0)
                report = wcp_agent.run_agent(f"real-{lang}", engine=REAL_ENGINE)
                self.assertTrue(report["ok"], report)
                self.assertTrue(report["lock_ok"])
                dry = wcp_agent.run_agent(f"real-{lang}", dry_run=True, engine=REAL_ENGINE)
                self.assertTrue(dry["ok"], dry)
                self.assertEqual(dry["logs"], {})


class BuiltinLocks(unittest.TestCase):
    """The five agents that used to run unlocked: a second start while the lock
    is held exits 0 straight away and does no work."""
    NAMES = ["bruteforce-guard", "cache-warmup", "error-log-digest", "metrics-agent", "resource-alert"]

    def test_second_start_exits_0_and_does_nothing(self):
        import fcntl
        import shutil
        import subprocess
        for name in self.NAMES:
            with self.subTest(agent=name):
                scratch = Path(tempfile.mkdtemp())
                self.addCleanup(shutil.rmtree, scratch, True)
                (scratch / "agents").mkdir()
                env = dict(os.environ, WCP_DIR=str(scratch), SITES_ROOT=str(scratch / "sites"),
                           METRICS_DIR=str(scratch / "metrics"))
                if shutil.which("flock") is None:  # macOS
                    shim = scratch / "bin" / "flock"
                    shim.parent.mkdir()
                    shim.write_text(wcp_agent.FLOCK_SHIM)
                    shim.chmod(0o755)
                    env["PATH"] = f"{shim.parent}{os.pathsep}{env['PATH']}"
                with open(scratch / "agents" / f"{name}.lock", "w") as held:
                    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    done = subprocess.run(["bash", str(ROOT / "agents" / name / "agent.sh")], env=env,
                                          stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                          timeout=30)
                self.assertEqual(done.returncode, 0, done.stderr)
                beat = json.loads((scratch / "agents" / f"{name}.heartbeat").read_text())
                self.assertEqual(beat["exit_code"], 0)
                written = sorted(str(f.relative_to(scratch)) for f in scratch.rglob("*")
                                 if f.is_file() and f.parent.name != "bin")
                self.assertEqual(written, [f"agents/{name}.heartbeat", f"agents/{name}.lock"])

    def test_no_agent_is_left_without_a_lock(self):
        for directory in sorted((ROOT / "agents").iterdir()):
            text = (directory / "agent.sh").read_text()
            self.assertEqual(check_agents.check(directory), [], directory.name)
            self.assertEqual(check_agents.advise(directory.name, text), [], directory.name)


class FrozenLibrary(unittest.TestCase):
    """lib/wcp_agent_lib.py is a compatibility layer: exactly the nine helpers
    published agents import, nothing added (docs/agent-api.md section 7)."""

    FROZEN = {"run", "run_argv", "shell_quote", "read_json", "write_json", "log_entry", "notify",
              "database_dump_command", "sql_digest"}

    def test_the_library_has_only_the_published_helpers(self):
        self.assertEqual(check_agents.library_names(), self.FROZEN)

    def test_phase_1_additions_are_gone(self):
        for name in ("list_sites", "engine_call", "emit_result", "write_heartbeat", "dry_run",
                     "read_conf", "wcp_dir", "LIB_API_VERSION"):
            self.assertFalse(hasattr(lib, name), name)

    def test_api_command_lists_the_frozen_helpers(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(wcp_agent.main(["api"]), 0)
        listed = {line.split("(")[0] for line in out.getvalue().splitlines()
                  if "(" in line and not line.startswith(" ")}
        self.assertEqual(listed, self.FROZEN)


class StubEngine(unittest.TestCase):
    """The stub engine used by `run` behaves as docs/agent-helpers.md in the
    engine says. With OPS_ENGINE_BIN set the same cases run against the real
    binary, which is how the two are kept in step."""

    def setUp(self):
        import subprocess
        self.subprocess = subprocess
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.dir, True)
        self.engines = {}
        stub = self.dir / "ops-engine"
        stub.write_text(wcp_agent.ENGINE_STUB)
        stub.chmod(0o755)
        self.engines["stub"] = str(stub)
        real = os.environ.get("OPS_ENGINE_BIN")
        if real:
            self.engines["real"] = real

    def call(self, kind, *args, dry=False, env_extra=None):
        wcp = self.dir / kind
        env = dict(os.environ, WCP_DIR=str(wcp), WCP_STUB_CALLS=str(self.dir / "calls"),
                   WCP_SITES_MANIFEST_DIR=str(self.dir / "manifests"), SITES_ROOT=str(self.dir / "www"),
                   WCP_TOOL_BIN_DIRS=str(self.dir / "bin"), WCP_TOOLS_DIR=str(self.dir / "tools"))
        env.pop("WCP_DRY_RUN", None)
        if dry:
            env["WCP_DRY_RUN"] = "1"
        env.update(env_extra or {})
        return self.subprocess.run([self.engines[kind], *args], env=env, capture_output=True,
                                   text=True, stdin=subprocess.DEVNULL)

    def each(self):
        for kind in self.engines:
            with self.subTest(engine=kind):
                yield kind

    def log_lines(self, kind, name="demo"):
        path = self.dir / kind / "logs" / f"{name}.log"
        return [json.loads(line) for line in path.read_text().splitlines()]

    def test_result_emit_writes_the_documented_line(self):
        for kind in self.each():
            done = self.call(kind, "agent", "result", "emit", "demo", "--status", "warn", "--summary",
                             "checked 3 sites", "--data", "sites=3", "--data-json", '{"slow": ["a"]}')
            self.assertEqual((done.returncode, done.stdout), (0, ""), done.stderr)
            line = self.log_lines(kind)[0]
            self.assertEqual(set(line), {"ts", "agent", "status", "summary", "data"})
            self.assertEqual((line["status"], line["summary"]), ("warn", "checked 3 sites"))
            self.assertEqual(line["data"], {"slow": ["a"], "sites": "3"})

    def test_result_emit_dry_run_and_bad_input(self):
        for kind in self.each():
            done = self.call(kind, "agent", "result", "emit", "dry", "--status", "ok", "--summary", "x",
                             "--json", dry=True)
            self.assertEqual(done.returncode, 0, done.stderr)
            envelope = json.loads(done.stdout)
            self.assertEqual((envelope["result"]["written"], envelope["result"]["dryRun"]), (False, True))
            self.assertFalse((self.dir / kind / "logs" / "dry.log").exists())
            base = ["agent", "result", "emit", "bad", "--status", "ok", "--summary", "s"]
            for extra in (["--data", "novalue"], ["--data", "api_token=1"], ["--data-json", "[1]"]):
                self.assertEqual(self.call(kind, *base, *extra).returncode, 2, extra)
            self.assertEqual(self.call(kind, "agent", "result", "emit", "bad", "--status", "great",
                                       "--summary", "s").returncode, 2)
            self.assertFalse((self.dir / kind / "logs" / "bad.log").exists())

    def test_config_get_and_list(self):
        for kind in self.each():
            agents = self.dir / kind / "agents"
            agents.mkdir(parents=True)
            conf = agents / "demo.conf"
            conf.write_text("# c\nWEBHOOK_URL=https://h.example/x?a=b\nTOKEN_FILE=/x\n")
            conf.chmod(0o600)
            get = lambda *a: self.call(kind, "agent", "config", "get", "demo", *a)  # noqa: E731
            self.assertEqual(get("WEBHOOK_URL").stdout, "https://h.example/x?a=b\n")
            self.assertEqual(get("NOPE", "--default", "7").stdout, "7\n")
            self.assertEqual(get("NOPE").returncode, 1)
            self.assertEqual(get("bad-key").returncode, 2)
            listed = self.call(kind, "agent", "config", "list", "demo")
            self.assertEqual(listed.stdout, "TOKEN_FILE\nWEBHOOK_URL\n")
            conf.chmod(0o666)
            self.assertEqual(get("WEBHOOK_URL").returncode, 1)
            self.assertEqual(self.call(kind, "agent", "config", "get", "nofile", "K").returncode, 1)

    def test_site_list_and_version(self):
        for kind in self.each():
            (self.dir / "manifests").mkdir(exist_ok=True)
            (self.dir / "www" / "b.example.com").mkdir(parents=True, exist_ok=True)
            (self.dir / "www" / "html").mkdir(exist_ok=True)
            site_id = "11111111-1111-4111-8111-111111111111"
            manifest = self.dir / "manifests" / f"{site_id}.json"
            manifest.write_text(json.dumps({
                "schemaVersion": 1, "siteId": site_id, "domain": "a.example.com", "siteUser": "u1",
                "contentRoot": f"sites/{site_id}/current",
                "repository": {"url": "u", "allowedBranches": ["main"], "credentialId": site_id}}))
            manifest.chmod(0o644)
            self.assertEqual(self.call(kind, "agent", "site", "list").stdout,
                             "a.example.com\nb.example.com\n")
            sites = json.loads(self.call(kind, "agent", "site", "list", "--json").stdout)["result"]["sites"]
            self.assertEqual([s["source"] for s in sites], ["manifest", "filesystem"])
            self.assertEqual(sites[0]["siteId"], site_id)
            self.assertNotIn("repository", sites[0])
            lines = self.call(kind, "agent", "version").stdout.splitlines()
            self.assertEqual(lines[0], "agent-helpers 1")
            self.assertEqual(lines[1:], ["heartbeat", "lock", "log", "result", "config", "site", "version", "tool"])

    def fake_bin(self, name, body):
        (self.dir / "bin").mkdir(exist_ok=True)
        path = self.dir / "bin" / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)

    def test_tool_status(self):
        for kind in self.each():
            status = lambda *a: self.call(kind, "agent", "tool", "status", *a)  # noqa: E731
            missing = status("rclone")
            self.assertEqual((missing.returncode, missing.stdout), (1, ""))
            self.fake_bin("rclone", "echo 'rclone v1.75.1'; echo more")
            self.fake_bin("docker", "echo 'Docker version 27.3.1, build abc'")
            self.assertEqual(status("rclone").stdout, "1.75.1\n")
            self.assertEqual(status("docker").stdout, "27.3.1\n")
            info = json.loads(status("rclone", "--json").stdout)["result"]
            self.assertEqual((info["present"], info["version"], info["installer"]),
                             (True, "1.75.1", "backup.installRclone"))
            absent = status("wp-cli", "--json")
            self.assertEqual(absent.returncode, 1)
            self.assertFalse(json.loads(absent.stdout)["result"]["present"])
            for bad in ("curl", "../rclone", "RCLONE"):
                self.assertEqual(status(bad).returncode, 2, bad)
            (self.dir / "bin" / "rclone").unlink()
            (self.dir / "bin" / "docker").unlink()

    def test_tool_ensure_refuses_unless_allowed_and_dry_run_changes_nothing(self):
        for kind in self.each():
            ensure = lambda *a, **k: self.call(kind, "agent", "tool", "ensure", *a, **k)  # noqa: E731
            self.assertEqual(ensure("rclone").returncode, 1)
            refused = ensure("rclone", "--json")
            self.assertEqual(json.loads(refused.stdout)["error"]["code"], "DEPENDENCY_UNAVAILABLE")
            self.assertEqual(ensure("curl").returncode, 2)
            agents = self.dir / kind
            agents.mkdir(exist_ok=True)
            allow = agents / "allow-tool-ensure"
            allow.write_text("rclone\n")
            allow.chmod(0o600)
            dry = ensure("rclone", "--json", dry=True)
            self.assertEqual(dry.returncode, 0, dry.stderr)
            result = json.loads(dry.stdout)["result"]
            self.assertEqual((result["outcome"], result["dryRun"], result["allowed"]), ("wouldInstall", True, True))
            self.assertFalse((self.dir / "bin").exists() and any((self.dir / "bin").iterdir()))
            self.assertEqual(ensure("docker", dry=True).returncode, 1)  # not listed
            self.fake_bin("rclone", "echo 'rclone v1.0.0'")
            present = json.loads(ensure("rclone", "--json").stdout)["result"]
            self.assertEqual(present["outcome"], "present")
            (self.dir / "bin" / "rclone").unlink()

    def test_the_scaffold_result_line_reaches_the_report(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, root, True)
        (root / "agents").mkdir()
        with mock.patch.object(wcp_agent, "ROOT", root), \
                contextlib.redirect_stdout(io.StringIO()):
            wcp_agent.main(["new", "demo-r", "--schedule", "0 * * * *"])
            report = wcp_agent.run_agent("demo-r")
            dry = wcp_agent.run_agent("demo-r", dry_run=True)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["result"]["status"], "ok")
        self.assertIsNone(dry["result"])


class Requirements(unittest.TestCase):
    META = {"version": "1.0.0", "name": "demo"}

    def test_valid_requirements_pass(self):
        meta = dict(self.META, requires_ops=["site.list", "version"], requires_helpers=["result", "tool"],
                    requires_tools=["rclone", "docker", "wp-cli"])
        self.assertEqual(check_agents.check_requires("demo", meta), [])
        self.assertEqual(check_agents.check_requires("demo", self.META), [])

    def test_bad_requirements_are_errors(self):
        bad = [("requires_tools", ["jq"]), ("requires_tools", "rclone"), ("requires_tools", ["rclone", "rclone"]),
               ("requires_helpers", ["nope"]), ("requires_ops", ["site list"]), ("requires_ops", [""]),
               ("requires_ops", [1]), ("requires_ops", ["a"] * 2), ("requires_ops", [f"op{i}" for i in range(33)])]
        for key, value in bad:
            errors = check_agents.check_requires("demo", dict(self.META, **{key: value}))
            self.assertEqual(len(errors), 1, (key, value))
            self.assertIn(key, errors[0])

    def test_a_helper_used_but_not_declared_is_an_error(self):
        text = ('# wcp-agent: demo\n# wcp-agent-version: 1.0.0\n"$OPS" agent result emit "$NAME" --status ok '
                '--summary x\nsubprocess.run([ops, "agent", "tool", "status", "rclone"])\n')
        errors = check_agents.check_script("demo", self.META, text)
        self.assertEqual(len([e for e in errors if "requires_helpers" in e]), 2, errors)
        declared = dict(self.META, requires_helpers=["result", "tool"])
        self.assertEqual([e for e in check_agents.check_script("demo", declared, text)
                          if "requires_helpers" in e], [])

    def test_prose_and_the_implied_helpers_need_no_declaration(self):
        text = ('# wcp-agent: demo\n# wcp-agent-version: 1.0.0\n# see agent site list in the docs\n'
                '"$OPS" agent log "$NAME" started\nexec "$OPS" agent lock "$NAME" -- bash "$0"\n'
                'trap \'"$OPS" agent heartbeat "$NAME"\' EXIT\n')
        errors = check_agents.check_script("demo", self.META, text)
        self.assertEqual([e for e in errors if "requires_helpers" in e], [])

    def test_scaffolds_declare_what_they_use_and_the_registry_carries_it(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, root, True)
        (root / "agents").mkdir()
        with mock.patch.object(wcp_agent, "ROOT", root), contextlib.redirect_stdout(io.StringIO()):
            for lang in ("bash", "python"):
                self.assertEqual(wcp_agent.main(["new", f"demo-{lang}", "--lang", lang]), 0)
                meta = __import__("tomllib").loads((root / "agents" / f"demo-{lang}" / "agent.toml").read_text())
                self.assertEqual(meta["requires_helpers"], ["result"])
        import build_registry
        with mock.patch.object(build_registry, "ROOT", root):
            (root / "agents" / "demo-bash" / "agent.toml").write_text(
                (root / "agents" / "demo-bash" / "agent.toml").read_text().replace(
                    'tier = "community"', 'tier = "official"') + 'requires_tools = ["rclone"]\nrequires_ops = ["site.list"]\n')
            self.assertEqual(build_registry.main("1.0.0", "abc"), 0)
        registry = json.loads((root / "registry.json").read_text())
        entry = registry["agents"]["demo-bash"]
        self.assertEqual((entry["requiresHelpers"], entry["requiresTools"], entry["requiresOps"]),
                         (["result"], ["rclone"], ["site.list"]))


class ImportCheck(unittest.TestCase):
    def test_unknown_library_helper_is_an_error(self):
        meta = {"version": "1.0.0", "name": "demo"}
        text = ("# wcp-agent: demo\n# wcp-agent-version: 1.0.0\nsys.path.insert(0, agents)\n"
                "from wcp_agent_lib import run, no_such_helper\n")
        errors = check_agents.check_script("demo", meta, text)
        self.assertEqual(len([e for e in errors if "no_such_helper" in e]), 1, errors)

    def test_header_name_must_match(self):
        meta = {"version": "1.0.0", "name": "demo"}
        errors = check_agents.check_script("demo", meta, "# wcp-agent: other\n# wcp-agent-version: 1.0.0\n")
        self.assertTrue(any("'# wcp-agent'" in e for e in errors), errors)


if __name__ == "__main__":
    unittest.main()
