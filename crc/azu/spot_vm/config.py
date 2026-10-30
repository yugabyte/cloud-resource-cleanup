# Copyright (c) Yugabyte, Inc.

"""Settings, age check and summary logging of the Azure Spot VM cleaner."""

import datetime
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from crc.azu.spot_vm.removal import Removed
from crc.core import age as core_age
from crc.core import policy

STOPPED_POWER_STATES = ("VM stopped", "VM deallocated")

TagSpec = Mapping[str, Sequence[str]]


@dataclass(frozen=True, slots=True)
class SpotVmConfig:
    """Flags of one run. `age` must already be validated (`require_positive_age`)."""

    resource_group: str
    filter_tags: TagSpec | None
    exception_tags: TagSpec | None
    notags: TagSpec | None
    age: core_age.AgeSpec
    custom_age_tag_key: str | None


def log_summary(removed: Removed, *, dry_run: bool) -> None:
    """The warning lines main logs at the end of a run."""
    if not removed.vms:
        logging.warning("No Azure Spot VMs (stopped/deallocated) to delete.")
    label = "which would be deleted" if dry_run else "deleted"
    logging.warning(
        "List of Azure Spot VMs (Total: %s) %s: %s", len(removed.vms), label, removed.vms
    )
    logging.warning("List of Azure NICs (Total: %s) %s: %s", len(removed.nics), label, removed.nics)
    logging.warning(
        "List of Azure leftover disks (delete_option!=Delete) (Total: %s) %s: %s",
        len(removed.disks),
        label,
        removed.disks,
    )


def old_enough(config: SpotVmConfig, vm: Any, now: datetime.datetime, time_created: Any) -> bool:
    """Older than `age` (or than the custom age tag); an invalid custom age tag keeps the VM."""
    current = now.astimezone(time_created.tzinfo)
    try:
        retention_age = core_age.validated_retention_age(
            vm.tags or {}, config.custom_age_tag_key or ""
        )
    except ValueError:
        return False
    if retention_age:
        logging.info("Updating age for Spot VM: %s", vm.name)
    age = policy.effective_age(retention_age, config.age)
    return core_age.is_old(age, current, time_created)
