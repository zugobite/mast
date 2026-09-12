"""The command line: exit codes, output formats and the settings it picks up."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from mast import export
from mast.cli import main
from mast.common import ROOT
from mast.config import find_project_root, load_settings
from mast.standard import init_standard
from tests.helpers import isolate_mast_state


class CliTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def write(self, rel: str, content: str) -> Path:
        file = self.root / rel
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding="utf-8")
        return file

    def run_cli(self, *argv: str) -> tuple[int, str]:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main(list(argv))
        return code, buffer.getvalue()


class CheckCommandTest(CliTestCase):
    BAD = "import os\n\n\ndef f(cmd, items=[]):\n    os.system(cmd)\n    return items\n"
    GOOD = "def f(items: list[int]) -> int:\n    return sum(items)\n"

    def test_fails_on_high_findings_by_default(self) -> None:
        path = self.write("src/bad.py", self.BAD)
        code, output = self.run_cli("check", str(path))
        self.assertEqual(code, 1)
        self.assertIn("bug/mutable-default", output)
        self.assertIn("sec/os-system", output)

    def test_passes_on_clean_code(self) -> None:
        path = self.write("src/good.py", self.GOOD)
        code, output = self.run_cli("check", str(path))
        self.assertEqual(code, 0)
        self.assertIn("No findings", output)

    def test_fail_on_none_always_succeeds(self) -> None:
        path = self.write("src/bad.py", self.BAD)
        self.assertEqual(self.run_cli("check", str(path), "--fail-on", "none")[0], 0)

    def test_fail_on_low_catches_everything(self) -> None:
        path = self.write("src/style.py", "def f(rows):\n    return [r for r in rows]\n")
        self.assertEqual(self.run_cli("check", str(path), "--fail-on", "low")[0], 1)
        self.assertEqual(self.run_cli("check", str(path), "--fail-on", "high")[0], 0)

    def test_json_format(self) -> None:
        path = self.write("src/bad.py", self.BAD)
        code, output = self.run_cli("check", str(path), "--format", "json")
        payload = json.loads(output)
        self.assertEqual(code, 1)
        self.assertIn("sec/os-system", [f["check"] for f in payload["findings"]])
        self.assertEqual(payload["files"], 1)

    def test_sarif_format(self) -> None:
        path = self.write("src/bad.py", self.BAD)
        _, output = self.run_cli("check", str(path), "--format", "sarif")
        document = json.loads(output)
        self.assertEqual(document["runs"][0]["tool"]["driver"]["name"], "mast")

    def test_select_and_ignore(self) -> None:
        path = self.write("src/bad.py", self.BAD)
        _, output = self.run_cli("check", str(path), "--select", "security", "--format", "json")
        self.assertEqual({f["category"] for f in json.loads(output)["findings"]}, {"security"})
        _, output = self.run_cli("check", str(path), "--ignore", "sec/os-system", "--format", "json")
        self.assertNotIn("sec/os-system", [f["check"] for f in json.loads(output)["findings"]])

    def test_unknown_check_is_reported_not_crashed(self) -> None:
        path = self.write("src/bad.py", self.BAD)
        self.assertEqual(self.run_cli("check", str(path), "--select", "nope")[0], 1)

    def test_explain_flag_includes_the_reasoning(self) -> None:
        path = self.write("src/bad.py", self.BAD)
        _, output = self.run_cli("check", str(path), "--explain")
        self.assertIn("why:", output)
        self.assertIn("fix:", output)

    def test_reads_settings_from_pyproject(self) -> None:
        self.write("pyproject.toml", "[tool.mast.analysis]\nrequireTypeHints = false\n")
        path = self.write("src/untyped.py", "def f(rows):\n    return rows\n")
        _, output = self.run_cli("check", str(path), "--format", "json")
        self.assertNotIn("api/missing-type-hints", [f["check"] for f in json.loads(output)["findings"]])

    def test_reads_settings_from_the_project_config(self) -> None:
        self.write(".claude/standards.config.json", json.dumps({"analysis": {"maxFunctionLines": 3}}))
        body = "\n".join(f"    x{i} = {i}" for i in range(8))
        path = self.write("src/long.py", f"def f() -> int:\n{body}\n    return 0\n")
        _, output = self.run_cli("check", str(path), "--format", "json")
        self.assertIn("structure/long-function", [f["check"] for f in json.loads(output)["findings"]])

    def test_explicit_config_file_wins(self) -> None:
        self.write("analysis.json", json.dumps({"select": ["security"]}))
        path = self.write("src/bad.py", self.BAD)
        _, output = self.run_cli("check", str(path), "--config", str(self.root / "analysis.json"), "--format", "json")
        self.assertEqual({f["category"] for f in json.loads(output)["findings"]}, {"security"})


class MetaCommandTest(CliTestCase):
    def test_checks_lists_every_check(self) -> None:
        code, output = self.run_cli("checks")
        self.assertEqual(code, 0)
        self.assertIn("bug/mutable-default", output)
        self.assertIn("check(s)", output)

    def test_checks_can_be_filtered_and_serialised(self) -> None:
        _, output = self.run_cli("checks", "--category", "security", "--format", "json")
        payload = json.loads(output)
        self.assertTrue(payload)
        self.assertEqual({c["category"] for c in payload}, {"security"})

    def test_explain_prints_why_and_how(self) -> None:
        code, output = self.run_cli("explain", "bug/mutable-default")
        self.assertEqual(code, 0)
        self.assertIn("Why it matters", output)
        self.assertIn("How to fix it", output)

    def test_explain_accepts_a_category(self) -> None:
        _, output = self.run_cli("explain", "security")
        self.assertIn("sec/sql-injection", output)

    def test_explain_rejects_an_unknown_id(self) -> None:
        self.assertEqual(self.run_cli("explain", "bug/not-real")[0], 1)

    def test_no_command_prints_help(self) -> None:
        code, output = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("usage: mast", output)


class ConfigDiscoveryTest(CliTestCase):
    def test_finds_the_project_root_from_a_nested_path(self) -> None:
        self.write("pyproject.toml", "[project]\nname = 'x'\n")
        nested = self.write("src/pkg/module.py", "x = 1\n")
        self.assertEqual(find_project_root(nested), self.root.resolve())

    def test_prefers_the_mast_config_over_pyproject(self) -> None:
        self.write("pyproject.toml", "[tool.mast.analysis]\nmaxComplexity = 99\n")
        self.write(".claude/standards.config.json", json.dumps({"analysis": {"maxComplexity": 7}}))
        settings, source = load_settings(self.root)
        self.assertEqual(settings.max_complexity, 7)
        self.assertEqual(source, ".claude/standards.config.json")

    def test_defaults_when_nothing_is_configured(self) -> None:
        settings, source = load_settings(self.root)
        self.assertIsNone(source)
        self.assertEqual(settings.max_complexity, 10)


class IsolatedCliTest(CliTestCase):
    """Export / doctor / learn / standard commands that touch ~/.mast."""

    def setUp(self) -> None:
        super().setUp()
        isolate_mast_state(self, self.root / "state")

    def test_list_shows_the_core(self) -> None:
        code, output = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertIn("Core", output)
        self.assertIn("analyzer checks", output)

    def test_status_and_sync_with_no_projects(self) -> None:
        code, output = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertIn("No exported projects", output)
        code, output = self.run_cli("sync")
        self.assertEqual(code, 0)
        self.assertIn("No exported projects", output)

    def test_export_doctor_status_sync_remove(self) -> None:
        project = self.root / "svc"
        project.mkdir()
        (project / "pyproject.toml").write_text('[project]\nname = "svc"\n', encoding="utf-8")
        code, output = self.run_cli("export", str(project), "-y")
        self.assertEqual(code, 0, output)
        self.assertTrue((project / ".claude" / "standards" / "manifest.json").exists())

        code, output = self.run_cli("doctor", str(project))
        self.assertEqual(code, 0, output)
        self.assertIn("All good", output)

        code, output = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertIn("svc", output)

        code, output = self.run_cli("sync", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("svc", output)

        code, output = self.run_cli("remove", str(project), "-y")
        self.assertEqual(code, 0, output)
        self.assertFalse((project / ".claude" / "standards" / "hooks").exists())

    def test_standard_check_and_list(self) -> None:
        code, output = self.run_cli("standard", "check", str(ROOT / "examples" / "acme-python"))
        self.assertEqual(code, 0, output)
        self.assertIn("Standard is valid", output)
        code, output = self.run_cli("standard", "list")
        self.assertEqual(code, 0)
        self.assertIn("No standards registered", output)

    def test_learn_promote_reject(self) -> None:
        std = self.root / "acme-std"
        init_standard(std, name="acme")
        project = self.root / "billing"
        project.mkdir()
        export.export_project(project, standard=str(std), profile="acme")
        learn = project / ".claude" / "standards" / "learning"
        learn.mkdir(parents=True, exist_ok=True)
        (learn / "candidates.jsonl").write_text(
            json.dumps(
                {
                    "id": "ab12cd34",
                    "kind": "grievance",
                    "scope": "global",
                    "lesson": "Never float clinic money",
                    "projectDir": str(project.resolve()),
                }
            )
            + "\n",
            encoding="utf-8",
        )
        code, output = self.run_cli("learn")
        self.assertEqual(code, 0, output)
        self.assertIn("ab12cd34", output)
        code, output = self.run_cli("promote", "ab12cd34", "--pack", "acme-shared")
        self.assertEqual(code, 0, output)
        wins = (std / "packs" / "acme-shared" / "grievances.md").read_text(encoding="utf-8")
        self.assertIn("Never float clinic money", wins)

        (learn / "candidates.jsonl").write_text(
            json.dumps(
                {
                    "id": "ef56gh78",
                    "kind": "win",
                    "scope": "project",
                    "lesson": "Reuse the tenant client",
                    "projectDir": str(project.resolve()),
                }
            )
            + "\n",
            encoding="utf-8",
        )
        code, output = self.run_cli("reject", "ef56gh78")
        self.assertEqual(code, 0, output)
        self.assertIn("dismissed", output)
        code, output = self.run_cli("learn")
        self.assertEqual(code, 0)
        self.assertNotIn("ef56gh78", output)


if __name__ == "__main__":
    unittest.main()

