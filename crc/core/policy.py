"""Pure filtering policy for CRC cleaners.

Every function here reproduces one filtering variant catalogued in
``docs/refactor/FILTER_VARIANTS.md`` exactly. Variants that behave differently are
separate, explicitly named functions: two different semantics are never merged behind
a flag. Nothing here reads the environment, the clock or any SDK; callers fetch data,
call :func:`normalize_tags`, and let these functions decide.
"""

import datetime
import fnmatch
import re
from collections.abc import Iterable, Mapping, Sequence
from enum import Enum
from typing import Any

from crc.core.age import AgeSpec

Tags = dict[str, str]
TagSpec = Mapping[str, Sequence[str]]

SENSITIVE_YB_TASK = frozenset({"infrastructure", "infra", "ipsec", "vpn", "prod", "production"})

_YB_TASK_KEYS = ("yb_task", "yb-task")
_YB_TASK_SPLIT = re.compile(r"[-_\s/]+")
_K8S_PREFIXES = ("kubernetes.io/", "ebs.csi.aws.com/")
_K8S_KEYS = ("KubernetesCluster", "CSIVolumeName")
_GKE_NODE_LABEL = "goog-gke-node"
_OKE_MARKERS = ("oke", "instancepool", "instance_pool", "instance-pool")


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


def name_regex_search_matches(patterns: Sequence[str], text: str | None) -> bool:
    """name_regex, ``re.search`` any: empty list matches. AWS disk and keypair. Case sensitive.

    ``None`` is searched as ``""``. An invalid pattern raises ``re.error``.
    """
    return not patterns or any(re.search(p, text or "") for p in patterns)


def exception_regex_search_matches(patterns: Sequence[str], text: str | None) -> bool:
    """exception_regex, ``re.search`` any: empty list gives False. AWS disk and keypair."""
    return any(re.search(p, text or "") for p in patterns)


def exception_regex_match_matches(patterns: Sequence[str], text: str) -> bool:
    """exception_regex, ``re.match`` (anchored at start) any: GCP disk. Empty gives False."""
    return any(re.match(p, text) for p in patterns)


def filter_substring_matches(patterns: Sequence[str], text: str) -> bool:
    """GCP IP filter_regex: plain substring, any. Empty list matches. Not a regex."""
    return not patterns or any(p in text for p in patterns)


def gcp_ip_exception_excludes(patterns: Sequence[str], text: str) -> bool:
    """GCP IP exception_regex as implemented: True (exclude) when patterns are set and NONE
    is a substring of the name. Inverted relative to the flag's name; preserved as is.
    """
    return bool(patterns) and not any(p in text for p in patterns)


def nic_name_included(patterns: Sequence[str], text: str) -> bool:
    """Azure NIC name_regex: plain substring, any. Empty list gives False (nothing is
    included; the cleaner returns early before listing)."""
    return any(p in text for p in patterns)


def nic_name_excluded(patterns: Sequence[str], text: str) -> bool:
    """Azure NIC exception_regex: plain substring, any. Empty list gives False."""
    return any(p in text for p in patterns)


def compile_patterns(patterns: Sequence[str], label: str) -> tuple[re.Pattern[str], ...]:
    """Compile regexes up front; ``ValueError`` naming ``label`` on a bad pattern (AWS disk)."""
    compiled: list[re.Pattern[str]] = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern))
        except re.error as e:
            raise ValueError(f"Invalid {label} pattern {pattern!r}: {e}") from e
    return tuple(compiled)


def state_in(state: str | None, allowed: Iterable[str]) -> bool:
    """Exact state membership (GCP and OCI VMs, Azure Spot power states, GCP IP, OCI disk)."""
    return state in tuple(allowed)


def state_is_substring_of_any(state: str, allowed: Iterable[str]) -> bool:
    """Azure disk: the disk state must be a substring of an allowed state string."""
    return any(state in entry for entry in allowed)


def status_contains_any_allowed(status: str, allowed: Iterable[str]) -> bool:
    """Azure VM: an allowed state must be a substring of the instance_view display status."""
    return any(entry in status for entry in allowed)


def _folded_key(key: str) -> str:
    return key.casefold().replace("-", "_")


def is_sensitive_yb_task(value: str) -> bool:
    """True for sensitive yb_task values (infra, prod, vpn, ...).

    Case-insensitive; matches the whole value, any token split on ``-``, ``_``, whitespace
    or ``/``, or a ``<sensitive>-`` / ``<sensitive>_`` prefix. Empty gives False.
    """
    folded = value.casefold().strip()
    if not folded:
        return False
    if folded in SENSITIVE_YB_TASK:
        return True
    if any(token in SENSITIVE_YB_TASK for token in _YB_TASK_SPLIT.split(folded)):
        return True
    return any(folded.startswith((s + "-", s + "_")) for s in SENSITIVE_YB_TASK)


