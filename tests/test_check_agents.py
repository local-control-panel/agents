"""Tests for scripts/check_agents.py. Run: python3 -m unittest discover tests

The validation rules live in the operations engine and are tested there. These
tests cover what this repository adds: finding the engine, failing closed when
it cannot judge, printing its findings, and a few end-to-end cases through a
real binary (skipped when no engine is available; CI builds one).
"""
import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_agents  # noqa: E402

ENGINE = check_agents.find_engine()
needs_engine = unittest.skipUnless(ENGINE, "no ops-engine binary (set OPS_ENGINE)")

LOCK = 'exec 9>"$WCP_DIR/agents/demo.lock"\nflock -n 9 || exit 0\n'


def bundled(version="1.0.0", name="demo"):
    return (
        f"#!/usr/bin/env bash\n# wcp-agent: {name}\n# wcp-agent-version: {version}\n"
        "python3 - <<'PY'\nimport os, sys\nsys.path.insert(0, os.path.join(WCP_DIR, \"agents\"))\n"
        "from wcp_agent_lib import run\nPY\n"
    ) + LOCK


def agent(script, version="1.0.0", extra="", author='author = "Ada Example"\n'):
    directory = Path(tempfile.mkdtemp()) / "demo"
    directory.mkdir()
    (directory / "agent.sh").write_text(script)
    (directory / "agent.toml").write_text(
        f'name = "demo"\nversion = "{version}"\ntier = "official"\n{author}script = "agent.sh"\n'
        f'schedule = "none"\ndefault_schedule = ""\nmin_engine = "0.1.0"\n'
        f'description = "d"\npaths = ["/root/.wcp"]\n{extra}'
    )
    return directory


def fake_engine(body: str) -> str:
    """An executable that stands in for ops-engine with the given shell body."""
    path = Path(tempfile.mkdtemp()) / "ops-engine"
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def answer(**result):
    base = {"valid": True, "agent": "demo", "problems": [], "warnings": []}
    base.update(result)
    return json.dumps({"protocolVersion": 1, "operation": "agent.validate", "ok": True,
                       "result": base, "warnings": []})


def says(text: str) -> str:
    return fake_engine(f"cat <<'EOF'\n{text}\nEOF\n")


class FindingTheEngine(unittest.TestCase):
    def test_explicit_path_then_environment_then_path(self):
        a, b = fake_engine("exit 0\n"), fake_engine("exit 0\n")
        with mock.patch.dict(os.environ, {"OPS_ENGINE": b}):
            self.assertEqual(check_agents.find_engine(a), str(Path(a).resolve()))
            self.assertEqual(check_agents.find_engine(), str(Path(b).resolve()))

    def test_a_missing_or_non_executable_engine_is_not_found(self):
        plain = Path(tempfile.mkdtemp()) / "ops-engine"
        plain.write_text("x")
        with mock.patch.dict(os.environ, {"OPS_ENGINE": str(plain), "PATH": "/nonexistent"}):
            self.assertIsNone(check_agents.find_engine())
            self.assertIsNone(check_agents.find_engine("/nonexistent/ops-engine"))

    def test_without_an_engine_nothing_passes(self):
        directory = agent(bundled())
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"PATH": "/nonexistent"}, clear=False), \
                mock.patch.object(check_agents, "find_engine", return_value=None), \
                contextlib.redirect_stderr(err):
            code = check_agents.check_all([directory], None)
        self.assertEqual(code, 2)
        self.assertIn("no ops-engine binary", err.getvalue())


