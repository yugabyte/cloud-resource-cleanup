"""SAF-LOCK check and self-tests."""

import json
from pathlib import Path

import pytest

from tests.archtest._common import REPO_ROOT
from tests.archtest.safety_lock import (
    check_safety_lock,
    find_test,
    hash_function,
    normalised_dump,
)

DOC = "- [SAF-001] first invariant\n- [SAF-002] second invariant\n"
GOOD_TEST = """
import unittest


class T(unittest.TestCase):
    def test_a(self):
        self.assertEqual(1, 1)
"""


def _setup(
    tmp_path: Path,
    source: str = GOOD_TEST,
    sha: str | None = None,
    doc: str = DOC,
    gaps: list[str] | None = None,
) -> tuple[Path, Path]:
    tests = tmp_path / "tests"
    tests.mkdir(exist_ok=True)
    (tests / "test_x.py").write_text(source, encoding="utf-8")
    node = "tests/test_x.py::T::test_a"
    if sha is None:
        found = find_test(tmp_path, node)
        assert not isinstance(found, str), found
        sha = hash_function(found[0])
    lock = tmp_path / "lock.json"
    entries = {"SAF-001": [{"test": node, "sha256": sha}]}
    lock.write_text(json.dumps({"locks": entries, "gaps": gaps or ["SAF-002"]}), encoding="utf-8")
    docfile = tmp_path / "doc.md"
    docfile.write_text(doc, encoding="utf-8")
    return lock, docfile


def _messages(tmp_path: Path, **kwargs: object) -> list[str]:
    lock, doc = _setup(tmp_path, **kwargs)  # type: ignore[arg-type]
    return check_safety_lock(tmp_path, lock, doc)


def _assert_formatted(messages: list[str]) -> None:
    assert messages
    for message in messages:
        assert message.startswith("SAF-LOCK: ")
        assert "\n\nREMEDIATION: " in message


def test_saf_lock_locked_tests_are_unchanged() -> None:
    problems = check_safety_lock(REPO_ROOT)
    assert not problems, "\n\n".join(problems)


def test_saf_lock_hash_ignores_positions_and_comments() -> None:
    a = "def f():\n    x = 1  # comment\n    return x\n"
    b = "\n\ndef f():\n        x = 1\n        return x\n"
    import ast

    assert hash_function(ast.parse(a)) == hash_function(ast.parse(b))
    assert normalised_dump(ast.parse("x = None")) != normalised_dump(ast.parse("x = 0"))


def test_saf_lock_selftest_passes_on_consistent_lock(tmp_path: Path) -> None:
    assert _messages(tmp_path) == []


def test_saf_lock_selftest_fires_on_changed_test(tmp_path: Path) -> None:
    lock, doc = _setup(tmp_path)
    (tmp_path / "tests" / "test_x.py").write_text(
        GOOD_TEST.replace("assertEqual(1, 1)", "assertTrue(True)"), encoding="utf-8"
    )
    messages = check_safety_lock(tmp_path, lock, doc)
    _assert_formatted(messages)
    assert "hash mismatch" in messages[0]


def test_saf_lock_selftest_fires_on_missing_test(tmp_path: Path) -> None:
    lock, doc = _setup(tmp_path)
    (tmp_path / "tests" / "test_x.py").write_text("x = 1\n", encoding="utf-8")
    messages = check_safety_lock(tmp_path, lock, doc)
    _assert_formatted(messages)
    assert "missing" in messages[0]


@pytest.mark.parametrize(
    "replacement",
    [
        ("    def test_a(self):", "    @unittest.skip('x')\n    def test_a(self):"),
        (
            "    def test_a(self):",
            "    @pytest.mark.skipif(True, reason='x')\n    def test_a(self):",
        ),
        ("    def test_a(self):", "    @unittest.expectedFailure\n    def test_a(self):"),
        ("self.assertEqual(1, 1)", "pytest.skip('x')"),
        ("self.assertEqual(1, 1)", "pytest.xfail('x')"),
        ("self.assertEqual(1, 1)", "self.skipTest('x')"),
        ("class T(", "@unittest.skip('x')\nclass T("),
        ("import unittest", "import unittest\n\npytestmark = pytest.mark.skip"),
    ],
    ids=["skip", "skipif", "expectedFailure", "pytest.skip", "xfail", "skipTest", "class", "mark"],
)
def test_saf_lock_selftest_fires_on_skip_or_xfail(
    tmp_path: Path, replacement: tuple[str, str]
) -> None:
    old, new = replacement
    source = GOOD_TEST.replace(old, new)
    assert source != GOOD_TEST
    messages = _messages(tmp_path, source=source)
    _assert_formatted(messages)
    assert any("skipped or xfailed" in message for message in messages)


def test_saf_lock_selftest_fires_when_id_is_untracked(tmp_path: Path) -> None:
    messages = _messages(tmp_path, doc=DOC + "- [SAF-003] third\n")
    _assert_formatted(messages)
    assert "SAF-003" in messages[0]


def test_saf_lock_selftest_fires_on_unknown_or_duplicate_id(tmp_path: Path) -> None:
    messages = _messages(tmp_path, doc="- [SAF-001] one\n- [SAF-001] again\n")
    _assert_formatted(messages)
    assert any("more than once" in message for message in messages)
    assert any("not tagged in the doc" in message for message in messages)


def test_saf_lock_selftest_fires_on_unreadable_lock(tmp_path: Path) -> None:
    doc = tmp_path / "doc.md"
    doc.write_text(DOC, encoding="utf-8")
    messages = check_safety_lock(tmp_path, tmp_path / "missing.json", doc)
    _assert_formatted(messages)
