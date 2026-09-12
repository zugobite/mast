"""Private standards: scaffold, validate, register and refuse a busy unregister."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mast import common, export
from mast.common import MastError, ROOT
from mast.standard import check_standard, init_standard, register_standard, unregister_standard
from tests.helpers import isolate_mast_state

EXAMPLE = ROOT / "examples" / "acme-python"


class StandardTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        isolate_mast_state(self, self.root / "state")

    def test_example_standard_is_valid(self) -> None:
        standard, results = check_standard(EXAMPLE)
        self.assertIsNotNone(standard)
        self.assertEqual(standard.name, "acme")
        self.assertTrue(all(r.ok for r in results), [r for r in results if not r.ok])

    def test_init_scaffolds_and_registers(self) -> None:
        target = self.root / "acme-python"
        result = init_standard(target, name="acme")
        self.assertEqual(result["name"], "acme")
        self.assertTrue((target / "standard.json").exists())
        self.assertTrue((target / "packs" / "acme-shared" / "acme.md").exists())
        self.assertTrue((target / "profiles" / "acme.json").exists())
        self.assertIn("acme", common.load_registry()["standards"])

    def test_unknown_pack_fails_the_check(self) -> None:
        target = self.root / "broken"
        init_standard(target, name="broken")
        profile = target / "profiles" / "broken.json"
        profile.write_text(
            json.dumps({"description": "x", "packs": ["core", "no-such-pack"]}) + "\n",
            encoding="utf-8",
        )
        _, results = check_standard(target)
        self.assertTrue(any(not r.ok and "no-such-pack" in (r.hint or "") + r.label for r in results))

    def test_bad_detect_regex_fails_the_check(self) -> None:
        target = self.root / "regex"
        init_standard(target, name="regex")
        manifest = json.loads((target / "standard.json").read_text(encoding="utf-8"))
        manifest["detect"] = [{"profile": "regex", "name": "("}]
        (target / "standard.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        _, results = check_standard(target)
        self.assertTrue(any(not r.ok and "regex" in (r.hint or "") for r in results))

    def test_missing_frontmatter_fails_the_check(self) -> None:
        target = self.root / "docs"
        init_standard(target, name="docs")
        (target / "packs" / "docs-shared" / "docs.md").write_text("# no frontmatter\n", encoding="utf-8")
        _, results = check_standard(target)
        self.assertTrue(any(not r.ok and "frontmatter" in (r.hint or "") for r in results))

    def test_unregister_is_blocked_while_a_project_uses_it(self) -> None:
        std_root = self.root / "acme-std"
        init_standard(std_root, name="acme")
        project = self.root / "app"
        project.mkdir()
        export.export_project(project, standard=str(std_root), profile="acme")
        with self.assertRaises(MastError) as caught:
            unregister_standard("acme")
        self.assertIn("still use", str(caught.exception))

    def test_register_and_unregister_when_idle(self) -> None:
        std_root = self.root / "idle"
        init_standard(std_root, name="idle")
        unregister_standard("idle")
        self.assertNotIn("idle", common.load_registry()["standards"])
        register_standard(std_root)
        self.assertIn("idle", common.load_registry()["standards"])


if __name__ == "__main__":
    unittest.main()
