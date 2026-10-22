# crc.aws

## Purpose

AWS cleaners (boto3). They delete or stop leftover EC2, EBS, network, key and KMS resources across regions.

**Refactored.** Every module is strict-checked and none is in the legacy ratchet. `_base.py`, `connectivity.py` and `__init__.py` (PL-AWS, strict typing and parity oracles, no behaviour change; they are not `Cleaner`s and register nothing) are shared plumbing; `keypairs.py` (R4a), `elastic_ips.py` (R4b), `kms.py` (R4c), `snapshot.py` (AWS-2), `vpc/` (AWS-1, subpackage since ST-AWS-VPC), `vm/` (AWS-3, subpackage since ST-AWS-VM), `disk/` (AWS-4) and `spot_instance_requests/` (AWS-5) are `Cleaner`s registered under `aws` (no behaviour change). The adapter classes (`KeyPairs`, `ElasticIPs`, `Kms`, `Snapshot`, `VPC`, `VM`, `Disk`, `SpotInstanceRequests`) remain the entry point `crc/cli` uses. Any change must follow `ARCHITECTURE.md`; a behaviour change needs a behaviour-fix rung in `docs/refactor/PLAN.md` (`.cursor/skills/crc-python-refactor/BEHAVIOUR_FIX.md`), with `make check` green.

Modules (operations and filters as catalogued in `docs/refactor/FILTER_VARIANTS.md`):

- `_base.py`: `get_all_regions` (region list via `describe_regions`, static botocore list on connectivity errors; refactored, PL-AWS)
- `_regionlog.py`: `start`, `region_done` and `region_skipped`, thin wrappers over `crc.core.logger.current()` with an injectable clock for the `REGION` line (LOG-AWS2; log text only)
- `connectivity.py`: `CONNECTIVITY_ERRORS` and `log_skipped_region` (unreachable regions are logged and skipped; refactored, PL-AWS)
- `disk/`: package (`__init__.py` keeps the import path `crc.aws.disk`): `DiskCleaner` (registered as `aws` / `disk`) and the `Disk` adapter: deletes available EBS volumes (`delete_volume`); tags, regex, `yb_task` and Kubernetes skips, CloudWatch detach age; opt-in `--cloud aws --resource disk`. See `crc/aws/disk/README.md`
- `elastic_ips.py`: `ElasticIpCleaner` (registered as `aws` / `ip`) and the `ElasticIPs` adapter: releases unassociated, tagged Elastic IPs (`release_address`)
- `keypairs.py`: `KeyPairCleaner` (registered as `aws` / `keypair`) and the `KeyPairs` adapter: deletes key pairs by name regex (`delete_key_pair`)
- `kms.py`: `KmsCleaner` (registered as `aws` / `kms`) and the `Kms` adapter: schedules key deletion (`schedule_key_deletion`) for keys matching description and user ARN
- `snapshot.py`: `SnapshotCleaner` (registered as `aws` / `snapshot`) and the `Snapshot` adapter: deletes snapshots older than `age_days` (`delete_snapshot`) that carry no exception tag and back no AMI
- `spot_instance_requests/`: package (`__init__.py` keeps the import path `crc.aws.spot_instance_requests`): `SpotRequestCleaner` (registered as `aws` / `spot_instance_requests`) and the `SpotInstanceRequests` adapter: cancels spot requests, terminates instances, deletes leftover volumes. See `crc/aws/spot_instance_requests/README.md`
- `vm/`: package (`__init__.py` keeps the import path `crc.aws.vm`): `VmCleaner` (registered as `aws` / `vm`; terminates (`terminate_instances`) or stops (`stop_instances`) running instances), the `VM` adapter (`delete()`, `stop()`, `find_instances_with_tag()`) with its `VmRuntime`, the typed EC2 Protocols and the read-only tag scan. See `crc/aws/vm/README.md`
- `vpc/`: package (`__init__.py` keeps the import path `crc.aws.vpc`): `VpcCleaner` (registered as `aws` / `vpc`) and the `VPC` adapter: tears down and deletes VPCs matching an EC2 `tag:key` filter (`delete_vpc`). See `crc/aws/vpc/README.md`

