# FIX-OCI-SPOT review

## Coding summary

- Bug fixed: BUG-255. After a preemptible instance is terminated, a leftover block volume is deleted only when it was created at or after the instance's `time_created` and no other instance has it attached. Otherwise it is kept with skip reason `not_owned` (DEBUG log with the cause). The instance is still terminated.
- Bugs left: none of the rung's list. BUG-255's other half (`filter_tags` and `age` are not applied to the volume) is not part of the task text and stays as is.
- Rules for the new check (all fail closed): a volume or instance creation time that is missing, not a `datetime` or not comparable keeps the volume; a failed `list_volume_attachments(compartment_id, volume_id=...)` read keeps the volume (logged as an error and recorded as a failure); an attachment that is not `DETACHED` (including `DETACHING`) and whose `instance_id` differs from the terminated instance, or is missing, keeps the volume. The terminated instance's own attachment is ignored. Exception tags and notags are still checked first (reason `leftover_exception_tag`), so no new read happens for a volume they protect.
- Dry run: `plan()` runs the same read-only check, so the reported volume list matches what `apply()` deletes.
- Direction rule: only extra reads were added (one `get_volume` already existed, plus one `list_volume_attachments` per tag-passing volume). Nothing is deleted that was not deleted before. No new flag, dependency or log level; no production command uses OCI spot.
- Files changed:
  - `crc/oci/spot_vm/leftovers.py` (`VolumeOwner`, `volume_owned`, owner argument of `volume_passes_tag_check` / `checked_volumes`), `adapter.py`, `cleaner.py` (build the owner from the instance), `client.py` (`SdkVolume.time_created`), `README.md` (Public API, Safety Invariants).
  - `tests/test_oci_spot_vm_fix.py` (new, 18 tests), `tests/test_oci_spot_instances_parity.py` (BUG-255 diverging table with 8 cases, 2 new equal cases, ownership reads filtered by `_without_ownership_reads` for the unmarked cases), `tests/test_oci_spot_instances.py` and `tests/test_oci_logger.py` (fakes only: volume `time_created` and by-volume attachment listing; one expected call order gained the new read).
  - `crc/oci/spot_vm.py` named in the task is now the package `crc/oci/spot_vm/`; the old import path still works.
- Locked test `tests/test_oci_spot_vm.py` is untouched and green: it mocks `_volume_ok_to_delete` and `_delete_volume`, and `_volume_ok_to_delete(client, volume_id)` keeps working with two arguments (the owner is an optional third one).
- Parity: every unmarked case still asserts equality with the oracle (calls, order, logs, volumes, stdout). The only filter is the new ownership read and its pager call, removed from the new calls before comparison. Cases that diverge are `test_bug_255_volume_not_owned_is_kept` (BUG-255): volume older than the instance, creation time missing, attached to another instance, attachment read fails; each in live and dry-run mode and through both the adapter and the cleaner, asserting the oracle still deletes `v1`.
- Test count: 42444 before (`pytest -q --co` at 182466d), 42474 after (`make check` green).
- Owner actions: none.

## Owner action

None. No destructive invariant text needs to change for the listed IDs (SAF-044 still holds); optionally add to `CLEANUP_SAFETY.md` under SAF-044: "A leftover volume is deleted only when it was created at or after the instance's creation time and no other instance has it attached; anything uncertain keeps it."

## Owner decision

None.

## New bugs

- `crc/oci/spot_vm/leftovers.py:attached_volume_ids`: the leftover list is taken before terminate, so a volume that another instance attaches between the read and the delete is only caught by the new ownership read, which is a narrow race that cannot be closed without a lock. Exists on main: yes. Not fixed.
- The ownership read lists attachments only in the run's compartment (`compartment_id`); a volume attached to an instance of another compartment is not seen. OCI volumes and their attachments normally share the compartment of the instance. Exists on main: yes (it was never checked). Not fixed.

## Round 1 (Argon)

### Findings

