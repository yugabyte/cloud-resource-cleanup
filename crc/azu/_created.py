# Copyright (c) Yugabyte, Inc.

"""Age gate for the Azure kinds whose network SDK model has no creation time (ip, nic).

`azure-mgmt-network` returns no creation time for public IPs and NICs, so it is read from
Azure Resource Manager (`ResourceManagementClient.resources.list_by_resource_group` with
`expand="createdTime"`), one listing per run and resource type. A resource with no creation time,
and every resource when the lookup fails, is kept (fail closed).
"""

import datetime
import logging
from collections.abc import Callable, Iterable
from typing import Any, Protocol

from crc.core import age as core_age
from crc.core.age import AgeSpec

IP_TYPE = "Microsoft.Network/publicIPAddresses"
NIC_TYPE = "Microsoft.Network/networkInterfaces"

NO_CREATED_TIME = "no creation time"
LOOKUP_FAILED = "creation time lookup failed"
TOO_YOUNG = "not older than the age threshold"


class ResourceOperations(Protocol):
    """The one `resources` call this gate uses."""

    def list_by_resource_group(
        self, resource_group_name: str, *, filter: str, expand: str
    ) -> Iterable[Any]:
        """List the resources of one group with their `created_time`."""
        ...


class ResourceClient(Protocol):
    """The part of the Azure resource management client this gate uses."""

    @property
    def resources(self) -> ResourceOperations:
        """Operations on generic resources."""
        ...


ResourceClientFactory = Callable[[], ResourceClient]


def _aware(created: datetime.datetime) -> datetime.datetime:
    return created if created.tzinfo is not None else created.replace(tzinfo=datetime.UTC)


class AgeGate:
    """Keeps a resource younger than `age`, or whose creation time is unknown."""

    def __init__(
        self,
        age: AgeSpec,
        client_factory: ResourceClientFactory,
        resource_group: str,
        resource_type: str,
    ) -> None:
        self._age = age
        self._client_factory = client_factory
        self._resource_group = resource_group
        self._resource_type = resource_type
        self._now: datetime.datetime | None = None
        self._created: dict[str, datetime.datetime | None] = {}
        self._error = ""
        self._loaded = False

    def load(self, now: datetime.datetime) -> None:
        """Read the creation times of the resource type: one listing, made once per run."""
        self._now = now
        if self._loaded:
            return
        self._loaded = True
        try:
            listing = self._client_factory().resources.list_by_resource_group(
                self._resource_group,
                filter=f"resourceType eq '{self._resource_type}'",
                expand="createdTime",
            )
            for resource in listing:
                resource_id = getattr(resource, "id", None)
                if isinstance(resource_id, str) and resource_id:
                    self._created[resource_id.lower()] = getattr(resource, "created_time", None)
        except Exception as e:  # noqa: BLE001 - fail closed: every resource is kept and the reason recorded
            self._created = {}
            self._error = str(e) or type(e).__name__
            logging.error(
                "Could not read the creation times of %s in %s: %s",
                self._resource_type,
                self._resource_group,
                e,
            )

    def keep_reason(self, resource_id: object) -> str:
        """Empty when the resource is old enough to act on, else why it is kept."""
        if self._error:
            return LOOKUP_FAILED
        created = self._created.get(resource_id.lower()) if isinstance(resource_id, str) else None
        if not isinstance(created, datetime.datetime) or self._now is None:
            return NO_CREATED_TIME
        created = _aware(created)
        current = self._now.astimezone(created.tzinfo)
        return "" if core_age.is_old(self._age, current, created) else TOO_YOUNG
