"""Tests for scripts/build_registry.py. Run: python3 -m unittest discover tests"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_registry  # noqa: E402


def tree(first_party, names=("ours", "theirs")):
    root = Path(tempfile.mkdtemp())
    for name in names:
        directory = root / "agents" / name
        directory.mkdir(parents=True)
        (directory / "agent.sh").write_text("#!/usr/bin/env bash\n")
        (directory / "agent.toml").write_text(
            f'name = "{name}"\nversion = "1.0.0"\ntier = "official"\nscript = "agent.sh"\n'
            'schedule = "none"\ndefault_schedule = ""\nmin_engine = "0.1.0"\n'
            'description = "d"\npaths = ["/root/.wcp"]\n'
        )
    (root / "first-party.json").write_text(json.dumps(first_party))
    return root


class Origin(unittest.TestCase):
    def build(self, root):
        original = build_registry.ROOT
        build_registry.ROOT = root
        try:
            self.assertEqual(build_registry.main("1.0.0", "c"), 0)
        finally:
            build_registry.ROOT = original
        return json.loads((root / "registry.json").read_text())["agents"]

    def test_origin_comes_from_the_allowlist_not_the_manifest(self):
        agents = self.build(tree(["ours"]))
        self.assertEqual(agents["ours"]["origin"], "first-party")
        self.assertEqual(agents["theirs"]["origin"], "third-party")

    def test_an_empty_allowlist_makes_everything_third_party(self):
        agents = self.build(tree([]))
        self.assertEqual({a["origin"] for a in agents.values()}, {"third-party"})


if __name__ == "__main__":
    unittest.main()
