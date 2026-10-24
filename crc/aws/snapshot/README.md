# crc.aws.snapshot

## Purpose

AWS EBS snapshot cleaner: deletes snapshots owned by the account that are at least `age_days` old, carry no exception tag and back no AMI, in every region. `SnapshotCleaner` is registered as `aws` / `snapshot`; `crc/cli` runs it through `crc.core.engine` (route in `crc/cli/routes/aws.py`).

Modules:

- `client.py`: the EC2 Protocol and the default client factory.
- `protection.py`: tag reading, exception-tag protection and AMI usage.
- `cleaner.py`: the Cleaner and its registry factory.

## Public API

- `SnapshotClient`: Protocol of the three EC2 calls used, describe_snapshots, describe_images and delete_snapshot; tests pass fakes.
- `boto3_client()`: default EC2 client factory.
- `is_tag_list()`: whether tags is a list of mappings.
- `read_tags()`: tag map of a list of tag entries; an entry without a key is ignored and a missing value reads as an empty string (BUG-144).
- `is_protected()`: whether an exception tag protects the snapshot (logs the matching key).
- `used_by_amis()`: snapshot ids that back an AMI of the account in the client's region.
- `SnapshotCleaner`: the Cleaner (supports, read-only plan, mutating apply, plus check_age, iter_region, delete_one and is_protected).
- `build_snapshot_cleaner()`: registry factory behind the aws / snapshot registration; reads age_days (default 90) and exception_tags from RunContext.options.
- `skip_failed_region()`: records a non-connectivity region error and skips the region (BUG-149).

## Safety Invariants

- Authoritative invariants and quirks: `crc/aws/README.md` (Safety Invariants) and `CLEANUP_SAFETY.md`.
- `plan()` and what it calls stay read-only (ARCH-005); every mutating call stays in `apply()` / `delete_one()` and never runs in a dry run.
- BUG-142 (not a bug: intended): every snapshot older than `--age` (fixed 90 days) without exception tags is deleted; there is no tag, name or owner guard by design.
- `plan()` lists and decides every region before `apply()` deletes. An error while listing or deciding a region is recorded, that region is skipped and deletes nothing, and the other regions continue (BUG-149); only a `describe_regions` error or an invalid `age_days` stops the run, before any delete.
- The age gate is `policy.snapshot_old_enough` on `age_days`; an invalid `age_days` raises before any call. A snapshot with an exception tag or one used by an AMI is kept. A `delete_snapshot` error is logged and kept out of the result.
- Tests: `tests/test_aws_snapshot.py`, `tests/test_aws_snapshot_fix.py` and `tests/test_aws_snapshot_parity.py` (drives the Cleaner through `crc.core.engine.run` against the oracle `tests/legacy/aws_snapshot.txt`, never edit it).

## Extension Rules

1. Behaviour changes need a `BUG-NNN` row in `docs/refactor/BUGS.md` and a test that fails without them.
2. Shared AWS helpers live in `crc/aws/_common.py`; keep this package free of copies.
3. Keep every mutating call in `apply()` / `delete_one()`.
4. Keep every file at 250 lines or fewer.
