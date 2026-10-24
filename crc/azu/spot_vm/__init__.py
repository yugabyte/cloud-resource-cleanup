# Copyright (c) Yugabyte, Inc.

"""Azure Spot VM cleaner (`AzureSpotVmCleaner`, registered as `azure`/`spot_instances`)."""

from crc.azu.spot_vm.cleaner import (
    STOPPED_POWER_STATES,
    AzureSpotVmCleaner,
    SpotVmConfig,
    build_spot_vm_cleaner,
    log_summary,
)
from crc.azu.spot_vm.client import (
    ComputeFactory,
    DiskOperations,
    NetworkFactory,
    Poller,
    SpotComputeClient,
    SpotNetworkClient,
    VirtualMachineOperations,
)
from crc.azu.spot_vm.disks import (
    DiskRef,
    DiskSlot,
    detached_disk_refs,
    disk_name_from_id,
    disk_ref_from_id,
    disk_slots,
)
from crc.azu.spot_vm.removal import RemovalOps, Removed, Remover

__all__ = [
    "STOPPED_POWER_STATES",
    "AzureSpotVmCleaner",
    "ComputeFactory",
    "DiskOperations",
    "DiskRef",
    "DiskSlot",
    "NetworkFactory",
    "Poller",
    "RemovalOps",
    "Removed",
    "Remover",
    "SpotComputeClient",
    "SpotNetworkClient",
    "SpotVmConfig",
    "VirtualMachineOperations",
    "build_spot_vm_cleaner",
    "detached_disk_refs",
    "disk_name_from_id",
    "disk_ref_from_id",
    "disk_slots",
    "log_summary",
]
