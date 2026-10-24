## Coding summary

Lane CL2-GCP (test-only): `tests/test_cli_bridge_gcp.py` and `tests/helpers_gcp_bridge.py` no longer
run the current legacy CLI code. No production code changed.

**What is routed.** Unchanged: every GCP pair (`disk`, `ip`, `vm`, `spot_instances`) already runs
through `crc/cli/bridge.py`. The routed side of the tests still builds the current `CRC` through
`make_crc` as the context object and calls `dispatch._run_pair`.

**Adapters deleted or shimmed.** None (test-only lane).

**How the CLI parity tests now reach the cleaners.**
- Legacy side: `tests/helpers_cl2_gcp_oracle.py` (new) loads `tests.helpers_oracle.load_legacy_crc()`
  (`main`'s `CRC` bound to `tests/legacy/gcp_*.txt`) and calls `delete_disks`, `delete_ip`,
  `delete_vm`, `stop_vm`, `delete_spot_instances` as `main` dispatched them. The oracle adapters
  call the SDK directly, so `patch_sdk` replaces `googleapiclient.discovery.build` and the
  `compute_v1` Disks/Instances/Addresses/Regions clients with fakes that delegate to the same
  `FakeGcp` the routed path uses (same recorded calls, same fault injection, `close()` on exit).
- Run-log lines: the oracles log none. `tests/helpers_cl2_gcp_runlog.py` (new, data only) holds the
  RUN START / REGION / ACTION / SKIPPED / ERRORS / RESULT lines the pre-CL2 legacy handlers logged for
  all 123 case/switch/fault combinations (captured while that code still existed). The routed run
  is asserted against them, with the same per-kind comparison as before.
- Fixed-bug divergences: `tests/helpers_cl2_gcp_diverge.py` (new, data only) lists, per combination,
  the BUG ids, the exact outcome fields (Slack, Influx, stdout, error, GCP calls) that differ from
  `main`, and the values the routed run must produce (frozen from the pre-CL2 legacy run, not from
  the routed run). `assert_equal` requires that exactly those fields differ from the oracle and that
  the routed run equals the frozen values, so a divergence can neither grow nor disappear silently.
  Bugs: BUG-001 (`ip` exception regex inverted in `main`), BUG-005 (`disk` notags ignored), BUG-006
  (`disk` name_regex ignored), BUG-127 (`disk` listing error aborts the run in `main`), BUG-186
  (`vm` zone listing error aborts the run in `main`). `spot_instances` and the dry-run Spot cases
  show no divergence.
- Test fake fix: `FakeGcp.delete_disk` took `(project, zone, disk)` but the Spot client calls
  `(project, scope, location, disk)`, so every routed and pre-CL2 leftover-disk delete of a Spot VM
  failed with a `TypeError` that the Spot cleaner logs and swallows. The leftover disk deletes were
  therefore never compared. The fake now accepts both shapes; the frozen run-log data was captured
  with the fixed fake, and the Spot cases now compare leftover disk deletes, Slack and Influx
  against the oracle.
- Assertions kept: the owner-ping known difference test, the BUG-400 "deletes fewer, never more"
  test, and the close-error test (which now states both the oracle's names and the routed names).

**Test count.** Before 44400 (`tests/test_cli_bridge_gcp.py`: 126), after 44400 (126). `make check` is
green; the floor is unchanged, so no `make raise-test-floor`.

**Grep.** `tests/test_cli_bridge_gcp.py`, `tests/helpers_gcp_bridge.py` and `tests/helpers_cl2_gcp_*`
import none of `crc.cli.resource_ops`, `vm_ops`, `notify`, `adapter_args`, `report_calls`,
`tests.helpers_legacy_handlers` and call no `delete_*`/`stop_*`/`notify_*` of `crc.cli.runner.CRC`
(the only such calls are on `main`'s CRC oracle and on the fake SDK client).

**Owner actions.** None. A later lane can delete `tests/helpers_legacy_handlers.py` once the other
bridge tests stop using it; this lane no longer does.

## New bugs

- `tests/helpers_gcp_bridge.py` (before this lane): `FakeGcp.delete_disk` had the wrong signature for the
  Spot client; fixed here (test-only), no production bug.
- None in production code found.

## Round 1 (Argon)

Scope reviewed: `git diff 2bd8e67..HEAD` on branch `cl2/gcp` (lane `CL2-GCP`).

### Findings & fixes made in review

1. **Removed remaining `*_adapter` monkeypatches from `tests/helpers_gcp_bridge.py::install`**:
   - `install()` still patched `ComputeIpClient`, `ComputeDiskClient`, `ComputeVmClient`, and `ComputeSpotClient` on `crc.gcp.ip_adapter`, `crc.gcp.disk_adapter`, `crc.gcp.vm_adapter`, and `crc.gcp.spot_vm.adapter`. Those patches are unused now that the legacy side runs `load_legacy_crc()` against `tests/legacy/gcp_*.txt` via `patch_sdk`, and keeping them would cause `ModuleNotFoundError` once a later lane deletes the current adapter modules. Narrowed `install()` to patch only `ip_cleaner`, `disk_cleaner`, `vm_cleaner`, and `spot_vm.cleaner`.
2. **Exercised all frozen `RUN_LOG` and `DIVERGENCES` entries in `tests/test_cli_bridge_gcp.py` and `tests/helpers_cl2_gcp_diverge.py`**:
   - `test_owner_pings_with_a_project_listing_error_differ_known` and `test_later_listing_error_deletes_fewer_never_more` populated `old.run_log` from `RUN_LOG` without asserting it, and `DIVERGENCES` had three `fail_list_last` entries (`ip`, `disk`, `vm-delete`) that were never read because `test_later_listing_error_deletes_fewer_never_more` did not call `assert_equal`. Moreover, `DIVERGENCES[("ip", False, False, False, "fail_list_last")]` listed `delete_address` calls from the pre-CL2 legacy loop rather than the routed `BUG-400` behaviour (where `plan()` aborts on `r2` before any delete in `r1`).
   - Removed the unreferenced `("ip", False, False, False, "fail_list_last")` entry from `DIVERGENCES`, added `assert new.run_log == old.run_log` to `test_owner_pings_with_a_project_listing_error_differ_known`, and added `assert_equal(old, new)` for `disk`, `vm-delete`, and `spot` (plus the explicit `BUG-400` `run_log` and error assertions for `ip`) in `test_later_listing_error_deletes_fewer_never_more`. Every entry in `RUN_LOG` (123) and `DIVERGENCES` (33) is now asserted.

### Checklist verification

1. **No imports or calls to current legacy CLI code**: Grepped `tests/test_cli_bridge_gcp.py`, `tests/helpers_gcp_bridge.py`, and `tests/helpers_cl2_gcp_*.py` — zero imports of `crc.cli.resource_ops`, `vm_ops`, `notify`, `adapter_args`, `report_calls`, `tests.helpers_legacy_handlers`, or `crc.gcp.*_adapter`, and zero calls to `CRC.delete_*`/`stop_*`/`notify_*` on `crc.cli.runner.CRC`.
2. **Parity comparisons, case count, and BUG-NNN divergences**: All 126 test items in `tests/test_cli_bridge_gcp.py` are preserved. Verified all 123 `RUN_LOG` entries and 33 `DIVERGENCES` entries (`BUG-001`, `BUG-005`, `BUG-006`, `BUG-127`, `BUG-186`) against the pre-CL2 legacy handler (`run_legacy_handler`) and `load_legacy_crc()`.
3. **Mutation check (non-vacuous)**: Tested in-memory mutations of `IpCleaner._delete` (dropping `delete_address`) and `VmCleaner`; `pytest tests/test_cli_bridge_gcp.py` failed immediately (9 failures on the `ip` cases).
4. **File scope**: Only `tests/test_cli_bridge_gcp.py`, `tests/helpers_gcp_bridge.py`, `tests/helpers_cl2_gcp_*.py`, and `docs/archive/reviews/CL2-GCP.md` changed (`git diff 2bd8e67..HEAD --stat`); zero production files, archtest files, or locked files touched.

### Gates & test count

- `python3 -m ruff check .`: passed
- `python3 -m mypy`: passed (367 source files)
- `lint-imports`: passed (5 contracts kept, 0 broken)
- `python3 -m pytest -q -n auto`: **44400 passed in 56.38s** (`make check` total ~76s)
- `python3 -m tests.archtest.test_count check`: `ARCH-008: 44400 tests, floor 44400`

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff 2bd8e67..HEAD` on `cl2/gcp` (coder commit `0d2e3b8` and round 1 commit `2263c1d`).

### Round 1 fixes checked

- `install()` in `tests/helpers_gcp_bridge.py` now patches only the four cleaner modules
  (`ip_cleaner`, `disk_cleaner`, `vm_cleaner`, `spot_vm.cleaner`). Correct: the legacy side runs on
  `patch_sdk` alone, so nothing reaches `crc.gcp.*_adapter` any more.
- The `RUN_LOG` entries that were never checked are now asserted (owner-ping test, later-listing-error test), and so are the
  `fail_list_last` divergences. The removed `("ip", ..., "fail_list_last")` divergence entry is
  covered instead by explicit BUG-400 assertions: the frozen lines have ACTION lines, the routed run has none, and both raise the same error. Agreed.

### Independent checks

1. **No legacy CLI code (grep).** `tests/test_cli_bridge_gcp.py`, `tests/helpers_gcp_bridge.py` and
   `tests/helpers_cl2_gcp_*.py` import none of `crc.cli.resource_ops`, `vm_ops`, `notify`,
   `adapter_args`, `report_calls`, `tests.helpers_legacy_handlers` or `crc.gcp.*_adapter`. The only
   `delete_*`/`stop_*` calls are on `main`'s CRC from `load_legacy_crc()` (`run_oracle`) and on the
   fake SDK client. There are no `notify_*` calls.
2. **Comparisons kept and frozen data checked against the source.** Same 9 test functions and 126 items as at `2bd8e67`.
   The assertions that were moved (Slack, Influx, stdout, error, GCP calls: ordered for a dry run, as a sorted list for a real run)
   all live in `assert_equal`, which now applies them to every scenario. Before, only the plain cases
   compared GCP calls, so the check is stricter. The later-listing-error cases for disk, vm-delete and spot are now
   exact equality instead of only "deletes fewer, never more". To check where the frozen data came from, I wrote a
   throwaway test (not committed) that runs the pre-CL2 legacy handler (`run_legacy_handler`, still
   present) for all 123 `RUN_LOG` keys with the same `CASES`, `BASE` and `apply_scenario`. All 123 run logs
   equal `RUN_LOG` exactly, and every frozen field of all 33 `DIVERGENCES` entries equals that legacy
   run's value, so none of the expected data comes from the routed side. The BUG ids used (BUG-001, -005,
   -006, -127, -186, -400) are all "fixed in" / "partly fixed in" rows of `docs/refactor/BUGS.md`, and
   every divergence names its BUG.
3. **Mutation (local, reverted, not committed).** (a) `disk_cleaner.apply_disk` skips deleting `d4`:
   11 failures. (b) `spot_vm/disks.py` skips the leftover disk delete: 7 failures. This was invisible
   before the `FakeGcp.delete_disk` signature fix, and confirms that fix makes the Spot disk deletes count.
   (c) Slack text in `crc/core/report.py` changed: 30 failures. The tests are not vacuous.
4. **File scope.** Changed files: only the two owned files, three new `tests/helpers_cl2_gcp_*` files and this
   review file. No production code, locked tests, `tests/legacy/*.txt` or `tests/archtest/**` touched.
6. **Gates.** `pip install -r requirements.txt -r requirements-dev.txt`, then `make check`: ruff
   passed, mypy passed (367 files), lint-imports passed, **44400 passed in 56.93s** (about 76s wall for
   all of `make check`). `ARCH-008: 44400 tests, floor 44400`. Count unchanged, so no `make raise-test-floor` was needed.

### Findings

- None blocking. Note: in `test_later_listing_error_deletes_fewer_never_more[ip]` the subset
  assertions are trivially true, because the routed run deletes nothing (BUG-400). The explicit BUG-400
  assertions round 1 added carry the check there. No change needed.

## New bugs

- None found in round 2.

ROUND 2 VERDICT: APPROVE
