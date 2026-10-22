# .cursor/skills/crc-python-refactor

## Purpose

Project skill for the CRC Python refactor. `SKILL.md` holds the implementer rules (protocols, DTOs,
registry decorators, strict typing, pure policy, error handling per `CLEANUP_SAFETY.md`, offline
tests). `REVIEW_CHECKLIST.md` is the reviewer checklist. `BEHAVIOUR_FIX.md` adds the rules for
behaviour-fix rungs (fixing `docs/refactor/BUGS.md` rows against the frozen parity oracles). `.claude/skills/crc-python-refactor` is a
symlink to this directory. Why it is shaped this way: `docs/refactor/SKILL_SELECTION.md`.

## Public API

- `SKILL.md`: read first when implementing a rung.
- `REVIEW_CHECKLIST.md`: used by the review run; output goes to `docs/refactor/reviews/<rung>.md`.
- `BEHAVIOUR_FIX.md`: read with `SKILL.md` when a rung fixes bugs (per-case parity divergence, locked
  safety tests, BUGS.md status).

## Safety Invariants

- Guardrail path in `.github/CODEOWNERS` (`.cursor/skills/`); do not edit unless a task asks.
- The skill never overrides `CLEANUP_SAFETY.md`: fail closed, preserve destructive call order, no
  live cloud calls, behaviour changes only for the bugs a behaviour-fix rung names.

## Extension Rules

1. Change the skill only in a task that asks for it, and keep it consistent with `AGENTS.md` and
   `ARCHITECTURE.md`.
2. Keep both files short; link to the owning document instead of copying it.
