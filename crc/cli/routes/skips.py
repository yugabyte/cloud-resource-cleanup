"""Skips of the routes that warn and run nothing, as the legacy spot handlers did."""

import logging
from typing import Any

from crc.cli.inputs import Options, Targets

_LOG = logging.getLogger("crc.cli.dispatch")


def explicit_cloud_spot(cloud: str, options: Options, targets: Targets) -> bool:
    """SAF-037: GCP and OCI `spot_instances` need an explicit `--cloud`; skip under `all`."""
    if targets.cloud_arg != "all":
        return False
    _LOG.warning(
        "GCP/OCI spot_instances require explicit --cloud %s "
        "(not --cloud all); skipped. Azure spot_instances still runs "
        "under --cloud all for backward compatibility.",
        cloud,
    )
    return True


def aws_spot_instances(cloud: str, options: Options, targets: Targets) -> bool:
    """AWS has no `spot_instances` kind: warn and run nothing (cancel the requests first)."""
    _LOG.warning(
        "For AWS use --resource spot_instance_requests "
        "(cancel request before terminate). Skipping spot_instances."
    )
    return True


def non_aws_spot_instance_requests(cloud: str, options: Options, targets: Targets) -> bool:
    """`spot_instance_requests` is an AWS kind: warn and run nothing on every other cloud."""
    _LOG.warning(
        "spot_instance_requests is AWS-only; skipped for %s (use --resource spot_instances)",
        cloud,
    )
    return True


def no_options(options: Options) -> dict[str, Any]:
    """A skip-only route never reaches a cleaner, so it passes no options."""
    return {}
