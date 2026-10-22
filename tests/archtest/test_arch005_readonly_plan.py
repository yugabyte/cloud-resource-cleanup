"""ARCH-005 check and self-tests."""

from pathlib import Path

import pytest

from tests.archtest._common import REPO_ROOT, load_legacy
from tests.archtest.readonly_plan import MUTATING_WORDS, check_plan_readonly, is_mutating

DIRECT = """
class C:
    def plan(self):
        return self.client.terminate_instances(InstanceIds=[])
"""
VIA_FUNCTION = """
def _helper(client):
    client.delete_volume(VolumeId="v")


class C:
    def plan(self):
        _helper(self.client)
"""
VIA_SELF_METHOD = """
class C:
    def _inner(self):
        self.client.begin_power_off("rg", "vm")

    def plan(self):
        self._inner()
"""
PASCAL_CASE = """
class C:
    def plan(self):
        self.client.CreateTags(Resources=[])
"""
READ_ONLY = """
def _describe(client):
    return client.describe_volumes()


class C:
    def plan(self):
        return _describe(self.client)

    def apply(self, plan):
        self.client.delete_volume(VolumeId="v")
"""


def _write(root: Path, source: str) -> None:
    target = root / "crc" / "aws" / "new_mod.py"
    target.parent.mkdir(parents=True)
    target.write_text(source, encoding="utf-8")


def test_arch005_plan_methods_are_read_only() -> None:
    problems = check_plan_readonly(REPO_ROOT, load_legacy(REPO_ROOT))
    assert not problems, "\n\n".join(problems)


@pytest.mark.parametrize("word", MUTATING_WORDS)
def test_arch005_every_listed_word_is_detected(word: str) -> None:
    assert is_mutating(word)
    assert is_mutating(word.upper())


@pytest.mark.parametrize("name", ["describe_volumes", "list", "get_paginator", "filter"])
def test_arch005_read_calls_are_not_flagged(name: str) -> None:
    assert not is_mutating(name)


@pytest.mark.parametrize(
    "source", [DIRECT, VIA_FUNCTION, VIA_SELF_METHOD, PASCAL_CASE], ids=lambda s: s.split()[1]
)
def test_arch005_selftest_fires_on_mutating_plan(tmp_path: Path, source: str) -> None:
    _write(tmp_path, source)
    messages = check_plan_readonly(tmp_path, frozenset())
    assert len(messages) == 1
    assert messages[0].startswith("ARCH-005: crc/aws/new_mod.py:")
    assert "\n\nREMEDIATION: " in messages[0]


def test_arch005_selftest_allows_mutation_in_apply_only(tmp_path: Path) -> None:
    _write(tmp_path, READ_ONLY)
    assert check_plan_readonly(tmp_path, frozenset()) == []


def test_arch005_selftest_skips_legacy_files(tmp_path: Path) -> None:
    _write(tmp_path, DIRECT)
    assert check_plan_readonly(tmp_path, frozenset({"crc/aws/new_mod.py"})) == []
