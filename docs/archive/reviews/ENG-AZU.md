## Coding summary

Lane ENG-AZU (branch `eng/azu`): every Azure kind runs through `crc.core.engine`, using the bridge features ENG-1 added (`Route.precheck`, `NotifyMode`). No bridge change was needed, so nothing is listed under Owner action for the bridge.

**Routed (`crc/cli/routes/azu.py`, cloud name `azure`)**: `ip`, `nic`, `disk`, `vm` (delete and stop, one route, the operation comes from `--operation_type`) and `spot_instances`.
- `vm`: `resource_states` becomes `instance_state` for delete only and only when non-empty (the legacy handler passed no states for stop). The NIC Slack message after the VM message and the `NIC` Influx point come from the report (`Section.slack_ids` of an Azure VM report), so no `extra_notifiers` / `after` is used.
- `disk`: `NotifyMode.REPORT_THEN_RERAISE` (as `delete_disks` did for every cloud).
- `spot_instances`: `precheck` raises the `resource_group is required for Azure Spot VM cleanup` `ValueError` after RUN START and before the cleaner (the legacy method raised it before it built the adapter; an `AZURE_RESOURCE_GROUP` environment value does not satisfy it, as before). The GCP and OCI paths and the AWS skip warning stay in `dispatch._spot_instances`, which is no longer reached for Azure.
- `ip`, `nic`: plain `REPORT` routes.

**Adapters deleted or shimmed**: none deleted. The `IP`, `NIC`, `Disk`, `VM` and `SpotVM` adapters of `crc/azu/**` stay importable and unchanged (the frozen oracle `tests/legacy/crc_main.txt` imports them, the legacy `CRC` methods and `HANDLERS` entries still serve GCP and OCI and the parity oracle comparisons, and the cleanup lane deletes the dead ones at the end). `SpotVM` is the adapter of the hash-locked `tests/test_azu_spot_vm.py`; it was left as is (it already drives the same helpers as the Cleaner, and the locked test calls its private methods). The locked test file, `tests/archtest/safety_lock.json` and `tests/legacy/*.txt` are untouched. Not edited, as required: `bridge.py`, `routes/__init__.py`, `dispatch.py`, `resource_ops.py`, `vm_ops.py`, `notify.py`, `crc.py`.

**One cleaner change, to remove a run-log difference found by the new tests**: `crc/azu/ip/cleaner.py` logged the `REGION` line in `plan()`, so a real run whose `begin_delete` raised (the IP delete is not isolated per IP) showed `regions=1` in RESULT where the adapter showed `regions=0` (it logged the line after the loop). A real run now logs the line at the end of `apply()` (a dry run still in `plan()`). The other four cleaners swallow per-resource errors, so they always reach the line.

**How the CLI parity tests reach the cleaners**: `tests/helpers_cli_routes.FakeCleaners` already registers a recording fake for every route in `bridge.TABLES`, so the Azure routes are reached with no change to it. `tests/test_cli_main_parity.py` now looks the oracle call of a routed pair up with `routed_call(cloud, kind)`: the shared `ROUTED_CALLS` for AWS, and for any other cloud `tests/helpers_routed_<cloud>.ROUTED_CALLS` if that module exists (new `tests/helpers_routed_azure.py`, `RoutedCall` moved to new `tests/helpers_routed_calls.py`). A routed pair without an entry fails (`assert call is not None`), so a route can never be vacuously green; the fake cleaner records `crc.<method> azure [args]` from `context.options` and `strip_unused_args` removes only the oracle arguments the Azure adapters never read (IP regexes, disk detach age, regexes and Slack owner flags). The fake `CRC` of that test raises the real `ValueError` for `delete_spot_instances` on Azure without a resource group (before it records an adapter event), as the real method does. `tests/test_core_selection.py` now passes `resource_group="rg"` in its fake main arguments (the Azure spot route needs it) and `tests/test_cli_bridge_parity.py` lists the Azure routes. `test_cli_crc_parity.py` and `test_cli_args_parity.py` needed no change: they call the `CRC` methods and the parser directly, which still exist.

