# crc.aws.elastic_ips

## Purpose

AWS Elastic IP cleaner: releases unassociated, tagged Elastic IPs that match the filters in every region. Registered as `aws` / `ip` and run by `crc/cli` through `crc.core.engine`.

Modules:

- `client.py`: EC2 Protocol and default factory.
- `cleaner.py`: the Cleaner and its registry factory.

## Public API

- `ElasticIpClient`: Protocol of the two EC2 calls used, describe_addresses and release_address; tests pass fakes.
- `boto3_client()`: default EC2 client factory.
- `ElasticIpCleaner`: the Cleaner (supports, read-only plan, mutating apply, should_skip, plus per-region plan_region and apply_region).
- `build_elastic_ip_cleaner()`: registry factory behind the aws / ip registration; reads filter_tags, exception_tags and notags from RunContext.options.
- `log_summary()`: the summary lines logged once all regions are done.

## Safety Invariants

- Authoritative invariants and quirks: `crc/aws/README.md` (Safety Invariants) and `CLEANUP_SAFETY.md`.
- `plan()` and what it calls stay read-only (ARCH-005); every mutating call stays in `apply()` / `apply_region()` and never runs in a dry run. `plan()` lists every region before `apply()` releases, so a listing error stops the run before any release. Tests: `tests/test_aws_elastic_ips.py`, `tests/test_aws_elastic_ips_fix.py`, `tests/test_aws_ip_parity.py` (oracle `tests/legacy/aws_elastic_ips.txt`, never edit it; the Cleaner runs through `crc.core.engine.run`).

## Extension Rules

1. Behaviour changes need a `BUG-NNN` row and a test (`AGENTS.md`).
2. Shared AWS helpers live in `crc/aws/_common.py`; keep this package free of copies.
3. Import path `crc.aws.elastic_ips` must keep re-exporting every public name (see `__init__.py`).
4. Keep every file at 250 lines or fewer.
