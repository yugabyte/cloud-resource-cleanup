"""Slack sink: the cleanup report messages, owner pings and the scan-tag cleanup summary.

The Slack client is injected (a `slack_sdk.WebClient` in production, a fake in tests);
nothing here creates one. The line splitting is in `slack_chunks`, the posting and owner
pings in `slack_owners`.
"""

import logging
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import dateutil.parser
from slack_sdk.errors import SlackApiError

from crc.core.cleaner import RunContext
from crc.core.report import DISK, Report
from crc.notify.slack_chunks import (
    ITEMS_PER_MESSAGE,
    SLACK_SAFE_CHARS,
    _chunk_lines_into_messages,
)
from crc.notify.slack_owners import NOT_FOUND, SlackClient, SlackPoster

logger = logging.getLogger(__name__)

__all__ = [
    "ITEMS_PER_MESSAGE",
    "NOT_FOUND",
    "SLACK_SAFE_CHARS",
    "SlackClient",
    "SlackNotifier",
    "_chunk_lines_into_messages",
    "human_readable_timedelta",
    "notify_cleanup",
    "send_slack_message",
]


def human_readable_timedelta(start_time: datetime | str | None) -> str:
    """Time since `start_time` (UTC datetime or ISO string) as '3d 04h 12m 05s'; '-' if unknown."""
    if not start_time:
        return "-"
    if isinstance(start_time, str):
        try:
            start_dt = dateutil.parser.isoparse(start_time)
        except Exception:  # noqa: BLE001 - display-only start time, falls back to a dash
            return "-"
    else:
        start_dt = start_time

    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=UTC)

    now = datetime.now(UTC)
    delta = now - start_dt
    if delta.total_seconds() < 0:
        return "0s"

    days = delta.days
    hours, rem = divmod(delta.seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or (days and (minutes or seconds)):
        parts.append(f"{hours:02d}h")
    if minutes or (hours and seconds):
        parts.append(f"{minutes:02d}m")
    parts.append(f"{seconds:02d}s")
    return " ".join(parts)


def _format_item_row(it: Mapping[str, Any]) -> str:
    """One instance row, with uptime when running. Keys: name, id, region, state, launch_time,
    console_url, instance_type."""
    name = it.get("name") or "-"
    rid = it.get("id") or "-"
    region = it.get("region") or "-"
    state = (it.get("state") or "").upper()
    launch = it.get("launch_time")
    uptime = human_readable_timedelta(launch) if state == "RUNNING" else "-"
    view = f" — <{it.get('console_url')}|View>" if it.get("console_url") else ""
    state_part = f" — {state}" if state else ""
    uptime_part = f" ({uptime})" if state == "RUNNING" else ""
    instance_type = it.get("instance_type") or "-"
    return f"• *{name}* | `{rid}` | {instance_type} | {region}{state_part}{uptime_part}{view}"


def send_slack_message(
    client: SlackClient,
    channel: str,
    text: str,
    blocks: list[dict[str, Any]],
    max_retries: int = 3,
) -> Any:
    """Retrying wrapper around `chat_postMessage`: a `SlackApiError` is retried up to
    `max_retries` attempts with a growing sleep, any other error is raised at once."""
    attempt = 0
    while True:
        try:
            return client.chat_postMessage(channel=channel, text=text, blocks=blocks)
        except SlackApiError as e:
            attempt += 1
            err = ""
            try:
                err = e.response.get("error")
            except Exception:  # noqa: BLE001 - retry loop, error text falls back to str(e)
                err = str(e)
            logger.warning(
                "Slack API error posting to %s: %s (attempt %d/%d)",
                channel,
                err,
                attempt,
                max_retries,
            )
            if attempt >= max_retries:
                logger.exception("Max retries reached sending Slack message to %s", channel)
                raise
            time.sleep(1 + attempt)
        except Exception as e:
            logger.exception("Unexpected error sending Slack message: %s", e)
            raise


def _cleanup_lines(items: Sequence[Mapping[str, Any]]) -> list[str]:
    running = [it for it in items if (it.get("state") or "").upper() == "RUNNING"]
    stopped = [it for it in items if (it.get("state") or "").upper() != "RUNNING"]
    lines: list[str] = []
    if running:
        lines.append("*Running instances*")
        lines.extend(_format_item_row(it) for it in running)
    if stopped:
        if lines:
            lines.append("")
        lines.append("*Stopped / Other instances*")
        lines.extend(_format_item_row(it) for it in stopped)
    return lines


def notify_cleanup(  # noqa: PLR0913, PLR0917 - the seven positional parameters are the public signature
    slack_client: SlackClient,
    slack_channel: str,
    operation: str,
    cloud: str,
    resource_type: str,
    items: Sequence[Mapping[str, Any]],
    dry_run: bool,
) -> None:
    """Send every item to Slack, split over as many messages as needed.

    A message part that fails after its retries is logged and skipped so later parts are still
    sent. `items` are dicts with keys including `state` and `launch_time`.
    """
    if not slack_client:
        raise ValueError("slack_client is required")

    count = len(items)
    prefix = "DRY RUN: " if dry_run else ""
    header_text = f"{prefix}{operation.upper()} — {count} {cloud.upper()} {resource_type.upper()}"
    header_blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{header_text}*"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"{cloud} • {resource_type}"}]},
        {"type": "divider"},
    ]
    lines = _cleanup_lines(items)
    block_messages = _chunk_lines_into_messages(
        header_blocks, lines, max_chars=SLACK_SAFE_CHARS, max_items=ITEMS_PER_MESSAGE
    )

    total_parts = len(block_messages)
    for idx, blocks in enumerate(block_messages, start=1):
        try:
            send_slack_message(slack_client, slack_channel, text=header_text, blocks=blocks)
        except Exception as e:
            logging.exception("Failed to send Slack message part %d/%d: %s", idx, total_parts, e)
            continue


class SlackNotifier(SlackPoster):
    """Sends a `Report` to the Slack channel the CLI configures.

    `fail_open` is False on purpose: a failed `chat_postMessage` is caught and printed per
    message here, but an error from `conversations_open` while pinging an
    individual owner propagates, and the engine must let it propagate too.
    """

    name = "slack"
    fail_open = False

    def send(self, report: Report, context: RunContext) -> None:
        """Post every Slack message of `report`; an owner-grouped report pings the owners."""
        if report.by_owner is not None:
            label = report.sections[0].label if report.sections else DISK
            self.ping_owners(report, label)
            return
        for message in report.slack_messages():
            self.post(message.text, link_names=message.link_names)

    def send_cleanup(
        self,
        operation: str,
        cloud: str,
        resource_type: str,
        items: Sequence[Mapping[str, Any]],
        dry_run: bool,
    ) -> None:
        """The `--scan_tag` summary: `notify_cleanup` to the channel, adding `#` only if missing."""
        channel = self.channel
        if not str(channel).startswith("#"):
            channel = "#" + channel
        notify_cleanup(self.client, channel, operation, cloud, resource_type, items, dry_run)
