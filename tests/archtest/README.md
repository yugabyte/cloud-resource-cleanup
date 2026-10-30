# tests/archtest

Mechanical architecture and documentation checks for CRC. Every check reads the legacy
ratchet from `pyproject.toml` (ruff `extend-exclude`, via `tomllib`) and applies only to
non-legacy files; the ratchet is empty, so every file is checked. Each failure message starts with the rule ID and ends with a
`REMEDIATION:` paragraph that says how to fix the cause properly.

## Purpose

Keep the repository honest without a human reviewer: the ratchet only shrinks, packages
are documented, the agent entry documents stay short and their links resolve, cloud modules stay free of ad hoc regex, clocks, env reads and exits,
files stay small, `plan()` stays read-only, and the tests that lock safety invariants
cannot be silently weakened. Run with `make check` (part of `pytest -q`).

## Public API

This package exposes rule checks, not a library. Each check is a pure function taking
a repository root, so its self-test can run it on a `tmp_path` project.

| Rule ID | Module | Enforces |
|---------|--------|----------|
| ARCH-001 | `ratchet.py` | Ruff `extend-exclude` is a subset of `legacy_baseline.txt` (empty: no file may enter the ratchet); the mypy override modules match it file for file; no directory or glob entries |
| ARCH-002 | `readmes.py` | Every directory that contains Python code (a `.py` file directly or in a subdirectory; except the root and the trees in `README_EXCLUDED_DIRS`: `.git`, `.github`, `.claude`, caches including `.import_linter_cache`, virtualenvs, `.tgrep`, build output) has `README.md` with `## Purpose`, `## Public API`, `## Safety Invariants`, `## Extension Rules`. Directories without Python code (`docs/`, `.cursor/`, `tests/legacy/`) need none. Each `crc/` package dir with a non-legacy `.py` also keeps the symbol sync: backticked names under Public API equal `__all__` plus public top-level defs. Legacy-only packages (every `.py` in the ratchet) need the sections but no symbol sync |
| ARCH-003 | `bans.py` | Per package (`PACKAGE_BANS`): `crc/aws`, `crc/azu`, `crc/gcp`, `crc/oci` ban `re`, wall-clock reads and references (`clock`, `clock_ref`: take `now` or a clock parameter wired to `RunContext.now`), `os.environ`/`os.getenv`, `print`, `sys.exit`; `crc/core` bans wall-clock reads (`clock`, `clock_ref`), `os.environ`/`os.getenv` (`env`), `print`, `sys.exit` (`MAX_AGE` reaches `crc/core/age.py` through `set_max_age_source`, installed by `crc/cli/context.py`); `crc/cli` bans `re` and `clock_ref` (it may read env, print, call the clock, exit); `crc/notify` bans `re`, env reads and `sys.exit`. `ARCH003_ALLOWLIST` holds the explicit owner exceptions; `time.monotonic` and `time.sleep` are allowed |
| ARCH-004 | `sizes.py` | Non-legacy, non-test files have at most 250 lines and functions at most 60 |
| ARCH-005 | `readonly_plan.py` | `plan` methods (and same-module functions or `self` methods they call) make no delete, terminate, cancel, stop, deallocate, release, schedule_key_deletion, begin_*, or tag-write call; `check_plan_readonly_siblings` also follows calls into sibling modules of the same package (`from .x import f`, `from . import x`, `import pkg.x as x`, transitively) |
| ARCH-006 | `banned_constructs.py` | No `async def`/`await`/`async for`/`async with`, and no import of `asyncio`, `pydantic`, `httpx`, `tenacity` or `threading` (thread singletons) anywhere in `crc/` and `crc.py`, legacy files included |
| ARCH-007 | `frozen_inputs.py` | SHA-256 of every `tests/legacy/*.txt`, `requirements.txt` and `constraints.txt` equals `frozen_inputs.json`. Only the owner runs `update_frozen_inputs.py`; agents must not |
| ARCH-008 | `test_count.py` | `pytest --collect-only` count is at least `test_count_floor.txt`; `make check` runs it after pytest; `make raise-test-floor` raises the floor, never lowers it |
| ARCH-009 | `bug_refs.py` | Every `BUG-NNN` in `crc/**`, `tests/**` (`.py`, `.md`) exists as a row in `docs/refactor/BUGS.md` |
| ARCH-010 | `doc_budget.py` | Doc budget: `AGENTS.md` at most 120 lines, each `.cursor/rules/*.mdc` at most 40, each `.cursor/skills/*/SKILL.md` at most 150 |
| ARCH-011 | `doc_links.py` | In every live markdown file (all `.md` and `.mdc` files, recursive, except symlinks and cache directories): every relative link resolves to an existing file or directory (external links, `#anchors` and code are ignored), and every backticked repo-relative path under `crc/`, `tests/`, `docs/`, `.cursor/` or `.github/` that looks like a file or ends in `/` exists (a `:line`, `::test` or `#anchor` suffix is dropped; a glob or `<placeholder>` must match at least one path). A backticked bare filename (no slash, ending `.py`, `.md`, `.txt`, `.json` or `.yml`) must exist next to the document or at the repository root; a name is exempt only through `BARE_NAME_ALLOWLIST` in `doc_links.py`, with a written reason. The rows of the `docs/refactor/BUGS.md` table are exempt from the path and bare-filename checks: they are written against `main` and never edited. Write a path that exists only on `main` as `main:<path>` |
| ARCH-012 | `todo_bugs.py` | Every bug-tracking `TODO(BUG-NNN)` comment in `crc/` (`.py`, `.md`) names a row of `docs/refactor/BUGS.md` whose status is open or partly fixed; every open or partly fixed critical or high row has exactly one such comment in `crc/`; a `TODO` in `crc/` without a BUG id fails (write the bug row first). Medium and low rows need no comment. `.github/workflows/todo-to-issue.yml` turns each comment into an issue on `main` and closes it when the comment is removed |
| SAF-LOCK | `safety_lock.py` | Tests listed in `safety_lock.json` exist, are not skipped or xfailed, and keep their normalised-AST SHA-256; every `[SAF-NNN]` tag in `CLEANUP_SAFETY.md` is locked or a listed gap |

