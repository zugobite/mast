"""Leakcheck: secrets, personal data and extra terms, without writing those literals into the suite."""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from mast.cli import main
from mast.leakcheck import leakcheck
from tests.helpers import isolate_mast_state


def _aws_key() -> str:
    return "AKIA" + "A" * 16


def _credential_line() -> str:
    return "api" + "_key = \"" + ("x" * 12) + "\""


def _home_path() -> str:
    return "/Users/" + "someone" + "/code"


def _personal_email() -> str:
    return "dev@" + "clinic.org"


class LeakcheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.project = root / "proj"
        self.project.mkdir()
        isolate_mast_state(self, root / "state")

    def write(self, rel: str, content: str) -> Path:
        file = self.project / rel
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding="utf-8")
        return file

    def test_flags_a_cloud_key(self) -> None:
        self.write("notes.txt", f"token = {_aws_key()}\n")
        report = leakcheck(self.project)
        self.assertTrue(any(h["check"] == "secret" for h in report.hits))

    def test_flags_a_credential_assignment(self) -> None:
        self.write("config.py", _credential_line() + "\n")
        report = leakcheck(self.project)
        self.assertTrue(any(h["check"] == "credential" for h in report.hits))

    def test_flags_a_home_path(self) -> None:
        self.write("README.md", f"clone into {_home_path()}\n")
        report = leakcheck(self.project)
        self.assertTrue(any(h["check"] == "home-path" for h in report.hits))

    def test_flags_a_personal_email(self) -> None:
        self.write("AUTHORS", f"Owner {_personal_email()}\n")
        report = leakcheck(self.project)
        self.assertTrue(any(h["check"] == "email" for h in report.hits))

    def test_ignores_example_dot_com_emails(self) -> None:
        self.write("docs.md", "Contact docs@example.com or help@example.org\n")
        report = leakcheck(self.project)
        self.assertFalse(any(h["check"] == "email" for h in report.hits))

    def test_extra_terms_are_matched(self) -> None:
        self.write("readme.md", "Internal host: payments.acme.internal\n")
        report = leakcheck(self.project, terms=["acme.internal"])
        self.assertTrue(any(h["check"] == "private-term" for h in report.hits))

    def test_skips_oversized_and_binary_files(self) -> None:
        big = self.project / "big.bin"
        big.write_bytes(b"x" * (1024 * 1024 + 10))
        self.write("blob.dat", "hello\0world\n")
        report = leakcheck(self.project)
        self.assertEqual(report.hits, [])

    def test_cli_exits_one_on_a_hit(self) -> None:
        self.write("notes.txt", f"token = {_aws_key()}\n")
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main(["leakcheck", str(self.project)])
        self.assertEqual(code, 1)
        self.assertIn("potential leak", buffer.getvalue())

    def test_cli_is_clean_on_an_empty_tree(self) -> None:
        self.write("ok.txt", "nothing to see\n")
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main(["leakcheck", str(self.project)])
        self.assertEqual(code, 0)
        self.assertIn("No leaks", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
