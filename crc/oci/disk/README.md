# crc.oci.disk

## Purpose

OCI block volume and boot volume cleaner (`--cloud oci --resource disk`). It deletes `AVAILABLE` volumes that are not attached, match the freeform `filter_tags` (any key), are not excluded by `exception_tags` or `notags`, and are old enough. Refactored in rung OCI-1 from `crc/oci/disk.py` and `crc/oci/disk_api.py`; rung ST-OCI-DISK turned those sibling files into this subpackage (pure move, no behaviour change). The old import path `crc.oci.disk` keeps working through the package `__init__.py`.

Modules, split by responsibility:

- `api.py` (was `disk_api.py`): the typed Protocols of the OCI calls the cleaner uses, their SDK implementation and the per-region factories on top of `Base`
- `cleaner.py` (was the first part of `disk.py`): `DiskCleaner` (registered as `oci` / `disk`), its settings and runtime dataclasses and the registry factory
- `adapter.py` (was the last part of `disk.py`): `Disk`, the class `crc.py` calls

## Public API

- `SdkVolume`: Protocol of the attributes read from an OCI volume or boot volume.
- `SdkAttachment`: Protocol of the attributes read from a volume or boot volume attachment.
- `VolumeApi`: Protocol of the seven OCI calls the disk cleaner uses (list volumes, boot volumes, attachments, availability domains, boot volume attachments, delete volume, delete boot volume); tests pass fakes.
- `OciVolumeApi`: `VolumeApi` over the compute and block storage clients of one region; pages through every result.
- `api_for()`: builds the per-region `VolumeApi` factory on top of Base, which caches clients per region.
- `regions_of()`: builds the region lister from get_all_regions of Base.
- `DiskRuntime`: frozen dataclass with the injected API factory, region lister and clock (now) of `Disk`; unset parts come from Base.
- `DiskSettings`: frozen dataclass with the filters and thresholds of one run (filter tags, exception tags, notags, age, custom age tag key).
- `DiskCleaner`: the Cleaner (supports, read-only plan, mutating apply, plus list_region, attached_volume_ids, attached_boot_volume_ids, decide and remove used by the adapter).
- `build_disk_cleaner()`: registry factory behind @register("oci", "disk"); reads filter_tags, exception_tags, notags, age and custom_age_tag_key from RunContext.options.
- `Disk`: adapter crc.py calls (delete(), get_deleted, count, disk_names_to_delete); subclasses Service and Base; decides and deletes volume by volume in the order of main.

## Safety Invariants

- Authoritative text: `CLEANUP_SAFETY.md`. Applicable IDs: SAF-006 to SAF-013 (age gates, shared by all clouds).
- `plan()` only lists and decides (ARCH-005); `apply()` is the only caller of `delete_volume` / `delete_boot_volume` and never calls them in a dry run.
- Only `AVAILABLE` volumes that are not `ATTACHED` / `ATTACHING` are candidates; block and boot volumes are both covered.
- An empty age matches every volume (SAF-006) and `MAX_AGE` can shorten the age (SAF-010); the age of a volume with a custom age tag fails open to `--age` (SAF-009, `BUG-004`).
- A failed delete is logged and skipped, never raised, as on main. A connectivity error while listing attachments is swallowed as on main (`BUG-096`, `BUG-097`), and a region with no availability domains has no boot volume attachments (`BUG-098`): attached volumes can then be deleted; do not fix outside a behaviour-fix rung.
- Filtering per module: `docs/refactor/FILTER_VARIANTS.md` section 5 (freeform tags, any key). Parity oracle: `tests/legacy/oci_disk.txt`, a frozen copy of `main`, never edited. Known quirks: `docs/refactor/BUGS.md` rows `BUG-004`, `BUG-096` to `BUG-110`, `BUG-353`, `BUG-354`.
- Do not call real cloud APIs in tests; mock every SDK client.

## Extension Rules

1. No behaviour change in a refactor rung; behaviour fixes are separate rungs (`docs/refactor/PLAN.md`).
2. Keep filtering in `crc.core.policy` and age in `crc.core.age`; no clock reads or environment reads here (ARCH-003): `now` comes from `RunContext` (or the adapter clock).
3. Keep every mutating call in `apply()` / `remove()`; `plan()` and what it calls stay read-only (ARCH-005).
4. Keep the SDK behind the `VolumeApi` Protocol in `api.py`; the `Base` clients stay cached per region.
5. `tests/test_oci_disk.py` and `tests/test_oci_disk_parity.py` must stay green. Every file stays under 400 lines.
