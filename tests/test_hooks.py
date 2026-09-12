"""The hooks, run the way Claude Code runs them: a JSON payload on stdin, JSON on stdout."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mast import common, export


class HookTestCase(unittest.TestCase):
    """One exported git project per test class, driven through the real hook scripts."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.project = root / "demo"
        (cls.project / "src" / "shop").mkdir(parents=True)
        (cls.project / "tests").mkdir()
        (cls.project / "pyproject.toml").write_text('[project]\nname = "demo"\ndependencies = ["fastapi"]\n', encoding="utf-8")
        (cls.project / "src" / "shop" / "orders.py").write_text(
            "import time\n\n\nasync def total(rows=[]):\n    time.sleep(1)\n    return rows\n", encoding="utf-8"
        )
        (cls.project / "tests" / "test_orders.py").write_text("def test_total():\n    assert True\n", encoding="utf-8")
        for args in (["init", "-q"], ["config", "user.email", "t@example.com"], ["config", "user.name", "T"]):
            subprocess.run(["git", *args], cwd=cls.project, check=True, capture_output=True)
        with mock.patch.object(common, "STATE", root / "state"), mock.patch.object(common, "REGISTRY", root / "state" / "registry.json"):
            export.export_project(cls.project)
        subprocess.run(["git", "add", "-A"], cwd=cls.project, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "chore: initial"], cwd=cls.project, check=True, capture_output=True)
        cls.hooks = cls.project / ".claude" / "standards" / "hooks"

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def run_hook(self, name: str, payload: dict, *, log: bool = False) -> dict:
        env = {
            "CLAUDE_PROJECT_DIR": str(self.project),
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(self.project),
        }
        if not log:
            env["MAST_NO_LOG"] = "1"
        result = subprocess.run(
            [sys.executable, str(self.hooks / name)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 0, f"{name} failed: {result.stderr}")
        self.stderr = result.stderr
        if not result.stdout.strip():
            return {}
        return json.loads(result.stdout)

    @staticmethod
    def denial(output: dict) -> str | None:
        hook = output.get("hookSpecificOutput") or {}
        if hook.get("permissionDecision") == "deny":
            return hook["permissionDecisionReason"]
        return None

    @staticmethod
    def feedback(output: dict) -> str:
        return output.get("reason") or (output.get("hookSpecificOutput") or {}).get("additionalContext") or ""


class PreToolTest(HookTestCase):
    def test_blocks_a_write_with_a_high_severity_finding(self) -> None:
        output = self.run_hook(
            "pre_tool.py",
            {"tool_name": "Write", "tool_input": {"file_path": "src/shop/new.py", "content": "def f(items=[]):\n    return items\n"}},
        )
        reason = self.denial(output)
        self.assertIsNotNone(reason)
        self.assertIn("bug/mutable-default", reason)

    def test_allows_a_clean_write(self) -> None:
        output = self.run_hook(
            "pre_tool.py",
            {"tool_name": "Write", "tool_input": {"file_path": "src/shop/new.py", "content": "def f(items: list[int]) -> int:\n    return sum(items)\n"}},
        )
        self.assertEqual(output, {})

    def test_blocks_a_text_rule_violation(self) -> None:
        output = self.run_hook(
            "pre_tool.py",
            {"tool_name": "Edit", "tool_input": {"file_path": "src/shop/orders.py", "new_string": "    breakpoint()"}},
        )
        self.assertIn("core/no-debugger", self.denial(output) or "")

    def test_blocks_writes_to_managed_files(self) -> None:
        output = self.run_hook(
            "pre_tool.py",
            {"tool_name": "Write", "tool_input": {"file_path": ".claude/standards/rules.json", "content": "[]"}},
        )
        self.assertIn("managed by mast", self.denial(output) or "")

    def test_blocks_writes_to_dotenv(self) -> None:
        output = self.run_hook("pre_tool.py", {"tool_name": "Write", "tool_input": {"file_path": ".env", "content": "A=1"}})
        self.assertIn("must not write .env", self.denial(output) or "")

    def test_allows_dotenv_example(self) -> None:
        output = self.run_hook("pre_tool.py", {"tool_name": "Write", "tool_input": {"file_path": ".env.example", "content": "A=\n"}})
        self.assertEqual(output, {})

    def test_blocks_dangerous_shell_commands(self) -> None:
        for command, expected in (
            ("git push --force origin main", "bash/force-push"),
            ("git push origin main", "bash/push-main"),
            ("git add -A", "git/stage-all"),
            ("cat .env", "bash/print-env"),
            ("twine upload dist/*", "python/publish-package"),
            ("python manage.py flush", "python/destructive-db"),
            ("sudo pip install requests", "python/pip-system"),
        ):
            with self.subTest(command=command):
                output = self.run_hook("pre_tool.py", {"tool_name": "Bash", "tool_input": {"command": command}})
                self.assertIn(expected, self.denial(output) or "")

    def test_allows_ordinary_shell_commands(self) -> None:
        for command in ("git status", "pytest -q", "git add -- src/shop/orders.py", "ruff check src"):
            with self.subTest(command=command):
                self.assertEqual(self.run_hook("pre_tool.py", {"tool_name": "Bash", "tool_input": {"command": command}}), {})

    def test_blocks_commits_on_a_protected_branch(self) -> None:
        output = self.run_hook("pre_tool.py", {"tool_name": "Bash", "tool_input": {"command": 'git commit -m "feat: x"'}})
        self.assertIn("git/protected-branch", self.denial(output) or "")

    def test_blocks_a_non_conventional_commit_header(self) -> None:
        subprocess.run(["git", "switch", "-q", "-c", "feat/probe"], cwd=self.project, check=True, capture_output=True)
        try:
            output = self.run_hook("pre_tool.py", {"tool_name": "Bash", "tool_input": {"command": 'git commit -m "fixed stuff"'}})
            self.assertIn("git/conventional-commit", self.denial(output) or "")
            self.assertEqual(self.run_hook("pre_tool.py", {"tool_name": "Bash", "tool_input": {"command": 'git commit -m "fix(orders): handle empty totals"'}}), {})
        finally:
            subprocess.run(["git", "switch", "-q", "master"], cwd=self.project, check=False, capture_output=True)


class PostToolTest(HookTestCase):
    def test_reports_analyzer_findings_on_the_edited_file(self) -> None:
        output = self.run_hook(
            "post_tool.py",
            {"session_id": "post1", "tool_name": "Edit", "tool_input": {"file_path": "src/shop/orders.py", "new_string": "    return rows"}},
        )
        text = self.feedback(output)
        self.assertIn("bug/mutable-default", text)
        self.assertIn("bug/blocking-call-in-async", text)
        self.assertEqual(output.get("decision"), "block", "high findings must come back for a fix")

    def test_tracks_the_edited_file_in_session_state(self) -> None:
        self.run_hook(
            "post_tool.py",
            {"session_id": "post2", "tool_name": "Edit", "tool_input": {"file_path": "src/shop/orders.py", "new_string": "x = 1"}},
        )
        state = json.loads((self.project / ".claude" / "standards" / ".state" / "post2.json").read_text(encoding="utf-8"))
        self.assertEqual(state["edited"], ["src/shop/orders.py"])

    def test_ignores_files_outside_the_project(self) -> None:
        self.assertEqual(
            self.run_hook("post_tool.py", {"session_id": "post3", "tool_name": "Edit", "tool_input": {"file_path": "/etc/hosts", "new_string": "x"}}),
            {},
        )

    def test_records_a_reviewer_verdict(self) -> None:
        payload = {
            "session_id": "post4",
            "tool_name": "Task",
            "tool_input": {"subagent_type": "reviewer"},
            "tool_response": [
                'Looks good overall.\nREVIEW_RESULT {"verdict":"changes_requested","findings":[{"severity":"high","category":"tests","file":"a.py","line":1,"summary":"no test"}],"wins":["clear naming"]}'
            ],
        }
        output = self.run_hook("post_tool.py", payload)
        self.assertIn("changes requested", self.feedback(output))
        state = json.loads((self.project / ".claude" / "standards" / ".state" / "post4.json").read_text(encoding="utf-8"))
        self.assertEqual(state["review"]["verdict"], "changes_requested")
        self.assertEqual(state["review"]["high"], 1)

    def test_asks_again_when_the_reviewer_forgets_the_result_line(self) -> None:
        payload = {"session_id": "post5", "tool_name": "Task", "tool_input": {"subagent_type": "reviewer"}, "tool_response": ["All fine."]}
        self.assertIn("REVIEW_RESULT", self.feedback(self.run_hook("post_tool.py", payload)))


class StopTest(HookTestCase):
    def _state(self, session: str, data: dict) -> None:
        state_dir = self.project / ".claude" / "standards" / ".state"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / f"{session}.json").write_text(json.dumps(data), encoding="utf-8")

    def test_releases_a_turn_with_no_edits(self) -> None:
        self._state("stop1", {"edited": []})
        self.assertEqual(self.run_hook("stop.py", {"session_id": "stop1"}), {})

    def test_gates_on_analyzer_findings(self) -> None:
        self._state("stop2", {"edited": ["src/shop/orders.py"], "editSeq": 1})
        output = self.run_hook("stop.py", {"session_id": "stop2"})
        self.assertEqual(output.get("decision"), "block")
        self.assertIn("Analyzer:", output["reason"])
        self.assertIn("bug/mutable-default", output["reason"])

    def test_gives_up_after_the_configured_rounds(self) -> None:
        self._state("stop3", {"edited": ["src/shop/orders.py"], "loops": 3})
        self.assertEqual(self.run_hook("stop.py", {"session_id": "stop3"}), {})

    def test_asks_for_tests_when_nothing_covers_the_change(self) -> None:
        (self.project / "src" / "shop" / "untested.py").write_text("VALUE: int = 1\n", encoding="utf-8")
        self._state("stop4", {"edited": ["src/shop/untested.py"], "editSeq": 1})
        output = self.run_hook("stop.py", {"session_id": "stop4"})
        self.assertIn("no test file covers", output.get("reason", ""))


