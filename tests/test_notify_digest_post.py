"""Posting the digest: one top post and its thread replies per channel."""

import logging
from pathlib import Path

import pytest

from crc.notify.digest_load import load_records
from crc.notify.digest_post import post_digest
from tests.helpers_digest import (
    FIXTURE_DIR,
    YBA,
    FakeSlack,
    check_limits,
    record,
    vm_ids,
    write_records,
)

STRESS = "db-stress-tests-cleanup-notifications"


def test_fixture_posts_one_top_message_per_channel_with_threaded_replies() -> None:
    client = FakeSlack()
    assert post_digest(client, load_records(FIXTURE_DIR)) == 0
    tops = [c for c in client.calls if "thread_ts" not in c]
    assert [c["channel"] for c in tops] == [
        f"#{YBA}",
        "#crc-untagged-resources-notifications",
        f"#{STRESS}",
    ]
    for call in client.calls:
        check_limits(call["blocks"])
        assert call["text"]
    yba_top_ts = "1700000000.000001"
    yba_replies = [c for c in client.calls if c.get("thread_ts") == yba_top_ts]
    assert len(yba_replies) == 9  # noqa: PLR2004
    assert all(c["channel"] == f"#{YBA}" for c in yba_replies)
    # replies come right after their top message, in summary order
    assert client.calls[1]["blocks"][0]["text"]["text"].startswith("🗑️ *AWS KMS 2 Days old*")
    stress_replies = [c for c in client.calls if c["channel"] == f"#{STRESS}" and "thread_ts" in c]
    assert len(stress_replies) == 1
    assert stress_replies[0]["thread_ts"] != yba_top_ts


def test_all_empty_run_posts_a_single_message(tmp_path: Path) -> None:
    client = FakeSlack()
    records = load_records(write_records(tmp_path, [record(), record(stage="Other")]))
    assert post_digest(client, records) == 0
    assert len(client.calls) == 1
    assert "Nothing to clean (2 checks)" in client.calls[0]["blocks"][1]["text"]["text"]


def test_failed_top_post_does_not_stop_other_channels(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    items = [
        record(channel="a-chan", sections=vm_ids(2)),
        record(channel="b-chan", sections=vm_ids(2)),
    ]
    client = FakeSlack(fail_channels=("a-chan",))
    with caplog.at_level(logging.ERROR):
        failed = post_digest(client, load_records(write_records(tmp_path, items)))
    assert failed == 1
    posted = [c for c in client.calls if c["channel"] == "#b-chan"]
    assert len(posted) == 2  # noqa: PLR2004
    assert "a-chan" in caplog.text
    assert not [c for c in client.calls if c["channel"] == "#a-chan" and "thread_ts" in c]


def test_failed_reply_is_not_a_failure_and_later_replies_go_on(tmp_path: Path) -> None:
    items = [record(sections=vm_ids(45))]
    client = FakeSlack(fail_replies=True)
    assert post_digest(client, load_records(write_records(tmp_path, items))) == 0
    assert len(client.calls) == 4  # noqa: PLR2004


def test_channel_id_and_hash_name_are_not_prefixed_twice(tmp_path: Path) -> None:
    items = [record(channel="C0123ABCD"), record(channel="#already")]
    client = FakeSlack()
    post_digest(client, load_records(write_records(tmp_path, items)))
    assert [c["channel"] for c in client.calls] == ["C0123ABCD", "#already"]
