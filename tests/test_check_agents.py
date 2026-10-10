"""Tests for scripts/check_agents.py. Run: python3 -m unittest discover tests"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_agents  # noqa: E402

FIRST_PARTY = "Website Control Panel"


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


class Library(unittest.TestCase):
    def test_a_plain_import_passes(self):
        self.assertEqual(check_agents.check(agent(bundled())), [])

    def test_an_embedded_blob_fails(self):
        script = bundled() + "exec(base64.b64decode('QQ=='), _agent_lib.__dict__)\n"
        errors = check_agents.check(agent(script))
        self.assertTrue(any("base64" in e for e in errors), errors)

    def test_an_import_without_the_sys_path_line_fails(self):
        script = bundled().replace("sys.path.insert", "# sys.path.insert")
        errors = check_agents.check(agent(script))
        self.assertTrue(any("sys.path" in e for e in errors), errors)

    def test_script_header_version_must_match_agent_toml(self):
        errors = check_agents.check(agent(bundled(version="1.0.1")))
        self.assertTrue(any("wcp-agent-version" in e for e in errors), errors)

    def test_a_script_without_the_library_is_fine(self):
        script = "#!/usr/bin/env bash\n# wcp-agent: demo\n# wcp-agent-version: 1.0.0\n" + LOCK
        self.assertEqual(check_agents.check(agent(script)), [])

    def test_a_script_without_a_lock_fails(self):
        script = "#!/usr/bin/env bash\n# wcp-agent: demo\n# wcp-agent-version: 1.0.0\n"
        errors = check_agents.check(agent(script))
        self.assertTrue(any("rule 4" in e for e in errors), errors)

    def test_each_lock_form_counts(self):
        head = "#!/usr/bin/env bash\n# wcp-agent: demo\n# wcp-agent-version: 1.0.0\n"
        for lock in ('exec "$OPS" agent lock demo -- bash "$0"\n',
                     "fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"):
            self.assertEqual(check_agents.check(agent(head + lock)), [], lock)


class Isolation(unittest.TestCase):
    SYSTEMD = 'isolation = "systemd"\nwritable_paths = ["/root/.wcp/logs"]\n'

    def test_cron_is_the_default_and_needs_nothing_else(self):
        for extra in ("", 'isolation = "cron"\n'):
            self.assertEqual(check_agents.check(agent(bundled(), extra=extra)), [])

    def test_systemd_with_writable_paths_passes(self):
        self.assertEqual(check_agents.check(agent(bundled(), extra=self.SYSTEMD)), [])

    def test_unknown_value_fails(self):
        directory = agent(bundled(), extra='isolation = "docker"\n')
        self.assertTrue(check_agents.check(directory))

    def test_systemd_requires_writable_paths(self):
        errors = check_agents.check(agent(bundled(), extra='isolation = "systemd"\n'))
        self.assertTrue(any("writable_paths" in e for e in errors), errors)

    def test_writable_paths_are_absolute_and_unquoted(self):
        for bad in ('"/root/a b"', '"relative"', '"/root/../etc"', '"/a\\"b"'):
            extra = f'isolation = "systemd"\nwritable_paths = [{bad}]\n'
            errors = check_agents.check(agent(bundled(), extra=extra))
            self.assertTrue(any("writable_paths" in e for e in errors), (bad, errors))

    def test_writable_paths_without_systemd_are_rejected(self):
        extra = 'writable_paths = ["/root/.wcp/logs"]\n'
        self.assertTrue(check_agents.check(agent(bundled(), extra=extra)))


if __name__ == "__main__":
    unittest.main()


class Author(unittest.TestCase):
    def test_any_plain_name_passes(self):
        for name in ("Ada Example", FIRST_PARTY, "ACME s.r.o."):
            self.assertEqual(
                check_agents.check(agent(bundled(), author=f'author = "{name}"\n')), [], name
            )

    def test_the_author_is_required(self):
        errors = check_agents.check(agent(bundled(), author=""))
        self.assertTrue(any("missing key author" in e for e in errors), errors)

    def test_an_empty_padded_or_control_author_fails(self):
        for value in ('""', '" Ada"', '"Ada "', '"Ada\\nExample"', '"' + "x" * 101 + '"'):
            errors = check_agents.check(agent(bundled(), author=f"author = {value}\n"))
            self.assertTrue(any("author must be" in e for e in errors), (value, errors))

    def test_a_non_string_author_fails(self):
        errors = check_agents.check(agent(bundled(), author="author = 7\n"))
        self.assertTrue(any("author must be" in e for e in errors), errors)

    def test_the_project_name_is_the_first_party_author(self):
        self.assertIn(FIRST_PARTY, check_agents.FIRST_PARTY_AUTHORS)
