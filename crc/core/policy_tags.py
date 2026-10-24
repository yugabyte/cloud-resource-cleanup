"""Tag filtering policy: normalisation, untagged modes, filter, exception and notags specs."""

import fnmatch
from collections.abc import Mapping, Sequence
from enum import Enum

__all__ = [
    "TagSpec",
    "Tags",
    "UntaggedMode",
    "aws_eip_should_skip",
    "exception_tags_any",
    "filter_tags_all_keys",
    "filter_tags_any_key",
    "filter_tags_exact_pairs",
    "filter_tags_glob_all_keys",
    "is_untagged",
    "matches_all_keys",
    "matches_any_key",
    "normalize_tags",
    "notags_all",
    "should_skip_by_tags",
    "skip_untagged",
]


Tags = dict[str, str]


TagSpec = Mapping[str, Sequence[str]]


class UntaggedMode(Enum):
    """How a cleaner treats a resource without tags (see the catalogue)."""

    SKIP = "skip"
    INCLUDE = "include"
    SKIP_UNLESS_NOTAGS = "skip_unless_notags"


def normalize_tags(tags: object) -> Tags:
    """Return ``dict[str, str]`` for AWS ``[{Key, Value}]`` lists, KMS ``TagKey/TagValue``
    lists, and dict-like tags (dict, Azure tags, GCP ScalarMapContainer, OCI freeform).

    ``None`` and empty input give ``{}``. List entries without a key are dropped, a
    missing or ``None`` value becomes ``""``, and a repeated key keeps the last value.
    """
    if not tags:
        return {}
    items = getattr(tags, "items", None)
    if callable(items):
        return {str(key): _text(value) for key, value in items()}
    if not isinstance(tags, list | tuple):
        return {}
    out: Tags = {}
    for entry in tags:
        if not isinstance(entry, Mapping):
            continue
        key_name, value_name = ("Key", "Value") if "Key" in entry else ("TagKey", "TagValue")
        key = entry.get(key_name)
        if key:
            out[str(key)] = _text(entry.get(value_name))
    return out


def _text(value: object) -> str:
    return "" if value is None else str(value)


def is_untagged(tags: Tags) -> bool:
    """True when the normalised tags are empty."""
    return not tags


def skip_untagged(tags: Tags, mode: UntaggedMode, *, has_notags: bool = False) -> bool:
    """True when ``mode`` says an untagged resource must be skipped.

    ``SKIP`` always skips (AWS spot, Azure VM and Spot VM), ``INCLUDE`` never skips
    (Azure disk with no filter, GCP, OCI), ``SKIP_UNLESS_NOTAGS`` skips unless a
    notags spec is set (AWS disk). Tagged resources are never skipped here.
    """
    if tags:
        return False
    if mode is UntaggedMode.SKIP:
        return True
    if mode is UntaggedMode.SKIP_UNLESS_NOTAGS:
        return not has_notags
    return False


def _key_matches(tags: Tags, key: str, values: Sequence[str]) -> bool:
    return key in tags and (not values or tags[key] in values)


def matches_any_key(tags: Tags, spec: TagSpec) -> bool:
    """True when at least one spec key matches; an empty spec gives False."""
    return any(_key_matches(tags, key, values) for key, values in spec.items())


def matches_all_keys(tags: Tags, spec: TagSpec) -> bool:
    """True when every spec key matches; an empty spec gives True (vacuous)."""
    return all(_key_matches(tags, key, values) for key, values in spec.items())


def filter_tags_any_key(tags: Tags, spec: TagSpec) -> bool:
    """filter_tags, any-key variant: empty spec passes; else one matching key suffices.

    Used by Azure disk and IP, GCP disk, OCI disk and the AWS Elastic IP. An empty value
    list means "key present". Matching is exact and case sensitive.
    """
    return not spec or matches_any_key(tags, spec)


def filter_tags_all_keys(tags: Tags, spec: TagSpec) -> bool:
    """filter_tags, all-keys variant: empty spec passes; else every key must match.

    Used by Azure VM and Spot VM, GCP VM and Spot VM, OCI VM, Spot VM. An empty value list
    means "key present". Matching is exact and case sensitive.
    """
    return not spec or matches_all_keys(tags, spec)


def filter_tags_glob_all_keys(tags: Tags, spec: TagSpec) -> bool:
    """filter_tags, AWS disk and Spot request variant.

    Empty spec passes. Every key must be present. A non-empty value list is matched with
    ``fnmatch.fnmatchcase`` globs (``*`` and ``?``), any pattern; an empty one means "key
    present".
    """
    for key, patterns in spec.items():
        if key not in tags:
            return False
        if patterns and not any(fnmatch.fnmatchcase(tags[key], p) for p in patterns):
            return False
    return True


def filter_tags_exact_pairs(tags: Tags, spec: TagSpec) -> bool:
    """filter_tags, AWS KMS variant: every (key, value) pair in the spec must be present.

    Each listed value is its own required pair, so a key with two values can never match.
    A key with an empty value list adds no pair: it does NOT require the key.
    """
    return all(tags.get(key) == value for key, values in spec.items() for value in values)


def exception_tags_any(tags: Tags, spec: TagSpec) -> bool:
    """exception_tags: True (skip) when any spec key matches. Empty spec gives False."""
    return matches_any_key(tags, spec)


def notags_all(tags: Tags, spec: TagSpec) -> bool:
    """notags: True (skip) when every spec key matches. Empty spec gives False.

    A resource lacking any listed key (or untagged) is NOT skipped.
    """
    return bool(spec) and matches_all_keys(tags, spec)


def should_skip_by_tags(tags: Tags, exception_tags: TagSpec, notags: TagSpec) -> bool:
    """Skip when exception_tags matches any key or notags matches every key.

    Pass an empty spec for a module that has no such flag (AWS KMS and snapshot have no
    notags, GCP disk ignores notags).
    """
    return exception_tags_any(tags, exception_tags) or notags_all(tags, notags)


def aws_eip_should_skip(tags: Tags, exception_tags: TagSpec) -> bool:
    """AWS Elastic IP skip decision.

    The legacy notags check can never be true (its accumulator starts False and is only
    and-ed), so notags never skips an Elastic IP. Preserved: this takes no notags.
    """
    return exception_tags_any(tags, exception_tags)
