# crc.azu

## Purpose

Azure cleaners (azure-mgmt SDK). They delete or stop leftover VMs, disks, IPs and NICs in one resource group.

**Refactored.** Every module is strict-checked and none is in the legacy ratchet: `ip.py` (rung AZ-1), `nic.py` (rung AZ-2), `disk.py` (rung AZ-3), `vm/` (rung AZ-4) and `spot_vm/` (rung AZ-5) are refactored into registered `Cleaner`s; `vm_delete_helpers.py` and `__init__.py` (rung PL-AZ) are strict-typed plumbing with the same behaviour as on `main`. `_base.py` is strict-clean too; its `os.environ` credential read is an owner-approved ARCH-003 allowlist entry (`tests/archtest/bans.py`), so the credentials are still read when an Azure cleaner is built, as on `main`. Any change must follow `ARCHITECTURE.md`; a behaviour change needs a behaviour-fix rung in `docs/refactor/PLAN.md` (`.cursor/skills/crc-python-refactor/BEHAVIOUR_FIX.md`), with `make check` green.

Modules (operations and filters as catalogued in `docs/refactor/FILTER_VARIANTS.md`):

- `_base.py`: `Base`: Azure credential from the `AZURE_CREDENTIALS_*` variables, and lazily built compute and network clients for a resource group (rung PL-AZ)
- `disk.py`: `AzureDiskCleaner` (registered as `azure`/`disk`) and the adapter `Disk`: deletes unattached managed disks (`begin_delete`); any-key tag filter, age gate
- `ip.py`: `AzureIpCleaner` (registered as `azure`/`ip`) and the adapter `IP`: deletes IPs without an IP configuration (`begin_delete`); any-key tag filter
- `nic.py`: `AzureNicCleaner` (registered as `azure`/`nic`) and the adapter `NIC`: deletes NICs not attached to a VM (`begin_delete(...).result()`, three attempts); plain substring name filters
- `spot_vm/`: `AzureSpotVmCleaner` (registered as `azure`/`spot_instances`) and the adapter `SpotVM`: deletes stopped or deallocated Spot VMs and their leftover disks and NICs (subpackage with its own README, rung AZ-5)
- `vm/`: subpackage (own README): `VmCleaner` (registered as `azure`/`vm`) and the adapter `VM`: deletes (via `vm_delete_helpers`) or stops (`begin_power_off`) VMs by status; `from crc.azu.vm import VM` still works
- `vm_delete_helpers.py`: `begin_delete_vm_and_wait`, `delete_vm_primary_nic`: delete helpers, no filtering (rung PL-AZ)

## Public API

The ip, nic and disk modules are refactored, so the names below are synced with this README by ARCH-002.
The vm and spot_vm subpackages document their own API in their own READMEs. The VM delete helpers module and the base module are refactored too (rung PL-AZ). The package init holds only a
copyright comment and declares no export list.

