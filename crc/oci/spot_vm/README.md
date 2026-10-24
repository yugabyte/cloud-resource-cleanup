# crc.oci.spot_vm

## Purpose

Cleaner for OCI preemptible ("spot") instances (`--cloud oci --resource spot_instances`). It
terminates preemptible instances that match the filter tags and are old enough, then deletes the
block volumes that were attached to them. `crc/cli/routes/oci.py` routes the pair through
`crc.core.engine`.

Modules, split by responsibility:

- `client.py`: the typed Protocols of the OCI calls used, the region client bundle and the factories on top of `Base`
- `decide.py`: state, preemptible, OKE, tag and age decisions (through `crc.core.policy` and `crc.core.age`)
- `leftovers.py`: attached volume discovery, the wait for `TERMINATED` and the leftover volume tag check and delete
- `report.py`: summary and failure logging and the dry-run action records
- `terminate.py`: terminating one planned instance, its leftover volumes and the dry-run volume check
- `cleaner.py`: `SpotVmCleaner` (registered as `oci` / `spot_instances`) with its `plan()` / `apply()`, and the registry factory

## Public API

- `Response`: Protocol of an OCI SDK response (its data).
- `SdkInstance`: Protocol of the attributes read from an OCI compute instance.
- `SdkAttachment`: Protocol of the attributes read from a volume attachment.
- `SdkInstanceState`: Protocol of the lifecycle state read while waiting for termination.
- `SdkVolume`: Protocol of the freeform tags and creation time read from a leftover volume.
- `ComputeClient`: Protocol of the four compute calls used (list instances, list volume attachments, get instance, terminate instance); the SDK client satisfies it and tests pass fakes.
- `BlockClient`: Protocol of the two block storage calls used (get volume, delete volume).
- `RegionClients`: frozen dataclass with the compute client, block storage client and compartment of one region.
- `list_all()`: default pager, oci.pagination.list_call_get_all_results, looked up at call time.
- `clients_for()`: builds the per-region `RegionClients` factory on top of Base (compute client first, then block storage client).
- `regions_of()`: builds the region lister from get_all_regions of Base (connectivity errors fall back to the home region there).
- `SpotVmSettings`: frozen dataclass with the filters and thresholds of one run (filter tags, exception tags, notags, age, custom age tag key, allowed states, wait timings).
- `tags_protect()`: exception tags (any key) or notags (all keys) protect a resource; an untagged resource is never protected.
- `is_preemptible()`: True when the instance has a preemptible instance config.
- `is_managed_or_oke_node()`: OKE and instance pool members (crc.core.policy, oci_is_managed_or_oke_node).
- `matches_filter_tags()`: filter tags, all-keys variant; an empty filter passes.
- `is_old_instance()`: age gate with the validated custom age tag (fail closed) and the injected clock.
- `skip_reason()`: why an instance is kept, or None; state, preemptible, OKE, tags, filter, age in a fixed order.
- `attached_volume_ids()`: attached, not detaching volume ids of an instance, or None when listing failed.
- `wait_terminated()`: waits (monotonic deadline, 180 s, 5 s poll) until the instance is TERMINATED or gone (404).
- `VolumeOwner`: frozen dataclass naming the terminated instance (id, creation time, the attachment lister of the compute client, compartment, pager) a leftover volume is judged against.
- `volume_owned()`: BUG-255 check: True only when the volume was created at or after the instance and no other instance has it attached; anything uncertain keeps the volume.
- `volume_passes_tag_check()`: reads a leftover volume and keeps it when unreadable, protected by tags or (with an owner) not owned by the instance.
- `delete_leftover()`: the only volume delete; a failure is logged and the volume left out.
- `checked_volumes()`: the volumes that pass the tag and ownership check, in order.
- `log_summary()`: the closing "No OCI preemptible instances..." and list warnings.
- `log_instance_error()`: the per-instance "Error processing OCI preemptible instance" line.
- `SpotVmCleaner`: the Cleaner (supports delete only, read-only plan, mutating apply, plus list_region, consider and terminate); built by the registry factory with the engine flag, see Safety Invariants.
- `build_spot_vm_cleaner()`: registry factory behind @register("oci", "spot_instances"); reads filter_tags, exception_tags, notags, age (required, positive) and custom_age_tag_key from RunContext.options.
- `record_dry_run()`: one dry-run ACTION record per planned instance and its leftover volumes (report module).
- `terminate_instance()`: terminates one planned instance, waits for it and deletes its leftover volumes (terminate module).
- `with_checked_volumes()`: in a dry run, the candidate with the leftover volumes that pass the tag check (terminate module).
- `owner_of()`: the `VolumeOwner` of a planned instance used by the leftover tag check (terminate module).

