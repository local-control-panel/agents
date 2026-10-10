"""Tests for scripts/wcp_agent.py and the agent API in lib/wcp_agent_lib.py.
Run: python3 -m unittest discover tests"""
import contextlib
import io
import json
import os
import sys
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


class Library(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        env = {"WCP_DIR": self.dir}
        patch = mock.patch.dict(os.environ, env)
        patch.start()
        self.addCleanup(patch.stop)
        os.environ.pop("WCP_DRY_RUN", None)

    def test_heartbeat_is_the_documented_line(self):
        path = lib.write_heartbeat("a", 2, now=100)
        self.assertEqual(json.loads(Path(path).read_text()), {"ts": 100, "exit_code": 2})
        self.assertEqual(path, os.path.join(self.dir, "agents", "a.heartbeat"))

    def test_read_conf(self):
        conf = Path(self.dir) / "c.conf"
        conf.write_text('# c\nA=1\nexport B="two words"\nC=\'x\'\nbad line\n=skip\n')
        self.assertEqual(lib.read_conf(str(conf)), {"A": "1", "B": "two words", "C": "x"})
        self.assertEqual(lib.read_conf(str(conf) + ".missing"), {})
        self.assertIsNone(lib.read_conf(str(conf) + ".missing", default=None) or None)

    def test_list_sites_merges_manifests_and_directories(self):
        manifests, www = Path(self.dir) / "m", Path(self.dir) / "www"
        manifests.mkdir()
        (www / "b.example").mkdir(parents=True)
        (www / "a.example").mkdir()
        (www / ".hidden").mkdir()
        (www / "file").write_text("x")
        (manifests / "1.json").write_text(json.dumps(
            {"siteId": "id-1", "domain": "a.example", "contentRoot": "sites/id-1/current", "siteUser": "u"}))
        (manifests / "2.json").write_text(json.dumps({"domain": "../evil"}))
        (manifests / "3.json").write_text("not json")
        sites = lib.list_sites(str(manifests), str(www))
        self.assertEqual([s["domain"] for s in sites], ["a.example", "b.example"])
        self.assertEqual(sites[0]["site_id"], "id-1")
        self.assertEqual(sites[0]["source"], "manifest")
        self.assertEqual(sites[1]["source"], "filesystem")
        self.assertEqual(lib.list_sites(str(manifests) + "x", str(www) + "x"), [])

    def stub_engine(self, body):
        path = Path(self.dir) / "engine"
        path.write_text("#!/bin/sh\necho \"$@\" >> \"$0.calls\"\n" + body)
        path.chmod(0o755)
        os.environ["OPS_ENGINE"] = str(path)
        self.addCleanup(os.environ.pop, "OPS_ENGINE", None)
        return path

    def test_engine_call_parses_and_survives_failures(self):
        self.stub_engine('echo \'{"ok":true,"result":{"operations":["version","site.list"]}}\'\n')
        self.assertEqual(lib.engine_operations(), {"version", "site.list"})
        self.stub_engine("echo garbage\n")
        self.assertEqual(lib.engine_call(["version"])["error"]["code"], "ENGINE_BAD_RESPONSE")
        os.environ["OPS_ENGINE"] = str(Path(self.dir) / "missing")
        self.assertEqual(lib.engine_call(["version"])["error"]["code"], "ENGINE_UNAVAILABLE")
        self.assertEqual(lib.engine_operations(), set())

    def test_dry_run_blocks_mutations_but_not_reads(self):
        engine = self.stub_engine('echo \'{"ok":true,"result":null}\'\n')
        with mock.patch.dict(os.environ, {"WCP_DRY_RUN": "1"}):
            blocked = lib.engine_call(["site", "deploy", "--site-id", "x"])
            self.assertTrue(blocked["dryRun"])
            self.assertFalse(Path(str(engine) + ".calls").exists())
            lib.engine_call(["operation", "status", "--site-id", "x"])
            self.assertTrue(Path(str(engine) + ".calls").exists())

    def test_emit_result(self):
        entry = lib.emit_result("a", "warn", "careful", count=3)
        line = json.loads(Path(lib.log_file("a")).read_text())
        self.assertEqual(line, entry)
        self.assertEqual((line["status"], line["count"]), ("warn", 3))
        with self.assertRaises(ValueError):
            lib.emit_result("a", "great", "x")
        with mock.patch.dict(os.environ, {"WCP_DRY_RUN": "1"}):
            lib.emit_result("b", "ok", "x")
        self.assertFalse(Path(lib.log_file("b")).exists())


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

    def test_new_helpers_are_known(self):
        for name in ("list_sites", "engine_call", "emit_result", "write_heartbeat", "dry_run"):
            self.assertIn(name, check_agents.library_names())


if __name__ == "__main__":
    unittest.main()
