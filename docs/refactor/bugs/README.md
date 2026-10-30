# docs/refactor/bugs

## Purpose

Holding area for bug and quirk rows that are not yet in `docs/refactor/BUGS.md`. A change that
finds a bug writes its row in a file under `pending/` so parallel branches never edit
`docs/refactor/BUGS.md` at the same time; the integrator folds the rows into `docs/refactor/BUGS.md` and
assigns the real `BUG-NNN` IDs.

## Public API

- `pending/<topic>.md`: rows of one change, same columns as `docs/refactor/BUGS.md`, ID column `NEW` (deleted when folded into `docs/refactor/BUGS.md`).

## Safety Invariants

- Location rule: a new row cites the current `file:line` in `crc/`, verified with `git grep -n`;
  an old row of `docs/refactor/BUGS.md` keeps its main location (the `Location on main` column).
- A pending row never fixes anything: a fix is its own change with a test and a `docs/refactor/BUGS.md` status.

## Extension Rules

1. One file per change in `pending/`; do not edit another change's file.
2. Once the rows are in `docs/refactor/BUGS.md`, the integrator deletes the pending file.
