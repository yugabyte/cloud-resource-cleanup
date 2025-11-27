# crc/notifications.py
import time
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

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

def _short_id(resource_id: Optional[str], keep: int = 12) -> str:
    if not resource_id:
        return "-"
    return resource_id if len(resource_id) <= keep else resource_id[:keep] + "…"

def build_cleanup_blocks(operation: str, cloud: str, resource_type: str, items: List[Dict], dry_run: bool, context_label: str):
    """
    Build Block Kit blocks for a cleanup summary, splitting long lists so each section text < SLACK_MAX_CHARS.
    items: list of dicts with keys: name, id, region/zone, console_url
    """
    SAFE_LIMIT = 2800           # keep margin for extra characters

    count = len(items)
    header = f"{'DRY RUN: ' if dry_run else ''}{operation.upper()} — {count} {cloud.upper()} {resource_type.upper()}"
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{header}*"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": context_label}]},
        {"type": "divider"},
    ]

    if not items:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"✔ No {cloud} {resource_type} matched the criteria. Nothing to clean."}} )
        return blocks

    # Build lines first
    lines = []
    for it in items:
        name = it.get("name") or "-"
        rid = it.get("id") or "-"
        scope = it.get("region") or it.get("zone") or it.get("location") or "-"
        url = it.get("console_url")
        view = f" — <{url}|View>" if url else ""
        lines.append(f"• *{name}* `{_short_id(rid)}` — {scope}{view}")

    # Chunk lines into multiple section blocks such that each block text <= SAFE_LIMIT characters
    current_chunk = []
    current_len = 0
    for line in lines:
        line_len = len(line) + 1  # + newline
        # if adding this line would exceed SAFE_LIMIT, flush current chunk first
        if current_chunk and (current_len + line_len > SAFE_LIMIT):
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(current_chunk)}})
            current_chunk = []
            current_len = 0

        current_chunk.append(line)
        current_len += line_len

        # defensive: if a single line exceeds SAFE_LIMIT (very long url or name), truncate it
        if current_len > SAFE_LIMIT:
            # truncate the last line to fit
            truncated = current_chunk[-1][:SAFE_LIMIT-10] + "…"
            current_chunk[-1] = truncated
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(current_chunk)}})
            current_chunk = []
            current_len = 0

    # flush remaining chunk
    if current_chunk:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(current_chunk)}})

    # If many items, add a small footer with counts and suggestion to view full report
    if count > len(lines):
        blocks.append({"type":"context","elements":[{"type":"mrkdwn","text":f"_Total: {count} items_"}]})
    elif count > 40:
        blocks.append({"type":"context","elements":[{"type":"mrkdwn","text":f"_Showing {min(40,count)} of {count} items — generate full report for complete list_"}]})

    return blocks

def notify_cleanup(slack_client, slack_channel: str, operation: str, cloud: str, resource_type: str, items: List[Dict], dry_run: bool):
    """
    Send a single informative Slack message to the given channel.
    slack_channel: '#channel-name' or channel ID (we'll accept '#name')
    items: list of dicts {name, id, region/zone, console_url}
    """
    if not slack_client:
        raise ValueError("slack_client is required to send notifications")

    # ensure channel starts with '#' if a name was passed
    channel = slack_channel
    # Accept channel id as-is or '#name' (crc.py historically used '#'+slack_channel)
    if not channel.startswith("#") and not channel.startswith("C") and not channel.startswith("G"):
        channel = "#" + channel

    blocks = build_cleanup_blocks(operation=operation, cloud=cloud, resource_type=resource_type, items=items, dry_run=dry_run, context_label=f"{cloud} • {resource_type}")
    text = f"{'DRY RUN: ' if dry_run else ''}{operation} {len(items)} {cloud} {resource_type}"
    return send_slack_message(slack_client, channel, text, blocks)
