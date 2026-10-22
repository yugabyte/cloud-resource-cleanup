"""Parity: `crc.aws.vpc.VPC` against main (`tests/legacy/aws_vpc.txt`).

Both run against the same fake `boto3.client` / `boto3.resource` object graph; SDK calls (in
order), results, log records, stdout and raised errors must be identical for every case. The
registered `Cleaner` path (factory with a `RunContext`, `plan()` then `apply()` unless dry run)
is compared per region on SDK calls, and on results, errors, stdout, log records and the
summary lines.
"""

import datetime
import logging
import types
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError, ConnectTimeoutError, EndpointConnectionError

from crc.aws.vpc import VPC, VpcCleaner, VpcRuntime, build_vpc_cleaner
from crc.core.cleaner import Cleaner, Operation, RunContext
from crc.core.registry import get

ORACLE = Path(__file__).parent / "legacy" / "aws_vpc.txt"
NOW = datetime.datetime(2026, 3, 1, 12, 0, 0)
MUTATING = {
    "associate_with_vpc",
    "detach_internet_gateway",
    "gw.delete",
    "rta.delete",
    "rt.delete",
    "instance.terminate",
    "delete_vpc_endpoints",
    "sg.delete",
    "peering.delete",
    "netacl.delete",
    "interface.delete",
    "subnet.delete",
    "delete_vpc",
}
FIVE = 5
SKIP_WARNING = "Tags and notags not present"
THREE_DELETES = 2
FOUR_DELETES = 4


@dataclass
class Sub:
    id: str
    instances: list[str] = field(default_factory=list)
    enis: list[str] = field(default_factory=list)


@dataclass
class Rt:
    id: str
    assocs: list[tuple[str, bool]] = field(default_factory=list)


@dataclass
class V:
    id: str
    gateways: list[str] = field(default_factory=list)
    route_tables: list[Rt] = field(default_factory=list)
    subnets: list[Sub] = field(default_factory=list)
    sgs: list[tuple[str, str]] = field(default_factory=list)
    acls: list[tuple[str, bool]] = field(default_factory=list)
    endpoints: list[str] = field(default_factory=list)
    peerings: list[str] = field(default_factory=list)
    tags: list[Any] | None = None


def full(vid: str, tags: list[Any] | None = None) -> V:
    return V(
        vid,
        gateways=[f"igw-{vid}"],
        route_tables=[
            Rt(f"rtb-{vid}-main", [(f"rtbassoc-{vid}-m", True)]),
            Rt(f"rtb-{vid}-a", [(f"rtbassoc-{vid}-s1", False), (f"rtbassoc-{vid}-s2", False)]),
            Rt(f"rtb-{vid}-empty"),
        ],
        subnets=[
            Sub(f"subnet-{vid}-1", [f"i-{vid}-1", f"i-{vid}-2"], [f"eni-{vid}-1"]),
            Sub(f"subnet-{vid}-2", [], [f"eni-{vid}-2", f"eni-{vid}-3"]),
        ],
        sgs=[(f"sg-{vid}-def", "default"), (f"sg-{vid}-a", "web"), (f"sg-{vid}-b", "db")],
        acls=[(f"acl-{vid}-def", True), (f"acl-{vid}-a", False)],
        endpoints=[f"vpce-{vid}-1", f"vpce-{vid}-2"],
        peerings=[f"pcx-{vid}-1"],
        tags=tags,
    )


def tag_list(mapping: Mapping[str, str]) -> list[dict[str, str]]:
    return [{"Key": k, "Value": v} for k, v in mapping.items()]


def conn_error() -> Exception:
    error: Exception = EndpointConnectionError(endpoint_url="https://ec2.example")
    return error


def client_error(operation: str) -> Exception:
    error: Exception = ClientError(
        {"Error": {"Code": "AuthFailure", "Message": "denied"}}, operation
    )
    return error


@dataclass
class Case:
    id: str
    regions: Mapping[str, Any]
    filter_tags: Any = field(default_factory=dict)
    exception_tags: Any = field(default_factory=dict)
    notags: Any = field(default_factory=dict)
    dry_run: bool = False
    errors: Mapping[tuple[Any, ...], Any] = field(default_factory=dict)
    regions_error: Exception | None = None
    fails_in_plan: bool = False


W2 = "us-west-2"
E1 = "eu-west-1"
ENV = tag_list({"env": "test", "owner": "qa"})
PROD = tag_list({"env": "prod"})
ONE = {W2: [full("vpc-1")]}
ONE_TAGGED = {W2: [full("vpc-1", ENV)]}
TWO_TAGGED = {W2: [full("vpc-1", ENV), full("vpc-2", PROD)]}
TWO = {W2: [full("vpc-1"), full("vpc-2")], E1: [full("vpc-3")]}


