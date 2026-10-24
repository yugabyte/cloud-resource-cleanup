## Coding summary

Rung FIX-GCP-SPOT (wave FIX-1), branch `fix/gcp-spot`. No production command uses GCP Spot.

- **Fixed BUG-228** (`crc/gcp/spot_vm/instances.py`): an instance with no labels at all is kept
  (skip reason `unlabelled`) when `filter_labels` is empty. The check runs last, after the age
  check, so every other reason, error and log line is unchanged; with an active filter the VM
  is still `filter_mismatch`. It only keeps more.
- **Fixed BUG-242, custom age part** (`crc/gcp/spot_vm/disks.py`, `cleaner.py`, `adapter.py`): a
  leftover disk that carries `custom_age_tag_key` is kept when it is younger than that age
  (disk `creationTimestamp`), when the label is invalid, or when the timestamp is missing or
  unreadable (skip reasons `disk_too_young`, `disk_invalid_age_tag`, `disk_age_unknown`, DEBUG
  line per disk). A disk without the label, or a run without `custom_age_tag_key`, behaves as
  before (same SDK calls). The clock is injected (`RunContext.now` or the adapter clock).
- **Not changed, BUG-229**: see "Owner decision".
- **BUG-242 rest, left**: the disk's own `filter_labels` and `users` are still not read. The task
  asked for the custom age tag only; reading `users` / `filter_labels` is a larger decision
  (a disk with no labels would then be kept whenever a filter is set).
- Files: `crc/gcp/spot_vm/{instances,disks,cleaner,adapter}.py`, `crc/gcp/spot_vm/README.md`,
  `tests/test_gcp_spot_vm_fix.py` (new, 30 tests), `tests/test_gcp_spot_instances_parity.py`
  (12 cases marked in `DIVERGES`, 4 quirk checks moved to `FIXED_QUIRKS`, BUG-242 parity test),
  `tests/test_gcp_spot_instances.py` (the `instance()` fixture now has a label by default, so the
  tests that delete a VM do not use an unlabelled one). `tests/test_gcp_spot_vm.py` untouched.
- Tests: `pytest -q` 42444 passed at 182466d, 42474 passed after (`make check` green).
- Owner actions: none. `CLEANUP_SAFETY.md` is not edited (see "Owner action").

Note: the task names `crc/gcp/spot_vm.py`; the code lives in the package `crc/gcp/spot_vm/`.

## Owner decision

**BUG-229 not fixed.** The task asks that a delete whose operation has no name, errors or does
not reach DONE is recorded as failed, not deleted. That is already what the code does:
`SpotVMCleaner._delete_and_wait` returns False, the VM is not added to the result, an error
line is logged and `logger.current().fail(...)` records the VM (`FAILED ... failed=1`). Only the
second half of the row remains: the leftover `autoDelete=false` disks are skipped because the VM
may in fact be gone. Deleting them anyway would delete more than today, which the wave rules and
`CLEANUP_SAFETY.md` SAF-039 (disks only after the instance-delete operation completed) forbid.
A safe version needs the owner to decide: after a failed wait, re-read the instance
(`instances.get`, a new SDK call) and delete the disks only when it is gone. I pinned the
current outcome with tests (`test_bug_229_*`: no name, empty name, operation error, never done;
VM not recorded, disks not read, failure recorded).

## Owner action

`CLEANUP_SAFETY.md` (CODEOWNERS) is not edited in this rung. Proposed updates to `CLEANUP_SAFETY.md` lines 178-183:

- `[SAF-043]`: replace `[SAF-043] GCP/OCI match their regular VM label/tag helpers; empty filter still needs the age gate. GCP/OCI are opt-in via explicit --cloud (not --cloud all).` with:
  `[SAF-043] GCP/OCI match their regular VM label/tag helpers; GCP Spot also skips unlabelled Spot VMs when --filter_tags is empty (BUG-228), while OCI with an empty filter still relies on the age gate. GCP/OCI are opt-in via explicit --cloud (not --cloud all).`
- `[SAF-044]`: replace `[SAF-044] Leftover disks/volumes are skipped when their own exception/notags match (and Azure disks outside --resource_group).` with:
  `[SAF-044] Leftover disks/volumes are skipped when their own exception/notags match, when a GCP leftover disk's own custom age label is not yet reached, invalid, or has an unreadable creationTimestamp (BUG-242), and for Azure disks outside --resource_group.`

## New bugs

