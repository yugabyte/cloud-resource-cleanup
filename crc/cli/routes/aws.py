"""AWS route table: the kinds run through `crc.core.engine`. Copy this file's shape per kind."""

from collections.abc import Sequence
from typing import Any

import crc.aws.disk
import crc.aws.elastic_ips
import crc.aws.keypairs
import crc.aws.kms
import crc.aws.snapshot
import crc.aws.spot_instance_requests
import crc.aws.vm  # noqa: F401
from crc.cli.inputs import Options, Targets
from crc.cli.pairs import AWS_DISK_OPT_IN_MESSAGE
from crc.cli.routes import NotifyMode, Route, skips
from crc.core.engine import RunOutcome

SNAPSHOT_AGE_DAYS = 90


def _keypair(options: Options) -> dict[str, Any]:
    return {
        "name_regex": options.name_regex,
        "exception_regex": options.exception_regex,
        "age": options.age,
    }


def _ip(options: Options) -> dict[str, Any]:
    return {
        "filter_tags": options.filter_tags,
        "exception_tags": options.exception_tags,
        "notags": options.notags,
    }


def _kms(options: Options) -> dict[str, Any]:
    return {
        "filter_tags": options.filter_tags,
        "exception_tags": options.exception_tags,
        "kms_key_description": options.kms_key_description,
        "kms_user": options.kms_user,
        "kms_pending_window": options.kms_pending_window,
        "age": options.age,
        "custom_age_tag_key": options.custom_age_tag_key,
    }


def _snapshot(options: Options) -> dict[str, Any]:
    return {"age_days": SNAPSHOT_AGE_DAYS, "exception_tags": options.exception_tags}


def _snapshot_message(crc: Any, options: Options, outcomes: Sequence[RunOutcome]) -> None:
    """The console line the legacy `Snapshot` branch printed (no Slack, no Influx)."""
    outcome = outcomes[0]
    if options.dry_run:
        msg = (
            f"DRY RUN: AWS Snapshots eligible for deletion (>{SNAPSHOT_AGE_DAYS} days): "
            f"{len(outcome.plan.candidates)}"
        )
    else:
        deleted = list(outcome.result.succeeded) if outcome.result is not None else []
        msg = crc.get_msg("Snapshot", "Deleted", deleted)
    print(msg)


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


def _spot_instance_requests(options: Options) -> dict[str, Any]:
    return {
        "filter_tags": options.filter_tags,
        "exception_tags": options.exception_tags,
        "notags": options.notags,
        "age": options.age,
        "custom_age_tag_key": options.custom_age_tag_key,
    }


def _disk(options: Options) -> dict[str, Any]:
    return {
        "filter_tags": options.filter_tags,
        "exception_tags": options.exception_tags,
        "notags": options.notags,
        "name_regex": options.name_regex,
        "exception_regex": options.exception_regex,
        "custom_age_tag_key": options.custom_age_tag_key,
        "age": options.age,
        "detach_age": options.detach_age,
    }


def _disk_opt_in(crc: Any, options: Options, targets: Targets) -> None:
    """Circuit breaker: AWS EBS is opt-in, so any other aws and disk pair raises."""
    if not targets.aws_disk_opt_in:
        raise ValueError(AWS_DISK_OPT_IN_MESSAGE)


ROUTES: dict[str, Route] = {
    "keypair": Route(_keypair),
    "ip": Route(_ip),
    "kms": Route(_kms),
    "snapshot": Route(_snapshot, notify=NotifyMode.NONE, after=_snapshot_message),
    "vm": Route(_vm),
    "spot_instance_requests": Route(_spot_instance_requests),
    "disk": Route(_disk, notify=NotifyMode.REPORT_THEN_RERAISE, precheck=_disk_opt_in),
    "spot_instances": Route(skips.no_options, skip=skips.aws_spot_instances),
}
