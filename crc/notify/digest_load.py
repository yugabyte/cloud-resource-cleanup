"""Reading the digest records (version 1) that the cleanup processes wrote into one directory."""

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

RECORD_VERSION = 1

# (label, ids) of one report section and (id, error) of one failure.
SectionData = tuple[str, tuple[str, ...]]
FailureData = tuple[str, str]


@dataclass(frozen=True)
class DigestRecord:
    """One parsed record file: what one `crc.py` process found and did."""

    channel: str
    cloud: str
    kind: str
    operation: str
    dry_run: bool
    resource_suffix: str | None
    stage: str | None
    build_url: str | None
    build_number: str | None
    job_name: str | None
    written_at: str
    sections: tuple[SectionData, ...]
    failures: tuple[FailureData, ...]
    by_owner: dict[str, tuple[str, ...]] | None

    @property
    def owner_grouped(self) -> bool:
        """True when the report was grouped by owner (the owners are pinged separately)."""
        return bool(self.by_owner)

    @property
    def count(self) -> int:
        """Resources found: the owner-grouped ids when grouped, else every section id."""
        if self.by_owner:
            return sum(len(ids) for ids in self.by_owner.values())
        return sum(len(ids) for _, ids in self.sections)

    @property
    def is_empty(self) -> bool:
        """True when the process found nothing and nothing failed."""
        return self.count == 0 and not self.failures


def _opt_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _ids(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError("ids is not a list")
    return tuple(str(item) for item in value)


def _sections(value: Any) -> tuple[SectionData, ...]:
    if not isinstance(value, list):
        raise ValueError("sections is not a list")
    return tuple((str(sec.get("label") or ""), _ids(sec.get("ids", []))) for sec in value)


def _failures(value: Any) -> tuple[FailureData, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("failures is not a list")
    return tuple((str(item.get("id", "")), str(item.get("error", ""))) for item in value)


def _by_owner(value: Any) -> dict[str, tuple[str, ...]] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("by_owner is not an object")
    return {str(owner): _ids(ids) for owner, ids in value.items()}


def _parse(data: Any) -> DigestRecord:
    """Turn the decoded JSON into a record; any shape problem raises ValueError."""
    if not isinstance(data, Mapping):
        raise ValueError("top level is not an object")
    version = data.get("version")
    if version != RECORD_VERSION or isinstance(version, bool):
        raise ValueError(f"unsupported version {version!r}")
    channel = _opt_str(data.get("channel"))
    if channel is None:
        raise ValueError("no channel")
    try:
        return DigestRecord(
            channel=channel,
            cloud=str(data.get("cloud") or ""),
            kind=str(data.get("kind") or ""),
            operation=str(data.get("operation") or "delete").lower(),
            dry_run=bool(data.get("dry_run")),
            resource_suffix=_opt_str(data.get("resource_suffix")),
            stage=_opt_str(data.get("stage")),
            build_url=_opt_str(data.get("build_url")),
            build_number=_opt_str(data.get("build_number")),
            job_name=_opt_str(data.get("job_name")),
            written_at=str(data.get("written_at") or ""),
            sections=_sections(data.get("sections", [])),
            failures=_failures(data.get("failures")),
            by_owner=_by_owner(data.get("by_owner")),
        )
    except (AttributeError, TypeError) as e:
        raise ValueError(f"bad shape: {e}") from e


def load_records(directory: Path) -> list[DigestRecord]:
    """Every readable version 1 record of `directory`, oldest first.

    A file that is not JSON, not version 1 or has the wrong shape is skipped with one WARNING;
    unknown keys are ignored. A missing directory gives an empty list.
    """
    if not directory.is_dir():
        return []
    records: list[DigestRecord] = []
    for path in sorted(directory.glob("*.json")):
        try:
            records.append(_parse(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError) as e:  # JSONDecodeError and UnicodeDecodeError are ValueErrors
            logger.warning("Skipping digest record %s: %s", path.name, e)
    records.sort(key=lambda record: record.written_at)
    return records
