## Coding summary

Partial state, green (`make check`): step (1a) and the wording fix of step (3) are done; the adapter
deletion (1b) and the selection and policy cleanup (2) are left.

- Routed: `--scan_tag` no longer builds the four VM adapters. `crc.cli.scan.scan_instances_with_tag`
  calls `crc.aws.vm.scan.scan_tag` (new, default boto3 session, scan client and region lister) for
  AWS. Azure, GCP and OCI raise the same `NotImplementedError` text as before, naming the former
  adapter class (`crc.azu.vm.adapter.VM`, `crc.gcp.vm_adapter.VM`, `crc.oci.vm.adapter.VM`), after
  `_prepare_scan` repeated the observable set-up of the adapter constructors (`Service()` logging
  init, Azure `Base(resource_group)` env and credential reads, OCI `Base()` key check, GCP
  `ComputeVmClient()`), so a missing credential is still reported as `error scanning: ...`.
  An unknown cloud still raises the same ValueError before any set-up.
- Adapters deleted or shimmed: none yet. `VM`, `VmRuntime` and the other adapter classes are
  unused by production now (READMEs say so) and only tests import them.
- CLI parity tests: `tests/helpers_wa2_scan.py` now patches `crc.cli.scan._prepare_scan` and
  `crc.aws.vm.scan.scan_tag` instead of the VM classes. `test_cli_scan_parity.py` compares the
  oracle (`crc_main.txt` on fakes) with the routed scan with the same grid and fields; changes:
  non-AWS clouds are compared against the oracle with no finder (production reality; the old
  cases gave Azure/GCP/OCI fake finders no production class has), fake class texts are mapped to
  the production class texts, and the `new` (adapter constructor) entries are replaced by an
  assertion that the set-up ran exactly once per cloud. New tests: exact NotImplementedError text
  per cloud, direct AWS call, set-up per cloud, missing-credential errors.
- Wording (BUG-004): "fail open" became "fail closed" in `crc/aws/vm/README.md`,
  `crc/azu/vm/README.md` and `crc/gcp/README.md` (`get_retention_age` fails closed).
- Test count: floor 44415 before, 44422 after (7 new tests; floor raised).
- Owner actions: none.

## Left (not done, out of time)

1. Delete the four VM adapter classes and exports, and retarget `test_{aws,azure,gcp,oci}_vm_parity.py`
   (about 3900 lines), `test_*_vm.py`, `test_*_vm_fix.py`, the logger tests, `test_cli_bridge_aws.py`
   and `test_oci_spot_instances*.py` (they import the adapters) to the registered cleaners via
   `crc.core.engine.run`.
2. `build_selection` / `SelectionOptions` (BUG-371): production never calls them, but
   `tests/test_core_selection.py` compares them with `crc_main.txt` over an exhaustive grid; I did not
   verify that the CLI tests cover every case, so nothing was deleted. The dead policy helpers (BUG-370)
   were not examined.

## New bugs

- `crc/cli/scan.py` `_prepare_scan`: the scan still runs the Azure/OCI/GCP set-up (credential reads,
  logging init that prints and truncates `logs/crc.log`) only to print "scan not implemented"; it is
  kept for exact output and can go once the owner accepts a changed message for those clouds.

## Round 1 (Argon)

Scope reviewed: `git diff a0b3d09..HEAD` on branch `ag/adapters` (lane `AG-ADAPT`).

### Findings & fixes made in review