- `Base`: Azure credential from the AZURE_CREDENTIALS_* environment variables (read when a cleaner is built) plus the lazily built, shared compute and network clients of one resource group; the client getters are typed as any object because each cleaner narrows the client with its own Protocol.
- `DeletePoller`: Protocol of the handle begin_delete returns (result(timeout)), used by the delete helpers.
- `begin_delete_vm_and_wait()`: starts a VM delete and waits on the poller (default timeout 3600 s), then logs the completion; errors propagate.
- `delete_vm_primary_nic()`: deletes the NIC named after the VM plus a suffix (default "-NIC") with up to 24 attempts 15 s apart; not-found counts as deleted and a final failure is logged, never raised.
- `PublicIpOperations`: Protocol of the two public IP calls used, list_all and begin_delete; tests pass fakes.
- `IpNetworkClient`: Protocol of the network client part used (the public_ip_addresses operations).
- `AzureIpCleaner`: the Cleaner (supports, read-only plan, mutating apply, plus list_ips, decide and delete_candidate used by the adapter); the client factory is injected through the constructor.
- `build_ip_cleaner()`: registry factory behind @register("azure", "ip"); reads resource_group, filter_tags, exception_tags and notags from RunContext.options and builds the client from `Base`.
- `IP`: adapter crc.py calls (delete(), get_deleted, count, deleted_ips, base); runs the cleaner IP by IP in the order of main.
- `DeletePoller`: Protocol of the handle begin_delete returns (result()).
- `NicOperations`: Protocol of the two network_interfaces calls used, list_all and begin_delete (positional arguments); tests pass fakes.
- `NicNetworkClient`: Protocol of the network client part used (the network_interfaces operations).
- `AzureNicCleaner`: the Cleaner (supports, read-only plan, mutating apply, plus list_nics, decide and attempt_delete used by the adapter); the client factory is injected through the constructor.
- `build_nic_cleaner()`: registry factory behind @register("azure", "nic"); reads resource_group, name_regex and exception_regex from RunContext.options and builds the client from `Base`.
- `NIC`: adapter crc.py calls (delete(), get_deleted, delete_count, nics_names_to_delete, base); runs the cleaner NIC by NIC in the order of main.
- `SdkDisk`: Protocol of the Azure disk fields read (name, tags, time_created, disk_state).
- `DiskOperations`: Protocol of the two disks calls used, list and begin_delete (positional arguments); tests pass fakes.
- `DiskComputeClient`: Protocol of the compute client part used (the disks operations).
- `DiskSettings`: frozen dataclass of the filters and age gate (filter_tags, exception_tags, notags, age, custom_age_tag_key, disk_states).
- `AzureDiskCleaner`: the Cleaner (supports, read-only plan, mutating apply, plus list_disks, decide and delete_disk used by the adapter); the client factory is injected through the constructor.
- `build_disk_cleaner()`: registry factory behind @register("azure", "disk"); reads resource_group, filter_tags, exception_tags, notags, age and custom_age_tag_key from RunContext.options and builds the client from `Base`.
- `Disk`: adapter crc.py calls (delete(), get_deleted, count, disks_names_to_delete, base, clock); runs the cleaner disk by disk in the order of main, reading the clock once per listed disk (main reads it once per tag-matching disk).
- `started()`, `region_done()`, `skip()`, `acted()`, `failed()`, `reason_slug()` (module _logger, rung LOG-AZU): thin Azure helpers over the current run of crc.core.logger. Azure has no regions, so `region_done()` logs one REGION line per resource group, with the resource group as region; `skip()` counts a skipped resource under a short snake_case reason (`reason_slug()`), `acted()` logs one ACTION line, `failed()` records a swallowed per-resource error. They only report, never decide.

## Safety Invariants

