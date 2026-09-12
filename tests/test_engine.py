"""The engine: suppression, severity gating, rendering and crash isolation."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mast.analysis import REGISTRY, Settings, analyze, analyze_source, findings_to_json
from mast.analysis.render import render_json, render_sarif, render_text


class SuppressionTest(unittest.TestCase):
    def test_inline_comment_suppresses_one_check(self) -> None:
        source = "def f(x=[]):  # mast: ignore[bug/mutable-default]\n    return x\n"
        self.assertEqual([f.check for f in analyze_source(source, "a.py").findings if f.check == "bug/mutable-default"], [])

    def test_comment_on_the_line_above_also_counts(self) -> None:
        source = "# mast: ignore[bug/mutable-default]\ndef f(x=[]):\n    return x\n"
        self.assertNotIn("bug/mutable-default", [f.check for f in analyze_source(source, "a.py").findings])

    def test_bare_ignore_suppresses_everything_on_the_line(self) -> None:
        source = "import os\n\n\ndef f(cmd):\n    os.system(cmd)  # mast: ignore\n"
        self.assertNotIn("sec/os-system", [f.check for f in analyze_source(source, "a.py").findings])

    def test_an_unrelated_suppression_does_not_hide_the_finding(self) -> None:
        source = "def f(x=[]):  # mast: ignore[sec/os-system]\n    return x\n"
        self.assertIn("bug/mutable-default", [f.check for f in analyze_source(source, "a.py").findings])


class SelectionTest(unittest.TestCase):
    SOURCE = "import os\n\n\ndef f(cmd, items=[]):\n    os.system(cmd)\n    return items\n"

    def test_select_limits_the_run(self) -> None:
        report = analyze_source(self.SOURCE, "a.py", Settings(select=("security",)))
        self.assertEqual({f.category for f in report.findings}, {"security"})

    def test_ignore_removes_a_check(self) -> None:
        report = analyze_source(self.SOURCE, "a.py", Settings(ignore=("sec/os-system",)))
        self.assertNotIn("sec/os-system", [f.check for f in report.findings])

    def test_exceeds_respects_the_threshold(self) -> None:
        report = analyze_source("def f(x: int) -> int:\n    return x\n", "a.py")
        self.assertFalse(report.exceeds("high"))
        report = analyze_source(self.SOURCE, "a.py")
        self.assertTrue(report.exceeds("high"))
        self.assertFalse(report.exceeds("none"))
        self.assertFalse(report.exceeds(None))

    def test_counts_and_categories(self) -> None:
        report = analyze_source(self.SOURCE, "a.py")
        counts = report.counts()
        self.assertEqual(sum(counts.values()), len(report.findings))
        self.assertEqual(sum(report.by_category().values()), len(report.findings))
        self.assertEqual(report.worst(), "high")


class RenderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.report = analyze_source(
            "def f(x=[]):\n    return x\n", "src/a.py", Settings(select=("bug/mutable-default",))
        )

    def test_text_output_names_the_file_and_check(self) -> None:
        text = render_text(self.report, tty=False)
        self.assertIn("src/a.py", text)
        self.assertIn("bug/mutable-default", text)

    def test_json_output_is_machine_readable(self) -> None:
        payload = json.loads(render_json(self.report))
        self.assertEqual([f["check"] for f in payload["findings"]], ["bug/mutable-default"])
        self.assertEqual(payload["findings"][0]["column"], self.report.findings[0].col + 1)
        self.assertIn("counts", payload)
        json.loads(findings_to_json(self.report.findings))

    def test_sarif_output_is_valid_and_describes_its_rules(self) -> None:
        document = json.loads(render_sarif(self.report, version="1.2.3"))
        self.assertEqual(document["version"], "2.1.0")
        run = document["runs"][0]
        self.assertEqual(run["tool"]["driver"]["version"], "1.2.3")
        self.assertEqual(run["results"][0]["ruleId"], "bug/mutable-default")
        self.assertEqual(run["results"][0]["level"], "error")
        self.assertIn("bug/mutable-default", [rule["id"] for rule in run["tool"]["driver"]["rules"]])

    def test_clean_report_says_so(self) -> None:
        clean = analyze_source("def f(x: int) -> int:\n    return x\n", "src/a.py")
        self.assertEqual(clean.findings, [])
        self.assertIn("No findings", render_text(clean, tty=False))


class RobustnessTest(unittest.TestCase):
    def test_a_crashing_check_is_isolated(self) -> None:
        broken = REGISTRY["bug/mutable-default"]
        original = broken.fn
        object.__setattr__(broken, "fn", lambda ctx: (_ for _ in ()).throw(RuntimeError("boom")))
        try:
            report = analyze_source("def f(x=[]):\n    return x\n", "a.py")
        finally:
            object.__setattr__(broken, "fn", original)
        self.assertTrue(any("crashed" in error for error in report.errors))
        self.assertNotIn("bug/mutable-default", [f.check for f in report.findings])

    def test_unreadable_and_oversized_files_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ok.py").write_text("x: int = 1\n", encoding="utf-8")
            (root / "binary.py").write_bytes(b"\xff\xfe\x00invalid")
            report = analyze([str(root)], Settings(), root=root)
        self.assertEqual(report.skipped, 1)
        self.assertEqual(report.files, 1)

    def test_changed_lines_filter_narrows_the_report(self) -> None:
        source = "def a(x=[]):\n    return x\n\n\ndef b(y=[]):\n    return y\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "m.py").write_text(source, encoding="utf-8")
            report = analyze(
                ["m.py"], Settings(select=("bug/mutable-default",)), root=root, changed_lines={"m.py": {5}}
            )
        self.assertEqual([(f.check, f.line) for f in report.findings], [("bug/mutable-default", 5)])


if __name__ == "__main__":
    unittest.main()
