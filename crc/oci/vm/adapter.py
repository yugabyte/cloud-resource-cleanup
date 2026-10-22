# Copyright (c) Yugabyte, Inc.

"""`VM`, the legacy OCI instance class `crc.py` uses: it keeps its constructor and attributes and
runs the cleaner region by region, instance by instance, in the exact call order of the original
implementation."""

import datetime
import logging
from dataclasses import dataclass
from typing import ClassVar

from crc.core import age as core_age
from crc.core.cleaner import Candidate, Operation
from crc.oci import _logger
from crc.oci._base import Base
from crc.oci.connectivity import CONNECTIVITY_ERRORS, log_skipped_region
from crc.oci.disk.api import RegionLister, regions_of
from crc.oci.vm.cleaner import Clock, TagSpec, VmCleaner, VmSettings, _log_summary
from crc.oci.vm.client import ApiFactory, compute_api_for
from crc.service import Service


@dataclass(frozen=True, slots=True)
class VmRuntime:
    """Injected collaborators of `VM`: any of them left as `None` is built from the OCI SDK
    (through `Base`), and the clock supplies `now` (the cleaner itself never reads the clock)."""

    api_factory: ApiFactory | None = None
    list_regions: RegionLister | None = None
    clock: Clock = datetime.datetime.now


class VM(Service, Base):
    """Legacy entry point used by `crc.py`: terminates (`delete()`) or stops (`stop()`)
    instances that match the filter tags and are older than `age`, excluding exception tags and
    notags. In dry run it only lists them.

    Attributes: `instance_names_to_delete` and `instance_names_to_stop` (display names acted
    on, or listed in a dry run, accumulated over calls), the constructor arguments,
    `get_deleted`, `delete_count`, `get_stopped`, `stopped_count`, `delete()` and `stop()`.
    """

    default_instance_state: ClassVar[list[str]] = ["RUNNING"]
    """The default state of instances that will be deleted."""

    def __init__(  # noqa: PLR0913, PLR0917 - the six parameters are the public signature
        self,
        dry_run: bool,
        filter_tags: TagSpec,
        exception_tags: TagSpec,
        age: core_age.AgeSpec,
        custom_age_tag_key: str,
        notags: TagSpec,
        runtime: VmRuntime | None = None,
    ) -> None:
        """
        :param dry_run: If True, only list matching instances without deleting/stopping them.
        :param filter_tags: Freeform tags and their values used to select instances.
        :param exception_tags: Freeform tags and their values that exclude instances.
        :param age: Age (days/hours) of instances that will be deleted.
        :param custom_age_tag_key: Tag name to overwrite the age condition.
        :param notags: Tags that exclude instances which have all of them.
        :param runtime: Injected SDK access and clock; tests pass fakes.
        """
        Service.__init__(self)
        Base.__init__(self)
        self.instance_names_to_delete: list[str] = []
        self.instance_names_to_stop: list[str] = []
        self.dry_run = dry_run
        self.filter_tags = filter_tags
        self.exception_tags = exception_tags
        self.age = age
        self.custom_age_tag_key = custom_age_tag_key
        self.notags = notags
        self._runtime = runtime or VmRuntime()

    @property
    def get_deleted(self) -> list[str]:
        """Display names of the instances terminated (listed in a dry run)."""
        return self.instance_names_to_delete

    @property
    def delete_count(self) -> int:
        """Number of instances terminated (listed in a dry run); logs the count."""
        count = len(self.instance_names_to_delete)
        logging.info(f"count of items in instance_names_to_delete: {count}")
        return count

    @property
    def get_stopped(self) -> list[str]:
        """Display names of the instances stopped (listed in a dry run)."""
        return self.instance_names_to_stop

    @property
    def stopped_count(self) -> int:
        """Number of instances stopped (listed in a dry run); logs the count."""
        count = len(self.instance_names_to_stop)
        logging.info(f"count of items in instance_names_to_stop: {count}")
        return count

    def delete(self, instance_state: list[str] = default_instance_state) -> None:
        """Terminate (or, in a dry run, only list) the matching instances of every region."""
        self._perform_operation(Operation.DELETE, instance_state)

    def stop(self) -> None:
        """Stop (or, in a dry run, only list) the matching instances of every region."""
        self._perform_operation(Operation.STOP, self.default_instance_state)

    def _perform_operation(self, operation: Operation, instance_state: list[str]) -> None:
        runtime = self._runtime
        api_factory = runtime.api_factory or compute_api_for(self)
        list_regions = runtime.list_regions or regions_of(self)
        cleaner = VmCleaner(
            api_factory,
            list_regions,
            VmSettings(
                self.filter_tags or {},
                self.exception_tags or {},
                self.notags or {},
                self.age,
                self.custom_age_tag_key,
                instance_state,
            ),
        )
        names = (
            self.instance_names_to_delete
            if operation is Operation.DELETE
            else self.instance_names_to_stop
        )
        for region in list_regions():
            started = _logger.region_started()
            try:
                api = api_factory(region)
                instances = api.list_instances()
            except CONNECTIVITY_ERRORS as e:
                log_skipped_region(region, f"list_instances vm {operation}", e)
                _logger.region_skipped(region, e)
                continue
            selected = 0
            for instance in instances:
                decision = cleaner.decide(instance, region, runtime.clock)
                if isinstance(decision, Candidate):
                    selected += 1
                    cleaner.act(api, decision, operation, names, dry_run=self.dry_run)
            _logger.region_done(region, len(instances), selected, started)
        _log_summary(
            operation,
            self.instance_names_to_delete,
            self.instance_names_to_stop,
            dry_run=self.dry_run,
        )
