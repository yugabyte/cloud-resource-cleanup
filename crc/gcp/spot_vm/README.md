# crc.gcp.spot_vm

## Purpose

GCP Spot / preemptible VM cleaner (`--cloud gcp --resource spot_instances`, GCP-4). `plan()` lists the zones and their instances and filters them; `apply()` deletes each VM, waits for the zone operation, then deletes its `autoDelete=false` disks. No behaviour change from `main`.

Modules:

- `settings.py`: constants and the frozen run settings.
- `client.py`: the typed SDK protocols and the google SDK implementation.
- `instances.py`: instance filters (Spot, GKE or MIG member, labels, age).
- `operations.py`: waiting for a zone or region operation.
- `disks.py`: leftover disk references, label check and delete.
- `cleaner.py`: `SpotVMCleaner`, its summary log and the registry factory.
- `adapter.py`: the `SpotVM` class, the adapter `crc/cli` uses.

## Public API

The package init re-exports the main names below, so the old import path of the `SpotVM` class keeps working.

- `SpotVM`: adapter the CLI calls (delete(), get_deleted, delete_count, get_deleted_disks, instance_names_to_delete, disks_to_delete, service_name, service_version, default_instance_state, time_format, INSTANCE_DELETE_WAIT_SEC, INSTANCE_POLL_INTERVAL_SEC); the optional eighth and ninth constructor arguments inject a client and a clock.
- `SpotVMCleaner`: the Cleaner (supports, read-only plan, mutating apply, plus zones, list_zone, scan_zone, evaluate, preview_instance and apply_instance used by the adapter).
- `build_spot_cleaner()`: registry factory behind @register("gcp", "spot_instances").
- `log_summary()`: the closing log lines (no VMs warning, VM list, leftover disk list).
- `SpotSettings`: frozen dataclass with the fields main reads (project_id, filter_labels, exception_labels, notags, age, custom_age_tag_key, instance_states, wait_sec, poll_sec).
- `DiskRef`: typed dict of a parsed disk source (project, scope, location, name).
- `InstanceView`: Protocol of the instance fields read (name, status, labels, creation_timestamp); scheduling, metadata and disks are read defensively.
- `GcpSpotClient`: Protocol of the Compute Engine calls used (list_zones, list_instances, delete_instance, get_operation, get_disk, delete_disk, close); tests pass fakes.
- `ComputeSpotClient`: the google-api-python-client plus google-cloud-compute implementation; one discovery session per run, opened lazily and closed by close(); an existing service can be passed in.
- `is_spot_instance()`: preemptible or provisioning model SPOT.
- `labels_protect()`: exception_labels any key, or notags all keys; empty labels never protect.
- `is_managed_or_gke_node()`: goog-gke-node label or created-by metadata naming instanceGroupManagers.
- `should_skip_instance()`: labels_protect on the instance labels.
- `has_matching_filter_label()`: filter_labels, all keys; empty matches all.
- `is_old_instance()`: custom age label (fail closed) or else age, through crc.core.age.
- `is_selected()`: every instance filter in the order of main.
- `reject_reason()`: the same filters, returning the skip reason (state, not_spot, managed_or_gke, exception_tag, filter_mismatch or too_young) of an instance to keep, or None when it is selected; `is_selected()` is true when it returns None.
- `disk_ref_from_source()`: parse a zonal, regional or name-only disk source.
- `non_autodelete_disks()`: references of the instance disks with autoDelete false.
- `disk_label()`: the project/location/name recorded for a disk.
- `disk_ok_to_delete()`: read the disk and keep it when its labels protect it.
- `remove_leftover_disk()`: delete one disk and wait for the operation (nothing in a dry run).
- `wait_operation()`: poll until DONE, an error, or the wait time.

## Safety Invariants

- Authoritative text: `CLEANUP_SAFETY.md`. Applicable IDs: SAF-006 to SAF-013 (age gates), SAF-039 (leftover disks are deleted only after the instance-delete zone operation completes), SAF-041 (positive `age`, custom age tag fail closed), SAF-043 (label filters as the GCP VM), SAF-044 (a leftover disk is kept when its own exception_labels or notags match), SAF-045 (GKE nodes and managed instance group members are skipped).
- `plan()` only calls `list_zones` and `list_instances` (plus, in a dry run, `get_disk` reads of the leftover disks); `delete_instance`, `delete_disk` and the operation polls are called from `apply_instance()` and `remove_leftover_disk()` only and never in a dry run (ARCH-005).
- An instance is selected only when its status is RUNNING, TERMINATED or SUSPENDED (`policy.state_in`), it is Spot or preemptible, is not a GKE node or MIG member (`policy.gcp_is_managed_or_gke_node`), is not excluded by `exception_labels` (any key) or `notags` (all keys) (`policy.should_skip_by_tags`; an unlabelled VM is never excluded), matches `filter_labels` (`policy.filter_tags_all_keys`; empty matches all, an unlabelled VM fails an active filter) and is at least as old as the custom age label or else `age` (`core_age.validated_retention_age`, `policy.effective_age`, `core_age.is_old`, `MAX_AGE` still applies). An invalid custom age label keeps the VM. The clock comes from `RunContext.now` (Cleaner) or the injected `clock` (adapter), never read inside the cleaner.
- If the delete call fails, or its operation errors, times out (`INSTANCE_DELETE_WAIT_SEC`, default 180 s) or has no name, the VM is not recorded and its leftover disks are kept. Errors per VM are logged (`Error processing GCP Spot VM ...`) and the run goes on; a zone listing error skips the zone; the zone listing itself, the instances client and the session errors propagate unchanged (no connectivity fallback).
- The wait reads `time.monotonic` and `time.sleep` (ARCH-003 bans `time.time`); `main` used `time.time`.
- `SpotVM.delete()` keeps the call order of `main` (per zone: list, then VM by VM: filter, delete, wait, disks); the registered Cleaner lists and filters every zone first, then deletes, so an error while listing a later zone stops before any delete, and in a dry run `plan()` also reads the leftover disks. Quirks are in `docs/refactor/BUGS.md` (`BUG-228` to `BUG-243`).
- Do not call real cloud APIs in tests; mock every SDK client.

## Extension Rules

1. Behaviour fixes are separate rungs (`docs/refactor/PLAN.md`); a refactor change must keep `tests/test_gcp_spot_instances_parity.py` (oracle `tests/legacy/gcp_spot_vm.txt`, a frozen copy of `main`, never edit it) green.
2. New filters go through `crc.core.policy`; age through `crc.core.age`; no `re`, `print`, wall clock or `os.environ` in this package (ARCH-003).
3. A new SDK call goes into `GcpSpotClient` first, with a fake in the tests; `plan()` stays read-only.
4. Keep each module under 400 lines; split by responsibility and keep the old import path through `__init__.py`.
5. Record every new bug or quirk in `docs/refactor/BUGS.md` with its `main` file:line.
