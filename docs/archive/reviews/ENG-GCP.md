## Coding summary

Lane ENG-GCP (branch `eng/gcp`): green partial state. GCP `ip`, `disk` and `vm` (delete and stop) run through `crc.core.engine`; GCP `spot_instances` stays on the legacy handler (see Owner action).

**Routed**: `crc/cli/routes/gcp.py` has `ip` (options `project_id`, `name_regex`, `exception_regex`), `disk` (`NotifyMode.REPORT_THEN_RERAISE`, as `delete_disks` reported before it re-raised; all disk flags including `slack_notify_users` and `slack_user_label`) and `vm` (one route for delete and stop; `--resource_states` becomes `instance_state` for delete only, as the legacy handler passed no states for stop). The route file imports the three cleaner modules so they register. No bridge change was needed for these kinds; no cleaner code under `crc/gcp/**` changed (the GCP cleaners already logged their dry-run ACTION lines in `plan()` and carry the names as ids).

**Not routed: `spot_instances`.** The legacy handler `dispatch._spot_instances` warns and skips the pair under `--cloud all` (GCP and OCI spot VMs need an explicit cloud). A `Route` has no way to skip a pair (`precheck` can only raise, which changes the exception and the run), so routing it would delete spot VMs under `--cloud all`. The kind keeps `HANDLERS["spot_instances"]` and the `GCP_SpotVM` adapter. `tests/test_gcp_spot_vm_engine.py` (17 tests) is the Cleaner-based copy of the locked `tests/test_gcp_spot_vm.py` and covers the same SAF cases (spot detection, label and age filters, fail-closed custom age, only `autoDelete=false` leftover disks, instance delete waited before the disk, dry run calls nothing, GKE and MIG skip, `project_id` and positive age required) through `SpotVMCleaner`, the registered factory and `RunContext`.

**Adapters deleted or shimmed**: none deleted, none changed. The GCP `IP`, `Disk`, `VM` and `SpotVM` adapters were already thin shims over their Cleaners and stay (module parity tests, `scan_instances_with_tag` for GCP VM, oracle `CRC` methods). The locked `tests/test_gcp_spot_vm.py` and `tests/archtest/safety_lock.json` are untouched and pass.

**How the CLI parity tests reach the cleaners**: `tests/test_cli_main_parity.py` gets one per-cloud table, `BY_CLOUD`, built from the new `tests/helpers_cli_routes_gcp.py` (`GCP_ROUTED_CALLS`: the oracle `CRC` call of each GCP pair and the `RunContext.options` keys the GCP cleaner reads, a `None` key marks an argument it never reads: the two tag filters of `ip`). `routed_call(cloud, kind)` picks the per-cloud entry before the AWS-shaped `ROUTED_CALLS`, and `strip_unused_args` now looks events up by (cloud, method) for every routed pair instead of AWS only. The fake cleaner of `FakeCleaners` records `crc.delete_ip gcp [...]`, `crc.delete_disks gcp [...]`, `crc.delete_vm` / `crc.stop_vm gcp [...]` from `context.options`, so the oracle facts (which flag reaches which cleaner with which value, including `slack_notify_users` and `slack_user_label` for disk) are asserted for the `gcp-ip`, `gcp-disk`, `gcp-vm` and `all-*` cases and the failure cases. The `notags` assertion of the fake skips `gcp` `ip` (the GCP IP cleaner has no `notags`). `tests/test_cli_bridge_parity.py` `test_route_tables_route_the_aws_kinds` has one changed line (`"gcp": ["disk", "ip", "vm"]`). The other CLI parity tests are unchanged and green.

