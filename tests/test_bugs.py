"""Bug-hunting checks: each must fire on the defect and stay quiet on the correct version."""

from __future__ import annotations

import unittest

from tests.helpers import assert_clean, assert_finds, check_ids, findings


class MutableDefaultTest(unittest.TestCase):
    def test_flags_list_dict_and_call_defaults(self) -> None:
        for default in ("[]", "{}", "set()", "dict()", "list()", "collections.defaultdict(list)"):
            assert_finds(self, f"import collections\ndef f(x={default}):\n    return x\n", "bug/mutable-default")

    def test_allows_none_and_immutables(self) -> None:
        assert_clean(self, "def f(x=None, y=0, z=(), s='a'):\n    return x\n", "bug/mutable-default")

    def test_names_the_parameter(self) -> None:
        found = [
            f
            for f in findings("def save(rows=[], cache={}):\n    return rows\n")
            if f.check == "bug/mutable-default"
        ]
        self.assertEqual(len(found), 2)
        self.assertIn("rows=[]", found[0].message)
        self.assertIn("cache={}", found[1].message)


class ClosureTest(unittest.TestCase):
    def test_flags_loop_variable_capture(self) -> None:
        assert_finds(self, "def f():\n    out = []\n    for i in range(3):\n        out.append(lambda: i)\n    return out\n", "bug/late-binding-closure")

    def test_flags_comprehension_capture(self) -> None:
        assert_finds(self, "def f(x):\n    return [lambda: i for i in range(3)]\n", "bug/late-binding-closure")

    def test_allows_default_binding(self) -> None:
        assert_clean(self, "def f():\n    out = []\n    for i in range(3):\n        out.append(lambda i=i: i)\n    return out\n", "bug/late-binding-closure")

    def test_allows_immediate_use(self) -> None:
        assert_clean(self, "def f(rows):\n    for r in rows:\n        apply_now(lambda: r)\n", "bug/late-binding-closure")


class ExceptionTest(unittest.TestCase):
    def test_flags_bare_except(self) -> None:
        assert_finds(self, "def f():\n    try:\n        go()\n    except:\n        return None\n", "bug/bare-except")

    def test_flags_silent_swallow(self) -> None:
        assert_finds(self, "def f():\n    try:\n        go()\n    except ValueError:\n        pass\n", "bug/silent-except")

    def test_a_comment_explains_a_deliberate_swallow(self) -> None:
        assert_clean(self, "def f():\n    try:\n        go()\n    except ValueError:\n        pass  # the value is optional here\n", "bug/silent-except")

    def test_allows_optional_import_idiom(self) -> None:
        assert_clean(self, "try:\n    import ujson\nexcept ImportError:\n    pass\n", "bug/silent-except")

    def test_flags_unreachable_handler(self) -> None:
        source = "def f():\n    try:\n        go()\n    except Exception:\n        raise\n    except ValueError:\n        raise\n"
        assert_finds(self, source, "bug/handler-order")

    def test_allows_narrow_then_broad(self) -> None:
        source = "def f():\n    try:\n        go()\n    except ValueError:\n        raise\n    except Exception:\n        raise\n"
        assert_clean(self, source, "bug/handler-order")

    def test_flags_return_in_finally(self) -> None:
        assert_finds(self, "def f():\n    try:\n        return go()\n    finally:\n        return 1\n", "bug/return-in-finally")


class ConditionTest(unittest.TestCase):
    def test_flags_literal_in_condition(self) -> None:
        assert_finds(self, "def f(x):\n    if x == 1 or 2:\n        return True\n    return False\n", "bug/constant-in-condition")

    def test_allows_idiomatic_defaulting(self) -> None:
        assert_clean(self, 'def f(x):\n    name = x or "anonymous"\n    return name\n', "bug/constant-in-condition")

    def test_flags_duplicate_branch(self) -> None:
        source = "def f(x):\n    if x > 1:\n        return 1\n    elif x > 1:\n        return 2\n    return 3\n"
        assert_finds(self, source, "bug/duplicate-condition")

    def test_flags_identity_on_literal(self) -> None:
        assert_finds(self, "def f(x):\n    return x is 'a'\n", "bug/identity-comparison")

    def test_allows_sentinels(self) -> None:
        for sentinel in ("None", "True", "..."):
            assert_clean(self, f"def f(x):\n    return x is {sentinel}\n", "bug/identity-comparison")


class IterationTest(unittest.TestCase):
    def test_flags_mutation_while_iterating(self) -> None:
        assert_finds(self, "def f(items):\n    for x in items:\n        items.remove(x)\n", "bug/mutate-during-iteration")

    def test_flags_delete_from_dict_being_iterated(self) -> None:
        assert_finds(self, "def f(d):\n    for k in d.keys():\n        del d[k]\n", "bug/mutate-during-iteration")

    def test_allows_iterating_a_copy(self) -> None:
        assert_clean(self, "def f(items):\n    for x in list(items):\n        items.remove(x)\n", "bug/mutate-during-iteration")


