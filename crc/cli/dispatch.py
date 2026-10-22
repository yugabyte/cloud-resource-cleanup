"""The cloud and resource loops of a cleanup run."""

import logging
from collections.abc import Callable
from typing import Any

from crc.aws.snapshot import Snapshot
from crc.cli.context import RunContext, make_crc
from crc.cli.inputs import Options, Targets
from crc.core import logger

_LOG = logging.getLogger(__name__)

Handler = Callable[[Any, Any, Options, Targets], None]

AWS_DISK_OPT_IN_MESSAGE = (
    "AWS EBS volume cleanup requires '--cloud aws --resource disk'. "
    "Refusing to continue so --cloud all / --resource all does not "
    "skip EBS and then delete other AWS resources, and so "
    "--cloud all --resource disk does not reach other clouds' disks."
)


def _disk(crc: Any, cloud: Any, options: Options, targets: Targets) -> None:
    """Circuit breaker: AWS EBS is opt-in, so any other aws and disk pair raises."""
    if cloud == "aws" and not targets.aws_disk_opt_in:
        raise ValueError(AWS_DISK_OPT_IN_MESSAGE)
    crc.delete_disks(
        options.filter_tags,
        options.exception_tags,
        options.age,
        options.custom_age_tag_key,
        options.detach_age,
        options.name_regex,
        options.exception_regex,
        options.slack_notify_users,
        options.slack_user_label,
    )


def _ip(crc: Any, cloud: Any, options: Options, targets: Targets) -> None:
    crc.delete_ip(
        options.filter_tags,
        options.exception_tags,
        options.name_regex,
        options.exception_regex,
    )


def _keypair(crc: Any, cloud: Any, options: Options, targets: Targets) -> None:
    crc.delete_keypairs(options.name_regex, options.exception_regex, options.age)


def _nic(crc: Any, cloud: Any, options: Options, targets: Targets) -> None:
    crc.delete_nic(options.name_regex, options.exception_regex)


def _vm(crc: Any, cloud: Any, options: Options, targets: Targets) -> None:
    if options.operation_type == "delete":
        crc.delete_vm(
            options.filter_tags,
            options.exception_tags,
            options.age,
            options.custom_age_tag_key,
            options.resource_states,
        )
    elif options.operation_type == "stop":
        crc.stop_vm(
            options.filter_tags,
            options.exception_tags,
            options.age,
            options.custom_age_tag_key,
        )


def _vpc(crc: Any, cloud: Any, options: Options, targets: Targets) -> None:
    crc.delete_vpc(options.filter_tags, options.exception_tags)


def _kms(crc: Any, cloud: Any, options: Options, targets: Targets) -> None:
    crc.delete_kms(
        options.filter_tags,
        options.exception_tags,
        options.kms_key_description,
        options.kms_user,
        options.kms_pending_window,
        options.age,
        options.custom_age_tag_key,
    )


def _spot_instance_requests(crc: Any, cloud: Any, options: Options, targets: Targets) -> None:
    if crc.cloud != "aws":
        _LOG.warning(
            "spot_instance_requests is AWS-only; skipped for %s (use --resource spot_instances)",
            crc.cloud,
        )
        return
    crc.delete_spot_instance_requests(
        options.filter_tags, options.exception_tags, options.age, options.custom_age_tag_key
    )


def _spot_instances(crc: Any, cloud: Any, options: Options, targets: Targets) -> None:
    if crc.cloud == "aws":
        _LOG.warning(
            "For AWS use --resource spot_instance_requests "
            "(cancel request before terminate). Skipping spot_instances."
        )
        return
    if crc.cloud in ("gcp", "oci") and targets.cloud_arg == "all":
        _LOG.warning(
            "GCP/OCI spot_instances require explicit --cloud %s "
            "(not --cloud all); skipped. Azure spot_instances still runs "
            "under --cloud all for backward compatibility.",
            crc.cloud,
        )
        return
    # Fail closed: missing --resource_group / --project_id / --age must raise
    # (do not continue into other clouds under --cloud all).
    crc.delete_spot_instances(
        options.filter_tags, options.exception_tags, options.age, options.custom_age_tag_key
    )


def _snapshot(crc: Any, cloud: Any, options: Options, targets: Targets) -> None:
    dry_run = options.dry_run
    snapshot = Snapshot(dry_run=dry_run, age_days=90, exception_tags=options.exception_tags)
    snapshot.delete()
    if dry_run:
        msg = (
            f"DRY RUN: AWS Snapshots eligible for deletion (>{snapshot.age_days} days): "
            f"{snapshot.delete_count}"
        )
    else:
        msg = crc.get_msg("Snapshot", "Deleted", snapshot.get_deleted)
    print(msg)


HANDLERS: dict[str, Handler] = {
    "disk": _disk,
    "ip": _ip,
    "keypair": _keypair,
    "nic": _nic,
    "vm": _vm,
    "vpc": _vpc,
    "kms": _kms,
    "spot_instance_requests": _spot_instance_requests,
    "spot_instances": _spot_instances,
    "snapshot": _snapshot,
}


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


def _run_handler(
    handler: Handler, crc: Any, cloud: Any, resource: Any, options: Options, targets: Targets
) -> None:
    """Run one cloud and resource pass between its RUN START and RESULT lines."""
    logger.begin(
        str(cloud),
        str(resource),
        str(options.operation_type or "delete"),
        bool(options.dry_run),
        _run_options(options),
    )
    try:
        handler(crc, cloud, options, targets)
    finally:
        logger.finish()


def run_dispatch(context: RunContext, options: Options, targets: Targets) -> None:
    """Run every selected resource on every selected cloud, in the order of `main`."""
    for cloud in targets.clouds:
        crc = make_crc(context, cloud)
        for resource in targets.resources:
            handler = HANDLERS.get(resource)
            if handler is not None:
                _run_handler(handler, crc, cloud, resource, options, targets)