**New tests**
- `tests/test_cli_bridge_gcp.py` (99 tests) with `tests/helpers_gcp_bridge.py`: the same fake GCP client data is run twice through `dispatch._run_handler`, once with `route_for` patched to None (the legacy handler on the real adapters) and once through the route (the real Cleaners), with fake Slack and Influx clients. For `ip`, `ip` match all, `disk`, `disk` with owner pings, `disk` with `exception_regex`, `vm` delete, `vm` delete with states, `vm` stop and an empty `vm` selection, in a dry run and a real run, with and without Slack and Influx, it compares the run-log lines (RUN START, REGION, ACTION, SKIPPED, ERRORS, RESULT), the Slack calls (text, channel, `link_names`, owner pings), the Influx points, the console output, the error and (dry run) the GCP calls; a real run must make the same set of GCP calls. Further cases: one failed delete, a listing error in the first zone, a project-level listing error (`disk` reports an empty list before it re-raises, the others send nothing), and a listing error in a later zone (the routed run deletes a subset, never more).
- `tests/test_gcp_spot_vm_engine.py` (17 tests), see above.

**BUGS.md and CLEANUP_SAFETY.md**: BUG-400 (low group end, fixed in ENG-GCP): plan-all-regions-before-apply for the GCP kinds (same change as BUG-388 and BUG-391), plus the run-log line order below. Counts line updated. One clause added to the engine-path bullet of `CLEANUP_SAFETY.md`.

**Allowed difference and a log-order difference** (both in BUG-400): (1) a listing error in a later zone or region now stops the pair before any delete. (2) Run-log line order: the engine lists every zone first, so a real run logs all REGION lines before the ACTION lines (the adapter interleaved them), and the Cleaner `plan()` logs a dry run's ACTION lines after its REGION lines (the adapter logged them per zone; for disk it logged a zone's ACTION lines before its REGION line). The set of lines, their text, counts and order within each kind are equal (`tests/test_cli_bridge_gcp.py` compares each line kind in order). I did not change `plan()` to interleave, because `tests/test_gcp_logger.py` pins the current plan order and those tests are not mine to loosen.

**Test count**: floor at the start 43820 (43803 tests at the start of the lane), after 43936 (`make check`: ruff, mypy strict, lint-imports, ARCH-001..009, SAF-LOCK green); floor raised with `make raise-test-floor`.

**Owner actions**
- `spot_instances` (gcp, and the same shape for oci): needs a bridge feature to route. Smallest change: `Route.skip: Callable[[Any, Options, Targets], bool] | None`, checked in `run_routed` after `precheck` (or before `init_run_logging`), returning True to log the legacy warning and run nothing; GCP and OCI spot routes would then carry the `--cloud all` warning and Azure none. Alternatively `Route.precheck` may return a bool "skip". Then add `"spot_instances": Route(_spot, ...)` to `routes/gcp.py` (options `project_id`, `filter_tags`, `exception_tags`, `notags`, `age`, `custom_age_tag_key`; `build_spot_cleaner` already raises the legacy `project_id` and age errors) and extend `GCP_ROUTED_CALLS`.
- Swap in and relock `tests/test_gcp_spot_vm_engine.py` for `tests/test_gcp_spot_vm.py` when the owner relocks the safety hashes.
- The single shared-table edit `tests/test_cli_bridge_parity.py` line 189 and the `BY_CLOUD` and `routed_call` lines of `tests/test_cli_main_parity.py` will conflict trivially with the Azure and OCI lanes (adjacent lines); merge by keeping every cloud's entries.
- Cleanup lane: after the routes, `CRC.delete_ip`, `delete_vm`, `stop_vm`, `delete_disks` and the GCP branches of `_get_ip`, `_make_disk` and `_get_vm` are dead in production for GCP except `scan_instances_with_tag` (GCP `VM` adapter) and `spot_instances`; they stay while Azure and OCI use them.

## New bugs