class SessionStartTest(HookTestCase):
    def test_emits_the_active_context(self) -> None:
        output = self.run_hook("session_start.py", {"hook_event_name": "SessionStart"})
        context = (output.get("hookSpecificOutput") or {}).get("additionalContext", "")
        self.assertIn('Profile "fastapi"', context)
        self.assertIn("analyzer", context)
        self.assertIn("Commit policy", context)


class PromptSubmitTest(HookTestCase):
    def test_notices_praise(self) -> None:
        output = self.run_hook("prompt_submit.py", {"session_id": "p1", "prompt": "perfect, exactly what I wanted"})
        self.assertIn("record it as a win", self.feedback(output))

    def test_notices_a_correction(self) -> None:
        output = self.run_hook("prompt_submit.py", {"session_id": "p2", "prompt": "no, I already told you not to do that"})
        self.assertIn("grievance", self.feedback(output))

    def test_ignores_ordinary_prompts_and_slash_commands(self) -> None:
        self.assertEqual(self.run_hook("prompt_submit.py", {"prompt": "add pagination to the orders endpoint"}), {})
        self.assertEqual(self.run_hook("prompt_submit.py", {"prompt": "/qa"}), {})


class RecordLessonTest(HookTestCase):
    def run_record(self, payload: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.hooks / "record_lesson.py")],
            input=payload,
            capture_output=True,
            text=True,
            env={"CLAUDE_PROJECT_DIR": str(self.project), "PATH": "/usr/bin:/bin", "HOME": str(self.project)},
            check=False,
        )

    def test_records_a_win_with_its_exemplar(self) -> None:
        result = self.run_record(json.dumps({"kind": "win", "lesson": "Return typed response models", "examples": ["src/shop/orders.py", "nope.py"]}))
        self.assertEqual(result.returncode, 0, result.stderr)
        candidates = (self.project / ".claude" / "standards" / "learning" / "candidates.jsonl").read_text(encoding="utf-8")
        record = json.loads(candidates.splitlines()[-1])
        self.assertEqual(record["kind"], "win")
        self.assertEqual(record["examples"], ["src/shop/orders.py"])
        self.assertEqual(len(record["id"]), 8)

    def test_rejects_input_without_a_lesson(self) -> None:
        self.assertEqual(self.run_record(json.dumps({"kind": "win"})).returncode, 1)

    def test_drops_an_invalid_rule(self) -> None:
        result = self.run_record(json.dumps({"kind": "grievance", "lesson": "Never do that again", "rule": {"id": "x/y", "pattern": "([bad"}}))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("dropping invalid rule", result.stderr)


if __name__ == "__main__":
    unittest.main()
