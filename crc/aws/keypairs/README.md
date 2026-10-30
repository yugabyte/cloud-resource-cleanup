# crc.aws.keypairs

## Purpose

AWS EC2 key pair cleaner: deletes key pairs by name regex, exception regex and age in every region. `KeyPairCleaner` is registered as `aws` / `keypair`; `crc/cli` runs it through `crc.core.engine` (route in `crc/cli/routes/aws.py`).

Modules:

- `client.py`: the EC2 Protocol and the default client factory.
- `cleaner.py`: the Cleaner and its registry factory.

## Public API

- `KeyPairClient`: Protocol of the two EC2 calls used, describe_key_pairs and delete_key_pair; tests pass fakes.
- `boto3_client()`: default EC2 client factory.
- `KeyPairCleaner`: the Cleaner (supports, read-only plan, mutating apply, plus per-region plan_region and apply_region).
- `build_keypair_cleaner()`: registry factory behind the aws / keypair registration; reads name_regex, exception_regex and age from RunContext.options.
- `log_summary()`: the summary lines logged once all regions are done.

## Safety Invariants

- Authoritative invariants and quirks: `crc/aws/README.md` (Safety Invariants) and `CLEANUP_SAFETY.md`. Through the engine `plan()` lists every region before `apply()` deletes: a listing error in a later region stops the run before any delete. In a dry run `plan()` logs the `would_delete` actions per region, since the engine never calls `apply()` then.
- `plan()` and what it calls stay read-only (ARCH-005); every mutating call stays in `apply()` / `apply_region()` and never runs in a dry run. Tests: `tests/test_aws_keypairs.py`, `tests/test_aws_keypair_parity.py` (oracle `tests/legacy/aws_keypairs.txt`, never edit it).

## Extension Rules

1. Behaviour changes need a `BUG-NNN` row in `docs/refactor/BUGS.md` and a test that fails without them.
2. `crc/cli` runs the Cleaner through `crc.core.engine` (route `aws` / `keypair` in `crc/cli/routes/aws.py`).
3. Shared AWS helpers live in `crc/aws/_common.py`; keep this package free of copies.
4. Keep every file at 250 lines or fewer.