class AsyncTest(unittest.TestCase):
    def test_flags_blocking_sleep_and_requests(self) -> None:
        source = "import time\nimport requests\n\nasync def f(url):\n    time.sleep(1)\n    return requests.get(url)\n"
        ids = check_ids(source)
        self.assertEqual(ids.count("bug/blocking-call-in-async"), 2)

    def test_allows_async_equivalents(self) -> None:
        source = "import asyncio\n\nasync def f(client, url):\n    await asyncio.sleep(1)\n    return await client.get(url)\n"
        assert_clean(self, source, "bug/blocking-call-in-async")


class MiscBugTest(unittest.TestCase):
    def test_flags_unraised_exception(self) -> None:
        assert_finds(self, "def f(x):\n    if not x:\n        ValueError('empty')\n    return x\n", "bug/unraised-exception")

    def test_flags_assert_outside_tests(self) -> None:
        assert_finds(self, "def f(x):\n    assert x > 0\n    return x\n", "bug/assert-for-validation")

    def test_allows_assert_in_tests(self) -> None:
        assert_clean(self, "def test_x():\n    assert compute() > 0\n", "bug/assert-for-validation", rel="tests/test_x.py")

    def test_flags_shared_class_attribute(self) -> None:
        assert_finds(self, "class A:\n    items = []\n", "bug/shared-class-attribute")

    def test_allows_constant_class_attribute(self) -> None:
        assert_clean(self, "class A:\n    CHOICES = ['a', 'b']\n", "bug/shared-class-attribute")

    def test_flags_missing_super_init(self) -> None:
        source = "class Base:\n    def __init__(self):\n        self.x = 1\n\nclass Child(Base):\n    def __init__(self):\n        self.y = 2\n"
        assert_finds(self, source, "bug/missing-super-init")

    def test_allows_super_call(self) -> None:
        source = "class Base:\n    def __init__(self):\n        self.x = 1\n\nclass Child(Base):\n    def __init__(self):\n        super().__init__()\n"
        assert_clean(self, source, "bug/missing-super-init")

    def test_flags_naive_utcnow(self) -> None:
        assert_finds(self, "from datetime import datetime\n\ndef f():\n    return datetime.utcnow()\n", "bug/naive-utcnow")

    def test_flags_unreachable_code(self) -> None:
        assert_finds(self, "def f():\n    return 1\n    cleanup()\n", "bug/unreachable-code")

    def test_flags_missing_f_prefix(self) -> None:
        assert_finds(self, 'def f(user_id):\n    return "user {user_id} saved"\n', "bug/missing-f-prefix")

    def test_allows_template_arguments(self) -> None:
        assert_clean(self, 'def f(template, file):\n    return template.replace("{file}", file)\n', "bug/missing-f-prefix")

    def test_flags_missing_comma_in_list(self) -> None:
        assert_finds(self, 'VALUES = [\n    "alpha"\n    "beta",\n    "gamma",\n]\n', "bug/implicit-string-concat")

    def test_allows_deliberate_continuation(self) -> None:
        assert_clean(self, 'MESSAGE = (\n    "a long sentence that "\n    "continues here"\n)\n', "bug/implicit-string-concat")

    def test_reports_syntax_errors(self) -> None:
        assert_finds(self, "def broken(:\n    pass\n", "bug/syntax-error")

    def test_flags_lru_cache_on_method(self) -> None:
        source = "from functools import lru_cache\n\nclass A:\n    @lru_cache\n    def f(self, x):\n        return x\n"
        assert_finds(self, source, "bug/lru-cache-on-method")

    def test_flags_comparison_to_none(self) -> None:
        assert_finds(self, "def f(x):\n    return x == None\n", "bug/comparison-to-none")

    def test_allows_is_none(self) -> None:
        assert_clean(self, "def f(x):\n    return x is None\n", "bug/comparison-to-none")

    def test_flags_duplicate_dict_key(self) -> None:
        assert_finds(self, 'VALUES = {"a": 1, "a": 2}\n', "bug/duplicate-key")

    def test_allows_unique_keys(self) -> None:
        assert_clean(self, 'VALUES = {"a": 1, "b": 2}\n', "bug/duplicate-key")

    def test_flags_self_assignment(self) -> None:
        assert_finds(self, "class A:\n    def f(self):\n        self.total = self.total\n", "bug/self-assignment")

    def test_allows_storing_the_parameter(self) -> None:
        assert_clean(self, "class A:\n    def f(self, total):\n        self.total = total\n", "bug/self-assignment")

    def test_flags_self_comparison(self) -> None:
        assert_finds(self, "def f(x):\n    return x == x\n", "bug/self-comparison")

    def test_allows_comparing_neighbours(self) -> None:
        assert_clean(self, "def f(x, y):\n    return x == y\n", "bug/self-comparison")

    def test_flags_type_equality(self) -> None:
        assert_finds(self, "def f(x):\n    return type(x) == int\n", "bug/type-equality")

    def test_allows_isinstance(self) -> None:
        assert_clean(self, "def f(x):\n    return isinstance(x, int)\n", "bug/type-equality")

    def test_flags_open_without_with(self) -> None:
        assert_finds(self, 'def f(path):\n    return open(path).read()\n', "bug/unclosed-resource")

    def test_allows_with_open(self) -> None:
        assert_clean(self, 'def f(path):\n    with open(path) as handle:\n        return handle.read()\n', "bug/unclosed-resource")


if __name__ == "__main__":
    unittest.main()
