# Copyright (c) Yugabyte, Inc.

"""`SpotVM`, the legacy class `crc.py` uses, as an adapter over `SpotVmCleaner`.

It keeps its constructor, attributes and private hooks, and runs the cleaner's building blocks
region by region and instance by instance in the exact call order of the original implementation.
"""

import datetime
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from crc.core import age as core_age
from crc.core.cleaner import Operation
from crc.oci import _logger
from crc.oci._base import Base
from crc.oci.spot_vm import decide, leftovers
from crc.oci.spot_vm.cleaner import SpotVmCleaner, log_instance_error, log_summary
from crc.oci.spot_vm.client import (
    BlockClient,
    ClientFactory,
    ComputeClient,
    Pager,
    RegionClients,
    RegionLister,
    SdkInstance,
    clients_for,
    list_all,
    regions_of,
)
from crc.service import Service


@dataclass(frozen=True, slots=True)
class SpotVmRuntime:
    """Injected collaborators of `SpotVM`: any of them left as `None` is built from the OCI SDK
    (through `Base`), and the clock supplies `now` (the cleaner itself never reads the clock)."""

    clients_factory: ClientFactory | None = None
    list_regions: RegionLister | None = None
    pager: Pager | None = None
    clock: decide.Clock = datetime.datetime.now


