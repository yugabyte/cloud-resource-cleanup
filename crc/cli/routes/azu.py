"""Azure route table: ip, nic, disk, vm and spot_instances run through `crc.core.engine`."""

from typing import Any

import crc.azu.disk
import crc.azu.ip
import crc.azu.nic
import crc.azu.spot_vm
import crc.azu.vm  # noqa: F401
from crc.cli.inputs import Options, Targets
from crc.cli.routes import NotifyMode, Route, skips

SPOT_NEEDS_GROUP = "resource_group is required for Azure Spot VM cleanup"


def _with_age(values: dict[str, Any], options: Options) -> dict[str, Any]:
    """Pass `--age` on when given (a real run of ip and nic requires it, a dry run does not)."""
    if options.age is not None:
        values["age"] = options.age
    return values


def _ip(options: Options) -> dict[str, Any]:
    return _with_age(
        {
            "resource_group": options.resource_group,
            "filter_tags": options.filter_tags,
            "exception_tags": options.exception_tags,
            "notags": options.notags,
        },
        options,
    )


def _nic(options: Options) -> dict[str, Any]:
    return _with_age(
        {
            "resource_group": options.resource_group,
            "name_regex": options.name_regex,
            "exception_regex": options.exception_regex,
        },
        options,
    )


def _disk(options: Options) -> dict[str, Any]:
    return {
        "resource_group": options.resource_group,
        "filter_tags": options.filter_tags,
        "exception_tags": options.exception_tags,
        "notags": options.notags,
        "age": options.age,
        "custom_age_tag_key": options.custom_age_tag_key,
    }


def _vm(options: Options) -> dict[str, Any]:
    """The legacy handler passed the states to delete only (stop used the cleaner default)."""
    values = _disk(options)
    if options.operation_type != "stop" and options.resource_states:
        values["instance_state"] = options.resource_states
    return values


def _spot_group(crc: Any, options: Options, targets: Targets) -> None:
    """An empty resource group raises before the Spot cleaner is built."""
    if not options.resource_group:
        raise ValueError(SPOT_NEEDS_GROUP)


ROUTES: dict[str, Route] = {
    "spot_instance_requests": Route(skips.no_options, skip=skips.non_aws_spot_instance_requests),
    "ip": Route(_ip),
    "nic": Route(_nic),
    "disk": Route(_disk, notify=NotifyMode.REPORT_THEN_RERAISE),
    "vm": Route(_vm),
    "spot_instances": Route(_disk, precheck=_spot_group),
}
