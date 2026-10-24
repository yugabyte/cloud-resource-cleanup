# FIX-GCP-DISK (behaviour-fix rung, wave FIX-1)

## Coding summary

- **Bugs fixed:** BUG-005, BUG-006, BUG-126, BUG-127 (all in `crc/gcp/disk.py`).
  - BUG-005: `notags` is applied with `policy.notags_all`, the helper and semantics of `crc/gcp/vm.py`
    (skip only when the disk's labels match every `notags` key; a disk missing a key is kept for
    deletion). The skip is counted as `exception_tag`.
  - BUG-006: `name_regex` is applied with `policy.name_regex_search_matches` (`re.search`, any
    pattern, empty list matches all; the same helper AWS disk and keypair use, because `vm.py` has no
    name regex). A disk that does not match is skipped as `name_regex_mismatch`. A non-list value or an
    invalid pattern raises `ValueError` naming `name_regex` when the cleaner is built, before any SDK
    call (fail closed). `disk.name` is only read when patterns are set, so a disk without a name
    behaves as before when `name_regex` is empty.
  - BUG-126: after `disks().delete().execute()` the zone operation is polled until DONE without an
    error, with the GCP spot helper `crc.gcp.spot_vm.operations.wait_operation` (180 s, every 5 s). A
    missing operation name, an operation error, a poll error or a timeout is logged, recorded with
    `fail()` and the disk is not recorded as deleted. `GcpDiskClient` gained `get_operation` and
    `ComputeDiskClient` implements it with `zoneOperations().get(...).execute()`.
  - BUG-127: an error while evaluating one disk (timestamp parse, `exception_regex`, `MAX_AGE`, a
    missing field) is logged, recorded with `fail(zone, disk)` and the disk is kept; the loop continues.
    A listing error of one zone (also while iterating) is logged, recorded as `zone <name>` and the
    zone skipped. The zones listing and building the disks client still raise (list/auth failures).
- **Bugs left:** none of the four. BUG-124 (`detach_age`) is out of this rung.
- **Production command:** neither `--notags` nor `--name_regex` is passed, so both are empty and the
  selection is unchanged (every unmarked parity case still equals the oracle). The only visible
  differences for it are the zone-operation polls after each delete and that a failed or
  incomplete delete is no longer counted as deleted. `--filter_tags`, `--exception_tags`,
  `--custom_age_tag_key`, `--slack_channel` keep working with the same flags.