1. **Completed item (2) — Deleted `build_selection`, `SelectionOptions` (`BUG-371`), and the dead policy helpers `gcp_ip_exception_excludes` and `nic_name_included` (`BUG-370`)** (`crc/core/selection.py`, `crc/core/policy_text.py`, `crc/core/__init__.py`, `crc/core/README.md`, `tests/test_core_selection.py`, `tests/test_core_policy.py`):
   - `crc/core/selection.py` (`BUG-371`): deleted `build_selection`, `SelectionOptions`, `_validate`, `_skip_warning`, and the duplicate CLI message constants (shrinking `crc/core/selection.py` from 119 lines to 21 lines), keeping `Selection`, `CLOUDS`, `RESOURCES`, and `EXTRA_RESOURCES`. Removed `build_selection` and `SelectionOptions` from `crc/core/__init__.py` and `crc/core/README.md`.
   - `tests/test_core_selection.py`: kept `test_all_expansion_order_follows_crc_py` untouched (owned by lane `AG-CLI`) and retargeted all other 183 parametrized cases (`OK_CASES`, `ERROR_CASES`, `test_ebs_breaker_selects_nothing_before_raising`, `test_single_pair`, `test_unknown_values_raise`, `test_warning_texts_match_crc_py`, `test_exhaustive_grid_matches_crc_main`) to assert `crc.py main()` (`run_crc_main` / `run_main_after_fix`), `bridge.route_for`, `get_argparser`, and `Selection` directly, plus asserting `not hasattr(selection, "build_selection")` and `not hasattr(selection, "SelectionOptions")`.
   - Covering CLI tests for every case of `tests/test_core_selection.py`:
     - `OK_CASES`, `test_single_pair`, `test_warning_texts_match_crc_py`, `test_exhaustive_grid_matches_crc_main`: covered by `tests/test_core_selection.py` (driving `crc.py main()` directly) and `tests/test_cli_main_parity.py` (`test_main_matches_oracle`, `test_unsupported_cloud_resource_pairs`, `test_aws_only_resources_under_non_aws_and_all`).
     - `ERROR_CASES`, `test_ebs_breaker_selects_nothing_before_raising`: covered by `tests/test_core_selection.py` (driving `crc.py main()` directly), `tests/test_cli_main_parity.py` (`test_main_errors_match_oracle`, `test_aws_ebs_breaker_selects_nothing`), and `tests/test_cli_inputs.py` (`test_check_flag_combinations`, `test_resolve_targets`).
     - `test_unknown_values_raise`: covered by `tests/test_core_selection.py::test_unknown_values_raise` (`bridge.route_for` and `crc_cli.get_argparser`) and `tests/test_cli_args_parity.py::test_invalid_choices_exit_with_2`.
   - `crc/core/policy_text.py` (`BUG-370`): verified that nothing in `crc/` calls `gcp_ip_exception_excludes` or `nic_name_included`, and deleted both functions from `crc/core/policy_text.py`, `crc/core/__init__.py`, `crc/core/README.md`, and `tests/test_core_policy.py` (replacing `test_gcp_ip_exception_is_inverted` with `test_dead_policy_helpers_removed`).
2. **Completed item (3) — Fixed remaining `BUG-004` "fail open" and stale `scan.py` wording in owned READMEs** (`crc/gcp/README.md:5,25,55`, `crc/oci/vm/README.md:39,53`):
   - `crc/gcp/README.md:55` (GCP VM bullet) and `crc/oci/vm/README.md:39` still stated that custom age labels/tags "fail open (`get_retention_age`, BUG-004)"; updated both to state that a present but bad custom age label/tag keeps the instance (`get_retention_age` returns `KEEP_AGE`, counted as skip `bad_age_tag`; `BUG-004` fixed).
   - `crc/gcp/README.md:5` still stated "`crc/cli/scan.py` also uses the `VM` class"; updated to reflect that `VM` is no longer used by production. Updated `crc/oci/vm/README.md:53` bug-location rule text.
3. **Strengthened item (1a) `--scan_tag` tests** (`tests/test_cli_scan_parity.py:86-90,185-206`):
   - Added `test_aws_scan_tag_delegates_to_find_instances_with_tag` to directly test `crc.aws.vm.scan.scan_tag` (previously always monkeypatched out).
   - Tightened `compare_scan` to assert `[call[:2] for call in builds] == [call[:2] for call in old["adapters"] if call[:1] == ("new",)]` (verifying the cloud adapter identifier as well as call count).
