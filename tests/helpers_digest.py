"""Shared helpers of the digest poster tests: a recording Slack client and record writers."""

import json
from pathlib import Path
from typing import Any

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "slack_digest"
YBA = "yba-crc-cleanup-notifications"


class FakeSlack:
    """Records every `chat_postMessage`; a channel in `fail_channels` raises on its top post."""

    def __init__(self, fail_channels: tuple[str, ...] = (), fail_replies: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail_channels = fail_channels
        self.fail_replies = fail_replies

    def chat_postMessage(self, **kwargs: Any) -> dict[str, Any]:  # noqa: N802 - Slack SDK name
        self.calls.append(kwargs)
        threaded = "thread_ts" in kwargs
        if (kwargs["channel"].lstrip("#") in self.fail_channels and not threaded) or (
            self.fail_replies and threaded
        ):
            raise RuntimeError("channel_not_found")
        return {"ok": True, "ts": f"1700000000.{len(self.calls):06d}"}


def record(**overrides: Any) -> dict[str, Any]:
    """A version 1 record: an empty AWS VM delete of channel `YBA`."""
    data: dict[str, Any] = {
        "version": 1,
        "written_at": "2026-10-26T07:31:02Z",
        "channel": YBA,
        "cloud": "aws",
        "kind": "vm",
        "operation": "delete",
        "dry_run": False,
        "resource_suffix": None,
        "stage": "AWS VMs 6 Hours old",
        "build_url": "https://jenkins.example/job/x/7/",
        "build_number": "7",
        "job_name": "x/y",
        "sections": [{"label": "VM", "ids": []}],
        "failures": [],
        "by_owner": None,
    }
    data.update(overrides)
    return data


def write_records(directory: Path, records: list[dict[str, Any]]) -> Path:
    """Write each record as its own file; returns `directory`."""
    directory.mkdir(parents=True, exist_ok=True)
    for index, data in enumerate(records):
        (directory / f"{index:04d}.json").write_text(json.dumps(data), encoding="utf-8")
    return directory


def vm_ids(count: int) -> list[dict[str, Any]]:
    """One VM section with `count` ids."""
    return [{"label": "VM", "ids": [f"i-{n:04d}" for n in range(count)]}]


def check_limits(blocks: list[dict[str, Any]]) -> None:
    """Assert the Block Kit limits of one message."""
    assert len(blocks) <= 50  # noqa: PLR2004
    for block in blocks:
        if block["type"] == "section":
            assert len(block["text"]["text"]) <= 3000  # noqa: PLR2004
        if block["type"] == "header":
            assert len(block["text"]["text"]) <= 150  # noqa: PLR2004
