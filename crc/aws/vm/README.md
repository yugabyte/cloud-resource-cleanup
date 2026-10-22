# crc.aws.vm

## Purpose

AWS EC2 VM cleaner (rung AWS-3, moved into a subpackage by ST-AWS-VM). `VmCleaner` is a registered `Cleaner` (`@register("aws", "vm")`): `plan()` lists the running instances of every region (`describe_instances`, `describe_network_interfaces`) and applies the tag and age policy; `apply()` calls `terminate_instances` or `stop_instances`. `VM` is the adapter entry point `crc.py` uses; `from crc.aws.vm import VM` keeps working, and so does every other name the old sibling modules exported (`crc.aws.vm_cleaner`, `crc.aws.vm_api` and `crc.aws.vm_scan` are now `crc.aws.vm.cleaner`, `crc.aws.vm.client` and `crc.aws.vm.scan`).

Modules, one per responsibility:

- `cleaner.py`: settings, default factories, summary logging, `VmCleaner` and the registry factory.
- `client.py`: typed Protocols of the boto3 EC2 calls used by the cleaner and the tag scan.
- `scan.py`: the read-only scan of instances by tag across regions.
- `adapter.py`: the `VM` class and its `VmRuntime`, a thin adapter that drives the cleaner region by region.

## Public API

- `VM`: adapter crc.py calls (delete(), stop(), get_deleted, get_stopped, delete_count, stopped_count, find_instances_with_tag(), instance_names_to_delete, instance_names_to_stop); runs the cleaner region by region in the order of main; tests replace its runtime attribute with fakes.
- `VmRuntime`: frozen dataclass with the injected client factory, region lister, clock (now, UTC), scan client factory and session factory of VM.
- `VmCleaner`: the Cleaner (supports, read-only plan, mutating apply, plus build_filters, filter_instances, apply_region and should_skip used by the adapter).
- `VmSettings`: frozen dataclass with the filters and thresholds of one run (filter_tags, exception_tags, notags, age, custom_age_tag_key, instance_state).
- `build_vm_cleaner()`: registry factory behind @register("aws", "vm"); reads filter_tags, exception_tags, notags, age, custom_age_tag_key and instance_state from RunContext.options.
- `boto3_client()`: default client factory of the VM cleaner (boto3.client("ec2", region_name=...)).
- `all_regions()`: default region lister of the VM cleaner (get_all_regions("ec2", "us-west-2")).
- `skip_region()`: logs a region skipped for a connectivity error (context "ec2 vm <operation>").
- `note_region()`: reports a listed and filtered region to the current logger (REGION line: scanned, selected, seconds).
- `note_dry_run()`: reports one would_delete or would_stop ACTION per instance a dry run lists.
- `log_summary()`: the closing warnings of a VM run (no instances, number and list deleted or stopped, or the dry-run list).
- `dry_run_names()`: what a VM dry run records, names for delete but instance ids for stop (as on main).
- `VmClient`: Protocol of the four EC2 calls used, describe_instances, describe_network_interfaces, terminate_instances and stop_instances; tests pass fakes.
- `VmPaginator`: Protocol of the describe_instances paginator (paginate) used by the tag scan.
- `VmScanClient`: Protocol of the EC2 client surface of the tag scan (get_paginator, exceptions).
- `VmSession`: Protocol of the boto3 session call of the tag scan (get_available_regions).
- `instance_name()`: value of the first Name tag of an instance, or None.
- `find_instances_with_tag()`: read-only scan of instances carrying a tag in every region.
- `ClientFactory`, `RegionLister`, `ScanClientFactory`, `SessionFactory`, `Filters`: type aliases of the injected factories and of the describe_instances filter list.
- `SERVICE_NAME`, `DEFAULT_REGION`, `DEFAULT_STATE`: constants (the service ec2, the region us-west-2 of the region lookup, and the default instance state running).

## Safety Invariants

- `plan()` only calls `describe_instances` and `describe_network_interfaces`; `terminate_instances` and `stop_instances` are called from `apply()` / `apply_region()` (and `VM.delete()` / `VM.stop()` through them) only and never in a dry run.
- The age gate is the attach time of the first network interface against `--age` or the fail-open custom age tag (`core.age.is_old`, `policy.effective_age`); an empty age matches every instance and `MAX_AGE` can shorten it (SAF-006 to SAF-010, BUG-004, BUG-213).
- Fail closed: any error while judging one instance is logged and keeps it; exception tags (any key) and notags skip an instance; an instance without a `Tags` key or `Name` tag is kept. Termination and stop errors are isolated per instance.
- Region errors: a `describe_instances` connectivity error skips the region (SAF-001); `describe_regions` falls back to the static list on a connectivity error only and every other error propagates (SAF-004).
- `VM.delete()` keeps the call and log order of main (per region: client, describe, per-instance decision, then the calls); the Cleaner path lists every region before it mutates.
- The move into this subpackage changed no behaviour; quirks are in `docs/refactor/BUGS.md` (BUG-004, BUG-020, BUG-021, BUG-213 to BUG-227, BUG-351).
- Logging (LOG-AWS1, BUG-358/361/362): the raw tag list of each instance is logged at DEBUG; REGION, ACTION (deleted/stopped, would_* in a dry run), skip counts (`no_tags`, `exception_tag`, `too_young`) and failures (processing errors, missing Name tag, failed terminate/stop) go to `crc.core.logger.current()`; log text of the existing lines is unchanged.

## Extension Rules

1. No behaviour change in a refactor rung; behaviour fixes are separate rungs (`docs/refactor/PLAN.md`).
2. Keep filtering in `crc.core.policy` and age in `crc.core.age`; no `re`, clock reads or environment reads here (ARCH-003): `now` comes from `RunContext` (or the adapter clock).
3. Keep every mutating call in `apply_region()`; `plan()` and what it calls stay read-only (ARCH-005).
4. Changes need `tests/test_aws_vm_parity.py` (oracle `tests/legacy/aws_vm.txt`, a frozen copy of `main`, never edit it) and `tests/test_aws_vm.py` to stay green.
5. Every file stays under 400 lines; split by responsibility and re-export every public name in `__init__.py` (and `__all__`).
