"""ARCH-005 check and self-tests."""

from pathlib import Path

import pytest

from tests.archtest._common import REPO_ROOT, load_legacy
from tests.archtest.readonly_plan import (
    MUTATING_WORDS,
    check_plan_readonly,
    check_plan_readonly_siblings,
    is_mutating,
)

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


HELPER = (
    "def wipe(client):\n    client.delete_volume(VolumeId='v')\n\n\n"
    "def look(client):\n    return client.describe_volumes()\n"
)
CHAINED = "from .helper import wipe\n\n\ndef outer(client):\n    wipe(client)\n"


def _plan_module(imports: str, call: str) -> str:
    return f"{imports}\n\n\nclass C:\n    def plan(self):\n        {call}\n"


SIBLING_CASES = {
    "from-relative": (_plan_module("from .helper import wipe", "wipe(self.c)"), HELPER),
    "aliased": (_plan_module("from .helper import wipe as w", "w(self.c)"), HELPER),
    "module-alias": (_plan_module("from . import helper", "helper.wipe(self.c)"), HELPER),
    "absolute": (_plan_module("from crc.aws.helper import wipe", "wipe(self.c)"), HELPER),
    "import-as": (
        _plan_module("import crc.aws.helper as helper", "helper.wipe(self.c)"),
        HELPER,
    ),
    "via-self-method": (
        "from .helper import wipe\n\n\nclass C:\n    def _go(self):\n        wipe(self.c)\n\n"
        "    def plan(self):\n        self._go()\n",
        HELPER,
    ),
    "via-local-func": (
        "from .helper import wipe\n\n\ndef _local(c):\n    wipe(c)\n\n\n"
        "class C:\n    def plan(self):\n        _local(self.c)\n",
        HELPER,
    ),
    "import-plain": (
        _plan_module("import crc.aws.helper", "crc.aws.helper.wipe(self.c)"),
        HELPER,
    ),
    "transitive": (_plan_module("from .chain import outer", "outer(self.c)"), HELPER),
}


def _project(root: Path, source: str, helper: str) -> None:
    package = root / "crc" / "aws"
    package.mkdir(parents=True)
    (package / "new_mod.py").write_text(source, encoding="utf-8")
    (package / "helper.py").write_text(helper, encoding="utf-8")
    (package / "chain.py").write_text(CHAINED, encoding="utf-8")


@pytest.mark.parametrize(
    ("source", "helper"), list(SIBLING_CASES.values()), ids=list(SIBLING_CASES)
)
def test_arch005_selftest_follows_sibling_module_calls(
    tmp_path: Path, source: str, helper: str
) -> None:
    _project(tmp_path, source, helper)
    messages = check_plan_readonly_siblings(tmp_path, frozenset())
    assert len(messages) == 1
    assert messages[0].startswith("ARCH-005: crc/aws/helper.py:2 `.delete_volume(...)` in `wipe`")
    assert "crc/aws/new_mod.py" in messages[0]
    assert "\n\nREMEDIATION: " in messages[0]


def test_arch005_selftest_sibling_read_only_helper_passes(tmp_path: Path) -> None:
    source = (
        "from .helper import look\n\n\nclass C:\n    def plan(self):\n        return look(self.c)\n"
    )
    _project(tmp_path, source, HELPER)
    assert check_plan_readonly_siblings(tmp_path, frozenset()) == []


def test_arch005_selftest_sibling_mutation_outside_plan_passes(tmp_path: Path) -> None:
    source = (
        "from .helper import wipe\n\n\nclass C:\n    def plan(self):\n        return []\n\n"
        "    def apply(self, plan):\n        wipe(self.c)\n"
    )
    _project(tmp_path, source, HELPER)
    assert check_plan_readonly_siblings(tmp_path, frozenset()) == []


def test_arch005_selftest_sibling_scan_ignores_other_packages(tmp_path: Path) -> None:
    source = (
        "from crc.gcp.helper import wipe\n\n\nclass C:\n    def plan(self):\n        wipe(self.c)\n"
    )
    _project(tmp_path, source, HELPER)
    other = tmp_path / "crc" / "gcp"
    other.mkdir()
    (other / "helper.py").write_text(HELPER, encoding="utf-8")
    assert check_plan_readonly_siblings(tmp_path, frozenset()) == []


def test_arch005_selftest_sibling_scan_skips_legacy_plan_files(tmp_path: Path) -> None:
    _project(tmp_path, SIBLING_CASES["from-relative"][0], HELPER)
    legacy = frozenset({"crc/aws/new_mod.py"})
    assert check_plan_readonly_siblings(tmp_path, legacy) == []


def test_arch005_sibling_scan_passes_on_the_repository() -> None:
    problems = check_plan_readonly_siblings(REPO_ROOT, load_legacy(REPO_ROOT))
    assert not problems, "\n\n".join(problems)
