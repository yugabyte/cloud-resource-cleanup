# Fix a bug

Companion of [SKILL.md](SKILL.md). One bug, one PR, named by its ID.

1. Record it. Find the row in `docs/refactor/BUGS.md` or add one with the next free `BUG-NNN`
   (ARCH-009 rejects an ID in code or tests that has no row). Give the risk; never reuse or delete
   an ID. Location rule: a new row cites the current `file:line` in
   `crc/`; an old row keeps its main location. Pending rows from other agents live in
   `docs/refactor/bugs/pending/`.
2. Locate the code. An old row's location column cites `main` lines that have moved or are gone.
   Grep the ID
   (`rg "BUG-NNN"`): it appears in package READMEs, tests and the register. Read the owning
   package `README.md`.
3. Reproduce offline: a test with fakes that fails today. Include the inputs that must keep their
   current behaviour (the unaffected cases).
4. Fix toward `CLEANUP_SAFETY.md`: fail closed, never delete more than before for inputs the bug
   does not cover, keep destructive order. A fix that changes a destructive path updates
   `CLEANUP_SAFETY.md` in the same PR (add a `[SAF-NNN]` tag only with the next unused number).
5. Frozen parity tests: `tests/legacy/*.txt` oracles are copies of the old tool and are never
   edited. When a parity test fails because the behaviour is now deliberately different, mark that
   one case as diverging with the bug ID, assert the exact new outcome (calls, results, logs,
   errors), and keep every other case equal to the oracle. Do not loosen a comparison for all
   cases.
6. Locked safety tests (`tests/archtest/safety_lock.json`): do not edit them. If the fix needs it,
   stop and put the proposed test change and reason in the PR description.
7. Remove the bug's `TODO(BUG-NNN)` comment in `crc/` (ARCH-012 fails if a closed row keeps one),
   then update the row: status `fixed in <PR>`; keep the row. The `todo-to-issue` workflow closes
   the issue after the merge to `main`. A new critical or high row needs its comment before the
   check passes, a two-line comment directly above the function where the behaviour lives:
   `# TODO(BUG-NNN): <title under 100 chars>` and `#  labels: bug, risk-<critical|high>, crc`.
   Fix any README or doc line the bug made false.
8. `make check`, `make raise-test-floor` when tests were added. PR title: `fix(BUG-NNN): ...`.
   Other bugs found on the way are new rows, not part of this PR.
