"""Tests for scripts/check_agents.py. Run: python3 -m unittest discover tests"""
import base64
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_agents  # noqa: E402

LIB = "def hello():\n    return 1\n"


def bundled(version="1.0.0", lib=LIB, name="demo"):
    blob = base64.b64encode(lib.encode()).decode()
    return (
        f"#!/usr/bin/env bash\n# wcp-agent: {name}\n# wcp-agent-version: {version}\n"
        f"python3 - <<'PY'\nimport base64, types\n_agent_lib = types.ModuleType('wcp_agent_lib')\n"
        f"exec(base64.b64decode('{blob}'), _agent_lib.__dict__)\nPY\n"
    )


def agent(script, version="1.0.0", extra=""):
    directory = Path(tempfile.mkdtemp()) / "demo"
    directory.mkdir()
    (directory / "agent.sh").write_text(script)
    (directory / "agent.toml").write_text(
        f'name = "demo"\nversion = "{version}"\ntier = "official"\nscript = "agent.sh"\n'
        f'schedule = "none"\ndefault_schedule = ""\nmin_engine = "0.1.0"\n'
        f'description = "d"\npaths = ["/root/.wcp"]\n{extra}'
    )
    return directory


class Bundle(unittest.TestCase):
    def test_a_matching_bundle_passes(self):
        self.assertEqual(check_agents.check(agent(bundled()), LIB), [])

    def test_a_stale_embedded_library_fails(self):
        errors = check_agents.check(agent(bundled(lib="old\n")), LIB)
        self.assertTrue(any("embedded library" in e for e in errors), errors)

    def test_script_header_version_must_match_agent_toml(self):
        errors = check_agents.check(agent(bundled(version="1.0.1")), LIB)
        self.assertTrue(any("wcp-agent-version" in e for e in errors), errors)

    def test_a_script_without_the_library_is_fine(self):
        script = "#!/usr/bin/env bash\n# wcp-agent: demo\n# wcp-agent-version: 1.0.0\n"
        self.assertEqual(check_agents.check(agent(script), LIB), [])


class Isolation(unittest.TestCase):
    SYSTEMD = 'isolation = "systemd"\nwritable_paths = ["/root/.wcp/logs"]\n'

    def test_cron_is_the_default_and_needs_nothing_else(self):
        for extra in ("", 'isolation = "cron"\n'):
            self.assertEqual(check_agents.check(agent(bundled(), extra=extra), LIB), [])

    def test_systemd_with_writable_paths_passes(self):
        self.assertEqual(check_agents.check(agent(bundled(), extra=self.SYSTEMD), LIB), [])

    def test_unknown_value_fails(self):
        directory = agent(bundled(), extra='isolation = "docker"\n')
        self.assertTrue(check_agents.check(directory, LIB))

    def test_systemd_requires_writable_paths(self):
        errors = check_agents.check(agent(bundled(), extra='isolation = "systemd"\n'), LIB)
        self.assertTrue(any("writable_paths" in e for e in errors), errors)

    def test_writable_paths_are_absolute_and_unquoted(self):
        for bad in ('"/root/a b"', '"relative"', '"/root/../etc"', '"/a\\"b"'):
            extra = f'isolation = "systemd"\nwritable_paths = [{bad}]\n'
            errors = check_agents.check(agent(bundled(), extra=extra), LIB)
            self.assertTrue(any("writable_paths" in e for e in errors), (bad, errors))

    def test_writable_paths_without_systemd_are_rejected(self):
        extra = 'writable_paths = ["/root/.wcp/logs"]\n'
        self.assertTrue(check_agents.check(agent(bundled(), extra=extra), LIB))


if __name__ == "__main__":
    unittest.main()


class Origin(unittest.TestCase):
    def test_an_agent_cannot_declare_its_own_origin(self):
        errors = check_agents.check(agent(bundled(), extra='origin = "first-party"\n'), LIB)
        self.assertTrue(any("origin is set by first-party.json" in e for e in errors), errors)

    def test_first_party_names_must_be_agent_directories(self):
        errors = check_agents.check_first_party({"real"})
        self.assertTrue(any("is not an agent directory" in e for e in errors), errors)

    def test_the_repository_allowlist_is_valid(self):
        directories = {p.name for p in (ROOT / "agents").iterdir() if p.is_dir()}
        self.assertEqual(check_agents.check_first_party(directories), [])