**Bridge parity tests (`tests/test_cli_bridge_azu.py`, data in `tests/helpers_cli_azu.py`)**: for each kind (vm delete, vm stop, vm with two states, ip, nic, disk, spot_instances), each data variant (plain mixed set, empty, listing error, one failing delete), dry run and real run, and four Slack and Influx modes, the real Azure code runs on the fake Azure SDK twice through `dispatch._run_handler`: once with the route lookup switched off (legacy `HANDLERS`, `CRC` method, adapter), once routed (`bridge.run_routed`, registered cleaner). Asserted equal: result or exception (type and message), Slack calls (text and recipient), Influx calls and points, console output, the ordered run-log lines RUN START, ACTION, SKIPPED, ERRORS, FAILED, RESULT, the mutating SDK calls in order, and the REGION lines as a set (see New bugs). The test counts the `run_routed` calls (0 legacy, 1 routed), so it cannot pass without reaching the cleaner. It also asserts the notify modes and that the spot precheck raises. The legacy instance-id/names difference ENG-1 round 1 found (dry-run VM names) and the missing dry-run `ACTION would_delete` lines / `selected=0` do not occur: they are in the compared lines and equal.

**Cleaner-based copy of the locked test**: `tests/test_azu_spot_vm_engine.py` (11 tests: SAF-038 detached disks, empty delete option, ephemeral disk, ARM id parsing, other resource group parsing, VM deleted before its leftover disks, dry run; SAF-041 positive age; SAF-042 untagged skipped; SAF-044 exception tag keeps the disk, other resource group not touched). For the owner to swap in and relock. The azure `SpotVM` locked tests map to SAF-038, 041, 042, 044 only.

**BUGS.md / CLEANUP_SAFETY.md**: new row BUG-392 (low group end, fixed in ENG-AZU, Exists on main yes, location `crc.py` adapter loop; the plan-before-apply change for the Azure kinds, plus the REGION line note), counts line updated, one sentence added to the engine path bullet of CLEANUP_SAFETY.md. Nothing else changed in those two files.

**Test count**: before 43820 (floor at the start of the lane), after 44057 (`make check`: ruff, mypy strict, lint-imports, ARCH-001..009 and SAF-LOCK green); floor raised to 44057 with `make raise-test-floor`.

**READMEs updated**: `crc/cli/README.md`, `crc/cli/routes/README.md`, `crc/azu/README.md`, `crc/azu/ip/README.md`.

**Owner actions**
- Swap in and relock `tests/test_azu_spot_vm_engine.py` for `tests/test_azu_spot_vm.py`.
- Other cloud lanes: give your cloud its own `tests/helpers_routed_<cloud>.py` (a `ROUTED_CALLS` table of `RoutedCall`, see `helpers_routed_azure.py`); `test_cli_main_parity.py` finds it by the cloud name used in `bridge.TABLES`. Add `resource_group`-style needs of your routes to `test_core_selection.run_crc_main` the same way I did for `resource_group` if a precheck raises.
- Cleanup lane: the Azure adapters (`IP`, `NIC`, `Disk`, `VM`) and the Azure branches of `CRC._get_ip`, `delete_nic`, `_make_disk`, `delete_disks`, `_get_vm`, `delete_vm`, `stop_vm`, `delete_spot_instances` and `adapter_args` are dead in production for Azure after this lane. `delete_nic` is dead for every cloud (NIC is Azure-only) as is `HANDLERS["nic"]`. Deleting them needs `provide_legacy_*` stand-ins for the oracle imports and retargeted module parity tests (`test_azure_*_parity.py`), as ENG-1 noted. BUG-392's mention of the adapter loop goes with them.
- The `ExtraNotifiers` / `AfterRun` hooks are still unused by every route.

## New bugs

