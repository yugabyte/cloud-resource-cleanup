# docs

## Purpose

Documentation that is not user-facing CLI help (that lives in the root `README.md`). Today it holds
only `docs/refactor/`, the plan, status, bug register and reviews of the Python refactor.

## Public API

- `docs/refactor/`: plan, status, catalogues, bug register and reviews (see `docs/refactor/README.md`).
- Root `AGENTS.md`, `ARCHITECTURE.md` and `CLEANUP_SAFETY.md` stay in the repository root.

## Safety Invariants

- `CLEANUP_SAFETY.md` is authoritative for destructive behaviour; documents here never override it.
- `docs/refactor/PLAN.md` is a guardrail file in `.github/CODEOWNERS`; do not edit it unless a task asks.
- State only what the code does; cite `file:line` instead of describing behaviour from memory.

## Extension Rules

1. Add refactor documents under `docs/refactor/` and list them in that README.
2. Any new directory under `docs/` needs a `README.md` with the four sections (ARCH-002).
3. Record every bug or quirk in `docs/refactor/BUGS.md`, not in free-form notes.
