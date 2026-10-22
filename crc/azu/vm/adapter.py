# Copyright (c) Yugabyte, Inc.

"""Legacy entry point `VM` that `crc.py` uses, as a thin adapter over `VmCleaner`."""

import datetime
import logging
from typing import ClassVar

from crc.azu import _logger
from crc.azu._base import Base
from crc.azu.vm.cleaner import Clock, TagSpec, Tally, VmCleaner, VmSettings, log_summary
from crc.core import age as core_age
from crc.core.cleaner import Candidate, Operation
from crc.service import Service


class VM(Service):
    """Deletes (`delete()`, with the `<vm>-NIC` NIC) or powers off (`stop()`) VMs that are
    tagged, match the filter tags (all keys), are not excluded by exception tags or notags, are
    old enough and are in an allowed state. In dry run it only lists them.

    Attributes: `instance_names_to_delete`, `instance_names_to_stop` and `nics_names_to_delete`
    (names acted on, or listed in a dry run, accumulated over calls), `base` and the constructor
    arguments. `get_deleted`, `get_deleted_nic` and `get_stopped` read the names; the matching
    `*_count` properties log the count.
    """

    default_instance_state: ClassVar[list[str]] = ["running"]
    """The default state of instances that will be deleted or stopped."""

    def __init__(  # noqa: PLR0913, PLR0917 - the six parameters are the public signature
        self,
        resource_group: str,
        dry_run: bool,
        filter_tags: TagSpec,
        exception_tags: TagSpec,
        age: core_age.AgeSpec,
        custom_age_tag_key: str,
        notags: TagSpec,
        clock: Clock = datetime.datetime.now,
    ) -> None:
        """
        :param resource_group: Resource group used for every call except the subscription-wide
            listing; empty means `AZURE_RESOURCE_GROUP`.
        :param dry_run: If True, only list matching VMs without deleting or stopping them.
        :param filter_tags: Tags and values a VM must have (all keys); empty selects every
            tagged VM.
        :param exception_tags: Tags and values that exclude a VM (any key).
        :param age: Age (days/hours) a VM must reach.
        :param custom_age_tag_key: Tag name to overwrite the age condition.
        :param notags: Tags that exclude VMs which have all of them.
        :param clock: Supplies the current time for the age check; tests pass a fixed one.
        """
        super().__init__()
        self.instance_names_to_delete: list[str] = []
        self.instance_names_to_stop: list[str] = []
        self.nics_names_to_delete: list[str] = []
        self.base = Base(resource_group)
        self.dry_run = dry_run
        self.filter_tags = filter_tags
        self.exception_tags = exception_tags
        self.age = age
        self.custom_age_tag_key = custom_age_tag_key
        self.notags = notags
        self._clock = clock

    @property
    def get_deleted(self) -> list[str]:
        """Names of the VMs deleted (listed in a dry run)."""
        return self.instance_names_to_delete

    @property
    def delete_count(self) -> int:
        """Number of VMs deleted (listed in a dry run); logs the count."""
        count = len(self.instance_names_to_delete)
        logging.info(f"count of items in instance_names_to_delete: {count}")
        return count

    @property
    def get_deleted_nic(self) -> list[str]:
        """Names of the NICs deleted (listed in a dry run)."""
        return self.nics_names_to_delete

    @property
    def nic_delete_count(self) -> int:
        """Number of NICs deleted (listed in a dry run); logs the count."""
        count = len(self.nics_names_to_delete)
        logging.info(f"count of items in nics_names_to_delete: {count}")
        return count

    @property
    def get_stopped(self) -> list[str]:
        """Names of the VMs stopped (listed in a dry run)."""
        return self.instance_names_to_stop

    @property
    def stopped_count(self) -> int:
        """Number of VMs stopped (listed in a dry run); logs the count."""
        count = len(self.instance_names_to_stop)
        logging.info(f"count of items in instance_names_to_stop: {count}")
        return count

    def delete(self, instance_state: list[str] = default_instance_state) -> None:
        """Delete (or, in a dry run, only list) the matching VMs and their NICs. Waits for the
        VM delete to finish, then deletes the NIC (with short retries)."""
        self._perform_operation(Operation.DELETE, instance_state)

    def stop(self) -> None:
        """Power off (or, in a dry run, only list) the matching VMs."""
        self._perform_operation(Operation.STOP, self.default_instance_state)

    def _perform_operation(self, operation: Operation, instance_state: list[str]) -> None:
        cleaner = VmCleaner(
            self.base.get_compute_client,
            self.base.get_network_client,
            self.base.resource_group,
            VmSettings(
                self.filter_tags or {},
                self.exception_tags or {},
                self.notags or {},
                self.age,
                self.custom_age_tag_key,
                instance_state,
            ),
        )
        tally = Tally(
            self.instance_names_to_delete, self.instance_names_to_stop, self.nics_names_to_delete
        )
        started = _logger.started()
        scanned = selected = 0
        for vm in cleaner.list_vms():
            scanned += 1
            decision = cleaner.decide(vm, self._clock)
            if isinstance(decision, Candidate):
                acted = cleaner.process(decision.id, operation, tally, dry_run=self.dry_run)
                selected += int(acted)
        _logger.region_done(self.base.resource_group, scanned, selected, started)
        log_summary(operation, tally, dry_run=self.dry_run)
