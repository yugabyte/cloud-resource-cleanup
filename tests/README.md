# tests

## Purpose

Offline pytest suite (`pytest -q`, part of `make check`). No test calls a real cloud API or uses
credentials; SDK clients are mocked. Layout:

- `test_core_*.py`: pure tests of `crc/core` (age, policy, cleaner, registry, report, selection, engine, logger).
  Some compare against `crc.py` itself, loaded under another module name.
- `test_notify_slack.py`, `test_notify_influx.py`: `crc/notify` with fake clients asserting the exact calls.
  `test_notify_parity.py` runs the same inputs through `legacy_crc_notify_reference.txt` (a frozen
  copy of the old `crc.py` Slack and Influx methods, not a Python module), the current `crc.py` and
  the notifiers.
- `test_cli_args_parity.py`, `test_cli_crc_parity.py`, `test_cli_main_parity.py`, `test_cli_logger.py`: `crc/cli` (flags, the
  `CRC` class, `main()`, `logger` header/footer wiring) against `legacy/crc_main.txt`, on recording fakes.
- `test_crc_service_parity.py`, `test_crc_service_is_old_parity.py` (with `helpers_crc_service.py`),
  `helpers_log_fix.py`, `test_log_fix_helpers.py` and `test_crc_utils_parity.py`, `test_crc_utils.py`:
  `crc/service.py` and `crc/utils.py` against `legacy/crc_service.txt` and `legacy/crc_utils.txt`, plus the
  per-bug oracle log rewriter of the LOG rungs (`docs/refactor/LOGGING.md`).
- `log_fix_aws_compute.py`, `log_fix_aws_misc.py`, `log_fix_azure.py`, `log_fix_gcp.py`, `log_fix_oci.py` and their
  `test_log_fix_<lane>.py` (plus `test_aws_log_fix.py`): the per-bug log rules of each LOG cloud lane
  (`RULES = {**helpers_log_fix.RULES, ...}`), passed as `rules=RULES` to `expected_logs` / `expected_outcome` by
  that cloud's parity tests. Each file arrives with its lane branch (`logging/aws-compute`, `logging/aws-misc`,
  `logging/azure`, `logging/gcp`, `logging/oci`); lanes never edit `helpers_log_fix.py` or this README.
- `test_aws_*.py`, `test_azure_*.py`, `test_azu_spot_vm.py`, `test_gcp_*.py`, `test_oci_*.py`: mocked tests of the
  refactored cloud cleaners (`crc/aws`, `crc/azu`, `crc/gcp`, `crc/oci`). A `*_parity.py` file runs the frozen
  `main` oracle in `legacy/` (see `legacy/README.md` for the full table) and the refactored module (adapter
  and registered `Cleaner`) on the same fake SDK clients and compares calls, logs, output and errors. The
  parity files are: AWS `test_aws_base_parity.py`, `test_aws_connectivity_parity.py`,
  `test_aws_disk_parity.py`, `test_aws_ip_parity.py` (Elastic IP), `test_aws_keypair_parity.py`,
  `test_aws_kms_parity.py`, `test_aws_snapshot_parity.py`, `test_aws_spot_instance_requests_parity.py`,
  `test_aws_vm_parity.py`, `test_aws_vpc_parity.py`; Azure `test_azure_base_parity.py`,
  `test_azure_disk_parity.py`, `test_azure_ip_parity.py`, `test_azure_nic_parity.py`,
  `test_azure_spot_instances_parity.py`, `test_azure_vm_parity.py`,
  `test_azure_vm_delete_helpers_parity.py`; GCP `test_gcp_base_parity.py`, `test_gcp_disk_parity.py`,
  `test_gcp_ip_parity.py`, `test_gcp_spot_instances_parity.py`, `test_gcp_vm_parity.py`; OCI
  `test_oci_base_parity.py`, `test_oci_connectivity_parity.py`, `test_oci_disk_parity.py`,
  `test_oci_spot_instances_parity.py`, `test_oci_vm_parity.py`. The `helpers_azure_*.py` files are shared fakes of the
  Azure tests.
- Five test files are the hash-locked safety tests (the only entries left in the legacy ratchet, so ruff
  and mypy skip them): `test_aws_disk.py`, `test_aws_spot_instance_requests.py`, `test_azu_spot_vm.py`,
  `test_gcp_spot_vm.py`, `test_oci_spot_vm.py`. They build the adapter classes directly.
- `legacy/`: the frozen `main` oracles (see `legacy/README.md`). `legacy_crc_notify_reference.txt` is the
  Slack and Influx oracle outside that directory.
- `archtest/`: mechanical architecture checks (see `tests/archtest/README.md`).

## Public API

This directory exposes no library. Run everything with `make check`, or only tests with `pytest -q`.
`tests/__init__.py` makes `tests.archtest` importable.

## Safety Invariants

- Never delete, skip, xfail, weaken or loosen a test; add or strengthen only. The test count never drops.
- Tests mapped in `tests/archtest/safety_lock.json` are hash-locked; changing one needs the owner
  (`tests/archtest/README.md`).
- Mock every SDK client; no credentials, no network. `plan()` tests assert no mutating call was made.
- Use `tmp_path` and `monkeypatch`; no shared mutable state, no autouse session fixtures.

## Extension Rules

1. Name new files `test_<package>_<module>.py` and keep them typed (they are checked by ruff and mypy
   unless they are one of the five hash-locked files above; the legacy ratchet may only shrink).
2. Cover the happy path, the fail-closed path, dry run without mutating calls, and call order for
   destructive sequences (`.cursor/skills/crc-python-refactor/SKILL.md`, section 5).
3. A new bug found by a test goes into `docs/refactor/BUGS.md`; the test preserves the quirk.
