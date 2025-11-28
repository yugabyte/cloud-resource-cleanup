# crc/notifications.py
import time
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

from datetime import datetime, timezone
import dateutil.parser  # pip install python-dateutil
import copy

SLACK_SAFE_CHARS = 2800   # safe per-section char limit
ITEMS_PER_MESSAGE = 20    # fallback chunk size; adjust as needed

def _format_item_row(it):
    """
    Format one instance row including uptime if running. Uses full id.
    it: dict with keys: name, id, region, state, launch_time, console_url
    """
    name = it.get("name") or "-"
    rid = it.get("id") or "-"
    region = it.get("region") or "-"
    state = (it.get("state") or "").upper()
    # launch_time may be datetime or ISO string or None
    launch = it.get("launch_time")
    uptime = human_readable_timedelta(launch) if state == "RUNNING" else "-"
    view = f" — <{it.get('console_url')}|View>" if it.get("console_url") else ""
    state_part = f" — {state}" if state else ""
    uptime_part = f" ({uptime})" if state == "RUNNING" else ""
    instance_type = it.get("instance_type") or "-"
    return f"• *{name}* | `{rid}` | {instance_type} | {region}{state_part}{uptime_part}{view}"

def _chunk_lines_into_messages(header_block, lines, max_chars=SLACK_SAFE_CHARS, max_items=ITEMS_PER_MESSAGE):
    """
    Yield list of block lists (each block list is one Slack message).
    Ensures:
      - each message contains <= max_items lines AND
      - no section block exceeds max_chars characters (approx safe Slack limit).
    Uses deepcopy so header_blocks is not mutated across parts.
    """
    messages = []
    cur = []
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

    # Build block lists for each chunk — DO NOT mutate header_blocks inplace
    block_messages = []
    total = len(messages)
    for idx, chunk in enumerate(messages, start=1):
        # deep copy header for safe mutation
        prefix = copy.deepcopy(header_block)
        # add part indicator only on prefix text (first section)
        if total > 1 and prefix and "text" in prefix[0]:
            original_text = prefix[0]["text"].get("text", "")
            # set a new string, do not mutate header_blocks
            prefix[0]["text"]["text"] = f"{original_text} — Part {idx}/{total}"
        # build message blocks
        blocks = prefix[:]  # shallow copy of prefix list (prefix is already a deepcopy)
        # join chunk lines into one section block (each section text will be within safe limit by construction)
        blocks.append({"type":"section", "text":{"type":"mrkdwn","text":"\n".join(chunk)}})
        block_messages.append(blocks)

    return block_messages

def human_readable_timedelta(start_time):
    """
    start_time: datetime or ISO string (UTC). Return '3d 04:12:05' or '-' if None.
    """
    if not start_time:
        return "-"
    # parse if string
    if isinstance(start_time, str):
        try:
            start_dt = dateutil.parser.isoparse(start_time)
        except Exception:
            return "-"
    else:
        start_dt = start_time

    # ensure tz-aware
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
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

def send_slack_message(client, channel: str, text: str, blocks: List[Dict], max_retries: int = 3):
    """
    Minimal retrying wrapper around chat_postMessage.
    channel: channel ID or '#channel-name'
    text: plain-text fallback
    blocks: Block Kit blocks
    """
    from slack_sdk.errors import SlackApiError

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
            logger.warning("Slack API error posting to %s: %s (attempt %d/%d)", channel, err, attempt, max_retries)
            if attempt >= max_retries:
                logger.exception("Max retries reached sending Slack message to %s", channel)
                raise
            time.sleep(1 + attempt)
        except Exception as e:
            logger.exception("Unexpected error sending Slack message: %s", e)
            raise

def notify_cleanup(slack_client, slack_channel: str, operation: str, cloud: str, resource_type: str, items: List[Dict], dry_run: bool):
    """
    Sends potentially multiple Slack messages to include ALL items.
    items: list of dicts with keys including state and launch_time
    """
    if not slack_client:
        raise ValueError("slack_client is required")

    # Build header blocks (base)
    count = len(items)
    header_text = f"{'DRY RUN: ' if dry_run else ''}{operation.upper()} — {count} {cloud.upper()} {resource_type.upper()}"
    header_blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{header_text}*"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"{cloud} • {resource_type}"}]},
        {"type": "divider"},
    ]
    # Partition into running and stopped/other
    running_items = [it for it in items if (it.get("state") or "").upper() == "RUNNING"]
    stopped_items = [it for it in items if (it.get("state") or "").upper() != "RUNNING"]

    # Build human-readable lines: running first, then stopped
    lines: List[str] = []
    if running_items:
        lines.append("*Running instances*")
        for it in running_items:
            lines.append(_format_item_row(it))
    if stopped_items:
        if lines:
            lines.append("")  # spacing between groups
        lines.append("*Stopped / Other instances*")
        for it in stopped_items:
            lines.append(_format_item_row(it))

    # Use chunker to create list of block lists, each representing one message part
    # Make sure _chunk_lines_into_messages(header_blocks, lines, max_chars, max_items) is available
    block_messages = _chunk_lines_into_messages(header_blocks, lines, max_chars=SLACK_SAFE_CHARS, max_items=ITEMS_PER_MESSAGE)

    # Send each part in sequence; add report_url in the last part if supplied
    total_parts = len(block_messages)
    for idx, blocks in enumerate(block_messages, start=1):
        try:
            send_slack_message(slack_client, slack_channel, text=header_text, blocks=blocks)
        except Exception as e:
            logging.exception("Failed to send Slack message part %d/%d: %s", idx, total_parts, e)
            # continue sending remaining parts so user receives partial results
            continue