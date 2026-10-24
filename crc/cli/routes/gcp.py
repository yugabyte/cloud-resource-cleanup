"""GCP route table: ip, disk, vm and spot_instances run through `crc.core.engine`.

`spot_instances` skips the pair under `--cloud all` (SAF-037) and needs a project.
"""

from typing import Any

import crc.gcp.disk_cleaner
import crc.gcp.ip_cleaner
import crc.gcp.spot_vm
import crc.gcp.vm_cleaner  # noqa: F401
from crc.cli.inputs import Options, Targets
from crc.cli.routes import NotifyMode, Route, skips


def _ip(options: Options) -> dict[str, Any]:
    """`age` is passed only when the flag was given (BUG-062; a dry run runs without it)."""
    values: dict[str, Any] = {
        "project_id": options.project_id,
        "name_regex": options.name_regex,
        "exception_regex": options.exception_regex,
    }
    if options.age is not None:
        values["age"] = options.age
    return values


def _disk(options: Options) -> dict[str, Any]:
    return {
        "project_id": options.project_id,
        "filter_tags": options.filter_tags,
        "exception_tags": options.exception_tags,
        "notags": options.notags,
        "name_regex": options.name_regex,
        "exception_regex": options.exception_regex,
        "custom_age_tag_key": options.custom_age_tag_key,
        "age": options.age,
        "detach_age": options.detach_age,
        "slack_notify_users": options.slack_notify_users,
        "slack_user_label": options.slack_user_label,
    }


def _vm(options: Options) -> dict[str, Any]:
    """The legacy handler passed the states to delete only (stop used the cleaner default)."""
    values: dict[str, Any] = {
        "project_id": options.project_id,
        "filter_tags": options.filter_tags,
        "exception_tags": options.exception_tags,
        "notags": options.notags,
        "age": options.age,
        "custom_age_tag_key": options.custom_age_tag_key,
    }
    if options.operation_type != "stop" and options.resource_states:
        values["instance_state"] = options.resource_states
    return values


def _spot(options: Options) -> dict[str, Any]:
    return {
        "project_id": options.project_id,
        "filter_tags": options.filter_tags,
        "exception_tags": options.exception_tags,
        "notags": options.notags,
        "age": options.age,
        "custom_age_tag_key": options.custom_age_tag_key,
    }


def _spot_project(crc: Any, options: Options, targets: Targets) -> None:
    """An empty project raises before the Spot cleaner is built."""
    if not options.project_id:
        raise ValueError("project_id is required for GCP Spot VM cleanup")


ROUTES: dict[str, Route] = {
    "spot_instance_requests": Route(skips.no_options, skip=skips.non_aws_spot_instance_requests),
    "ip": Route(_ip),
    "disk": Route(_disk, notify=NotifyMode.REPORT_THEN_RERAISE),
    "vm": Route(_vm),
    "spot_instances": Route(_spot, skip=skips.explicit_cloud_spot, precheck=_spot_project),
}
