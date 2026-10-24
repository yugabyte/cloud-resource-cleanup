# crc.azu.disk

## Purpose

Azure managed disk cleaner. `AzureDiskCleaner` is a registered `Cleaner` (`@register("azure", "disk")`) that deletes unattached disks passing the tag filters and the age gate. The CLI runs it through `crc.core.engine` (`crc/cli/routes/azu.py`).

Modules:

- `client.py`: SDK Protocols
- `cleaner.py`: `DiskSettings`, `AzureDiskCleaner` and the registry factory

## Public API

- `SdkDisk`: Protocol of the Azure disk fields read (name, tags, time_created, disk_state).
- `DiskOperations`: Protocol of the two disks calls used, list_by_resource_group and begin_delete (positional arguments); tests pass fakes.
- `DiskComputeClient`: Protocol of the compute client part used (the disks operations).
- `DiskSettings`: frozen dataclass of the filters and age gate (filter_tags, exception_tags, notags, age, custom_age_tag_key, disk_states).
- `AzureDiskCleaner`: the Cleaner (supports, read-only plan, mutating apply, plus list_disks, decide and delete_disk, which returns None or the error text and never raises); the client factory is injected through the constructor.
- `build_disk_cleaner()`: registry factory behind @register("azure", "disk"); reads resource_group, filter_tags, exception_tags, notags, age and custom_age_tag_key from RunContext.options and builds the client from the Azure base.
- `DeletePoller`: Protocol of the poller disks.begin_delete returns (result()).
- `DEFAULT_DISK_STATES`: the default allowed disk state, Unattached.

## Safety Invariants

- Authoritative text: `CLEANUP_SAFETY.md`; the per-module invariants are in `crc/azu/README.md` (Azure disks). `plan()` makes no mutating call and never in a dry run (ARCH-005); the delete calls run from `apply()` only.
- Each delete is isolated and its poller awaited; age through `crc.core.age`; an empty `filter_tags` selects every disk, so a real run needs a positive `--age` (BUG-173): `build_disk_cleaner` raises `ValueError` naming `--age` before any cloud call; a dry run needs none.
- Do not call real cloud APIs in tests; mock every SDK client.

## Extension Rules

1. Keep call order, log text and swallowed errors unless a `BUG-NNN` row says otherwise.
2. Reach the SDK only through the Protocols in `client.py`; shared log and skip helpers live in `crc/azu/_common.py`.
3. Update this README with any public change.
