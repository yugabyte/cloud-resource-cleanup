"""Block Kit messages of the digest: the top message and the thread replies of one channel."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from slack_sdk.models.blocks import ContextBlock, HeaderBlock, SectionBlock
from slack_sdk.models.blocks.basic_components import MarkdownTextObject

from crc.notify.digest_load import DigestRecord
from crc.notify.digest_text import (
    FAILURE_ERROR_CHARS,
    ID_CHARS,
    _cut,
    _escape,
    _fallback_text,
    _group_line,
    _group_records,
    _idle_line,
    _stage_label,
    _status,
)
from crc.notify.slack_chunks import (
    ITEMS_PER_MESSAGE,
    SLACK_SAFE_CHARS,
    _chunk_lines_into_messages,
)

MAX_BLOCKS = 50
MAX_SECTION_CHARS = 3000
MAX_HEADER_CHARS = 150


@dataclass(frozen=True)
class DigestMessage:
    """One Slack message: the fallback text and the Block Kit blocks as dicts."""

    text: str
    blocks: list[dict[str, Any]]


def _section(text: str) -> dict[str, Any]:
    return SectionBlock(text=MarkdownTextObject(text=text[:MAX_SECTION_CHARS])).to_dict()


def _header(record_build: str | None) -> dict[str, Any]:
    title = f"CRC cleanup · #{record_build}" if record_build else "CRC cleanup"
    return HeaderBlock(text=title[:MAX_HEADER_CHARS]).to_dict()


def _context(records: Sequence[DigestRecord]) -> dict[str, Any]:
    first = records[0]
    number = first.build_number
    build = f"Build #{number}" if number else "Build"
    if first.build_url:
        build = f"<{first.build_url}|{build}>"
    parts = [build] if (first.build_url or number) else []
    if first.job_name:
        parts.append(_escape(first.job_name))
    parts.append(f"{len(records)} command{'' if len(records) == 1 else 's'}")
    stamps = [r.written_at for r in records if r.written_at]
    if stamps:
        latest = max(stamps).replace("T", " ").removesuffix("Z")
        parts.append(f"{latest[:16]} UTC")
    return ContextBlock(elements=[MarkdownTextObject(text=" · ".join(parts))]).to_dict()


def _summary_sections(lines: Sequence[str], room: int) -> list[dict[str, Any]]:
    """Summary lines in sections of at most `SLACK_SAFE_CHARS`; at most `room` sections."""
    chunks: list[list[str]] = [[]]
    size = 0
    for line in lines:
        if chunks[-1] and size + len(line) + 1 > SLACK_SAFE_CHARS:
            chunks.append([])
            size = 0
        chunks[-1].append(line[:SLACK_SAFE_CHARS])
        size += len(line) + 1
    sections = [_section("\n".join(chunk)) for chunk in chunks if chunk]
    if len(sections) > room:
        sections = sections[: room - 1]
        sections.append(_section("…more lines cut, see the thread and the build log"))
    return sections


def build_top_message(records: Sequence[DigestRecord]) -> DigestMessage:
    """The top-level message of one channel: header, summary lines, context."""
    groups = _group_records(records)
    lines = [_group_line(g) for g in groups]
    idle = _idle_line(records, groups)
    if idle:
        lines.append(idle)
    number = records[0].build_number
    blocks = [_header(number)]
    blocks.extend(_summary_sections(lines, MAX_BLOCKS - 2))
    blocks.append(_context(records))
    return DigestMessage(text=_fallback_text(number, groups), blocks=blocks)


def _bullets(ids: Sequence[str]) -> list[str]:
    return [f"• `{_escape(_cut(item, ID_CHARS)).replace('`', chr(39))}`" for item in ids]


def _chunked(title: str, lines: list[str]) -> list[DigestMessage]:
    """Thread replies for `lines` under the header line `title` (`Part i/n` when split)."""
    plain = title.replace("*", "")
    messages = _chunk_lines_into_messages(
        [_section(title)], lines, max_chars=SLACK_SAFE_CHARS, max_items=ITEMS_PER_MESSAGE
    )
    return [DigestMessage(text=_cut(plain, MAX_HEADER_CHARS), blocks=blocks) for blocks in messages]


def _record_replies(record: DigestRecord) -> list[DigestMessage]:
    emoji, verb = _status(record)
    label = _escape(_cut(_stage_label(record), 120))
    replies: list[DigestMessage] = []
    if not record.owner_grouped:
        for section, ids in record.sections:
            if ids:
                kind = _escape(f"{record.cloud} {section}".strip())
                title = f"*{label}* · {kind} · {len(ids)} {verb}"
                replies.extend(_chunked(f"{emoji} {title}", _bullets(ids)))
    if record.failures:
        lines = [
            f"• `{_escape(_cut(fid, ID_CHARS))}` — {_escape(_cut(err, FAILURE_ERROR_CHARS))}"
            for fid, err in record.failures
        ]
        replies.extend(_chunked(f"⚠️ *{label}* · {len(lines)} failed", lines))
    return replies


def build_replies(records: Sequence[DigestRecord]) -> list[DigestMessage]:
    """Thread replies in summary order: one per non-empty section and per record with failures."""
    ordered = sorted(
        (r for r in records if not r.is_empty),
        key=lambda r: (r.cloud, _stage_label(r), r.written_at),
    )
    return [reply for record in ordered for reply in _record_replies(record)]
