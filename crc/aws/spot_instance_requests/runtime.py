# Copyright (c) Yugabyte, Inc.

"""Injected collaborators of the spot cleaner and the settings read from the run options."""

from dataclasses import dataclass, field
from typing import Any

from crc.aws.spot_instance_requests.client import (
    ClientFactory,
    RegionLister,
    all_regions,
    boto3_client,
)
from crc.aws.spot_instance_requests.model import SpotSettings, Waiter
from crc.core import age as core_age


@dataclass(frozen=True, slots=True)
class SpotRuntime:
    """Injected collaborators: SDK client factory, region lister and wait."""

    client_factory: ClientFactory = boto3_client
    list_regions: RegionLister = all_regions
    waiter: Waiter = field(default_factory=Waiter)


def settings_from_options(options: Any) -> SpotSettings:
    """Build the settings from `RunContext.options` (age is required and positive)."""
    return SpotSettings(
        filter_tags=options.get("filter_tags") or {},
        exception_tags=options.get("exception_tags") or {},
        notags=options.get("notags") or {},
        age=core_age.require_positive_age(options.get("age")),
        custom_age_tag_key=options.get("custom_age_tag_key"),
    )
