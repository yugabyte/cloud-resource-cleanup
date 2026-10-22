# Copyright (c) Yugabyte, Inc.

"""The OCI calls `crc.oci.vm` uses, behind small typed Protocols.

`OciComputeApi` is the real implementation over the OCI compute client of one region; tests pass
fakes that satisfy `ComputeApi` instead.
"""

import datetime
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

import oci

from crc.oci._base import Base

STOP_ACTION = "STOP"


class SdkInstance(Protocol):
    """The attributes of an OCI compute instance this cleaner reads."""

    @property
    def id(self) -> str:
        """OCID of the instance."""
        ...

    @property
    def display_name(self) -> str:
        """Display name, the name reported to Slack and InfluxDB."""
        ...

    @property
    def lifecycle_state(self) -> str:
        """Lifecycle state; only the allowed states (`RUNNING` by default) are considered."""
        ...

    @property
    def freeform_tags(self) -> Mapping[str, str] | None:
        """Freeform tags, or `None`."""
        ...

    @property
    def time_created(self) -> datetime.datetime:
        """Creation time."""
        ...


class ComputeApi(Protocol):
    """The OCI calls this cleaner uses, for one region and the fixed compartment."""

    def list_instances(self) -> Sequence[SdkInstance]:
        """Every instance of the compartment (all pages)."""
        ...

    def terminate_instance(self, instance_id: str) -> None:
        """Terminate one instance."""
        ...

    def stop_instance(self, instance_id: str) -> None:
        """Stop one instance (`instance_action` with `STOP`)."""
        ...


ApiFactory = Callable[[str], ComputeApi]


class OciComputeApi:
    """`ComputeApi` over the OCI compute client of one region."""

    def __init__(self, compute_client: Any, compartment_id: str) -> None:
        self._compute = compute_client
        self._compartment_id = compartment_id

    def list_instances(self) -> Sequence[SdkInstance]:
        instances: Sequence[SdkInstance] = oci.pagination.list_call_get_all_results(
            self._compute.list_instances, compartment_id=self._compartment_id
        ).data
        return instances

    def terminate_instance(self, instance_id: str) -> None:
        self._compute.terminate_instance(instance_id)

    def stop_instance(self, instance_id: str) -> None:
        self._compute.instance_action(instance_id, STOP_ACTION)


def compute_api_for(base: Base) -> ApiFactory:
    """Factory of regional APIs on top of `Base`, which caches its clients per region."""

    def factory(region: str) -> ComputeApi:
        return OciComputeApi(base.get_compute_client(region), base.compartment_id)

    return factory