## Public API

Every module is refactored. The modules directly in this package (_base.py, connectivity.py, keypairs.py, elastic_ips.py, kms.py and snapshot.py) are synced with this README by ARCH-002; the disk, spot_instance_requests, vm and vpc subpackages list their names in their own READMEs.
__init__.py holds only a copyright comment and declares no __all__ (no export list).

- `get_all_regions()`: (_base.py) regions of an AWS service from describe_regions on a client in the default region; a connectivity error falls back to the static botocore region list (Session get_available_regions), every other error propagates.
- CONNECTIVITY_ERRORS (connectivity.py, a constant tuple, not a symbol): the botocore network errors (ConnectionError and its subclasses, ReadTimeoutError, ConnectionClosedError) that skip a region instead of failing; auth errors are not in it.
- `log_skipped_region()`: (connectivity.py) logs the WARNING of a region skipped for a connectivity error (region, context, error type and text).
- `start()`, `region_done()`, `region_skipped()`: (_regionlog.py) timestamp for a region, the REGION run-log line (resources listed, selected, seconds) and the skipped-region count of the core run log; they never decide what is selected or deleted.

- `KeyPairClient`: Protocol of the two EC2 calls used, describe_key_pairs and delete_key_pair; tests pass fakes.
- `KeyPairRuntime`: frozen dataclass with the injected client factory, region lister and clock (now) of KeyPairs.
- `KeyPairCleaner`: the Cleaner (supports, read-only plan, mutating apply, plus per-region plan_region and apply_region).
- `build_keypair_cleaner()`: registry factory behind @register("aws", "keypair"); reads name_regex, exception_regex and age from RunContext.options.
- `KeyPairs`: adapter crc.py calls (delete(), get_deleted, count, deleted_keypairs); runs the cleaner region by region in the order of main.
- `ElasticIpClient`: Protocol of the two EC2 calls used, describe_addresses and release_address; tests pass fakes.
- `ElasticIpRuntime`: frozen dataclass with the injected client factory and region lister of ElasticIPs (no clock: the module has no age gate).
- `ElasticIpCleaner`: the Cleaner (supports, read-only plan, mutating apply, plus per-region plan_region and apply_region).
- `build_elastic_ip_cleaner()`: registry factory behind @register("aws", "ip"); reads filter_tags, exception_tags and notags from RunContext.options.
- `ElasticIPs`: adapter crc.py calls (delete(), get_deleted, count, deleted_ips); runs the cleaner region by region in the order of main.
- `KmsClient`: Protocol of the five KMS calls used (get_paginator, list_resource_tags, describe_key, get_key_policy, schedule_key_deletion); tests pass fakes.
- `KmsPaginator`: Protocol of the list_keys paginator (paginate).
- `KmsRuntime`: frozen dataclass with the injected client factory and clock (now) of Kms.
- `KmsSettings`: frozen dataclass with the filters and thresholds of one run (filter_tags, exception_tags, description, user, pending window, age, custom age tag key).
- `KmsCleaner`: the Cleaner (supports, read-only plan, mutating apply, plus connect_and_list, plan_keys and schedule used by the adapter).
- `build_kms_cleaner()`: registry factory behind @register("aws", "kms"); reads filter_tags, exception_tags, kms_key_description, kms_user, kms_pending_window, age and custom_age_tag_key from RunContext.options.
- `Kms`: adapter crc.py calls (delete(), get_deleted, kms_keys_to_delete); runs the cleaner in the order of main.
- `SnapshotClient`: Protocol of the three EC2 calls used, describe_snapshots, describe_images and delete_snapshot; tests pass fakes.
- `SnapshotRuntime`: frozen dataclass with the injected client factory, region lister and clock (now, UTC) of Snapshot.
- `SnapshotCleaner`: the Cleaner (supports, read-only plan, mutating apply, plus check_age, iter_region, delete_one and is_protected used by the adapter).
- `build_snapshot_cleaner()`: registry factory behind @register("aws", "snapshot"); reads age_days (default 90, the value crc.py passes) and exception_tags from RunContext.options.
- `Snapshot`: adapter crc.py calls (delete(), get_deleted, delete_count, deleted_snapshots); runs the cleaner region by region, snapshot by snapshot, in the order of main.

