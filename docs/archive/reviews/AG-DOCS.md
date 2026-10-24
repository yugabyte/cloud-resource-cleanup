# AG-DOCS review

## Coding summary

Lane AG-DOCS (branch `ag/docs`): every live doc now describes the current code, and ARCH-011
checks that.

- Routed: nothing in the code changed. Every pair already runs through `crc.core.engine`; the
  docs now say so. No adapter was deleted or shimmed by this lane (AG-ADAPT owns that).
- Deleted-code descriptions removed: `dispatch.HANDLERS`, `crc/cli/vm_ops.py`,
  `resource_ops.py`, `notify.py`, `CRC.delete_*`, `AWS_Disk`, `GCP_Disk`, the "kinds being moved
  to the engine" text in `AGENTS.md`, `ARCHITECTURE.md` (run-flow diagram; add-a-cleaner steps
  6-8 now name args choices, `constants.RESOURCES`, `pairs.py`, routes, `KIND_LABELS`, tests and
  the CLI parity tests to extend), `CLEANUP_SAFETY.md` (SAF-004, SAF-005, SAF-018, SAF-021,
  SAF-022, the engine-path note, the module map), `crc/cli/README.md`, `.cursor/rules`,
  `.cursor/skills` and `tests/legacy/README.md`. Every SAF rule is kept and numbered as before.
- BUG-004: the KMS (`crc/aws/README.md`), Azure disk, GCP disk and `is_old_enough`, and OCI disk
  README lines now say a present but bad custom age tag keeps the resource (`KEEP_AGE`).
  `crc/core/README.md` marks `gcp_ip_exception_excludes` and `nic_name_included` as dead helpers
  (BUG-370), and no longer lists the inverted GCP IP exception as a current quirk.
- One bug-location rule, worded the same in `AGENTS.md`, the `BUGS.md` header, rules 1-2 of
  `BUGS.md`, `docs/refactor/README.md`, `docs/refactor/bugs/README.md`, `bugs/pending/README.md`,
  `fix-bug.md`, the package READMEs and the ARCH-009 remediation: a new row cites the current
  `file:line` in `crc/`; an old row keeps its main location. `BUGS.md` header and notes links
  now point to `docs/archive/`. No `BUGS.md` row was edited.
- `AGENTS.md` (112 lines, within ARCH-010): measured timings (4-core VM, warm caches:
  `make check-fast` 24 s, `make check` 61 s), "never hand-edit `test_count_floor.txt`
  (`make raise-test-floor` only)", which failures print `REMEDIATION:` (archtest) and which do
  not (ruff, mypy, import-linter, plain tests), the flag workflow now states that
  `tests/test_cli_args_parity.py` compares flag for flag against the oracle. The rule numbers in
  the ARCH-007 remediation are now "rules 3 and 7" and the ARCH-009 remediation cites rule 5.
- ARCH-011 (`tests/archtest/doc_links.py`) now scans every `.md` and `.mdc` file recursively
  except `docs/archive/**`, symlinks and cache directories, and checks backticked repo-relative
  paths under `crc/`, `tests/`, `docs/`, `.cursor/`, `.github/` (a path must exist; a glob or
  `<placeholder>` must match at least one path; `:line`, `::test` and `#anchor` suffixes are
  dropped). Eight self-tests were added or reworked. Historic paths are written `main:<path>`
  (`tests/legacy/README.md`).
- Test count: before 44,415 (floor 44,415); after 44,421; floor raised with
  `make raise-test-floor`. `make check` is green.

## Owner action

- None required for the guardrail files. Decision for the owner: ARCH-011 skips the whole table
  rows of `docs/refactor/BUGS.md` for the backticked-path check, not only the "Location on main"
  column, because the title and notes cells of old rows also name files that exist only on
  `main` (for example `crc/oci/spot_vm.py`, `crc/cli/notify.py`) and rows may not be edited.
  The header and footer of `BUGS.md` are checked. Links are checked everywhere.

