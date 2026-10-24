## Coding summary

Rung FIX-OCI-VM (wave FIX-1, behaviour fix). Branch `fix/oci-vm`.

- **Bugs fixed.**
  - `BUG-158`: `VmCleaner` now skips OKE node-pool and instance-pool members with the guard `crc/oci/spot_vm` uses (`policy.oci_is_managed_or_oke_node` over freeform tags, defined tags and metadata). It runs right after the state check, also with empty `filter_tags`. The run-log skip key is `managed`; `plan().skipped` carries reason `OKE or instance pool member`. Keeps more than before, never deletes or stops more.
  - `BUG-162`: `VmCleaner.decide()` catches an unexpected error while evaluating one instance (missing or naive `time_created`, malformed `MAX_AGE`, anything else), logs it at ERROR (`Error occurred while evaluating instance <name>: <error>`), records it with `record_failure(region, id, error)` and keeps the instance (skip reason `evaluation failed`). The loop continues with the next instance and region; both the `VM` adapter and the registered Cleaner path use `decide()`, so both are covered. Instances already acted on are now reported and the summary is logged.
- **Bugs left.** None of the two. (`BUG-159`, `BUG-160`, `BUG-161`, `BUG-163` to `BUG-171` are out of scope.)
- **Files changed.**
  - `crc/oci/vm/cleaner.py` (guard, `decide()` try/except, skip key)
  - `crc/oci/vm/client.py` (`SdkInstance` Protocol gains `defined_tags` and `metadata`; the real SDK object has both, the guard reads them with `getattr` so fakes without them still work)
  - `crc/oci/vm/README.md` (Safety Invariants, Public API note)
  - `tests/test_oci_vm_parity.py` (BUG-162 cases marked in `DIVERGES`; the five old BUG-162 quirk checks moved to `test_bug_162_oracle_fails_and_new_code_carries_on`; `test_vm_diverges_from_oracle` skips those cases; every other case still equals the oracle)
  - `tests/test_oci_vm_fix.py` (new, 23 tests: BUG-158 freeform/defined/metadata markers, order after state check, empty filter, dry run, adapter, fakes without the attributes; BUG-162 missing/naive time, malformed MAX_AGE, other region continues, ERROR log, run-log failure, adapter)
  - `docs/refactor/reviews/FIX-OCI-VM.md`
- **Locked tests.** `tests/test_oci_spot_vm.py` and the other hash-locked tests are untouched and pass. `tests/legacy/*.txt` untouched.
- **Production commands.** Same CLI flags and selection. Only instances that are OKE or instance pool members, or whose evaluation raised, are now kept instead of acted on or aborting the run. The oci vm commands in the Jenkinsfile keep working.
- **Test count.** `pytest -q`: 42444 at 182466d, 42467 after (`make check` green: ruff, mypy, lint-imports, pytest).
- **Owner actions.** See below.

## Owner action

`CLEANUP_SAFETY.md` (CODEOWNERS, not edited here). Proposed text, extending SAF-045:

> [SAF-045] GCP skips GKE/MIG members; OCI skips OKE / instance-pool members (tags/metadata) in both the OCI Spot VM and the OCI VM cleaner (`crc/oci/vm`, skip reason `managed`).

Optional new invariant (needs a new locked test if the owner wants it hash-locked): "OCI VM: an error while evaluating one instance keeps that instance, is recorded as a failure and does not stop the run."

## New bugs

- `crc/oci/vm/cleaner.py` `_is_old`: a naive `time_created` is not given a timezone as `crc/oci/spot_vm/decide.py:is_old_instance` does, so with BUG-162 fixed such an instance is now kept with a recorded failure instead of being evaluated. Safe (fail closed); the spot VM variant treats it as local time. Exists on main: yes (`crc/oci/vm.py:121` on main raised `TypeError`).
- The `oke` guard is a case-insensitive substring match (`policy._looks_oke`), so any tag or metadata entry containing `oke` (for example a tag value such as `joker`) keeps a VM. Same on `spot_vm` today; fail closed, not fixed.

## Round 1 (Argon)

