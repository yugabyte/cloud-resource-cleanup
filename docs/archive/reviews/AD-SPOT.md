# AD-SPOT review

## Coding summary

Partial, green state (time limit). No adapter class was deleted in this pass; all five
(`crc.aws.disk.Disk`, `crc.aws.spot_instance_requests.SpotInstanceRequests`, `crc.azu.spot_vm.SpotVM`,
`crc.gcp.spot_vm.SpotVM`, `crc.oci.spot_vm.SpotVM`) are still importable.

- Routed: production already runs every `(cloud, kind)` through `crc/cli/bridge.py` and the registered
  Cleaners; no production module in `crc/` or `crc.py` imports any of the five adapters (checked with
  `git grep`). Nothing in this lane touched production code.
- Done: `tests/test_oci_spot_instances_parity.py` no longer builds the OCI `SpotVM` adapter. Every case
  (same `CASES`, quirks, BUG-255, BUG-358, repeated-delete) now drives the registered Cleaner through
  `crc.core.engine.run` (new helper `tests/helpers_ad_spot_engine.py`) and is compared with the
  oracle `tests/legacy/oci_spot_vm.txt`. The adapter-only test `test_adapter_matches_main` became
  `test_engine_matches_main` (same cases: error, stdout, resources, mutation order). The
  repeated-delete test runs the engine twice; it compares mutating calls in order, `list_*` reads and
  non-summary logs as sets (the engine does not accumulate `get_deleted` across runs, and a second
  adapter run polled `get_instance` a different number of times).
- Test count: 44417 before and after (floor 44417, ARCH-008 green). `make check` green.
- CLI parity tests (`test_cli_*_parity.py`) were not changed in this pass.

## Owner action

None found: the hash-locked tests (`tests/archtest/safety_lock.json`) are all per function and none of
the locked functions builds an adapter (they use `*_engine.py`, `*Cleaner` and `crc.cli` tests).
Note that unlocked tests in locked files still build adapters (see Left).

## Left (unrouted for deletion; the adapters stay until these are retargeted)

Delete each adapter module, its package export and its README Public API line after retargeting:

- OCI `SpotVM` (`crc/oci/spot_vm/adapter.py`, `SpotVmRuntime`): `tests/test_oci_logger.py:384`,
  `tests/test_oci_spot_instances.py:228`.
- GCP `SpotVM` (`crc/gcp/spot_vm/adapter.py`, `settings.py` if only it uses it):
  `tests/test_gcp_spot_instances_parity.py` (adapter path), `tests/test_gcp_spot_instances.py:170`,
  `tests/test_gcp_spot_vm_fix.py:119,165,269`.
- Azure `SpotVM` (class in `crc/azu/spot_vm/__init__.py`, `config.py`/`removal.py` helpers it alone
  uses): `tests/test_azure_spot_instances_parity.py`, `tests/test_azure_logger.py:354`.
- AWS `SpotInstanceRequests` (`crc/aws/spot_instance_requests/adapter.py`, `runtime.py`):
  `tests/test_aws_spot_instance_requests_parity.py`, `tests/test_aws_spot_instance_requests_cleaner.py:140`.
- AWS `Disk` (class in `crc/aws/disk/__init__.py`): `tests/test_aws_disk_parity.py`,
  `tests/test_aws_disk_fix.py:214,265`, `tests/test_aws_disk_cleaner.py:191`.
- Strings `OCI_SpotVM`, `AZU_SpotVM`, `GCP_SpotVM`, `SpotInstanceRequests` in `tests/helpers_cl2_crc.py`,
  `tests/test_cli_bridge_*.py`, `tests/test_core_engine.py` name the frozen oracle's classes (not the
  adapters); review before the final cleanup.

Pattern to reuse: `tests/helpers_ad_spot_engine.run_through_engine(cloud, kind, factory, context)`.

## New bugs

None found.


## Round 1 (Argon)

Scope reviewed: `git diff 7a8c936..HEAD` on branch `ad/spot` (lane `AD-SPOT`).

### Findings & fixes made in review

The initial commit (`2ef9b22`) retargeted only `tests/test_oci_spot_instances_parity.py` and left all five adapter classes (`crc.aws.disk.Disk`, `crc.aws.spot_instance_requests.SpotInstanceRequests`, `crc.azu.spot_vm.SpotVM`, `crc.gcp.spot_vm.SpotVM`, `crc.oci.spot_vm.SpotVM`) in place. In Round 1, I completed the remaining retargeting and deleted all five adapter classes and their package exports/README entries:

