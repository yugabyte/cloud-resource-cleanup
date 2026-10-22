# Copyright (c) Yugabyte, Inc.

"""Instance filters of the GCP Spot VM cleaner: what is a Spot VM, what to keep."""

import datetime
import logging
from collections.abc import Callable, Iterator
from typing import Any

from crc.core import age as core_age
from crc.core import policy
from crc.gcp.spot_vm.client import InstanceView
from crc.gcp.spot_vm.settings import TIME_FORMAT, SpotSettings

Clock = Callable[[], datetime.datetime]


def _labels(instance: InstanceView) -> dict[str, str]:
    return dict(getattr(instance, "labels", None) or {})


def is_spot_instance(instance: InstanceView) -> bool:
    """`scheduling.preemptible` is true, or the provisioning model is `SPOT`."""
    scheduling = getattr(instance, "scheduling", None)
    if not scheduling:
        return False
    if getattr(scheduling, "preemptible", False):
        return True
    provisioning = str(getattr(scheduling, "provisioning_model", "") or "").upper()
    return provisioning == "SPOT"


def labels_protect(labels: object, settings: SpotSettings) -> bool:
    """True when `exception_labels` matches any key or `notags` matches every key. Empty
    labels never protect."""
    if not labels:
        return False
    tags = policy.normalize_tags(labels)
    return policy.should_skip_by_tags(tags, settings.exception_labels, settings.notags)


def _metadata_items(instance: InstanceView) -> Iterator[tuple[Any, str]]:
    metadata = getattr(instance, "metadata", None)
    # Iterate any sequence (proto-plus RepeatedComposite is not list/tuple).
    for item in getattr(metadata, "items", None) or []:
        yield getattr(item, "key", None), str(getattr(item, "value", "") or "")


def is_managed_or_gke_node(instance: InstanceView) -> bool:
    """A GKE node (`goog-gke-node` label) or a managed instance group member (`created-by`
    metadata naming `instanceGroupManagers`)."""
    tags = policy.normalize_tags(_labels(instance))
    return policy.gcp_is_managed_or_gke_node(tags, _metadata_items(instance))


def should_skip_instance(instance: InstanceView, settings: SpotSettings) -> bool:
    """The instance labels match `exception_labels` or `notags`."""
    return labels_protect(_labels(instance), settings)


def has_matching_filter_label(instance: InstanceView, settings: SpotSettings) -> bool:
    """Every `filter_labels` key matches; an empty filter matches all (unlabelled included)."""
    if not settings.filter_labels:
        return True
    tags = policy.normalize_tags(_labels(instance))
    return policy.filter_tags_all_keys(tags, settings.filter_labels)


def is_old_instance(instance: InstanceView, settings: SpotSettings, clock: Clock) -> bool:
    """At least as old as the custom age label or else `age`. An invalid custom age label
    keeps the instance (fail closed)."""
    created = datetime.datetime.strptime(instance.creation_timestamp, TIME_FORMAT)
    now = clock().astimezone(created.tzinfo)
    try:
        retention_age = core_age.validated_retention_age(
            _labels(instance), settings.custom_age_tag_key or ""
        )
    except ValueError:
        return False
    if retention_age:
        logging.info("Updating age for Spot VM: %s", instance.name)
    return core_age.is_old(policy.effective_age(retention_age, settings.age), now, created)


def reject_reason(instance: InstanceView, settings: SpotSettings, clock: Clock) -> str | None:
    """The skip reason of an instance to keep, or None when every instance filter passes, in
    the order of `main`: status, Spot, managed or GKE node, exception labels, filter labels, age."""
    if not policy.state_in(instance.status, settings.instance_states):
        return "state"
    if not is_spot_instance(instance):
        return "not_spot"
    if is_managed_or_gke_node(instance):
        return "managed_or_gke"
    if should_skip_instance(instance, settings):
        return "exception_tag"
    if not has_matching_filter_label(instance, settings):
        return "filter_mismatch"
    return None if is_old_instance(instance, settings, clock) else "too_young"


def is_selected(instance: InstanceView, settings: SpotSettings, clock: Clock) -> bool:
    """All instance filters, in the order of `main`: status, Spot, managed or GKE node,
    exception labels, filter labels, age."""
    return reject_reason(instance, settings, clock) is None
