# Copyright (c) Yugabyte, Inc.

# Entry point of the CRC command line: `python crc.py <flags>`. main() and the CRC class live in
# crc/cli. The names below are re-exported so that code that loads this file and looks them up
# here (the tests, `from crc import ...` style lookups) keeps working.

from influxdb_client import InfluxDBClient, Point
from slack_sdk import WebClient

from crc.aws.disk import Disk as AWS_Disk
from crc.aws.elastic_ips import ElasticIPs
from crc.aws.keypairs import KeyPairs
from crc.aws.kms import Kms
from crc.aws.snapshot import Snapshot
from crc.aws.spot_instance_requests import SpotInstanceRequests
from crc.aws.vm import VM as AWS_VM
from crc.aws.vpc import VPC
from crc.azu.disk import Disk
from crc.azu.ip import IP as AZU_IP
from crc.azu.nic import NIC
from crc.azu.spot_vm import SpotVM as AZU_SpotVM
from crc.azu.vm import VM as AZU_VM
from crc.cli.args import get_argparser
from crc.cli.constants import (
    CLOUDS,
    DELETED,
    DISKS,
    IPS,
    KEYPAIRS,
    KMS,
    NICS,
    RESOURCES,
    SPOT_INSTANCE_REQUEST,
    STOPPED,
    VMS,
)
from crc.cli.main import main
from crc.cli.runner import CRC
from crc.cli.validate import (
    _validate_influxdb_input,
    are_values_of_dict_lists,
    is_valid_dict,
    is_valid_list,
    is_valid_type,
    validate_influxdb_inputs,
)
from crc.gcp.disk import Disk as GCP_Disk
from crc.gcp.ip import IP as GCP_IP
from crc.gcp.spot_vm import SpotVM as GCP_SpotVM
from crc.gcp.vm import VM as GCP_VM
from crc.notifications import notify_cleanup
from crc.oci.disk import Disk as OCI_Disk
from crc.oci.spot_vm import SpotVM as OCI_SpotVM
from crc.oci.vm import VM as OCI_VM

__all__ = [
    "AWS_VM",
    "AZU_IP",
    "AZU_VM",
    "CLOUDS",
    "CRC",
    "DELETED",
    "DISKS",
    "GCP_IP",
    "GCP_VM",
    "IPS",
    "KEYPAIRS",
    "KMS",
    "NIC",
    "NICS",
    "OCI_VM",
    "RESOURCES",
    "SPOT_INSTANCE_REQUEST",
    "STOPPED",
    "VMS",
    "VPC",
    "AWS_Disk",
    "AZU_SpotVM",
    "Disk",
    "ElasticIPs",
    "GCP_Disk",
    "GCP_SpotVM",
    "InfluxDBClient",
    "KeyPairs",
    "Kms",
    "OCI_Disk",
    "OCI_SpotVM",
    "Point",
    "Snapshot",
    "SpotInstanceRequests",
    "WebClient",
    "_validate_influxdb_input",
    "are_values_of_dict_lists",
    "get_argparser",
    "is_valid_dict",
    "is_valid_list",
    "is_valid_type",
    "main",
    "notify_cleanup",
    "validate_influxdb_inputs",
]

if __name__ == "__main__":
    main()