- `crc/cli/bridge.py:100` (`_ReportThenRaise._partial`) and `crc/gcp/disk_cleaner.py` `DiskCleaner`: the GCP disk cleaner exposes no `partial_succeeded`, so if `apply()` ever raised mid-run (today `apply_disk` catches every per-disk error, so only a `close()` error can) the report would list nothing where `delete_disks` listed the disks deleted so far (Slack and Influx content only; no extra delete).
- `crc/cli/bridge.py:84-90` (`_ReportThenRaise.plan`) with `--slack_notify_users` on GCP disk: on a project-level listing error it reports `Plan()` without owner groups, so Slack gets one `Deleted the following 0 gcp Disk(s)` message and Influx a list point (`names='[]'`), where `delete_disks` only called `usergroups_list` (no message) and wrote `names='{}'`. No extra delete; pinned by `test_owner_pings_with_a_project_listing_error_differ_known`. Fix needs the bridge (a `partial_by_owner` hook), so it is left for the owner.
- `crc/gcp/vm_cleaner.py:218` and `crc/gcp/ip_cleaner.py:79`: `plan()` logs dry-run ACTION lines after all REGION lines (see BUG-400); a dry-run `RESULT` is equal.

## Round 1 (Argon)

Scope reviewed: `git diff 6a98441..HEAD` on branch `eng/gcp` (lane `ENG-GCP`).

### Findings & fixes made in review

1. **Fixed: GCP `DiskCleaner` now exposes `partial_succeeded` for `NotifyMode.REPORT_THEN_RERAISE` and logs dry-run `ACTION` / summary lines before `close()`** (`crc/gcp/disk_cleaner.py`, `crc/gcp/README.md`, `tests/helpers_gcp_bridge.py`, `tests/test_cli_bridge_gcp.py`):
   - Like AWS `DiskCleaner`, GCP `DiskCleaner` now maintains `self.partial_succeeded: list[str]` in `plan()` (dry-run candidates) and `apply()` (confirmed deletes) and emits dry-run `ACTION` and summary lines inside the `try:` block before `finally: self._client.close()`, matching `Disk.delete()`. When `close()` (or a mid-plan zone entry) raises, `bridge._ReportThenRaise` reports the disks planned or deleted so far to Slack and InfluxDB before re-raising, matching `CRC.delete_disks`.
   - Added `test_disk_close_error_reports_partial_succeeded_before_reraising` (real and dry run) and expanded `CASES` in `tests/test_cli_bridge_gcp.py` to also exercise `exception_tags`, `notags`, and `custom_age_tag_key` on `disk` and `vm`.
2. **`spot_instances` left on legacy handler as required**:
   - Verified that `dispatch._spot_instances` warns and skips `gcp` (and `oci`) under `--cloud all`, which `Route.precheck` cannot express without raising and aborting the run. Leaving `spot_instances` on `HANDLERS["spot_instances"]` and documenting the needed bridge hook under Owner actions follows the lane instructions.

### Checklist verification

