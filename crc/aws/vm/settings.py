# Copyright (c) Yugabyte, Inc.

"""Settings, constants and default factories of the AWS VM cleaner."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import boto3

from crc.aws._base import get_all_regions
from crc.aws.vm.client import VmClient

SERVICE_NAME = "ec2"
DEFAULT_REGION = "us-west-2"
DEFAULT_STATE: list[str] = ["running"]

ClientFactory = Callable[[str], VmClient]
RegionLister = Callable[[], Sequence[str]]
Filters = list[dict[str, Any]]


def boto3_client(region: str) -> VmClient:
    client: VmClient = boto3.client(SERVICE_NAME, region_name=region)
    return client


def all_regions() -> Sequence[str]:
    return get_all_regions(SERVICE_NAME, DEFAULT_REGION)


@dataclass(frozen=True, slots=True)
class VmSettings:
    """Filters and thresholds of one run."""

    filter_tags: Any
    exception_tags: Any
    notags: Any
    age: Any
    custom_age_tag_key: Any
    instance_state: Any
    require_age: bool = False


def _optional(options: Mapping[str, Any], key: str) -> Any:
    return options.get(key, {})


def settings_from_options(options: Mapping[str, Any], *, require_age: bool = False) -> VmSettings:
    """Build the settings from `RunContext.options` (missing filters are empty dicts).

    `require_age` is True for a real run: an instance with neither a custom age tag nor an
    `--age` is then kept (BUG-213)."""
    return VmSettings(
        filter_tags=_optional(options, "filter_tags"),
        exception_tags=_optional(options, "exception_tags"),
        notags=_optional(options, "notags"),
        age=_optional(options, "age"),
        custom_age_tag_key=options.get("custom_age_tag_key"),
        instance_state=options.get("instance_state", DEFAULT_STATE),
        require_age=require_age,
    )
