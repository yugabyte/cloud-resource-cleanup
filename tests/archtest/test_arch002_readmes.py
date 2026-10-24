"""ARCH-002 check and self-tests."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.archtest._common import REPO_ROOT, load_legacy, relpath
from tests.archtest.readmes import (
    README_EXCLUDED_DIRS,
    README_EXEMPT_DIRS,
    check_readmes,
    directories_with_code,
    documented_names,
    sections,
)

GOOD_README = """# pkg

## Purpose
Does things.

## Public API
- `Thing`: a class.
- `do_work()`: a function.

## Safety Invariants
None.

## Extension Rules
Be careful.
"""
MODULE = '__all__ = ["Thing"]\n\n\nclass Thing:\n    pass\n\n\ndef do_work() -> None:\n    pass\n'


def _package(root: Path, readme: str | None, module: str = MODULE) -> None:
    crc_dir = root / "crc"
    crc_dir.mkdir(parents=True, exist_ok=True)
    (crc_dir / "README.md").write_text(GOOD_README, encoding="utf-8")
    pkg = crc_dir / "core"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(module, encoding="utf-8")
    if readme is not None:
        (pkg / "README.md").write_text(readme, encoding="utf-8")


def _assert_formatted(messages: list[str]) -> None:
    assert messages
    for message in messages:
        assert message.startswith("ARCH-002: ")
        assert "\n\nREMEDIATION: " in message


def test_arch002_package_readmes() -> None:
    problems = check_readmes(REPO_ROOT, load_legacy(REPO_ROOT))
    assert not problems, "\n\n".join(problems)


def test_arch002_helpers_parse_sections_and_names() -> None:
    parsed = sections(GOOD_README)
    assert list(parsed) == ["Purpose", "Public API", "Safety Invariants", "Extension Rules"]
    assert documented_names(parsed["Public API"]) == ({"Thing", "do_work"}, [])


def test_arch002_selftest_passes_on_good_package(tmp_path: Path) -> None:
    _package(tmp_path, GOOD_README)
    assert check_readmes(tmp_path, frozenset()) == []


def test_arch002_selftest_legacy_only_package_skips_symbol_sync(tmp_path: Path) -> None:
    _package(tmp_path, GOOD_README.replace("`Thing`", "`Other`"))
    assert check_readmes(tmp_path, frozenset({"crc/core/__init__.py"})) == []


def test_arch002_selftest_legacy_only_package_still_needs_readme(tmp_path: Path) -> None:
    _package(tmp_path, None)
    messages = check_readmes(tmp_path, frozenset({"crc/core/__init__.py"}))
    _assert_formatted(messages)
    assert "crc/core/ contains Python code but has no README.md" in messages[0]


def test_arch002_selftest_legacy_only_package_needs_sections(tmp_path: Path) -> None:
    _package(tmp_path, GOOD_README.replace("## Safety Invariants", "## Other"))
    messages = check_readmes(tmp_path, frozenset({"crc/core/__init__.py"}))
    _assert_formatted(messages)
    assert "Safety Invariants" in messages[0]


def _write(root: Path, relative: str, text: str = "x\n") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_arch002_selftest_fires_on_any_directory_with_code(tmp_path: Path) -> None:
    _write(tmp_path, "tools/sub/mod.py")
    messages = check_readmes(tmp_path, frozenset())
    _assert_formatted(messages)
    assert [m.split("\n")[0] for m in messages] == [
        "ARCH-002: tools/ contains Python code but has no README.md",
        "ARCH-002: tools/sub/ contains Python code but has no README.md",
    ]


def test_arch002_selftest_fires_on_parent_directory_without_direct_files(tmp_path: Path) -> None:
    _write(tmp_path, "tools/sub/mod.py")
    _write(tmp_path, "tools/sub/README.md", GOOD_README)
    messages = check_readmes(tmp_path, frozenset())
    _assert_formatted(messages)
    assert [m.split("\n")[0] for m in messages] == [
        "ARCH-002: tools/ contains Python code but has no README.md"
    ]


def test_arch002_selftest_every_repo_directory_fires_when_readme_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy = load_legacy(REPO_ROOT)
    checked = directories_with_code(REPO_ROOT)
    assert REPO_ROOT / "tests" in checked
    assert REPO_ROOT / "docs" not in checked
    real_walk = os.walk
    real_is_file = Path.is_file

    for target in checked:
        target_readme = target / "README.md"
        where = relpath(REPO_ROOT, target)

        def fake_walk(
            top: Path, _target: Path = target
        ) -> Iterator[tuple[str, list[str], list[str]]]:
            for current, dirnames, filenames in real_walk(top):
                if Path(current) == _target:
                    filenames = [f for f in filenames if f != "README.md"]
                yield current, dirnames, filenames

        def fake_is_file(self: Path, _readme: Path = target_readme) -> bool:
            if self == _readme:
                return False
            return real_is_file(self)

        monkeypatch.setattr(os, "walk", fake_walk)
        monkeypatch.setattr(Path, "is_file", fake_is_file)
        messages = check_readmes(REPO_ROOT, legacy)
        _assert_formatted(messages)
        expected = f"{where}/ contains Python code but has no README.md"
        assert any(expected in m for m in messages), where


def test_arch002_selftest_passes_with_readme_in_every_directory(tmp_path: Path) -> None:
    for directory in ("tools/sub", "tests"):
        _write(tmp_path, f"{directory}/mod.py")
    for directory in ("tools", "tools/sub", "tests"):
        _write(tmp_path, f"{directory}/README.md", GOOD_README)
    assert check_readmes(tmp_path, frozenset()) == []


def test_arch002_selftest_non_package_readme_needs_sections(tmp_path: Path) -> None:
    _write(tmp_path, "tools/a.py")
    _write(tmp_path, "tools/README.md", "# tools\n\n## Purpose\nx\n")
    messages = check_readmes(tmp_path, frozenset())
    _assert_formatted(messages)
    assert "Public API" in messages[0]


def test_arch002_selftest_ignores_excluded_directories(tmp_path: Path) -> None:
    for name in sorted(README_EXCLUDED_DIRS):
        _write(tmp_path, f"{name}/inner/file.py")
    assert check_readmes(tmp_path, frozenset()) == []


def test_arch002_selftest_ignores_root_files(tmp_path: Path) -> None:
    _write(tmp_path, "Makefile")
    _write(tmp_path, "crc.py")
    assert set(README_EXEMPT_DIRS) == {""}
    assert check_readmes(tmp_path, frozenset()) == []


def test_arch002_selftest_directories_without_python_need_no_readme(tmp_path: Path) -> None:
    for relative in (
        "docs/archive/reviews/R1.md",
        "docs/logging.md",
        ".cursor/environment.json",
        ".cursor/rules/00-map.mdc",
        ".cursor/skills/crc-maintain/SKILL.md",
        ".github/PULL_REQUEST_TEMPLATE.md",
        "tests/legacy/crc_main.txt",
    ):
        _write(tmp_path, relative)
    _write(tmp_path, "tests/mod.py")
    _write(tmp_path, "tests/README.md", GOOD_README)
    assert check_readmes(tmp_path, frozenset()) == []


def test_arch002_selftest_directory_with_python_beside_docs_still_fires(tmp_path: Path) -> None:
    _write(tmp_path, "docs/notes.md")
    _write(tmp_path, "docs/tool/mod.py")
    messages = check_readmes(tmp_path, frozenset())
    _assert_formatted(messages)
    assert [m.split("\n")[0] for m in messages] == [
        "ARCH-002: docs/ contains Python code but has no README.md",
        "ARCH-002: docs/tool/ contains Python code but has no README.md",
    ]


def test_arch002_selftest_non_legacy_package_keeps_symbol_sync(tmp_path: Path) -> None:
    _package(tmp_path, GOOD_README.replace("`Thing`", "`Other`"))
    messages = check_readmes(tmp_path, frozenset())
    _assert_formatted(messages)
    assert "Thing" in messages[0]


def test_arch002_directories_with_code_skips_bytecode_only_dirs(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/mod.pyc")
    _write(tmp_path, "pkg2/mod.py")
    assert directories_with_code(tmp_path) == [tmp_path / "pkg2"]


def test_arch002_selftest_fires_without_readme(tmp_path: Path) -> None:
    _package(tmp_path, None)
    messages = check_readmes(tmp_path, frozenset())
    _assert_formatted(messages)
    assert "no README.md" in messages[0]


def test_arch002_selftest_fires_on_missing_section(tmp_path: Path) -> None:
    _package(tmp_path, GOOD_README.replace("## Extension Rules", "## Other"))
    messages = check_readmes(tmp_path, frozenset())
    _assert_formatted(messages)
    assert "Extension Rules" in messages[0]


def test_arch002_selftest_fires_on_undocumented_public_name(tmp_path: Path) -> None:
    extra = MODULE + "\n\ndef another() -> None:\n    pass\n"
    _package(tmp_path, GOOD_README, extra)
    messages = check_readmes(tmp_path, frozenset())
    _assert_formatted(messages)
    assert "another" in messages[0]


def test_arch002_selftest_fires_on_nonexistent_documented_name(tmp_path: Path) -> None:
    _package(tmp_path, GOOD_README.replace("`do_work()`", "`do_work()`, `ghost`"))
    messages = check_readmes(tmp_path, frozenset())
    _assert_formatted(messages)
    assert "ghost" in messages[0]


def test_arch002_selftest_fires_on_non_name_backticks(tmp_path: Path) -> None:
    _package(tmp_path, GOOD_README.replace("a class.", "see `crc.core.thing`."))
    messages = check_readmes(tmp_path, frozenset())
    _assert_formatted(messages)
    assert "not bare" in messages[0]
