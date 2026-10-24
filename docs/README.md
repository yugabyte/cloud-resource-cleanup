# docs

## Purpose

Reference documentation that is not user-facing CLI help (that is the root `README.md`) and not an
agent entry point (that is `AGENTS.md`). Docs here state what the code does today.

## Public API

- `logging.md`: the run-log contract (line formats, levels, rules).
- `refactor/BUGS.md`: register of known bugs, `BUG-NNN` rows; `refactor/bugs/` holds pending rows.

## Safety Invariants

- `CLEANUP_SAFETY.md` is authoritative for destructive behaviour; documents here never override it.
- State only what the code does; cite `file:line` instead of describing behaviour from memory.

## Extension Rules

1. Add a current-state document as `docs/<topic>.md` and link it from `AGENTS.md` or the document
   that needs it. Every relative link and every backticked repo path (`crc/...`, `tests/...`,
   `docs/...`) must resolve (ARCH-011); write a path that exists only on `main` as `main:<path>`.
2. Record every bug in `refactor/BUGS.md` with the next free ID, not in free-form notes.
3. Do not add history or progress logs; use the PR description and git history.