Scope reviewed: `git diff 182466d..HEAD` (`crc/oci/vm/cleaner.py`, `crc/oci/vm/client.py`, `crc/oci/vm/README.md`, `tests/test_oci_vm_fix.py`, `tests/test_oci_vm_parity.py`, `docs/refactor/reviews/FIX-OCI-VM.md`).

### Findings

1. **Fixed bugs (`BUG-158`, `BUG-162`):**
   - `BUG-158` (`crc/oci/vm/cleaner.py:40,235-240`, `crc/oci/vm/client.py:43-51`): `VmCleaner._skip_reason` calls `policy.oci_is_managed_or_oke_node(instance.freeform_tags, getattr(instance, "defined_tags", None), getattr(instance, "metadata", None) or {})` immediately after the lifecycle state check (including when `filter_tags` is empty), returning `"OKE or instance pool member"` and recording run-log skip key `"managed"`. Verified against `crc/oci/spot_vm/decide.py:63-69,95-113`.
   - `BUG-162` (`crc/oci/vm/cleaner.py:45,176-181`): `VmCleaner.decide` wraps `self._skip_reason(instance, clock)` in `try/except Exception`, logs `Error occurred while evaluating instance <display_name>: <e>` at `ERROR`, calls `_logger.record_failure(region, instance.id, e)`, and returns `Skipped(id=instance.id, reason="evaluation failed", name=instance.display_name)` so both the `VM` adapter and `VmCleaner.plan` keep the instance and continue across remaining instances and regions.
   - `tests/test_oci_vm_fix.py` adds 23 unit tests covering `BUG-158` (freeform key/value, defined tags, metadata, delete and stop, empty `filter_tags`, ordering after state check, dry run, missing optional attributes, `VM` adapter) and `BUG-162` (missing and naive `time_created`, malformed `MAX_AGE`, multi-region continuation, `ERROR` log, `RunLog.fail` recording, `VM` adapter) that fail on `182466d` and pass on `HEAD`.
2. **Direction rule and parity (`tests/test_oci_vm_parity.py`):**
   - Neither fix can delete, stop, or release any instance that was kept before: `BUG-158` only adds a skip branch, and `BUG-162` keeps instances that previously raised an unhandled exception while letting healthy instances finish and be reported.
   - `tests/legacy/oci_vm.txt` is untouched. Only the 5 `BUG-162` cases (`max-age-not-a-dict`, `naive-created-time`, `missing-created-time`, `unexpected-error-in-later-instance-after-action`, `unexpected-error-in-later-region`) are in `DIVERGES`, where `_assert_bug_162_outcome` verifies that the oracle raises and the new code succeeds with the exact expected mutations, results, counts, and `ERROR` logs; all other 70 cases (for both `delete` and `stop`, adapter and Cleaner) assert exact equality with the oracle.
3. **Production commands and fail-closed paths:**
   - All 5 production Jenkins commands for `--cloud oci --resource vm` keep the same CLI flags and selection (except keeping OKE/instance-pool nodes and instances whose evaluation raises). `plan()` remains strictly read-only before any SDK mutation.
4. **Protected files and docs (`ARCH-002`):**
   - `tests/archtest/safety_lock.json`, the 5 hash-locked test files, `docs/refactor/PLAN.md`, `docs/refactor/STATUS.md`, `docs/refactor/BUGS.md`, and `CLEANUP_SAFETY.md` are untouched.
   - `crc/oci/vm/README.md` updates `Public API` (`SdkInstance`) and `Safety Invariants` for `BUG-158` and `BUG-162`.
   - **NIT (protected CODEOWNERS file, do not edit):** `CLEANUP_SAFETY.md:183` currently scopes `[SAF-045]` under leftover/spot disks; owner may update `CLEANUP_SAFETY.md:183` as proposed under `## Owner action` above (`[SAF-045] GCP skips GKE/MIG members; OCI skips OKE / instance-pool members (tags/metadata) in both the OCI Spot VM and the OCI VM cleaner (crc/oci/vm, skip reason managed).`).

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` | Success: no issues found in 229 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | 42467 passed (42444 at `182466d`, +23) |

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff 182466d..HEAD`. I re-checked round 1's findings and then ran each check myself. Round 1 made no code changes, and I made none either; this section is the only edit.