def _case_group_basic() -> list[Case]:
    return [
        Case("dry-run", TWO, dry_run=True),
        Case("dry-run-with-filter", TWO, filter_tags={"env": ["test"]}, dry_run=True),
        Case("delete-two-regions", TWO),
        Case("delete-one", ONE),
        Case("filter-none", ONE, filter_tags=None),
        Case("filter-key-only-empty-values", ONE, filter_tags={"env": []}),
        Case("filter-key-and-values", ONE, filter_tags={"env": ["a", "b*"]}),
        Case("filter-two-keys", ONE, filter_tags={"env": ["test"], "owner": ["qa"]}),
        Case("filter-regex-like-value-passed-unchanged", ONE, filter_tags={"a": [".*", "^x$"]}),
        Case("empty-results", {W2: [], E1: []}),
        Case("empty-results-dry-run", {W2: [], E1: []}, dry_run=True),
        Case("no-regions", {}),
        Case("no-regions-dry-run", {}, dry_run=True),
        Case("same-vpc-id-two-regions", {W2: [full("vpc-1")], E1: [full("vpc-1")]}),
        Case("many-vpcs-order", {W2: [full(f"vpc-{i}") for i in range(7)], E1: [full("vpc-x")]}),
        Case("bare-vpc-without-children", {W2: [V("vpc-1")]}),
        Case("bare-vpc-dry-run", {W2: [V("vpc-1")]}, dry_run=True),
    ]


def _case_group_children() -> list[Case]:
    return [
        Case("default-security-group-kept", {W2: [V("vpc-1", sgs=[("sg-d", "default")])]}),
        Case("default-acl-kept", {W2: [V("vpc-1", acls=[("acl-d", True)])]}),
        Case(
            "main-association-kept-and-route-table-kept",
            {W2: [V("vpc-1", route_tables=[Rt("rtb-1", [("a-1", True)])])]},
        ),
        Case(
            "non-main-association-deleted-route-table-kept",
            {W2: [V("vpc-1", route_tables=[Rt("rtb-1", [("a-1", False)])])]},
        ),
        Case(
            "route-table-without-associations-deleted",
            {W2: [V("vpc-1", route_tables=[Rt("rtb-1")])]},
        ),
        Case("two-gateways", {W2: [V("vpc-1", gateways=["igw-1", "igw-2"])]}),
        Case(
            "instances-in-two-subnets",
            {W2: [V("vpc-1", subnets=[Sub("s-1", ["i-1"]), Sub("s-2", ["i-2", "i-3"])])]},
        ),
    ]


def _case_group_skip() -> list[Case]:
    return [
        Case("exception-tags-real-vpc-raises-type-error", ONE, exception_tags={"keep": []}),
        Case("notags-real-vpc-raises-type-error", ONE, notags={"keep": []}),
        Case("both-real-vpc-raises-type-error", ONE, exception_tags={"a": []}, notags={"b": []}),
        Case(
            "exception-tags-dry-run-does-not-raise",
            ONE,
            exception_tags={"keep": []},
            dry_run=True,
        ),
        Case("notags-dry-run-does-not-raise", ONE, notags={"keep": []}, dry_run=True),
        Case(
            "exception-tags-empty-first-region-raises-in-second",
            {W2: [], E1: [full("vpc-2")]},
            exception_tags={"keep": []},
        ),
        Case("exception-tags-no-vpcs-no-error", {W2: [], E1: []}, exception_tags={"keep": []}),
        Case("exception-none-notags-none", ONE, exception_tags=None, notags=None),
        Case("exception-empty-notags-empty", ONE, exception_tags={}, notags={}),
        Case(
            "exception-tags-second-vpc-never-reached",
            {W2: [full("vpc-1"), full("vpc-2")]},
            exception_tags={"keep": []},
        ),
    ]


def _case_group_tagged() -> list[Case]:
    return [
        Case("tagged-no-skip-spec", ONE_TAGGED),
        Case("tagged-exception-key-only", TWO_TAGGED, exception_tags={"env": []}),
        Case("tagged-exception-key-and-value", TWO_TAGGED, exception_tags={"env": ["prod"]}),
        Case("tagged-exception-value-mismatch", TWO_TAGGED, exception_tags={"env": ["x"]}),
        Case("tagged-exception-no-key", TWO_TAGGED, exception_tags={"zzz": []}),
        Case("tagged-notags-all-keys-present", TWO_TAGGED, notags={"env": [], "owner": []}),
        Case("tagged-notags-one-key-missing", TWO_TAGGED, notags={"env": [], "owner": ["x"]}),
        Case("tagged-notags-value-match", TWO_TAGGED, notags={"env": ["prod"]}),
        Case("tagged-notags-value-mismatch", TWO_TAGGED, notags={"env": ["x"]}),
        Case(
            "tagged-exception-wins-over-notags",
            ONE_TAGGED,
            exception_tags={"env": []},
            notags={"zzz": []},
        ),
        Case(
            "tagged-notags-and-exception-miss",
            ONE_TAGGED,
            exception_tags={"zzz": []},
            notags={"env": []},
        ),
        Case(
            "tagged-dry-run-records-protected", TWO_TAGGED, exception_tags={"env": []}, dry_run=True
        ),
        Case("tagged-case-sensitive", ONE_TAGGED, exception_tags={"ENV": []}),
        Case(
            "tagged-protected-vpc-still-recorded",
            {W2: [full("vpc-1", PROD), full("vpc-2", ENV)], E1: [full("vpc-3", PROD)]},
            exception_tags={"env": ["prod"]},
        ),
        Case(
            "tagged-empty-tag-list-exception", {W2: [full("vpc-1", [])]}, exception_tags={"a": []}
        ),
        Case("tagged-empty-tag-list-notags", {W2: [full("vpc-1", [])]}, notags={"a": []}),
        Case("tagged-empty-tag-list-no-spec", {W2: [full("vpc-1", [])]}),
        Case(
            "tagged-tag-without-key",
            {W2: [full("vpc-1", [{"Value": "v"}])]},
            exception_tags={"a": []},
        ),
        Case(
            "tagged-tag-without-value",
            {W2: [full("vpc-1", [{"Key": "a"}])]},
            exception_tags={"a": []},
        ),
        Case(
            "tagged-exception-hit-before-malformed-tag",
            {W2: [full("vpc-1", [*ENV, {"Key": "x"}])]},
            exception_tags={"env": []},
        ),
        Case(
            "tagged-malformed-tag-before-exception-hit",
            {W2: [full("vpc-1", [{"Key": "x"}, *ENV])]},
            exception_tags={"env": []},
        ),
        Case(
            "tagged-duplicate-key-last-value-wins-for-notags",
            {W2: [full("vpc-1", [{"Key": "a", "Value": "1"}, {"Key": "a", "Value": "2"}])]},
            notags={"a": ["2"]},
        ),
        Case(
            "tagged-duplicate-key-notags-first-value-gone",
            {W2: [full("vpc-1", [{"Key": "a", "Value": "1"}, {"Key": "a", "Value": "2"}])]},
            notags={"a": ["1"]},
        ),
    ]