1. **OCI `SpotVM` and `SpotVmRuntime`** (`crc/oci/spot_vm/adapter.py`, `crc/oci/spot_vm/__init__.py`, `crc/oci/spot_vm/cleaner.py`, `crc/oci/spot_vm/README.md`, `crc/oci/README.md`, `tests/test_oci_spot_instances.py`, `tests/test_oci_logger.py`):
   - Retargeted `tests/test_oci_spot_instances.py::test_engine_runs_cleaner_with_injected_fakes` and the three spot tests in `tests/test_oci_logger.py` (`test_spot_vm_engine_reports_instance_and_volume_actions`, `test_spot_vm_protected_leftover_volume_is_debug_and_counted`, `test_spot_vm_attachment_failure_is_a_listed_failure`) to run `SpotVmCleaner(..., engine=True)` through `crc.core.engine.run` (`run_through_engine`).
   - Deleted `crc/oci/spot_vm/adapter.py` (`SpotVM`, `SpotVmRuntime`) and removed its exports and Public API lines.
2. **GCP `SpotVM`** (`crc/gcp/spot_vm/adapter.py`, `crc/gcp/spot_vm/__init__.py`, `crc/gcp/spot_vm/README.md`, `tests/test_gcp_spot_instances_parity.py`, `tests/test_gcp_spot_instances.py`, `tests/test_gcp_spot_vm_fix.py`):
   - Retargeted `tests/test_gcp_spot_instances_parity.py` (`test_engine_matches_main`, `test_quirk_is_the_same_on_main_and_new` with `ENGINE_QUIRKS` for `zone-without-name-after-first-zone`, `test_bug_242_leftover_disk_custom_age_main_deletes_new_code_keeps`, `test_fixed_quirk_main_deletes_new_code_keeps` for `BUG-228`, `test_bug_358_leftover_disk_skip_is_debug_not_info`), `tests/test_gcp_spot_instances.py::test_engine_runs_the_cleaner_with_an_injected_client`, and the three adapter tests in `tests/test_gcp_spot_vm_fix.py` (`BUG-228`, `BUG-229`, `BUG-242`) to run `SpotVMCleaner` / `build_spot_cleaner` through `crc.core.engine.run`.
   - Deleted `crc/gcp/spot_vm/adapter.py` (`SpotVM`) and removed its exports and Public API lines.
3. **Azure `SpotVM`** (`crc/azu/spot_vm/__init__.py`, `crc/azu/spot_vm/removal.py`, `crc/azu/spot_vm/README.md`, `crc/azu/README.md`, `tests/test_azure_spot_instances_parity.py`, `tests/test_azure_logger.py`):
   - Retargeted `tests/test_azure_spot_instances_parity.py` (`test_engine_matches_main`, `test_leftover_disk_skip_lines_diverge_from_main_in_level_only` for `BUG-356`, `test_repeated_delete_accumulates_lists_as_main`) and `tests/test_azure_logger.py` (`test_spot_real_run_reports_region_action_skips_and_failures`, `test_spot_dry_run_and_leftover_disk_skips_are_debug`) to run `build_spot_vm_cleaner` through `crc.core.engine.run`.
   - Deleted `SpotVM` and its `_delete_vm_via_helper` / `_delete_nic_via_helper` shims from `crc/azu/spot_vm/__init__.py`, and updated `crc/azu/spot_vm/README.md` and `crc/azu/README.md`.
4. **AWS `SpotInstanceRequests`** (`crc/aws/spot_instance_requests/adapter.py`, `crc/aws/spot_instance_requests/__init__.py`, `crc/aws/spot_instance_requests/README.md`, `crc/aws/README.md`, `tests/test_aws_spot_instance_requests_parity.py`, `tests/test_aws_spot_instance_requests_cleaner.py`):
   - Retargeted `tests/test_aws_spot_instance_requests_parity.py` (`test_engine_matches_main`, `test_skip_lines_are_debug_only_in_new_code` for `BUG-356`) to run `build_spot_request_cleaner` through `crc.core.engine.run`, and updated `tests/test_aws_spot_instance_requests_cleaner.py::test_cleaner_keeps_settings_and_runtime_attributes`.
   - Deleted `crc/aws/spot_instance_requests/adapter.py` (`SpotInstanceRequests`) and removed its exports and Public API lines.