## What is left

- `crc/aws/vm`, `crc/azu/vm`, `crc/oci/vm` READMEs and the VM lines of `crc/gcp/README.md` (AG-ADAPT)
  still say the age tag "fails open" (`crc/azu/vm/README.md:47`, `crc/aws/vm/README.md:46`,
  `crc/oci/vm/README.md:39`) and `crc/oci/vm/README.md:53` still has the old bug-location text.
- The Spot and disk adapter classes (`SpotVM`, `SpotInstanceRequests`, `Disk`, `VM`) still exist
  in the code, so their READMEs still describe them; they need a pass when AG-ADAPT deletes or
  shims them.
- `tests/test_cli_args_parity.py`, `test_cli_main_parity.py` and `test_core_selection.py` fail
  for an added flag or resource (AGENTS.md says so now); an additive-flag allowance is a code
  change outside this lane.

## New bugs

- None found. (Doc-only drift fixed above; no new `BUGS.md` rows.)

## Round 1 (Argon)

### Findings

None blocking. Every item of the lane task (`git diff a0b3d09..HEAD`) was verified against the live code and the two audits (`origin/audit/agent-native-2:docs/archive/reviews/AGENT-NATIVE-AUDIT-2.md` and `origin/audit/agent-native-2-gpt:docs/archive/reviews/AGENT-NATIVE-AUDIT-2-GPT.md`):

1. **Task item (1) — Deleted-code descriptions removed & SAF rules preserved:**
   - Verified across `AGENTS.md`, `ARCHITECTURE.md`, `CLEANUP_SAFETY.md`, `.cursor/**`, and all owned `crc/**/README.md` / `tests/**/README.md` / `docs/**/README.md` files that no references remain to deleted `dispatch.HANDLERS`, `crc/cli/vm_ops.py`, `crc/cli/resource_ops.py`, `crc/cli/notify.py`, `CRC.delete_*`, `AWS_Disk`, `GCP_Disk`, legacy handlers, or "kinds being moved to the engine".
   - Checked `ARCHITECTURE.md` run-flow diagram (`:9-18`), bridge/routes description (`:37-46`), and "Add a cleaner" steps 6–8 (`:115-129`) against `crc/cli/dispatch.py`, `crc/cli/bridge.py`, `crc/cli/args.py`, `crc/cli/constants.py`, `crc/cli/pairs.py`, `crc/cli/routes/*.py`, `crc/core/engine.py` (`KIND_LABELS`), `crc/core/report.py`, `tests/test_cli_main_parity.py`, `tests/helpers_routed_*.py`, `tests/test_cli_bridge_*.py`, and `tests/helpers_cli_routes.py`.
   - Checked `CLEANUP_SAFETY.md` (`[SAF-004]` at `:48-52`, `[SAF-005]` at `:53-54`, Engine path at `:55-59`, `[SAF-018]` at `:121-122`, `[SAF-021]`/`[SAF-022]` at `:130-132`, Module map at `:259,263`) against `crc/aws/disk/cleaner.py`, `crc/aws/disk/settings.py`, `crc/azu/spot_vm/cleaner.py`, and `crc/gcp/disk_cleaner.py`. All 46 `[SAF-001]`..`[SAF-046]` tags are intact and unrenumbered.
2. **Task item (2) — `BUG-004` fail-closed (`KEEP_AGE`) & `BUG-370` dead policy helpers:**
   - Verified `crc/aws/README.md:44` (KMS), `crc/azu/README.md:38` (Azure disks), `crc/gcp/README.md:25,54` (`is_old_enough` and GCP disk), and `crc/oci/disk/README.md:39` against `crc/core/age_tags.py:111-145`, `crc/aws/kms/cleaner.py:152-164`, `crc/azu/disk/cleaner.py:157-166`, `crc/gcp/_common.py:26-45`, `crc/gcp/disk_cleaner.py:207-216`, and `crc/oci/disk/decide.py:45-56`.
   - Verified `crc/core/README.md:59-62,179-180` against `crc/core/policy_text.py:39-59` (`gcp_ip_exception_excludes` and `nic_name_included` documented as dead helpers per `BUG-370`, keeping `ARCH-002` symbol sync green until `AG-ADAPT` removes them).
