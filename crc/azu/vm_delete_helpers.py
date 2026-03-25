# Copyright (c) Yugabyte, Inc.

"""
Helpers for Azure VM delete + associated NIC cleanup without long fixed sleeps.
"""

import logging
import time
from typing import List

from azure.core.exceptions import HttpResponseError, ResourceNotFoundError


# Max time to wait for a single VM delete LRO (large disks can be slow)
DEFAULT_VM_DELETE_TIMEOUT_SEC = 3600

# NIC delete: retry with short intervals after VM is gone (Azure may lag detaching)
NIC_DELETE_ATTEMPTS = 24
NIC_DELETE_RETRY_INTERVAL_SEC = 15


def begin_delete_vm_and_wait(
    compute_client,
    resource_group: str,
    vm_name: str,
    timeout_sec: int = DEFAULT_VM_DELETE_TIMEOUT_SEC,
) -> None:
    """
    Start VM delete and block until the long-running operation completes.
    """
    poller = compute_client.virtual_machines.begin_delete(resource_group, vm_name)
    poller.result(timeout=timeout_sec)
    logging.info("VM delete completed: %s", vm_name)


def delete_vm_primary_nic(
    network_client,
    resource_group: str,
    vm_name: str,
    dry_run: bool,
    nics_names_to_delete: List[str],
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
        try:
            poller = network_client.network_interfaces.begin_delete(
                resource_group, nic_name
            )
            poller.result(timeout=600)
            logging.info("Deleted the NIC - %s", nic_name)
            nics_names_to_delete.append(nic_name)
            return
        except ResourceNotFoundError:
            logging.info("NIC %s already removed (or never existed)", nic_name)
            nics_names_to_delete.append(nic_name)
            return
        except HttpResponseError as e:
            if e.status_code == 404:
                logging.info("NIC %s not found (404)", nic_name)
                nics_names_to_delete.append(nic_name)
                return
            logging.warning(
                "NIC delete attempt %s/%s for %s: %s",
                attempt,
                NIC_DELETE_ATTEMPTS,
                nic_name,
                e,
            )
        except Exception as e:
            logging.warning(
                "NIC delete attempt %s/%s for %s: %s",
                attempt,
                NIC_DELETE_ATTEMPTS,
                nic_name,
                e,
            )

        if attempt < NIC_DELETE_ATTEMPTS:
            time.sleep(NIC_DELETE_RETRY_INTERVAL_SEC)

    logging.error("Failed to delete the NIC - %s after %s attempts", nic_name, NIC_DELETE_ATTEMPTS)
