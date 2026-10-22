# crc.gcp

## Purpose

GCP cleaners (google-cloud-compute). They delete or stop leftover instances, disks and addresses.

**Refactored.** Every module is strict-checked and none is in the legacy ratchet: `ip.py` (GCP-1), `disk.py` (GCP-2), `vm.py` (GCP-3) and `spot_vm/` (GCP-4) are `Cleaner`s (no behaviour change); `_base.py` and `__init__.py` (PL-GCP) are refactored plumbing (strict-clean, no behaviour change). The adapter classes (`IP`, `Disk`, `VM`, `SpotVM`) remain the entry point `crc/cli` uses. Any change must follow `ARCHITECTURE.md`; a behaviour change needs a behaviour-fix rung in `docs/refactor/PLAN.md` (`.cursor/skills/crc-python-refactor/BEHAVIOUR_FIX.md`), with `make check` green.

Modules (operations and filters as catalogued in `docs/refactor/FILTER_VARIANTS.md`):

- `_base.py` (PL-GCP, refactored): `get_gcp_regions`
- `_scan.py` (LOG-GCP): `ZoneScan` and `log_action`, the run-log helpers shared by the four cleaners
- `disk.py`: `DiskCleaner` (registered as `gcp` / `disk`) and the `Disk` adapter: deletes unattached disks by `last_detach_timestamp` (zonal disks of every zone); label any-key filter; `notags`, `name_regex` and `age` are not used
- `ip.py`: `IpCleaner` (registered as `gcp` / `ip`) and the `IP` adapter: deletes RESERVED addresses (`AddressesClient().delete`); substring name filters, exception filter inverted
- `spot_vm/`: subpackage (GCP-4) with its own README; the import path `crc.gcp.spot_vm` is unchanged
- `vm.py`: `VmCleaner` (registered as `gcp` / `vm`) and the `VM` adapter: deletes (`delete`) or stops (`stop`) RUNNING instances by labels (all keys), `notags` and exception labels, and age

## Public API

ip.py, disk.py and vm.py are refactored, so the names below are synced with this README by ARCH-002. The spot_vm
subpackage documents its own API in its own README. The plumbing modules (PL-GCP) are refactored too and the package init holds only a copyright comment and declares no __all__.

- `get_gcp_regions()`: returns the region names of a project (one Compute Engine regions client list call), a plain list in listing order. It builds a new client per call, has no fallback and lets every SDK error propagate. No caller on this branch (`ComputeIpClient` lists regions through its own client).

