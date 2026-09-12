"""Efficiency checks: they must catch the quadratic pattern, not the idiomatic one."""

from __future__ import annotations

import unittest

from tests.helpers import assert_clean, assert_finds


class LoopCostTest(unittest.TestCase):
    def test_flags_string_concatenation_in_a_loop(self) -> None:
        source = "def f(rows):\n    out = ''\n    for r in rows:\n        out += f'{r}\\n'\n    return out\n"
        assert_finds(self, source, "perf/loop-string-concat")

    def test_allows_join(self) -> None:
        source = "def f(rows):\n    parts = []\n    for r in rows:\n        parts.append(str(r))\n    return ''.join(parts)\n"
        assert_clean(self, source, "perf/loop-string-concat")

    def test_flags_list_membership_inside_a_loop(self) -> None:
        source = "def f(rows):\n    allowed = [r.id for r in rows]\n    for r in rows:\n        if r.id in allowed:\n            yield r\n"
        assert_finds(self, source, "perf/quadratic-membership")

    def test_ignores_a_named_constant(self) -> None:
        source = "LEVELS = ['a', 'b']\n\ndef f(rows):\n    for r in rows:\n        if r.level in LEVELS:\n            yield r\n"
        assert_clean(self, source, "perf/quadratic-membership")

    def test_respects_scope(self) -> None:
        # `names` is a list in one function and a set in another: only the list is a problem.
        source = (
            "def a(rows):\n    names = [r.name for r in rows]\n    return names\n\n"
            "def b(rows):\n    names = {r.name for r in rows}\n    for r in rows:\n        if r.name in names:\n            yield r\n"
        )
        assert_clean(self, source, "perf/quadratic-membership")

    def test_flags_regex_compiled_in_a_loop(self) -> None:
        source = "import re\n\ndef f(rows):\n    for r in rows:\n        if re.compile(r'\\d+').match(r):\n            yield r\n"
        assert_finds(self, source, "perf/regex-compile-in-loop")


class QueryTest(unittest.TestCase):
    def test_flags_orm_query_in_a_loop(self) -> None:
        source = "def f(ids):\n    for i in ids:\n        yield Order.objects.get(pk=i)\n"
        assert_finds(self, source, "perf/n-plus-one-query")

    def test_flags_session_query_in_a_loop(self) -> None:
        source = "def f(session, ids):\n    for i in ids:\n        yield session.query(Order).filter(Order.id == i).first()\n"
        assert_finds(self, source, "perf/n-plus-one-query")

    def test_allows_a_plain_mapping_lookup(self) -> None:
        source = "def f(lookup, ids):\n    for i in ids:\n        yield lookup.get(i)\n"
        assert_clean(self, source, "perf/n-plus-one-query")

    def test_allows_one_query_before_the_loop(self) -> None:
        source = "def f(ids):\n    rows = Order.objects.filter(pk__in=ids)\n    for row in rows:\n        yield row\n"
        assert_clean(self, source, "perf/n-plus-one-query")


class PandasTest(unittest.TestCase):
    def test_flags_row_iteration(self) -> None:
        source = "import pandas as pd\n\ndef f(df):\n    for _, row in df.iterrows():\n        yield row\n"
        assert_finds(self, source, "perf/pandas-row-loop")

    def test_flags_concat_in_a_loop(self) -> None:
        source = "import pandas as pd\n\ndef f(frames):\n    out = pd.DataFrame()\n    for frame in frames:\n        out = pd.concat([out, frame])\n    return out\n"
        assert_finds(self, source, "perf/pandas-concat-in-loop")

    def test_quiet_without_pandas(self) -> None:
        source = "def f(df):\n    for _, row in df.iterrows():\n        yield row\n"
        assert_clean(self, source, "perf/pandas-row-loop")


class IdiomTest(unittest.TestCase):
    def test_flags_keys_membership(self) -> None:
        assert_finds(self, "def f(d, k):\n    return k in d.keys()\n", "perf/dict-keys-membership")

    def test_flags_range_len(self) -> None:
        assert_finds(self, "def f(rows):\n    for i in range(len(rows)):\n        yield rows[i]\n", "perf/range-len")

    def test_flags_identity_comprehension(self) -> None:
        assert_finds(self, "def f(rows):\n    return [r for r in rows]\n", "perf/identity-comprehension")

    def test_allows_a_transforming_comprehension(self) -> None:
        assert_clean(self, "def f(rows):\n    return [r.id for r in rows]\n", "perf/identity-comprehension")

    def test_flags_append_only_loop(self) -> None:
        source = "def f(rows):\n    out = []\n    for r in rows:\n        out.append(r)\n    return out\n"
        assert_finds(self, source, "perf/append-only-loop")

    def test_allows_a_comprehension_instead_of_append(self) -> None:
        assert_clean(self, "def f(rows):\n    return [r for r in rows if r]\n", "perf/append-only-loop")

    def test_flags_await_in_loop(self) -> None:
        source = "async def f(urls):\n    out = []\n    for url in urls:\n        out.append(await fetch(url))\n    return out\n"
        assert_finds(self, source, "perf/await-in-loop")

    def test_allows_gather(self) -> None:
        source = "import asyncio\n\nasync def f(urls):\n    return await asyncio.gather(*[fetch(u) for u in urls])\n"
        assert_clean(self, source, "perf/await-in-loop")

    def test_flags_count_for_membership(self) -> None:
        assert_finds(self, "def f(items, value):\n    return items.count(value) > 0\n", "perf/count-for-membership")

    def test_allows_in(self) -> None:
        assert_clean(self, "def f(items, value):\n    return value in items\n", "perf/count-for-membership")

    def test_flags_deepcopy_in_loop(self) -> None:
        source = "import copy\n\ndef f(rows):\n    out = []\n    for row in rows:\n        out.append(copy.deepcopy(row))\n    return out\n"
        assert_finds(self, source, "perf/deepcopy-in-loop")

    def test_allows_deepcopy_once(self) -> None:
        source = "import copy\n\ndef f(row, n):\n    template = copy.deepcopy(row)\n    return [template] * n\n"
        assert_clean(self, source, "perf/deepcopy-in-loop")

    def test_flags_list_membership_literal(self) -> None:
        assert_finds(self, 'def f(value):\n    return value in ["a", "b", "c", "d"]\n', "perf/list-membership-literal")

    def test_allows_a_set_literal(self) -> None:
        assert_clean(self, 'def f(value):\n    return value in {"a", "b", "c", "d"}\n', "perf/list-membership-literal")

    def test_flags_redundant_list_wrapper(self) -> None:
        assert_finds(self, "def f(rows):\n    return sorted(list(rows))\n", "perf/redundant-call")

    def test_allows_sorted_on_the_iterable(self) -> None:
        assert_clean(self, "def f(rows):\n    return sorted(rows)\n", "perf/redundant-call")


if __name__ == "__main__":
    unittest.main()
