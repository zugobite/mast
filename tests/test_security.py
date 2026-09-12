"""Security checks: the pattern must be caught, the safe version must pass."""

from __future__ import annotations

import unittest

from tests.helpers import assert_clean, assert_finds


class InjectionTest(unittest.TestCase):
    def test_flags_f_string_sql(self) -> None:
        assert_finds(self, 'def f(cursor, uid):\n    cursor.execute(f"SELECT * FROM users WHERE id = {uid}")\n', "sec/sql-injection")

    def test_flags_sql_built_then_executed(self) -> None:
        source = 'def f(cursor, uid):\n    query = "SELECT * FROM users WHERE id = %s" % uid\n    cursor.execute(query)\n'
        assert_finds(self, source, "sec/sql-injection")

    def test_allows_bound_parameters(self) -> None:
        assert_clean(self, 'def f(cursor, uid):\n    cursor.execute("SELECT * FROM users WHERE id = %s", (uid,))\n', "sec/sql-injection")

    def test_ignores_prose_that_mentions_from(self) -> None:
        assert_clean(self, 'MESSAGE = f"imported {count} rows from the upstream feed where needed"\n', "sec/sql-injection")

    def test_flags_shell_true_with_interpolation(self) -> None:
        source = 'import subprocess\n\ndef f(name):\n    subprocess.run(f"git clone {name}", shell=True)\n'
        assert_finds(self, source, "sec/shell-injection")

    def test_allows_argument_list(self) -> None:
        source = 'import subprocess\n\ndef f(name):\n    subprocess.run(["git", "clone", name], check=True)\n'
        assert_clean(self, source, "sec/shell-injection")

    def test_flags_os_system(self) -> None:
        assert_finds(self, "import os\n\ndef f(cmd):\n    os.system(cmd)\n", "sec/os-system")

    def test_flags_eval_of_a_value(self) -> None:
        assert_finds(self, "def f(text):\n    return eval(text)\n", "sec/eval-exec")

    def test_allows_literal_eval_of_a_constant(self) -> None:
        assert_clean(self, "def f():\n    return eval('1 + 1')\n", "sec/eval-exec")


class DataTest(unittest.TestCase):
    def test_flags_pickle_and_unsafe_yaml(self) -> None:
        assert_finds(self, "import pickle\n\ndef f(blob):\n    return pickle.loads(blob)\n", "sec/unsafe-deserialization")
        assert_finds(self, "import yaml\n\ndef f(text):\n    return yaml.load(text)\n", "sec/unsafe-deserialization")

    def test_allows_safe_yaml(self) -> None:
        assert_clean(self, "import yaml\n\ndef f(text):\n    return yaml.safe_load(text)\n", "sec/unsafe-deserialization")

    def test_flags_hardcoded_secret(self) -> None:
        name = "DATABASE_" + "PASSWORD"
        assert_finds(self, f'{name} = "s3cret-production-value"\n', "sec/hardcoded-secret")

    def test_allows_environment_lookup_and_placeholders(self) -> None:
        name = "DATABASE_" + "PASSWORD"
        assert_clean(self, f'import os\n\n{name} = os.environ["DB_PASSWORD"]\n', "sec/hardcoded-secret")
        assert_clean(self, f'{name} = "changeme"\n', "sec/hardcoded-secret")

    def test_ignores_test_fixtures(self) -> None:
        name = "PASS" + "WORD"
        assert_clean(self, f'{name} = "fixture-password-value"\n', "sec/hardcoded-secret", rel="tests/conftest.py")


class TransportTest(unittest.TestCase):
    def test_flags_disabled_verification(self) -> None:
        assert_finds(self, 'import requests\n\ndef f(url):\n    return requests.get(url, verify=False)\n', "sec/tls-verification-disabled")

    def test_flags_unverified_token(self) -> None:
        source = 'import jwt\n\ndef f(token):\n    return jwt.decode(token, options={"verify_signature": False})\n'
        assert_finds(self, source, "sec/jwt-unverified")

    def test_flags_weak_hash(self) -> None:
        assert_finds(self, "import hashlib\n\ndef f(b):\n    return hashlib.md5(b).hexdigest()\n", "sec/weak-hash")

    def test_allows_weak_hash_marked_non_security(self) -> None:
        source = "import hashlib\n\ndef f(b):\n    return hashlib.md5(b, usedforsecurity=False).hexdigest()\n"
        assert_clean(self, source, "sec/weak-hash")

    def test_flags_random_for_a_token(self) -> None:
        assert_finds(self, "import random\n\ndef f():\n    token = random.random()\n    return token\n", "sec/random-for-secrets")

    def test_flags_debug_true(self) -> None:
        assert_finds(self, "DEBUG = True\n", "sec/debug-enabled")

    def test_flags_wildcard_cors(self) -> None:
        assert_finds(self, 'CORS_ALLOWED_ORIGINS = ["*"]\n', "sec/permissive-cors")

    def test_flags_mark_safe_on_a_value(self) -> None:
        source = "from django.utils.safestring import mark_safe\n\ndef f(name):\n    return mark_safe(f'<b>{name}</b>')\n"
        assert_finds(self, source, "sec/unsafe-markup")

    def test_flags_mktemp(self) -> None:
        source = "import tempfile\n\ndef f():\n    return tempfile.mktemp()\n"
        assert_finds(self, source, "sec/insecure-temp-file")

    def test_allows_named_temporary_file(self) -> None:
        source = "import tempfile\n\ndef f():\n    return tempfile.NamedTemporaryFile()\n"
        assert_clean(self, source, "sec/insecure-temp-file")

    def test_flags_stdlib_xml_parse(self) -> None:
        source = "from xml.etree.ElementTree import parse\n\ndef f(path):\n    return parse(path)\n"
        assert_finds(self, source, "sec/unsafe-xml")

    def test_allows_defusedxml(self) -> None:
        source = "from defusedxml.ElementTree import parse\n\ndef f(path):\n    return parse(path)\n"
        assert_clean(self, source, "sec/unsafe-xml")


if __name__ == "__main__":
    unittest.main()
