# Copyright (c) Yugabyte, Inc.

"""OCI preemptible instance cleaner (`crc.oci.spot_vm`): the old import path keeps working."""

import oci as oci

from crc.oci._base import Base as Base
from crc.oci.spot_vm.adapter import SpotVM, SpotVmRuntime
from crc.oci.spot_vm.cleaner import SpotVmCleaner, build_spot_vm_cleaner

__all__ = ["SpotVM", "SpotVmCleaner", "SpotVmRuntime", "build_spot_vm_cleaner"]
