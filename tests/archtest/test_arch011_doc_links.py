"""ARCH-011 check and self-tests: links and backticked repo paths of the live markdown."""

from pathlib import Path

import pytest

from tests.archtest import doc_links
from tests.archtest._common import REPO_ROOT
from tests.archtest.doc_links import check_doc_links


def _write(root: Path, name: str, text: str = "x\n") -> None:
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def test_arch011_every_relative_link_resolves() -> None:
    problems = check_doc_links(REPO_ROOT)
    assert not problems, "\n\n".join(problems)


def test_arch011_selftest_fires_on_missing_target(tmp_path: Path) -> None:
    _write(tmp_path, "AGENTS.md", "intro\nsee [plan](docs/PLAN.md)\n")
    messages = check_doc_links(tmp_path)
    assert len(messages) == 1
    assert messages[0].startswith("ARCH-011: AGENTS.md:2 links to docs/PLAN.md")
    assert "\n\nREMEDIATION: " in messages[0]


def test_arch011_selftest_resolves_relative_to_the_document(tmp_path: Path) -> None:
    _write(tmp_path, "docs/a.md", "[b](b.md) [up](../AGENTS.md) [no](c.md)\n")
    _write(tmp_path, "docs/b.md")
    _write(tmp_path, "AGENTS.md")
    messages = check_doc_links(tmp_path)
    assert [m.split("\n")[0] for m in messages] == [
        "ARCH-011: docs/a.md:1 links to c.md, which does not exist"
    ]


def test_arch011_selftest_checks_cursor_files_and_directories(tmp_path: Path) -> None:
    _write(tmp_path, ".cursor/skills/s/SKILL.md", "[ok](review.md) [dir](../s) [bad](gone.md)\n")
    _write(tmp_path, ".cursor/skills/s/review.md")
    _write(tmp_path, ".cursor/rules/00.mdc", "[bad](../../nope.md)\n")
    messages = check_doc_links(tmp_path)
    assert [m.split("\n")[0].split(",")[0] for m in messages] == [
        "ARCH-011: .cursor/rules/00.mdc:1 links to ../../nope.md",
        "ARCH-011: .cursor/skills/s/SKILL.md:1 links to gone.md",
    ]


def test_arch011_selftest_ignores_anchors_external_code_and_archive(tmp_path: Path) -> None:
    text = (
        "[a](ARCHITECTURE.md#section) [web](https://example.com/x) [mail](mailto:a@b.c) "
        "[here](#top) `[code](missing.md)`\n```\n[fenced](missing.md)\n```\n"
    )
    _write(tmp_path, "AGENTS.md", text)
    _write(tmp_path, "ARCHITECTURE.md")
    _write(tmp_path, "docs/archive/old.md", "[dead](missing.md) `crc/gone.py`\n")
    _write(tmp_path, "docs/archive/reviews/old.md", "[dead](missing.md)\n")
    _write(tmp_path, "notes.txt", "[dead](missing.md)\n")
    assert check_doc_links(tmp_path) == []


def test_arch011_selftest_scans_every_markdown_file_recursively(tmp_path: Path) -> None:
    _write(tmp_path, "docs/sub/deep/old.md", "[dead](missing.md)\n")
    _write(tmp_path, "README.md", "[dead](gone.md)\n")
    _write(tmp_path, "crc/aws/vm/README.md", "[dead](nope.md)\n")
    _write(tmp_path, "tests/archtest/README.md", "[dead](nada.md)\n")
    messages = check_doc_links(tmp_path)
    assert sorted(m.split("\n")[0].split(" links")[0] for m in messages) == [
        "ARCH-011: README.md:1",
        "ARCH-011: crc/aws/vm/README.md:1",
        "ARCH-011: docs/sub/deep/old.md:1",
        "ARCH-011: tests/archtest/README.md:1",
    ]