### spot_instance_requests

Refactored in AWS-5 into the subpackage crc/aws/spot_instance_requests/ (its own README lists the names). The old import path still works: crc.py imports SpotInstanceRequests, a thin adapter over the registered Cleaner (cloud aws, kind spot_instance_requests) with the same constructor, delete(), get_deleted, delete_count, spot_requests_to_delete, instance_ids_to_delete and volumes_to_delete.

Safety Invariants: plan() only describes (a dry run is plan() alone and also performs the reads of the real path); tag, cancel, terminate, wait and delete happen in apply() in the order of CLEANUP_SAFETY.md (SAF-028 to SAF-033). Cancel before terminate; a failed tag or cancel aborts; leftover volumes are protected by their own exception, notags and sensitive yb_task tags; untagged requests and bad custom age tags are kept. Quirks: BUG-004, BUG-021, BUG-022 and BUG-272 to BUG-281 (docs/refactor/BUGS.md).

Extension Rules: changes need tests/test_aws_spot_instance_requests_parity.py (oracle tests/legacy/aws_spot_instance_requests.txt, a frozen copy of main, never edit it), tests/test_aws_spot_instance_requests_cleaner.py and tests/test_aws_spot_instance_requests.py to stay green.

### disk

The EBS disk cleaner is a subpackage with its own README (crc/aws/disk/README.md) that lists its public names. It exports the Cleaner (registered as aws / disk), the adapter class that crc.py calls, the runtime dataclass and the registry factory, with the typed Protocols of the boto3 EC2 and CloudWatch calls it uses in client.py.

## Safety Invariants

