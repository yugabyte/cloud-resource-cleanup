# Copyright (c) Yugabyte, Inc.

"""Tag and age selection of the Azure VM cleaner, in the order of main."""

import logging
from typing import cast

from crc.azu.vm.client import SdkVm
from crc.azu.vm.settings import Clock, VmSettings
from crc.core import age as core_age
from crc.core import policy


def skip_reason(settings: VmSettings, vm: SdkVm, tags: policy.Tags, clock: Clock) -> str | None:
    """The reason a VM is kept before the status check, or None when it is selected."""
    if policy.skip_untagged(tags, policy.UntaggedMode.SKIP):
        return "untagged"
    if policy.should_skip_by_tags(tags, settings.exception_tags, settings.notags):
        return "matches exception_tags or notags"
    if not policy.filter_tags_all_keys(tags, settings.filter_tags):
        return "does not match filter_tags"
    if not is_old(settings, vm, clock):
        return "not older than age"
    return None


def is_old(settings: VmSettings, vm: SdkVm, clock: Clock) -> bool:
    """Older than `age`, or than the VM's own custom age tag (capped by MAX_AGE)."""
    created = vm.time_created
    now = clock().astimezone(created.tzinfo)
    logging.debug(vm.tags)
    retention_age = core_age.get_retention_age(
        cast(dict[str, str], vm.tags), settings.custom_age_tag_key
    )
    if retention_age:
        logging.info("Updating age for vm: %s", vm.name)
    return core_age.is_old(policy.effective_age(retention_age, settings.age), now, created)
