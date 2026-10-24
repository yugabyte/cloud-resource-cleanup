# Copyright (c) Yugabyte, Inc.

"""Filters and thresholds of one KMS cleanup run."""

from dataclasses import dataclass, field

from crc.core import age as core_age

from .tags import TagSpec


@dataclass(frozen=True, slots=True)
class KmsSettings:
    """Filters and thresholds of one run."""

    filter_tags: TagSpec | None = None
    exception_tags: TagSpec | None = None
    kms_key_description: str = ""
    kms_user: str = ""
    kms_pending_window: int = 0
    age: core_age.AgeSpec = field(default_factory=dict)
    custom_age_tag_key: str = ""
