# Copyright (c) Yugabyte, Inc.

"""
Helpers for Azure VM delete + associated NIC cleanup without long fixed sleeps.
"""

import logging
import time
from typing import Any, Protocol

from azure.core.exceptions import HttpResponseError, ResourceNotFoundError

from crc.azu import _logger

# Max time to wait for a single VM delete LRO (large disks can be slow)
DEFAULT_VM_DELETE_TIMEOUT_SEC = 3600

# NIC delete: retry with short intervals after VM is gone (Azure may lag detaching)
NIC_DELETE_ATTEMPTS = 24
NIC_DELETE_RETRY_INTERVAL_SEC = 15

NIC_DELETE_TIMEOUT_SEC = 600
HTTP_NOT_FOUND = 404


class DeletePoller(Protocol):
    """The long-running-operation handle `begin_delete` returns.

    The clients themselves are `Any`: the VM, Spot VM and cleaner callers each pass their own
    narrow client Protocol, and none of them declares both delete calls.
    """

    def result(self, timeout: int) -> object:
        """Wait for the operation to finish."""
        ...


def begin_delete_vm_and_wait(
    compute_client: Any,
    resource_group: str,
    vm_name: str,
    timeout_sec: int = DEFAULT_VM_DELETE_TIMEOUT_SEC,
) -> None:
    """
    Start VM delete and block until the long-running operation completes.
    """
    poller: DeletePoller = compute_client.virtual_machines.begin_delete(resource_group, vm_name)
    poller.result(timeout=timeout_sec)
    logging.info("VM delete completed: %s", vm_name)


def _log_attempt_failed(attempt: int, nic_name: str, error: Exception) -> None:
    logging.warning(
        "NIC delete attempt %s/%s for %s: %s",
        attempt,
        NIC_DELETE_ATTEMPTS,
        nic_name,
        error,
    )


def _try_delete_nic(
    network_client: Any,
    resource_group: str,
    nic_name: str,
    attempt: int,
    nics_names_to_delete: list[str],
) -> bool:
    """Run one delete attempt; True when the NIC is recorded and no retry is needed."""
    try:
        poller: DeletePoller = network_client.network_interfaces.begin_delete(
            resource_group, nic_name
        )
        poller.result(timeout=NIC_DELETE_TIMEOUT_SEC)
        logging.info("Deleted the NIC - %s", nic_name)
        nics_names_to_delete.append(nic_name)
        return True
    except ResourceNotFoundError:
        logging.info("NIC %s already removed (or never existed)", nic_name)
        nics_names_to_delete.append(nic_name)
        return True
    except HttpResponseError as e:
        if e.status_code == HTTP_NOT_FOUND:
            logging.info("NIC %s not found (404)", nic_name)
            nics_names_to_delete.append(nic_name)
            return True
        _log_attempt_failed(attempt, nic_name, e)
    except Exception as e:  # noqa: BLE001 - per-attempt isolation, NIC delete retried then reported
        _log_attempt_failed(attempt, nic_name, e)
    return False


def delete_vm_primary_nic(
    network_client: Any,
    resource_group: str,
    vm_name: str,
    dry_run: bool,
    nics_names_to_delete: list[str],
    nic_name_suffix: str = "-NIC",
) -> None:
    """
    Delete the NIC named ``{vm_name}{nic_name_suffix}`` (default ``vm-NIC``).
    Retries briefly if Azure has not finished detaching yet.
    """
    nic_name = f"{vm_name}{nic_name_suffix}"
    if dry_run:
        nics_names_to_delete.append(nic_name)
        return

    for attempt in range(1, NIC_DELETE_ATTEMPTS + 1):
        if _try_delete_nic(network_client, resource_group, nic_name, attempt, nics_names_to_delete):
            return
        if attempt < NIC_DELETE_ATTEMPTS:
            time.sleep(NIC_DELETE_RETRY_INTERVAL_SEC)

    logging.error("Failed to delete the NIC - %s after %s attempts", nic_name, NIC_DELETE_ATTEMPTS)
    _logger.failed(
        resource_group, nic_name, f"NIC delete failed after {NIC_DELETE_ATTEMPTS} attempts"
    )