def _case_group_errors() -> list[Case]:
    delete_key = ("delete_vpc", W2, "vpc-1")
    return [
        Case(
            "list-connection-error-skips-region",
            {W2: conn_error(), E1: [full("vpc-3")]},
        ),
        Case(
            "list-timeout-skips-region",
            {W2: ConnectTimeoutError(endpoint_url="https://ec2.example"), E1: [full("vpc-3")]},
        ),
        Case(
            "list-client-error-propagates",
            {W2: [full("vpc-1")], E1: client_error("DescribeVpcs")},
            fails_in_plan=True,
        ),
        Case("list-unexpected-error", {W2: ValueError("boom")}, fails_in_plan=True),
        Case("list-error-dry-run", {W2: client_error("DescribeVpcs")}, dry_run=True),
        Case("delete-vpc-retried-then-succeeds", ONE, errors={delete_key: [ValueError("a"), None]}),
        Case(
            "delete-vpc-fails-every-retry-swallowed",
            {W2: [full("vpc-1"), full("vpc-2")]},
            errors={delete_key: [client_error("DeleteVpc")] * FIVE},
        ),
        Case(
            "delete-vpc-dependency-violation-swallowed",
            ONE,
            errors={delete_key: [ValueError("DependencyViolation")] * 9},
        ),
        Case(
            "delete-vpc-connection-error-is-retried-not-skipped",
            {W2: [full("vpc-1")], E1: [full("vpc-3")]},
            errors={delete_key: [conn_error(), conn_error(), None]},
        ),
        Case(
            "security-group-delete-client-error-propagates",
            {W2: [full("vpc-1"), full("vpc-2")]},
            errors={("sg.delete", W2, "sg-vpc-1-a"): client_error("DeleteSecurityGroup")},
        ),
        Case(
            "security-group-delete-connection-error-skips-region-keeps-ids",
            {W2: [full("vpc-1"), full("vpc-2")], E1: [full("vpc-3")]},
            errors={("sg.delete", W2, "sg-vpc-1-a"): conn_error()},
        ),
        Case(
            "gateway-detach-error-propagates",
            ONE,
            errors={("detach_internet_gateway", W2, "vpc-1", "igw-vpc-1"): client_error("Detach")},
        ),
        Case(
            "gateway-delete-error-propagates",
            ONE,
            errors={("gw.delete", W2, "igw-vpc-1"): client_error("DeleteInternetGateway")},
        ),
        Case(
            "dhcp-associate-error-propagates",
            ONE,
            errors={("associate_with_vpc", W2, "vpc-1", "vpc-1"): client_error("Associate")},
        ),
        Case(
            "route-table-delete-error-propagates",
            ONE,
            errors={("rt.delete", W2, "rtb-vpc-1-empty"): client_error("DeleteRouteTable")},
        ),
        Case(
            "association-delete-error-propagates",
            ONE,
            errors={("rta.delete", W2, "rtbassoc-vpc-1-s1"): client_error("DisassociateRoute")},
        ),
        Case(
            "instance-terminate-error-propagates",
            ONE,
            errors={("instance.terminate", W2, "i-vpc-1-2"): client_error("Terminate")},
        ),
        Case(
            "describe-endpoints-error-propagates",
            ONE,
            errors={("describe_vpc_endpoints", W2, "vpc-1"): client_error("DescribeVpcEndpoints")},
        ),
        Case(
            "delete-endpoints-error-propagates",
            ONE,
            errors={
                ("delete_vpc_endpoints", W2, ("vpce-vpc-1-2",)): client_error("DeleteVpcEndpoints")
            },
        ),
        Case(
            "describe-peering-error-propagates",
            ONE,
            errors={("describe_vpc_peering_connections", W2, "vpc-1"): client_error("Peering")},
        ),
        Case(
            "peering-delete-error-propagates",
            ONE,
            errors={("peering.delete", W2, "pcx-vpc-1-1"): client_error("DeletePeering")},
        ),
        Case(
            "acl-delete-error-propagates",
            ONE,
            errors={("netacl.delete", W2, "acl-vpc-1-a"): client_error("DeleteNetworkAcl")},
        ),
        Case(
            "interface-delete-error-propagates",
            ONE,
            errors={("interface.delete", W2, "eni-vpc-1-2"): client_error("DeleteNic")},
        ),
        Case(
            "subnet-delete-error-propagates",
            ONE,
            errors={("subnet.delete", W2, "subnet-vpc-1-1"): client_error("DeleteSubnet")},
        ),
        Case(
            "dry-run-errors-never-reached",
            ONE,
            dry_run=True,
            errors={("sg.delete", W2, "sg-vpc-1-a"): client_error("DeleteSecurityGroup")},
        ),
    ]