5. **AWS `Disk`** (`crc/aws/disk/__init__.py`, `crc/aws/disk/cleaner.py`, `crc/aws/disk/README.md`, `crc/aws/README.md`, `tests/test_aws_disk_parity.py`, `tests/test_aws_disk_cleaner.py`, `tests/test_aws_disk_fix.py`):
   - Retargeted `tests/test_aws_disk_parity.py` (`test_engine_matches_main`, `test_skip_lines_are_debug_only_in_new_code` for `BUG-356`, `test_quirk_is_preserved` with `BUG_004_FIXED`, `test_fixed_quirk_is_pinned` for `BUG-282` and `BUG-283`, `test_second_delete_accumulates_and_count_logs_like_main`, `test_cleaner_helpers_match_main`), `tests/test_aws_disk_cleaner.py::test_engine_runs_cleaner_with_injected_runtime`, and `tests/test_aws_disk_fix.py` (`test_production_command_1_end_to_end`, `test_production_command_2_end_to_end`) to run `DiskCleaner` / `build_disk_cleaner` through `crc.core.engine.run`.
   - Deleted `Disk` (and its private `_ec2`, `_cloudwatch`, `_regions`, `Clock`, `_UTC_CLOCK` helpers) from `crc/aws/disk/__init__.py`, and updated `crc/aws/disk/README.md` and `crc/aws/README.md`.

### Checklist verification

1. **Nothing production uses was deleted**: `crc/cli/scan.py`, `crc/cli/routes/**`, and all registered `@register` cleaner factories (`build_disk_cleaner`, `build_spot_request_cleaner`, `build_spot_vm_cleaner`, `build_spot_cleaner`) are untouched. Following `OWN-1`, no hash-locked safety test in `tests/archtest/safety_lock.json` builds any of the five deleted adapters (`SAF-LOCK` green).
2. **Module parity tests drive the registered Cleaner through `crc.core.engine.run`**:
   - `tests/test_aws_disk_parity.py` (`tests/legacy/aws_disk.txt`), `tests/test_aws_spot_instance_requests_parity.py` (`tests/legacy/aws_spot_instance_requests.txt`), `tests/test_azure_spot_instances_parity.py` (`tests/legacy/azu_spot_vm.txt`), `tests/test_gcp_spot_instances_parity.py` (`tests/legacy/gcp_spot_vm.txt`), and `tests/test_oci_spot_instances_parity.py` (`tests/legacy/oci_spot_vm.txt`) all drive the registered Cleaners through `crc.core.engine.run` with all oracle cases, SDK mutation and call-order checks, log line comparisons, stdout/error checks, and `BUG-NNN` divergences (`BUG-004`, `BUG-228`, `BUG-242`, `BUG-255`, `BUG-282`, `BUG-283`, `BUG-356`, `BUG-358`) preserved.
   - Local sabotage verification (reverted without committing): turning `SpotVmCleaner.terminate` (OCI) and `DiskCleaner._delete_volume` (AWS) into no-ops caused 442 test failures across `tests/test_oci_spot_instances_parity.py` and `tests/test_aws_disk_parity.py`.
3. **Deleted names gone & READMEs trimmed**:
   - `git grep` across `crc/`, `crc.py`, and `tests/` confirms `crc.aws.disk.Disk`, `crc.aws.spot_instance_requests.SpotInstanceRequests`, `crc.azu.spot_vm.SpotVM`, `crc.gcp.spot_vm.SpotVM`, `crc.oci.spot_vm.SpotVM`, and `SpotVmRuntime` are gone and unreferenced outside frozen oracle loaders (`tests/legacy/*`, `tests/helpers_cl2_crc.py`, `tests/test_cli_bridge_*.py`, `tests/test_core_engine.py`, `tests/test_core_report.py`).
   - `crc/aws/disk/README.md`, `crc/aws/spot_instance_requests/README.md`, `crc/azu/spot_vm/README.md`, `crc/gcp/spot_vm/README.md`, `crc/oci/spot_vm/README.md`, and the corresponding lines in `crc/{aws,azu,oci}/README.md` describe only the current code with no rung, refactor, legacy-path, or deleted-adapter wording and no links to moved docs.
4. **No hash-locked test, oracle, or archtest file touched**:
   - All 94 locked functions in `tests/archtest/safety_lock.json` are untouched (`SAF-LOCK` green), and no `tests/legacy/*.txt` or `tests/archtest/**` file was modified.
5. **Size limits & Public API sync**:
   - Every touched file in `crc/` is at most 250 lines (`crc/gcp/spot_vm/cleaner.py`: 245, `crc/azu/spot_vm/cleaner.py`: 248, `crc/oci/spot_vm/cleaner.py`: 220, `crc/aws/disk/cleaner.py`: 220, `crc/aws/spot_instance_requests/cleaner.py`: 196) and every function is at most 60 lines (`ARCH-004` green). Package `Public API` sections are in sync (`ARCH-002` green).
6. **Gates & test count**:
   - After `pip install -r requirements.txt -r requirements-dev.txt`, `make check` passed in `1m02.6s` wall time:
     - `python3 -m ruff check .`: passed
     - `python3 -m mypy`: passed (`Success: no issues found in 367 source files`)
     - `lint-imports`: passed (`5 kept, 0 broken`)
     - `python3 -m pytest -q -n auto`: **44417 passed in 55.69s**
     - `python3 -m tests.archtest.test_count check`: `ARCH-008: 44417 tests, floor 44417`

