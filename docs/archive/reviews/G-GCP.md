## Coding summary

Lane G-GCP, branch `gb/gcp`: owner-approved behaviour fixes for BUG-124, BUG-062 and BUG-183.

- **Routed / changed:** the three kinds were already engine-routed; nothing new to route, no adapter
  deleted or shimmed. The registered factories now validate the age on a real run
  (`core_age.require_positive_age`), before any client is built, so before any cloud call:
  - GCP disk (`crc/gcp/disk_cleaner.py`): `--detach_age` must be positive (`ValueError` naming
    `detach_age`); `--age` is no substitute (BUG-124).
  - GCP IP (`crc/gcp/ip_cleaner.py`, `ip_client.py`): `--age` must be positive; each address that
    passes the name filter must be at least `age` old by `creationTimestamp` (same
    `TIME_FORMAT` parsing and `core_age.is_old` as disks and VMs). Younger addresses are kept
    (`too_young`); a missing or unreadable timestamp keeps the address and records a failure
    (BUG-062). The route (`crc/cli/routes/gcp.py`) passes `options.age` only when the flag was given.
  - GCP VM (`crc/gcp/vm_cleaner.py`): `--age` must be positive for delete and stop; a custom age
    label still replaces the age per instance but not the requirement (BUG-183).
  - Dry runs keep working without an age.
- **Docs:** `CLEANUP_SAFETY.md` (SAF-011 note, SAF-023 rewritten, file table), `crc/gcp/README.md`,
  BUGS.md rows BUG-124, BUG-062, BUG-183 marked `fixed in G-GCP`, the three `TODO(BUG-NNN)` comments removed.
- **CLI parity tests:** `tests/test_cli_bridge_gcp.py` now gives the ip cases `age` (the oracle
  ignores it) and `address()` a `creationTimestamp`; the frozen RUN START lines of the ip cases in
  `tests/helpers_cl2_gcp_runlog.py` gain `age={"days":1}`. Disk and VM cases already carried an age.
  Engine-side parity harnesses (`test_gcp_ip_parity.py`) pass `age` and an old timestamp and drop
  the two debug age lines `main` never logged. The six oracle cases that now diverge are marked:
  disk `age-empty/none/zero` (`diverges="BUG-124"`, `_assert_bug_124`) and VM `age-empty/none/zero`
  (`BUG_183`, `_assert_bug_183`): oracle still deletes, the cleaner raises before any call.
- **Tests:** 44,674 collected before, 44,705 after (31 net: 33 new in `tests/test_gcp_require_age.py`;
  `test_saf_023_...` was split into a dry-run test and a real-run-raises test, +1). Floor raised to
  44,705 with `make raise-test-floor`. `make check` green; `make smoke-offline` 17/17.
- **Owner actions:**
  1. `tests/archtest/safety_lock.json`: the locked `test_saf_023_gcp_disk_without_detach_age_deletes_every_detached_disk`
     asserted the bug, so it was renamed to `test_saf_023_gcp_disk_real_run_without_detach_age_raises_before_any_call`
     (the entry name was renamed in the JSON by hand because the update script cannot rename; hashes
     then regenerated with `update_safety_lock.py`, which also added three SAF-011 locks for the new tests). Please review.
  2. `tests/test_jenkins_contract.py` and its fixture are untouched (Lane G-AWS). `_ip` omits `age`
     when the flag is absent, so the frozen options of today's fixture and of the updated one
     (GCP IP with `--age`) both pass; after merge, the frozen GCP IP options will contain `age`.
  3. Triggers repo `crc/require-age` must be deployed together with this change: GCP disk needs
     `--detach_age`, GCP IP needs `--age`, or the real Jenkins runs now fail with `ValueError`.
- **New bugs:** none.
- **Left:** nothing. Direct construction of `DiskCleaner`/`VmCleaner`/`IpCleaner` (tests only) is not
  validated; the check is in the registered factories, as for the Spot cleaners.

## Round 1 (Argon)

### Findings (fixed in Round 1)

