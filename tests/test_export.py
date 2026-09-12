"""Exporting into a project: what lands, what is preserved, and what a removal undoes."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mast import common, export
from mast.common import MastError


class ExportTestCase(unittest.TestCase):
    """Each test gets its own project and its own registry."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.project = root / "demo"
        self.project.mkdir()
        self.state = root / "state"
        self._patches = [
            mock.patch.object(common, "STATE", self.state),
            mock.patch.object(common, "REGISTRY", self.state / "registry.json"),
        ]
        for patch in self._patches:
            patch.start()
        self.addCleanup(self._tmp.cleanup)
        for patch in self._patches:
            self.addCleanup(patch.stop)

    def write(self, rel: str, content: str) -> Path:
        file = self.project / rel
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding="utf-8")
        return file

    def read(self, rel: str) -> str:
        return (self.project / rel).read_text(encoding="utf-8")


class DetectionTest(ExportTestCase):
    def test_detects_django(self) -> None:
        self.write("pyproject.toml", '[project]\nname = "x"\ndependencies = ["Django>=5.0"]\n')
        self.assertEqual(export.detect_profile(self.project), "django")

    def test_detects_fastapi(self) -> None:
        self.write("pyproject.toml", '[project]\nname = "x"\ndependencies = ["fastapi", "uvicorn"]\n')
        self.assertEqual(export.detect_profile(self.project), "fastapi")

    def test_detects_a_web_service_from_requirements(self) -> None:
        self.write("requirements.txt", "flask==3.0.0\ngunicorn\n")
        self.assertEqual(export.detect_profile(self.project), "web-service")

    def test_detects_data_work(self) -> None:
        self.write("requirements.txt", "pandas\nscikit-learn\n")
        self.assertEqual(export.detect_profile(self.project), "data")

    def test_detects_a_library(self) -> None:
        self.write("pyproject.toml", '[build-system]\nrequires = ["setuptools"]\n\n[project]\nname = "x"\n')
        self.assertEqual(export.detect_profile(self.project), "lib")

    def test_falls_back_to_base(self) -> None:
        self.assertEqual(export.detect_profile(self.project), "base")

    def test_reads_poetry_and_optional_dependencies(self) -> None:
        self.write(
            "pyproject.toml",
            '[tool.poetry.dependencies]\npython = "^3.11"\nDjango = "^5.0"\n',
        )
        self.assertIn("django", export.read_dependencies(self.project))


