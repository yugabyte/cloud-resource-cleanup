# tests/archtest

Mechanical architecture checks for the CRC refactor. Every check reads the legacy
ratchet from `pyproject.toml` (ruff `extend-exclude`, via `tomllib`) and applies only to
non-legacy files. Each failure message starts with the rule ID and ends with a
`REMEDIATION:` paragraph that says how to fix the cause properly.

## Purpose

Keep the refactor honest without a human reviewer: the ratchet only shrinks, packages
are documented, cloud modules stay free of ad hoc regex, clocks, env reads and exits,
files stay small, `plan()` stays read-only, and the tests that lock safety invariants
cannot be silently weakened. Run with `make check` (part of `pytest -q`).

## Public API

This package exposes rule checks, not a library. Each check is a pure function taking
a repository root, so its self-test can run it on a `tmp_path` project.

| Rule ID | Module | Enforces |
|---------|--------|----------|
| ARCH-001 | `ratchet.py` | Ruff `extend-exclude` is a subset of `legacy_baseline.txt`; the mypy override modules match it file for file; no directory or glob entries |
| ARCH-002 | `readmes.py` | Every directory that contains files directly or in subdirectories (except `README_EXEMPT_DIRS`: the root, `.cursor` and `.cursor/skills`, and the trees in `README_EXCLUDED_DIRS`: `.git`, `.github`, `.claude`, caches including `.import_linter_cache`, virtualenvs, `.tgrep`, build output) has `README.md` with `## Purpose`, `## Public API`, `## Safety Invariants`, `## Extension Rules`. Each `crc/` package dir with a non-legacy `.py` also keeps the symbol sync: backticked names under Public API equal `__all__` plus public top-level defs. Legacy-only packages (every `.py` in the ratchet) need the sections but no symbol sync until their refactor rung |
| ARCH-003 | `bans.py` | No `re`, wall-clock reads, `os.environ`/`os.getenv`, `print`, `sys.exit` in non-legacy `crc/aws`, `crc/azu`, `crc/gcp`, `crc/oci` (`ARCH003_ALLOWLIST` holds explicit exceptions; `time.monotonic` and `time.sleep` are allowed) |
| ARCH-004 | `sizes.py` | Non-legacy, non-test files have at most 400 lines |
| ARCH-005 | `readonly_plan.py` | `plan` methods (and same-module functions or `self` methods they call) make no delete, terminate, cancel, stop, deallocate, release, schedule_key_deletion, begin_*, or tag-write call |
| SAF-LOCK | `safety_lock.py` | Tests listed in `safety_lock.json` exist, are not skipped or xfailed, and keep their normalised-AST SHA-256; every `[SAF-NNN]` tag in `CLEANUP_SAFETY.md` is locked or a listed gap |

Self-tests live next to each check (`test_arch00N_*.py`, `test_safety_lock.py`). They
build bad snippets under `tmp_path` and assert the check fires.

## Safety Invariants

- `legacy_baseline.txt`, `safety_lock.json` and `update_safety_lock.py` are guardrail
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

30 of 46 IDs are locked. Gaps (no existing test covers them; not invented here):
SAF-001, SAF-002, SAF-004, SAF-005, SAF-006, SAF-009, SAF-010, SAF-014, SAF-016,
SAF-021, SAF-022, SAF-023, SAF-024, SAF-034, SAF-036, SAF-037.
