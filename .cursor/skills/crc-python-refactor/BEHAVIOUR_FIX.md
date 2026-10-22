# BEHAVIOUR_FIX.md (behaviour-fix rungs)

The refactor rungs are finished: every module under `crc/` was rewritten with no
behaviour change and is pinned to `main` by parity tests. A behaviour-fix rung changes
behaviour on purpose to fix rows of `docs/refactor/BUGS.md`. Everything in `SKILL.md`
still applies; this file adds the rules for changing behaviour without losing the
parity net.

## 1. Scope

- One rung fixes the `BUG-NNN` rows its entry in `docs/refactor/PLAN.md` ("Behaviour-fix
  rungs") names, and nothing else. A bug you notice on the way is recorded in
  `BUGS.md`, not fixed.
- CLI flags, environment variables and JSON-style flag values keep working
  (`AGENTS.md` rule 6). A fix that needs a new flag or a changed default says so in
  its PLAN.md entry; otherwise it is out of scope.
- A fix moves behaviour towards `CLEANUP_SAFETY.md` (fail closed). It never removes
  a safety check, never deletes more than before for inputs the bug does not cover,
  and never weakens a test outside the rules below.

## 2. Find the code

`BUGS.md` cites `main` file:line, which no longer matches the refactored tree (for
example `crc.py:1429` now lives in `crc/cli/scan.py`). To find the current code:

1. Grep the bug ID: `BUG-NNN` appears in the Safety Invariants of the package or
   subpackage `README.md` that owns it, in `docs/refactor/reviews/<rung>.md` of the
   rung that found it, and often in a quirk check of the module's parity test.
2. Open the module's parity test (`tests/test_<cloud>_<kind>_parity.py`,
   `tests/test_cli_*_parity.py`, `tests/test_<cloud>_base_parity.py`, ...); its oracle
   is the `tests/legacy/*.txt` file it loads.

## 3. Parity tests: diverge per case, never globally

The oracle files in `tests/legacy/*.txt` are frozen copies of `main`. Never edit them.

- A parity case whose inputs trigger the bug is marked with the bug ID (for example a
  `diverges="BUG-NNN"` field on the case, or a separate table of diverging cases).
  For a marked case the test asserts both that the new code differs from the oracle
  and the exact new outcome (calls, results, logs, errors). This strengthens the
  test: it pins the fix and still proves the oracle shows the old behaviour.
- Every unmarked case still asserts equality with the oracle. Do not loosen a
  comparison (for example by dropping logs or call order) for all cases.
- A quirk check that asserted the buggy behaviour for `BUG-NNN` is replaced by a check
  that asserts the fixed behaviour and names the bug. Only the checks of the bugs
  in scope change.
- Add focused unit tests for the fixed path, including the fail-closed and error
  paths, as `SKILL.md` section 5 says.

Reviewers: a divergence that is marked with an in-scope bug ID, asserts the new
outcome and leaves every other case equal is not a weakened test. An unmarked
divergence, a global loosening or an edited oracle is.

## 4. Locked safety tests

`tests/archtest/safety_lock.json` pins the hashes of the safety tests tied to `SAF-NNN`
IDs (today `tests/test_aws_disk.py`, `tests/test_aws_spot_instance_requests.py`,
`tests/test_azu_spot_vm.py`, `tests/test_gcp_spot_vm.py`, `tests/test_oci_spot_vm.py`).
If a fix needs one of them changed, or needs a new `SAF-NNN` invariant in
`CLEANUP_SAFETY.md`:

1. Stop before editing the locked file.
2. Write the exact proposed change and why under `## Owner action` in
   `docs/refactor/reviews/<rung>.md`.
3. The owner edits the test and runs `tests/archtest/update_safety_lock.py`. Agents
   never run it and never edit `safety_lock.json`.

## 5. Docs and records

- Set the `Status` of each fixed row in `docs/refactor/BUGS.md` to `fixed in <rung>` (as `BUGS.md` says);
  keep the row.
- Update the Safety Invariants of the README that owned the bug and, if destructive
  behaviour changed, `CLEANUP_SAFETY.md` (CODEOWNERS file: the PLAN.md entry must
  allow it).
- Write the rung to `docs/refactor/STATUS.md` and the review to
  `docs/refactor/reviews/<rung>.md`.
- `make check` green; the test count stays the same or grows.
