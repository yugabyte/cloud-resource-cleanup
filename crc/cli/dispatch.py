"""The cloud and resource loops of a cleanup run."""

import logging
from typing import Any

from crc.cli.bridge import is_routed_kind, route_for, run_routed
from crc.cli.context import RunContext, make_crc
from crc.cli.inputs import Options, Targets
from crc.cli.pairs import (
    AWS_DISK_OPT_IN_MESSAGE as AWS_DISK_OPT_IN_MESSAGE,
)
from crc.cli.pairs import (
    SUPPORTED_CLOUDS as SUPPORTED_CLOUDS,
)
from crc.cli.pairs import (
    _unsupported_message,
)
from crc.cli.pairs import (
    is_supported_pair as is_supported_pair,
)
from crc.core import logger

_LOG = logging.getLogger(__name__)


def _run_options(options: Options) -> dict[str, Any]:
    """The flags `RUN START` reports; `logger.begin` prints the non-empty ones."""
    return {
        "age": options.age,
        "filter_tags": options.filter_tags,
        "exception_tags": options.exception_tags,
        "notags": options.notags,
        "name_regex": options.name_regex,
        "exception_regex": options.exception_regex,
        "custom_age_tag_key": options.custom_age_tag_key,
        "resource_group": options.resource_group,
        "project_id": options.project_id,
    }


def _run_pair(crc: Any, cloud: Any, resource: Any, options: Options, targets: Targets) -> None:
    """Run one cloud and resource pass between its RUN START and RESULT lines."""
    logger.begin(
        str(cloud),
        str(resource),
        str(options.operation_type or "delete"),
        bool(options.dry_run),
        _run_options(options),
    )
    try:
        route = route_for(cloud, resource)
        if route is not None:
            run_routed(crc, cloud, resource, options, targets, route)
    finally:
        logger.finish()


def validate_targets(targets: Targets) -> list[tuple[Any, Any]]:
    """Check every pair of the run before any cleaner runs; return the pairs to skip.

    BUG-018: `--cloud all` with disk or all can never complete (the AWS EBS breaker raises on the
    aws pair), so it raises here, before a cleaner of any cloud ran. BUG-017: an unsupported
    pair of an explicit cloud raises here. Under `--cloud all` the unsupported pairs are returned
    to be skipped only when no supported pair comes after the first one: `main` stopped at that
    pair, so running a later pair (azure for `--cloud all --resource nic`) would delete more.
    """
    handled = [r for r in targets.resources if is_routed_kind(r)]
    if "disk" in handled and targets.cloud_arg == "all" and not targets.aws_disk_opt_in:
        raise ValueError(AWS_DISK_OPT_IN_MESSAGE)
    pairs = [(cloud, resource) for cloud in targets.clouds for resource in handled]
    first = next((i for i, pair in enumerate(pairs) if not is_supported_pair(*pair)), None)
    if first is None:
        return []
    rest = pairs[first:]
    if targets.cloud_arg != "all" or any(is_supported_pair(*pair) for pair in rest):
        raise ValueError(_unsupported_message(*pairs[first]))
    return rest


def _skip_pair(cloud: Any, resource: Any, options: Options) -> None:
    """Log an unsupported pair under `--cloud all` as one skipped run; nothing is called."""
    _LOG.warning("%s is not supported on %s; skipped under --cloud all", resource, cloud)
    logger.begin(
        str(cloud),
        str(resource),
        str(options.operation_type or "delete"),
        bool(options.dry_run),
        _run_options(options),
    )
    logger.current().skip("unsupported_cloud_resource")
    logger.finish()


def run_dispatch(context: RunContext, options: Options, targets: Targets) -> None:
    """Run every selected resource on every selected cloud, in the order of `main`."""
    skipped = validate_targets(targets)
    for cloud in targets.clouds:
        crc: Any = None
        for resource in targets.resources:
            if not is_routed_kind(resource):
                continue
            if (cloud, resource) in skipped:
                _skip_pair(cloud, resource, options)
                continue
            if crc is None:
                crc = make_crc(context, cloud)
            _run_pair(crc, cloud, resource, options, targets)
