# Copyright (c) Yugabyte, Inc.

"""Closing log lines of a spot instance request run."""

import logging

from crc.aws.spot_instance_requests.model import SpotState


def log_summary(state: SpotState, *, dry_run: bool) -> None:
    """The closing log lines of main (warnings)."""
    if not state.spot_requests_to_delete:
        logging.warning("No SpotInstanceRequest to delete.")
    label = "which will be deleted" if dry_run else "deleted"
    logging.warning(
        "List of AWS SpotInstanceRequest (Total: %s) %s: %s",
        len(state.spot_requests_to_delete),
        label,
        state.spot_requests_to_delete,
    )
    logging.warning(
        "List of AWS SpotInstanceRequest Instances (Total: %s) %s: %s",
        len(state.instance_ids_to_delete),
        label,
        state.instance_ids_to_delete,
    )
    logging.warning(
        "List of AWS leftover EBS volumes (DeleteOnTermination=false) (Total: %s) %s: %s",
        len(state.volumes_to_delete),
        label,
        state.volumes_to_delete,
    )
