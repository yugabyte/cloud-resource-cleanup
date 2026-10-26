"""Block Kit text of the digest: top message, thread replies, limits."""

from pathlib import Path
from typing import Any

from crc.notify.digest_blocks import build_replies, build_top_message
from crc.notify.digest_load import DigestRecord, load_records
from tests.helpers_digest import FIXTURE_DIR, YBA, check_limits, record, vm_ids, write_records


def _load(tmp_path: Path, items: list[dict[str, Any]]) -> list[DigestRecord]:
    return load_records(write_records(tmp_path, items))


def _yba() -> list[DigestRecord]:
    return [r for r in load_records(FIXTURE_DIR) if r.channel == YBA]


def test_top_message_of_the_fixture() -> None:
    top = build_top_message(_yba())
    header, summary, context = top.blocks
    assert header["text"]["text"] == "CRC cleanup · #16654"
    lines = summary["text"]["text"].split("\n")
    assert "🗑️ *AWS VMs 6 Hours old* · aws vm · 34 deleted · ⚠️ 1 failed" in lines
    assert any(
        ln.startswith("🔍 *Dry Run: iTest AWS VMs with Autoclean=False label*")
        and ln.endswith("7 would be deleted")
        for ln in lines
    )
    assert "🗑️ *Azure VMs and NICs 6 Hours old* · azure vm · 7 VM, 7 NIC deleted" in lines
    assert lines[-1] == "✅ 12 other checks found nothing"
    assert lines.index("🗑️ *AWS KMS 2 Days old* · aws kms · 5 deleted") < lines.index(
        "🗑️ *Azure VMs and NICs 6 Hours old* · azure vm · 7 VM, 7 NIC deleted"
    )
    ctx = context["elements"][0]["text"]
    assert ctx.startswith("<https://jenkins.dev.yugabyte.com/job/")
    assert "|Build #16654>" in ctx
    assert "18 commands" in ctx
    assert top.text == "CRC cleanup #16654: 54 deleted, 7 would be deleted, 3 failed"
    check_limits(top.blocks)


def test_stopped_and_owner_lines(tmp_path: Path) -> None:
    items = [
        record(operation="stop", sections=vm_ids(3)),
        record(stage="Autoclean", by_owner={"a": ["i-1"], "b": ["i-2"]}, dry_run=True),
    ]
    top = build_top_message(_load(tmp_path, items))
    text = top.blocks[1]["text"]["text"]
    assert "⏸️ *AWS VMs 6 Hours old* · aws vm · 3 stopped" in text
    assert "🔍 *Autoclean* · aws vm · 2 would be deleted · owners pinged separately" in text


def test_all_empty_is_one_nothing_to_clean_message(tmp_path: Path) -> None:
    records = _load(tmp_path, [record(), record(stage="Azure IPs", cloud="azure", kind="ip")])
    top = build_top_message(records)
    assert top.blocks[1]["text"]["text"] == "✅ Nothing to clean (2 checks)"
    assert top.text == "CRC cleanup #7: nothing to clean"
    assert build_replies(records) == []


def test_no_build_number_and_label_without_stage(tmp_path: Path) -> None:
    items = [
        record(
            build_number=None, build_url=None, stage=None, resource_suffix="X", sections=vm_ids(1)
        )
    ]
    top = build_top_message(_load(tmp_path, items))
    assert top.blocks[0]["text"]["text"] == "CRC cleanup"
    assert top.blocks[1]["text"]["text"] == "🗑️ *aws vm (X)* · 1 deleted"
    assert top.text == "CRC cleanup: 1 deleted"


def test_replies_chunk_at_twenty_ids_with_part_labels(tmp_path: Path) -> None:
    replies = build_replies(_load(tmp_path, [record(sections=vm_ids(45))]))
    assert len(replies) == 3  # noqa: PLR2004
    sizes = [r.blocks[1]["text"]["text"].count("\n") + 1 for r in replies]
    assert sizes == [20, 20, 5]
    assert replies[0].blocks[0]["text"]["text"].endswith("45 deleted — Part 1/3")
    assert replies[0].blocks[1]["text"]["text"].startswith("• `i-0000`\n• `i-0001`")
    for reply in replies:
        check_limits(reply.blocks)


def test_long_ids_split_on_characters_and_stay_within_limits(tmp_path: Path) -> None:
    ids = [{"label": "VM", "ids": ["x" * 500 + str(n) for n in range(15)]}]
    replies = build_replies(_load(tmp_path, [record(sections=ids)]))
    assert len(replies) >= 2  # noqa: PLR2004
    for reply in replies:
        check_limits(reply.blocks)


def test_failures_reply_cuts_error_and_owner_records_have_no_id_reply(tmp_path: Path) -> None:
    failures = [{"id": "i-9", "error": "E" * 500}]
    items = [
        record(failures=failures),
        record(stage="Owner", by_owner={"a": ["i-1"]}, sections=vm_ids(1)),
    ]
    replies = build_replies(_load(tmp_path, items))
    assert len(replies) == 1
    line = replies[0].blocks[1]["text"]["text"]
    assert line.startswith("• `i-9` — EEEE")
    assert len(line) < 260  # noqa: PLR2004
    assert line.endswith("…")


def test_many_summary_lines_stay_within_block_limits(tmp_path: Path) -> None:
    items = [record(stage=f"Stage {n:04d} " + "s" * 150, sections=vm_ids(1)) for n in range(300)]
    top = build_top_message(_load(tmp_path, items))
    check_limits(top.blocks)
    assert top.blocks[0]["type"] == "header"
    assert top.blocks[-1]["type"] == "context"


def test_slack_control_characters_are_escaped(tmp_path: Path) -> None:
    items = [record(stage="A <b> & c", sections=[{"label": "VM", "ids": ["<@U1>"]}])]
    records = _load(tmp_path, items)
    assert "*A &lt;b&gt; &amp; c*" in build_top_message(records).blocks[1]["text"]["text"]
    assert "`&lt;@U1&gt;`" in build_replies(records)[0].blocks[1]["text"]["text"]


def test_context_says_one_command_in_the_singular(tmp_path: Path) -> None:
    top = build_top_message(load_records(write_records(tmp_path, [record()])))
    context = next(b for b in top.blocks if b["type"] == "context")
    assert " · 1 command · " in context["elements"][0]["text"]
