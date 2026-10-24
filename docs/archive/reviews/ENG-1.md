## Coding summary

Lane ENG-1 (branch `eng/aws`): every remaining AWS kind runs through `crc.core.engine`. Green partial state: the AWS pairs are routed, the adapter classes and most `CRC` methods are not yet deleted (see "Left to do").

**Bridge features (generic, the Azure, GCP and OCI lanes only add route entries)** in `crc/cli/routes/__init__.py` and `crc/cli/bridge.py`:
- `Route.precheck`: the AWS disk opt-in gate now lives in the AWS disk route (`routes/aws.py`, `_disk_opt_in`), run between RUN START and the cleaner. `AWS_DISK_OPT_IN_MESSAGE` moved to `crc/cli/pairs.py` (still re-exported by `dispatch`).
- `Route.notify` is now a `NotifyMode`: `NONE` (vpc, snapshot, BUG-084 and BUG-009), `REPORT` (default), `REPORT_THEN_RERAISE` (disk). The last wraps the registered cleaner in `bridge._ReportThenRaise`: an error from `plan()` or `apply()` is kept, the notifiers run on what the cleaner exposes as `partial_succeeded` (an empty report when it exposes nothing), then `run_routed` raises the same error, as `delete_disks` did (Slack then Influx, then re-raise). `DiskCleaner` exposes `partial_succeeded` (the ids deleted so far in `apply()`, the ids listed so far in a dry-run `plan()`).
- `Route.extra_notifiers(crc)`: notifiers appended after Slack and Influx. For Azure VM: the report already carries the NIC section (`Section.slack_ids`), so Azure may need no extra notifier; the hook exists for anything the report does not carry. Not wired (Azure lane).
- `Route.after(crc, options, outcomes)`: runs after the notifiers. Used for the console line of `snapshot` (dry-run count line, or `crc.get_msg("Snapshot", "Deleted", ids)`).
- VM operations: delete and stop both go through the one `aws` / `vm` route (the operation comes from `--operation_type`); `--resource_states` becomes `instance_state` for delete only (the legacy handler passed no states for stop). `spot_instance_requests` is an ordinary route; `spot_instances` (azure, gcp, oci) is untouched.

**Routed AWS kinds**: `ip`, `kms`, `snapshot`, `vpc`, `vm` (delete and stop), `disk`, `spot_instance_requests` (plus `keypair` from ENG-0). Options builders are in `crc/cli/routes/aws.py`.

**Adapters deleted or shimmed**: none deleted. The kind adapters (`ElasticIPs`, `Kms`, `Snapshot`, `VPC`, `VM`) were already thin shims over their Cleaner and stay (the oracle `tests/legacy/crc_main.txt` imports them, `scan_instances_with_tag` still uses `crc.aws.vm.VM`, and their module parity tests are many thousands of cases that assert the oracle through the adapter). `Disk` and `SpotInstanceRequests` stay as they are (hash-locked tests); the 5 locked test files and `tests/archtest/safety_lock.json` are untouched and pass. Deleted in `crc/cli/dispatch.py`: the `_kms`, `_vpc` and `_snapshot` handlers and `HANDLERS` entries (no other cloud supports those kinds), the AWS disk gate in `_disk`, the AWS call in `_spot_instance_requests` (it now only warns for non-AWS clouds), and the `Snapshot` import.

**How the CLI parity tests reach the cleaners**: `tests/helpers_cli_routes.FakeCleaners` (patches `bridge.build_registry`, `bridge.init_run_logging` and `bridge.build_notifiers`) now also takes per pair `plans` and `results`. `tests/test_cli_main_parity.py` `ROUTED_CALLS` maps each routed kind to its oracle `CRC` method, the `RunContext.options` keys in oracle argument order (and `stop_vm` for a stop run); `strip_unused_args` removes from the oracle events only the arguments the legacy AWS adapters never read (ip regexes, disk Slack owner flags). The fake cleaner records `crc.<method> aws [args]` from `context.options`, and the fake snapshot cleaner records the `Snapshot` and `Snapshot.delete` events and plans 3 candidates and applies `snap-1`, so the oracle's `crc.get_msg` and dry-run count line are compared too. `test_cli_logger.py`, `test_cli_route_fix.py` (and so `test_cli_merge_fix.py`), `test_cli_dispatch_fix.py`, `test_core_selection.py` use the same seam. New `tests/test_cli_bridge_aws.py` runs the oracle `CRC` method (ip, kms, vpc, vm delete and stop, spot_instance_requests, disk) and `bridge.run_routed` on the same fake Slack and Influx clients across dry run x Slack mode x Influx mode x full/empty data and error cases; Slack calls, Influx calls, stdout and error are equal (disk reports before re-raising, the others send nothing on error), and the options and operation the cleaner receives are asserted. The CLI parity tests' CRC-method oracle cases (`test_cli_crc_parity.py`) are unchanged: the `CRC` methods are still there.

