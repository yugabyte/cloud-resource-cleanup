"""Splitting of Slack cleanup lines into message-sized block lists."""

import copy
from collections.abc import Sequence
from typing import Any

SLACK_SAFE_CHARS = 2800
ITEMS_PER_MESSAGE = 20


def _split_lines(lines: Sequence[str], max_chars: int, max_items: int) -> list[list[str]]:
    messages: list[list[str]] = []
    cur: list[str] = []
    cur_len = 0
    for line in lines:
        line_len = len(line) + 1
        if (len(cur) >= max_items) or (cur and cur_len + line_len > max_chars):
            messages.append(cur)
            cur = []
            cur_len = 0
        cur.append(line)
        cur_len += line_len
    if cur:
        messages.append(cur)
    return messages


def _chunk_lines_into_messages(
    header_block: list[dict[str, Any]],
    lines: Sequence[str],
    max_chars: int = SLACK_SAFE_CHARS,
    max_items: int = ITEMS_PER_MESSAGE,
) -> list[list[dict[str, Any]]]:
    """One block list per Slack message: at most `max_items` lines and `max_chars` characters
    per section. The header is deep-copied, never mutated."""
    messages = _split_lines(lines, max_chars, max_items)
    block_messages: list[list[dict[str, Any]]] = []
    total = len(messages)
    for idx, chunk in enumerate(messages, start=1):
        prefix = copy.deepcopy(header_block)
        if total > 1 and prefix and "text" in prefix[0]:
            original_text = prefix[0]["text"].get("text", "")
            prefix[0]["text"]["text"] = f"{original_text} — Part {idx}/{total}"
        blocks = prefix[:]
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(chunk)}})
        block_messages.append(blocks)

    return block_messages
