"""The Slack digest record: one JSON file per process that `crc_digest.py` later posts.

`build_record` is pure (a `Report` and a `DigestTarget` in, a dict out); `write_record` writes
it atomically. The target is built in `crc/cli` from the environment; nothing here reads it.
"""

import json
import os
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from crc.core.report import Report

RECORD_VERSION = 1


@dataclass(frozen=True, slots=True)
class DigestTarget:
    """Where the record goes and the run facts the report does not have.

    `directory` is `CRC_SLACK_DIGEST_DIR`; `stage`, `build_url`, `build_number` and `job_name`
    are the Jenkins variables (None when unset); `kind` is the `--resource` value.
    """

    directory: str
    kind: str | None = None
    resource_suffix: str | None = None
    stage: str | None = None
    build_url: str | None = None
    build_number: str | None = None
    job_name: str | None = None


def _utc(moment: datetime) -> datetime:
    """`moment` in UTC; a naive value is taken as UTC."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def build_record(
    report: Report, channel: str | None, target: DigestTarget, now: datetime
) -> dict[str, Any]:
    """The version 1 record of `report`; `now` is the caller's clock (the run's `context.now`)."""
    by_owner = (
        None
        if report.by_owner is None
        else {owner: list(ids) for owner, ids in report.by_owner.items()}
    )
    return {
        "version": RECORD_VERSION,
        "written_at": _utc(now).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "channel": channel,
        "cloud": report.cloud,
        "kind": target.kind,
        "operation": str(report.operation.value).lower(),
        "dry_run": bool(report.dry_run),
        "resource_suffix": target.resource_suffix,
        "stage": target.stage,
        "build_url": target.build_url,
        "build_number": target.build_number,
        "job_name": target.job_name,
        "sections": [
            {
                "label": section.label,
                "ids": list(section.ids if section.slack_ids is None else section.slack_ids),
            }
            for section in report.sections
        ],
        "failures": [{"id": f.id, "error": f.error} for f in report.failures],
        "by_owner": by_owner,
    }


def record_name(now: datetime) -> str:
    """`<UTC yyyymmddTHHMMSS>-<pid>-<uuid4 hex>.json`."""
    stamp = _utc(now).strftime("%Y%m%dT%H%M%S")
    return f"{stamp}-{os.getpid()}-{uuid.uuid4().hex}.json"


def write_record(directory: str, record: dict[str, Any], now: datetime) -> Path:
    """Write `record` into `directory` (created when missing): temp file, then `os.replace`.

    The temp file has no `.json` suffix, so a reader never sees a half-written record.
    """
    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(record, sort_keys=True)
    handle, temp_name = tempfile.mkstemp(prefix=".crc-digest-", suffix=".tmp", dir=target_dir)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as temp_file:
            temp_file.write(payload)
        final = target_dir / record_name(now)
        os.replace(temp_name, final)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
    return final
