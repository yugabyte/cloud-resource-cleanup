# tests

## Purpose

Offline pytest suite (`pytest -q`, part of `make check`). No test calls a real cloud API or uses
credentials; SDK clients are mocked. Layout:

- `test_core_*.py`: pure tests of `crc/core` (age, policy, cleaner, registry, report, selection, engine, logger).
  Some compare against the frozen `main` oracle in `legacy/crc_main.txt`, or run `crc.py` by path.
- `test_notify_slack.py`, `test_notify_influx.py`: `crc/notify` with fake clients asserting the exact calls.
  `test_notify_parity.py` and `test_notify_crc_parity.py` (with `helpers_cl2_notify.py` and
  `helpers_wa1_notify.py`) run the same inputs through `legacy/crc_main.txt` (`load_legacy_crc()`)
  and the notifiers.
- `helpers_oracle.py` (tested by `test_helpers_oracle.py`): loads `legacy/crc_main.txt` with its class
  imports bound to the module oracles (`legacy/<cloud>_<kind>.txt`), so a parity test's legacy side
  runs `main`'s own classes and `crc/` holds no adapter classes.
- `test_cli_args_parity.py`, `test_cli_crc_parity.py`, `test_cli_runner_parity.py`, `test_cli_scan_parity.py`, `test_cli_bridge_parity.py`, `test_cli_bridge_<cloud>.py` (`aws`, `azu`, `gcp`, `oci`), `test_cli_main_parity.py`, `test_cli_logger.py`, `test_cli_logging_init.py` (with `helpers_cli_routes.py`): `crc/cli` (flags, the
  `CRC` run context, `--scan_tag`, `bridge` to `crc.core.engine`, `main()`, the `logger` header/footer and BUG-363 root handler initialisation) against `legacy/crc_main.txt`, on recording fakes.
- `test_crc_service_parity.py`, `test_crc_service_is_old_parity.py` (with `helpers_crc_service.py`),
  `helpers_log_fix.py`, `test_log_fix_helpers.py` and `test_crc_utils_parity.py`, `test_crc_utils.py`:
  `crc/service.py` and `crc/utils.py` against `legacy/crc_service.txt` and `legacy/crc_utils.txt`, plus the
  per-bug oracle log rewriter (`docs/logging.md`).
- `log_fix_aws_compute.py`, `log_fix_aws_misc.py`, `log_fix_azure.py`, `log_fix_gcp.py`, `log_fix_oci.py` and their
  `test_log_fix_<cloud>.py` (plus `test_aws_log_fix.py`): the per-bug log rules of each cloud
  (`RULES = {**helpers_log_fix.RULES, ...}`), passed as `rules=RULES` to `expected_logs` / `expected_outcome` by
  that cloud's parity tests.
- `test_aws_*.py`, `test_azure_*.py`, `test_azu_spot_vm_engine.py`, `test_gcp_*.py`, `test_oci_*.py`: mocked tests of the
  cloud cleaners (`crc/aws`, `crc/azu`, `crc/gcp`, `crc/oci`). A `*_parity.py` file runs the frozen
  `main` oracle in `legacy/` (see `legacy/README.md` for the full table) and the current registered `Cleaner` on the same fake SDK clients and compares calls, logs, output and errors. The
  parity files are: AWS `test_aws_base_parity.py`, `test_aws_connectivity_parity.py`,
  `test_aws_disk_parity.py`, `test_aws_ip_parity.py` (Elastic IP), `test_aws_keypair_parity.py`,
  `test_aws_kms_parity.py`, `test_aws_snapshot_parity.py`, `test_aws_spot_instance_requests_parity.py`,
  `test_aws_vm_parity.py`; Azure `test_azure_base_parity.py`,
  `test_azure_disk_parity.py`, `test_azure_ip_parity.py`, `test_azure_nic_parity.py`,
  `test_azure_spot_instances_parity.py`, `test_azure_vm_parity.py`,
  `test_azure_vm_delete_helpers_parity.py`; GCP `test_gcp_base_parity.py`, `test_gcp_disk_parity.py`,
  `test_gcp_ip_parity.py`, `test_gcp_spot_instances_parity.py`, `test_gcp_vm_parity.py`; OCI
  `test_oci_base_parity.py`, `test_oci_connectivity_parity.py`, `test_oci_disk_parity.py`,
  `test_oci_spot_instances_parity.py`, `test_oci_vm_parity.py`. The `helpers_azure_*.py` files are shared fakes of the
  Azure tests.
- `test_<cloud>_<kind>_engine.py` and `test_*_cleaner.py`: the engine-level tests of each cleaner. The
  tests that `tests/archtest/safety_lock.json` hash-locks (46 of 46 `SAF-NNN` IDs) live in these
  files and in the other engine and gap tests. No legacy safety test file is left.
- `test_jenkins_contract.py` with `fixtures/jenkins_commands.txt` (the production Jenkins commands),
  `helpers_ag_cli_jenkins.py` and `helpers_ag_cli_jenkins_expected.json`: every command parses,
  validates, routes and plans offline. `test_smoke_offline.py` covers `make smoke-offline`.
- `helpers_ag_cli_added.py` (`ADDED_FLAGS`, `ADDED_CHOICES`, `ADDED_RESOURCES`, `RESOURCE_HELP`) and
  `helpers_ag_cli_args.py`: the additions that the CLI parity tests allow beyond `legacy/crc_main.txt`.
- `legacy/`: the frozen `main` oracles (see `legacy/README.md`). `legacy_crc_notify_reference.txt` is a
  frozen copy of the old `crc.py` Slack and Influx methods outside that directory.
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

1. Name new files `test_<package>_<module>.py` and keep them typed (ruff and mypy check every test file:
   the legacy ratchet in `pyproject.toml` is empty and stays empty).
2. Cover the happy path, the fail-closed path, dry run without mutating calls, and call order for
   destructive sequences (`.cursor/skills/crc-maintain/SKILL.md`).
3. A new bug found by a test goes into `docs/refactor/BUGS.md`; the test preserves the quirk.
