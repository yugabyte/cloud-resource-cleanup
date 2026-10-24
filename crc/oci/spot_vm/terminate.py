# Copyright (c) Yugabyte, Inc.

"""Terminating one planned preemptible instance and handling its leftover volumes."""

import logging
from collections.abc import Sequence

from crc.core.cleaner import Candidate, Operation
from crc.oci import _logger
from crc.oci.spot_vm import leftovers
from crc.oci.spot_vm.client import Pager, RegionClients
from crc.oci.spot_vm.decide import SpotVmSettings


def owner_of(clients: RegionClients, candidate: Candidate, pager: Pager) -> leftovers.VolumeOwner:
    return leftovers.VolumeOwner(
        clients.compute.list_volume_attachments,
        clients.compartment_id,
        candidate.id,
        candidate.launch_time,
        pager,
    )


def with_checked_volumes(
    clients: RegionClients,
    candidate: Candidate,
    settings: SpotVmSettings,
    pager: Pager,
    *,
    dry_run: bool,
) -> Candidate:
    """In a dry run, the candidate with the leftover volumes that pass the tag check."""
    if not dry_run:
        return candidate
    volumes = leftovers.checked_volumes(
        clients.block,
        candidate.extra["leftover"],
        settings,
        candidate.region,
        owner_of(clients, candidate, pager),
    )
    return Candidate(
        id=candidate.id,
        name=candidate.name,
        region=candidate.region,
        state=candidate.state,
        launch_time=candidate.launch_time,
        tags=candidate.tags,
        extra={"leftover": candidate.extra["leftover"], "volumes": tuple(volumes)},
    )


def terminate_instance(
    clients: RegionClients,
    candidate: Candidate,
    settings: SpotVmSettings,
    pager: Pager,
    done: list[str],
    deleted: list[str],
) -> None:
    """Terminate one planned instance, wait for it, and delete its leftover volumes."""
    leftover: Sequence[str] = candidate.extra["leftover"]
    clients.compute.terminate_instance(candidate.id, preserve_boot_volume=False)
    logging.info("Terminating OCI preemptible instance %s", candidate.name)
    if leftover and not leftovers.wait_terminated(
        clients.compute, candidate.id, settings, candidate.region
    ):
        logging.error(
            "Instance %s not TERMINATED yet; attempting leftover volume deletes anyway",
            candidate.name,
        )
    done.append(candidate.name)
    _logger.record_action(
        Operation.DELETE,
        dry_run=False,
        region=candidate.region,
        resource_id=candidate.id,
        name=candidate.name,
    )
    owner = owner_of(clients, candidate, pager)
    for volume_id in leftover:
        if leftovers.volume_passes_tag_check(
            clients.block, volume_id, settings, candidate.region, owner
        ):
            before = len(deleted)
            leftovers.delete_leftover(clients.block, volume_id, deleted, candidate.region)
            if len(deleted) > before:
                _logger.record_action(
                    Operation.DELETE,
                    dry_run=False,
                    region=candidate.region,
                    resource_id=volume_id,
                    name=None,
                )
