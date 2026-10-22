# Copyright (c) Yugabyte, Inc.

"""The legacy `Disk` class `crc.py` uses.

It keeps its constructor and attributes and runs `DiskCleaner` region by region, volume by
volume, in the exact call order of the original implementation.
"""

import logging
from collections.abc import Sequence

from crc.core import age as core_age
from crc.core.cleaner import Candidate
from crc.oci import _logger
from crc.oci._base import Base
from crc.oci.connectivity import CONNECTIVITY_ERRORS, log_skipped_region
from crc.oci.disk.api import SdkVolume, VolumeApi, api_for, regions_of
from crc.oci.disk.cleaner import (
    _LIST_CONTEXT,
    KIND_BOOT_DISK,
    KIND_DISK,
    DiskCleaner,
    DiskRuntime,
    DiskSettings,
    TagSpec,
    _log_summary,
)
from crc.service import Service


class Disk(Service, Base):
    """Legacy entry point used by `crc.py`: deletes unattached block volumes and boot volumes
    that match the filter tags and are older than `age`, excluding exception tags and notags.
    In dry run it only lists them.

    Attributes: `disk_names_to_delete` (display names deleted, or listed in a dry run,
    accumulated over calls), the constructor arguments, `get_deleted`, `count` and `delete()`.
    """

    def __init__(  # noqa: PLR0913, PLR0917 - the six parameters are the public signature
        self,
        dry_run: bool,
        filter_tags: TagSpec,
        exception_tags: TagSpec,
        age: core_age.AgeSpec,
        custom_age_tag_key: str,
        notags: TagSpec,
        runtime: DiskRuntime | None = None,
    ) -> None:
        """
        :param dry_run: If True, only list matching volumes without deleting them.
        :param filter_tags: Freeform tags and their values used to select volumes.
        :param exception_tags: Freeform tags and their values that exclude volumes.
        :param age: Age (days/hours) since volume creation, used to decide deletion eligibility.
        :param custom_age_tag_key: Tag name to overwrite the age condition.
        :param notags: Tags that exclude volumes which have all of them.
        :param runtime: Injected SDK access and clock; tests pass fakes.
        """
        Service.__init__(self)
        Base.__init__(self)
        self.disk_names_to_delete: list[str] = []
        self.dry_run = dry_run
        self.filter_tags = filter_tags
        self.exception_tags = exception_tags
        self.age = age
        self.custom_age_tag_key = custom_age_tag_key
        self.notags = notags
        self._runtime = runtime or DiskRuntime()

    @property
    def get_deleted(self) -> list[str]:
        """Display names of the volumes deleted (listed in a dry run)."""
        return self.disk_names_to_delete

    @property
    def count(self) -> int:
        """Number of volumes deleted (listed in a dry run); logs the count."""
        count = len(self.disk_names_to_delete)
        logging.info(f"count of items in disk_names_to_delete: {count}")
        return count

    def delete(self) -> None:
        """Delete (or, in a dry run, only list) the matching volumes of every region."""
        runtime = self._runtime
        api_factory = runtime.api_factory or api_for(self)
        list_regions = runtime.list_regions or regions_of(self)
        cleaner = DiskCleaner(
            api_factory,
            list_regions,
            DiskSettings(
                self.filter_tags or {},
                self.exception_tags or {},
                self.notags or {},
                self.age,
                self.custom_age_tag_key,
            ),
        )
        for region in list_regions():
            started = _logger.region_started()
            try:
                api = api_factory(region)
                volumes, boot_volumes = cleaner.list_region(api)
            except CONNECTIVITY_ERRORS as e:
                log_skipped_region(region, _LIST_CONTEXT, e)
                _logger.region_skipped(region, e)
                continue
            attached = cleaner.attached_volume_ids(api, region)
            selected = self._process(cleaner, api, volumes, attached, KIND_DISK, region)
            attached_boot = cleaner.attached_boot_volume_ids(api, region)
            selected += self._process(
                cleaner, api, boot_volumes, attached_boot, KIND_BOOT_DISK, region
            )
            _logger.region_done(region, len(volumes) + len(boot_volumes), selected, started)
        _log_summary(self.disk_names_to_delete, dry_run=self.dry_run)

    def _process(
        self,
        cleaner: DiskCleaner,
        api: VolumeApi,
        volumes: Sequence[SdkVolume],
        attached_ids: set[str],
        kind: str,
        region: str,
    ) -> int:
        selected = 0
        for volume in volumes:
            decision = cleaner.decide(volume, attached_ids, kind, region, self._runtime.clock())
            if isinstance(decision, Candidate):
                selected += 1
                cleaner.remove(api, decision, self.disk_names_to_delete, dry_run=self.dry_run)
        return selected