- `ZoneScan`: counts the resources listed and selected in one zone or region and logs the REGION line through the current run log when finished; log text only (LOG-GCP, BUG-361).
- `log_action`: logs the ACTION line (would_delete, deleted, would_stop or stopped) of one candidate through the current run log (LOG-GCP).
- `AddressView`: Protocol of the two address fields read (name, status); tests pass fakes.
- `GcpIpClient`: Protocol of the three Compute Engine calls used (list_regions, list_addresses, delete_address); tests pass fakes.
- `ComputeIpClient`: the google-cloud-compute implementation of `GcpIpClient`; like main it builds a new RegionsClient or AddressesClient for every call.
- `IpCleaner`: the Cleaner (supports, read-only plan, mutating apply, plus regions, plan_region and apply_region used by the adapter).
- `build_ip_cleaner()`: registry factory behind @register("gcp", "ip"); reads project_id, name_regex and exception_regex from RunContext.options and raises ValueError without a project_id.
- `IP`: adapter crc.py calls (delete(), get_deleted, count, deleted_ips, default_state); runs the cleaner region by region in the order of main. The optional fifth constructor argument injects a client.
- `DiskView`: Protocol of the four disk fields read (users, name, labels, last_detach_timestamp); tests pass fakes.
- `GcpDiskClient`: Protocol of the Compute Engine calls used (list_zones, list_disks, delete_disk, close); tests pass fakes.
- `ComputeDiskClient`: the google-api-python-client (zones, deletes) plus google-cloud-compute (disk listing) implementation of `GcpDiskClient`; it builds the discovery service lazily, once per run, builds the disks client right after the zones were listed as main does, and close() closes the service.
- `DiskSettings`: frozen dataclass with the fields main reads (project_id, filter_labels, exception_labels, exception_regex, custom_age_tag_key, detach_age, slack_notify_users, slack_user_label).
- `DiskCleaner`: the Cleaner (supports, read-only plan, mutating apply, plus zones, plan_zone and apply_disk used by the adapter); plan_zone is a lazy generator so the adapter deletes disk by disk while listing, as main does.
- `build_disk_cleaner()`: registry factory behind @register("gcp", "disk"); reads project_id (ValueError without it), filter_tags, exception_tags, exception_regex, custom_age_tag_key, detach_age, slack_notify_users and slack_user_label from RunContext.options.
- `Disk`: adapter crc.py calls (delete(), get_deleted, count, disk_names_to_delete, service_name, service_version, time_format, default_disk_state); the optional 13th and 14th constructor arguments inject a client and a clock (default is the current time). Age, notags, name_regex and the disk_state argument of delete() are accepted and ignored, as on main.
- `InstanceView`: Protocol of the four instance fields read (status, name, labels, creation_timestamp); tests pass fakes.
- `GcpVmClient`: Protocol of the Compute Engine calls used (list_zones, list_instances, delete_instance, stop_instance); tests pass fakes.
- `ComputeVmClient`: the google-api-python-client (zones, deletes, stops) plus google-cloud-compute (instance listing) implementation of `GcpVmClient`; like main it builds the discovery service in list_zones, builds the instances client right after the zones were listed and closes the service at that point, and later deletes and stops still use the closed service object.
- `VmSettings`: frozen dataclass with the fields main reads (project_id, filter_labels, exception_labels, notags, age, custom_age_tag_key, instance_state).
- `VmCleaner`: the Cleaner (supports DELETE and STOP, read-only plan, mutating apply, plus zones, plan_zone and apply_instance used by the adapter); plan_zone is a lazy generator so the adapter acts instance by instance while listing, as main does.
- `build_vm_cleaner()`: registry factory behind @register("gcp", "vm"); reads project_id (ValueError without it), filter_tags, exception_tags, notags, age, custom_age_tag_key and instance_state from RunContext.options; the operation (delete or stop) is the RunContext operation.
- `VM`: adapter crc.py calls (delete(instance_state), stop(), get_deleted, delete_count, get_stopped, stopped_count, instance_names_to_delete, instance_names_to_stop, service_name, service_version, default_instance_state, time_format); the optional eighth and ninth constructor arguments inject a client and a clock (default is the current time). Both lists accumulate over calls, as on main.

## Safety Invariants