- Authoritative text: `CLEANUP_SAFETY.md`. Applicable IDs: SAF-006 to SAF-013 (age gates, shared by all clouds), SAF-001, SAF-003, SAF-004, SAF-014, SAF-015, SAF-016, SAF-017 to SAF-020, SAF-025, SAF-026 to SAF-037.
- Filtering per module: `docs/refactor/FILTER_VARIANTS.md` sections 2 (AWS), 6 and 7.
- Known bugs and quirks (do not fix outside a behaviour-fix rung): `docs/refactor/BUGS.md` rows `BUG-004`, `BUG-007`, `BUG-008`, `BUG-011`, `BUG-012`, `BUG-014`, `BUG-015`, `BUG-020`, `BUG-021`, `BUG-022`, `BUG-045` to `BUG-061`, and for snapshots `BUG-009`, `BUG-040` and `BUG-142` to `BUG-157`.
- The AWS EBS cleanup needs `--cloud aws --resource disk` (SAF-014); spot requests cancel before terminate (SAF-028).
- Key pairs (R4a): `plan()` only calls `describe_key_pairs`; `delete_key_pair` is called from `apply_region()` only and never in a dry run (SAF-006: an empty age matches every key pair, an empty `name_regex` matches every name). `KeyPairs.delete()` keeps the call order of `main`: per region client, describe, then deletes, so a later region error leaves earlier regions deleted. Connectivity errors skip the region; every other error propagates (CLEANUP_SAFETY.md). Quirks in `BUGS.md`: `BUG-045` to `BUG-050`.
- Elastic IPs (R4b): `plan()` only calls `describe_addresses`; `release_address` is called from `apply_region()` only and never in a dry run. Only addresses without `NetworkInterfaceId` that carry a `Tags` key are considered (BUG-020); there is no age gate (BUG-053), so the filter is the only protection: an empty `filter_tags` selects every tagged unassociated address. Exception tags skip an address (`policy.aws_eip_should_skip`); `notags` raises `TypeError` as on `main` (BUG-011). Connectivity errors skip the region (SAF-001, `describe_regions` falls back to the static list, other errors propagate, SAF-004); every other error propagates and nothing is isolated per address (CLEANUP_SAFETY.md). `ElasticIPs.delete()` keeps the call order of `main` (per region: client, describe, releases). Quirks in `BUGS.md`: `BUG-051` to `BUG-054`.
- KMS (R4c): `plan()` calls `list_keys`, `list_resource_tags`, `describe_key` and `get_key_policy` only; `schedule_key_deletion` is called from `apply()` (and `Kms.delete()`) only and never in a dry run. Works in `us-west-2` only; a connectivity error there skips the cleanup (SAF-001) and every other list error propagates (SAF-004). Per key, any error skips that key (a bare `except:` on `main`, so keeping it fails closed, but a key found for the user before the error stays selected: BUG-055); the age gate is `is_old` with the fail-open custom age tag that never matches (SAF-006 to SAF-010, BUG-004, BUG-008) and `filter_tags` needs every (key, value) pair (`policy.filter_tags_exact_pairs`, BUG-007, BUG-014); exception tags raise `KeyError` for any tagged key and so skip it (BUG-015). The same key can be scheduled twice and a scheduling error aborts the run without a record of earlier keys (BUG-056, BUG-057). Quirks in `BUGS.md`: `BUG-055` to `BUG-061`.
- VPC (AWS-1): see `crc/aws/vpc/README.md` (Safety Invariants). `plan()` only lists, every mutating call is in `apply()` and never in a dry run; there is no age gate or default-VPC guard (BUG-083). Quirks: BUG-012, BUG-021 and BUG-083 to BUG-095 (AWS-1).
- Snapshots (AWS-2): `plan()` only calls `describe_snapshots` and `describe_images`; `delete_snapshot` is called from `apply()` (and `Snapshot.delete()` through `SnapshotCleaner.delete_one`) only and never in a dry run (the age gate is its own day cutoff, not `is_old`, so SAF-006 to SAF-013 do not apply as written: `policy.snapshot_old_enough`, a snapshot started exactly at `now - age_days` is old, `StartTime` after it is kept; a non-numeric or overflowing `age_days` raises `TypeError` / `OverflowError` before any call, while `0`, negative or `True` are accepted by `timedelta`). There is no `filter_tags`, `notags` or name filter, so every old snapshot of the account in every region is selected unless an exception tag (`policy.exception_tags_any`, one key at a time so the log names the first matching key and a tag entry without `Key` or `Value` raises `KeyError` as on `main`) or an AMI of the same account and region protects it (`BUG-142` to `BUG-157` in `docs/refactor/BUGS.md`). Connectivity errors skip the region (SAF-001, `describe_regions` falls back to the static list, other errors propagate, SAF-004); every other listing error propagates and nothing is isolated per snapshot; a `delete_snapshot` error of any kind is logged and swallowed and the snapshot is not recorded. `Snapshot.delete()` keeps the call order of `main` (per region: client, `describe_snapshots`, `describe_images`, then per snapshot the decision and the delete). The Cleaner path lists every region before deleting, so a listing error in a later region is raised before earlier regions are deleted, and its skip log lines come before the `Deleted snapshot` lines. Quirks: `BUG-009`, `BUG-040`, `BUG-142` to `BUG-157`.
- VMs (AWS-3, subpackage `vm/` since ST-AWS-VM): see `crc/aws/vm/README.md`; `plan()` is read-only, `terminate_instances` and `stop_instances` run from `apply()` only and never in a dry run. Quirks: BUG-004, BUG-020, BUG-021, BUG-213 to BUG-227 and BUG-351.
- Region listing and connectivity (PL-AWS): `get_all_regions` only calls `describe_regions` (read-only) and, on a connectivity error only (`CONNECTIVITY_ERRORS`, SAF-001), the static `get_available_regions`; every other error, including auth errors, propagates and is never replaced by a default region list (SAF-004), so a credential problem can not look like an unreachable region here. `CONNECTIVITY_ERRORS` must not grow to include `ClientError` or `NoCredentialsError`. The log lines, the single `describe_regions` call and the call order are those of `main`. Quirks: `BUG-302` to `BUG-307` in `docs/refactor/BUGS.md`.
- Logging (LOG-AWS2, BUG-357, BUG-359, BUG-361, BUG-362): KMS `KEY SKIPPED`, snapshot `used by AMI` / `has tag`, keypair `not old enough` / `in exception_regex` lines are DEBUG with unchanged text; the cleaners report `REGION`, `ACTION` (`would_delete` / `deleted`), skip counts (`too_young`, `exception_tag`, `used_by_ami`, `not_ours`, `unreadable`, ...) and failures to `crc.core.logger.current()`. Selection, deletion order and SDK calls are unchanged.
- Do not call real cloud APIs in tests; mock every SDK client.

