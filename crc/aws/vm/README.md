# crc.aws.vm

## Purpose

AWS EC2 VM cleaner. `VmCleaner` is a registered `Cleaner` (`@register("aws", "vm")`): `plan()` lists the running instances of every region (`describe_instances`, `describe_network_interfaces`) and applies the tag and age policy; `apply()` calls `terminate_instances` or `stop_instances`. `crc/cli` runs it through `crc.core.engine`; `scan_tag()` serves the `--scan_tag` scan (`crc/cli/scan.py`).

Modules, one per responsibility:

- `cleaner.py`: `VmCleaner` (plan/apply) and the registry factory; re-exports the names of the modules below.
- `settings.py`: `VmSettings` (with `require_age`), constants, default client and region factories, `settings_from_options()`.
- `judge.py`: `InstanceJudge`, the read-only decision on each instance (tags, Name tag, age).
- `reporting.py`: region, dry-run and summary logging.
- `client.py`: typed Protocols of the boto3 EC2 calls used by the cleaner and the tag scan.
- `scan.py`: the read-only scan of instances by tag across regions.

## Public API

- `VmCleaner`: the Cleaner (supports, read-only plan, mutating apply, plus build_filters, filter_instances, apply_region and should_skip; the decisions are delegated to InstanceJudge).
- `InstanceJudge`: (judge.py) read-only decision per instance: should_skip, filter_instances.
- `settings_from_options()`: (settings.py) builds VmSettings from RunContext.options.
- `summarise()`: (reporting.py) closing summary of a run from the deleted or stopped names.
- `VmSettings`: frozen dataclass with the filters and thresholds of one run (filter_tags, exception_tags, notags, age, custom_age_tag_key, instance_state).
- `build_vm_cleaner()`: registry factory behind @register("aws", "vm"); reads filter_tags, exception_tags, notags, age, custom_age_tag_key and instance_state from RunContext.options.
- `boto3_client()`: default client factory of the VM cleaner (boto3.client("ec2", region_name=...)).
- `all_regions()`: default region lister of the VM cleaner (get_all_regions("ec2", "us-west-2")).
- `skip_region()`: logs a region skipped for a connectivity error (context "ec2 vm <operation>").
- `note_region()`: reports a listed and filtered region to the current logger (REGION line: scanned, selected, seconds).
- `note_dry_run()`: reports one would_delete or would_stop ACTION per instance a dry run lists.
- `log_summary()`: the closing warnings of a VM run (no instances, number and list deleted or stopped, or the dry-run list).
- `dry_run_names()`: what a VM dry run records, names for delete but instance ids for stop.
- `VmClient`: Protocol of the four EC2 calls used, describe_instances, describe_network_interfaces, terminate_instances and stop_instances; tests pass fakes.
- `VmPaginator`: Protocol of the describe_instances paginator (paginate) used by the tag scan.
- `VmScanClient`: Protocol of the EC2 client surface of the tag scan (get_paginator, exceptions).
- `VmSession`: Protocol of the boto3 session call of the tag scan (get_available_regions).
- `instance_name()`: value of the first Name tag of an instance, or None.
- `scan_tag()`: (scan.py) find_instances_with_tag with the default boto3 session, clients and region lister; what the scan_tag flag calls for AWS.
- `find_instances_with_tag()`: read-only scan of instances carrying a tag in every region.
- `ClientFactory`, `RegionLister`, `ScanClientFactory`, `SessionFactory`, `Filters`: type aliases of the injected factories and of the describe_instances filter list.
- `SERVICE_NAME`, `DEFAULT_REGION`, `DEFAULT_STATE`: constants (the service ec2, the region us-west-2 of the region lookup, and the default instance state running).

## Safety Invariants

- `plan()` only calls `describe_instances` and `describe_network_interfaces`; `terminate_instances` and `stop_instances` are called from `apply()` / `apply_region()` only and never in a dry run.
- The age gate is the attach time of the first network interface against `--age` or the fail-closed custom age tag (`get_retention_age`; a present but bad tag keeps the instance, BUG-004) (`core.age.is_old`, `policy.effective_age`); `MAX_AGE` can shorten it (SAF-006 to SAF-010, BUG-004). BUG-213 (fixed in G-AWS, SAF-011): `build_vm_cleaner` raises `ValueError` naming `--age` for a real run (not a dry run) without a positive age, before any cloud call; a custom age tag still replaces `--age` per instance (it never replaces the requirement for `--age`, and `VmSettings.require_age` keeps any instance with no effective age). A dry run without an age still matches every instance.
- Fail closed: any error while judging one instance is logged and keeps it; exception tags (any key) and notags skip an instance; an instance without a `Tags` key or `Name` tag is kept. Termination and stop errors are isolated per instance.
- Region errors: a `describe_instances` connectivity error skips the region (SAF-001); `describe_regions` falls back to the static list on a connectivity error only and every other error propagates (SAF-004).
- `plan()` lists every region before `apply()` mutates.
- Quirks are in `docs/refactor/BUGS.md` (BUG-004, BUG-020, BUG-021, BUG-214 to BUG-227, BUG-351 (BUG-213 is fixed)).
- Logging (BUG-358/361/362): the raw tag list of each instance is logged at DEBUG; REGION, ACTION (deleted/stopped, would_* in a dry run), skip counts (`no_tags`, `exception_tag`, `too_young`) and failures (processing errors, missing Name tag, failed terminate/stop) go to `crc.core.logger.current()`.

## Extension Rules

1. Behaviour changes need a `BUG-NNN` row in `docs/refactor/BUGS.md` and a test that fails without them.
2. Keep filtering in `crc.core.policy` and age in `crc.core.age`; no `re`, clock reads or environment reads here (ARCH-003): `now` comes from `RunContext`.
3. Keep every mutating call in `apply_region()`; `plan()` and what it calls stay read-only (ARCH-005).
4. Changes need `tests/test_aws_vm_parity.py` (oracle `tests/legacy/aws_vm.txt`, never edit it) and `tests/test_aws_vm.py` to stay green.
5. Every file stays at most 250 lines; split by responsibility and re-export every public name in `__init__.py` (and `__all__`).
