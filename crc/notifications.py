"""Compatibility shim: the Slack helpers moved to `crc.notify.slack`."""

from crc.notify.slack import (
    ITEMS_PER_MESSAGE,
    SLACK_SAFE_CHARS,
    _chunk_lines_into_messages,
    _format_item_row,
    human_readable_timedelta,
    notify_cleanup,
    send_slack_message,
)

__all__ = [
    "ITEMS_PER_MESSAGE",
    "SLACK_SAFE_CHARS",
    "_chunk_lines_into_messages",
    "_format_item_row",
    "human_readable_timedelta",
    "notify_cleanup",
    "send_slack_message",
]
