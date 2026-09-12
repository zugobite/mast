"""Structure checks and their configurable thresholds."""

from __future__ import annotations

import unittest

from mast.analysis import Settings, analyze
from tests.helpers import assert_clean, assert_finds, check_ids


def _long_function(lines: int) -> str:
    body = "\n".join(f"    x{i} = {i}" for i in range(lines))
    return f"def f():\n{body}\n    return 0\n"


class ThresholdTest(unittest.TestCase):
    def test_long_function_uses_the_configured_limit(self) -> None:
        source = _long_function(30)
        assert_finds(self, source, "structure/long-function", max_function_lines=10)
        assert_clean(self, source, "structure/long-function", max_function_lines=100)

    def test_flags_complexity(self) -> None:
        branches = "\n".join(f"    if x == {i}:\n        return {i}" for i in range(12))
        assert_finds(self, f"def f(x):\n{branches}\n    return None\n", "structure/high-complexity")

    def test_flags_deep_nesting(self) -> None:
        source = (
            "def f(a, b, c, d, e):\n"
            "    if a:\n        if b:\n            if c:\n                if d:\n                    if e:\n                        return 1\n    return 0\n"
        )
        assert_finds(self, source, "structure/deep-nesting")

    def test_flags_too_many_arguments_but_not_self(self) -> None:
        assert_finds(self, "def f(a, b, c, d, e, f_, g):\n    return 1\n", "structure/too-many-arguments")
        assert_clean(self, "class A:\n    def m(self, a, b):\n        return a\n", "structure/too-many-arguments", max_arguments=2)

    def test_flags_too_many_locals(self) -> None:
        source = "def f():\n    a = 1\n    b = 2\n    c = 3\n    d = 4\n    return a\n"
        assert_finds(self, source, "structure/too-many-locals", max_locals=3)
        assert_clean(self, source, "structure/too-many-locals", max_locals=10)

    def test_flags_a_large_class(self) -> None:
        methods = "\n".join(f"    def m{i}(self):\n        return {i}" for i in range(5))
        assert_finds(self, f"class Big:\n{methods}\n", "structure/large-class", max_class_methods=3)
        assert_clean(self, f"class Big:\n{methods}\n", "structure/large-class", max_class_methods=20)

    def test_flags_a_large_module(self) -> None:
        source = "\n".join(f"VALUE_{i} = {i}" for i in range(12)) + "\n"
        assert_finds(self, source, "structure/large-module", max_module_lines=5)
        assert_clean(self, source, "structure/large-module", max_module_lines=50)
        assert_clean(self, source, "structure/large-module", max_module_lines=5, rel="tests/test_big.py")

    def test_flags_wildcard_import(self) -> None:
        assert_finds(self, "from os.path import *\n", "structure/wildcard-import")


class ProjectWideTest(unittest.TestCase):
    def test_finds_duplicated_function_bodies(self) -> None:
        import tempfile
        from pathlib import Path

        body = "\n".join(
            [
                "    total = 0",
                "    for row in rows:",
                "        total += row.amount",
                "    if total > limit:",
                "        total = limit",
                "    logged = str(total)",
                "    return logged",
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_text(f"def one(rows, limit):\n{body}\n", encoding="utf-8")
            (root / "b.py").write_text(f"def two(items, cap):\n{body.replace('rows', 'items').replace('limit', 'cap')}\n", encoding="utf-8")
            report = analyze([str(root)], Settings(select=("structure/duplicate-block",)), root=root)
        self.assertEqual([f.check for f in report.findings], ["structure/duplicate-block"])

    def test_finds_import_cycles(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pkg").mkdir()
            (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
            (root / "pkg" / "a.py").write_text("from pkg import b\n\nVALUE = b.NAME\n", encoding="utf-8")
            (root / "pkg" / "b.py").write_text("from pkg import a\n\nNAME = 'x'\n", encoding="utf-8")
            report = analyze([str(root)], Settings(select=("structure/import-cycle",)), root=root)
        self.assertTrue(report.findings, "expected a cycle between pkg.a and pkg.b")
        self.assertIn("circular import", report.findings[0].message)

    def test_deferred_imports_are_not_a_cycle(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pkg").mkdir()
            (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
            (root / "pkg" / "a.py").write_text("from pkg import b\n\nVALUE = b.NAME\n", encoding="utf-8")
            (root / "pkg" / "b.py").write_text("NAME = 'x'\n\n\ndef late():\n    from pkg import a\n    return a.VALUE\n", encoding="utf-8")
            report = analyze([str(root)], Settings(select=("structure/import-cycle",)), root=root)
        self.assertEqual(report.findings, [])


class FileKindTest(unittest.TestCase):
    def test_print_is_allowed_in_scripts_and_tests(self) -> None:
        assert_finds(self, "def f():\n    print('x')\n", "log/print-in-code")
        assert_clean(self, "def f():\n    print('x')\n", "log/print-in-code", rel="scripts/run.py")
        assert_clean(self, "def test_f():\n    print('x')\n", "log/print-in-code", rel="tests/test_f.py")

    def test_generated_trees_are_excluded(self) -> None:
        from mast.analysis import Settings, collect_files
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app").mkdir()
            (root / "app" / "models.py").write_text("x = 1\n", encoding="utf-8")
            (root / "app" / "migrations").mkdir()
            (root / "app" / "migrations" / "0001_initial.py").write_text("x = 1\n", encoding="utf-8")
            files = collect_files([str(root)], Settings(), root)
        self.assertEqual([f.name for f in files], ["models.py"])


if __name__ == "__main__":
    unittest.main()
