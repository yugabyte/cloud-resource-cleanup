"""`crc_digest.py`: exit codes, dry run, missing token and directory."""

import json
import logging
import runpy
from pathlib import Path
from typing import Any

import pytest

import crc_digest
from crc.cli import digest
from tests.helpers_digest import FIXTURE_DIR, YBA, FakeSlack, record, vm_ids, write_records


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeSlack:
    client = FakeSlack()
    monkeypatch.setattr(digest, "WebClient", lambda token: client)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test-not-a-secret")
    monkeypatch.delenv("BUILD_NUMBER", raising=False)
    return client


def test_posts_and_exits_zero(fake: FakeSlack) -> None:
    assert digest.main(["--dir", str(FIXTURE_DIR)]) == 0
    assert len([c for c in fake.calls if "thread_ts" not in c]) == 3  # noqa: PLR2004


def test_entry_point_reexports_main() -> None:
    assert crc_digest.main is digest.main


def test_failed_top_post_exits_one(fake: FakeSlack, tmp_path: Path) -> None:
    fake.fail_channels = ("a-chan",)
    items = [record(channel="a-chan"), record(channel="b-chan")]
    assert digest.main(["--dir", str(write_records(tmp_path, items))]) == 1
    assert [c["channel"] for c in fake.calls] == ["#a-chan", "#b-chan"]


def test_missing_token_exits_two(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    assert digest.main(["--dir", str(FIXTURE_DIR)]) == 2  # noqa: PLR2004
    assert "SLACK_BOT_TOKEN" in capsys.readouterr().err


def test_missing_or_empty_dir_logs_and_exits_zero(
    fake: FakeSlack, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        assert digest.main(["--dir", str(tmp_path / "absent")]) == 0
        assert digest.main(["--dir", str(tmp_path)]) == 0
    assert caplog.text.count("no digest records in") == 2  # noqa: PLR2004
    assert fake.calls == []


def test_dry_run_prints_blocks_and_posts_nothing(
    fake: FakeSlack, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    assert digest.main(["--dir", str(FIXTURE_DIR), "--dry_run"]) == 0
    preview: list[dict[str, Any]] = json.loads(capsys.readouterr().out)
    assert fake.calls == []
    yba = next(c for c in preview if c["channel"] == YBA)
    assert [b["type"] for b in yba["top"]["blocks"]] == ["header", "section", "context"]
    assert len(yba["replies"]) == 9  # noqa: PLR2004


def test_dir_is_required() -> None:
    with pytest.raises(SystemExit) as exc:
        digest.main([])
    assert exc.value.code == 2  # noqa: PLR2004


def test_one_bad_file_does_not_stop_the_digest(fake: FakeSlack, tmp_path: Path) -> None:
    write_records(tmp_path, [record(sections=vm_ids(1))])
    (tmp_path / "bad.json").write_text("nope", encoding="utf-8")
    digest.main(["--dir", str(tmp_path)])
    assert len(fake.calls) == 2  # noqa: PLR2004


def test_entry_script_exits_with_main_status(tmp_path: Path) -> None:
    argv = ["crc_digest.py", "--dir", str(tmp_path), "--dry_run"]
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("sys.argv", argv)
        with pytest.raises(SystemExit) as exc:
            runpy.run_path(str(Path(crc_digest.__file__)), run_name="__main__")
    assert exc.value.code == 0


def test_records_of_another_build_are_dropped_with_a_warning(
    fake: FakeSlack,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("BUILD_NUMBER", "8")
    items = [
        record(build_number="7", stage="Old stage", sections=vm_ids(2)),
        record(build_number="8", stage="New stage", sections=vm_ids(1)),
    ]
    with caplog.at_level(logging.WARNING):
        assert digest.main(["--dir", str(write_records(tmp_path, items))]) == 0
    top = next(c for c in fake.calls if "thread_ts" not in c)
    text = json.dumps(top["blocks"])
    assert "New stage" in text
    assert "Old stage" not in text
    assert "dropping digest record of build 7 (this is build 8)" in caplog.text


def test_without_build_number_every_record_is_posted(
    fake: FakeSlack, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("BUILD_NUMBER", raising=False)
    items = [record(build_number="7", stage="A"), record(build_number="8", stage="B")]
    assert digest.main(["--dir", str(write_records(tmp_path, items))]) == 0
    top = next(c for c in fake.calls if "thread_ts" not in c)
    assert "2 commands" in json.dumps(top["blocks"])