**Cleaner-based copies of locked tests**: `tests/test_aws_disk_engine.py` (18 tests: SAF-003, 007, 011, 012, 013, 015, 017, 018, 019, 020, 025, all the locked disk cases) and `tests/test_aws_spot_instance_requests_engine.py` (18 tests: SAF-007, 008, 011, 026, 027, 028, 029, 035). The azure, gcp and oci SpotVM copies are not written (those adapters are not touched in this lane).

**BUGS.md / CLEANUP_SAFETY.md**: new row BUG-391 (low group end, fixed in ENG-1, same plan-before-apply change for the other AWS kinds), counts line updated, and one clause appended to the ENG-0 bullet in CLEANUP_SAFETY.md.

**Test count**: before 43078 (floor at the start of the lane), after 43785 (`make check`: ruff, mypy strict, lint-imports, ARCH-001..009 and SAF-LOCK green); the floor is raised to 43785 with `make raise-test-floor`.

**Owner actions**
- Review, swap in and relock the two `*_engine.py` copies; write the azure, gcp and oci SpotVM copies in their lanes.
- Docs lane: `crc/core/README.md` and `docs/refactor/STATUS.md` still describe the CLI as running adapters.
- Decide whether `CRC.delete_kms`, `delete_vpc`, `delete_spot_instance_requests`, the AWS branches of `_get_ip`, `_make_disk` and the AWS adapters are removed in the cleanup lane at the end (see below).

## Left to do (not done in this lane, time)

- Delete the routed AWS adapters (`ElasticIPs`, `Kms`, `Snapshot`, `VPC`, `VM` delete path) and the dead `CRC` methods and imports (`delete_kms`, `delete_vpc`, `delete_spot_instance_requests`, the AWS branches of `_get_ip`, `_make_disk`; `delete_ip`, `delete_vm`, `stop_vm`, `delete_disks` stay for the other clouds). Each deletion needs: a `provide_legacy_*` stand-in for the name the frozen oracle imports (see `tests.helpers_cli_routes.provide_legacy_keypairs`), the module parity tests (`test_aws_{ip,kms,snapshot,vpc,vm}_parity.py` and the `*_fix` tests) retargeted to the Cleaner, and the `test_cli_crc_parity.py` operations of the deleted methods dropped. Watch ARCH-008: dropping those cases lowers the test count, so retarget rather than delete.
- The azure, gcp and oci SpotVM Cleaner copies of the locked tests.
- The SAF-030 to SAF-033 locked spot request cases (tag, leftover volume and terminate-error cases) are not copied into `test_aws_spot_instance_requests_engine.py`.

## New bugs

- `crc/cli/bridge.py` `_ReportThenRaise.plan`: for disk, an error while listing a later region now reports the volumes of the earlier regions only in a dry run (they are the ones the cleaner listed); in a real run it reports nothing deleted (nothing was deleted), where `delete_disks` reported the earlier regions it had already deleted. Covered by BUG-391 (fewer deletes), the notification content differs.
- `crc/cli/routes/aws.py:_snapshot_message`: the dry-run line counts `len(plan.candidates)`, which equals the old `delete_count` only because the plan lists the same ids the adapter listed; no behaviour difference found.
- `crc/cli/notify.py`: `notify_deleted_vm_via_slack`, `notify_stopped_vm_via_slack`, `notify_deleted_ip_via_slack`, `notify_deleted_kms_via_slack`, `notify_deleted_spot_request_via_slack`, `notify_deleted_disk_via_slack` are dead in production for AWS (still used by Azure, GCP and OCI paths for vm, ip and disk, and by `test_cli_crc_parity.py`); the kms and spot request ones are dead for every cloud.

## Round 1 (Argon)

