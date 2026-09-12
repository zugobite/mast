"""The regex rule engine that backs the hooks."""

from __future__ import annotations

import unittest

from mast.analysis.rules import active_rules, format_hits, invalid_rules, scan

RULES = [
    {"id": "x/debugger", "severity": "block", "globs": ["**/*.py"], "pattern": r"^\s*breakpoint\(\)", "message": "no breakpoints"},
    {"id": "x/secret", "severity": "warn", "globs": [], "exclude": ["tests/**"], "pattern": r"password\s*=", "flags": "i", "message": "no secrets"},
]


class ScanTest(unittest.TestCase):
    def test_matches_per_line_and_reports_line_numbers(self) -> None:
        hits = scan("src/a.py", "x = 1\nbreakpoint()\n", RULES)
        self.assertEqual([h["rule"]["id"] for h in hits], ["x/debugger"])
        self.assertEqual(hits[0]["lines"], [2])

    def test_respects_globs(self) -> None:
        self.assertEqual(scan("README.md", "breakpoint()\n", RULES), [])

    def test_empty_globs_match_every_file(self) -> None:
        self.assertEqual([h["rule"]["id"] for h in scan("config.yaml", "PASSWORD = x\n", RULES)], ["x/secret"])

    def test_respects_exclude(self) -> None:
        self.assertEqual(scan("tests/test_a.py", "PASSWORD = x\n", RULES), [])

    def test_flags_are_honoured(self) -> None:
        self.assertTrue(scan("a.py", "PaSsWoRd =\n", RULES))

    def test_formats_hits_for_the_hook_message(self) -> None:
        text = format_hits(scan("src/a.py", "breakpoint()\n", RULES))
        self.assertIn("[x/debugger]", text)
        self.assertIn("no breakpoints", text)


class ActiveRulesTest(unittest.TestCase):
    def test_merges_sources_and_defaults_severity(self) -> None:
        merged = active_rules([[{"id": "a", "pattern": "x", "message": "m"}], [RULES[0]]], {})
        self.assertEqual([r["id"] for r in merged], ["a", "x/debugger"])
        self.assertEqual(merged[0]["severity"], "warn")

    def test_disabled_rules_are_dropped(self) -> None:
        merged = active_rules([RULES], {"disabledRules": ["x/debugger"]})
        self.assertEqual([r["id"] for r in merged], ["x/secret"])

    def test_severity_overrides_apply(self) -> None:
        merged = active_rules([RULES], {"severityOverrides": {"x/secret": "block", "x/debugger": "off"}})
        self.assertEqual([(r["id"], r["severity"]) for r in merged], [("x/secret", "block")])

    def test_incomplete_rules_are_ignored(self) -> None:
        self.assertEqual(active_rules([[{"id": "a"}, {"pattern": "b"}, "nonsense"]], {}), [])

    def test_invalid_patterns_are_reported(self) -> None:
        broken = [{"id": "bad", "pattern": "([unclosed", "message": "m"}]
        self.assertEqual([r["id"] for r in invalid_rules(broken)], ["bad"])
        self.assertEqual(scan("a.py", "([unclosed\n", broken), [])


class ShippedRulesTest(unittest.TestCase):
    def test_every_shipped_rule_is_valid(self) -> None:
        from mast.common import list_packs, pack_rules

        seen: dict[str, str] = {}
        for pack in list_packs(None):
            rules = pack_rules(pack.name, None)
            self.assertEqual(invalid_rules(rules), [], f"{pack.name} has an invalid pattern")
            for rule in rules:
                with self.subTest(rule=rule.get("id")):
                    self.assertRegex(rule["id"], r"^[a-z]+/[a-z0-9-]+$")
                    self.assertTrue(rule.get("message"))
                    self.assertIn(rule.get("severity", "warn"), ("warn", "block"))
                    self.assertNotIn(rule["id"], seen, f"duplicate id, also in {seen.get(rule['id'])}")
                    seen[rule["id"]] = pack.name


if __name__ == "__main__":
    unittest.main()
