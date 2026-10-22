# docs/refactor/bugs

## Purpose

Holding area for bugs and quirks found by parallel refactor lanes. Each lane writes its own
file under `pending/` so lanes never edit `docs/refactor/BUGS.md` at the same time; the
integrator folds the rows into `BUGS.md` and assigns the real `BUG-NNN` IDs.

## Public API

- `pending/<rung>.md`: rows of one rung, same columns as `docs/refactor/BUGS.md`, ID column `NEW` (deleted when folded into `BUGS.md`).

## Safety Invariants

- Locations are `file:line` on `main`, verified with `git show main:<path>`.
- A pending row never fixes anything: bugs are fixed only in a behaviour-fix rung.

## Extension Rules

1. One file per rung in `pending/`; do not edit another rung's file.
2. Once the rows are in `docs/refactor/BUGS.md`, the integrator deletes the pending file.