3. **Task item (3) — Unified bug-location rule & `BUGS.md` header/footer links:**
   - Confirmed identical rule wording ("a new row cites the current `file:line` in `crc/`; an old row keeps its main location") across `AGENTS.md:68-70,80-82`, `docs/refactor/BUGS.md:4-14`, `docs/refactor/README.md:17,21-22`, `docs/refactor/bugs/README.md:16-17`, `docs/refactor/bugs/pending/README.md:13`, `.cursor/skills/crc-maintain/fix-bug.md:7-11`, `tests/archtest/bug_refs.py:14-16` (`ARCH-009` remediation), and package READMEs (`crc/README.md:45`, `crc/aws/README.md:60`, `crc/azu/README.md:51`, `crc/notify/README.md:56`, `crc/oci/README.md:50`, `crc/oci/spot_vm/README.md:74`).
   - Confirmed `docs/refactor/BUGS.md:27-29,445` links resolve to `../archive/{FILTER_VARIANTS,STATUS,PLAN}.md` and zero `BUGS.md` table rows were edited.
4. **Task item (4) — `AGENTS.md` timings, floor rule, remediation scope & rule numbers:**
   - Verified `AGENTS.md` (112 lines <= 120 `ARCH-010` limit) states measured warm-cache timings (`make check-fast` ~24–25s, `make check` ~58–61s), forbids hand-editing `test_count_floor.txt` (`make raise-test-floor` only; also in `.cursor/rules/30-safety.mdc:12-13`), clarifies that archtest checks print `REMEDIATION:` while ruff/mypy/import-linter/plain tests do not (also in `.cursor/rules/00-map.mdc:15-16`), and explains `tests/test_cli_args_parity.py` flag-for-flag oracle comparison (`:101-104`).
   - Verified `tests/archtest/frozen_inputs.py:13-14` cites `AGENTS.md` rules 3 and 7, `tests/archtest/bug_refs.py:15` cites rule 5, and `tests/legacy/README.md:63` cites rule 3.
5. **Task item (5) — `ARCH-011` (`tests/archtest/doc_links.py`) recursive live-markdown & backticked-path check:**
   - Verified `_documents()` scans all 50 live `.md` and `.mdc` files recursively while skipping `docs/archive/**`, symlinks, and cache/venv directories.
   - Verified `_repo_paths()` and `_path_exists()` check backticked repo-relative paths under `crc/`, `tests/`, `docs/`, `.cursor/`, and `.github/` (stripping `:line`, `::test`, `#anchor` suffixes and requiring globs and `<placeholder>` patterns to match at least one path).
   - Verified the coder's documented reason under `## Owner action` for exempting full `BUGS.md` table rows (`_REGISTER_ROW`) rather than only the `Location on main` column: 4 old rows (`BUGS.md:192,308,361,378`) contain `main`-only paths (`crc/oci/spot_vm.py`, `crc/oci/vm.py`, `crc/azu/vm.py`, `crc/azu/spot_vm.py`, `crc/cli/notify.py`) in their `Title` column and rows may not be edited.

### Gates & Checks

