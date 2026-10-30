# Copyright (c) Yugabyte, Inc.

"""Mutating cleanup of one spot request and the region sweep of tagged leftover volumes.

Order (critical for Type=persistent): tag DoT=false volumes, cancel the request, terminate
the instance, wait for the volumes to become available, delete them.
"""

import datetime
import logging
from dataclasses import dataclass

from botocore.exceptions import ClientError

from crc.aws.spot_instance_requests.client import SpotClient
from crc.aws.spot_instance_requests.leftover import (
    invalidate_leftover_marker,
    tag_volumes_as_leftover,
    wait_volume_available_then_delete,
)
from crc.aws.spot_instance_requests.model import (
    NOT_FOUND_INSTANCE,
    Inspection,
    RequestMatch,
    SpotSettings,
    SpotState,
    Waiter,
)
from crc.aws.spot_instance_requests.volumes import (
    ERRORS,
    filter_deletable_leftover_volumes,
    inspect_instance,
    instance_state_and_dot_false_volumes,
    run_sweep,
)
from crc.core import logger

Scope = dict[str, list[str]]


@dataclass(frozen=True, slots=True)
class CleanupEnv:
    """Everything one cleanup needs besides the client and the request."""

    settings: SpotSettings
    state: SpotState
    waiter: Waiter
    region: str


def _cancel(
    env: CleanupEnv, client: SpotClient, match: RequestMatch, inspection: Inspection, tagged: Scope
) -> bool:
    """Cancel the request (or note it is already cancelled). False aborts the cleanup."""
    if match.already_cancelled:
        logging.info(
            "Spot request %s already cancelled in %s; finishing terminate/leftover volumes",
            match.request_id,
            env.region,
        )
        return True
    try:
        client.cancel_spot_instance_requests(SpotInstanceRequestIds=[match.request_id])
        env.state.spot_requests_to_delete.append(match.request_id)
        logger.current().action("deleted", env.region, match.request_id)
        logging.info(
            "Cancelled spot request %s (type=%s) in %s",
            match.request_id,
            match.request_type,
            env.region,
        )
    except ERRORS as e:
        logger.current().fail(env.region, match.request_id, e, logged=True)
        logging.error(
            "Failed to cancel spot request %s in %s: %s — not terminating "
            "its instance (persistent requests would relaunch)",
            match.request_id,
            env.region,
            e,
        )
        # Invalidate even on transport errors: a timed-out cancel may still have succeeded.
        if tagged:
            invalidate_leftover_marker(client, inspection.leftover_volumes, tagged)
        return False
    return True


def _terminate_ambiguous(
    env: CleanupEnv, client: SpotClient, match: RequestMatch, instance_id: str, error: Exception
) -> bool:
    logging.error(
        "Failed to terminate instance %s after cancelling %s: %s",
        instance_id,
        match.request_id,
        error,
    )
    logger.current().fail(env.region, instance_id, error, logged=True)
    state, _ = instance_state_and_dot_false_volumes(client, instance_id)
    if state in {None, "shutting-down", "terminated"}:
        logging.info(
            "Instance %s state=%s after ambiguous terminate error; keeping leftover markers",
            instance_id,
            state or "not-found",
        )
        if state == "shutting-down" and instance_id not in env.state.instance_ids_to_delete:
            env.state.instance_ids_to_delete.append(instance_id)
            logger.current().action("deleted", env.region, instance_id)
        return True
    logging.error(
        "Instance %s state=%s after ambiguous terminate error; invalidating leftover markers",
        instance_id,
        state,
    )
    return False


