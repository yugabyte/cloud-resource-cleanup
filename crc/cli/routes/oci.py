"""OCI route table: `disk`, `vm` (delete and stop) and `spot_instances` run through the engine.

`spot_instances` skips the pair under `--cloud all` (SAF-037).
"""

from typing import Any

import crc.oci.disk
import crc.oci.spot_vm
import crc.oci.vm  # noqa: F401
from crc.cli.inputs import Options
from crc.cli.routes import NotifyMode, Route, skips


def _disk(options: Options) -> dict[str, Any]:
    return {
        "filter_tags": options.filter_tags,
        "exception_tags": options.exception_tags,
        "notags": options.notags,
        "age": options.age,
        "custom_age_tag_key": options.custom_age_tag_key,
    }


_spot_instances = _disk


def _vm(options: Options) -> dict[str, Any]:
    """The legacy handler passed the states to delete only (stop used the cleaner default)."""
    values: dict[str, Any] = {
        "filter_tags": options.filter_tags,
        "exception_tags": options.exception_tags,
        "notags": options.notags,
        "age": options.age,
        "custom_age_tag_key": options.custom_age_tag_key,
    }
    if options.operation_type != "stop" and options.resource_states:
        values["instance_state"] = options.resource_states
    return values


ROUTES: dict[str, Route] = {
    "spot_instance_requests": Route(skips.no_options, skip=skips.non_aws_spot_instance_requests),
    "disk": Route(_disk, notify=NotifyMode.REPORT_THEN_RERAISE),
    "vm": Route(_vm),
    "spot_instances": Route(_spot_instances, skip=skips.explicit_cloud_spot),
}