def _case_group_malformed() -> list[Case]:
    return [
        Case(
            "missing-vpc-endpoints-key",
            ONE,
            errors={("describe_vpc_endpoints", W2, "vpc-1"): "no-key"},
        ),
        Case(
            "missing-vpc-endpoint-id",
            ONE,
            errors={("describe_vpc_endpoints", W2, "vpc-1"): "no-id"},
        ),
        Case(
            "missing-peering-connections-key",
            ONE,
            errors={("describe_vpc_peering_connections", W2, "vpc-1"): "no-key"},
        ),
        Case(
            "missing-peering-id",
            ONE,
            errors={("describe_vpc_peering_connections", W2, "vpc-1"): "no-id"},
        ),
        Case(
            "describe-regions-client-error-propagates",
            ONE,
            regions_error=client_error("DescribeRegions"),
        ),
        Case(
            "describe-regions-connection-error-falls-back",
            ONE,
            regions_error=conn_error(),
        ),
        Case(
            "describe-regions-connection-error-falls-back-dry-run",
            TWO,
            regions_error=conn_error(),
            dry_run=True,
        ),
        Case(
            "describe-regions-client-error-dry-run",
            ONE,
            regions_error=client_error("DescribeRegions"),
            dry_run=True,
        ),
    ]


CASES = [
    *_case_group_basic(),
    *_case_group_children(),
    *_case_group_skip(),
    *_case_group_tagged(),
    *_case_group_errors(),
    *_case_group_malformed(),
]


class FakeAws:
    def __init__(self, case: Case) -> None:
        self.case = case
        self.calls: list[tuple[Any, ...]] = []
        self.pending: dict[tuple[Any, ...], list[Any]] = {
            key: list(value) for key, value in case.errors.items() if isinstance(value, list)
        }

    def fire(self, *call: Any) -> Any:
        self.calls.append(call)
        outcome = self.case.errors.get(call)
        if isinstance(outcome, list):
            outcome = self.pending[call].pop(0) if self.pending[call] else None
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def vpc_by_id(self, region: str, vpc_id: str) -> V:
        found: V = next(v for v in self.case.regions[region] if v.id == vpc_id)
        return found

    def client(self, service: str, region_name: str | None = None) -> "FakeClient":
        self.calls.append(("boto3.client", region_name, service))
        assert region_name is not None
        return FakeClient(self, region_name)

    def resource(self, service: str, region_name: str | None = None) -> "FakeResource":
        self.calls.append(("boto3.resource", region_name, service))
        assert region_name is not None
        return FakeResource(self, region_name)

    def session(self) -> Any:
        self.calls.append(("session", ""))
        return types.SimpleNamespace(get_available_regions=lambda service: list(self.case.regions))