class SpotVM(Service, Base):
    """
    Delete OCI preemptible instances that match tag/age filters.

    OKE node-pool / instance-pool members are skipped (CSI volumes stay attached).
    terminate_instance is the cancel (preserve_boot_volume=False). Attached
    block volumes are deleted after TERMINATED wait; volume freeform_tags are
    checked for exception/notags first. Attachment-list failure skips terminate.

    Attributes: `instance_names_to_delete` and `volumes_to_delete` (accumulated over calls),
    the constructor arguments, `get_deleted`, `delete_count`, `get_deleted_volumes`, `delete()`.
    """

    default_instance_state: ClassVar[list[str]] = ["RUNNING", "STOPPED"]
    # terminate_instance is async; volumes stay attached until TERMINATED.
    INSTANCE_TERMINATED_WAIT_SEC = decide.INSTANCE_TERMINATED_WAIT_SEC
    INSTANCE_POLL_INTERVAL_SEC = decide.INSTANCE_POLL_INTERVAL_SEC
    _runtime: SpotVmRuntime = SpotVmRuntime()
    _region: str | None = None

    def __init__(  # noqa: PLR0913, PLR0917 - the six parameters are the public signature
        self,
        dry_run: bool,
        filter_tags: Mapping[str, Sequence[str]],
        exception_tags: Mapping[str, Sequence[str]],
        age: core_age.AgeSpec,
        custom_age_tag_key: str,
        notags: Mapping[str, Sequence[str]],
        runtime: SpotVmRuntime | None = None,
    ) -> None:
        Service.__init__(self)
        Base.__init__(self)
        self.instance_names_to_delete: list[str] = []
        self.volumes_to_delete: list[str] = []
        self.dry_run = dry_run
        self.filter_tags = filter_tags or {}
        self.exception_tags = exception_tags or {}
        self.age = self.require_positive_age(age)
        self.custom_age_tag_key = custom_age_tag_key
        self.notags = notags or {}
        self._runtime = runtime or SpotVmRuntime()

    @property
    def get_deleted(self) -> list[str]:
        """Display names of the instances terminated (listed in a dry run)."""
        return self.instance_names_to_delete

    @property
    def delete_count(self) -> int:
        return len(self.instance_names_to_delete)

    @property
    def get_deleted_volumes(self) -> list[str]:
        """OCIDs of the leftover volumes deleted (listed in a dry run)."""
        return self.volumes_to_delete

    def _settings(self) -> decide.SpotVmSettings:
        return decide.SpotVmSettings(
            self.filter_tags or {},
            self.exception_tags or {},
            self.notags or {},
            self.age,
            self.custom_age_tag_key,
            self.default_instance_state,
            self.INSTANCE_TERMINATED_WAIT_SEC,
            self.INSTANCE_POLL_INTERVAL_SEC,
        )

    def _pager(self) -> Pager:
        return self._runtime.pager or list_all

    def _is_preemptible(self, instance: SdkInstance) -> bool:
        return decide.is_preemptible(instance)

    def _is_managed_or_oke_node(self, instance: SdkInstance) -> bool:
        """Skip OKE node-pool / instance-pool members (CSI PVs stay attached)."""
        return decide.is_managed_or_oke_node(instance)

    def _has_matching_filter_tags(self, instance: SdkInstance) -> bool:
        return decide.matches_filter_tags(self._settings(), instance)

    def _attached_block_volume_ids(
        self, compute_client: ComputeClient, instance_id: str
    ) -> list[str] | None:
        """Attached block volume IDs, or None if listing failed (the caller must not terminate)."""
        return leftovers.attached_volume_ids(
            compute_client, self.compartment_id, instance_id, self._pager(), self._region
        )

    def _wait_instance_terminated(self, compute_client: ComputeClient, instance_id: str) -> bool:
        return leftovers.wait_terminated(
            compute_client, instance_id, self._settings(), self._region
        )

    def _volume_ok_to_delete(self, blockstorage_client: BlockClient, volume_id: str) -> bool:
        return leftovers.volume_passes_tag_check(
            blockstorage_client, volume_id, self._settings(), self._region
        )

    def _delete_volume(self, blockstorage_client: BlockClient, volume_id: str) -> None:
        before = len(self.volumes_to_delete)
        if self.dry_run:
            self.volumes_to_delete.append(volume_id)
        else:
            leftovers.delete_leftover(
                blockstorage_client, volume_id, self.volumes_to_delete, self._region
            )
        if len(self.volumes_to_delete) > before:
            _logger.record_action(
                Operation.DELETE,
                dry_run=self.dry_run,
                region=self._region,
                resource_id=volume_id,
                name=None,
            )

    def delete(self) -> None:
        """Terminate (or, in a dry run, only list) the matching preemptible instances of every
        region and delete their leftover volumes."""
        runtime = self._runtime
        untyped: Any = self
        list_regions = runtime.list_regions or regions_of(untyped)
        cleaner = SpotVmCleaner(
            runtime.clients_factory or clients_for(untyped),
            list_regions,
            self._settings(),
            self._pager(),
        )
        for region in list_regions():
            started = _logger.region_started()
            listed = cleaner.list_region(region)
            if listed is None:
                continue
            clients, instances = listed
            selected = 0
            for instance in instances:
                try:
                    selected += self._process(clients, instance, runtime.clock, region)
                except Exception as e:
                    log_instance_error(getattr(instance, "display_name", "?"), e)
                    _logger.record_failure(region, getattr(instance, "id", "?"), e)
            _logger.region_done(region, len(instances), selected, started)

        log_summary(self.instance_names_to_delete, self.volumes_to_delete, dry_run=self.dry_run)

    def _process(
        self,
        clients: RegionClients,
        instance: SdkInstance,
        clock: decide.Clock,
        region: str | None = None,
    ) -> int:
        """Act on one instance; returns 1 when it was selected (terminated, or listed in a dry
        run), else 0."""
        self._region = region
        if decide.skip_reason(self._settings(), instance, clock) is not None:
            return 0
        leftover = self._attached_block_volume_ids(clients.compute, instance.id)
        if leftover is None:
            logging.error(
                "Skipping terminate for %s: volume attachment list failed", instance.display_name
            )
            return 0
        if not self.dry_run:
            clients.compute.terminate_instance(instance.id, preserve_boot_volume=False)
            logging.info("Terminating OCI preemptible instance %s", instance.display_name)
            if leftover and not self._wait_instance_terminated(clients.compute, instance.id):
                logging.error(
                    "Instance %s not TERMINATED yet; attempting leftover volume deletes anyway",
                    instance.display_name,
                )
        self.instance_names_to_delete.append(instance.display_name)
        _logger.record_action(
            Operation.DELETE,
            dry_run=self.dry_run,
            region=region,
            resource_id=instance.id,
            name=instance.display_name,
        )
        for volume_id in leftover:
            if self._volume_ok_to_delete(clients.block, volume_id):
                self._delete_volume(clients.block, volume_id)
        return 1