- **Check (2) CLI & behaviour parity:** No runtime Python code under `crc/` or `crc.py` was modified. `python3 crc.py --help` exits `0` with unchanged output.
- **Check (3) Guardrails & mutation proof:** `tests/archtest/safety_lock.json`, `update_safety_lock.py`, the 5 hash-locked safety tests, `tests/legacy/*.txt`, `requirements.txt`, and `tests/archtest/frozen_inputs.json` are untouched. Replaced self-test `test_arch011_selftest_ignores_anchors_external_code_and_archive` plus `test_arch011_selftest_scans_every_markdown_file_recursively` and 5 new backticked-path/glob/register/symlink self-tests in `tests/archtest/test_arch011_doc_links.py` assert strictly more than before (+6 net tests). Verified locally by injecting a dead link (`[missing](no_such_doc.md)`), a deleted file path (`` `crc/cli/vm_ops.py` ``), and an unmatched placeholder (`` `crc/aws/nope_<kind>.py` ``) into `crc/aws/README.md`: `test_arch011_every_relative_link_resolves` failed and reported all 3 violations with `REMEDIATION:` blocks; reverted before committing.
- **Check (4) File ownership:** All 31 changed files in `a0b3d09..HEAD` belong to lane `AG-DOCS` (plus `tests/archtest/test_count_floor.txt` via `make raise-test-floor` and this review file). Note for integrator: `crc/core/README.md:59-62,179-180` was updated per item (2) and may overlap if `AG-ADAPT` deletes the `BUG-370` dead helpers.
- **Check (5) Size limits & `ARCH-002`:** `tests/archtest/doc_links.py` is 113 lines (max function 20 lines), `tests/archtest/test_arch011_doc_links.py` is 163 lines, `AGENTS.md` is 112 lines (`<= 120`), `.cursor/rules/*.mdc` are 16–20 lines (`<= 40`), `.cursor/skills/crc-maintain/SKILL.md` is 52 lines (`<= 150`). `ARCH-002` Public API sync passes across all packages.
- **Check (6) `make check`:** After `pip install -r requirements.txt -r requirements-dev.txt`:
  - `make check-fast`: `14,063 passed in 24.50s` (`25.47s` wall).
  - `make check`: `ruff check .` passed, `mypy` passed (`367 source files`), `lint-imports` passed (`5 kept, 0 broken`), `pytest -q -n auto` passed (`44,421 passed in 50.87s`), `ARCH-008: 44421 tests, floor 44421` (`57.70s` total wall time).

VERDICT: APPROVE

## Round 2 (Opus)

### Round 1 fixes and findings

Round 1 made no code changes and raised no blocking finding. I rechecked its claims against
the code and agree with them, with one exception (finding 1 below).

### Findings

1. Item (4) is not complete. `tests/archtest/test_count.py:17` (the ARCH-008 remediation)
   still cites "AGENTS.md hard rule 1" for "tests may only be added or strengthened". That is
   rule 3 (`AGENTS.md:61`); rule 1 is "never call a real cloud API". The file is not in this
   lane's owned list (only the remediation strings of `bug_refs.py` and `frozen_inputs.py`
   are), so I did not edit it. See Owner action. The other citations are correct:
   `bug_refs.py:15` cites rule 5 (bugs go to `BUGS.md`), `frozen_inputs.py` cites rules 3
   and 7, and `banned_constructs.py:11` cites rule 7 (no new dependencies).
2. I accept the whole-row exemption for `BUGS.md` rows, which goes beyond the task's "exclude
   the Location on main column". The non-location cells of the rows also name files that
   exist only on `main` or were deleted during the refactor. Examples: BUG-403 names
   `crc/cli/notify.py`, BUG-002 and BUG-004 name `crc/azu/vm.py`. Rows may not be edited, so
   checking those cells would force a rule break. The header, the rules, the notes and every
   link in `BUGS.md` (rows included) are still checked; I proved this with mutation M-doc
   below.
