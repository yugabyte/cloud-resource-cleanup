# Copyright (c) Yugabyte, Inc.

"""GCP Spot VM cleaner. The old import path `crc.gcp.spot_vm` keeps working: `SpotVM` is the
adapter `crc.py` uses, `SpotVMCleaner` the registered `Cleaner`."""

from google.cloud import compute_v1 as compute_v1
from googleapiclient import discovery as discovery

from crc.gcp.spot_vm.adapter import SpotVM
from crc.gcp.spot_vm.cleaner import SpotVMCleaner, build_spot_cleaner
from crc.gcp.spot_vm.client import ComputeSpotClient, GcpSpotClient, InstanceView
from crc.gcp.spot_vm.settings import SpotSettings

__all__ = [
    "ComputeSpotClient",
    "GcpSpotClient",
    "InstanceView",
    "SpotSettings",
    "SpotVM",
    "SpotVMCleaner",
    "build_spot_cleaner",
]