- `crc/gcp/spot_vm/disks.py` `remove_leftover_disk`: a disk delete response without `name`
  counts as deleted (parity case `disk-delete-no-op-name-counted`); exists on main: yes
  (`crc/gcp/spot_vm.py`, disk delete after `_delete_disk`). Not fixed. Already recorded as
  `BUG-237` in `docs/refactor/BUGS.md` (not a new bug).

## Round 1 (Argon)

### Findings

- **NIT** (`crc/gcp/spot_vm/README.md:37,41`, fixed in review): updated `Public API` entries for `reject_reason()` (added `unlabelled` skip reason) and `disk_ok_to_delete()` (added custom age label protection) to match the `Safety Invariants` section.
- **NIT** (`CLEANUP_SAFETY.md:178-183`, CODEOWNERS — not edited): added exact proposed text for `[SAF-043]` (BUG-228) and `[SAF-044]` (BUG-242) under `## Owner action` above.

### Verification

1. **Bug fixes & regression tests (`182466d..HEAD`)**:
   - **BUG-228**: `crc/gcp/spot_vm/instances.py` (`_label_and_age_reason`) returns `"unlabelled"` when `not settings.filter_labels and not _labels(instance)` after the age check, preserving all prior skip reasons (`state`, `not_spot`, `managed_or_gke`, `exception_tag`, `filter_mismatch`, `too_young`) and error paths while keeping unlabelled Spot VMs when `filter_labels` is empty. Locked test `tests/test_gcp_spot_vm.py::GcpSpotDetectTests::test_empty_filter_allows_unlabeled` (which tests `_has_matching_filter_label` directly) remains untouched and passing. Covered by 7 unit tests in `tests/test_gcp_spot_vm_fix.py` and 12 diverging parity cases + 4 `FIXED_QUIRKS` entries in `tests/test_gcp_spot_instances_parity.py`.
   - **BUG-229**: Left unfixed and documented under `## Owner decision`. On `182466d`, `SpotVMCleaner._delete_and_wait` already records a delete whose operation has no `name`, empty `name`, `error`, or poll timeout as failed (`logger.current().fail(...)`, not added to `succeeded` / `get_deleted`) and skips leftover disks per `SAF-039`. Deleting leftover disks after an incomplete instance-delete operation wait would either violate `SAF-039` / the direction rule or require a new `instances.get` SDK call (owner decision). Pinned by 5 tests in `tests/test_gcp_spot_vm_fix.py`.
   - **BUG-242**: `crc/gcp/spot_vm/disks.py` (`_custom_age_keeps`, `disk_ok_to_delete`) checks `core_age.validated_retention_age` on the leftover disk's own labels when `custom_age_tag_key` is set, keeping the disk (fail closed) on `disk_invalid_age_tag`, `disk_age_unknown` (missing/unparseable `creationTimestamp`), or `disk_too_young` (`core_age.is_old` against disk `creationTimestamp` using the injected clock). Disks without the custom age label or runs without `custom_age_tag_key` make identical SDK calls and decisions to `182466d`. The remaining parts of `BUG-242` (`filter_labels` and `users` on the disk) were outside the task prompt and are noted in the review. Covered by 18 unit tests in `tests/test_gcp_spot_vm_fix.py` and `test_bug_242_leftover_disk_custom_age_main_deletes_new_code_keeps` in `tests/test_gcp_spot_instances_parity.py`.
2. **Direction rule & parity**:
   - Fix only keeps more (unlabelled VMs when `filter_labels` is empty; leftover disks protected by their own custom age tag).
   - `tests/legacy/gcp_spot_vm.txt` is untouched. Every unmarked parity case asserts exact equality with the oracle; diverging cases are explicitly keyed in `DIVERGES` with `"BUG-228"`.
3. **Production commands**: None reach GCP Spot; no CLI flags or dependencies changed.
4. **Fail-closed paths**: Invalid `project_id` or `age` raises `ValueError` before any SDK call; invalid custom age tags or unreadable disk `creationTimestamp` skip the resource before any delete call.
5. **Protected files**: `tests/test_gcp_spot_vm.py`, `tests/archtest/safety_lock.json`, `docs/refactor/PLAN.md`, `docs/refactor/STATUS.md`, `docs/refactor/BUGS.md`, and `CLEANUP_SAFETY.md` are untouched.

### Gates

- `make check` (`ruff check`, `ruff format --check`, `mypy`, `lint-imports`, `pytest`): **passed** (`42474 passed`).

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff 182466d..HEAD`.

### Round 1 check

Round 1 findings hold. Its README `Public API` fix (`reject_reason()` lists `unlabelled`,
`disk_ok_to_delete()` mentions the custom age label) and its `## Owner action` text for SAF-043 /
SAF-044 are correct and still match the code.

