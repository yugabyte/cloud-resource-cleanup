"""Parsed flags, environment tokens and input validation, in the order `main` runs them."""

import os
from dataclasses import dataclass
from typing import Any

from crc.cli.constants import CLOUDS, RESOURCES
from crc.cli.validate import (
    are_values_of_dict_lists,
    is_valid_dict,
    is_valid_list,
)


@dataclass(frozen=True, slots=True)
class Options:
    """The values of the parsed flags, as read from the argument dict."""

    clouds: Any
    project_id: Any
    resources: Any
    operation_type: Any
    resource_states: Any
    filter_tags: Any
    exception_tags: Any
    name_regex: Any
    exception_regex: Any
    age: Any
    custom_age_tag_key: Any
    max_age: Any
    detach_age: Any
    dry_run: Any
    notags: Any
    slack_channel: Any
    slack_notify_users: Any
    slack_user_label: Any
    influxdb: Any
    kms_pending_window: Any
    kms_key_description: Any
    kms_user: Any
    resource_group: Any
    scan_tag: Any


@dataclass(frozen=True, slots=True)
class Tokens:
    """The secrets `main` reads from the environment."""

    influxdb: str | None
    slack_bot: str | None


@dataclass(frozen=True, slots=True)
class Targets:
    """The clouds and resources one run walks, and the facts the loops need about the flags."""

    cloud_arg: Any
    aws_disk_opt_in: bool
    clouds: list[Any]
    resources: list[Any]


def read_options(args: dict[str, Any]) -> Options:
    """Read every flag from the argument dict with `.get`, as `main` does."""
    return Options(
        clouds=args.get("cloud"),
        project_id=args.get("project_id"),
        resources=args.get("resource"),
        operation_type=args.get("operation_type"),
        resource_states=args.get("resource_states"),
        filter_tags=args.get("filter_tags"),
        exception_tags=args.get("exception_tags"),
        name_regex=args.get("name_regex"),
        exception_regex=args.get("exception_regex"),
        age=args.get("age"),
        custom_age_tag_key=args.get("custom_age_tag_key"),
        max_age=args.get("max_age"),
        detach_age=args.get("detach_age"),
        dry_run=args.get("dry_run"),
        notags=args.get("notags"),
        slack_channel=args.get("slack_channel"),
        slack_notify_users=args.get("slack_notify_users"),
        slack_user_label=args.get("slack_user_label"),
        influxdb=args.get("influxdb"),
        kms_pending_window=args.get("kms_pending_window"),
        kms_key_description=args.get("kms_key_description"),
        kms_user=args.get("kms_user"),
        resource_group=args.get("resource_group"),
        scan_tag=args.get("scan_tag"),
    )


def read_tokens() -> Tokens:
    """Read INFLUXDB_TOKEN and SLACK_BOT_TOKEN from the environment."""
    return Tokens(
        influxdb=os.environ.get("INFLUXDB_TOKEN"),
        slack_bot=os.environ.get("SLACK_BOT_TOKEN"),
    )


def check_tokens(options: Options, tokens: Tokens) -> None:
    """Raise when a channel or InfluxDB is asked for without its token."""
    if options.slack_channel and not tokens.slack_bot:
        raise OSError("SLACK_BOT_TOKEN is not set")

    if options.influxdb and not tokens.influxdb:
        raise OSError("INFLUXDB_TOKEN is not set")


def check_flag_combinations(options: Options) -> None:
    """Raise on the flag combinations `main` refuses, in its order."""
    if options.operation_type == "stop" and options.resources != "vm":
        raise ValueError("Stop is supported only for vm resource")

    if options.resources == "all" and options.clouds != "all":
        raise ValueError(
            "All Resources cleanup is supported only with all Clouds. "
            "Format: --cloud all --resources all"
        )

    if options.slack_notify_users and not options.slack_user_label:
        raise ValueError("--slack_user_label is mandatory when passing --slack_notify_user")

    if options.resources in ("kms", "all"):
        if not options.kms_key_description:
            raise ValueError("Key description string is required for deleting KMS keys.")
        if not options.kms_user:
            raise ValueError("Jenkins user ARN is reuired for deleting associated KMS keys.")


def resolve_targets(options: Options) -> Targets:
    """Expand `all` into the cloud and resource lists.

    AWS EBS is opt-in: `aws_disk_opt_in` is True only for `--cloud aws --resource disk`, and the
    dispatch loop raises on the aws and disk pair otherwise. GCP and OCI spot cleanup deletes
    running instances, so `--cloud all` keeps them out unless the cloud is explicit.
    """
    clouds = options.clouds
    resources = options.resources
    return Targets(
        cloud_arg=clouds,
        aws_disk_opt_in=clouds == "aws" and resources == "disk",
        clouds=CLOUDS if clouds == "all" else [clouds],
        resources=RESOURCES if resources == "all" else [resources],
    )


def validate_values(options: Options) -> None:
    """Check the type of the list and dict flags, in the order of `main`."""
    is_valid_list("resource_states", options.resource_states)
    are_values_of_dict_lists("filter_tags", options.filter_tags)
    are_values_of_dict_lists("exception_tags", options.exception_tags)
    is_valid_list("name_regex", options.name_regex)
    is_valid_list("exception_regex", options.exception_regex)
    is_valid_dict("age", options.age)
    is_valid_dict("detach_age", options.detach_age)
    is_valid_dict("influxdb", options.influxdb)