1. **BUG-255 fix verified**: `crc/oci/spot_vm/leftovers.py` (`VolumeOwner`, `volume_owned`, `volume_passes_tag_check`, `checked_volumes`), `crc/oci/spot_vm/adapter.py`, and `crc/oci/spot_vm/cleaner.py` verify that after a preemptible instance is selected/terminated and a leftover block volume passes the tag check (`exception_tags` / `notags`), the volume is deleted only when its `time_created` is a `datetime` greater than or equal to the instance's `time_created` and `list_volume_attachments(compartment_id, volume_id=...)` shows no non-`DETACHED` attachment to any other instance. Otherwise the volume is kept with skip reason `not_owned` and a `DEBUG` log line (and `_logger.record_failure` + `ERROR` log when `list_volume_attachments` raises).
2. **Protocol completeness (fixed in review)**: Added `instance_id` property to `SdkAttachment` and updated `ComputeClient.list_volume_attachments` signature in `crc/oci/spot_vm/client.py` to accept optional `instance_id` and `volume_id` parameters matching the new ownership lookup, and asserted `_logger.record_failure` on attachment read failure in `tests/test_oci_spot_vm_fix.py`.
3. **Direction rule & parity**: Only keep-more conditions and read-only calls (`list_volume_attachments` per tag-passing volume) were added; nothing is deleted, stopped, or released that was kept before. `tests/legacy/oci_spot_vm.txt` is untouched. All 69 unmarked parity cases in `tests/test_oci_spot_instances_parity.py` assert equality with the oracle (filtering only the new ownership read and its pager call), and the 8 `BUG_255` diverging cases (4 scenarios x live/dry-run across both `SpotVM` and `SpotVmCleaner`) assert that the oracle deletes `v1` while the new code keeps it.
4. **Production commands**: None reach OCI spot; no CLI flags, dependencies, or public signatures were broken (`SpotVM._volume_ok_to_delete` keeps `owner` optional so existing two-argument callers continue to work).
5. **Fail-closed paths**: Missing, non-`datetime`, or incomparable `time_created`, volume older than instance, failed `list_volume_attachments`, and non-`DETACHED` attachments to another or unknown instance all fail closed before `delete_volume` is called.
6. **Protected files & docs**: `tests/test_oci_spot_vm.py`, `tests/archtest/safety_lock.json`, `docs/refactor/PLAN.md`, `docs/refactor/STATUS.md`, `docs/refactor/BUGS.md`, and `CLEANUP_SAFETY.md` are untouched. `crc/oci/spot_vm/README.md` Public API and Safety Invariants are updated (`ARCH-002` passes).

### Gates

- `ruff format --check .`: passed
- `ruff check .`: passed
- `mypy`: passed (`Success: no issues found in 147 source files`)
- `pytest` (including `tests/archtest`): 42474 passed (up from 42444 on `182466d`)

VERDICT: APPROVE

## Round 2 (Opus)

### Round 1 fixes checked

- The `SdkAttachment.instance_id` property and the widened `ComputeClient.list_volume_attachments(compartment_id, instance_id="", volume_id="")` Protocol in `crc/oci/spot_vm/client.py` are correct. They match the real SDK call `list_volume_attachments(compartment_id, **kwargs)` (filters `instance_id` / `volume_id`), which `list_all` reaches through `oci.pagination.list_call_get_all_results(call, compartment_id=..., volume_id=...)`. The `record_failure` assertion in `tests/test_oci_spot_vm_fix.py::test_failed_attachment_read_keeps_the_volume` is correct.
- I agree with Round 1 findings 1 to 6.

### Findings