- **Files changed:** `crc/gcp/disk.py`, new `crc/gcp/disk_client.py` (client protocols and
  `ComputeDiskClient`, split out for ARCH-004's 400-line limit and re-exported by `disk.py`),
  `crc/gcp/README.md`, `tests/test_gcp_disk.py` (fake returns an operation), `tests/test_gcp_disk_parity.py`
  (marked cases, wait calls), new `tests/test_gcp_disk_fix.py`.
- **Parity test:** 17 cases carry `diverges` (`BUG-127`; `ignored-age-notags-name-regex` is `BUG-006`).
  For each, the oracle still shows the old outcome and the new code is asserted to give the exact new
  `deleted` list and number of logged errors, on the `Disk` and the Cleaner path. The 10 quirk checks
  of those cases assert the oracle's old quirk and the new outcome. Every unmarked case still equals the
  oracle on calls (leaving out the BUG-126 polls), results, logs and stdout;
  `test_bug_126_every_accepted_delete_is_followed_by_one_operation_poll` pins the polls exactly. The
  oracle runs with its old `notags`/`name_regex` values; the new code with empty ones for unmarked cases.
- **Locked tests:** none touched; `tests/test_gcp_spot_vm.py` passes unchanged.
- **Test count (`pytest -q`):** 42444 at 182466d, 42579 now.
- **Gates:** `make check` green (ruff, mypy, lint-imports, pytest).
- **Owner actions:** see below.

## Owner action

`CLEANUP_SAFETY.md` is CODEOWNERS and was not edited. Proposed edits:

1. Module map row for `crc/gcp/disk.py`, replace with:
   `| crc/gcp/disk.py | GCP disks; detach_age via last_detach_timestamp; --age ignored; custom label replaces detach_age; notags and name_regex applied; delete waits for the zone operation; per-disk and per-zone errors are recorded and skipped |`
2. SAF-021..SAF-024 stay true. Optionally add a new invariant after SAF-045:
   `[SAF-NNN] GCP disk delete is recorded only after the zone operation is DONE without error; an error evaluating or deleting one disk, or listing one zone, is recorded and the run continues; the disk is kept.`

## Owner decision

- Time bound: a stuck zone operation now costs up to 180 s per disk (the spot default) before the
  disk is reported as failed. If that is too long for a Jenkins run with many disks, the bound can be
  lowered through `DiskSettings.wait_sec` (not wired to a flag: no new CLI flag in this wave).

## New bugs

- `crc/gcp/disk.py` `Disk.age` / `--age` is still ignored (SAF-022, known, unchanged) and zonal disks only:
  regional disks are never listed (exists on main: yes; main `crc/gcp/disk.py:117-124`).
- `ComputeDiskClient.list_zones` reads `["items"]` and raises `KeyError` for a project without zones
  (exists on main: yes; main `crc/gcp/disk.py:117-124`); the same call is not isolated by BUG-127 on purpose.
- An invalid `--exception_regex` is now evaluated per disk, so it logs one error per disk and keeps
  every disk instead of aborting the run on the first one (exists on main: yes, as an abort).

## Round 1 (Argon)

### Findings (fixed in round 1)

1. **`crc/gcp/disk.py:57-65` (`_name_patterns`)**: only checked `isinstance(name_regex, str)` after
   `if not name_regex:`. A non-list value such as `""`, `{}`, `{"a": "b"}`, `123` or `[1]` either
   passed silently or raised `TypeError` instead of `ValueError` naming `name_regex`. Fixed to allow
   `None` or a `list`/`tuple` of `str` entries and raise `ValueError` naming `name_regex` before any
   SDK call for any other value; added parametrized test cases in `tests/test_gcp_disk_fix.py`.
2. **`crc/gcp/disk.py:249-263` (`_exception_reason`)**: used separate `policy.exception_tags_any` and
   `policy.notags_all` blocks (normalizing `disk.labels` twice when both are set) instead of
   `policy.should_skip_by_tags`, the helper `crc/gcp/vm.py` uses. Updated to call
   `policy.should_skip_by_tags`.
3. **`tests/test_gcp_disk_parity.py`**: `_args(case, fixed=True)` passed different `notags` and
   `name_regex` inputs to `_legacy` vs `_new` for unmarked cases, and had no parity case marked
   `diverges="BUG-005"`. Unified `_args(case)` so `_legacy` and `_new` receive identical inputs,
   added `Case("notags-skips-matching-label", labelled, notags={"env": ["qa"]}, diverges="BUG-005")`
   to `_label_cases()`, `DIVERGING` and `QUIRKS`, and strengthened `_assert_fixed` to also assert
   `new.count`, `new.stdout`, `disks.delete.execute` calls and `close()`.
4. **`tests/test_gcp_disk_fix.py`**: added `test_production_jenkins_flags_selection_unchanged`
   covering the exact production Jenkins flags.

### Protected files (do not edit)

- **NIT** `CLEANUP_SAFETY.md:211`: module map entry for `crc/gcp/disk.py` still describes pre-fix
  behaviour. Proposed text (matches `## Owner action` above):
  `| crc/gcp/disk.py | GCP disks; detach_age via last_detach_timestamp; --age ignored; custom label replaces detach_age; notags and name_regex applied; delete waits for the zone operation; per-disk and per-zone errors are recorded and skipped |`

### Checklist

1. **Each fixed bug is fixed with tests failing on `182466d` and passing now:**
   - `BUG-005`: `notags` applied via `policy.should_skip_by_tags` (`notags_all` semantics matching
     `crc/gcp/vm.py`), counted as `exception_tag`.
   - `BUG-006`: `name_regex` applied via `policy.name_regex_search_matches` (`re.search`), counted as
     `name_regex_mismatch`; invalid pattern or non-list fails closed with `ValueError` before any SDK call.
   - `BUG-126`: `apply_disk` polls the zone operation via `wait_operation` before recording a disk as
     deleted; missing operation name, operation error, poll error or timeout logs an error, records
     `fail()` and does not record the disk as deleted.
   - `BUG-127`: per-disk evaluation/delete errors and per-zone disk-listing errors (including mid-page
     iteration errors) are logged, recorded via `logger.current().fail(...)`, and skipped while the run
     continues; project zone-listing and client-construction errors still raise.
   - `BUG-124` (`detach_age`) untouched.
2. **Direction rule & parity:** never deletes more than before; unmarked parity cases equal the oracle
   (`tests/legacy/gcp_disk.txt` untouched); 18 diverging cases marked with `BUG-005`, `BUG-006` or
   `BUG-127` assert both divergence from the oracle and the exact new outcome; `BUG-126` operation
   polls pinned across all unmarked cases.
3. **Production command:** verified with exact flags (`--cloud gcp --project_id yugabyte --resource disk --filter_tags ... --exception_tags ... --slack_channel ... --custom_age_tag_key max_retention_age`); selection unchanged.
4. **Fail-closed paths:** invalid `name_regex` raises `ValueError` in `DiskCleaner.__init__` before any SDK call.
5. **Protected / forbidden files:** `git diff 182466d..HEAD --name-only` touches only `crc/gcp/README.md`, `crc/gcp/disk.py`, `crc/gcp/disk_client.py`, `docs/refactor/reviews/FIX-GCP-DISK.md`, `tests/test_gcp_disk.py`, `tests/test_gcp_disk_fix.py`, `tests/test_gcp_disk_parity.py`. No locked safety test, `safety_lock.json`, `PLAN.md`, `STATUS.md`, `BUGS.md` or `CLEANUP_SAFETY.md` edited.
6. **README:** `crc/gcp/README.md` `## Public API` and `## Safety Invariants` updated and ARCH-002 green.
7. **Unfixed bugs:** all four in-scope bugs fixed; operation wait timeout bound noted under `## Owner decision`.
8. **Gates & test count:** `make check` (`ruff check .`, `mypy`, `lint-imports`, `pytest -q`) green; `42587 passed` (up from `42444` at `182466d`).

VERDICT: APPROVE

## Round 2 (Opus)

### Round 1 fixes checked

- `_name_patterns` now accepts only `None` or a list/tuple of `str` and raises `ValueError` naming
  `name_regex` otherwise, before any SDK call (`DiskCleaner.__init__`, and `Disk.delete()` builds the
  cleaner before `list_zones`). The CLI already rejects a non-list `--name_regex`
  (`crc/cli/inputs.py:153`), so the extra check only covers the registry path. Correct.
- `_exception_reason` uses `policy.should_skip_by_tags` as `crc/gcp/vm.py:255` does. With `notags`
  empty it reduces to `exception_tags_any`, so the production selection is unchanged. Correct.
- Parity `_args(case)` now gives the oracle and the new code the same inputs. The
  `notags-skips-matching-label` (BUG-005) case was added. Correct.

### Independent checks

1. **Bugs fixed, with tests that fail on `182466d`:** I copied `tests/test_gcp_disk_fix.py` into a
   worktree at `182466d`, and all 45 tests fail there (`45 failed`). The BUG-005/006/126/127 tests
   fail on behaviour. The production-flags and zones-listing tests fail there only because
   `DiskSettings`/`get_operation` do not exist yet, which is expected. The fixes match the BUGS.md rows:
   - BUG-005: `notags_all` is used, as in the VM cleaner.
   - BUG-006: `re.search` is used.
   - BUG-126: `wait_operation` (spot helper, `zoneOperations().get`) runs before `_record`. A missing
     name, an operation error, a poll error or a timeout causes a `fail()` and the disk is not recorded.
   - BUG-127: an error evaluating one disk is caught in `_evaluate`. An error listing or iterating a
     zone is caught in `plan_zone`, and a `zone <name>` failure is recorded.
2. **Direction and parity:** `tests/legacy/gcp_disk.txt` is untouched. The 18 diverging cases all
   carry an in-scope ID: 16 × BUG-127, 1 × BUG-005 and 1 × BUG-006. Each one asserts the exact new
   `deleted` list, the delete calls, the error count, the stdout and `close()`. For unmarked cases,
   only the `zoneOperations*` polls are filtered out of the comparison. Those polls are pinned
   separately by `test_bug_126_every_accepted_delete_is_followed_by_one_operation_poll`, so the
   comparison is not loosened in general. One direction note follows under "Owner decision (proposed)".
3. **Production command:** the CLI passes `notags=None` and `name_regex=None` (no defaults in
   `crc/cli/args.py`), so `_name_patterns` returns `()` and `should_skip_by_tags(..., {})` equals the
   old `exception_tags_any`. The selection is unchanged.
   `test_production_jenkins_flags_selection_unchanged` covers the exact Jenkins labels. The flags
   are the same as before.
4. **Fail closed:** an invalid or non-list `name_regex` raises `ValueError` in `DiskCleaner.__init__`,
   before `list_zones`, `list_disks` or `delete_disk`.
5. **Protected files:** `git diff 182466d..HEAD --name-only` shows no locked test, no
   `safety_lock.json`, and no change to PLAN/STATUS/BUGS or `CLEANUP_SAFETY.md`.
   `tests/test_gcp_spot_vm.py` passes.
6. **README (ARCH-002):** Safety Invariants, the module list, `GcpDiskClient`, `DiskSettings` and
   `Disk` were updated. **Fixed in round 2:** the `build_disk_cleaner()` Public API entry still
   listed the options without `notags` and `name_regex`. It now names both, plus the `ValueError`
   for `name_regex`.
7. **Unfixed bugs:** none of the four are left unfixed. BUG-124 is untouched.

### Owner decision (proposed, not blocking)

- BUG-127 makes the run go on after an error. So for the inputs BUG-127 covers, disks in later
  zones, or later in the same zone, are now deleted, where `main` aborted before reaching them. The
  parity case `disks-list-first-zone-error` pins this: the oracle deletes nothing and the new code
  deletes `a` in `z2`. Every such disk passes every filter and age gate, so no disk is selected
  that the filters keep. But this is more deletes per run than the aborted run made. The rung's task
  notes ask for exactly this ("the loop continues", "the zone skipped"), so I accept it. The owner
  should confirm that this note overrides the literal direction rule ("never delete more than
  today") for BUG-127. The alternative would be to stop deleting for the rest of the run after the
  first error, which does not match the task.

### NITs (not fixed)

- `crc/gcp/disk.py:171-174`: a failed zone listing records `fail(zone, "zone <name>")` and still
  emits a `REGION` line through `scan.finish()`. The spot cleaner instead calls `region_skipped()`
  and emits no `REGION` line. Both are allowed by LOGGING.md, and the task asks for "recorded", so
  I left it.
- `crc/gcp/disk.py:233-242`: a `SpotSettings` is built for each delete only to pass `wait_sec` and
  `poll_sec`. It could be built once in `__init__`. This is cosmetic.

### Gates

- `pip install -r requirements.txt -r requirements-dev.txt`: OK.
- `make check`: green. `ruff` passed, `mypy` reported no issues in 230 source files, and
  `lint-imports` reported 5 contracts kept and 0 broken.
- Test count: **42587 passed**. After the README edit I reran `tests/archtest` and
  `tests/test_gcp_disk*.py`: 549 passed.

ROUND 2 VERDICT: APPROVE

