"""Names shared by the CRC class and `crc.py`: supported clouds, resources and report labels."""

from crc.core.cleaner import Operation

CLOUDS = ["aws", "azure", "gcp", "oci"]
RESOURCES = ["disk", "ip", "keypair", "vm", "kms", "nic", "snapshot"]

DELETED = "Deleted"
STOPPED = "Stopped"

NICS = "NIC"
DISKS = "Disk"
VMS = "VM"
IPS = "IP"
KEYPAIRS = "Keypair"
KMS = "KMS"
SPOT_INSTANCE_REQUEST = "SPOT_INSTANCE_REQUEST_AND_ASSOCIATED_INSTANCES"

OPERATIONS = {DELETED: Operation.DELETE, STOPPED: Operation.STOP}
