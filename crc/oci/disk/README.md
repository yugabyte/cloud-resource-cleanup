# crc.oci.disk

## Purpose

OCI block volume and boot volume cleaner (`--cloud oci --resource disk`). It deletes `AVAILABLE` volumes that are not attached, match the freeform `filter_tags` (any key), are not excluded by `exception_tags` or `notags`, and are old enough. `crc/cli/routes/oci.py` routes the pair through `crc.core.engine` to the registered `DiskCleaner`.

Modules, split by responsibility:

- `api.py`: the typed Protocols of the OCI calls the cleaner uses, their SDK implementation and the per-region factories on top of `Base`
- `settings.py`: `DiskSettings`, the skip vocabulary and the summary log
- `attachments.py`: which volumes and boot volumes are attached (so kept) and the dry-run ACTION lines of `plan()`
- `decide.py`: state, attachment, tag and age checks and small helpers over decisions and attachments
- `cleaner.py`: `DiskCleaner` (registered as `oci` / `disk`), re-exports of the settings names, and the registry factory

## Public API

- `SdkVolume`: Protocol of the attributes read from an OCI volume or boot volume.
- `SdkAttachment`: Protocol of the attributes read from a volume or boot volume attachment.
- `VolumeApi`: Protocol of the seven OCI calls the disk cleaner uses (list volumes, boot volumes, attachments, availability domains, boot volume attachments, delete volume, delete boot volume); tests pass fakes.
- `OciVolumeApi`: `VolumeApi` over the compute and block storage clients of one region; pages through every result.
- `api_for()`: builds the per-region `VolumeApi` factory on top of Base, which caches clients per region.
- `regions_of()`: builds the region lister from get_all_regions of Base.
- `DiskSettings`: frozen dataclass with the filters and thresholds of one run (filter tags, exception tags, notags, age, custom age tag key).
- `attached_volume_ids()`, `attached_boot_volume_ids()`: ids of block and boot volumes in use, or nothing when unknown (module attachments; `DiskCleaner` wraps them).
- `note_dry_run()`: logs the ACTION lines of a dry run and collects the display names (module attachments).
- `DiskCleaner`: the Cleaner (supports, read-only plan, mutating apply, list_region, attached_volume_ids, attached_boot_volume_ids (both return None when the attachments are unknown), decide and remove used by plan and apply); built by the registry factory with the engine flag, see Safety Invariants.
- `build_disk_cleaner()`: registry factory behind @register("oci", "disk"); reads filter_tags, exception_tags, notags, age and custom_age_tag_key from RunContext.options.
- `skip_reason()`: why a volume is kept (state, attachment, tag, age), or None for a candidate; used by DiskCleaner.decide.
- `is_old()`: older than age or the volume's own custom age tag (capped by MAX_AGE).
- `in_use()`: the attachments that still claim their volume (ATTACHED or ATTACHING).
- `sort_decisions()`: appends each decision to the candidate or skipped list in order.
- `log_summary()`: the closing log lines of a disk run (settings module).

## Safety Invariants

- Authoritative text: `CLEANUP_SAFETY.md`. Applicable IDs: SAF-006 to SAF-013 (age gates, shared by all clouds).
- `plan()` only lists and decides (ARCH-005); `apply()` is the only caller of `delete_volume` / `delete_boot_volume` and never calls them in a dry run.
- Only `AVAILABLE` volumes that are not `ATTACHED` / `ATTACHING` are candidates; block and boot volumes are both covered.
- An empty age matches every volume (SAF-006) and `MAX_AGE` can shorten the age (SAF-010); a volume whose custom age tag is present but bad is kept (`get_retention_age` returns `KEEP_AGE`; SAF-009, `BUG-004` fixed).
- A failed delete is logged and skipped, never raised.
- Engine path: the factory builds `DiskCleaner(..., engine=True)`, so a dry-run `plan()` logs the would_delete ACTION lines per region and names its candidates by display name, because `crc.core.engine` never calls `apply()` in a dry run. (`crc/cli/routes/oci.py`, `NotifyMode.REPORT_THEN_RERAISE`, BUG-408): `plan()` lists every region before `apply()` deletes, so a listing error in a later region stops the pair before any delete; `partial_succeeded` holds the names listed so far in a dry run and the names deleted so far in `apply()`, which the bridge reports before it re-raises.
- Fail closed per region (`BUG-096`, `BUG-097`, `BUG-098`): `attached_volume_ids()` and `attached_boot_volume_ids()` return `None` when the attachments are unknown, and `decide()` then keeps every block volume (or boot volume) of that region (skip reasons `block volume attachments unknown` / `boot volume attachments unknown`, run-log skip `attachments_unknown`). Unknown means: a connectivity or auth error (`CONNECTIVITY_ERRORS`) while listing volume attachments, while listing availability domains or while listing the attachments of any one availability domain (a partial set is discarded), or a region with no availability domains. The region is recorded once as `region_skipped` and a WARNING `Region <r>: keeping every disk|boot disk: ...` is logged. The other volume kind of the region and other regions are decided as before; an unexpected (non-connectivity) error still propagates.
- Filtering: freeform tags, any key. Parity oracle: `tests/legacy/oci_disk.txt`, a frozen copy of `main`, never edited. Known quirks: `docs/refactor/BUGS.md` rows `BUG-004`, `BUG-096` to `BUG-110`, `BUG-353`, `BUG-354`.
- Do not call real cloud APIs in tests; mock every SDK client.

## Extension Rules

1. Behaviour fixes name their `BUG-NNN` (`docs/refactor/BUGS.md`); do not fix a quirk inside an unrelated change.
2. Keep filtering in `crc.core.policy` and age in `crc.core.age`; no clock reads or environment reads here (ARCH-003): `now` comes from `RunContext`.
3. Keep every mutating call in `apply()` / `remove()`; `plan()` and what it calls stay read-only (ARCH-005).
4. Keep the SDK behind the `VolumeApi` Protocol in `api.py`; the `Base` clients stay cached per region.
5. `tests/test_oci_disk.py`, `tests/test_oci_disk_fix.py` and `tests/test_oci_disk_parity.py` must stay green.