`frozen_inputs.json`, `test_count_floor.txt` and `update_frozen_inputs.py` are guardrail files
too: never edit them or run the updater to make a check pass; the floor only rises through
`make raise-test-floor`.

Self-tests live next to each check (`test_arch0NN_*.py`, `test_safety_lock.py`). They
build bad snippets under `tmp_path` and assert the check fires.

## Safety Invariants

- `legacy_baseline.txt`, `safety_lock.json`, `frozen_inputs.json` and the `update_*.py` scripts are guardrail
  files in CODEOWNERS. Never edit them to make a check pass.
- Never add a file to the ruff or mypy legacy lists. Refactor it and remove it.
- Never skip, xfail, weaken or delete a locked test. If a locked test must change for a
  deliberate reason, the owner reviews it and runs `update_safety_lock.py` (owner-only).
- Never add bad example files to the repository; self-tests use `tmp_path`.

## Extension Rules

1. New rule: add a pure `check_*(root, legacy) -> list[str]` module, format every
   failure with `_common.violation(rule, problem, remediation)`, add a real test and a
   self-test that proves it fires, and add a row to the table above.
2. Map a new invariant: tag it `[SAF-NNN]` in `CLEANUP_SAFETY.md` (never reuse an ID),
   then the owner adds `--map SAF-NNN <node id>` via `update_safety_lock.py` and removes
   it from `gaps`. Do not invent tests to close a gap.
3. Keep checks typed, ruff- and mypy-clean; these files are not in the legacy ratchet.

## SAF coverage

46 of 46 IDs are locked in `safety_lock.json`; `gaps` is empty. A new `[SAF-NNN]` tag in
`CLEANUP_SAFETY.md` fails `SAF-LOCK` until it is mapped to a test.
