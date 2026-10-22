# docs/refactor

## Purpose

Plan, progress and findings of the CRC Python refactor on `refactor/python-agentic`. Every refactor rung is done; further work happens in behaviour-fix rungs (see `PLAN.md`).

| File | Content |
|------|---------|
| `PLAN.md` | Goal, target architecture, guardrails, rungs (guardrail file) |
| `STATUS.md` | Rung log with counts and open issues; read it first, update it every rung |
| `BUGS.md` | Register of every bug and quirk, with the `main` file:line, risk and status |
| `FILTER_VARIANTS.md` | How each module filters by tags, regex, state and age (written from `main`, with its `file:line`) |
| `LOGGING.md` | Contract for the LOG behaviour-fix rungs (line formats, levels, parity-test rule) |
| `SKILL_SELECTION.md` | Why the project skill is shaped as it is |
| `reviews/` | One review per rung (`<rung>.md`) |

## Public API

- `STATUS.md`: one row per rung (R0a to R4c, the cloud and CLI lanes GCP-*, AZ-*, AWS-*, OCI-*, CLI-*, the PL-* and ST-* rungs, Final, and the LOG-* rungs).
- `BUGS.md`: `BUG-NNN` IDs, never reused.
- `FILTER_VARIANTS.md`: spec for the functions in `crc/core/policy.py`.
- `LOGGING.md`: spec for `crc/core/logger.py`, `tests/helpers_log_fix.py` and the LOG rungs.

## Safety Invariants

- Bugs are recorded in `BUGS.md`, not fixed, outside a behaviour-fix rung. Rows are never deleted;
  only their status changes.
- Documented quirks must match the code: verify with `git show main:<path>` before writing.
- `PLAN.md` is guarded by `.github/CODEOWNERS`.

## Extension Rules

1. Each rung updates its `STATUS.md` row (summary, counts, open issues) and adds `reviews/<rung>.md`
   after review.
2. Append new bugs to `BUGS.md` with the `main` file:line; leave older lists in `STATUS.md` as they are.
3. Keep documents short and factual; cite files instead of inventing behaviour.