Scope reviewed: `git diff 6b3d85e..HEAD` on branch `eng/aws` (lane `ENG-1`).

### Findings & fixes made in review

1. **BLOCKING (fixed): `aws` / `vm` dry-run `delete` through `engine.run` reported instance IDs instead of instance names to Slack and InfluxDB, and logged dry-run `ACTION` lines after all regions instead of per region** (`crc/aws/vm/cleaner.py`, `tests/test_aws_vm.py`, `tests/test_aws_vm_parity.py`, `tests/test_cli_bridge_aws.py`):
   - On `main` (and in `VM.delete()` / `VM.stop()`), a dry-run `delete` populates `get_deleted` with instance `Name` tag values (`dry_run_names(Operation.DELETE, candidates)`), while a dry-run `stop` populates `get_stopped` with instance IDs (`BUG-217`). `engine.run` builds a dry-run `Report` via `Report.from_plan`, which reads `[c.id for c in plan.candidates]`. `VmCleaner.plan()` returned `Candidate(id=instance_id, name=name)` unchanged in dry run, so `--cloud aws --resource vm --dry_run` sent instance IDs (`['i-1', ...]`) instead of instance names (`['vm-1', ...]`) to Slack and InfluxDB.
   - Fixed `VmCleaner.plan()` so in dry-run mode it logs `note_dry_run(operation, found)` per region right after `note_region` (matching `VM._perform_operation`) and sets each returned candidate's `id` to its `dry_run_names(operation, candidates)` value (`c.name` for `delete`, `c.id` for `stop`). Added `test_real_vm_cleaner_through_bridge_matches_oracle_report_names_and_ids` in `tests/test_cli_bridge_aws.py` and updated `tests/test_aws_vm.py` and `tests/test_aws_vm_parity.py`.
2. **BLOCKING (fixed): `ElasticIpCleaner`, `KmsCleaner`, `SnapshotCleaner`, and `VpcCleaner` omitted `ACTION would_delete` run-log lines and reported `selected=0` in `RESULT` during a dry run through `engine.run`** (`crc/aws/elastic_ips/cleaner.py`, `crc/aws/kms/cleaner.py`, `crc/aws/snapshot/cleaner.py`, `crc/aws/vpc/cleaner.py`, `tests/test_aws_misc_logger.py`):
   - `engine.run` calls `plan()` only when `context.dry_run` is `True` and never calls `apply()`. Those four cleaners emitted their `would_delete` run-log actions inside `apply()` / `apply_region()` / `delete_one()` (which the legacy adapters called in dry-run mode), so routing `ip`, `kms`, `snapshot`, and `vpc` through the engine dropped all `ACTION would_delete` lines and left `selected=0` in `RESULT`.
   - Moved dry-run `would_delete` logging into `plan()` / `plan_region()` for `ElasticIpCleaner`, `KmsCleaner`, `SnapshotCleaner`, and `VpcCleaner` (and guarded `apply()` against double-logging in dry-run unit tests). Added `test_engine_dry_run_logs_would_delete_for_kms_snapshot_ip_and_vpc` in `tests/test_aws_misc_logger.py`.
3. **Completeness & test tightening (fixed)** (`tests/test_aws_spot_instance_requests_engine.py`, `tests/test_cli_main_parity.py`, `tests/test_cli_route_fix.py`, `crc/aws/README.md`, `crc/aws/disk/README.md`):
   - Added all remaining locked `SpotInstanceRequests` safety cases (`SAF-030`, `SAF-031`, `SAF-032`, `SAF-033`, and `test_saf_035_retry_already_gone_volume_is_not_recounted`, 13 tests) to `tests/test_aws_spot_instance_requests_engine.py` (now 31 tests covering every SAF ID locked to `tests/test_aws_spot_instance_requests.py`).
   - Asserted `"notags" in context.options` for `ip`, `vpc`, `vm`, `spot_instance_requests`, and `disk` in `tests/test_cli_main_parity.py::install_routed_fakes`, removed dead `Recorder.snapshot_class` in `tests/test_cli_route_fix.py`, and synced `crc/aws/README.md` and `crc/aws/disk/README.md`.

### Checklist verification

