# crc.aws.vpc

## Purpose

AWS VPC cleaner (AWS-1, subpackage since ST-AWS-VPC). Tears down and deletes the VPCs of every region that match an EC2 `tag:key` filter and are not protected by `exception_tags` / `notags`. The old import path `crc.aws.vpc` keeps working: the package `__init__.py` re-exports every public name, including the `VPC` class `crc.py` uses.

Modules:

- `cleaner.py`: `VpcCleaner` (`plan()` lists and decides protection, `apply()` tears down and deletes), its registry factory, the runtime dataclass and the `VPC` adapter.
- `teardown.py`: `tear_down()`, the detach and delete steps of everything a VPC owns, in the order of `main` (moved out of `cleaner.py`, no behaviour change).
- `client.py`: typed Protocols of the boto3 EC2 client, resource and resource objects the VPC teardown walks.

## Public API

- `VPC`: adapter that crc.py calls (delete(), get_deleted, delete_count, vpc_ids_to_delete, get_vpc_ids); runs the cleaner region by region, VPC by VPC, in the order of main.
- `VpcCleaner`: the Cleaner (supports, read-only plan, mutating apply, plus build_filter, list_vpcs, plan_region, apply_region, is_protected, should_skip and delete_vpc used by the adapter).
- `VpcRuntime`: frozen dataclass with the injected client factory, resource factory and region lister of VPC (no clock: the module has no age gate).
- `build_vpc_cleaner()`: registry factory behind @register("aws", "vpc"); reads filter_tags, exception_tags and notags from RunContext.options.
- `tear_down()`: (teardown.py) detaches and deletes the gateways, route tables, instances, endpoints, security groups, peering connections, network ACLs, subnets and interfaces of one VPC, in the order of the original implementation; called by the VPC cleaner before it deletes the VPC.
- `Deletable`, `Collection`, `InternetGateway`, `RouteTableAssociation`, `RouteTable`, `Instance`, `Subnet`, `SecurityGroup`, `NetworkAcl`, `DhcpOptionsHandle`, `Vpc`, `VpcCollection`: Protocols (client.py) of the boto3 EC2 resource objects the VPC teardown walks; tests pass fakes.
- `Ec2Resource`, `Ec2Client`: Protocols (client.py) of the boto3 EC2 resource (vpcs.filter, DhcpOptions, VpcPeeringConnection) and the four client calls used (describe_vpc_endpoints, delete_vpc_endpoints, describe_vpc_peering_connections, delete_vpc).

## Safety Invariants

- `plan()` only lists (`ec2.vpcs.filter` with the server side `tag:key` filter) and decides protection; every mutating call (`associate_with_vpc`, gateway, route table, instance, endpoint, security group, peering, ACL, interface, subnet and `delete_vpc`) is in `apply()` / `apply_region()` / `VpcCleaner.delete_vpc()` and never runs in a dry run. There is no age gate and no default-VPC guard, so an empty `filter_tags` selects every VPC of every region (BUG-083); only the EC2 tag filter, `exception_tags` and `notags` protect a VPC. Protection (`policy.exception_tags_any`, `policy.notags_all`) is evaluated on the boto3 Vpc resource, not on its tags, so with `exception_tags` or `notags` set a real run raises `TypeError` before anything is deleted and every listed VPC, protected or not, is recorded as deleted (BUG-012). Connectivity errors skip the region (SAF-001, `describe_regions` falls back to the static list, other errors propagate, SAF-004); every other error propagates and nothing is isolated per VPC (CLEANUP_SAFETY.md); only `delete_vpc` is retried 5 times and its errors are swallowed. `VPC.delete()` keeps the call order of `main` (per region: client, resource, listing, then for each VPC protection check and teardown). The Cleaner path decides protection for a whole region in `plan()`, so after an aborted region it logs the `Tags and notags not present` warning of VPCs `main` never reached. Quirks: BUG-012, BUG-021 and BUG-083 to BUG-095 (AWS-1).
- Do not call real cloud APIs in tests; mock every SDK client.

## Extension Rules

1. No behaviour change in a refactor rung; behaviour fixes are separate rungs (`docs/refactor/PLAN.md`).
2. Keep filtering in `crc.core.policy`; no clock or environment reads here (ARCH-003).
3. Keep every mutating call in `apply()` / `apply_region()` / `VpcCleaner.delete_vpc()`; `plan()` and what it calls stay read-only (ARCH-005).
4. Changes need `tests/test_aws_vpc_parity.py` (oracle `tests/legacy/aws_vpc.txt`, a frozen copy of `main`, never edit it) and `tests/test_aws_vpc.py` to stay green.
5. Every file stays under 400 lines; split by responsibility and keep this README in sync with the public names.