3. Item (1) checked in code. `dispatch.py` validates every pair (`validate_targets`) and then
   loops. `bridge.route_for` and `run_routed` exist. `routes/skips.py` holds the warn-only
   pairs. `pairs.SUPPORTED_CLOUDS`, `engine.KIND_LABELS`,
   `tests/test_cli_main_parity.py` `ROUTED_CALLS`, `tests/helpers_routed_<cloud>.py` and
   `tests/test_cli_bridge_<cloud>.py` all exist. SAF-004 ("the disk route reports first,
   then re-raises") matches `routes/aws.py:127` `NotifyMode.REPORT_THEN_RERAISE` and
   `bridge._ReportThenRaise`. A grep of the live docs finds no remaining mention of
   `HANDLERS`, `vm_ops`, `resource_ops`, `CRC.delete_*`, `AWS_Disk` or `GCP_Disk`.
4. Item (2) checked: `crc/core/age_tags.py:111-115` returns `KEEP_AGE`. The remaining "fail
   open" lines are all in AG-ADAPT-owned VM READMEs (`crc/gcp/README.md:55`,
   `crc/azu/vm/README.md:47`, `crc/oci/vm/README.md:39`), as the coder listed.
   `gcp_ip_exception_excludes` and `nic_name_included` are still in `crc/core/policy_text.py`
   and are called by no cleaner, so "dead helper (BUG-370)" is true. Merge risk: AG-ADAPT
   may delete them, which also touches `crc/core/README.md:59-62`.
5. Item (3) checked: the location rule is worded the same in `AGENTS.md:68-70`, `BUGS.md:3-12`,
   `docs/refactor/README.md:21-22`, `docs/refactor/bugs/README.md:16-17`, `fix-bug.md:7-8` and
   `bug_refs.py:15`. The `BUGS.md` links to `../archive/{FILTER_VARIANTS,STATUS,PLAN}.md`
   resolve. No `BUGS.md` row changed in `a0b3d09..HEAD`.
6. The `AGENTS.md` claim that `make check-fast` skips the floor check is true: in the
   `Makefile`, only `check` runs `tests.archtest.test_count check`.

### Gates

- CLI: `python3 crc.py --help` exits 0. Its 128 lines are byte-identical to the output at
  `a0b3d09`. No `.py` file under `crc/` and no `crc.py` changed.
- Guardrails untouched: `safety_lock.json`, `update_safety_lock.py`, the hash-locked safety
  tests, `tests/legacy/*.txt`, `requirements.txt` and `frozen_inputs.json`.
  `test_count_floor.txt` changed only through `make raise-test-floor` (44415 to 44421).
- Mutations, each made locally, run, then reverted; none committed:
  - M-doc: I added a dead link, the deleted path `crc/cli/vm_ops.py`, the unmatched
    placeholder `tests/helpers_nope_<cloud>.py` (all in `.cursor/rules/20-core.mdc`), a
    missing `docs/` path in `docs/refactor/bugs/pending/README.md` and a missing path in the
    `BUGS.md` footer. ARCH-011 reported all five, each with `REMEDIATION:`.
  - Mutations of the checker in `doc_links.py`, each caught by its self-tests:
    - M1, archive no longer skipped: 2 tests failed.
    - M2, globs always count as a match: 1 failed.
    - M3, row exemption applied to every file: 1 failed.
    - M4, scan no longer recursive: 5 failed.
    - M5, `:line` and `::test` suffixes no longer stripped: 1 failed.
- Sizes: `doc_links.py` 113 lines, `test_arch011_doc_links.py` 163, `bug_refs.py` 56,
  `frozen_inputs.py` 54. The longest function is 18 lines. `AGENTS.md` is 112 lines, within
  ARCH-010's limit of 120. ARCH-002 (Public API sync) is green.
- Changed files: all are owned by this lane, plus the floor file (changed by
  `make raise-test-floor`) and this review file.
- `make check` after `pip install -r requirements.txt -r requirements-dev.txt`: green.
  44421 passed (pytest 54.6 s); `ARCH-008: 44421 tests, floor 44421`; 75 s wall time (cold
  mypy cache). `make check-fast`: 14063 passed, 25 s wall time. Both match the timings
  stated in `AGENTS.md`.

### Owner action

`tests/archtest/test_count.py` is outside this lane. Proposed diff:

```diff
-    "Tests may only be added or strengthened (AGENTS.md hard rule 1). A lower count means a "
+    "Tests may only be added or strengthened (AGENTS.md rule 3). A lower count means a "
```

ROUND 2 VERDICT: APPROVE