1. BUG-255 is fixed for the inputs in its row: a volume older than the instance, a volume attached to another instance (multi-attach), and uncertain cases (missing, non-`datetime` or naive creation time; failed attachment read; attachment without `instance_id`). In each of these the volume is kept with skip `not_owned`. The ownership read sits behind the tag check in both paths (adapter `SpotVM._process` at `crc/oci/spot_vm/adapter.py:231-240` and cleaner `apply` / dry-run `_checked` at `crc/oci/spot_vm/cleaner.py:229-262`). The cleaner uses `candidate.launch_time`, which is set to `instance.time_created` without conversion (`cleaner.py:198`).
2. Fails on 182466d, passes now: I copied the HEAD `tests/test_oci_spot_instances_parity.py` onto a 182466d worktree and ran `-k bug_255`. All 8 `test_bug_255_volume_not_owned_is_kept` cases failed (the old code deletes `v1`), and they pass on HEAD. `tests/test_oci_spot_vm_fix.py` only fails at collection on 182466d (it imports `leftovers.VolumeOwner`), so the parity cases are the behavioural proof.
3. Direction rule: the only new branches return `False` (keep), and they run before `delete_leftover`. Instance terminate, wait and order are unchanged. `tests/legacy/oci_spot_vm.txt` is untouched. `_without_ownership_reads` drops only the new by-volume read (`list_volume_attachments_of_volume`) and the pager call just before it. Every other call, log, volume and stdout value is still compared with the oracle, and the mutating-call comparison in `test_cleaner_protocol_matches_main` is unfiltered. The parity fake defaults the volume creation time to `LATE` (after every instance), so unmarked cases stay owned and equal. The two new equal cases (volume created with the instance; another instance's `DETACHED` attachment) pin the non-covered inputs. The extra read per tag-passing volume is the minimum needed to tell a covered input from an uncovered one. It is a read only, and a volume older than the instance skips it.
4. Production commands: none reach OCI spot. No CLI flag, dependency, async or pydantic was added. `SpotVM._volume_ok_to_delete` keeps its two-argument form, so the locked `tests/test_oci_spot_vm.py` stays green.
5. Fail-closed: no `ValueError` path was added. Every uncertain input keeps the volume, and nothing here mutates before the check.
6. Protected files: `git diff 182466d..HEAD` touches none of `tests/test_oci_spot_vm.py`, `tests/archtest/**`, `tests/legacy/**`, `docs/refactor/PLAN.md`, `STATUS.md`, `BUGS.md`, `CLEANUP_SAFETY.md`, `AGENTS.md`, `ARCHITECTURE.md` or `.cursor/**`.
7. README: Public API (`VolumeOwner`, `volume_owned`, the owner argument) and Safety Invariants (the BUG-255 rule, compartment scope, keep-on-uncertain, the remaining `filter_tags` / `age` gap) are updated. ARCH-002 passes.
8. Owner decision: nothing is left unfixed from the rung's list. The `filter_tags` / `age` half of the BUGS.md row text is a description of today's behaviour, not part of the task's fix, and the README says so explicitly.
9. NIT (`crc/oci/spot_vm/leftovers.py:140-143`): a failed attachment read calls both `record_failure` and `record_skip("not_owned")`, so one volume counts as `failed=1` and `skipped=1` in `RESULT`. The existing `get_volume` failure path (`leftovers.py:166-169`) records only the failure. This does not affect the exit code (`crc/core/logger.py` has no exit-code logic) and it does not change what is deleted. Proposed (optional): in the `except` branch, log the DEBUG reason line and return `False` without `record_skip`, then update the parity reason assertion to match. I left it as is to keep this round minimal.
10. NIT (not blocking, owner-only file): the optional SAF-044 text proposed under "## Owner action" above is accurate. Proposed `CLEANUP_SAFETY.md` text under SAF-044: "A leftover OCI spot volume is deleted only when it was created at or after the instance's `time_created` and no other instance in the run's compartment has it attached (any state but `DETACHED`); a missing time or a failed attachment read keeps it."

### Gates

- `pip install -r requirements.txt -r requirements-dev.txt`: ok
- `make check`: passed (`ruff check`: all checks passed; `mypy`: no issues found in 229 source files; import-linter: 5 kept, 0 broken; `pytest -q`: 42474 passed)
- Test count: 42474 (42444 at 182466d, +30)

ROUND 2 VERDICT: APPROVE