VERDICT: APPROVE


## Round 2 (Opus)

Scope: `git diff 7a8c936..HEAD` on `ad/spot` (commits `2ef9b22`, `71f5741`). Round 1's claims
were re-checked independently; they hold.

### Findings

1. Nothing production uses was deleted: the diff touches no file in `crc/cli/**`, `crc.py` or
   `crc/core/**`; the five `@register` factories (`aws/disk`, `aws/spot_instance_requests`,
   `azure|gcp|oci/spot_instances`) are in place. `crc/aws/spot_instance_requests/runtime.py`
   (`SpotRuntime`, `Clock`) and `crc/gcp/spot_vm/settings.py` stay: the cleaners and
   `crc/gcp/disk_*` import them. `git grep` finds no import or call of `crc.aws.disk.Disk`,
   `crc.aws.spot_instance_requests.SpotInstanceRequests`, `crc.azu|gcp|oci.spot_vm.SpotVM` or
   `SpotVmRuntime` in `crc/`, `crc.py` or `tests/`; the remaining hits are the frozen oracles'
   own classes (`_load_oracle().SpotVM`, `.Disk`, `.SpotInstanceRequests`) and the
   `tests/helpers_cl2_crc.py` fake names. The `from crc.aws.spot_instance_requests import
   SpotInstanceRequests` line in `tests/legacy/crc_main.txt` is rebound to the module oracle by
   `tests/helpers_oracle.load_legacy_crc`, so it does not need the deleted class.
2. Retargeted tests: per-file collected test counts over the 13 touched test files are
   1057 at `7a8c936` and 1057 at HEAD; every adapter test was renamed one to one with the same
   parametrisation (for example `test_matches_main` 149 → `test_engine_matches_main` 149 in the
   AWS disk parity test). The engine tests reuse the existing cleaner-protocol comparison (same
   oracle cases; calls per region in order, results, stdout and errors exact, logs as a
   multiset and exact for single-region cases). That is the plan-before-apply difference the
   contract allows. The repeated-delete tests compare the list lines by count only, because the
   engine does not accumulate `get_deleted` across runs; everything else is still compared.
   Sabotage (local, reverted, not committed): skipping `terminate_instances` in
   `crc/aws/spot_instance_requests/cleanup.py` (38 of 112 engine parity cases fail), skipping
   the NIC delete in `crc/azu/spot_vm/removal.py` (55 of 83 fail) and renaming the deleted
   instance in `crc/gcp/spot_vm/client.py` (83 of 144 fail). Round 1 covered OCI and AWS disk.
3. Fixed in this round: `ENGINE_QUIRKS` in `tests/test_gcp_spot_instances_parity.py` asserts the
   engine-only outcome of `zone-without-name-after-first-zone` (KeyError, nothing deleted, where
   main deleted `a1`, `a2`) without naming its bug. Marked it `BUG-409` (the `docs/refactor/BUGS.md`
   row for GCP/OCI `spot_instances` listing before acting). The assertion is unchanged.
4. READMEs of the five packages and `crc/{aws,azu,oci}/README.md` describe only the cleaners;
   no rung, refactor, hash-lock or deleted-adapter wording, and every linked doc exists. Out of
   lane, not changed: `crc/aws/README.md:7` and the `crc/azu/README.md` engine-path line still say
   the `VM` adapter class "stays for its tests" (the VM lane owns those lines).
5. No file in `tests/legacy/**`, `tests/archtest/**`, `docs/refactor/BUGS.md`,
   `CLEANUP_SAFETY.md`, `AGENTS.md`, `ARCHITECTURE.md`, `pyproject.toml`, `Makefile` or
   `requirements*.txt` changed. Locked functions in touched files
   (`test_aws_disk_cleaner.py::test_connectivity_error_skips_the_region`,
   `test_aws_spot_instance_requests_cleaner.py::test_apply_runs_in_safety_order` and
   `::test_dry_run_plan_reports_and_apply_does_nothing`) are not in the diff; SAF-LOCK green.
6. Size: touched `crc/` files are at most 248 lines; ARCH-004 and ARCH-002 green.

### Owner action

None.

### New bugs

None found.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt`, then `make check`: ruff passed, mypy
`Success: no issues found in 367 source files`, lint-imports `5 kept, 0 broken`, pytest
`44417 passed in 52.29s`, `ARCH-008: 44417 tests, floor 44417`; wall time 1m11.7s. Re-run after
the fix: all gates green, `44417 passed in 52.48s`, wall time 59.4s.

ROUND 2 VERDICT: APPROVE
