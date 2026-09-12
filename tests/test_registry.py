"""The check registry itself must stay well-formed — it is the tool's public contract."""

from __future__ import annotations

import unittest

from mast.analysis import CATEGORIES, REGISTRY, SEVERITIES, all_checks, resolve_selection


class RegistryTest(unittest.TestCase):
    def test_every_check_is_complete(self) -> None:
        for check in all_checks():
            with self.subTest(check=check.id):
                self.assertRegex(check.id, r"^[a-z]+/[a-z0-9-]+$")
                self.assertIn(check.category, CATEGORIES)
                self.assertIn(check.severity, SEVERITIES)
                self.assertTrue(check.title and check.title[0].isupper() or check.title.startswith("`"))
                self.assertGreater(len(check.why), 40, "every check must justify itself")
                self.assertGreater(len(check.fix), 20, "every check must say how to fix it")

    def test_no_helper_was_registered_by_mistake(self) -> None:
        # A module-level helper or constant placed under a @check decorator would register itself
        # instead of the check function; its name gives it away.
        for check in all_checks():
            with self.subTest(check=check.id):
                self.assertFalse(check.fn.__name__.startswith("_"), f"{check.id} registered {check.fn.__name__}")

    def test_ids_are_unique_and_resolvable(self) -> None:
        self.assertEqual(len(REGISTRY), len(all_checks()))
        self.assertEqual(resolve_selection(["bugs"]), {c.id for c in all_checks() if c.category == "bugs"})
        self.assertEqual(resolve_selection(["bug/mutable-default"]), {"bug/mutable-default"})
        self.assertTrue(resolve_selection(["sec/"]) <= {c.id for c in all_checks() if c.category == "security"})
        with self.assertRaises(ValueError):
            resolve_selection(["not-a-check"])

    def test_every_category_has_checks(self) -> None:
        for category in CATEGORIES:
            self.assertTrue(any(c.category == category for c in all_checks()), category)


if __name__ == "__main__":
    unittest.main()
