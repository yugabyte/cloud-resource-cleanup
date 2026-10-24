# crc.azu.spot_vm

## Purpose

Azure Spot VM cleaner. Deletes Spot VMs that are stopped or deallocated, match the tag filters and are old enough, then their leftover Detach disks and their `<vm>-NIC`. Split by responsibility: `cleaner.py` (plan and apply), `config.py` (`SpotVmConfig`, summary log, age check), `client.py` (SDK Protocols), `disks.py` (leftover disk detection), `removal.py` (the mutating steps), `__init__.py` (package re-exports). The CLI runs the cleaner through `crc.core.engine` (`crc/cli/routes/azu.py`).

## Public API

- `AzureSpotVmCleaner`: the Cleaner (supports, read-only plan, mutating apply, plus list_vms, decide and power_status); registered as azure / spot_instances.
- `SpotVmConfig`: frozen dataclass with the flags of one run (resource group, tag specs, validated age, custom age tag key).
- `build_spot_vm_cleaner()`: registry factory; reads resource_group, filter_tags, exception_tags, notags, age and custom_age_tag_key from RunContext.options and builds the clients from Base.
- `old_enough()`: the age check of one Spot VM; an invalid custom age tag keeps it.
- `log_summary()`: the closing warning lines logged for a run.
- `STOPPED_POWER_STATES`: the two power states that qualify, "VM stopped" and "VM deallocated".
- `SpotComputeClient`, `SpotNetworkClient`, `VirtualMachineOperations`, `DiskOperations`, `Poller`: Protocols of the SDK calls used; tests pass fakes.
- `ComputeFactory`, `NetworkFactory`: client factories injected through the constructor.
- `RemovalOps`: the two shared delete helpers (VM delete and NIC delete), swappable.
- `Removed`: the VM, NIC and disk lists of a run.
- `Remover`: the mutating steps (remove_vm, remove_vm_instance, remove_disk, remove_nic) and the read-only disk tag check (disk_protected).
- `DiskSlot`, `DiskRef`: plain-value view of an attached disk and the (resource group, name, rg_from_id) reference.
- `disk_slots()`, `detached_disk_refs()`, `disk_name_from_id()`, `disk_ref_from_id()`: leftover disk helpers.

## Safety Invariants

- Authoritative text: `CLEANUP_SAFETY.md`. Applicable IDs: SAF-005 (a failed VM list is logged and re-raised), SAF-006 to SAF-013 and SAF-041 (age gate: `age` must be positive, an invalid custom age tag skips the VM), SAF-038 (only Detach or unset `delete_option` disks are deleted after the VM), SAF-042 (untagged VMs are never deleted), SAF-044 (leftover disks outside `--resource_group` or protected by their own tags are kept).
- `plan()` only reads: `virtual_machines.list`, `instance_view` and, in a dry run, `disks.get`. `begin_delete` (VM, disks, NIC) is called from `apply()` only and never in a dry run.
- Order per VM: VM delete (waited), leftover disks (tag check by `disks.get`, then delete), NIC. A failing VM is logged and skipped, the run goes on; leftover disks outside `--resource_group` or protected by their own exception_tags or notags are kept; a failed disk tag read keeps the disk.
- A dry run needs `plan()` alone: it also previews the disks and the NIC and logs the summary. In a real run all VMs are listed and checked before the first delete.
- Quirks in `docs/refactor/BUGS.md` (`BUG-244` to `BUG-254`).

## Extension Rules

1. Do not change a filter, call order or log line without a `BUG-NNN` row.
2. Keep `plan()` free of calls whose name says delete, stop or release (ARCH-005).
3. New SDK calls go through the Protocols in `client.py` and get a fake in the parity test.
