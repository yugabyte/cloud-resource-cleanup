"""`python crc_digest.py --dir <dir>`: post the Slack digest of the records one run wrote."""

import argparse
import json
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from slack_sdk import WebClient

from crc.notify.digest_load import DigestRecord, load_records
from crc.notify.digest_post import build_messages, post_digest

logger = logging.getLogger(__name__)

EXIT_POST_FAILED = 1
EXIT_USAGE = 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crc_digest.py",
        description="Post one Slack digest per channel from the records CRC wrote "
        "(CRC_SLACK_DIGEST_DIR). The token is read from SLACK_BOT_TOKEN.",
    )
    parser.add_argument("--dir", required=True, help="directory with the digest records")
    parser.add_argument(
        "--dry_run",
        action="store_true",
        help="print the Block Kit JSON per channel to stdout and post nothing (no token needed)",
    )
    return parser


def _print_preview(records: Sequence[DigestRecord]) -> None:
    channels = [
        {
            "channel": channel,
            "top": {"text": messages[0].text, "blocks": messages[0].blocks},
            "replies": [{"text": m.text, "blocks": m.blocks} for m in messages[1:]],
        }
        for channel, messages in build_messages(records).items()
    ]
    print(json.dumps(channels, indent=2, ensure_ascii=False))


def _this_build(records: Sequence[DigestRecord], build_number: str | None) -> list[DigestRecord]:
    """The records of `build_number` (Jenkins BUILD_NUMBER); all of them when it is unset.

    A record of another build is left over from a run whose post step never ran; it is dropped
    with one warning so two runs are never merged under one header.
    """
    if not build_number:
        return list(records)
    kept = []
    for record in records:
        if record.build_number == build_number:
            kept.append(record)
        else:
            logger.warning(
                "dropping digest record of build %s (this is build %s): %s %s %s",
                record.build_number,
                build_number,
                record.cloud,
                record.kind,
                record.stage,
            )
    return kept


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the flags, post (or preview) the digest; return the exit status 0, 1 or 2.

    `crc_digest.py` passes the status to `SystemExit`; argparse itself exits 2 on bad flags.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _build_parser().parse_args(argv)
    directory = Path(args.dir)
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token and not args.dry_run:
        print("SLACK_BOT_TOKEN is not set; cannot post the digest.", file=sys.stderr)
        return EXIT_USAGE
    records = _this_build(load_records(directory), os.environ.get("BUILD_NUMBER"))
    if not records:
        logger.info("no digest records in %s", directory)
        return 0
    if args.dry_run:
        _print_preview(records)
        return 0
    if post_digest(WebClient(token=token), records):
        return EXIT_POST_FAILED
    return 0
