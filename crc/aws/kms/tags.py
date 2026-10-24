# Copyright (c) Yugabyte, Inc.

"""Tag reading and filter matching of the KMS cleaner (pure functions, no SDK calls)."""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from crc.core import policy

TagSpec = Mapping[str, Sequence[str]]


class NotOursError(Exception):
    """The key policy has no AWS principal to compare with `kms_user`: not one of ours."""


def tag_pairs(response_tags: Iterable[Mapping[str, Any]]) -> set[tuple[str, str]]:
    """(TagKey, TagValue) pairs of one key. A tag without `TagKey` / `TagValue` raises KeyError
    and an unhashable value raises TypeError: the caller skips the key (fail closed). One set,
    so a repeated TagKey keeps every one of its values."""
    return {(tag["TagKey"], tag["TagValue"]) for tag in response_tags}


def matches_filter(pairs: set[tuple[str, str]], spec: TagSpec | None) -> bool:
    """BUG-007, BUG-014: every filter key must be on the key, with any of its listed values; an
    empty value list means "key present, any value" (as `policy.filter_tags_all_keys` and
    `crc/aws/vm`). Materialises every value list first, so a non-iterable one raises TypeError
    (the key is skipped) whatever the other keys say."""
    wanted = {key: tuple(values) for key, values in spec.items()} if spec else {}
    return all(
        any(policy.matches_any_key({key: value}, {wanted_key: values}) for key, value in pairs)
        for wanted_key, values in wanted.items()
    )
