# Copyright (c) Yugabyte, Inc.

"""Which preemptible instances and leftover volumes are fair game.

State, preemptible, OKE, tag and age checks in the order of main. Tag and age decisions come
from `crc.core.policy` and `crc.core.age`; the clock is injected and only read by the age check.
"""

import datetime
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import cast

from crc.core import age as core_age
from crc.core import policy
from crc.oci import _logger
from crc.oci.spot_vm.client import SdkInstance

DEFAULT_INSTANCE_STATES = ("RUNNING", "STOPPED")
INSTANCE_TERMINATED_WAIT_SEC = 180
INSTANCE_POLL_INTERVAL_SEC = 5

SKIP_KEYS = {
    "state is not allowed": "state",
    "not preemptible": "not_preemptible",
    "OKE or instance pool member": "oke_member",
    "matches exception_tags or notags": "exception_tag",
    "does not match filter_tags": "filter_mismatch",
    "not older than age": "too_young",
}

TagSpec = Mapping[str, Sequence[str]]
Clock = Callable[[], datetime.datetime]


@dataclass(frozen=True, slots=True)
class SpotVmSettings:
    """Filters and thresholds of one run."""

    filter_tags: TagSpec = field(default_factory=dict)
    exception_tags: TagSpec = field(default_factory=dict)
    notags: TagSpec = field(default_factory=dict)
    age: core_age.AgeSpec = field(default_factory=dict)
    custom_age_tag_key: str = ""
    instance_states: Sequence[str] = DEFAULT_INSTANCE_STATES
    terminated_wait_sec: float = INSTANCE_TERMINATED_WAIT_SEC
    poll_interval_sec: float = INSTANCE_POLL_INTERVAL_SEC


def tags_protect(settings: SpotVmSettings, tags: Mapping[str, str] | None) -> bool:
    """exception_tags (any key) or notags (all keys) protect a resource; untagged never."""
    normalised = policy.normalize_tags(tags)
    if not normalised:
        return False
    return policy.should_skip_by_tags(normalised, settings.exception_tags, settings.notags)


def is_preemptible(instance: SdkInstance) -> bool:
    return getattr(instance, "preemptible_instance_config", None) is not None


def is_managed_or_oke_node(instance: SdkInstance) -> bool:
    """OKE node-pool and instance-pool members keep their CSI volumes attached."""
    return policy.oci_is_managed_or_oke_node(
        instance.freeform_tags,
        instance.defined_tags,
        getattr(instance, "metadata", None) or {},
    )


def matches_filter_tags(settings: SpotVmSettings, instance: SdkInstance) -> bool:
    return policy.filter_tags_all_keys(
        policy.normalize_tags(instance.freeform_tags), settings.filter_tags
    )


def is_old_instance(settings: SpotVmSettings, instance: SdkInstance, clock: Clock) -> bool:
    """Older than `age` or than the custom age tag; an invalid custom age tag keeps it."""
    created = instance.time_created
    if created.tzinfo is None:
        created = created.replace(tzinfo=clock().astimezone().tzinfo)
    now = clock().astimezone(created.tzinfo)
    try:
        retention_age = core_age.validated_retention_age(
            cast(dict[str, str], instance.freeform_tags or {}), settings.custom_age_tag_key
        )
    except ValueError:
        return False
    if retention_age:
        logging.info("Updating age for preemptible instance: %s", instance.display_name)
    return core_age.is_old(policy.effective_age(retention_age, settings.age), now, created)


def skip_reason(settings: SpotVmSettings, instance: SdkInstance, clock: Clock) -> str | None:
    """Why `instance` is kept, or `None` when it is a candidate. Checks run lazily, in order."""
    kept_when: tuple[tuple[str, Callable[[], bool]], ...] = (
        ("state is not allowed", lambda: not _state_allowed(settings, instance)),
        ("not preemptible", lambda: not is_preemptible(instance)),
        ("OKE or instance pool member", lambda: is_managed_or_oke_node(instance)),
        (
            "matches exception_tags or notags",
            lambda: tags_protect(settings, instance.freeform_tags),
        ),
        ("does not match filter_tags", lambda: not matches_filter_tags(settings, instance)),
        ("not older than age", lambda: not is_old_instance(settings, instance, clock)),
    )
    for reason, applies in kept_when:
        if applies():
            _logger.record_skip(SKIP_KEYS[reason])
            return reason
    return None


def _state_allowed(settings: SpotVmSettings, instance: SdkInstance) -> bool:
    return policy.state_in(instance.lifecycle_state, settings.instance_states)
