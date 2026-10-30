# Copyright (c) Yugabyte, Inc.

"""Summary and failure logging of the OCI preemptible instance cleaner, and the dry-run records."""

import logging

from crc.core.cleaner import Operation, Plan
from crc.oci import _logger

VOLUMES_LABEL = "Disk"


def log_summary(names: list[str], volumes: list[str], *, dry_run: bool) -> None:
    """The closing lines of main. The lists are logged as they are (main logs its own lists)."""
    if not names:
        logging.warning("No OCI preemptible instances to delete.")
    label = "which will be deleted" if dry_run else "deleted"
    logging.warning(
        "List of OCI preemptible instances (Total: %s) %s: %s", len(names), label, names
    )
    logging.warning(
        "List of OCI leftover block volumes (Total: %s) %s: %s", len(volumes), label, volumes
    )


def log_instance_error(name: object, error: Exception) -> None:
    logging.error("Error processing OCI preemptible instance %s: %s", name, error)


def record_dry_run(plan: Plan) -> None:
    """One dry-run action per planned instance, each followed by its leftover volumes."""
    for candidate in plan.candidates:
        _logger.record_action(
            Operation.DELETE,
            dry_run=True,
            region=candidate.region,
            resource_id=candidate.id,
            name=candidate.name,
        )
        for volume_id in candidate.extra["volumes"]:
            _logger.record_action(
                Operation.DELETE,
                dry_run=True,
                region=candidate.region,
                resource_id=volume_id,
                name=None,
            )
