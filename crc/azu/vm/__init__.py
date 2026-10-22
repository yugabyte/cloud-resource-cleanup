# Copyright (c) Yugabyte, Inc.

"""Azure VM cleaner package; `crc.azu.vm.VM` stays the import path `crc.py` uses."""

from crc.azu.vm.adapter import VM
from crc.azu.vm.cleaner import (
    DEFAULT_INSTANCE_STATES,
    Tally,
    VmCleaner,
    VmSettings,
    build_vm_cleaner,
    log_summary,
)
from crc.azu.vm.client import (
    ComputeFactory,
    InstanceStatus,
    InstanceView,
    LongRunningOperation,
    NetworkFactory,
    NicOperations,
    SdkVm,
    VmComputeClient,
    VmNetworkClient,
    VmOperations,
)

__all__ = [
    "DEFAULT_INSTANCE_STATES",
    "VM",
    "ComputeFactory",
    "InstanceStatus",
    "InstanceView",
    "LongRunningOperation",
    "NetworkFactory",
    "NicOperations",
    "SdkVm",
    "Tally",
    "VmCleaner",
    "VmComputeClient",
    "VmNetworkClient",
    "VmOperations",
    "VmSettings",
    "build_vm_cleaner",
    "log_summary",
]
