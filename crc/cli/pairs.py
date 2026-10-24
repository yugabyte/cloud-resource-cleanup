"""Which (cloud, resource) pairs a run supports, and the error for one that is not."""

from typing import Any

from crc.cli.constants import CLOUDS
from crc.core.kinds import AWS_ONLY_MESSAGE, KINDS, KINDS_BY_NAME

SUPPORTED_CLOUDS: dict[str, tuple[str, ...]] = {
    kind.name: kind.clouds for kind in KINDS if kind.clouds is not None
}

AWS_ONLY_RESOURCES = tuple(kind.name for kind in KINDS if kind.unsupported == AWS_ONLY_MESSAGE)

AWS_DISK_OPT_IN_MESSAGE = (
    "AWS EBS volume cleanup requires '--cloud aws --resource disk'. "
    "Refusing to continue so --cloud all / --resource all does not "
    "skip EBS and then delete other AWS resources, and so "
    "--cloud all --resource disk does not reach other clouds' disks."
)


def is_supported_pair(cloud: Any, resource: Any) -> bool:
    """False for a (cloud, resource) pair the cloud does not support."""
    allowed = SUPPORTED_CLOUDS.get(resource)
    return allowed is None or cloud in allowed


def _unsupported_message(cloud: Any, resource: Any) -> str:
    return KINDS_BY_NAME[resource].unsupported_message(cloud, CLOUDS)