- Authoritative text: `CLEANUP_SAFETY.md`. Applicable IDs: SAF-006 to SAF-013 (age gates, shared by all clouds), SAF-021 to SAF-024, SAF-037, SAF-039, SAF-041, SAF-043, SAF-044, SAF-045.
- Filtering per module: `docs/refactor/FILTER_VARIANTS.md` sections 4 (GCP), 6 and 7.
- Known bugs and quirks (do not fix outside a behaviour-fix rung): `docs/refactor/BUGS.md` rows `BUG-001`, `BUG-004`, `BUG-005`, `BUG-006`, `BUG-023`, `BUG-062` to `BUG-073`, `BUG-124` to `BUG-141`, `BUG-183` to `BUG-197`, `BUG-228` to `BUG-243`, `BUG-297` to `BUG-301`.
- `GCP_Disk` uses `--detach_age`, never `--age` (SAF-021, SAF-022); Spot VMs skip GKE and instance group members (SAF-045).
- GCP IP (GCP-1): `plan()` only calls `RegionsClient().list` and `AddressesClient().list`; `AddressesClient().delete` is called from `apply_region()` only and never in a dry run. There is no age gate and no tag filter, so the name filter is the only protection: an empty `filter_regex` selects every RESERVED address (`policy.filter_substring_matches`), and the exception filter is inverted (`policy.gcp_ip_exception_excludes`, BUG-001). No SAF ID is specific to this module. Region and address listing errors and delete errors propagate unchanged (nothing is isolated or skipped, there is no connectivity fallback); a delete error loses the record of the addresses of that region already deleted. `IP.delete()` keeps the call order of `main` (per region: list, filter, deletes); the registered Cleaner lists every region first, then deletes. Quirks are in `docs/refactor/BUGS.md` (`BUG-062` to `BUG-073`).
- GCP disk (GCP-2): `plan()` only calls `service.zones().list().execute()` and `DisksClient().list`; `service.disks().delete().execute()` is called from `apply_disk()` only and never in a dry run (ARCH-005). A disk is deleted only when it is unattached (`users` empty), has a `last_detach_timestamp`, is not excluded by `exception_regex` (`policy.exception_regex_match_matches`, `re.match`) or `exception_labels` (`policy.exception_tags_any`), matches `filter_labels` (`policy.filter_tags_any_key`; empty matches all, an unlabelled disk fails an active filter) and is at least as old as the custom age label or else `detach_age` (`policy.effective_age`, `core_age.is_old`, `MAX_AGE` still applies). The age gate is `--detach_age` only (SAF-021, SAF-022): `age` is never read, so `--age` cannot protect a disk; `notags` and `name_regex` are never applied; an empty `detach_age` matches every unattached disk with a detach timestamp; the custom age label is fail open (`get_retention_age`, BUG-004). The clock comes from `RunContext.now` (Cleaner) or the injected `clock` (adapter), never read inside the cleaner. A failed delete is logged (`Error occurred while deleting disk ...`) and skipped, not reported as failed, and not recorded. Zone, disk listing, timestamp parse, regex and `MAX_AGE` errors propagate unchanged (nothing is isolated, no connectivity fallback). `Disk.delete()` keeps the call order of `main` (per zone: list, filter, delete, record, disk by disk); the registered Cleaner lists and filters every zone first, then deletes, so an error while listing a later zone stops before any delete. Quirks are in `docs/refactor/BUGS.md` (`BUG-124` to `BUG-141`).
- GCP VM (GCP-3): `plan()` only calls `service.zones().list().execute()` and `InstancesClient().list`; `service.instances().delete()` and `.stop()` (`.execute()`) are called from `apply_instance()` only and never in a dry run (ARCH-005). An instance is acted on only when its status is in `instance_state` (default `RUNNING`, `policy.state_in`), it is not excluded by `exception_labels` (any key) or `notags` (all keys) (`policy.should_skip_by_tags`), carries every `filter_labels` key (`policy.filter_tags_all_keys`; empty matches all, an unlabelled instance fails an active filter) and is at least as old as the custom age label or else `age` (`policy.effective_age`, `core_age.is_old`, `MAX_AGE` still applies). The custom age label is fail open (`get_retention_age`, BUG-004). The clock comes from `RunContext.now` (Cleaner) or the injected `clock` (adapter), never read inside the cleaner. A failed delete or stop is logged (`Error occurred while delete instance ...`) and skipped, not reported as failed, and not recorded. Zone and instance listing, timestamp parse and `MAX_AGE` errors propagate unchanged (nothing is isolated, no connectivity fallback, no region listing). `VM.delete()` and `VM.stop()` keep the call order of `main` (per zone: list, filter, act, record, instance by instance); the registered Cleaner lists and filters every zone first, then acts, so an error while listing a later zone stops before any delete. Quirks are in `docs/refactor/BUGS.md` (`BUG-183` to `BUG-197`).
- GCP plumbing (PL-GCP): `get_gcp_regions()` is read-only (only `RegionsClient().list`); it reads no environment variable, config or credential source itself (the SDK default credentials apply), logs and prints nothing, has no connectivity fallback and swallows no error (BUG-067: a listing or auth error, also mid-iteration, propagates and the partial list is lost). Region names are not filtered, deduplicated or validated, and `project_id` is passed to the SDK unchecked. Quirks are in `docs/refactor/BUGS.md` (`BUG-297` to `BUG-301`).
- Do not call real cloud APIs in tests; mock every SDK client.