4. **Blocking — Incomplete task scope (item 1b: deleting the four VM adapter classes and retargeting VM tests)**:
   - Item (1a) (`--scan_tag` calling `crc.aws.vm.scan.scan_tag` directly), item (2) (`BUG-370` and `BUG-371` dead core helpers removed), and item (3) (`BUG-004` README fixes) are complete and green.
   - Item (1b) remains open (listed under `## Left` above with reason: coder ran out of time): `crc/aws/vm/adapter.py` (`VM`, `VmRuntime`), `crc/azu/vm/adapter.py` (`VM`), `crc/gcp/vm_adapter.py` (`VM`), and `crc/oci/vm/adapter.py` (`VM`, `VmRuntime`) and their package exports have not yet been deleted, and `tests/test_{aws,azure,gcp,oci}_vm_parity.py` (~3,900 lines), `tests/test_*_vm.py`, `tests/test_*_vm_fix.py`, and the VM logger tests have not yet been retargeted to `crc.core.engine.run`.

### Checklist verification

- **Check (1) Task items verified against code**: Items (1a), (2), and (3) are verified in code and green; item (1b) is listed under `## Left` with reason. The 5 hash-locked adapters (`crc.aws.disk.Disk`, `crc.aws.spot_instance_requests.SpotInstanceRequests`, `crc.azu.spot_vm.SpotVM`, `crc.gcp.spot_vm.SpotVM`, `crc.oci.spot_vm.SpotVM`) are untouched.
- **Check (2) CLI contract intact**: Ran `python3 crc.py --help` (exit 0, output unchanged). No CLI flag, Jenkins command behaviour, selection, delete, Slack/Influx output, or exit code changed (`test_cli_scan_parity.py` 968 passed, `test_cli_main_parity.py` 18,083 passed).
- **Check (3) No check or test weakened; sabotage check; locked files untouched**:
  - `tests/archtest/safety_lock.json`, `update_safety_lock.py`, the 5 hash-locked safety tests, `tests/legacy/*.txt`, `requirements.txt`, and `tests/archtest/frozen_inputs.json` are untouched.
  - Local sabotage check (not committed): swapped `tag_key` and `tag_value` in `crc/cli/scan.py:48` (`aws_scan.scan_tag(tag_value, tag_key)`); `tests/test_cli_scan_parity.py` failed immediately (3 failures under `--maxfail=3`). Reverted.
- **Check (4) Only owned files changed**: `git diff --name-only a0b3d09..HEAD` touches only lane-owned files (`crc/aws/README.md`, `crc/aws/vm/**`, `crc/azu/vm/README.md`, `crc/cli/README.md`, `crc/cli/scan.py`, `crc/core/__init__.py`, `crc/core/README.md`, `crc/core/policy_text.py`, `crc/core/selection.py`, `crc/gcp/README.md`, `crc/oci/vm/README.md`, `docs/archive/reviews/AG-ADAPT.md`, `tests/archtest/test_count_floor.txt`, `tests/helpers_wa2_scan.py`, `tests/test_cli_scan_parity.py`, `tests/test_core_policy.py`, `tests/test_core_selection.py`).
- **Check (5) Size limits & Public API sync**: Every touched file in `crc/` is well under 250 lines (`crc/cli/scan.py` 191 lines, `crc/aws/vm/scan.py` 114 lines, `crc/core/policy_text.py` 76 lines, `crc/core/selection.py` 21 lines) and every function is at most 60 lines (`ARCH-004`). `Public API` sections in `crc/aws/vm/README.md`, `crc/cli/README.md`, `crc/core/README.md`, `crc/gcp/README.md`, and `crc/oci/vm/README.md` are synced (`ARCH-002`).
- **Check (6) Gates & test count**: After `pip install -r requirements.txt -r requirements-dev.txt`, `make check` is green:
  - `python3 -m ruff check .`: All checks passed
  - `python3 -m mypy`: Success: no issues found in 367 source files
  - `lint-imports`: 5 contracts kept, 0 broken
  - `python3 -m pytest -q -n auto`: **44423 passed in 54.34s** (total `make check` runtime ~67s)
  - `python3 -m tests.archtest.test_count check`: `ARCH-008: 44423 tests, floor 44423` (`ARCH-001..011` and `SAF-LOCK` green)

### New bugs (additional)

- `crc/core/policy_text.py:72` (`status_contains_any_allowed`) and `crc/core/policy_managed.py:72` (`aws_disk_has_sensitive_yb_task`): dead policy helpers no longer called by any cleaner in `crc/` after `BUG-010`/`BUG-198` (Azure VM exact power-state check) and `BUG-282` (AWS disk checks all folded `yb_task` tags) were fixed; only `tests/test_core_policy.py` calls them.

