"""Tests for scripts/check_agents.py. Run: python3 -m unittest discover tests"""
import base64
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_agents  # noqa: E402

FIRST_PARTY = "Website Control Panel"
LIB = "def hello():\n    return 1\n"


def bundled(version="1.0.0", lib=LIB, name="demo"):
    blob = base64.b64encode(lib.encode()).decode()
    return (
        f"#!/usr/bin/env bash\n# wcp-agent: {name}\n# wcp-agent-version: {version}\n"
        f"python3 - <<'PY'\nimport base64, types\n_agent_lib = types.ModuleType('wcp_agent_lib')\n"
        f"exec(base64.b64decode('{blob}'), _agent_lib.__dict__)\nPY\n"
    )


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


class Author(unittest.TestCase):
    def test_any_plain_name_passes(self):
        for name in ("Ada Example", FIRST_PARTY, "ACME s.r.o."):
            self.assertEqual(
                check_agents.check(agent(bundled(), author=f'author = "{name}"\n'), LIB), [], name
            )

    def test_the_author_is_required(self):
        errors = check_agents.check(agent(bundled(), author=""), LIB)
        self.assertTrue(any("missing key author" in e for e in errors), errors)

    def test_an_empty_padded_or_control_author_fails(self):
        for value in ('""', '" Ada"', '"Ada "', '"Ada\\nExample"', '"' + "x" * 101 + '"'):
            errors = check_agents.check(agent(bundled(), author=f"author = {value}\n"), LIB)
            self.assertTrue(any("author must be" in e for e in errors), (value, errors))

    def test_a_non_string_author_fails(self):
        errors = check_agents.check(agent(bundled(), author="author = 7\n"), LIB)
        self.assertTrue(any("author must be" in e for e in errors), errors)

    def test_the_project_name_is_the_first_party_author(self):
        self.assertIn(FIRST_PARTY, check_agents.FIRST_PARTY_AUTHORS)