- Authoritative text: `CLEANUP_SAFETY.md`. Applicable IDs: SAF-006 to SAF-013 (age gates, shared by all clouds), SAF-005, SAF-038, SAF-041, SAF-042, SAF-044.
- Filtering per module: `docs/refactor/FILTER_VARIANTS.md` sections 3 (Azure), 6 and 7.
- Known bugs and quirks (do not fix outside a behaviour-fix rung): `docs/refactor/BUGS.md` rows `BUG-002`, `BUG-003`, `BUG-004`, `BUG-010`, `BUG-013`, `BUG-019`, `BUG-074` to `BUG-082`, `BUG-111` to `BUG-123`, `BUG-172` to `BUG-182`, `BUG-198` to `BUG-212`, `BUG-244` to `BUG-254`, `BUG-318` to `BUG-326`.
- Azure IPs (AZ-1): `plan()` only calls `list_all`; `begin_delete` is called from `apply()` (and `IP.delete()`) only and never in a dry run. There is no age gate (SAF-011 is not met: an empty `filter_tags` selects every unattached IP) and `list_all` is subscription-wide while the delete uses the configured resource group; untagged IPs raise `TypeError` when `exception_tags` or `notags` is set (BUG-013). Nothing is isolated per IP: every error propagates and a later error leaves earlier IPs deleted. Quirks in `docs/refactor/BUGS.md` (`BUG-074` to `BUG-082`).
- Azure NICs (AZ-2): `plan()` only calls `list_all` (twice, as main does); `begin_delete(...).result()` is called from `apply()` (and `NIC.delete()`) only and never in a dry run. An empty `name_regex` logs a warning and returns before any call. Selection is any unattached NIC (`virtual_machine` falsy) whose name contains a `name_regex` entry and no `exception_regex` entry (plain substrings, not regexes). There is no tag filter and no age gate (SAF-011 is not met), `list_all` is subscription-wide while the delete uses the configured resource group, and a failed delete is retried up to three times, logged and swallowed (the NIC is then missing from `get_deleted` and the run still succeeds). Quirks in `docs/refactor/BUGS.md` (`BUG-111` to `BUG-123`).
- Azure disks (AZ-3): `plan()` only calls `disks.list`; `disks.begin_delete(resource_group, name)` is called from `apply()` (and `Disk.delete()`) only and never in a dry run. Selection (an empty `filter_tags` selects every disk, so SAF-011 is not met by the tag filter): `filter_tags` any key (empty selects all, untagged disks match only when no filter), `exception_tags` any key, `notags` all keys, age through `core_age.get_retention_age` (custom age tag fails open, BUG-004) and `core_age.is_old`, then the state test, which is a substring test (BUG-019). Nothing is isolated per disk: every error propagates and earlier disks stay deleted; the delete poller is not awaited; `list` is subscription-wide while the delete uses the configured resource group. Quirks in `docs/refactor/BUGS.md` (`BUG-172` to `BUG-182`).
- Azure VMs (AZ-4): see `crc/azu/vm/README.md`. `plan()` only calls `list_all` and `instance_view`; the VM and NIC deletes and `begin_power_off` run from `apply()` (and `VM.delete()` / `VM.stop()`) only, never in a dry run. Untagged VMs are skipped (SAF-042), age goes through `crc.core.age`, per-VM errors are logged and swallowed, and BUG-002, BUG-003, BUG-010 are preserved. Quirks in `docs/refactor/BUGS.md` (`BUG-198` to `BUG-212`).
- Azure Spot VMs (AZ-5): see `crc/azu/spot_vm/README.md`; `plan()` is read-only, `SpotVM.delete` re-raises when the list call fails (SAF-005), Spot VMs without tags are skipped (SAF-042).
- Azure plumbing (PL-AZ): `Base` reads `AZURE_CREDENTIALS_SUBSCRIPTION_ID`, `AZURE_CREDENTIALS_TENANT_ID`, `AZURE_CREDENTIALS_CLIENT_ID` and `AZURE_CREDENTIALS_CLIENT_SECRET` (in that order) and `AZURE_RESOURCE_GROUP` (only when the resource group argument is falsy); it reads credentials from the environment only (a missing variable raises `KeyError` at construction, even in a dry run) and never logs the secret. `begin_delete_vm_and_wait` is called by the VM and Spot VM cleaners only when `dry_run` is `False`, and `delete_vm_primary_nic` checks `dry_run` before making any SDK call. The NIC helper never raises and records a not-found NIC as deleted (BUG-200, BUG-203, BUG-252, BUG-254, `BUG-318` to `BUG-326` in `docs/refactor/BUGS.md`). Parity with main: `tests/test_azure_base_parity.py`, `tests/test_azure_vm_delete_helpers_parity.py`.
- Logging (LOG-AZU, BUG-358, BUG-361, BUG-362): every cleaner (vm, spot_vm, disk, ip, nic, `vm_delete_helpers`) reports to `crc.core.logger.current()`: one REGION line per resource group, one ACTION line per resource deleted, stopped or listed in a dry run, skip counts under short reasons, and swallowed per-resource errors (VM processing, Spot VM, leftover Spot disk, NIC after three attempts, VM primary NIC after 24 attempts) through `failed()`. The raw tag dumps of disks and VMs and the per-leftover-disk skip lines are DEBUG with unchanged text. Log text and levels only: selection, calls and call order are unchanged. Unattached (no `begin()`), these calls log nothing.
- Do not call real cloud APIs in tests; mock every SDK client.

## Extension Rules

1. Do not edit these modules to add features or fix bugs in a refactor rung; behaviour fixes are
   separate rungs (`docs/refactor/PLAN.md`).
2. New resources go in a new module with the `Cleaner` contract (`ARCHITECTURE.md`, "Add a resource
   cleaner"): a `Cleaner` registered with `@register("azure", ...)`.
3. A new or refactored module follows `ip.py`, `nic.py`, `disk.py`, `vm/` and `spot_vm/`: a Cleaner with an injected client Protocol, `@register("azure", ...)`, a parity test against a frozen oracle in `tests/legacy/`. No module of this package is in the legacy ratchet (`pyproject.toml` lists only five hash-locked safety test files); never add one. Update this README and its symbol list with any public change.
4. Keep `Base`, the delete helpers and their call order, environment variables, log lines and swallowed errors as they are; a cleaner narrows the `Any` clients with its own Protocol instead of changing the plumbing's return types.
5. Record every new bug or quirk in `docs/refactor/BUGS.md` with its `main` file:line.
