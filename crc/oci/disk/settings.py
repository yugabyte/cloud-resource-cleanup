# Copyright (c) Yugabyte, Inc.

"""Settings, skip vocabulary and summary log of the OCI disk cleaner."""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from crc.core import age as core_age

# Attachment lifecycle states that mean the volume/boot volume is still in use and
# must not be deleted. ATTACHING is included alongside ATTACHED since an
# in-progress attachment is not yet reflected as a running instance's dependency
# but the volume is still claimed.
IN_USE_ATTACHMENT_STATES = ("ATTACHED", "ATTACHING")

KIND_DISK = "disk"
KIND_BOOT_DISK = "boot disk"

AVAILABLE = "AVAILABLE"
LIST_CONTEXT = "list volumes/boot volumes disk delete"
ATTACHMENTS_CONTEXT = "list_volume_attachments"
BOOT_ATTACHMENTS_CONTEXT = "list_boot_volume_attachments"
KIND_KEY = "kind"
UNKNOWN_REASONS = {
    KIND_DISK: "block volume attachments unknown",
    KIND_BOOT_DISK: "boot volume attachments unknown",
}

TagSpec = Mapping[str, Sequence[str]]

SKIP_KEYS = {
    "state is not AVAILABLE": "state",
    "attached": "attached",
    "block volume attachments unknown": "attachments_unknown",
    "boot volume attachments unknown": "attachments_unknown",
    "matches exception_tags or notags": "exception_tag",
    "does not match filter_tags": "filter_mismatch",
    "not older than age": "too_young",
}


@dataclass(frozen=True, slots=True)
class DiskSettings:
    """Filters and thresholds of one run."""

    filter_tags: TagSpec = field(default_factory=dict)
    exception_tags: TagSpec = field(default_factory=dict)
    notags: TagSpec = field(default_factory=dict)
    age: core_age.AgeSpec = field(default_factory=dict)
    custom_age_tag_key: str = ""


def log_summary(deleted: Sequence[str], *, dry_run: bool) -> None:
    items = list(deleted)
    if not items:
        logging.warning("No OCI disk to delete.")

    if not dry_run:
        logging.warning("number of OCI disks deleted: %s", len(items))
        logging.warning("List of OCI disk deleted: %s", items)
    else:
        logging.warning("List of OCI disk (Total: %s) which will be deleted: %s", len(items), items)