- `crc/azu/disk/cleaner.py` (`plan()`) with `NotifyMode.REPORT_THEN_RERAISE`: the Azure disk cleaner exposes no `partial_succeeded`, so when `plan()` or `apply()` raises, the report sent before the error is raised is empty. `delete_disks` reported the disks deleted before a listing or selection error (paging error, naive `time_created`) because it deleted while listing. Now nothing is deleted before such an error (BUG-392, fewer deletes) and an empty report is sent; the Slack text and Influx point differ from the legacy one only in that error case. Not covered by the parity grid (its disk errors are per-disk delete errors, which are swallowed).
- `crc/azu/{vm,nic,disk}/cleaner.py` and `crc/azu/spot_vm/cleaner.py` `plan()`: the `REGION` line is logged at the end of `plan()`, before the `ACTION` lines of a real run; the adapters logged it after the actions (cosmetic, same fields; the same cross-order note as ENG-1 round 2 for the AWS cleaners). The parity test therefore compares REGION lines as a set.
- `crc/azu/vm/cleaner.py` `plan()`: the instance view (read) of every VM is now read before the first delete; the adapter interleaved status read and delete per VM. Read-only difference; a VM whose state changes during the run is judged earlier.
- `crc/cli/routes/azu.py` `_spot_group` and `crc/cli/vm_ops.py` `delete_spot_instances`: the Azure spot run requires `--resource_group` even when `AZURE_RESOURCE_GROUP` is set (every other Azure kind falls back to the variable via `Base`). Kept as is.

## Round 1 (Argon)

Scope reviewed: `git diff 6a98441..HEAD` on branch `eng/azu` (lane `ENG-AZU`).

### Findings & fixes made in review

1. **BLOCKING (fixed): `tests/test_cli_dispatch_fix.py::test_bug_017_cloud_all_nic_real_crc_deletes_nothing` had a vacuous patch on `resource_ops.NIC`** (`tests/test_cli_dispatch_fix.py`):
   - After routing `("azure", "nic")` through `crc.cli.bridge`, `dispatch.run_dispatch` invokes `bridge.run_routed` rather than `HANDLERS["nic"]` / `CRC.delete_nic`, so patching `resource_ops.NIC` alone could no longer detect if `("azure", "nic")` ran when `validate_targets` failed to raise.
   - Installed `FakeCleaners().install(monkeypatch).side_effect[("azure", "nic")] = lambda ctx: deleted.append("azure")` alongside `FakeNIC`, matching `test_bug_017_cloud_all_real_crc_does_not_build_skipped_clouds`.
