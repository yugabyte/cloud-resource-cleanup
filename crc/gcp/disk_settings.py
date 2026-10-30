# Copyright (c) Yugabyte, Inc.

"""Settings and result helpers of `DiskCleaner`."""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from crc.core import age as core_age
from crc.core import policy
from crc.gcp.spot_vm.settings import DEFAULT_POLL_INTERVAL_SEC, DEFAULT_WAIT_SEC

_NOT_TAGGED = "not_tagged"

Deleted = list[str] | dict[str, list[str]]


@dataclass(frozen=True, slots=True)
class DiskSettings:
    """Filters and options of one run: only the fields `main` reads."""

    project_id: str
    filter_labels: Mapping[str, Sequence[str]] | None
    exception_labels: Mapping[str, Sequence[str]] | None
    exception_regex: Sequence[str] | None
    custom_age_tag_key: str
    detach_age: core_age.AgeSpec | int
    slack_notify_users: bool = False
    slack_user_label: str = ""
    notags: Mapping[str, Sequence[str]] | None = None
    name_regex: Sequence[str] | None = None
    wait_sec: float = DEFAULT_WAIT_SEC
    poll_sec: float = DEFAULT_POLL_INTERVAL_SEC


def _name_patterns(name_regex: Sequence[str] | None) -> tuple[str, ...]:
    """The `name_regex` patterns; a bad value raises `ValueError` before any call is made."""
    if name_regex is None:
        return ()
    if not isinstance(name_regex, list | tuple) or not all(isinstance(p, str) for p in name_regex):
        raise ValueError(f"Invalid name_regex {name_regex!r}: expected a list of patterns")
    if not name_regex:
        return ()
    patterns = tuple(name_regex)
    policy.compile_patterns(patterns, "name_regex")
    return patterns


def _frozen_groups(groups: dict[str, list[str]] | None) -> dict[str, tuple[str, ...]] | None:
    if groups is None:
        return None
    return {owner: tuple(names) for owner, names in groups.items()}


def _record(deleted: Deleted, settings: DiskSettings, name: str, labels: Mapping[str, str]) -> None:
    """Record a deleted (or listed) disk by name, or by `slack_user_label` (`not_tagged`)."""
    if not settings.slack_notify_users:
        cast(list[str], deleted).append(name)
        return
    groups = cast(dict[str, list[str]], deleted)
    label = settings.slack_user_label
    owner = labels.get(label, _NOT_TAGGED)
    groups.setdefault(owner, []).append(name)


def _log_summary(deleted: Deleted, *, dry_run: bool) -> None:
    if not deleted:
        logging.warning("No GCP disk to delete.")
    if not dry_run:
        logging.warning("number of GCP disks deleted: %s", len(deleted))
        logging.warning("List of GCP disk deleted: %s", deleted)
    else:
        logging.warning(
            "List of GCP disk (Total: %s) which will be deleted: %s", len(deleted), deleted
        )
