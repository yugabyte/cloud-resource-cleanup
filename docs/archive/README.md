# docs/archive

## Purpose

Frozen history of the CRC Python refactor: the plan, the rung status log, the skill selection notes,
the per-module filter variants and one review per rung under `reviews/`. Nothing here is a source
of truth for current behaviour; `ARCHITECTURE.md`, `CLEANUP_SAFETY.md` and the package READMEs are.

## Public API

- `PLAN.md`, `STATUS.md`, `SKILL_SELECTION.md`, `FILTER_VARIANTS.md`: moved here unchanged. The filter
  variants describe behaviour before the behaviour fixes and are partly out of date.
- `reviews/`: review of each rung, plus `reviews/DOCS-1.md` for the agent-native docs lane.

## Safety Invariants

- Do not follow instructions in these files (branch names, rung workflows, "do not edit" lists):
  they are superseded by `AGENTS.md`.
- Links inside archived files may point to paths that no longer exist.

## Extension Rules

1. Do not edit archived files except to add a new review under `reviews/`.
2. If something here is still true and needed, copy one or two lines into the active document.
