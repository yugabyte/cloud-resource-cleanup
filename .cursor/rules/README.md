# .cursor/rules

## Purpose

Cursor rules (`.mdc`) that load repository guidance for agents. They are guardrail files in
`.github/CODEOWNERS`.

- `00-map.mdc`: always on; points to `AGENTS.md`, the plan, architecture and safety documents.
- `10-cloud-packages.mdc`: for `crc/aws`, `crc/azu`, `crc/gcp`, `crc/oci`.
- `20-core.mdc`: for `crc/core` and `crc/notify`.
- `30-safety.mdc`: for `CLEANUP_SAFETY.md` and `tests/`.

## Public API

- Each `.mdc` file has front matter (`description`, `globs`, `alwaysApply`) and a short rule list.

## Safety Invariants

- Rules must agree with `AGENTS.md`, `ARCHITECTURE.md` and `CLEANUP_SAFETY.md`; those win on conflict.
- Do not edit these files unless a task explicitly asks for it (`AGENTS.md`, hard rule 2).
- Rules never relax a safety test, the legacy ratchet or the no-real-cloud-calls rule.

## Extension Rules

1. Add a numbered `NN-name.mdc` with front matter and at most one screen of rules.
2. Keep rules short and point to the owning document instead of copying it.
