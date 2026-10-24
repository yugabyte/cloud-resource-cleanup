# FIX-AZU-DISK review

## Coding summary

Rung FIX-AZU-DISK (wave FIX-1): behaviour fixes in `crc/azu/disk.py`. Status: complete.

- Bugs fixed: BUG-172, BUG-174.
  - BUG-172: `AzureDiskCleaner.list_disks()` calls `disks.list_by_resource_group(Base.resource_group)`
    instead of the subscription-wide `disks.list()`. The delete still uses the same configured
    group, so a disk is only ever listed and deleted in the group the job targets. The group is not
    derived from the listed disk's id. The production commands (`--resource_group "$RESOURCE_GROUP"`,
    same filters) keep the same flags and the same selection inside that group.
  - BUG-174: `delete_disk()` waits for the `begin_delete` poller (`.result()`) before the disk is
    recorded as deleted, and catches `Exception` per disk: it logs
    `Error occurred while deleting disk <name>: <error>` at ERROR, records `_logger.failed(...)` in
    the run log (RESULT `failed=N`), returns the error text and the caller (`apply()` and
    `Disk.delete()`) goes on with the next disk. `apply()` returns `Result.failed`. Dry runs call no
    delete and no poller. Listing errors still propagate.
- Bugs left: none of the two. Part of the BUG-174 row stays as today on purpose: errors while
  deciding a disk (`time_created` None `AttributeError`, naive `time_created` `TypeError`,
  `disk_state` None `TypeError`) and mid-listing paging errors still propagate (task: "listing
  errors still propagate as today"; they happen before any delete in `plan()`). See "Owner decision".
- Files changed: `crc/azu/disk.py`, `crc/azu/README.md` (Safety Invariants / quirk note),
  `tests/helpers_azure_disk.py` (fake: `list_by_resource_group`, poller with `result()`),
  `tests/test_azure_disk_parity.py` (cases marked `diverges="BUG-174"` plus new
  `poller-error-not-deleted` and `begin-delete-error-every-disk`; the oracle `("list",)` call is
  mapped to the new group listing for every case; two quirk checks that asserted the old
  propagation are replaced by the `DIVERGING` table), `tests/test_azure_disk.py` (listing call;
  the `apply propagates delete error` test is replaced by the isolation tests), new
  `tests/test_azure_disk_fix.py`. Oracles in `tests/legacy/` and the hash-locked tests are untouched.
- Test count (`pytest --collect-only`): 42444 at 182466d, 42454 now. `make check` green.
- Owner actions: none required.

## Owner decision

- Decision/selection errors (BUG-174 sub-cases `created-none-error`, `state-none-error`, naive
  `time_created`) are not isolated: in the registered path they abort `plan()` before any delete, and
  `Disk.delete()` (legacy adapter) still stops mid-loop after earlier disks were deleted, as today.
  Isolating them means skipping a disk whose age or state cannot be read; that is a selection change
  that was not in the approved scope. Tests: `test_bug_174_selection_errors_still_propagate`.
- Exit code: a failed delete no longer raises, so the process no longer exits non-zero because of it
  (the failure appears in the RESULT line `failed=N` and the ERRORS list). If Jenkins must still go
  red on a failed delete, the CLI exit-code rule belongs to the CLI layer (not changed here).

## New bugs

- `crc/azu/disk.py` `Disk.delete()` / `apply()`: a disk named `None` is deleted by Azure with
  `begin_delete(rg, None)` semantics unclear; with the fix the later `"Deleted disk: " + name`
  `TypeError` is caught and reported as a failure. Exists on main: yes (it raised there). Not fixed.

## Round 1 (Argon)

### Findings

- Fixed in review:
  - `crc/azu/README.md:12,41`: updated `## Purpose` (`begin_delete(...).result()`) and `## Public API` (`DiskOperations`: `list_by_resource_group and begin_delete` instead of stale `list and begin_delete`) to match `crc/azu/disk.py`.
  - `tests/test_azure_disk_fix.py`: added `test_production_commands_keep_selection_and_list_configured_group` covering all three production Jenkins CLI invocations (`--cloud azure --resource disk`) end-to-end.
  - `tests/test_azure_disk_parity.py`: asserted summary log lines in `_assert_diverges` and `_assert_cleaner_diverges` for `BUG-174` diverging cases.
- Checklist verification:
  1. **Bugs fixed**: `BUG-172` (`disks.list_by_resource_group(self._resource_group)` replaces subscription-wide `disks.list()`, never deriving group from resource id) and `BUG-174` (`begin_delete(...).result()` awaited before recording deletion, per-disk `Exception` logged at `ERROR`, recorded via `_logger.failed` and `Result.failed`, loop continues to next disk, listing/selection errors still propagate) are fixed and covered by unit and parity tests that fail on `182466d` and pass now.
  2. **Direction rule**: `list_by_resource_group` only narrows listing to the configured resource group; `decide()` is unchanged; failed deletes are no longer recorded as deleted; `tests/legacy/azu_disk.txt` is untouched; diverging parity cases are explicitly marked `diverges="BUG-174"` with exact `Fixed` assertions in `DIVERGING`.
  3. **Production commands**: all three Jenkins commands run with identical flags and selection inside `$RESOURCE_GROUP`.
  4. **Fail-closed paths**: listing and selection errors in `plan()` raise before any `begin_delete` call.
  5. **Protected files**: `tests/archtest/safety_lock.json`, locked safety test files, `docs/refactor/PLAN.md`, `docs/refactor/STATUS.md`, `docs/refactor/BUGS.md`, and `CLEANUP_SAFETY.md` are untouched.
  6. **README (ARCH-002)**: `crc/azu/README.md` `## Purpose`, `## Public API`, and `## Safety Invariants` updated and in sync.
  7. **Unfixed sub-cases**: selection/decision error propagation and CLI exit-code semantics are documented under `## Owner decision`.
  8. **Gates**: `ruff check .`, `mypy`, `lint-imports`, and `pytest -q` all pass (42,457 passed, up from 42,444 at `182466d`).

VERDICT: APPROVE


## Round 2 (Opus)

### Round 1 fixes checked

- `crc/azu/README.md` `## Purpose` / `## Public API` updates match `crc/azu/disk.py`.
- `tests/test_azure_disk_fix.py::test_production_commands_keep_selection_and_list_configured_group`
  runs the three Jenkins commands through `crc.cli.main.main` with the same flags; it asserts the
  same selection (`match-old` only; autoclean, `crc: gke`, attached and other-task disks kept).
- Parity summary-line asserts in `_assert_diverges` / `_assert_cleaner_diverges` are correct.

### Independent checks

1. Bugs fixed. Copied `tests/test_azure_disk_fix.py` and `tests/helpers_azure_disk.py` onto a
   182466d worktree: 9 of 12 fail there (both BUG-172 tests, the four BUG-174 isolation / poller /
   run-log / adapter tests, the three production-command tests, which fail only on the listing
   call, while the old code selects the same `['match-old']`). The 3 that pass on both are
   meant to: listing error propagates, dry run never deletes or waits, selection errors propagate.
2. Direction rule. BUG-172: a disk of the configured group was deleted when any listed disk with
   its name passed the filters; now only when the disk itself passes, so the deleted set is a
   subset of today's. `decide()` is unchanged. BUG-174: a failed poller now keeps the disk out of
   the deleted list (reports less). Continuing after a failed delete does delete later candidates
   that today's crashed run left behind; the task notes approve this explicitly ("the loop
   continues with the next disk"), and those disks pass the same selection, so they would be
   deleted on the next run today. Parity: `tests/legacy/azu_disk.txt` untouched; the only global
   mapping is `_fix_172` (`("list",)` -> `("list_by_resource_group", <group>)`), which is the
   BUG-172 call itself and leaves every other call, result, log and error compared exactly; the
   five diverging cases carry `diverges="BUG-174"` and assert the exact new outcome in `DIVERGING`
   plus the old oracle error, so a changed oracle fails.
3. Production commands: same flags, same selection inside `$RESOURCE_GROUP` (point 1). They go
   through the `Disk` adapter (`crc/cli/resource_ops.py:177`), which is covered by
   `test_bug_174_adapter_continues_and_lists_only_deleted` and the parity `test_matches_main`.
4. Fail-closed: listing errors (including a group the SDK rejects) and selection errors raise in
   `plan()` / the adapter loop before any `begin_delete`.
5. Protected files: `git diff 182466d..HEAD` touches none of the locked safety tests,
   `tests/archtest/**`, `safety_lock.json`, `tests/legacy/**`, PLAN.md, STATUS.md, BUGS.md,
   CLEANUP_SAFETY.md, AGENTS.md, ARCHITECTURE.md or `.cursor/**`.
6. README. Fixed in review: `crc/azu/README.md` `## Public API` did not list the new disk
   `DeletePoller` Protocol (the name check passed only because `nic.py` has a Protocol with the same
   name); added it, and noted that `delete_disk` returns None or the error text and never raises.
   Safety Invariants line for AZ-3 is correct.
7. Owner decision section covers the parts left as today (selection-error propagation, exit code).

### Findings

- NIT (owner decision already recorded, agree): a failed delete no longer makes `crc.py` exit
  non-zero; the run log `RESULT ... failed=N` and the ERROR line are the only signals. If Jenkins
  should go red on `failed>0`, that belongs to a CLI rung, not this one.
- NIT: if `Base.resource_group` resolves to `""` (`--resource_group ""` and
  `AZURE_RESOURCE_GROUP=""`), a dry run that used to list the whole subscription now fails at
  `list_by_resource_group("")` in the SDK. That is fail-closed, before any mutation, and no
  production command does it. No change.
- NIT: `delete_disk` wraps `_logger.acted` and the `"Deleted disk: " + name` log in the same
  `try` as the delete, so an error after a successful `.result()` records a deleted disk as failed
  (only reachable with a `None` name, already listed under "New bugs"). Reports less, never
  deletes more. No change.
- No BLOCKING findings.

### Gates

- `pip install -r requirements.txt -r requirements-dev.txt`: ok.
- `make check` (ruff, mypy, lint-imports 5 contracts kept, pytest): green, 42457 passed
  (42444 at 182466d).
- After the README edit: `ruff check .` clean; `tests/archtest`, `tests/test_azure_disk.py`,
  `tests/test_azure_disk_fix.py`, `tests/test_azure_disk_parity.py`: 309 passed.

ROUND 2 VERDICT: APPROVE