def test_arch011_selftest_backticked_paths_must_exist(tmp_path: Path) -> None:
    _write(tmp_path, "crc/cli/main.py")
    _write(tmp_path, "tests/test_a.py")
    _write(tmp_path, ".github/workflows/ci.yml")
    text = (
        "ok `crc/cli/main.py` `crc/cli/main.py:12-30` `tests/test_a.py::test_x` "
        "`.github/workflows/ci.yml`\n"
        "bad `crc/cli/vm_ops.py` `tests/test_gone.py::test_y` `docs/refactor/PLAN.md:3`\n"
    )
    _write(tmp_path, "AGENTS.md", text)
    messages = check_doc_links(tmp_path)
    assert [m.split("\n")[0].split(", which")[0] for m in messages] == [
        "ARCH-011: AGENTS.md:2 names `crc/cli/vm_ops.py`",
        "ARCH-011: AGENTS.md:2 names `tests/test_gone.py`",
        "ARCH-011: AGENTS.md:2 names `docs/refactor/PLAN.md`",
    ]
    assert "\n\nREMEDIATION: " in messages[0]


def test_arch011_selftest_globs_and_placeholders_need_a_match(tmp_path: Path) -> None:
    _write(tmp_path, "crc/aws/vm/README.md")
    _write(tmp_path, "tests/helpers_routed_gcp.py")
    text = (
        "ok `crc/**/README.md` `crc/<cloud>/<kind>/README.md` `tests/helpers_routed_<cloud>.py` "
        "`crc/aws/*/README.md`\n"
        "bad `crc/*/gone.py` `tests/helpers_nope_<cloud>.py`\n"
    )
    _write(tmp_path, "AGENTS.md", text)
    messages = check_doc_links(tmp_path)
    assert [m.split("\n")[0].split(", which")[0] for m in messages] == [
        "ARCH-011: AGENTS.md:2 names `crc/*/gone.py`",
        "ARCH-011: AGENTS.md:2 names `tests/helpers_nope_<cloud>.py`",
    ]


def test_arch011_selftest_directories_and_ignored_spans(tmp_path: Path) -> None:
    _write(tmp_path, "crc/aws/keypairs/README.md")
    _write(tmp_path, "crc.py")
    text = (
        "dir `crc/aws/keypairs/` missing dir `crc/aws/gone/`\n"
        "not paths `python crc.py` `crc.py` `main:crc/aws/disk.py` `crc/aws/disk` `see crc/x.py` "
        "`other/dir/file.py` `crc/cli/` `crc/`\n"
        "```\n`crc/in/fence.py`\n```\n"
    )
    _write(tmp_path, "AGENTS.md", text)
    messages = check_doc_links(tmp_path)
    assert [m.split("\n")[0].split(", which")[0] for m in messages] == [
        "ARCH-011: AGENTS.md:1 names `crc/aws/gone/`",
        "ARCH-011: AGENTS.md:2 names `crc/cli/`",
    ]


def test_arch011_selftest_bug_register_rows_are_exempt_from_path_checks(tmp_path: Path) -> None:
    rows = (
        "header names `docs/refactor/PLAN.md`\n"
        "| BUG-001 | title `crc/gone.py` | `crc/old.py:3` | yes | cosmetic | low | R1 | open |\n"
        "| NEW | title | `crc/older.py:3` | yes | cosmetic | low | R1 | open |\n"
        "footer names `crc/footer_gone.py`\n"
    )
    _write(tmp_path, "docs/refactor/BUGS.md", rows)
    _write(tmp_path, "docs/other.md", "| BUG-001 | `crc/gone.py` |\n")
    messages = check_doc_links(tmp_path)
    assert [m.split("\n")[0].split(", which")[0] for m in messages] == [
        "ARCH-011: docs/other.md:1 names `crc/gone.py`",
        "ARCH-011: docs/refactor/BUGS.md:1 names `docs/refactor/PLAN.md`",
        "ARCH-011: docs/refactor/BUGS.md:4 names `crc/footer_gone.py`",
    ]


def test_arch011_selftest_skips_symlinks_and_cache_directories(tmp_path: Path) -> None:
    _write(tmp_path, "AGENTS.md", "[x](missing.md)\n")
    (tmp_path / "CLAUDE.md").symlink_to("AGENTS.md")
    _write(tmp_path, ".venv/lib/pkg/README.md", "[dead](missing.md)\n")
    _write(tmp_path, "node_modules/pkg/README.md", "[dead](missing.md)\n")
    messages = check_doc_links(tmp_path)
    assert [m.split("\n")[0].split(" links")[0] for m in messages] == ["ARCH-011: AGENTS.md:1"]


