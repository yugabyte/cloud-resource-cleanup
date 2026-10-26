"""Grouping of digest records and the mrkdwn text lines of the digest message."""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from crc.notify.digest_load import DigestRecord

FAILURE_ERROR_CHARS = 200
ID_CHARS = 200

_DELETED = ("🗑️", "deleted")
_WOULD_DELETE = ("🔍", "would be deleted")
_STOPPED = ("⏸️", "stopped")
_WOULD_STOP = ("🔍", "would be stopped")


def _escape(text: str) -> str:
    """Slack control characters (`&`, `<`, `>`) as entities."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _cut(text: str, limit: int) -> str:
    """`text` on one line and at most `limit` characters, ending in an ellipsis when cut."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _status(record: DigestRecord) -> tuple[str, str]:
    """(emoji, verb phrase) for what the record's process did."""
    stop = record.operation == "stop"
    if record.dry_run:
        return _WOULD_STOP if stop else _WOULD_DELETE
    return _STOPPED if stop else _DELETED


def _stage_label(record: DigestRecord) -> str:
    """The Jenkins stage name, else `<cloud> <kind>` plus `(<resource_suffix>)`."""
    if record.stage:
        return record.stage
    label = f"{record.cloud} {record.kind}".strip() or "cleanup"
    return f"{label} ({record.resource_suffix})" if record.resource_suffix else label


@dataclass(frozen=True)
class _Group:
    """Records of one stage and operation, counted together."""

    cloud: str
    label: str
    has_stage: bool
    kind: str
    emoji: str
    verb: str
    found: int
    failed: int
    owners: bool
    by_label: tuple[tuple[str, int], ...] = ()


def _label_counts(members: Sequence[DigestRecord]) -> tuple[tuple[str, int], ...]:
    """Ids per section label, in first-seen order, empty labels left out (owner records: none)."""
    counts: Counter[str] = Counter()
    for record in members:
        if not record.owner_grouped:
            for label, ids in record.sections:
                counts[label] += len(ids)
    return tuple((label, n) for label, n in counts.items() if n)


def _group_key(record: DigestRecord) -> tuple[str, str, str, str, bool]:
    return (record.cloud, _stage_label(record), record.kind, record.operation, record.dry_run)


def _group_records(records: Sequence[DigestRecord]) -> list[_Group]:
    """Groups of the records that found something or failed, ordered by cloud then stage."""
    merged: dict[tuple[str, str, str, str, bool], list[DigestRecord]] = {}
    for record in records:
        if not record.is_empty:
            merged.setdefault(_group_key(record), []).append(record)
    groups = []
    for (cloud, label, kind, _op, _dry), members in merged.items():
        emoji, verb = _status(members[0])
        groups.append(
            _Group(
                cloud=cloud,
                label=label,
                has_stage=bool(members[0].stage),
                kind=kind,
                emoji=emoji,
                verb=verb,
                found=sum(r.count for r in members),
                failed=sum(len(r.failures) for r in members),
                owners=any(r.owner_grouped for r in members),
                by_label=_label_counts(members),
            )
        )
    return sorted(groups, key=lambda g: (g.cloud, g.label, g.verb))


def _group_line(group: _Group) -> str:
    """`🗑️ *AWS VMs 6 Hours old* · aws vm · 34 deleted · ⚠️ 1 failed`."""
    parts = [f"{group.emoji} *{_escape(_cut(group.label, 120))}*"]
    if group.has_stage and (group.cloud or group.kind):
        parts.append(_escape(f"{group.cloud} {group.kind}".strip()))
    if len(group.by_label) > 1:
        counts = ", ".join(f"{n} {_escape(label)}" for label, n in group.by_label)
        parts.append(f"{counts} {group.verb}")
    elif group.found or not group.failed:
        parts.append(f"{group.found} {group.verb}")
    if group.owners:
        parts.append("owners pinged separately")
    if group.failed:
        parts.append(f"⚠️ {group.failed} failed")
    return " · ".join(parts)


def _idle_line(records: Sequence[DigestRecord], groups: Sequence[_Group]) -> str | None:
    """The last summary line about the commands that found nothing; None when there are none."""
    idle = sum(1 for r in records if r.is_empty)
    if not idle:
        return None
    if not groups:
        return f"✅ Nothing to clean ({idle} checks)"
    return f"✅ {idle} other checks found nothing"


def _fallback_text(build_number: str | None, groups: Sequence[_Group]) -> str:
    """Notification text: `CRC cleanup #N: 41 deleted, 7 would be deleted, 1 failed`."""
    title = f"CRC cleanup #{build_number}" if build_number else "CRC cleanup"
    totals: Counter[str] = Counter()
    for group in groups:
        totals[group.verb] += group.found
    parts = [f"{count} {verb}" for verb, count in totals.items() if count]
    failed = sum(g.failed for g in groups)
    if failed:
        parts.append(f"{failed} failed")
    return f"{title}: {', '.join(parts)}" if parts else f"{title}: nothing to clean"