1. **CLI result parity for all routed AWS pairs (`keypair`, `ip`, `kms`, `snapshot`, `vpc`, `vm` delete/stop, `spot_instance_requests`, `disk`)**: selection, mutations and order within each region, `RUN START` / `RESULT` lines, `Service` / `init_logging` startup, Slack text and recipients, Influx points, console and log lines, exceptions and exit codes, `validate_targets` and the AWS disk opt-in gate (`Route.precheck`), `NotifyMode.NONE` (`vpc`, `snapshot`), `NotifyMode.REPORT_THEN_RERAISE` (`disk`), and `--dry_run` non-mutation match the legacy path; the only behaviour change is plan-all-regions-before-apply, recorded in `BUG-391` (`docs/refactor/BUGS.md`) and one clause in `CLEANUP_SAFETY.md`.
2. **Parity tests non-vacuous**: `test_cli_main_parity.py`, `test_cli_bridge_aws.py`, `test_cli_bridge_parity.py`, `test_cli_crc_parity.py`, `test_cli_args_parity.py`, `test_cli_dispatch_fix.py`, `test_cli_route_fix.py`, `test_cli_merge_fix.py`, and all `test_aws_*_parity.py` files reach real or recording fake cleaners and assert the oracle facts.
3. **Hash-locked safety tests**: the 5 locked test files and `tests/archtest/safety_lock.json` are untouched and passing; `tests/test_aws_disk_engine.py` (18 tests) and `tests/test_aws_spot_instance_requests_engine.py` (31 tests) cover every SAF case locked to the two AWS files.
4. **Protected files**: no oracle (`tests/legacy/*.txt`), archtest (`tests/archtest/*.py`), guardrail (`AGENTS.md`, `ARCHITECTURE.md`, `.cursor/**`, `pyproject.toml`, `Makefile`, `docs/refactor/PLAN.md`, `docs/refactor/STATUS.md`), or `requirements*.txt` file was edited; `test_count_floor.txt` raised to `43803` via `make raise-test-floor`.
5. **Limits & README sync**: all touched non-legacy, non-test files are at most 250 lines and functions at most 60 lines (`ARCH-004`); all package READMEs are in sync (`ARCH-002`).

### Gates & test count