class ExportedFilesTest(ExportTestCase):
    def test_writes_everything_a_project_needs(self) -> None:
        self.write("pyproject.toml", '[project]\nname = "x"\ndependencies = ["fastapi"]\n')
        result = export.export_project(self.project)
        self.assertEqual(result.profile, "fastapi")
        for rel in (
            ".claude/standards/packs/core/coding.md",
            ".claude/standards/packs/fastapi/fastapi.md",
            ".claude/standards/rules.json",
            ".claude/standards/manifest.json",
            ".claude/standards/hooks/pre_tool.py",
            ".claude/standards/hooks/analysis/__init__.py",
            ".claude/standards.config.json",
            ".claude/settings.json",
            ".claude/agents/reviewer.md",
            ".claude/agents/bug-hunter.md",
            ".claude/commands/qa.md",
            ".claude/commands/hunt.md",
            "CLAUDE.md",
        ):
            with self.subTest(rel=rel):
                self.assertTrue((self.project / rel).exists(), rel)

    def test_vendored_analyzer_runs_standalone(self) -> None:
        import subprocess
        import sys

        export.export_project(self.project)
        hooks = self.project / ".claude" / "standards" / "hooks"
        probe = subprocess.run(
            [sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); import analysis; print(len(analysis.REGISTRY))", str(hooks)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(probe.returncode, 0, probe.stderr)
        self.assertGreater(int(probe.stdout.strip()), 50)

    def test_claude_md_keeps_the_project_content(self) -> None:
        self.write("CLAUDE.md", "# demo\n\nOur own notes.\n")
        export.export_project(self.project)
        text = self.read("CLAUDE.md")
        self.assertIn("Our own notes.", text)
        self.assertIn("<!-- mast:start", text)
        self.assertIn("@.claude/standards/packs/core/coding.md", text)

    def test_a_second_export_replaces_only_the_managed_block(self) -> None:
        export.export_project(self.project)
        (self.project / "CLAUDE.md").write_text(
            self.read("CLAUDE.md") + "\n## Later addition\n", encoding="utf-8"
        )
        export.export_project(self.project)
        text = self.read("CLAUDE.md")
        self.assertEqual(text.count("<!-- mast:start"), 1)
        self.assertIn("## Later addition", text)

    def test_settings_keep_other_hooks(self) -> None:
        self.write(
            ".claude/settings.json",
            json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}]}}),
        )
        export.export_project(self.project)
        settings = json.loads(self.read(".claude/settings.json"))
        commands = [h["command"] for entry in settings["hooks"]["Stop"] for h in entry["hooks"]]
        self.assertIn("echo mine", commands)
        self.assertTrue(any(".claude/standards/hooks/stop.py" in c for c in commands))
        self.assertEqual(len(settings["hooks"]), 5)

    def test_syncing_twice_does_not_duplicate_hooks(self) -> None:
        export.export_project(self.project)
        export.export_project(self.project)
        settings = json.loads(self.read(".claude/settings.json"))
        entries = [h for entry in settings["hooks"]["PreToolUse"] for h in entry["hooks"]]
        self.assertEqual(len(entries), 1)

    def test_project_owned_files_are_never_overwritten(self) -> None:
        export.export_project(self.project)
        config_path = self.project / ".claude" / "standards.config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["lint"] = None
        config["analysis"]["maxComplexity"] = 99
        config_path.write_text(json.dumps(config), encoding="utf-8")
        self.write(".claude/standards/local-wins.md", "# mine\n")
        export.export_project(self.project)
        after = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertIsNone(after["lint"])
        self.assertEqual(after["analysis"]["maxComplexity"], 99)
        self.assertEqual(self.read(".claude/standards/local-wins.md"), "# mine\n")

    def test_config_gains_keys_added_by_a_newer_version(self) -> None:
        export.export_project(self.project)
        config_path = self.project / ".claude" / "standards.config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        del config["analysis"]
        config_path.write_text(json.dumps(config), encoding="utf-8")
        export.export_project(self.project)
        self.assertIn("analysis", json.loads(config_path.read_text(encoding="utf-8")))

    def test_detected_config_wires_up_the_projects_own_tools(self) -> None:
        self.write("pyproject.toml", '[project]\nname = "x"\ndependencies = ["ruff", "pytest", "mypy"]\n')
        (self.project / "uv.lock").write_text("", encoding="utf-8")
        export.export_project(self.project)
        config = json.loads(self.read(".claude/standards.config.json"))
        self.assertEqual(config["format"]["command"], "uv run ruff format {file}")
        self.assertIn("uv run pytest", config["qa"]["relatedTests"]["command"])
        self.assertEqual(config["commands"]["typecheck"], "uv run mypy .")

    def test_dry_run_writes_nothing(self) -> None:
        result = export.export_project(self.project, dry_run=True)
        self.assertTrue(result.actions)
        self.assertFalse((self.project / ".claude").exists())
        self.assertFalse((self.project / "CLAUDE.md").exists())

    def test_rejects_a_missing_directory(self) -> None:
        with self.assertRaises(MastError):
            export.export_project(self.project / "nope")

    def test_gitignores_the_learning_data(self) -> None:
        export.export_project(self.project)
        self.assertIn(".claude/standards/learning/", self.read(".gitignore"))

    def test_remove_undoes_the_managed_parts(self) -> None:
        self.write("CLAUDE.md", "# demo\n\nOur own notes.\n")
        export.export_project(self.project)
        _, removed = export.remove_project(self.project)
        self.assertIn("Our own notes.", self.read("CLAUDE.md"))
        self.assertNotIn("<!-- mast:start", self.read("CLAUDE.md"))
        self.assertFalse((self.project / ".claude" / "standards" / "hooks").exists())
        self.assertFalse((self.project / ".claude" / "agents" / "reviewer.md").exists())
        self.assertTrue((self.project / ".claude" / "standards.config.json").exists())
        self.assertTrue(removed)
        settings = json.loads(self.read(".claude/settings.json"))
        self.assertNotIn("hooks", settings)

    def test_purge_also_removes_the_project_config(self) -> None:
        export.export_project(self.project)
        export.remove_project(self.project, purge=True)
        self.assertFalse((self.project / ".claude" / "standards.config.json").exists())

    def test_registry_tracks_the_project(self) -> None:
        export.export_project(self.project)
        registry = common.load_registry()
        self.assertIn(str(self.project.resolve()), registry["projects"])
        export.remove_project(self.project)
        self.assertNotIn(str(self.project.resolve()), common.load_registry()["projects"])

    def test_manifest_hash_detects_drift(self) -> None:
        export.export_project(self.project)
        manifest = json.loads(self.read(".claude/standards/manifest.json"))
        profile = common.load_profile(manifest["profile"], None)
        self.assertEqual(manifest["hash"], common.source_hash(profile, None))

    def test_cursor_mirror_is_optional(self) -> None:
        export.export_project(self.project, cursor=True)
        mirrored = list((self.project / ".cursor" / "rules").glob("mast-*.mdc"))
        self.assertTrue(mirrored)


class MergeTest(unittest.TestCase):
    def test_objects_merge_recursively(self) -> None:
        merged = export.deep_merge({"a": {"b": 1, "c": 2}}, {"a": {"c": 3}})
        self.assertEqual(merged, {"a": {"b": 1, "c": 3}})

    def test_named_list_entries_merge_by_name(self) -> None:
        merged = export.deep_merge(
            {"stopChecks": [{"name": "typecheck", "enabled": False}]},
            {"stopChecks": [{"name": "typecheck", "enabled": True}, {"name": "audit", "command": "x"}]},
        )
        self.assertEqual(merged["stopChecks"][0], {"name": "typecheck", "enabled": True})
        self.assertEqual(merged["stopChecks"][1]["name"], "audit")

    def test_plain_lists_union_without_duplicates(self) -> None:
        self.assertEqual(export.deep_merge({"a": [1, 2]}, {"a": [2, 3]}), {"a": [1, 2, 3]})


if __name__ == "__main__":
    unittest.main()