2. **Test tightening & merge hygiene (fixed)** (`tests/test_cli_main_parity.py`, `tests/helpers_cli_azu.py`, `tests/test_cli_bridge_azu.py`, and 14 unrelated test files):
   - In `tests/test_cli_main_parity.py::install_routed_fakes`, added `"spot_instances"` to the `"notags" in context.options` assertion (and scoped the `"ip"` `notags` check to `{"aws", "azure"}` so the parallel GCP lane's `ip` route is not blocked), asserted `"resource_group" in context.options` and that its value equals `option_value(fakes.case, "--resource_group")` for every Azure routed pair, and asserted `"instance_state" not in context.options` on `Operation.STOP`.
   - In `tests/helpers_cli_azu.py` and `tests/test_cli_bridge_azu.py`, passed `resource_states: ["stopped", "running"]` on the `vm` `stop` case so the bridge parity test verifies that `--resource_states` is ignored on `stop` (skipping stopped VM `b`), added console `INFO`+ log comparison (`logs`) between legacy and routed runs, and added `("no_channel", "on_suffix")` to `MODES`.
   - Reverted 14 unrelated test files (`test_aws_*`, `test_gcp_*`, `test_oci_*`, `test_notify_*`, `test_core_*`) that `1e4e965` had reformatted with `ruff format`, keeping the branch strictly within `ENG-AZU` scope and avoiding merge conflicts with the parallel GCP and OCI lanes.

### Checklist verification

1. **CLI result parity for all routed Azure pairs (`ip`, `nic`, `disk`, `vm` delete/stop, `spot_instances`)**: selection, mutations and order within the resource group, `RUN START` / `RESULT` / `ACTION` / `SKIPPED` / `ERRORS` / `FAILED` lines (`REGION` lines equal as a set, and `AzureIpCleaner` logs `REGION` after `apply()` deletes so a failed delete leaves `regions=0`), `Service` / `init_logging` startup, Slack text and recipients (including the `NIC` Slack message after the `VM` message via `Section.slack_ids` and the conditional leftover `Disk` message for `spot_instances`), Influx points (`VM`, `NIC`, `Disk`), console and log lines, exceptions and exit codes, `validate_targets`, `_spot_group` `Route.precheck`, `NotifyMode.REPORT_THEN_RERAISE` for `disk`, and `--dry_run` non-mutation match the legacy path; the only behaviour change is plan-before-apply, recorded in `BUG-392` (`docs/refactor/BUGS.md`) and one sentence in `CLEANUP_SAFETY.md`.
2. **Parity tests non-vacuous**: `test_cli_main_parity.py` (with `tests/helpers_routed_azure.py`), `test_cli_bridge_azu.py` (280 scenarios + route/precheck tests), `test_cli_bridge_parity.py`, `test_cli_crc_parity.py`, `test_cli_args_parity.py`, `test_cli_dispatch_fix.py`, `test_cli_logger.py`, `test_core_selection.py`, and all `test_azure_*_parity.py` files reach real or recording fake cleaners and assert the oracle facts.
3. **Hash-locked safety tests**: all 5 locked test files and `tests/archtest/safety_lock.json` are untouched and passing; `tests/test_azu_spot_vm_engine.py` (11 tests) covers every SAF case (`SAF-038`, `SAF-041`, `SAF-042`, `SAF-044`) locked to `tests/test_azu_spot_vm.py`.
4. **Protected files**: no oracle (`tests/legacy/*.txt`), archtest (`tests/archtest/*.py`), guardrail (`AGENTS.md`, `ARCHITECTURE.md`, `.cursor/**`, `pyproject.toml`, `Makefile`), or `requirements*.txt` file was edited; `crc/cli/{bridge,dispatch,resource_ops,vm_ops,notify}.py`, `crc/cli/routes/__init__.py`, and `crc.py` are untouched; `test_count_floor.txt` raised to `44113` via `make raise-test-floor`.
5. **Limits & README sync**: all touched non-legacy, non-test files are at most 250 lines and functions at most 60 lines (`ARCH-004`); all touched package READMEs (`crc/azu/README.md`, `crc/azu/ip/README.md`, `crc/cli/README.md`, `crc/cli/routes/README.md`) are in sync (`ARCH-002`).

### Gates & test count

- `python3 -m ruff check .`: passed
- `python3 -m mypy`: passed (349 source files)
- `lint-imports`: passed (5 contracts kept, 0 broken)
- `python3 -m pytest -q -n auto`: **44113 passed in 53.47s** (`make check` wall time ~65s)
- `python3 -m tests.archtest.test_count check`: `ARCH-008: 44113 tests, floor 44113`

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff 6a98441..HEAD` on `eng/azu`, after the round 1 fixes (`c252ca3`).

### Round 1 fixes checked

- The `test_cli_dispatch_fix.py` NIC fix is correct: `FakeCleaners` records `("azure", "nic")`, so the test fails if the pair runs after `validate_targets` should have raised.
- The `test_cli_main_parity.py` tightening is non-vacuous: `install_routed_fakes` asserts a `routed_call` entry for every routed pair (`assert call is not None`), the Azure entries live in their own `tests/helpers_routed_azure.py`, and the fake cleaner checks `resource_group` against `--resource_group` and no `instance_state` on stop. The fake `CRC` raises the real spot `ValueError` for Azure without a group, as `vm_ops.delete_spot_instances` does.

### Independent checks

1. Route option mapping against the legacy handlers (`dispatch._ip/_nic/_disk/_vm/_spot_instances`, `adapter_args`, `CRC` constructor fed from the same `Options` in `cli/main.py`): `resource_group` and `notags` come from the same flags on both paths; `instance_state` is passed for delete only and only when non-empty, matching `_delete_vm` (`not instance_state` uses the adapter default) and `stop_vm` (default states); the IP regexes and disk detach/regex/Slack-owner flags were never read by the Azure adapters. `disk` uses report-then-reraise as `delete_disks` does; the spot precheck raises after RUN START and before `Service` and the cleaner, as the legacy method did. `operation_type` is limited to delete/stop by the parser and `stop` is rejected for non-vm resources by `validate_values`, so the routed `Operation(...)` never sees another value.
2. Parity tests reach a real or fake cleaner: `test_cli_bridge_azu.py` counts `run_routed` calls (0 legacy, 1 routed) and compares real code on both sides; `test_cli_main_parity.py`, `test_cli_dispatch_fix.py`, `test_cli_bridge_parity.py` and `test_core_selection.py` reach `FakeCleaners`. No vacuous fake or patch found.
3. The hash-locked files and `tests/archtest/safety_lock.json` are untouched (diff empty) and pass. The 7 locked Azure spot tests map to SAF-038, 041, 042 and 044; `tests/test_azu_spot_vm_engine.py` covers each.
4. Protected files: only `tests/archtest/test_count_floor.txt` changed (via `make raise-test-floor`, allowed); no oracle, archtest code, guardrail, requirements, bridge/dispatch/resource_ops/vm_ops/notify/`crc.py` edit. BUGS.md: one row (BUG-392, end of the low group, counts line updated); CLEANUP_SAFETY.md: one sentence in the engine path bullet.
5. File and function limits and README Public API sync: ARCH-002/004 green in `make check`.

### Findings and fixes made in review

1. **Fixed (test gap): the bridge parity test never compared the notags filter.** `tests/test_cli_crc_parity.build` builds the legacy `CRC` with `notags={"owner": ["x"]}` and `resource_group` from the scenario, while the routed side read `notags={}` and `resource_group="rg"` from the flags, and no fake resource carried an `owner` tag. So the two paths ran with different notags and the difference could not show. `tests/test_cli_bridge_azu.py` now sets `crc.notags` and `crc.resource_group` from the parsed options (as `cli/main.py` does), `COMMON` uses `notags={"owner": ["x"]}`, and `tests/helpers_cli_azu.py` adds an `owner`-tagged resource to the VM, IP, disk and Spot VM data (the run logs show `SKIPPED reason=exception_or_notags` on both paths).
2. **Fixed (test gap): spot without `--resource_group` was only tested on the precheck function.** A new `spot_instances` case with `resource_group=None` now runs both full paths through `dispatch._run_handler`: same `ValueError`, same RUN START/RESULT lines, no Slack, no Influx, no SDK call.
3. **Fixed (copy gap): the engine copy of the locked SAF-042 test used `tags={}` only;** the locked test uses `tags=None`. `test_saf_042_untagged_spot_vm_is_always_skipped` now covers both.

## New bugs (round 2)

- `crc/azu/ip/cleaner.py:71-77` (`plan()`) against the adapter loop: when an IP delete raises in a real run (the IP delete is not isolated per address), the adapter had stopped walking the listing, so the addresses listed after the failing one were never counted; the cleaner counts their skips in `plan()`, so the `SKIPPED` counts and `skipped=` in `RESULT` are higher on the routed path in that error case. Selection, deletes and their order are equal. A consequence of plan-before-apply (BUG-392); count-only. Found by placing a notags-tagged IP after the failing one; the test data keeps it before the failing IP so the grid stays green.

### Gates and test count

- `make check` (after `pip install -r requirements.txt -r requirements-dev.txt`): ruff, mypy strict, lint-imports, ARCH-001..009 and SAF-LOCK green.
- Before the round 2 changes: 44113 passed in 57.40s (`make check` wall time 1m17s).
- After: 44123 passed in 52.50s (`make check` wall time 59s); floor raised 44113 -> 44123 with `make raise-test-floor`.

ROUND 2 VERDICT: APPROVE