1. **CLI result parity for all routed GCP pairs (`ip`, `disk`, `vm` delete and stop)**: selection, mutations and order within each region/zone, `RUN START` / `RESULT` / `ACTION` / `SKIPPED` / `ERRORS` / `FAILED` lines, `Service` / `init_logging` startup, Slack text and recipients (including `link_names=True` and `--slack_notify_users` / `--slack_user_label` owner pings on `disk`), Influx points, console and log lines, exceptions and exit codes, `validate_targets`, `NotifyMode.REPORT_THEN_RERAISE` on `disk`, and `--dry_run` non-mutation match the legacy path; the only behaviour change is plan-all-regions-before-apply, recorded in `BUG-400` (`docs/refactor/BUGS.md`) and one clause in `CLEANUP_SAFETY.md`.
2. **Parity tests non-vacuous**: `test_cli_main_parity.py` (`BY_CLOUD` via `tests/helpers_cli_routes_gcp.py`), `test_cli_bridge_gcp.py` (101 tests driving real `IpCleaner`, `DiskCleaner`, `VmCleaner` against legacy `CRC` handlers), `test_cli_bridge_parity.py`, `test_cli_crc_parity.py`, `test_cli_args_parity.py`, `test_cli_dispatch_fix.py`, `test_cli_logger.py`, `test_core_selection.py`, and all `test_gcp_*_parity.py` files reach real or recording fake cleaners and assert the oracle facts.
3. **Hash-locked safety tests**: the 5 locked test files (`tests/test_gcp_spot_vm.py` et al.) and `tests/archtest/safety_lock.json` are untouched and passing; `tests/test_gcp_spot_vm_engine.py` (17 tests) covers every SAF case locked to `tests/test_gcp_spot_vm.py` (`SAF-008`, `SAF-039`, `SAF-041`, `SAF-043`, `SAF-044`, `SAF-045`).
4. **Protected files**: no oracle (`tests/legacy/*.txt`), archtest (`tests/archtest/*.py`), guardrail (`AGENTS.md`, `ARCHITECTURE.md`, `.cursor/**`, `pyproject.toml`, `Makefile`), or `requirements*.txt` file was edited; `test_count_floor.txt` raised to `43938` via `make raise-test-floor`.
5. **Limits & README sync**: all touched non-legacy, non-test files are at most 250 lines and functions at most 60 lines (`ARCH-004`); all package READMEs (`crc/cli/README.md`, `crc/cli/routes/README.md`, `crc/gcp/README.md`) are in sync (`ARCH-002`).

### Gates & test count

- `python3 -m ruff check .`: passed
- `python3 -m mypy`: passed (343 source files)
- `lint-imports`: passed (5 contracts kept, 0 broken)
- `python3 -m pytest -q -n auto`: **43938 passed in 54.93s** (`make check` total 66.6s)
- `python3 -m tests.archtest.test_count check`: `ARCH-008: 43938 tests, floor 43938`

VERDICT: APPROVE


## Round 2 (Opus)

Scope: `git diff 6a98441..HEAD` on `eng/gcp`, round 1 fixes included.

### Round 1 fixes checked

- `DiskCleaner.partial_succeeded` (round 1 fix 1) is correct. A dry-run `close()` error reports every planned disk, as `Disk.delete()` did (it logged and recorded each disk before the final `close()`). In a real run `apply()` exposes the confirmed deletes. A project-level listing error reports an empty list in both paths. Moving the dry-run ACTION and summary lines inside `try` keeps their text and count.
- `spot_instances` staying on the legacy handler is correct. `_spot_instances` warns and returns under `--cloud all` for gcp and oci, and `Route.precheck` can only raise. The Owner action (a `Route.skip` hook) stands.

### Independent checks

1. **Parity of the routed pairs.** Every value in `routes/gcp.py` (`_ip`, `_disk`, `_vm`) matches the legacy arguments. `project_id` and `notags` come from `Options`, which are the same values `make_crc` stores on the CRC object (`crc/cli/main.py:42-45`). `instance_state` is only set for delete, and the VM factory forces the default state for `STOP` anyway (`crc/gcp/vm_cleaner.py:221`). Disk uses report-then-reraise as `delete_disks` did. In `tests/helpers_gcp_bridge.py` the legacy side patches `dispatch.route_for` to `None`, so it really runs `HANDLERS` on the adapters. Dry runs make no mutating GCP call. Slack text and recipients, Influx points, console output and exceptions are equal. GCP candidates carry the name as id, so the ENG-1 "ids instead of names" problem cannot occur. RUN START and RESULT lines are equal, `selected=` included.
2. **Mutation checks for vacuous tests (none found).** I broke `routes/gcp.py` four ways:
   - `exception_regex` of `ip` set to None: 14 failures, 4 of them in the oracle test `test_cli_main_parity.py` (`gcp-ip-*` and `all-ip-*`).
   - `slack_user_label` of `disk` set to None: 10 failures.
   - `detach_age` of `disk` set to None: 2 failures in `test_cli_main_parity.py` (`gcp-disk-*`).
   - `disk` route without `REPORT_THEN_RERAISE`: 5 failures.
   - Passing `--resource_states` to `vm` stop: no failure, but it changes nothing (the factory ignores the states for `STOP`).
