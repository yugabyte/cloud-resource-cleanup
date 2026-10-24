# Copyright (c) Yugabyte, Inc.

"""Block volumes left behind by a terminated preemptible instance.

Attachments are read before the instance is terminated (a failed read blocks the terminate), the
instance is waited on until `TERMINATED`, and each volume is tag-checked before it is deleted.
Every read here is safe in `plan()`; `delete_leftover` is the only mutating call.
"""

import datetime
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from oci.exceptions import ServiceError

from crc.oci import _logger
from crc.oci.spot_vm.client import BlockClient, ComputeClient, Pager, SdkVolume
from crc.oci.spot_vm.decide import SpotVmSettings, tags_protect

_DETACHED = {"DETACHED", "DETACHING"}
_NOT_ATTACHED = {"DETACHED"}
_HTTP_NOT_FOUND = 404


@dataclass(frozen=True, slots=True)
class VolumeOwner:
    """The terminated instance a leftover volume is judged against (BUG-255): the volume is only
    deleted when it was created at or after `created` and no other instance has it attached."""

    list_attachments: Callable[..., Any]
    compartment_id: str
    instance_id: str
    created: datetime.datetime | None
    pager: Pager


def attached_volume_ids(
    compute: ComputeClient,
    compartment_id: str,
    instance_id: str,
    pager: Pager,
    region: str | None = None,
) -> list[str] | None:
    """Attached block volume IDs, or None if listing failed (the caller must not terminate:
    the leftovers would be lost)."""
    try:
        attachments = pager(
            compute.list_volume_attachments,
            compartment_id=compartment_id,
            instance_id=instance_id,
        ).data
    except Exception as e:  # noqa: BLE001 - SAF-040 attachment list failure keeps the volumes
        logging.error("Failed to list volume attachments for %s: %s", instance_id, e)
        _logger.record_failure(region, instance_id, e)
        return None

    volume_ids: list[str] = []
    for attachment in attachments:
        lifecycle = getattr(attachment, "lifecycle_state", "") or ""
        if lifecycle.upper() in _DETACHED:
            continue
        volume_id = getattr(attachment, "volume_id", None)
        if volume_id:
            volume_ids.append(volume_id)
            logging.info(
                "Instance %s has attached block volume %s (will delete after terminate)",
                instance_id,
                volume_id,
            )
    return volume_ids


def _poll_failed(instance_id: str, error: Exception, region: str | None = None) -> bool:
    logging.error("Error polling OCI instance %s during terminate wait: %s", instance_id, error)
    _logger.record_failure(region, instance_id, error)
    return False


def wait_terminated(
    compute: ComputeClient,
    instance_id: str,
    settings: SpotVmSettings,
    region: str | None = None,
) -> bool:
    """Block until the instance is TERMINATED (or gone). Volumes cannot be deleted while still
    attached during the asynchronous terminate."""
    deadline = time.monotonic() + settings.terminated_wait_sec
    while time.monotonic() < deadline:
        try:
            state = (compute.get_instance(instance_id).data.lifecycle_state or "").upper()
            if state == "TERMINATED":
                return True
            logging.info("Waiting for OCI instance %s to terminate (state=%s)", instance_id, state)
        except ServiceError as e:
            if getattr(e, "status", None) == _HTTP_NOT_FOUND:
                return True
            return _poll_failed(instance_id, e, region)
        except Exception as e:  # noqa: BLE001 - SAF-040 best-effort wait, poll failure reported
            return _poll_failed(instance_id, e, region)
        time.sleep(settings.poll_interval_sec)

    logging.error(
        "Timed out waiting for OCI instance %s to reach TERMINATED; "
        "will still attempt leftover volume deletes",
        instance_id,
    )
    _logger.record_failure(region, instance_id, "timed out waiting for TERMINATED")
    return False


def _not_owned(volume_id: str, why: str) -> bool:
    logging.debug("Skipping leftover volume %s: not owned by the instance (%s)", volume_id, why)
    _logger.record_skip("not_owned")
    return False


def volume_owned(
    volume: SdkVolume, volume_id: str, owner: VolumeOwner, region: str | None = None
) -> bool:
    """True only when the volume was created at or after the instance and is attached to no other
    instance. Anything uncertain (missing or incomparable times, failed read) keeps the volume."""
    volume_created = getattr(volume, "time_created", None)
    if not isinstance(volume_created, datetime.datetime) or not isinstance(
        owner.created, datetime.datetime
    ):
        return _not_owned(volume_id, "creation time unknown")
    try:
        older = volume_created < owner.created
    except TypeError:
        return _not_owned(volume_id, "creation times not comparable")
    if older:
        return _not_owned(volume_id, "created before the instance")
    try:
        attachments = owner.pager(
            owner.list_attachments,
            compartment_id=owner.compartment_id,
            volume_id=volume_id,
        ).data
    except Exception as e:  # noqa: BLE001 - SAF-040 attachment list failure keeps the volume
        logging.error("Failed to list attachments of volume %s (keeping it): %s", volume_id, e)
        _logger.record_failure(region, volume_id, e)
        return _not_owned(volume_id, "attachment list failed")
    for attachment in attachments:
        lifecycle = (getattr(attachment, "lifecycle_state", "") or "").upper()
        if lifecycle in _NOT_ATTACHED:
            continue
        if getattr(attachment, "instance_id", None) != owner.instance_id:
            return _not_owned(volume_id, "attached to another instance")
    return True


def volume_passes_tag_check(
    block: BlockClient,
    volume_id: str,
    settings: SpotVmSettings,
    region: str | None = None,
    owner: VolumeOwner | None = None,
) -> bool:
    """Read the volume and keep it when it cannot be read, its tags protect it or (when `owner`
    is given) it is not owned by the terminated instance."""
    try:
        volume = block.get_volume(volume_id).data
    except Exception as e:  # noqa: BLE001 - SAF-040 a failed volume read keeps the volume
        logging.error("Failed to get volume %s for tag check (skipping): %s", volume_id, e)
        _logger.record_failure(region, volume_id, e)
        return False
    if tags_protect(settings, volume.freeform_tags):
        logging.debug("Skipping leftover volume %s: exception_tags/notags", volume_id)
        _logger.record_skip("leftover_exception_tag")
        return False
    if owner is not None:
        return volume_owned(volume, volume_id, owner, region)
    return True


def delete_leftover(
    block: BlockClient, volume_id: str, done: list[str], region: str | None = None
) -> None:
    """Delete one leftover volume; a failure is logged and the volume left out of `done`."""
    try:
        block.delete_volume(volume_id)
        logging.info("Deleted leftover block volume %s", volume_id)
        done.append(volume_id)
    except Exception as e:  # noqa: BLE001 - SAF-040 per-volume isolation, failure logged and recorded
        logging.error("Failed to delete leftover volume %s: %s", volume_id, e)
        _logger.record_failure(region, volume_id, e)


def checked_volumes(
    block: BlockClient,
    volume_ids: Sequence[str],
    settings: SpotVmSettings,
    region: str | None = None,
    owner: VolumeOwner | None = None,
) -> list[str]:
    """The volumes of `volume_ids` that pass the tag and ownership check, in order."""
    return [v for v in volume_ids if volume_passes_tag_check(block, v, settings, region, owner)]
