"""Learning loop: collect, report, promote into a standard, reject so it stays gone."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mast import export
from mast.learn import analyze, collect_candidates, promote, reject, render_report
from mast.standard import init_standard
from tests.helpers import isolate_mast_state


class LearnTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        isolate_mast_state(self, self.root / "state")
        self.standard = self.root / "acme-std"
        init_standard(self.standard, name="acme")
        self.project = self.root / "billing"
        self.project.mkdir()
        export.export_project(self.project, standard=str(self.standard), profile="acme")

    def _write_candidate(self, **fields: object) -> dict:
        candidate = {
            "id": "cafe1234",
            "kind": "win",
            "scope": "global",
            "lesson": "Batch tenant queries before the loop",
            "why": "N+1 on the appointments list",
            "projectDir": str(self.project.resolve()),
            **fields,
        }
        learn_dir = self.project / ".claude" / "standards" / "learning"
        learn_dir.mkdir(parents=True, exist_ok=True)
        with (learn_dir / "candidates.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(candidate) + "\n")
        return candidate

    def test_collects_a_project_candidate(self) -> None:
        self._write_candidate()
        found = collect_candidates()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["id"], "cafe1234")
        self.assertIn(str(self.project.resolve()), found[0]["projects"])

    def test_promote_appends_to_the_standard_pack(self) -> None:
        self._write_candidate()
        result = promote("cafe1234", scope="global", pack="acme-shared")
        wins = (self.standard / "packs" / "acme-shared" / "wins.md").read_text(encoding="utf-8")
        self.assertIn("Batch tenant queries before the loop", wins)
        self.assertIn("lesson:cafe1234", wins)
        self.assertTrue(any("wins.md" in path for path in result["written"]))
        self.assertEqual(collect_candidates(), [])

    def test_promote_project_scope_writes_local_wins(self) -> None:
        self._write_candidate(scope="project")
        promote("cafe1234", scope="project")
        local = (self.project / ".claude" / "standards" / "local-wins.md").read_text(encoding="utf-8")
        self.assertIn("Batch tenant queries before the loop", local)

    def test_reject_hides_the_candidate(self) -> None:
        self._write_candidate()
        rejected = reject("cafe")
        self.assertEqual(rejected["id"], "cafe1234")
        self.assertEqual(collect_candidates(), [])

    def test_report_includes_the_pending_lesson(self) -> None:
        self._write_candidate()
        report = render_report(analyze(days=30))
        self.assertIn("cafe1234", report)
        self.assertIn("Batch tenant queries before the loop", report)
        self.assertIn("Pending wins", report)


if __name__ == "__main__":
    unittest.main()