## Safety Invariants

- Authoritative text: `CLEANUP_SAFETY.md`. Applicable IDs: SAF-006 to SAF-013 (age gates), SAF-037 (explicit `--cloud oci`), SAF-040 (wait for `TERMINATED`, then delete volumes), SAF-041 (positive age; an empty, unparseable, invalid or zero custom age tag keeps the instance), SAF-043 (all-keys tag filter), SAF-044 (leftover volumes skipped by exception tags and notags), SAF-045 (OKE and instance pool members are skipped), SAF-046 (terminate is skipped when the attachment list fails).
- `plan()` only lists and decides (ARCH-005). In a dry run it also reads the leftover volumes to report them; no mutating call is made. The registry factory builds `SpotVmCleaner(..., engine=True)`, so a dry-run `plan()` logs the would_delete ACTION lines per region (for each instance and its leftover volumes) and names its candidates by display name, because `crc.core.engine` never calls `apply()` in a dry run.
- `apply()` is the only caller of `terminate_instance` (with `preserve_boot_volume=False`) and `delete_volume`. Order per instance: terminate, wait for `TERMINATED` when volumes were attached, delete the tag-checked volumes. A failed wait still tries the volume deletes.
- A failure on one instance or volume is logged and skipped, never raised. A failed region list is skipped; the region list itself (`get_all_regions`) falls back to the home region on connectivity and auth errors.
- BUG-255 (fixed): a leftover volume is deleted only when it was created at or after the instance's `time_created` and is attached to no other instance (an attachment that is not `DETACHED` counts; the lookup is `list_volume_attachments(compartment_id, volume_id=...)`, so only the run's compartment is seen). Otherwise it is kept with skip reason `not_owned` (DEBUG log with the cause). A missing or incomparable creation time, or a failed attachment read, keeps the volume. The terminated instance itself is ignored in the attachment list. The check runs after the tag check, in `plan()` of a dry run and in `apply()`; it only adds reads.
- Known bugs and quirks (do not fix outside their own change): `docs/refactor/BUGS.md` (`BUG-256` to `BUG-271`; `BUG-255` is fixed). BUG-255 still leaves `filter_tags` and `age` unapplied to the volume: only ownership was added.
- Do not call real cloud APIs in tests; use fakes for `ComputeClient` and `BlockClient`.

## Extension Rules

1. A behaviour fix names its `BUG-NNN` (`docs/refactor/BUGS.md`); do not fix a quirk inside an unrelated change.
2. Tag, age and OKE decisions stay in `decide.py` and call `crc.core.policy` / `crc.core.age`; no `re`, no wall-clock read, no `os.environ`, no `print` (ARCH-003). The clock is injected; only `time.monotonic` and `time.sleep` are used for the wait.
3. Every SDK call goes through `ComputeClient` or `BlockClient`; add a method to the Protocol only for a call the cleaner really makes.
4. `plan()` stays read-only: every mutating call belongs in `apply()` (`terminate()`, `delete_leftover()`).
5. Record every new bug or quirk in `docs/refactor/BUGS.md` (a new row cites the current `file:line` in `crc/`).