### disk

- AWS-4: plan() only lists volumes (describe_volumes paginator) and queries CloudWatch get_metric_data; delete_volume is called from apply() and apply_region() only and never in a dry run. An age gate is required (age and/or detach_age), CreateTime is compared with a floor that ignores MAX_AGE, and a missing CreateTime, invalid custom age tag or incomplete CloudWatch answer keeps the volume (SAF-006, SAF-010, SAF-014). Region errors never propagate (connectivity and unauthorised errors skip the region, others are logged and flagged); describe_regions errors other than connectivity propagate. The adapter keeps the call and log order of main; the Cleaner path lists every region before deleting. Quirks: BUG-004, BUG-022 and BUG-282 to BUG-296 (`docs/refactor/BUGS.md`).

## Extension Rules

1. Do not edit these modules to add features or fix bugs in a refactor rung; behaviour fixes are
   separate rungs (`docs/refactor/PLAN.md`).
2. New resources go in a new module with the `Cleaner` contract (`ARCHITECTURE.md`, "Add a resource
   cleaner"): a `Cleaner` registered with `@register("aws", ...)`, as every module here is.
3. VM changes need `tests/test_aws_vm_parity.py` (oracle `tests/legacy/aws_vm.txt`, a frozen copy of `main`, never edit it) and `tests/test_aws_vm.py` to stay green; snapshot changes need `tests/test_aws_snapshot_parity.py` (oracle `tests/legacy/aws_snapshot.txt`, a frozen copy of `main`, never edit it) and `tests/test_aws_snapshot.py` to stay green; VPC changes: see `crc/aws/vpc/README.md`; key pair changes need `tests/test_aws_keypair_parity.py` (oracle `tests/legacy/aws_keypairs.txt`, a frozen copy of `main`, never edit it) to stay green; Elastic IP changes need `tests/test_aws_ip_parity.py` (oracle `tests/legacy/aws_elastic_ips.txt`); KMS changes need `tests/test_aws_kms_parity.py` (oracle `tests/legacy/aws_kms.txt`); `_base.py` changes need `tests/test_aws_base_parity.py` and `tests/test_aws_base.py` (oracle `tests/legacy/aws_base.txt`), `connectivity.py` changes need `tests/test_aws_connectivity_parity.py` and `tests/test_aws_connectivity.py` (oracle `tests/legacy/aws_connectivity.txt`); oracles are frozen copies of `main`, never edit them.
4. No module of this package is in the legacy ratchet (`pyproject.toml` lists only five hash-locked safety test files); never add one. Update this README and its symbol list with any public change.
5. Record every new bug or quirk in `docs/refactor/BUGS.md` with its `main` file:line.

### disk

Disk changes need tests/test_aws_disk_parity.py (oracle tests/legacy/aws_disk.txt, a frozen copy of main, never edit it), tests/test_aws_disk_cleaner.py and tests/test_aws_disk.py to stay green.

### vpc

The VPC cleaner is a subpackage with its own README (`crc/aws/vpc/README.md`) that lists its public names. It exports the Cleaner (registered as aws / vpc), the `VPC` adapter that crc.py calls, the runtime dataclass, the registry factory and the typed Protocols of the boto3 EC2 client, resource and resource objects (in `client.py`).
