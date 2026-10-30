"""Posting of the digest: one top message per channel, then its thread replies."""

import logging
from collections.abc import Sequence
from typing import Any

from crc.notify.digest_blocks import DigestMessage, build_replies, build_top_message
from crc.notify.digest_load import DigestRecord
from crc.notify.slack import SlackClient, send_slack_message

logger = logging.getLogger(__name__)


class _ThreadClient:
    """Adds `thread_ts` to every `chat_postMessage`, so `send_slack_message` can be reused."""

    def __init__(self, client: SlackClient, thread_ts: str) -> None:
        self._client = client
        self._thread_ts = thread_ts

    def chat_postMessage(self, **kwargs: Any) -> Any:  # noqa: N802 - Slack SDK method name
        return self._client.chat_postMessage(thread_ts=self._thread_ts, **kwargs)


def build_messages(records: Sequence[DigestRecord]) -> dict[str, list[DigestMessage]]:
    """Per channel (in order of first record) the top message followed by its replies."""
    by_channel: dict[str, list[DigestRecord]] = {}
    for record in records:
        by_channel.setdefault(record.channel, []).append(record)
    return {
        channel: [build_top_message(group), *build_replies(group)]
        for channel, group in by_channel.items()
    }


def _post_replies(
    client: SlackClient, channel: str, thread_ts: str, replies: Sequence[DigestMessage]
) -> None:
    threaded = _ThreadClient(client, thread_ts)
    for index, reply in enumerate(replies, start=1):
        try:
            send_slack_message(threaded, channel, text=reply.text, blocks=reply.blocks)
        except Exception as e:  # noqa: BLE001 - one lost reply must not stop the others
            logger.error("Digest reply %d/%d to %s failed: %s", index, len(replies), channel, e)


def _target(channel: str) -> str:
    """The channel as `chat_postMessage` wants it: a name gets the `#`, an id or `#name` stays."""
    if channel.startswith("#") or (channel.isalnum() and channel.isupper()):
        return channel
    return "#" + channel


def _post_channel(client: SlackClient, channel: str, messages: Sequence[DigestMessage]) -> bool:
    """Post the top message and its replies; False when the top message was not posted."""
    top, replies = messages[0], messages[1:]
    target = _target(channel)
    try:
        response = send_slack_message(client, target, text=top.text, blocks=top.blocks)
        thread_ts = response["ts"]
    except Exception as e:  # noqa: BLE001 - a failed channel is reported, the others go on
        logger.error("Digest top message to %s failed: %s", channel, e)
        return False
    _post_replies(client, target, str(thread_ts), replies)
    return True


def post_digest(client: SlackClient, records: Sequence[DigestRecord]) -> int:
    """Post the digest of `records`; the number of channels whose top message was not posted."""
    failed = 0
    for channel, messages in build_messages(records).items():
        if not _post_channel(client, channel, messages):
            failed += 1
    return failed