def test_arch011_selftest_anchor_on_missing_file_fires(tmp_path: Path) -> None:
    _write(tmp_path, "AGENTS.md", "[x](NOPE.md#part)\n")
    assert len(check_doc_links(tmp_path)) == 1


def test_arch011_selftest_root_relative_links(tmp_path: Path) -> None:
    _write(tmp_path, "docs/a.md", "[ok](/AGENTS.md) [bad](/gone.md)\n")
    _write(tmp_path, "AGENTS.md")
    messages = check_doc_links(tmp_path)
    assert len(messages) == 1
    assert "links to /gone.md" in messages[0]


def _first_lines(messages: list[str]) -> list[str]:
    return [m.split("\n")[0] for m in messages]


def test_arch011_selftest_bare_filename_resolves_next_to_the_document(tmp_path: Path) -> None:
    _write(
        tmp_path, "crc/cli/README.md", "ok `bridge.py` `bridge.py:12` `routes.md` gone `ghost.py`\n"
    )
    _write(tmp_path, "crc/cli/bridge.py")
    _write(tmp_path, "crc/cli/routes.md")
    messages = check_doc_links(tmp_path)
    assert _first_lines(messages) == [
        "ARCH-011: crc/cli/README.md:1 names `ghost.py`, "
        "found in neither its directory nor the root"
    ]
    assert "\n\nREMEDIATION: " in messages[0]
    assert "BARE_NAME_ALLOWLIST" in messages[0]


def test_arch011_selftest_bare_filename_falls_back_to_the_repo_root(tmp_path: Path) -> None:
    for name in ("BUGS.md", "crc.py", "constraints.txt", "settings.json", "ci.yml"):
        _write(tmp_path, name)
    text = "`BUGS.md` `crc.py` `constraints.txt` `settings.json` `ci.yml` `other.yml`\n"
    _write(tmp_path, "docs/refactor/bugs/README.md", text)
    assert _first_lines(check_doc_links(tmp_path)) == [
        "ARCH-011: docs/refactor/bugs/README.md:1 names `other.yml`, "
        "found in neither its directory nor the root"
    ]


def test_arch011_selftest_bare_filename_ignores_non_files_fences_and_register_rows(
    tmp_path: Path,
) -> None:
    text = (
        "not names `python crc.py` `crc` `.py` `x.pyc` `a.b.c` `Makefile` `<name>.py` `*.py`\n"
        "```\n`fenced.py`\n```\n"
        "| BUG-001 | `old.py` | `gone.md` | yes | cosmetic | low | R1 | open |\n"
        "footer `footer_gone.py`\n"
    )
    _write(tmp_path, "docs/refactor/BUGS.md", text)
    assert _first_lines(check_doc_links(tmp_path)) == [
        "ARCH-011: docs/refactor/BUGS.md:6 names `footer_gone.py`, "
        "found in neither its directory nor the root"
    ]


def test_arch011_selftest_bare_filename_allowlist_needs_a_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, "AGENTS.md", "`main.py` `other.py`\n")
    monkeypatch.setitem(doc_links.BARE_NAME_ALLOWLIST, "main.py", "names the file on main only")
    assert _first_lines(check_doc_links(tmp_path)) == [
        "ARCH-011: AGENTS.md:1 names `other.py`, found in neither its directory nor the root"
    ]


def test_arch011_bare_name_allowlist_entries_have_reasons() -> None:
    assert all(reason.strip() for reason in doc_links.BARE_NAME_ALLOWLIST.values())


def test_arch011_selftest_skips_markdown_behind_a_symlinked_directory(tmp_path: Path) -> None:
    _write(tmp_path, ".cursor/skills/s/SKILL.md", "[bad](gone.md)\n")
    (tmp_path / ".agents").mkdir()
    (tmp_path / ".agents" / "skills").symlink_to("../.cursor/skills")
    messages = check_doc_links(tmp_path)
    assert _first_lines(messages) == [
        "ARCH-011: .cursor/skills/s/SKILL.md:1 links to gone.md, which does not exist"
    ]