VERDICT: CHANGES_REQUESTED

## Round 2 (Opus)

Scope: `git diff a0b3d09..HEAD` on `ag/adapters`, round 1 fixes re-checked by opening the code.

### Findings

1. **Blocking, item (1b) still not done.** `crc/aws/vm/adapter.py` (`VM`, `VmRuntime`), `crc/azu/vm/adapter.py`,
   `crc/gcp/vm_adapter.py` and `crc/oci/vm/adapter.py` and their exports still exist, and
   `tests/test_{aws,azure,gcp,oci}_vm_parity.py` still drive the adapters instead of the registered
   cleaners through `crc.core.engine.run`. Too large for the review time limit; listed under `## Left`.
2. Item (1a) verified: `crc/cli/scan.py` `scan_instances_with_tag` calls `crc.aws.vm.scan.scan_tag`, which
   passes the same default boto3 session, scan client and `all_regions` as `VM.runtime` did
   (`crc/aws/vm/adapter.py:48-70,130-139`). `_prepare_scan` repeats the constructor side effects of each
   adapter (`Service()`; Azure `Base(resource_group)`; OCI `Base()`; GCP `ComputeVmClient()`), and the
   NotImplementedError texts name the real former classes (`<class 'crc.azu.vm.adapter.VM'>` etc.).
3. Item (2) verified: no `build_selection`, `SelectionOptions`, `gcp_ip_exception_excludes` or
   `nic_name_included` left in `crc/`, `crc.py` or the tests except the `not hasattr` checks. The `crc_cli`
   fixture of `tests/test_core_selection.py` loads the production `crc.py`, so the retargeted cases
   test the real CLI. Round 1's covering-test list is accurate.
4. **Fixed: weakened test.** `test_warning_texts_match_crc_py` had gone from exact warning-text equality (against
   the `build_selection` constants) to a count check. It now asserts the exact texts per case
   (`_expected_warnings`, same strings as the deleted constants at `a0b3d09:crc/core/selection.py:23-35`).
5. **Fixed: BUG-004 wording.** `crc/gcp/README.md:54` (GCP disk bullet) still said "the custom age label is fail
   open"; it now says that a present but bad label keeps the disk (`KEEP_AGE`, skip `bad_age_tag`). No other
   "fail open" age wording is left in the owned READMEs (`fail_open` in `crc/core/README.md` is the notifier flag).

### Gates

- `python3 crc.py --help`: exit 0, byte-identical to `a0b3d09`.
- Locked files (`safety_lock.json`, `update_safety_lock.py`, the five safety tests, `tests/legacy/*.txt`,
  `requirements*.txt`, `frozen_inputs.json`) and the five hash-locked adapters are untouched; only lane files changed.
- Break checks in a scratch worktree (not committed): removing the Azure `Base(...)` call in `_prepare_scan`
  fails `test_prepare_scan_keeps_the_setup_of_the_adapters`; renaming the GCP class text fails
  `test_scan_matches_main[scan-gcp-...]`; changing the `spot_instance_requests` warning text fails 177 tests in
  `test_cli_main_parity.py` / `test_cli_route_skip.py` and 4 cases of the tightened `test_warning_texts_match_crc_py`
  (round 1's version passed with the broken text).
- Size: `crc/cli/scan.py` 191, `crc/aws/vm/scan.py` 114, `crc/core/selection.py` 21, `crc/core/policy_text.py` 70 lines;
  functions are at most 60 lines; README Public API in sync (ARCH-002 green).
- `make check` after `pip install -r requirements.txt -r requirements-dev.txt`: ruff, mypy (367 files), import-linter
  (5/5) green; **44423 passed in 49.50s** (`make check` 67 s wall); ARCH-008 44423, floor 44423 (unchanged).

### New bugs

- Same as round 1: `crc/core/policy_text.py:68` `status_contains_any_allowed` and `crc/core/policy_managed.py:72`
  `aws_disk_has_sensitive_yb_task` are only called from tests (not part of BUG-370, left in place).

ROUND 2 VERDICT: CHANGES_REQUESTED