class FailsClosed(unittest.TestCase):
    """Whatever the engine does, only a well-formed `valid: true` is a pass."""

    def run_with(self, engine):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = check_agents.check_all([agent(bundled())], engine)
        return code, err.getvalue()

    def test_a_valid_answer_passes(self):
        self.assertEqual(self.run_with(says(answer()))[0], 0)

    def test_problems_are_printed_with_the_agent_name_and_fail(self):
        problem = {"code": "no_lock", "message": "no lock taken"}
        code, out = self.run_with(says(answer(valid=False, problems=[problem])))
        self.assertEqual(code, 1)
        self.assertIn("demo: no lock taken", out)

    def test_warnings_are_printed_and_do_not_fail(self):
        warning = {"code": "no_heartbeat", "message": "no heartbeat"}
        code, out = self.run_with(says(answer(warnings=[warning])))
        self.assertEqual(code, 0)
        self.assertIn("warning: demo: no heartbeat", out)

    def test_every_unusable_answer_is_an_error_not_a_pass(self):
        bad = {
            "not json": says("garbage"),
            "empty": fake_engine("exit 0\n"),
            "old engine without the command": fake_engine("echo 'unrecognized subcommand' >&2; exit 2\n"),
            "ok false": says(json.dumps({"operation": "agent.validate", "ok": False,
                                         "error": {"code": "INVALID_INPUT", "message": "no"}})),
            "wrong operation": says(json.dumps({"operation": "version", "ok": True, "result": {}})),
            "no result": says(json.dumps({"operation": "agent.validate", "ok": True})),
            "valid with problems": says(answer(valid=True, problems=[{"code": "x", "message": "m"}])),
            "invalid without problems": says(answer(valid=False)),
            "valid not a bool": says(answer(valid="yes")),
        }
        for label, engine in bad.items():
            code, out = self.run_with(engine)
            self.assertEqual(code, 2, (label, out))

    def test_a_manifest_that_is_not_toml_is_a_problem(self):
        directory = agent(bundled())
        (directory / "agent.toml").write_text("name = ")
        errors = check_agents.check(directory, says(answer()))
        self.assertTrue(any("not valid TOML" in e for e in errors), errors)

    def test_a_missing_manifest_is_a_problem(self):
        directory = agent(bundled())
        (directory / "agent.toml").unlink()
        self.assertEqual(check_agents.check(directory, says(answer())), ["demo: agent.toml missing"])

    def test_the_engine_gets_the_manifest_as_json_and_the_directory(self):
        record = Path(tempfile.mkdtemp()) / "args"
        engine = fake_engine(
            f'echo "$@" > {record}\nwhile [ $# -gt 0 ]; do [ "$1" = --manifest-file ] && cp "$2" {record}.json; shift; done\n'
            f"cat <<'EOF'\n{answer()}\nEOF\n")
        directory = agent(bundled())
        self.assertEqual(check_agents.check(directory, engine), [])
        self.assertIn("agent validate --directory", record.read_text())
        self.assertEqual(json.loads(Path(f"{record}.json").read_text())["name"], "demo")


class Library(unittest.TestCase):
    """The one rule kept here: names imported from this repository's library exist."""

    def test_an_unknown_helper_is_an_error(self):
        text = "from wcp_agent_lib import run, no_such_helper\n"
        errors = check_agents.library_errors("demo", text)
        self.assertEqual(errors, ["demo: wcp_agent_lib has no 'no_such_helper'"])

    def test_known_helpers_pass(self):
        self.assertEqual(check_agents.library_errors("demo", "from wcp_agent_lib import run, notify\n"), [])

    def test_it_runs_after_a_valid_engine_answer(self):
        directory = agent(bundled().replace("import run", "import run, nope"))
        errors = check_agents.check(directory, says(answer()))
        self.assertEqual(errors, ["demo: wcp_agent_lib has no 'nope'"])


@needs_engine
class WithTheRealEngine(unittest.TestCase):
    def test_a_plain_agent_passes(self):
        self.assertEqual(check_agents.check(agent(bundled())), [])

    def test_the_header_version_must_match(self):
        errors = check_agents.check(agent(bundled(version="1.0.1")))
        self.assertTrue(any("wcp-agent-version" in e for e in errors), errors)

    def test_a_script_without_a_lock_fails(self):
        script = "#!/usr/bin/env bash\n# wcp-agent: demo\n# wcp-agent-version: 1.0.0\n"
        errors = check_agents.check(agent(script))
        self.assertTrue(any("rule 4" in e for e in errors), errors)

    def test_the_author_is_required_and_plain(self):
        errors = check_agents.check(agent(bundled(), author=""))
        self.assertTrue(any("missing key author" in e for e in errors), errors)
        errors = check_agents.check(agent(bundled(), author='author = " Ada"\n'))
        self.assertTrue(any("author must be" in e for e in errors), errors)

    def test_isolation_rules_come_from_the_engine(self):


        extra = 'isolation = "systemd"\nwritable_paths = ["/root/.wcp/logs"]\n'
        errors = check_agents.check(agent(bundled(), extra=extra))
        self.assertTrue(any("schedule" in e for e in errors), errors)
        errors = check_agents.check(agent(bundled(), extra='writable_paths = ["/root/.wcp/logs"]\n'))
        self.assertTrue(any("writable_paths" in e for e in errors), errors)

    def test_every_agent_in_this_repository_passes(self):
        for directory in sorted((ROOT / "agents").iterdir()):
            self.assertEqual(check_agents.check(directory), [], directory.name)


if __name__ == "__main__":
    unittest.main()