### Findings

1. **The bugs are fixed, and the tests prove it.** I copied `tests/test_oci_vm_fix.py` into a worktree at `182466d`: 21 of its 23 tests fail there and all 23 pass at HEAD. The two that pass on the base are regression guards: `test_bug_158_guard_comes_after_the_state_check` and `test_bug_158_instances_without_defined_tags_or_metadata_still_match`.
   - `BUG-158`: `crc/oci/vm/cleaner.py:235-240` calls the same `policy.oci_is_managed_or_oke_node(freeform_tags, defined_tags, metadata or {})` that `crc/oci/spot_vm/decide.py:63-69` uses, in the same position (right after the state check, before the exception, filter and age checks). It applies even when `filter_tags` is empty, and on both the `VM` adapter path and the registered Cleaner path, because both go through `decide()`.
   - `BUG-162`: `decide()` (`cleaner.py:176-181`) catches the error, logs it at ERROR, calls `record_failure`, and returns `Skipped` with reason `evaluation failed`, so the instance is kept. The adapter (`adapter.py:140-145`) and `plan()` both keep looping, and `_log_summary` runs.
2. **Direction rule and parity.** Oracles in `tests/legacy/` are untouched. Only the five `BUG-162` cases are listed in `DIVERGES` (`tests/test_oci_vm_parity.py:565-574`, under a `BUG-162` comment). Each asserts that the oracle still fails and pins the exact new mutations and the ERROR lines. Every other case keeps exact equality on both the adapter and Cleaner paths. `BUGS = {"BUG-358"}` is unchanged, so no log rule was loosened. `BUG-158` only adds a keep branch.
   - **Correction to round 1, recorded for the owner (NIT, not blocking).** Round 1 says neither fix can act on an instance that was kept before. That is not quite true for `BUG-162`. In cases `unexpected-error-in-later-instance-after-action` (instance `c`, after broken instance `b`) and `unexpected-error-in-later-region` (any healthy instance in `r2` after `b`), the new code terminates or stops instances that main never reached, because main aborted first. This is the intended outcome of the `BUG-162` row ("later instances and regions are not processed") and of the task note ("the loop continues"). Those instances pass every selection gate, and main would have acted on them if the broken instance had not been listed first. The broken instance itself is always kept. No selection rule is widened.
   - **Exit code (NIT, by design).** On main these errors aborted the run with a non-zero exit, so Jenkins went red. Now the run exits 0. The failure shows up in the ERROR line and in the run-log `ERRORS count=` summary (`crc/core/logger.py:160-163`), the same way per-instance terminate and stop failures already did on main. The task asks for exactly this ("recorded (run-log fail) and the loop continues").
3. **Production commands.** All five `--cloud oci --resource vm` Jenkins commands use the same flags as before, and no CLI flag or dependency was added. Their selection changes only by keeping OKE and instance-pool members and instances whose evaluation raises. With `--resource_states '["STOPPED"]'` and the `-d --notags` run, the guard comes before the notags check, so OKE nodes are kept there too.
4. **Fail-closed paths.** None were added. `plan()` makes no mutating call, and the `decide()` catch runs before `act()` for that instance.
5. **Protected files.** `git diff --name-only` touches none of `safety_lock.json`, the five locked tests, `tests/legacy/`, `tests/archtest/`, `.cursor/`, `PLAN.md`, `STATUS.md`, `BUGS.md` or `CLEANUP_SAFETY.md`.
6. **README (ARCH-002).** `crc/oci/vm/README.md` updates Public API (`SdkInstance`) and adds Safety Invariants for both bugs. It also narrows the known-bugs list.
7. **Owner decision.** Not needed; both bugs are fixed. The SAF-045 text under `## Owner action` is correct.
8. **NIT, run-log key mismatch.** The task requires the skip key `managed`, which `crc/oci/vm` now uses. `crc/oci/spot_vm/decide.py:27` records the same guard as `oke_member`, so dashboards that count both cleaners see two keys for one guard. Not fixed (spot_vm is out of scope). Proposal for a later rung: pick one key for both cleaners.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` | Success: no issues found in 229 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | 42467 passed |

ROUND 2 VERDICT: APPROVE
