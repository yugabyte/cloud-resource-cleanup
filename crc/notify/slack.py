"""Slack sink: the `notify_*` messages, owner pings and the scan-tag cleanup summary.

Moved from `crc/notifications.py` and `crc.py` without behaviour change. The Slack client is
injected (a `slack_sdk.WebClient` in production, a fake in tests); nothing here creates one.
"""

import copy
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import dateutil.parser
from slack_sdk.errors import SlackApiError

from crc.core.cleaner import RunContext
from crc.core.report import DISK, Report, format_owner_ping

logger = logging.getLogger(__name__)

SlackClient = Any

SLACK_SAFE_CHARS = 2800
ITEMS_PER_MESSAGE = 20
NOT_FOUND = "not_found"


def human_readable_timedelta(start_time: datetime | str | None) -> str:
    """Time since `start_time` (UTC datetime or ISO string) as '3d 04h 12m 05s'; '-' if unknown."""
    if not start_time:
        return "-"
    if isinstance(start_time, str):
        try:
            start_dt = dateutil.parser.isoparse(start_time)
        except Exception:
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
            except Exception:
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


class SlackNotifier:
    """Sends a `Report` to a Slack channel as `crc.py` does today.

    `fail_open` is False on purpose: a failed `chat_postMessage` is caught and printed per
    message here (as in `crc.py`), but an error from `conversations_open` while pinging an
    individual owner propagates, and the engine must let it propagate too.
    """

    name = "slack"
    fail_open = False

    def __init__(self, client: SlackClient, channel: str) -> None:
        self.client = client
        self.channel = channel

    def send(self, report: Report, context: RunContext) -> None:
        """Post every Slack message of `report`; an owner-grouped report pings the owners."""
        if report.by_owner is not None:
            label = report.sections[0].label if report.sections else DISK
            self.ping_owners(report, label)
            return
        for message in report.slack_messages():
            self.post(message.text, link_names=message.link_names)

    def post(self, text: str, link_names: bool = False) -> None:
        """Post `text` to `#<channel>`; a failure is printed and swallowed.

        The channel is always prefixed with `#`, so a channel that already starts with `#`
        becomes `##name` (BUG-034).
        """
        try:
            if link_names:
                self.client.chat_postMessage(
                    channel="#" + self.channel, text=text, link_names=True
                )
            else:
                self.client.chat_postMessage(channel="#" + self.channel, text=text)
        except Exception as e:
            print(f"Failed to send message '{text}' to Slack channel '{self.channel}': {e}")

    def lookup_user_by_email(self, email: str) -> Any:
        """Slack user id of `email`, or "not_found" on any error."""
        try:
            user_info = self.client.users_lookupByEmail(email=email)
            return user_info["user"]["id"]
        except:  # noqa: E722 - the bare except of `crc.py` is kept, it also catches BaseException
            return NOT_FOUND

    def user_groups(self) -> Any:
        """Slack user groups, or the string "not_found" on any error (callers iterate it)."""
        try:
            user_groups = self.client.usergroups_list()
            return user_groups["usergroups"]
        except:  # noqa: E722 - the bare except of `crc.py` is kept, it also catches BaseException
            print("Something Went Wront! Could not get user_groups.")
            return NOT_FOUND

    def ping_owners(
        self,
        report: Report,
        resource: str,
        lookup_user: Callable[[str], Any] | None = None,
        get_groups: Callable[[], Any] | None = None,
    ) -> None:
        """Ping each owner of `report.by_owner`: users 1:1, groups and untagged in the channel.

        `lookup_user` and `get_groups` default to this notifier's own lookups; `crc.py` passes
        its `CRC` methods so that overriding or patching them keeps working.
        """
        lookup = lookup_user or self.lookup_user_by_email
        groups = get_groups or self.user_groups
        owners = report.by_owner or {}
        user_groups = groups()
        for owner, ids in owners.items():
            print(f"Pinging '{owner}'")
            member_id = lookup(f"{owner}@yugabyte.com")
            if member_id == NOT_FOUND:
                self._ping_in_channel(report, resource, owner, ids, user_groups)
            else:
                self._ping_user(report, resource, owner, ids, member_id)

    def _ping_in_channel(
        self, report: Report, resource: str, owner: str, ids: Sequence[str], user_groups: Any
    ) -> None:
        member_id = NOT_FOUND
        for user_group in user_groups:
            if user_group["handle"] == owner:
                member_id = user_group["id"]
                break
        mention = None if member_id == NOT_FOUND else f"<!subteam^{member_id}>"
        text = format_owner_ping(report, resource, owner, ids, mention)
        self.post(text, link_names=True)

    def _ping_user(
        self, report: Report, resource: str, owner: str, ids: Sequence[str], member_id: str
    ) -> None:
        text = format_owner_ping(report, resource, owner, ids, f"<@{member_id}>")
        response = self.client.conversations_open(users=[member_id])
        channel_id = response["channel"]["id"]
        try:
            self.client.chat_postMessage(channel=channel_id, text=text, link_names=True)
        except Exception as e:
            print(f"Failed to send message '{text}' to Slack channel '{channel_id}': {e}")

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