def aws_disk_yb_task(tags: Tags) -> str:
    """AWS disk lookup: first key equal to yb_task ignoring case and ``-``/``_``; stripped value."""
    for key, value in tags.items():
        if _folded_key(key) == "yb_task":
            return value.strip()
    return ""


def aws_disk_has_sensitive_yb_task(tags: Tags) -> bool:
    """AWS disk: only the first folded yb_task key is judged."""
    return is_sensitive_yb_task(aws_disk_yb_task(tags))


def aws_spot_has_sensitive_yb_task(tags: Tags) -> bool:
    """AWS spot: exact (case sensitive) keys ``yb_task`` and ``yb-task``; either sensitive."""
    return any(key in tags and is_sensitive_yb_task(tags[key]) for key in _YB_TASK_KEYS)


def aws_is_kubernetes_volume(tags: Tags) -> bool:
    """AWS disk: a ``kubernetes.io/`` or ``ebs.csi.aws.com/`` key, or a known cluster key."""
    return any(key.startswith(_K8S_PREFIXES) or key in _K8S_KEYS for key in tags)


def aws_filter_wants_kubernetes(filter_tags: TagSpec) -> bool:
    """AWS disk: filter_tags explicitly names a Kubernetes key (opt-in to delete CSI volumes)."""
    return any(key.startswith(_K8S_PREFIXES) or key in _K8S_KEYS for key in filter_tags)


def aws_disk_skips_kubernetes(tags: Tags, filter_tags: TagSpec) -> bool:
    """AWS disk: skip Kubernetes volumes unless filter_tags names a Kubernetes key."""
    return aws_is_kubernetes_volume(tags) and not aws_filter_wants_kubernetes(filter_tags)


def gcp_is_managed_or_gke_node(labels: Tags, metadata_items: Iterable[tuple[str, str]]) -> bool:
    """GCP Spot VM: ``goog-gke-node`` label, or ``created-by`` metadata naming an
    ``instanceGroupManagers`` resource (managed instance group member)."""
    if _GKE_NODE_LABEL in labels:
        return True
    return any(
        key == "created-by" and "instanceGroupManagers" in value for key, value in metadata_items
    )


def _looks_oke(text: str) -> bool:
    folded = text.casefold()
    return any(marker in folded for marker in _OKE_MARKERS)


def oci_is_managed_or_oke_node(
    freeform_tags: Mapping[str, Any] | None,
    defined_tags: Mapping[str, Any] | None,
    metadata: object,
) -> bool:
    """OCI Spot VM: ``oke`` / instance-pool markers (case-insensitive substring) in any
    ``key:value`` of freeform tags, ``namespace:key:value`` of defined tags (non-dict
    namespaces ignored), or instance metadata (only when it is a dict)."""
    if any(_looks_oke(f"{k}:{v}") for k, v in (freeform_tags or {}).items()):
        return True
    for namespace, entries in (defined_tags or {}).items():
        if isinstance(entries, dict) and any(
            _looks_oke(f"{namespace}:{k}:{v}") for k, v in entries.items()
        ):
            return True
    return isinstance(metadata, dict) and any(_looks_oke(f"{k}:{v}") for k, v in metadata.items())


def effective_age(custom_age: AgeSpec | None, default_age: AgeSpec) -> AgeSpec:
    """``retention_age or age``: a truthy custom age tag replaces the default age."""
    return custom_age or default_age


def age_to_timedelta(age: AgeSpec | int) -> datetime.timedelta:
    """Convert an age (int days or days/hours dict) to a timedelta, as AWS disk does."""
    spec: AgeSpec = {"days": age} if isinstance(age, int) else age
    return datetime.timedelta(days=int(spec.get("days", 0)), hours=int(spec.get("hours", 0)))


def aws_disk_creation_floor(
    age: AgeSpec, detach_age: AgeSpec, retention_age: AgeSpec | None
) -> AgeSpec:
    """AWS disk CreateTime floor. With ``age``: the custom age if any, else ``age``.
    Without ``age``: the longer of the custom age and ``detach_age``. ``{}`` when none."""
    if age:
        return retention_age or age
    floors = [spec for spec in (retention_age, detach_age) if spec]
    return max(floors, key=age_to_timedelta) if floors else {}


def is_older_than_floor(
    created: datetime.datetime, now: datetime.datetime, floor: AgeSpec
) -> bool:
    """AWS disk: ``now - created >= floor``, ignoring MAX_AGE. A zero or empty floor gives False."""
    limit = age_to_timedelta(floor)
    return bool(limit) and (now - created) >= limit


def snapshot_old_enough(start_time: datetime.datetime, now: datetime.datetime, days: int) -> bool:
    """AWS snapshot: old when ``start_time <= now - days`` (exactly at the cutoff is old)."""
    return not start_time > now - datetime.timedelta(days=days)
