# crc.oci.spot_vm

## Purpose

Cleaner for OCI preemptible ("spot") instances (`--cloud oci --resource spot_instances`). It
terminates preemptible instances that match the filter tags and are old enough, then deletes the
block volumes that were attached to them. Refactored in rung OCI-3 from the single module
`crc/oci/spot_vm.py` (no behaviour change); the old import path `crc.oci.spot_vm` still works
through this package.

Modules, split by responsibility:

- `client.py`: the typed Protocols of the OCI calls used, the region client bundle and the factories on top of `Base`
- `decide.py`: state, preemptible, OKE, tag and age decisions (through `crc.core.policy` and `crc.core.age`)
- `leftovers.py`: attached volume discovery, the wait for `TERMINATED` and the leftover volume tag check and delete
- `cleaner.py`: `SpotVmCleaner` (registered as `oci` / `spot_instances`), its `plan()` / `apply()` and the summary log
- `adapter.py`: `SpotVM`, the class `crc.py` calls

## Public API

- `Response`: Protocol of an OCI SDK response (its data).
- `SdkInstance`: Protocol of the attributes read from an OCI compute instance.
- `SdkAttachment`: Protocol of the attributes read from a volume attachment.
- `SdkInstanceState`: Protocol of the lifecycle state read while waiting for termination.
- `SdkVolume`: Protocol of the freeform tags read from a leftover volume.
- `ComputeClient`: Protocol of the four compute calls used (list instances, list volume attachments, get instance, terminate instance); the SDK client satisfies it and tests pass fakes.
- `BlockClient`: Protocol of the two block storage calls used (get volume, delete volume).
- `RegionClients`: frozen dataclass with the compute client, block storage client and compartment of one region.
- `list_all()`: default pager, oci.pagination.list_call_get_all_results, looked up at call time.
- `clients_for()`: builds the per-region `RegionClients` factory on top of Base (compute client first, then block storage client, as on main).
- `regions_of()`: builds the region lister from get_all_regions of Base (connectivity errors fall back to the home region there).
- `SpotVmSettings`: frozen dataclass with the filters and thresholds of one run (filter tags, exception tags, notags, age, custom age tag key, allowed states, wait timings).
- `tags_protect()`: exception tags (any key) or notags (all keys) protect a resource; an untagged resource is never protected.
- `is_preemptible()`: True when the instance has a preemptible instance config.
- `is_managed_or_oke_node()`: OKE and instance pool members (crc.core.policy, oci_is_managed_or_oke_node).
- `matches_filter_tags()`: filter tags, all-keys variant; an empty filter passes.
- `is_old_instance()`: age gate with the validated custom age tag (fail closed) and the injected clock.
- `skip_reason()`: why an instance is kept, or None; state, preemptible, OKE, tags, filter, age in the order of main.
- `attached_volume_ids()`: attached, not detaching volume ids of an instance, or None when listing failed.
- `wait_terminated()`: waits (monotonic deadline, 180 s, 5 s poll) until the instance is TERMINATED or gone (404).
- `volume_passes_tag_check()`: reads a leftover volume and keeps it when unreadable or protected by tags.
- `delete_leftover()`: the only volume delete; a failure is logged and the volume left out.
- `checked_volumes()`: the volumes that pass the tag check, in order.
- `log_summary()`: the closing "No OCI preemptible instances..." and list warnings of main.
- `log_instance_error()`: the per-instance "Error processing OCI preemptible instance" line.
- `SpotVmCleaner`: the Cleaner (supports delete only, read-only plan, mutating apply, plus list_region, consider and terminate used by the adapter).
- `build_spot_vm_cleaner()`: registry factory behind @register("oci", "spot_instances"); reads filter_tags, exception_tags, notags, age (required, positive) and custom_age_tag_key from RunContext.options.
- `SpotVmRuntime`: frozen dataclass with the injected clients factory, region lister, pager and clock (now) of `SpotVM`; unset parts come from Base and the SDK.
- `SpotVM`: adapter crc.py calls (delete(), get_deleted, get_deleted_volumes, delete_count, instance_names_to_delete, volumes_to_delete, and the private hooks the hash-locked safety test uses); subclasses Service and Base; acts instance by instance in the order of main.

## Safety Invariants

- Authoritative text: `CLEANUP_SAFETY.md`. Applicable IDs: SAF-006 to SAF-013 (age gates), SAF-037 (explicit `--cloud oci`), SAF-040 (wait for `TERMINATED`, then delete volumes), SAF-041 (positive age; an empty, unparseable, invalid or zero custom age tag keeps the instance), SAF-043 (all-keys tag filter), SAF-044 (leftover volumes skipped by exception tags and notags), SAF-045 (OKE and instance pool members are skipped), SAF-046 (terminate is skipped when the attachment list fails).
- `plan()` only lists and decides (ARCH-005). In a dry run it also reads the leftover volumes to report them; no mutating call is made.
- `apply()` is the only caller of `terminate_instance` (with `preserve_boot_volume=False`) and `delete_volume`. Order per instance: terminate, wait for `TERMINATED` when volumes were attached, delete the tag-checked volumes. A failed wait still tries the volume deletes (as on main).
- A failure on one instance or volume is logged and skipped, never raised, as on main. A failed region list is skipped; the region list itself (`get_all_regions`) falls back to the home region on connectivity and auth errors.
- Known bugs and quirks (do not fix outside a behaviour-fix rung): `docs/refactor/BUGS.md` (`BUG-255` to `BUG-271`).
- Do not call real cloud APIs in tests; use fakes for `ComputeClient` and `BlockClient`.

## Extension Rules

1. Behaviour fixes are separate rungs (`docs/refactor/PLAN.md`); do not fix a quirk inside a refactor change.
2. Tag, age and OKE decisions stay in `decide.py` and call `crc.core.policy` / `crc.core.age`; no `re`, no wall-clock read, no `os.environ`, no `print` (ARCH-003). The clock is injected; only `time.monotonic` and `time.sleep` are used for the wait.
3. Every SDK call goes through `ComputeClient` or `BlockClient`; add a method to the Protocol only for a call the cleaner really makes.
4. `plan()` stays read-only: every mutating call belongs in `apply()` (`terminate()`, `delete_leftover()`).
5. Record every new bug or quirk in `docs/refactor/BUGS.md` with its `main` file:line.