def _terminate_call(
    env: CleanupEnv, client: SpotClient, match: RequestMatch, instance_id: str
) -> bool:
    try:
        client.terminate_instances(InstanceIds=[instance_id])
        env.state.instance_ids_to_delete.append(instance_id)
        logger.current().action("deleted", env.region, instance_id)
        logging.info(
            "Terminated instance %s for cancelled spot request %s",
            instance_id,
            match.request_id,
        )
        return True
    except ClientError as e:
        code = (e.response.get("Error") or {}).get("Code")
        if code in NOT_FOUND_INSTANCE:
            logging.info(
                "Instance %s already gone for spot request %s", instance_id, match.request_id
            )
            return True
        logging.error(
            "Failed to terminate instance %s after cancelling %s: %s",
            instance_id,
            match.request_id,
            e,
        )
        logger.current().fail(env.region, instance_id, e, logged=True)
        return False
    except Exception as e:  # noqa: BLE001 - SAF-033 ambiguous failure re-checks the instance state and keeps it unless proven gone
        return _terminate_ambiguous(env, client, match, instance_id, e)


def _terminate(
    env: CleanupEnv, client: SpotClient, match: RequestMatch, inspection: Inspection, tagged: Scope
) -> bool:
    """Terminate the instance; False when it failed (markers are invalidated first)."""
    instance_id = match.instance_id
    assert instance_id is not None
    if inspection.instance_done:
        logging.info(
            "Instance %s already %s for spot request %s; only leftover volumes will be deleted",
            instance_id,
            inspection.instance_state or "missing",
            match.request_id,
        )
        return True
    if _terminate_call(env, client, match, instance_id):
        return True
    if tagged:
        invalidate_leftover_marker(client, inspection.leftover_volumes, tagged)
    return False


def _delete_leftover_volumes(env: CleanupEnv, client: SpotClient, volumes: list[str]) -> bool:
    deleted_any = False
    for volume_id in filter_deletable_leftover_volumes(env.settings, client, volumes, env.region):
        result = wait_volume_available_then_delete(
            env.settings, env.waiter, client, volume_id, env.region
        )
        if result == "deleted":
            env.state.volumes_to_delete.append(volume_id)
            logger.current().action("deleted", env.region, volume_id)
            deleted_any = True
        # already_gone / timeout: tagged volumes remain findable by the sweep
    return deleted_any


def cleanup_one(env: CleanupEnv, client: SpotClient, match: RequestMatch) -> None:
    """Cancel first, then terminate, then delete the DeleteOnTermination=false volumes."""
    inspection = inspect_instance(client, match, env.region)
    if inspection is None:
        return
    # Tag BDM volumes before cancel so a CreateTags failure leaves the request untouched.
    tagged: Scope = {}
    if match.instance_id and inspection.leftover_volumes and inspection.volumes_from_bdm:
        tagged_or_none = tag_volumes_as_leftover(
            env.settings, client, match.request_id, inspection.leftover_volumes, match.tags
        )
        if tagged_or_none is None:
            logging.error(
                "Not cancelling/terminating spot request %s — failed to tag leftover volumes on %s",
                match.request_id,
                match.instance_id,
            )
            logger.current().fail(
                env.region, match.request_id, "failed to tag leftover volumes", logged=True
            )
            return
        tagged = tagged_or_none
    if not _cancel(env, client, match, inspection, tagged):
        return
    if not match.instance_id:
        return
    if not _terminate(env, client, match, inspection, tagged):
        return
    deleted_any = _delete_leftover_volumes(env, client, inspection.leftover_volumes)
    # Count a retried (already cancelled) request only when this run did real work.
    if not match.already_cancelled:
        return
    if deleted_any or (match.instance_id in env.state.instance_ids_to_delete):
        env.state.spot_requests_to_delete.append(match.request_id)
        logger.current().action("deleted", env.region, match.request_id)


def sweep_region(env: CleanupEnv, client: SpotClient, now: datetime.datetime) -> None:
    """Delete available volumes tagged crc_leftover_of whose gates still allow it."""

    def act(volume_id: str) -> None:
        result = wait_volume_available_then_delete(
            env.settings, env.waiter, client, volume_id, env.region
        )
        if result == "deleted" and volume_id not in env.state.volumes_to_delete:
            env.state.volumes_to_delete.append(volume_id)
            logger.current().action("deleted", env.region, volume_id)

    run_sweep(env.settings, client, env.region, now, act)