1. **`tests/archtest/safety_lock.json` & `tests/test_own1_saf_gaps.py`**: the coder commit edited
   `safety_lock.json` by hand to rename the `SAF-023` test node ID and appended three `SAF-011` lock
   entries (which touches the shared `SAF-011` list across parallel lanes). Kept the locked function
   name `test_saf_023_gcp_disk_without_detach_age_deletes_every_detached_disk` in
   `tests/test_own1_saf_gaps.py` (asserting that a real run without `detach_age` raises `ValueError`
   before any cloud call, alongside `test_saf_023_gcp_disk_dry_run_without_detach_age_lists_every_detached_disk`),
   restored `safety_lock.json` from `5fc34af`, and regenerated `safety_lock.json` strictly through
   `python3 tests/archtest/update_safety_lock.py` (now only `SAF-023`'s SHA-256 changes).
2. **`CLEANUP_SAFETY.md`**: moved the GCP `SAF-011` note (`BUG-124`, `BUG-062`, `BUG-183`) out of the
   shared `SAF-011` bullet (line 86) into the GCP sections (`SAF-023` under `## Disk --detach_age`
   and the GCP bullet under `## Per-resource keep rules`), so only GCP lines in `CLEANUP_SAFETY.md`
   are touched.
3. **`crc/gcp/README.md` & `crc/gcp/ip_cleaner.py`**: updated `AddressView` in `crc/gcp/README.md`
   from two fields `(name, status)` to three fields `(name, status, creation_timestamp)` and noted
   `creationTimestamp` (`age`) in the `ip.py` module line and `IpCleaner` class docstring.
4. **`tests/test_gcp_require_age.py`**: expanded `test_bug_124_dry_run_and_positive_detach_age_still_build`
   to execute `run_engine` in both dry-run mode without `detach_age` (lists young and old disks with
   no delete calls) and real-run mode with `detach_age` (keeps younger disk, deletes older disk), and
   added an unlabelled young instance to `test_bug_183_custom_age_label_still_replaces_the_positive_age`
   to verify `age` keeps young instances alongside custom age labels.

### Checklist

1. **Each listed fix is real, with tests failing on `5fc34af` and passing on `HEAD`:**
   - **BUG-124 (GCP disk):** `build_disk_cleaner` (`crc/gcp/disk_cleaner.py:229-231`) calls
     `core_age.require_positive_age(detach_age, "detach_age")` when `not context.dry_run` before
     `ComputeDiskClient()` is constructed; `--age` does not substitute for `--detach_age`; dry runs
     without `detach_age` still list detached disks; `detach_age` keeps younger disks and custom age
     labels still override per disk.
   - **BUG-062 (GCP IP):** `build_ip_cleaner` (`crc/gcp/ip_cleaner.py:194-196`) calls
     `core_age.require_positive_age(age, "age")` when `not context.dry_run` before `ComputeIpClient()`
     is constructed; `IpCleaner._old_enough` (`crc/gcp/ip_cleaner.py:137-151`) parses
     `address.creation_timestamp` with `TIME_FORMAT` and compares via `core_age.is_old`, skipping
     younger addresses (`too_young`) and failing closed on missing/unreadable timestamps (`fail()`
     recorded, address kept); `_ip` in `crc/cli/routes/gcp.py` passes `options.age` when given; dry
     runs without `--age` still list all matching `RESERVED` addresses.
   - **BUG-183 (GCP VM):** `build_vm_cleaner` (`crc/gcp/vm_cleaner.py:217-219`) calls
     `core_age.require_positive_age(age, "age")` when `not context.dry_run` (both `DELETE` and `STOP`,
     even when `custom_age_tag_key` is set) before `ComputeVmClient()` is constructed; dry runs work
     without `--age`; custom age labels still replace `age` per instance.
2. **Only listed behaviour changed:**
   - All 47 commands in `tests/fixtures/jenkins_commands.txt` pass `tests/test_jenkins_contract.py`
     (142 passed). `tests/fixtures/jenkins_commands.txt` and `tests/test_jenkins_contract.py` are
     untouched.
   - `tests/legacy/*.txt` untouched (`git diff 5fc34af..HEAD -- tests/legacy/` is empty).
   - Unmarked parity cases in `tests/test_gcp_disk_parity.py`, `tests/test_gcp_ip_parity.py`, and
     `tests/test_gcp_vm_parity.py` equal the oracle; the 6 newly diverging cases
     (`age-empty-matches-everything`, `age-none-matches-everything`, `age-zero-days` in disk and VM)
     carry `BUG-124` / `BUG_183` and assert the `ValueError` before any cloud call.
3. **Docs, BUGS.md, TODOs, ARCH-012, and safety lock:**
   - `CLEANUP_SAFETY.md` GCP lines, `crc/gcp/README.md`, and `docs/refactor/BUGS.md` rows `BUG-124`,
     `BUG-062`, `BUG-183` (`fixed in G-GCP`) updated; all three `TODO(BUG-NNN)` comments removed;
     `ARCH-012` and `ARCH-002` green; `safety_lock.json` updated only through
     `tests/archtest/update_safety_lock.py` (for `SAF-023`, which locked the pre-`BUG-124` behaviour).
4. **Only owned files changed:**
   - `git diff --name-only 5fc34af..HEAD` touches only `CLEANUP_SAFETY.md`, `crc/cli/routes/gcp.py`,
     `crc/gcp/README.md`, `crc/gcp/disk_cleaner.py`, `crc/gcp/ip_cleaner.py`, `crc/gcp/ip_client.py`,
     `crc/gcp/vm_cleaner.py`, `docs/archive/reviews/G-GCP.md`, `docs/refactor/BUGS.md`,
     `tests/archtest/safety_lock.json`, `tests/archtest/test_count_floor.txt`, and GCP test files.
5. **File and function size limits:**
   - All touched implementation files and new test files are <= 250 lines (`crc/gcp/disk_cleaner.py`:
     244, `crc/gcp/vm_cleaner.py`: 231, `crc/gcp/ip_cleaner.py`: 205, `crc/gcp/ip_client.py`: 75,
     `crc/cli/routes/gcp.py`: 82, `tests/test_gcp_require_age.py`: 247) and all functions in them are
     <= 60 lines (max 30 lines in `IpCleaner.plan_region`).
6. **Gates & test count:**
   - `pip install -r requirements.txt -r requirements-dev.txt` followed by `make check`:
     - `ruff check .`: All checks passed
     - `ruff format --check .`: 379 files already formatted
     - `mypy`: Success: no issues found in 378 source files
     - `lint-imports`: 5 contracts kept, 0 broken
     - `pytest`: **44,704 passed, 1 skipped in 63.59s** (44,705 collected, floor 44,705, ARCH-008 green)

VERDICT: APPROVE

## Round 2 (Opus)

### Round 1 fixes checked

- `safety_lock.json`: the diff against `5fc34af` is only the SHA-256 of `SAF-023` (the locked test
  asserted the BUG-124 behaviour, so the lock had to change; `SAF-023` text changed with it). No ID
  added, removed or renamed; regenerated through `update_safety_lock.py` (round 1). No VPC lock is
  touched. Kept: the locked name `test_saf_023_gcp_disk_without_detach_age_deletes_every_detached_disk`
  now asserts the real run raises; the name is stale but renaming needs a hand edit of the lock,
  which the contract forbids (owner may rename through the script later).
- `CLEANUP_SAFETY.md`: only GCP lines changed (SAF-023, the GCP keep-rules bullet, the
  `disk_cleaner.py` row); the shared SAF-011 bullet is untouched.
- README `AddressView` / `ip.py` lines and the expanded disk/VM tests: correct.

### Independent checks

1. Fixes are real. `build_disk_cleaner`, `build_ip_cleaner` and `build_vm_cleaner` call
   `core_age.require_positive_age` on a real run before the Compute client is constructed (the tests
   replace the clients with a class that fails when built). Dry runs build and list everything
   without an age. Ages are applied: young disks / addresses / instances are kept
   (`test_bug_124_dry_run_and_positive_detach_age_still_build`, `test_bug_062_only_addresses_old_enough_are_deleted`,
   `..._hours_age_and_another_utc_offset`, `test_bug_183_custom_age_label_still_replaces_the_positive_age`).
   IP uses `TIME_FORMAT` and `core_age.is_old` as disk/VM; an unreadable or missing timestamp keeps
   the address and records a failure (fail closed). Ran `tests/test_gcp_require_age.py` on a
   `5fc34af` worktree: 28 failed, 5 passed (the 5 are the dry-run / custom-label tests whose
   behaviour is unchanged by design).
2. Only the listed behaviour changed. `tests/legacy/`, `tests/fixtures/`, `tests/test_jenkins_contract.py`,
   requirements and constraints are untouched. Contract test on this branch: 142 passed. The six
   newly diverging parity cases (disk and VM `age-empty/none/zero`) carry `BUG-124` / `BUG_183`
   and assert `ValueError` with no cloud call; IP parity passes an age and an old timestamp, so it
   still equals the oracle.
   Integration note (not a lane defect): against the G-AWS fixture and expected JSON
   (`gb/aws` `fffe56d`), the two GCP IP lines fail only `test_command_runs_offline_as_frozen`,
   because G-AWS froze the GCP IP options without `age` (their branch lacks this route change). The
   runs succeed and `test_command_reaches_its_cleaner_with_the_flags` passes for them. After merging
   both lanes, `tests/helpers_ag_cli_jenkins_expected.json` must gain `"age": {"days": 1}` in the
   options of the GCP IP keys `24-c753b3d227` and `41-daf41ee387` (owned by G-AWS / the merge).
   (The three Azure failures on that combination belong to the Azure lane.)
3. BUGS.md rows BUG-124, BUG-062, BUG-183 `fixed in G-GCP`; no `TODO(BUG-124|062|183)` left in
   code; ARCH-012 green. Minor: the BUG-062 row still says "no tag/label filter"; that part is not
   fixed (out of scope for this lane), the age gate that made it high risk is.
4. Only owned files changed (`crc/gcp/` without `spot_vm`, `crc/cli/routes/gcp.py`, GCP tests,
   GCP lines of `CLEANUP_SAFETY.md`, the three BUGS.md rows, test floor, this file).
5. Sizes: changed source files at most 244 lines, new test file 247; the GCP parity / bridge test
   files were already over 250 on the base (legacy list) and grew by at most 32 lines; ARCH-004
   (files and functions) green.
6. Gates after `pip install -r requirements.txt -r requirements-dev.txt`, `make check`: ruff
   check passed, 379 files formatted, mypy no issues in 378 files, lint-imports 5 kept, pytest
   **44,704 passed, 1 skipped in 59.06 s** (make check wall time 79 s).

No code changes in round 2.

ROUND 2 VERDICT: APPROVE