### Findings

- **NIT** (`docs/refactor/reviews/FIX-GCP-SPOT.md`, "New bugs", fixed in this review): the
  disk-delete-without-`name` quirk is not new. It is already `BUG-237` in `docs/refactor/BUGS.md`, and
  the entry now says so. It is out of scope here: `BUG-229` is about the instance delete.
- **NIT** (`crc/gcp/spot_vm/cleaner.py:188`, `:164`, `crc/gcp/spot_vm/disks.py:113`, not changed):
  `apply_instance`, `preview_instance` and `disk_ok_to_delete` default `clock` to
  `datetime.datetime.now`. Every production caller passes `RunContext.now` or the adapter clock,
  so nothing reads the wall clock today. A required argument would be stricter, but that would
  break existing direct callers in tests, which is outside this rung.
- No BLOCKING findings.

### Checks

1. **Bugs fixed, with tests that fail on 182466d.** I copied `tests/test_gcp_spot_vm_fix.py` onto a
   `182466d` worktree: 16 failed, 13 passed. Every BUG-228 and BUG-242 fix test is among the
   16 failures. The 13 that pass on both are the BUG-229 pins and the checks that other inputs
   behave as before.
   - BUG-228: `reject_reason` returns `unlabelled` only when `filter_labels` is empty and the VM
     has no labels, and this check runs after the age check. All earlier reasons and error paths
     (for example `labels-not-a-mapping`, or a timestamp that cannot be parsed) are reached first
     and are unchanged. Both run paths, `SpotVM.delete()` and the registered Cleaner, go through
     `reject_reason`.
   - BUG-242 (custom age part): `_custom_age_keeps` reads the disk's own label with
     `validated_retention_age`. When the label is set it uses the same semantics as the
     instance: `effective_age(retention, age)` equals `retention`, and `core_age.is_old` with
     `MAX_AGE`. An invalid label, or a missing or unreadable `creationTimestamp`, keeps the disk
     (fail closed). The dry-run plan, apply and the adapter all receive the injected clock.
   - BUG-229: I agree with the owner decision. `_delete_and_wait` already records a missing
     `name`, an empty `name`, an operation error, a poll error or a timeout as failed
     (`logger.current().fail`), never as deleted, and keeps the leftover disks. The only thing
     left would be deleting those disks anyway, which deletes more than today (SAF-039).
2. **Direction rule.** Both fixes only add skips; no new SDK mutation is added. A disk without
   the custom age label, or a run without `custom_age_tag_key` (`validated_retention_age` returns
   None when the key is empty), makes the same calls as before. The parity oracles
   (`tests/legacy/`) are untouched. The 12 cases that now differ are keyed one by one in
   `DIVERGES`, all to BUG-228, and each asserts the exact new `deleted` list, the
   `instances.delete` calls, the disks, stdout and the logs. Every other case still asserts
   equality with the oracle. The 4 quirk checks moved to `FIXED_QUIRKS` assert both the old and
   the new outcome. A separate test pins the BUG-242 difference (young disk: main deletes it, the
   new code keeps it; old disk: same calls and logs as main). The `instance()` fixture in
   `tests/test_gcp_spot_instances.py` now gets a default label, so the tests that expect a delete
   do not use an unlabelled VM. Tests that pass explicit labels are unchanged.
3. **Production commands.** None reach GCP Spot. No CLI flag or dependency changed.
4. **Fail-closed paths.** The new keeps happen in `plan` / `evaluate` and in `disk_ok_to_delete`,
   before `delete_instance` / `delete_disk`. There is no new raise.
5. **Protected files.** `git diff 182466d..HEAD --stat` is empty for `tests/test_gcp_spot_vm.py`,
   `tests/archtest/`, `tests/legacy/`, `docs/refactor/{PLAN,STATUS,BUGS}.md`, `CLEANUP_SAFETY.md`,
   `.cursor/`, `AGENTS.md` and `ARCHITECTURE.md`.
6. **README (ARCH-002).** The Safety Invariants and Public API sections of
   `crc/gcp/spot_vm/README.md` describe both fixes and the BUG-229 status.
7. **Unfixed bugs.** BUG-229 is under `## Owner decision`. The rest of BUG-242 (the disk's own
   `filter_labels` / `users`) is explained in the coding summary, because the task asked only for
   the custom age tag.

### Gates

- `pip install -r requirements.txt -r requirements-dev.txt`: ok.
- `make check` (ruff check, ruff format --check, mypy, lint-imports, pytest): **passed**,
  `42474 passed`.

ROUND 2 VERDICT: APPROVE
