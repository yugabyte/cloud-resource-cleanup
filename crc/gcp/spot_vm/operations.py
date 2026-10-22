# Copyright (c) Yugabyte, Inc.

"""Waiting for a Compute Engine zone or region operation to reach DONE."""

import logging
import time
from collections.abc import Mapping
from typing import Any

from crc.gcp.spot_vm.client import GcpSpotClient
from crc.gcp.spot_vm.settings import SpotSettings


def _finished_ok(
    operation: Mapping[str, Any], scope: str, project: str, location: str, name: str
) -> bool:
    if operation.get("error"):
        logging.error(
            "GCP %s operation %s/%s/%s failed: %s",
            scope,
            project,
            location,
            name,
            operation.get("error"),
        )
        return False
    return True


def wait_operation(
    client: GcpSpotClient,
    settings: SpotSettings,
    scope: str,
    location: str,
    operation_name: str,
    project: str | None = None,
) -> bool:
    """Poll until the operation is DONE. False on a poll error, an operation error or after
    `settings.wait_sec` (every case is logged)."""
    target = project or settings.project_id
    deadline = time.monotonic() + settings.wait_sec
    while time.monotonic() < deadline:
        try:
            operation = client.get_operation(target, scope, location, operation_name)
        except Exception as e:
            logging.error(
                "Error polling GCP %s operation %s/%s/%s: %s",
                scope,
                target,
                location,
                operation_name,
                e,
            )
            return False
        status = operation.get("status")
        if status == "DONE":
            return _finished_ok(operation, scope, target, location, operation_name)
        logging.info(
            "Waiting for GCP %s operation %s/%s/%s (status=%s)",
            scope,
            target,
            location,
            operation_name,
            status,
        )
        time.sleep(settings.poll_sec)
    logging.error(
        "Timed out waiting for GCP %s operation %s/%s/%s",
        scope,
        target,
        location,
        operation_name,
    )
    return False
