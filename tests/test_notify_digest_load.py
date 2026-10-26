"""Reading digest records: skip bad files with one WARNING, ignore unknown keys."""

import logging
from pathlib import Path

import pytest

from crc.notify.digest_load import load_records
from tests.helpers_digest import FIXTURE_DIR, YBA, record, vm_ids, write_records


def test_fixture_loads_every_record() -> None:
    records = load_records(FIXTURE_DIR)
    assert len(records) == len(list(FIXTURE_DIR.glob("*.json")))
    assert {r.channel for r in records} >= {YBA, "db-stress-tests-cleanup-notifications"}


def test_bad_files_are_skipped_with_one_warning_each(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    write_records(tmp_path, [record(sections=vm_ids(2)), record(version=2)])
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "list.json").write_text("[1]", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("ignored", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        records = load_records(tmp_path)
    assert [r.count for r in records] == [2]
    warnings = [m for m in caplog.records if m.levelno == logging.WARNING]
    assert len(warnings) == 3  # noqa: PLR2004


def test_unknown_keys_are_ignored_and_missing_dir_is_empty(tmp_path: Path) -> None:
    write_records(tmp_path, [record(future_key={"a": 1})])
    assert len(load_records(tmp_path)) == 1
    assert load_records(tmp_path / "absent") == []


def test_wrong_shape_is_skipped(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    write_records(tmp_path, [record(sections="nope"), record(by_owner=[1]), record(channel=None)])
    with caplog.at_level(logging.WARNING):
        assert load_records(tmp_path) == []
    assert len(caplog.records) == 3  # noqa: PLR2004


def test_owner_grouped_and_empty_flags(tmp_path: Path) -> None:
    write_records(tmp_path, [record(by_owner={"a": ["x", "y"]}), record()])
    owner, empty = sorted(load_records(tmp_path), key=lambda r: r.count, reverse=True)
    assert owner.owner_grouped
    assert owner.count == 2  # noqa: PLR2004
    assert empty.is_empty