- `python3 -m ruff check .`: passed
- `python3 -m mypy`: passed (340 source files)
- `lint-imports`: passed (5 contracts kept, 0 broken)
- `python3 -m pytest -q -n auto`: **43803 passed in 53.56s**
- `python3 -m tests.archtest.test_count check`: `ARCH-008: 43803 tests, floor 43803`

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff 6b3d85e..HEAD` on `eng/aws` (3 commits, including the round 1 fixes). No code change in this round.

### Round 1 fixes, re-checked

- VM dry-run names (round 1 item 1): `VmCleaner.plan()` sets the dry-run candidate id to `dry_run_names(...)` (the `Name` tag for delete, the instance id for stop, BUG-217), so `Report.from_plan` sends what `VM.get_deleted` / `get_stopped` held. `note_dry_run` now runs per region, as in `VM._perform_operation`. Correct.
- Dry-run `would_delete` actions (round 1 item 2): `ip`, `kms`, `snapshot` and `vpc` now log them in `plan()`, in the same per-region position the adapters used (VPC: after the default-VPC filter, which only bumps a `skip` counter that `finish()` prints sorted, so the order does not matter). Correct.
- SAF-030..033 spot copies (round 1 item 3): present.

### Independent checks

1. **Parity of the routed pairs** (`ip`, `kms`, `snapshot`, `vpc`, `vm` delete and stop, `spot_instance_requests`, `disk`; `keypair` from ENG-0), checked against `crc/cli/resource_ops.py`, `vm_ops.py`, `report_calls.py` and `notify.py`:
   - Slack and Influx: `REPORT` gives Slack then Influx, each only with a client, as `report_result` does. For a stop run, `_PAST_TENSE` gives "Stopped" (like `notify_stopped_vm_via_slack`) and the Influx `VM` point carries the stopped ids. For spot requests, the `DISK` related section (`notify_empty=False`) gives the extra Slack message and the Influx `DISKS` point of `volumes_to_delete`, and `_INFLUX_LABEL` maps the primary point to `VM`. `vpc` and `snapshot` send nothing (`NotifyMode.NONE`, BUG-084/BUG-009). Disk with `REPORT_THEN_RERAISE` reports, then raises the same error, as `delete_disks` did. The other kinds send nothing on error, as before.
   - Options: `vm` passes `instance_state` only for delete and only when truthy (legacy `_delete_vm`). `stop` passes no states. `ip` and `disk` drop the regexes and Slack owner flags that the AWS adapters never read.
   - Gate and order: `Route.precheck` (the disk opt-in) runs inside `begin`/`finish` and before `init_run_logging`, the same point where `_disk` raised before the adapter was built. `validate_targets` is unchanged. The engine never calls `apply()` in a dry run, and `_ReportThenRaise.apply` returns early after a `plan()` error, so a dry run never mutates.
   - Snapshot console line: `len(plan.candidates)` and `result.succeeded` equal the adapter's `delete_count` and `get_deleted` (same cleaner decisions).
   - The only behaviour change is plan-all-regions-before-apply: BUG-391 (low group end, "fixed in ENG-1", Exists on main yes, location `crc.py` adapter loop) plus one clause in `CLEANUP_SAFETY.md`. Those are the only edits to those two files.
2. **Parity tests are not vacuous.** `test_cli_main_parity.install_routed_fakes` makes every route in `bridge.TABLES` record the oracle `crc.<method>` event from `context.options` (`ROUTED_CALLS` raises `KeyError` for a route without an entry), and the events are compared to the oracle `CRC` fake's events. A fake cleaner that is never reached would therefore fail, not pass. Line 942 also asserts that `Snapshot` and `Snapshot.delete` are recorded. `test_cli_bridge_aws.py` runs the oracle `CRC` method and `run_routed` on the same Slack and Influx fakes, and asserts the cleaner's options and operation. `test_core_selection.py` and `test_cli_logger.py` dropped their `Snapshot` patch for the `FakeCleaners` seam, which their routed side effects reach. No patch of an AWS adapter name in a `crc.cli` module is left unreached: the `test_cli_crc_parity.py` and `test_core_engine.py` patches call the `CRC` methods directly, and those methods still exist.
3. **Locked safety tests**: the 5 files in `safety_lock.json` and the lock itself are untouched and pass. Every SAF ID that the lock maps to `test_aws_disk.py` (003, 007, 011, 012, 013, 015, 017, 018, 019, 020, 025) appears in `test_aws_disk_engine.py`. Every SAF ID mapped to `test_aws_spot_instance_requests.py` (007, 008, 011, 026..033, 035) appears in `test_aws_spot_instance_requests_engine.py`; 011 and 027 are in `test_saf_007_011_027_a_positive_age_is_required`. The azure, gcp and oci SpotVM copies are still to do in their lanes (adapters not touched here).
4. **Protected files**: no `tests/legacy/*`, `tests/archtest/*.py`, `safety_lock.json`, guardrail or requirements file is edited. `tests/archtest/test_count_floor.txt` changed only through `make raise-test-floor`.
5. **Limits and READMEs**: ARCH-002 and ARCH-004 are green in `make check`. The touched READMEs (`crc/aws`, `crc/aws/disk`, `crc/cli`, `crc/cli/routes`) match the new names (`NotifyMode`, `ExtraNotifiers`, `AfterRun`, `partial_succeeded`).

### New bugs (round 2, not fixed)

- `crc/aws/elastic_ips/cleaner.py:90` and `:128`: `ElasticIpCleaner.apply()` in a dry run still calls `apply_region(dry_run=True)`, which logs `would_delete` again after `plan()` already did. Neither the engine nor the adapter calls it that way (the engine skips `apply()` in a dry run, the adapter calls `apply_region` without `plan()`), so production is not affected. Unlike kms, snapshot and vpc, this one has no early dry-run return.
- `crc/aws/vpc/cleaner.py:172` and `crc/aws/elastic_ips/cleaner.py:118` (the same shape in the other AWS cleaners): through the engine, every region's `region_done` line is logged in `plan()`, before any delete line. The adapters logged each region's line after that region's deletes. Same lines and counts, different cross-region interleaving. This follows from plan-all-regions-before-apply (BUG-388/BUG-391) and is cosmetic.

### Gates (after `pip install -r requirements.txt -r requirements-dev.txt`)

- `make check`: ruff, mypy strict, lint-imports, ARCH-001..009 and SAF-LOCK green, exit 0.
- Tests: **43803 passed in 54.39s** (`make check` wall time 74s). `ARCH-008: 43803 tests, floor 43803`.

ROUND 2 VERDICT: APPROVE

