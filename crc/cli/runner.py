"""The `CRC` class: the operation mixins composed into one object, as `crc.py` had it."""

from crc.cli.resource_ops import ResourceOps
from crc.cli.vm_ops import VmOps


class CRC(VmOps, ResourceOps):
    """
    Class for cleaning up resources across different clouds.
    This also supports sending notification to Slack Channels
    """
