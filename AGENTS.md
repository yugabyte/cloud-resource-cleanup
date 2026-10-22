# AGENTS.md

## What this repo is

`cloud-resource-cleanup` (CRC) is a synchronous Python CLI that Jenkins runs to
stop and delete leftover test resources on AWS, Azure, GCP and OCI. Entry point
is `crc.py`; cloud code lives in `crc/aws`, `crc/azu`, `crc/gcp`, `crc/oci`.
It deletes real resources, so safety beats elegance.

This branch (`refactor/python-agentic`) holds the in-place refactor to Python 3.11
with mechanically enforced guardrails. The refactor rungs are done: every module
behaves as on `main` and is pinned to it by parity tests against frozen oracles in
`tests/legacy/`. Work now happens in behaviour-fix rungs, which fix rows of
`docs/refactor/BUGS.md` one named set at a time.

## Map of docs

| File | Read it for |
|------|-------------|
| `docs/refactor/PLAN.md` | Goal, target architecture, rungs (R0a ... Final), behaviour-fix rungs |
| `ARCHITECTURE.md` | Dependency rules, Cleaner contract, legacy ratchet, how to add a cleaner |
| `CLEANUP_SAFETY.md` | Destructive-cleanup invariants. Authoritative for safety |
| `docs/refactor/STATUS.md` | Which rung is done, open issues |
| `docs/refactor/BUGS.md` | Register of every known bug and quirk with main file:line |
| `tests/archtest/README.md` | Rule IDs ARCH-001..005 and SAF-LOCK, how failures are fixed |
| `docs/refactor/SKILL_SELECTION.md` | Why the project skill is shaped as it is |
| `README.md` | User-facing CLI documentation |

## Commands

```
make check     # ruff check ., mypy (crc/ and tests/), lint-imports, pytest -q
```

CI (`.github/workflows/ci.yml`) runs the same `make check` on Python 3.11. A failing
`ARCH-xxx` or `SAF-LOCK` test ends with a `REMEDIATION:` paragraph: follow it, do not
edit `tests/archtest/` or its baseline and lock files to make it pass.

Setup: `pip install -r requirements.txt -r requirements-dev.txt` on Python 3.11.
`make check` must be green before every commit. A rung must also keep the
`pytest -q` test count the same or higher, never lower.

## Hard rules

1. Never weaken, skip, loosen or delete a safety test. Tests may only be added
   or strengthened.
2. Never edit guardrail files listed in `.github/CODEOWNERS` (this file,
   `ARCHITECTURE.md`, `CLEANUP_SAFETY.md`, `pyproject.toml`, `Makefile`,
   `.github/`, `.cursor/rules/`, `.cursor/skills/`, `tests/archtest/`,
   `docs/refactor/PLAN.md`) unless the task explicitly asks for it.
3. Never call real cloud APIs and never use real credentials. Tests mock every
   SDK client. Use `--dry_run` reasoning, not live runs.
4. Use the project skill `.cursor/skills/crc-python-refactor/` (`SKILL.md` to
   implement, `REVIEW_CHECKLIST.md` to review, `BEHAVIOUR_FIX.md` when a rung
   fixes bugs).
5. No behaviour change, except in a behaviour-fix rung named in
   `docs/refactor/PLAN.md`, and then only for the `BUG-NNN` rows it lists. Never
   edit a `tests/legacy/*.txt` oracle; mark each diverging parity case with its
   bug ID as `BEHAVIOUR_FIX.md` says.
6. Keep every CLI flag, environment variable and JSON-style flag value
   (for example `--age '{"days": 1}'`) working exactly as today. Jenkins jobs
   in `cloud-resource-cleanup-triggers` depend on it.
7. Do not add dependencies to `requirements.txt`. No `async`, no `pydantic`.
8. Legacy ratchet: the exemption lists in `pyproject.toml` may only shrink.
   Never add a module to them. Today they hold only the five hash-locked safety
   test files; every module under `crc/` and `crc.py` must pass ruff (mypy skips
   `crc.py` because of its name clash with the `crc` package; its code lives in `crc/cli/`).
9. Stay inside the current rung. Do not start another rung.
10. Fail closed: when age, tags or telemetry are uncertain, keep the resource.
11. Record every bug or quirk you find in docs/refactor/BUGS.md with its main-branch file:line; do not fix it outside a behaviour-fix rung that names it.
12. If a fix needs a hash-locked safety test changed (`tests/archtest/safety_lock.json`),
    stop and hand the owner the proposed change; never run `update_safety_lock.py`.

## Adding a cleaner

Follow the step-by-step guide in `ARCHITECTURE.md` ("Add a resource cleaner").
In short: implement `plan()` (read-only) and `apply()` (mutating) in
`crc/<cloud>/<resource>.py`, register it with the decorator, add mocked tests,
update the package README and `CLEANUP_SAFETY.md` if behaviour is destructive.

## Rungs workflow

One rung per run: read `docs/refactor/STATUS.md`, do only that rung, update
STATUS.md, run `make check`, commit, push to `refactor/python-agentic`. Never
force-push. Reviews go to `docs/refactor/reviews/<rung>.md`.
