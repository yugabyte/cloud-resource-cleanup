"""Scenarios of `tests/test_cli_bridge_azu.py`: per Azure kind, the fake SDK data and the flags.

Each `Kind` builds a fresh fake Azure SDK per run (the fakes consume their scripts), so the
legacy handler and the routed pair see the same data. `variants` name the data sets: a plain
mixed set, and the error shapes (listing error, one failing delete).
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from azure.core.exceptions import HttpResponseError

from tests import helpers_azure_disk as hd
from tests import helpers_azure_ip as hi
from tests import helpers_azure_nic as hn
from tests import helpers_azure_spot as hs
from tests import helpers_azure_vm as hv

ENV = {"env": ["ci"]}
AGE = {"days": 1}
BOOM = HttpResponseError("boom")


@dataclass(frozen=True)
class Kind:
    """One routed Azure kind: the resource, the operation, the fake SDK per variant, the flags."""

    resource: str
    make: Callable[[str], Any]
    flags: dict[str, Any]
    operation: str = "delete"
    tag: str = ""
    variants: tuple[str, ...] = ("plain", "empty", "list_error", "delete_error")


def _vms(variant: str) -> Any:
    vms = [
        hv.vm("a"),
        hv.vm("b", statuses=hv.STOPPED),
        hv.vm("c", tags={}),
        hv.vm("d", tags={"other": "x"}),
        hv.vm("e"),
        hv.vm("f", tags={"env": "ci", "owner": "x"}),
    ]
    if variant == "empty":
        return hv.FakeAzure([])
    if variant == "list_error":
        return hv.FakeAzure(vms, list_error=OSError("list down"))
    if variant == "delete_error":
        return hv.FakeAzure(vms, scripts={"vm_begin": {"a": [BOOM]}, "nic_begin": {"e": [BOOM]}})
    return hv.FakeAzure(vms)


def _ips(variant: str) -> Any:
    ips = [
        hi.ip("a", {"env": "ci"}),
        hi.ip("b", {"env": "ci"}, config=object()),
        hi.ip("c", {"x": "y"}),
        hi.ip("d", None),
        hi.ip("f", {"env": "ci", "owner": "x"}),
        hi.ip("e", {"env": "ci"}),
    ]
    if variant == "empty":
        return hi.FakeAzure([])
    if variant == "list_error":
        return hi.FakeAzure(ips, list_error=OSError("list down"))
    if variant == "delete_error":
        return hi.FakeAzure(ips, delete_errors={"e": BOOM})
    return hi.FakeAzure(ips)


def _nics(variant: str) -> Any:
    nics = [hn.nic("nic-a"), hn.nic("nic-b", vm="vm"), hn.nic("other"), hn.nic("nic-keep")]
    if variant == "empty":
        return hn.FakeAzure([])
    if variant == "list_error":
        return hn.FakeAzure(nics, list_error=OSError("list down"))
    if variant == "delete_error":
        return hn.FakeAzure(nics, begin_script={"nic-a": [BOOM, BOOM, BOOM]})
    return hn.FakeAzure(nics)


def _disks(variant: str) -> Any:
    disks = [
        hd.disk("a", {"env": "ci"}),
        hd.disk("b", {"env": "ci"}, state="Reserved"),
        hd.disk("c", {"other": "x"}),
        hd.disk("d", {"env": "ci"}),
        hd.disk("f", {"env": "ci", "owner": "x"}),
    ]
    if variant == "empty":
        return hd.FakeAzure([])
    if variant == "list_error":
        return hd.FakeAzure(disks, list_error=OSError("list down"))
    if variant == "delete_error":
        return hd.FakeAzure(disks, begin_script={"d": [BOOM]})
    return hd.FakeAzure(disks)


def _spots(variant: str) -> Any:
    vms = [
        hs.vm("s1", os_disk=hs.slot("os-1"), data_disks=[hs.slot("data-1")]),
        hs.vm("s2"),
        hs.vm("s3", priority="Regular"),
        hs.vm("s4", tags={"other": "x"}),
        hs.vm("s5"),
        hs.vm("s6", tags={"yb_task": "itest", "owner": "x"}),
    ]
    power = {"s5": hs.RUNNING}
    if variant == "empty":
        return hs.FakeAzure([])
    if variant == "list_error":
        return hs.FakeAzure(vms, list_error=OSError("list down"), power=power)
    if variant == "delete_error":
        script = {("vm_begin", "s2"): [BOOM]}
        return hs.FakeAzure(vms, power=power, script=script)
    return hs.FakeAzure(vms, power=power)


COMMON = {"filter_tags": ENV, "exception_tags": {}, "notags": {"owner": ["x"]}, "age": AGE}
SPOT = {**COMMON, "filter_tags": {"yb_task": ["itest"]}}
KINDS = [
    Kind("vm", _vms, {**COMMON, "resource_states": ["running"]}, tag="running"),
    Kind(
        "vm",
        _vms,
        {**COMMON, "resource_states": ["stopped", "running"]},
        operation="stop",
        tag="stop",
    ),
    Kind("vm", _vms, {**COMMON, "resource_states": ["stopped", "running"]}, tag="stopped"),
    Kind("ip", _ips, {**COMMON, "name_regex": ["n"], "exception_regex": ["x"]}),
    Kind("nic", _nics, {"name_regex": ["^nic"], "exception_regex": ["keep"]}),
    Kind("disk", _disks, COMMON),
    Kind("spot_instances", _spots, SPOT),
    Kind(
        "spot_instances",
        _spots,
        {**SPOT, "resource_group": None},
        variants=("empty",),
        tag="no_group",
    ),
]


def case_key(kind: Kind) -> str:
    """The name of a kind in the frozen run-log data: resource, operation and tag."""
    return "-".join(x for x in (kind.resource, kind.operation, kind.tag) if x)