### spot_vm

`crc/gcp/spot_vm/` (GCP-4) is a subpackage with its own README (public names, safety invariants). `SpotVMCleaner` is registered as `gcp` / `spot_instances` (`build_spot_cleaner`); `SpotVM` is the adapter the CLI calls (`delete()`, `get_deleted`, `delete_count`, `get_deleted_disks`; same constructor plus two optional trailing arguments). It deletes Spot or preemptible VMs (RUNNING, TERMINATED, SUSPENDED) that match `filter_labels` (all keys) and the age gate, skips GKE nodes and managed instance group members, then deletes `autoDelete=false` disks after the instance operation completes (SAF-039, SAF-044, SAF-045). Behaviour is the same as `main`; any change needs `tests/test_gcp_spot_instances_parity.py` (oracle `tests/legacy/gcp_spot_vm.txt`, a frozen copy of `main`, never edit it) to stay green. Quirks are in `docs/refactor/BUGS.md` (`BUG-228` to `BUG-243`).

- Logging (LOG-GCP, `docs/refactor/LOGGING.md`; BUG-358, BUG-361, BUG-362): log text and levels only, no selection, call or order change. The label dump of every listed disk and instance (`logging.info(labels)`), the address dump of the IP cleaner and the per-disk `Skipping leftover Spot disk ...` line are DEBUG. Every zone or region pass logs one `REGION` line when its listing is exhausted (`ZoneScan`); every delete or stop (or, in a dry run, every would-be one) logs one `ACTION` line, in `apply_*()` and, for a dry-run `plan()`, in `plan()`; skipped resources are counted with `logger.current().skip(reason)` (`state`, `exception_tag`, `exception_regex`, `filter_mismatch`, `attached`, `never_detached`, `too_young`, `not_spot`, `managed_or_gke`, `disk_exception_tag`); a swallowed per-resource error is also recorded with `fail()` (the IP cleaner records and re-raises). A Spot zone whose listing fails calls `region_skipped()` and logs no `REGION` line.

## Extension Rules

1. Do not edit these modules to add features or fix bugs in a refactor rung; behaviour fixes are
   separate rungs (`docs/refactor/PLAN.md`).
2. New resources go in a new module with the `Cleaner` contract (`ARCHITECTURE.md`, "Add a resource
   cleaner"): a `Cleaner` registered with `@register("gcp", ...)`.
3. No module of this package is in the legacy ratchet (`pyproject.toml` lists only five hash-locked safety test files); never add one. Update this README and its symbol list with any public change.
4. GCP IP changes need `tests/test_gcp_ip_parity.py` (oracle `tests/legacy/gcp_ip.txt`, a frozen copy of `main`, never edit it) to stay green.
5. GCP disk changes need `tests/test_gcp_disk_parity.py` (oracle `tests/legacy/gcp_disk.txt`, a frozen copy of `main`, never edit it) to stay green.
6. GCP VM changes need `tests/test_gcp_vm_parity.py` (oracle `tests/legacy/gcp_vm.txt`, a frozen copy of `main`, never edit it) to stay green.
7. Record every new bug or quirk in `docs/refactor/BUGS.md` with its `main` file:line.
8. GCP plumbing changes need `tests/test_gcp_base_parity.py` (oracle `tests/legacy/gcp_base.txt`, a frozen copy of `main`, never edit it) to stay green. `__init__.py` stays a copyright comment with no export list.
