"""Enterprise standards checks: typing, exceptions, logging, shared state."""

from __future__ import annotations

import unittest

from tests.helpers import assert_clean, assert_finds


class TypingTest(unittest.TestCase):
    def test_flags_unannotated_public_function(self) -> None:
        assert_finds(self, "def total(rows):\n    return sum(rows)\n", "api/missing-type-hints")

    def test_allows_a_fully_annotated_function(self) -> None:
        assert_clean(self, "def total(rows: list[int]) -> int:\n    return sum(rows)\n", "api/missing-type-hints")

    def test_ignores_private_functions_and_tests(self) -> None:
        assert_clean(self, "def _total(rows):\n    return sum(rows)\n", "api/missing-type-hints")
        assert_clean(self, "def test_total(rows):\n    assert rows\n", "api/missing-type-hints", rel="tests/test_t.py")

    def test_can_be_switched_off(self) -> None:
        assert_clean(self, "def total(rows):\n    return sum(rows)\n", "api/missing-type-hints", require_type_hints=False)

    def test_docstrings_are_opt_in(self) -> None:
        source = '"""Module."""\n\n\ndef total(rows: list[int]) -> int:\n    return sum(rows)\n'
        assert_clean(self, source, "api/missing-docstring")
        assert_finds(self, source, "api/missing-docstring", require_docstrings=True)

    def test_flags_any_in_a_public_signature(self) -> None:
        assert_finds(self, "from typing import Any\n\n\ndef f(value: Any) -> None:\n    return None\n", "api/any-annotation")


class ExceptionStandardsTest(unittest.TestCase):
    def test_flags_broad_except_that_does_nothing(self) -> None:
        source = "def f():\n    try:\n        go()\n    except Exception:\n        return None\n"
        assert_finds(self, source, "err/broad-except")

    def test_allows_broad_except_that_logs(self) -> None:
        source = "import logging\n\nlogger = logging.getLogger(__name__)\n\n\ndef f():\n    try:\n        go()\n    except Exception:\n        logger.exception('failed')\n        return None\n"
        assert_clean(self, source, "err/broad-except")

    def test_allows_broad_except_that_records_the_error(self) -> None:
        source = "def f(errors):\n    try:\n        go()\n    except Exception as exc:\n        errors.append(str(exc))\n"
        assert_clean(self, source, "err/broad-except")

    def test_flags_generic_raise(self) -> None:
        assert_finds(self, "def f():\n    raise Exception('nope')\n", "err/bare-exception-class")

    def test_flags_lost_traceback(self) -> None:
        source = "def f():\n    try:\n        go()\n    except ValueError:\n        raise RuntimeError('wrapped')\n"
        assert_finds(self, source, "err/lost-traceback")

    def test_allows_raise_from(self) -> None:
        source = "def f():\n    try:\n        go()\n    except ValueError as exc:\n        raise RuntimeError('wrapped') from exc\n"
        assert_clean(self, source, "err/lost-traceback")


class LoggingTest(unittest.TestCase):
    def test_flags_root_logger(self) -> None:
        assert_finds(self, "import logging\n\n\ndef f() -> None:\n    logging.info('hello')\n", "log/root-logger")

    def test_flags_eager_formatting(self) -> None:
        source = "import logging\n\nlogger = logging.getLogger(__name__)\n\n\ndef f(x: int) -> None:\n    logger.info(f'value {x}')\n"
        assert_finds(self, source, "log/eager-formatting")

    def test_allows_lazy_arguments(self) -> None:
        source = "import logging\n\nlogger = logging.getLogger(__name__)\n\n\ndef f(x: int) -> None:\n    logger.info('value %s', x)\n"
        assert_clean(self, source, "log/eager-formatting")

    def test_flags_error_without_traceback(self) -> None:
        source = (
            "import logging\n\nlogger = logging.getLogger(__name__)\n\n\n"
            "def f() -> None:\n    try:\n        go()\n    except ValueError as exc:\n        logger.error(str(exc))\n"
        )
        assert_finds(self, source, "log/error-without-traceback")


class StateTest(unittest.TestCase):
    def test_flags_mutated_module_state(self) -> None:
        source = "cache = {}\n\n\ndef remember(key: str, value: int) -> None:\n    cache[key] = value\n"
        assert_finds(self, source, "api/global-mutable-state")

    def test_allows_an_unmutated_module_literal(self) -> None:
        source = "defaults = {'a': 1}\n\n\ndef get(key: str) -> int:\n    return defaults[key]\n"
        assert_clean(self, source, "api/global-mutable-state")

    def test_flags_mutable_dataclass_default(self) -> None:
        source = "from dataclasses import dataclass\n\n\n@dataclass\nclass A:\n    items: list = []\n"
        assert_finds(self, source, "api/mutable-dataclass-default")

    def test_allows_default_factory(self) -> None:
        source = "from dataclasses import dataclass, field\n\n\n@dataclass\nclass A:\n    items: list = field(default_factory=list)\n"
        assert_clean(self, source, "api/mutable-dataclass-default")

    def test_flags_unfinished_stub(self) -> None:
        assert_finds(self, "def f() -> None:\n    raise NotImplementedError\n", "api/unfinished-code")

    def test_allows_abstract_methods(self) -> None:
        source = (
            "from abc import ABC, abstractmethod\n\n\nclass A(ABC):\n    @abstractmethod\n"
            "    def f(self) -> None:\n        raise NotImplementedError\n"
        )
        assert_clean(self, source, "api/unfinished-code")

    def test_flags_sys_exit_in_library_code(self) -> None:
        assert_finds(self, "import sys\n\n\ndef f() -> None:\n    sys.exit(1)\n", "api/sys-exit-in-library")

    def test_allows_sys_exit_in_an_entry_point(self) -> None:
        assert_clean(self, "import sys\n\n\ndef main() -> None:\n    sys.exit(1)\n", "api/sys-exit-in-library", rel="src/app/__main__.py")


if __name__ == "__main__":
    unittest.main()