class FakeClient:
    def __init__(self, aws: FakeAws, region: str) -> None:
        self.aws = aws
        self.region = region

    def describe_regions(self) -> dict[str, Any]:
        self.aws.calls.append(("describe_regions", self.region))
        if self.aws.case.regions_error is not None:
            raise self.aws.case.regions_error
        return {"Regions": [{"RegionName": r} for r in self.aws.case.regions]}

    def describe_vpc_endpoints(self, *, Filters: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        vpc_id = Filters[0]["Values"][0]
        mode = self.aws.fire("describe_vpc_endpoints", self.region, vpc_id)
        if mode == "no-key":
            return {}
        if mode == "no-id":
            return {"VpcEndpoints": [{"Other": 1}]}
        endpoints = self.aws.vpc_by_id(self.region, vpc_id).endpoints
        return {"VpcEndpoints": [{"VpcEndpointId": e} for e in endpoints]}

    def delete_vpc_endpoints(self, *, VpcEndpointIds: Sequence[str]) -> dict[str, Any]:
        self.aws.fire("delete_vpc_endpoints", self.region, tuple(VpcEndpointIds))
        return {}

    def describe_vpc_peering_connections(
        self, *, Filters: Sequence[Mapping[str, Any]]
    ) -> dict[str, Any]:
        vpc_id = Filters[0]["Values"][0]
        assert Filters[0]["Name"] == "requester-vpc-info.vpc-id"
        mode = self.aws.fire("describe_vpc_peering_connections", self.region, vpc_id)
        if mode == "no-key":
            return {}
        if mode == "no-id":
            return {"VpcPeeringConnections": [{"Other": 1}]}
        peerings = self.aws.vpc_by_id(self.region, vpc_id).peerings
        return {"VpcPeeringConnections": [{"VpcPeeringConnectionId": p} for p in peerings]}

    def delete_vpc(self, *, VpcId: str) -> None:
        self.aws.fire("delete_vpc", self.region, VpcId)


class Coll:
    def __init__(self, aws: FakeAws, name: str, region: str, owner: str, items: list[Any]) -> None:
        self.aws, self.name, self.region, self.owner, self.items = aws, name, region, owner, items

    def all(self) -> Iterator[Any]:
        self.aws.fire(self.name, self.region, self.owner)
        return iter(self.items)


class Leaf:
    def __init__(self, aws: FakeAws, region: str, prefix: str, ident: str, **attrs: Any) -> None:
        self.aws, self.region, self.prefix, self.id = aws, region, prefix, ident
        for key, value in attrs.items():
            setattr(self, key, value)

    def delete(self) -> None:
        self.aws.fire(f"{self.prefix}.delete", self.region, self.id)

    def terminate(self) -> None:
        self.aws.fire(f"{self.prefix}.terminate", self.region, self.id)


class FakeRouteTable(Leaf):
    def __init__(self, aws: FakeAws, region: str, spec: Rt) -> None:
        super().__init__(aws, region, "rt", spec.id)
        self._assocs = [Leaf(aws, region, "rta", a, main=m) for a, m in spec.assocs]

    @property
    def associations(self) -> list[Leaf]:
        self.aws.fire("rt.associations", self.region, self.id)
        return self._assocs


class FakeSubnet(Leaf):
    def __init__(self, aws: FakeAws, region: str, spec: Sub) -> None:
        super().__init__(aws, region, "subnet", spec.id)
        self.instances = Coll(
            aws,
            "subnet.instances.all",
            region,
            spec.id,
            [Leaf(aws, region, "instance", i) for i in spec.instances],
        )
        self.network_interfaces = Coll(
            aws,
            "subnet.network_interfaces.all",
            region,
            spec.id,
            [Leaf(aws, region, "interface", e) for e in spec.enis],
        )


class FakeVpc:
    def __init__(self, aws: FakeAws, region: str, spec: V) -> None:
        self.aws, self.region, self.spec, self.id = aws, region, spec, spec.id
        rid = spec.id
        self.internet_gateways = Coll(
            aws,
            "internet_gateways.all",
            region,
            rid,
            [Leaf(aws, region, "gw", g) for g in spec.gateways],
        )
        self.route_tables = Coll(
            aws,
            "route_tables.all",
            region,
            rid,
            [FakeRouteTable(aws, region, r) for r in spec.route_tables],
        )
        self.subnets = Coll(
            aws, "subnets.all", region, rid, [FakeSubnet(aws, region, s) for s in spec.subnets]
        )
        self.security_groups = Coll(
            aws,
            "security_groups.all",
            region,
            rid,
            [Leaf(aws, region, "sg", i, group_name=n) for i, n in spec.sgs],
        )
        self.network_acls = Coll(
            aws,
            "network_acls.all",
            region,
            rid,
            [Leaf(aws, region, "netacl", i, is_default=d) for i, d in spec.acls],
        )

    def detach_internet_gateway(self, *, InternetGatewayId: str) -> None:
        self.aws.fire("detach_internet_gateway", self.region, self.id, InternetGatewayId)


class FakeTaggedVpc(FakeVpc):
    """A Vpc that can be iterated for its tags (a real boto3 Vpc cannot, BUG-012)."""

    def __iter__(self) -> Iterator[Any]:
        assert self.spec.tags is not None
        return iter(self.spec.tags)


class FakeVpcs:
    def __init__(self, aws: FakeAws, region: str) -> None:
        self.aws, self.region = aws, region

    def filter(self, *, Filters: Sequence[Mapping[str, Any]]) -> list[FakeVpc]:
        self.aws.calls.append(("vpcs.filter", self.region, str(list(Filters))))
        outcome = self.aws.case.regions[self.region]
        if isinstance(outcome, Exception):
            raise outcome
        return [
            (FakeTaggedVpc if v.tags is not None else FakeVpc)(self.aws, self.region, v)
            for v in outcome
        ]


class FakeResource:
    def __init__(self, aws: FakeAws, region: str) -> None:
        self.aws, self.region = aws, region
        self.vpcs = FakeVpcs(aws, region)

    def DhcpOptions(self, id: str) -> Any:
        self.aws.calls.append(("DhcpOptions", self.region, id))
        return types.SimpleNamespace(
            associate_with_vpc=lambda *, VpcId: self.aws.fire(
                "associate_with_vpc", self.region, VpcId, VpcId
            )
        )

    def VpcPeeringConnection(self, id: str) -> Any:
        self.aws.calls.append(("VpcPeeringConnection", self.region, id))
        return Leaf(self.aws, self.region, "peering", id)


@dataclass
class Outcome:
    calls: list[tuple[Any, ...]]
    deleted: list[str] | None
    count: int | None
    logs: list[tuple[str, int, str]]
    stdout: str
    error: tuple[str, str] | None


def _load_oracle() -> Any:
    module = types.ModuleType("legacy_aws_vpc")
    exec(compile(ORACLE.read_text(), str(ORACLE), "exec"), module.__dict__)
    return module


def _legacy(case: Case) -> Any:
    return _load_oracle().VPC(case.dry_run, case.filter_tags, case.exception_tags, case.notags)


def _new(case: Case) -> Any:
    return VPC(case.dry_run, case.filter_tags, case.exception_tags, case.notags)


def _setup(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> FakeAws:
    aws = FakeAws(case)
    monkeypatch.setattr("boto3.client", aws.client)
    monkeypatch.setattr("boto3.resource", aws.resource)
    monkeypatch.setattr("boto3.session.Session", aws.session)
    monkeypatch.setattr("crc.service.init_logging", lambda *args, **kwargs: None)
    caplog.clear()
    capsys.readouterr()
    return aws


def _run(
    build: Callable[[Case], Any],
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> Outcome:
    aws = _setup(case, monkeypatch, caplog, capsys)
    cleaner = build(case)
    error: tuple[str, str] | None = None
    try:
        cleaner.delete()
    except Exception as e:
        error = (type(e).__name__, str(e))
    deleted = list(cleaner.get_deleted)
    count = cleaner.delete_count
    logs = [(r.name, r.levelno, r.getMessage()) for r in caplog.records]
    return Outcome(aws.calls, deleted, count, logs, capsys.readouterr().out, error)


@pytest.fixture
def debug_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_matches_main(
    case: Case,
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    new = _run(_new, case, monkeypatch, caplog, capsys)
    assert new.calls == old.calls
    assert new.error == old.error
    assert new.deleted == old.deleted
    assert new.count == old.count
    assert new.logs == old.logs
    assert new.stdout == old.stdout


def _run_cleaner(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> Outcome:
    aws = _setup(case, monkeypatch, caplog, capsys)
    ctx = RunContext(
        dry_run=case.dry_run,
        operation=Operation.DELETE,
        options={
            "filter_tags": case.filter_tags,
            "exception_tags": case.exception_tags,
            "notags": case.notags,
        },
        now=NOW,
    )
    cleaner = get("aws", "vpc")(ctx)
    error: tuple[str, str] | None = None
    deleted: list[str] = []
    try:
        plan = cleaner.plan(ctx)
        if case.dry_run:
            deleted = [c.id for c in plan.candidates]
        else:
            deleted = list(cleaner.apply(plan, ctx).succeeded)
    except Exception as e:
        error = (type(e).__name__, str(e))
    logs = [(r.name, r.levelno, r.getMessage()) for r in caplog.records]
    return Outcome(aws.calls, deleted, len(deleted), logs, capsys.readouterr().out, error)


def _sdk(calls: Sequence[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    return [c for c in calls if c[0] not in ("boto3.client", "boto3.resource")]


def _by_region(calls: Sequence[tuple[Any, ...]]) -> dict[str, list[tuple[Any, ...]]]:
    grouped: dict[str, list[tuple[Any, ...]]] = {}
    for call in _sdk(calls):
        grouped.setdefault(call[1], []).append(call)
    return grouped


def _without_skip_warnings(logs: Sequence[tuple[str, int, str]]) -> list[tuple[str, int, str]]:
    return [log for log in logs if log[2] != SKIP_WARNING]


def _skip_warnings(logs: Sequence[tuple[str, int, str]]) -> int:
    return sum(1 for log in logs if log[2] == SKIP_WARNING)


def _mutations(calls: Sequence[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    return [c for c in calls if c[0] in MUTATING]


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_cleaner_protocol_matches_main(
    case: Case,
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    new = _run_cleaner(case, monkeypatch, caplog, capsys)
    assert new.error == old.error
    assert new.stdout == old.stdout
    if case.fails_in_plan:
        assert _mutations(new.calls) == []
        return
    if new.error is not None:
        assert _mutations(new.calls) == _mutations(old.calls)
        return
    assert new.deleted == old.deleted
    assert _by_region(new.calls) == _by_region(old.calls)
    old_logs = old.logs[:-1]
    # plan() decides every VPC up front, so after an aborted region the Cleaner path has
    # already logged the "Tags and notags not present" warning of VPCs main never reached.
    assert Counter(_without_skip_warnings(new.logs)) == Counter(_without_skip_warnings(old_logs))
    assert _skip_warnings(new.logs) >= _skip_warnings(old_logs)
    summary = 1 if case.dry_run else 2
    assert new.logs[-summary:] == old_logs[-summary:]
    if len(case.regions) <= 1 and len(next(iter(case.regions.values()), [])) <= 1:
        assert new.logs == old_logs


def _find(case_id: str) -> Case:
    return next(c for c in CASES if c.id == case_id)


def _errors_with(kind: str) -> Callable[[Outcome], bool]:
    return lambda o: o.error is not None and o.error[0] == kind


QUIRKS: Sequence[tuple[str, Callable[[Outcome], bool]]] = [
    (
        "exception-tags-real-vpc-raises-type-error",
        lambda o: (
            o.error == ("TypeError", "'FakeVpc' object is not iterable") and o.deleted == ["vpc-1"]
        ),
    ),
    ("notags-real-vpc-raises-type-error", _errors_with("TypeError")),
    ("both-real-vpc-raises-type-error", _errors_with("TypeError")),
    (
        "exception-tags-dry-run-does-not-raise",
        lambda o: o.error is None and o.deleted == ["vpc-1"],
    ),
    (
        "exception-tags-empty-first-region-raises-in-second",
        lambda o: o.error is not None and o.deleted == ["vpc-2"],
    ),
    ("exception-tags-no-vpcs-no-error", lambda o: o.error is None and o.deleted == []),
    (
        "exception-tags-second-vpc-never-reached",
        lambda o: o.deleted == ["vpc-1", "vpc-2"] and o.error is not None,
    ),
    (
        "tagged-protected-vpc-still-recorded",
        lambda o: (
            o.deleted == ["vpc-1", "vpc-2", "vpc-3"]
            and ("delete_vpc", W2, "vpc-1") not in o.calls
            and ("delete_vpc", W2, "vpc-2") in o.calls
        ),
    ),
    (
        "tagged-dry-run-records-protected",
        lambda o: o.deleted == ["vpc-1", "vpc-2"] and _mutations(o.calls) == [],
    ),
    (
        "tagged-notags-one-key-missing",
        lambda o: (
            [c for c in o.calls if c[0] == "delete_vpc"]
            == [("delete_vpc", W2, "vpc-1"), ("delete_vpc", W2, "vpc-2")]
        ),
    ),
    (
        "tagged-notags-all-keys-present",
        lambda o: [c for c in o.calls if c[0] == "delete_vpc"] == [("delete_vpc", W2, "vpc-2")],
    ),
    (
        "tagged-exception-key-only",
        lambda o: [c for c in o.calls if c[0] == "delete_vpc"] == [],
    ),
    ("tagged-tag-without-key", lambda o: o.error == ("KeyError", "'Key'")),
    ("tagged-tag-without-value", lambda o: o.error == ("KeyError", "'Value'")),
    ("tagged-exception-hit-before-malformed-tag", lambda o: o.error is None),
    ("tagged-malformed-tag-before-exception-hit", lambda o: o.error == ("KeyError", "'Value'")),
    (
        "tagged-no-skip-spec",
        lambda o: (
            ("WARNING", "Tags and notags not present")
            in [(logging.getLevelName(level), msg) for _, level, msg in o.logs]
        ),
    ),
    (
        "delete-vpc-fails-every-retry-swallowed",
        lambda o: (
            o.error is None
            and sum(1 for c in o.calls if c[0] == "delete_vpc") == FIVE + 1
            and o.deleted == ["vpc-1", "vpc-2"]
        ),
    ),
    (
        "delete-vpc-retried-then-succeeds",
        lambda o: sum(1 for c in o.calls if c[0] == "delete_vpc") == THREE_DELETES,
    ),
    (
        "delete-vpc-connection-error-is-retried-not-skipped",
        lambda o: (
            o.error is None and sum(1 for c in o.calls if c[0] == "delete_vpc") == FOUR_DELETES
        ),
    ),
    (
        "security-group-delete-connection-error-skips-region-keeps-ids",
        lambda o: (
            o.deleted == ["vpc-1", "vpc-2", "vpc-3"]
            and ("delete_vpc", W2, "vpc-2") not in o.calls
            and ("delete_vpc", E1, "vpc-3") in o.calls
        ),
    ),
    ("security-group-delete-client-error-propagates", _errors_with("ClientError")),
    ("list-connection-error-skips-region", lambda o: o.deleted == ["vpc-3"]),
    ("list-client-error-propagates", lambda o: o.error is not None and o.deleted == ["vpc-1"]),
    ("describe-regions-client-error-propagates", lambda o: o.error is not None and o.deleted == []),
    ("describe-regions-connection-error-falls-back", lambda o: o.deleted == ["vpc-1"]),
    (
        "default-security-group-kept",
        lambda o: (
            ("sg.delete", W2, "sg-d") not in o.calls and ("delete_vpc", W2, "vpc-1") in o.calls
        ),
    ),
    ("default-acl-kept", lambda o: ("netacl.delete", W2, "acl-d") not in o.calls),
    (
        "main-association-kept-and-route-table-kept",
        lambda o: not any(c[0] in ("rta.delete", "rt.delete") for c in o.calls),
    ),
    (
        "non-main-association-deleted-route-table-kept",
        lambda o: (
            ("rta.delete", W2, "a-1") in o.calls and not any(c[0] == "rt.delete" for c in o.calls)
        ),
    ),
    (
        "route-table-without-associations-deleted",
        lambda o: ("rt.delete", W2, "rtb-1") in o.calls,
    ),
    (
        "missing-vpc-endpoints-key",
        lambda o: o.error == ("KeyError", "'VpcEndpoints'") and o.deleted == ["vpc-1"],
    ),
    ("missing-vpc-endpoint-id", lambda o: o.error == ("KeyError", "'VpcEndpointId'")),
    (
        "missing-peering-connections-key",
        lambda o: o.error == ("KeyError", "'VpcPeeringConnections'"),
    ),
    ("missing-peering-id", lambda o: o.error == ("KeyError", "'VpcPeeringConnectionId'")),
    ("empty-results", lambda o: o.deleted == [] and _mutations(o.calls) == []),
    ("same-vpc-id-two-regions", lambda o: o.deleted == ["vpc-1", "vpc-1"]),
]


@pytest.mark.parametrize(("case_id", "check"), QUIRKS, ids=[q[0] for q in QUIRKS])
def test_quirk_is_preserved(
    case_id: str,
    check: Callable[[Outcome], bool],
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = _find(case_id)
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    new = _run(_new, case, monkeypatch, caplog, capsys)
    assert check(old), "oracle no longer shows the quirk"
    assert check(new)


SKIP_TAGS: Sequence[Any] = [
    ENV,
    [],
    PROD,
    tag_list({"owner": "qa", "env": "test"}),
    [{"Value": "v"}],
    [{"Key": "owner"}],
    [{"Key": "a", "Value": "1"}, {"Key": "a", "Value": "2"}],
]
SKIP_SPECS: Sequence[tuple[Any, Any]] = [
    ({}, {}),
    (None, None),
    ({"env": []}, {}),
    ({"owner": ["qa"]}, {}),
    ({"owner": ["x"]}, {}),
    ({}, {"env": []}),
    ({}, {"env": [], "owner": ["qa"]}),
    ({}, {"a": ["2"]}),
    ({"env": []}, {"env": []}),
]


def _skip_outcome(obj: Any, tags: Any) -> tuple[str, Any]:
    try:
        return ("ok", obj._should_skip_vpc(tags))
    except Exception as e:
        return (type(e).__name__, str(e))


@pytest.mark.parametrize("tags", SKIP_TAGS, ids=range(len(SKIP_TAGS)))
@pytest.mark.parametrize("spec", SKIP_SPECS, ids=range(len(SKIP_SPECS)))
def test_should_skip_vpc_matches_main(
    spec: tuple[Any, Any], tags: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("crc.service.init_logging", lambda *args, **kwargs: None)
    exception_tags, notags = spec
    old = _load_oracle().VPC(False, {}, exception_tags, notags)
    new = VPC(False, {}, exception_tags, notags)
    assert _skip_outcome(new, tags) == _skip_outcome(old, tags)
    assert _skip_outcome(new, object()) == _skip_outcome(old, object())


@pytest.mark.parametrize(
    "filter_tags", [{}, None, {"a": []}, {"a": ["1", "2"], "b": ["3"]}], ids=range(4)
)
def test_get_filter_matches_main(filter_tags: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("crc.service.init_logging", lambda *args, **kwargs: None)
    old = _load_oracle().VPC(False, filter_tags, {}, {})
    new = VPC(False, filter_tags, {}, {})
    assert new._get_filter() == old._get_filter()


def test_get_vpc_ids_matches_main(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("crc.service.init_logging", lambda *args, **kwargs: None)
    vpcs = [types.SimpleNamespace(id=i) for i in ("a", "b", "c")]
    assert VPC(False, {}, {}, {}).get_vpc_ids(vpcs) == _load_oracle().VPC(
        False, {}, {}, {}
    ).get_vpc_ids(vpcs)


def test_registered_factory_builds_cleaner() -> None:
    ctx = RunContext(dry_run=True, operation=Operation.DELETE, options={}, now=NOW)
    cleaner: Cleaner = get("aws", "vpc")(ctx)
    assert isinstance(cleaner, VpcCleaner)
    assert get("aws", "vpc") is build_vpc_cleaner
    assert (cleaner.cloud, cleaner.kind) == ("aws", "vpc")


def test_runtime_injection_replaces_boto3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("crc.service.init_logging", lambda *args, **kwargs: None)
    case = _find("delete-two-regions")
    aws = FakeAws(case)
    monkeypatch.setattr("boto3.client", lambda *a, **k: pytest.fail("boto3 used"))
    monkeypatch.setattr("boto3.resource", lambda *a, **k: pytest.fail("boto3 used"))
    runtime = VpcRuntime(
        client_factory=lambda region: FakeClient(aws, region),
        resource_factory=lambda region: FakeResource(aws, region),
        list_regions=lambda: list(case.regions),
    )
    vpc = VPC(False, {}, {}, {}, runtime)
    vpc.delete()
    assert vpc.get_deleted == ["vpc-1", "vpc-2", "vpc-3"]
    assert vpc.delete_count == len(vpc.get_deleted)