3. **Locked safety tests.** `tests/test_gcp_spot_vm.py`, the other 4 locked files and `tests/archtest/safety_lock.json` are untouched and pass. All 8 locked `test_gcp_spot_vm.py` tests (SAF-008, 039 x2, 041, 043, 044, 045 x2) have a same-named copy in `tests/test_gcp_spot_vm_engine.py` that goes through `SpotVMCleaner`, `build_spot_cleaner`, the registry or the `spot_vm` modules, with the same assertions (the delete order `instance, operation, disk`, fail-closed zero custom age, MIG `UserList` metadata).
4. **Protected files.** No edits to `tests/legacy/*.txt`, `tests/archtest/**` (except the floor raised by `make raise-test-floor`), `AGENTS.md`, `ARCHITECTURE.md`, `.cursor/**`, `pyproject.toml`, `Makefile` or `requirements*.txt`. In `docs/archive/**` only this review file changed.
5. **Limits and docs.** The lane's source files are at most 240 lines (`disk_cleaner.py`), ARCH-004 passes, and the READMEs match the Public API (ARCH-002). The BUGS.md counts line checks out against the table: 392 rows, low 295, 60 fixed, 5 partly fixed, no duplicate IDs. BUG-400 is at the end of the low group.

### Fix made in round 2

- **GCP ip dry-run log order now matches the adapter** (`crc/gcp/ip_cleaner.py` `plan()`). `IP.delete()` logged each region's REGION line and then that region's `would_delete` ACTION lines. The routed `plan()` logged all REGION lines first, which BUG-400 had listed as a difference. `plan()` now logs each region's ACTION lines right after that region's REGION line. The pinned single-region test in `tests/test_gcp_logger.py` still holds. New test: `test_ip_dry_run_logs_each_region_then_its_actions_as_legacy` (2 cases) in `tests/test_cli_bridge_gcp.py` checks that the whole run log equals the legacy one; both cases fail without the fix. I updated the wording of BUG-400 to match.
- **Not fixed: disk and vm dry runs.** Their adapters logged each zone's ACTION lines before its REGION line, because `plan_zone` is a generator and the REGION line is written when it runs out. `test_disk_dry_run_logs_would_delete_and_never_done` and `test_vm_stop_dry_run_logs_would_stop_from_plan` pin the opposite order for `plan()`, so these two stay as recorded in BUG-400. Only the order changes; the set of lines, their text and the RESULT counts are equal. In a real run, logging every REGION line before the ACTION lines follows from the allowed plan-before-apply change.

## New bugs (round 2)

- `crc/gcp/disk_cleaner.py:76` and `:94`: through the engine a real disk run calls `close()` twice, once at the end of `plan()` and once at the end of `apply()`; `Disk.delete()` called it once. If `close()` raises in `plan()`, the pair now stops before any delete and reports an empty list, where the legacy path deleted every disk, reported them and then raised. This means fewer deletes, never more. The exception and exit code are the same.
- `crc/gcp/disk_client.py:106`: `close()` resets `_service`, so `apply()` builds the discovery service a second time (`discovery.build`) in every real run. The SDK call sequence differs from `main` (same shape as BUG-390). No mutation.

### Gates

- `make check` (after `pip install -r requirements.txt -r requirements-dev.txt`): ruff, mypy strict (348 files), lint-imports (5 kept, 0 broken), ARCH-001..009, SAF-LOCK green.
- Tests: 43938 at the start of round 2 (pytest 51.7s, `make check` 69s wall). After round 2: **43940 passed in 50.0s** (`make check` 56s wall), `ARCH-008: 43940 tests, floor 43940` (floor raised with `make raise-test-floor`).

ROUND 2 VERDICT: APPROVE
