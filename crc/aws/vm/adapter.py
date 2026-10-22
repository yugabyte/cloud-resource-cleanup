# Copyright (c) Yugabyte, Inc.

"""Legacy `VM` adapter of the AWS EC2 VM cleaner (`crc.aws.vm.cleaner.VmCleaner`).

`VM` keeps the constructor, attributes and methods `crc.py` uses and runs the cleaner region by
region in the exact call order of the original implementation.
"""

import datetime
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any, ClassVar

import boto3

from crc.aws.connectivity import CONNECTIVITY_ERRORS
from crc.aws.vm import scan as vm_scan
from crc.aws.vm.cleaner import (
    DEFAULT_REGION,
    DEFAULT_STATE,
    SERVICE_NAME,
    ClientFactory,
    Filters,
    RegionLister,
    VmCleaner,
    VmSettings,
    all_regions,
    boto3_client,
    build_vm_cleaner,
    dry_run_names,
    log_summary,
    note_dry_run,
    note_region,
    skip_region,
)
from crc.aws.vm.client import VmClient, VmScanClient, VmSession
from crc.core.cleaner import Candidate, Operation
from crc.service import Service

__all__ = ["VM", "VmRuntime", "build_vm_cleaner"]

Clock = Callable[[], datetime.datetime]


def _boto3_scan_client(region: str) -> VmScanClient:
    client: VmScanClient = boto3.client(SERVICE_NAME, region_name=region)
    return client


def _boto3_session() -> VmSession:
    session: VmSession = boto3.session.Session()
    return session


_UTC_CLOCK: Clock = partial(datetime.datetime.now, datetime.UTC)


@dataclass(frozen=True, slots=True)
class VmRuntime:
    """Injected collaborators of `VM`: SDK client factories, region lister and the clock that
    supplies `now` (the cleaner itself never reads the clock)."""

    client_factory: ClientFactory = boto3_client
    list_regions: RegionLister = all_regions
    clock: Clock = _UTC_CLOCK
    scan_client_factory: vm_scan.ScanClientFactory = _boto3_scan_client
    session_factory: vm_scan.SessionFactory = _boto3_session


class VM(Service):
    """Legacy entry point used by `crc.py`: terminates (`delete()`) or stops (`stop()`) running
    instances that match the filters. In dry run it only lists them.

    Attributes: `instance_names_to_delete`, `instance_names_to_stop`, `dry_run`,
    `filter_tags`, `exception_tags`, `age`, `custom_age_tag_key`, `notags` and `runtime` (the
    injected `VmRuntime`; tests replace it with fakes).
    """

    service_name = SERVICE_NAME
    default_region_name = DEFAULT_REGION
    default_instance_state: ClassVar[list[str]] = DEFAULT_STATE

    def __init__(
        self,
        dry_run: bool,
        filter_tags: dict[str, list[str]],
        exception_tags: dict[str, list[str]],
        age: dict[str, int],
        custom_age_tag_key: str,
        notags: dict[str, list[str]],
    ) -> None:
        super().__init__()
        self.instance_names_to_delete: list[str] = []
        self.instance_names_to_stop: list[str] = []
        self.dry_run = dry_run
        self.filter_tags = filter_tags
        self.exception_tags = exception_tags
        self.age = age
        self.custom_age_tag_key = custom_age_tag_key
        self.notags = notags
        self.runtime = VmRuntime()

    @property
    def get_deleted(self) -> list[str]:
        """Names of the instances deleted (listed in a dry run)."""
        return self.instance_names_to_delete

    @property
    def delete_count(self) -> int:
        """Number of instances deleted (listed in a dry run)."""
        count = len(self.instance_names_to_delete)
        logging.info(f"count of items in instance_names_to_delete: {count}")
        return count

    @property
    def get_stopped(self) -> list[str]:
        """Names of the instances stopped (their ids in a dry run)."""
        return self.instance_names_to_stop

    @property
    def stopped_count(self) -> int:
        """Number of instances stopped (listed in a dry run)."""
        count = len(self.instance_names_to_stop)
        logging.info(f"count of items in instance_names_to_stop: {count}")
        return count

    def find_instances_with_tag(self, tag_key: str, tag_value: str) -> list[dict[str, Any]]:
        """Instances with the tag across all regions (read-only)."""
        runtime = self.runtime
        return vm_scan.find_instances_with_tag(
            tag_key,
            tag_value,
            runtime.session_factory,
            runtime.scan_client_factory,
            runtime.list_regions,
        )

    def _cleaner(self, instance_state: Any = None) -> VmCleaner:
        settings = VmSettings(
            filter_tags=self.filter_tags,
            exception_tags=self.exception_tags,
            notags=self.notags,
            age=self.age,
            custom_age_tag_key=self.custom_age_tag_key,
            instance_state=instance_state,
        )
        return VmCleaner(self.runtime.client_factory, self.runtime.list_regions, settings)

    def _get_filter(self, instance_state: list[str]) -> Filters:
        return self._cleaner(instance_state).build_filters()

    def _get_filtered_instances(
        self, ec2: VmClient, instance_details: Mapping[str, Any]
    ) -> tuple[list[str], list[str]]:
        found, _ = self._cleaner().filter_instances(ec2, instance_details, "", self.runtime.clock())
        return [c.id for c in found], [c.name for c in found]

    def _should_skip_instance(self, tags: Sequence[Mapping[str, str]]) -> bool:
        return self._cleaner().should_skip(tags)

    def _get_instance_name(self, tags: Sequence[Mapping[str, str]]) -> str | None:
        return vm_scan.instance_name(tags)

    def _record(
        self,
        cleaner: VmCleaner,
        client: VmClient,
        found: Sequence[Candidate],
        operation: Operation,
    ) -> None:
        if self.dry_run:
            note_dry_run(operation, found)
            names = dry_run_names(operation, found)
        else:
            names, _ = cleaner.apply_region(client, found, operation)
        target = (
            self.instance_names_to_delete
            if operation is Operation.DELETE
            else self.instance_names_to_stop
        )
        target.extend(names)

    def _perform_operation(
        self, operation_type: str, instance_state: list[str] = default_instance_state
    ) -> None:
        operation = Operation(operation_type)
        runtime = self.runtime
        cleaner = self._cleaner(instance_state)
        filters = cleaner.build_filters()
        for region in runtime.list_regions():
            started = time.monotonic()
            try:
                client = runtime.client_factory(region)
                found, kept = cleaner.filter_instances(
                    client, client.describe_instances(Filters=filters), region, runtime.clock()
                )
                note_region(region, started, found, kept)
                if found:
                    self._record(cleaner, client, found, operation)
            except CONNECTIVITY_ERRORS as e:
                skip_region(region, operation, e)
        log_summary(
            operation, self.dry_run, self.instance_names_to_delete, self.instance_names_to_stop
        )

    def delete(self, instance_state: list[str] = default_instance_state) -> None:
        """Terminate the matching instances (only list them in a dry run)."""
        self._perform_operation("delete", instance_state)

    def stop(self) -> None:
        """Stop the matching instances (only list them in a dry run)."""
        self._perform_operation("stop", self.default_instance_state)
