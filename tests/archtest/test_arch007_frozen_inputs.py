"""ARCH-007 check and self-tests."""

from pathlib import Path

from tests.archtest._common import REPO_ROOT
from tests.archtest.frozen_inputs import (
    MANIFEST_PATH,
    check_frozen_inputs,
    compute_manifest,
    load_manifest,
)


def _project(root: Path) -> dict[str, str]:
    (root / "tests" / "legacy").mkdir(parents=True)
    (root / "tests" / "legacy" / "a.txt").write_text("oracle a\n", encoding="utf-8")
    (root / "requirements.txt").write_text("boto3==1\n", encoding="utf-8")
    (root / "constraints.txt").write_text("boto3==1.0\n", encoding="utf-8")
    return compute_manifest(root)


def test_arch007_frozen_inputs_match_the_manifest() -> None:
    problems = check_frozen_inputs(REPO_ROOT, load_manifest())
    assert not problems, "\n\n".join(problems)


def test_arch007_manifest_covers_every_oracle_and_requirements() -> None:
    manifest = load_manifest()
    assert {"requirements.txt", "constraints.txt"} <= set(manifest)
    legacy = {
        p.relative_to(REPO_ROOT).as_posix() for p in (REPO_ROOT / "tests/legacy").glob("*.txt")
    }
    assert legacy
    assert legacy <= set(manifest)
    assert MANIFEST_PATH.name == "frozen_inputs.json"


def test_arch007_selftest_passes_on_unchanged_inputs(tmp_path: Path) -> None:
    manifest = _project(tmp_path)
    assert check_frozen_inputs(tmp_path, manifest) == []


def test_arch007_selftest_fires_on_edited_oracle(tmp_path: Path) -> None:
    manifest = _project(tmp_path)
    (tmp_path / "tests" / "legacy" / "a.txt").write_text("edited\n", encoding="utf-8")
    messages = check_frozen_inputs(tmp_path, manifest)
    assert len(messages) == 1
    assert messages[0].startswith("ARCH-007: tests/legacy/a.txt changed")
    assert "\n\nREMEDIATION: " in messages[0]
    assert "Never run" in messages[0]


def test_arch007_selftest_fires_on_new_dependency(tmp_path: Path) -> None:
    manifest = _project(tmp_path)
    (tmp_path / "requirements.txt").write_text("boto3==1\npydantic==2\n", encoding="utf-8")
    assert len(check_frozen_inputs(tmp_path, manifest)) == 1


def test_arch007_selftest_fires_on_changed_constraints(tmp_path: Path) -> None:
    manifest = _project(tmp_path)
    (tmp_path / "constraints.txt").write_text("boto3==2.0\n", encoding="utf-8")
    messages = check_frozen_inputs(tmp_path, manifest)
    assert len(messages) == 1
    assert messages[0].startswith("ARCH-007: constraints.txt changed")


def test_arch007_selftest_fires_on_added_and_deleted_oracle(tmp_path: Path) -> None:
    manifest = _project(tmp_path)
    (tmp_path / "tests" / "legacy" / "b.txt").write_text("new\n", encoding="utf-8")
    (tmp_path / "tests" / "legacy" / "a.txt").unlink()
    messages = check_frozen_inputs(tmp_path, manifest)
    assert len(messages) == len({"a.txt", "b.txt"})
    assert any("missing from the repository" in m for m in messages)
    assert any("not in the frozen manifest" in m for m in messages)
