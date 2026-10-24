# R0b review

Scope reviewed: `git diff cb21c84..23b1614` (commit `23b1614`, R0b architecture
tests, SAF IDs, pinned dev dependencies and CI workflow) against
`docs/refactor/PLAN.md`, `AGENTS.md`, `ARCHITECTURE.md`, `CLEANUP_SAFETY.md`
and `.cursor/skills/crc-python-refactor/REVIEW_CHECKLIST.md`.

## Scope and behaviour

- `CLEANUP_SAFETY.md`: stripping `[SAF-001]`..`[SAF-046]` leaves every
  existing bullet, table cell and rule word-for-word identical to `cb21c84`
  (only the two-sentence header note explaining `[SAF-NNN]` and
  `tests/archtest/safety_lock.json` was added).
- Zero changes to `crc.py`, `crc/**`, `requirements.txt` or pre-existing
  `tests/*.py` files.
- `tests/archtest/` implements `ARCH-001` (`ratchet.py`), `ARCH-002`
  (`readmes.py`), `ARCH-003` (`bans.py`), `ARCH-004` (`sizes.py`), `ARCH-005`
  (`readonly_plan.py`) and `SAF-LOCK` (`safety_lock.py`). Every check reads the
  legacy ratchet from `pyproject.toml`, applies only to non-legacy files,
  formats failures with `<RULE>: ...` and a `REMEDIATION:` paragraph, and has
  `tmp_path` self-tests proving it fires on violations and passes on clean
  inputs. None of the checks are vacuous.
- `requirements-dev.txt` pins `pytest==9.1.1`, `ruff==0.17.1`, `mypy==2.4.0`,
  `import-linter==2.15` and `types-python-dateutil==2.9.0.20260807`.
- `.github/workflows/ci.yml` runs `pip install -r requirements.txt -r requirements-dev.txt`
  and `make check` on `ubuntu-latest` with Python `"3.11"`.

## BLOCKING (fixed in this run)

1. Wrong test mapping under `SAF-034` (`tests/archtest/safety_lock.json`,
   `tests/archtest/README.md`, `docs/refactor/STATUS.md`): `SAF-034` ("Sweep
   isolates per-volume errors") was mapped to
   `SpotCleanupOrderTests::test_sweep_tagged_leftover_volumes` and
   `SpotCleanupOrderTests::test_sweep_skips_other_owner_and_in_use`. Neither
   test injects a per-volume error in `_sweep_tagged_leftover_volumes` or
   verifies that an error on one volume does not abort sweeping the remaining
   volumes (`test_sweep_tagged_leftover_volumes` exercises orphan rediscovery
   in the sweep under `SAF-030`/`SAF-031`; `test_sweep_skips_other_owner_and_in_use`
   exercises filter/status gating in the sweep under `SAF-031`). Fixed:
   removed the wrong `SAF-034` lock entries and moved `SAF-034` to `gaps` (no
   existing test covers per-volume error isolation in the sweep; no test
   invented).
2. Wrong test mapping under `SAF-015` (`tests/archtest/safety_lock.json`):
   `SAF-015` ("Incomplete CloudWatch answers (non-`Complete`, missing pages,
   pagination truncation, auth failures on metric lookup) keep the volume
   rather than delete it") included
   `AwsDiskDetachAgeTests::test_zero_detach_window_deletes_nothing`, which
   mocks `_age_to_timedelta` to `timedelta(0)` and returns `[]` before
   CloudWatch is ever called. Fixed: removed
   `test_zero_detach_window_deletes_nothing` from `SAF-015`; `SAF-015` remains
   locked by `test_incomplete_status_keeps_the_volume` and
   `test_cloudwatch_failure_drops_the_batch`.
3. Wrong test mapping under `SAF-032` (`tests/archtest/safety_lock.json`):
   `SAF-032` ("If cancel/terminate fails after tagging, the leftover marker and
   every `crc_leftover_tag_*` key are cleared") included
   `SpotCleanupOrderTests::test_create_tags_timeout_invalidates_failing_volume`,
   which tests `create_tags` timing out during initial volume tagging before
   cancel or terminate is reached (`SAF-028`), not a cancel/terminate failure
   after tagging. Fixed: removed `test_create_tags_timeout_invalidates_failing_volume`
   from `SAF-032`; `SAF-032` remains locked by
   `test_terminate_failure_clears_leftover_tag`,
   `test_cancel_botocore_error_invalidates_marker` and
   `test_terminate_failure_sentinel_when_delete_tags_denied`.

## NIT (left)

- `tests/archtest/readmes.py` (`required_names`): collects `declared_all(tree)`
  only when `module.name == "__init__.py"`, plus `public_defs(tree)` (public
  top-level functions and classes) from every non-legacy module in the package.
  If a non-`__init__.py` module exports a public constant or `TypeAlias` via
  its own `__all__` without re-exporting it in `__init__.py`, `required_names`
  would not see it unless re-exported in `__init__.__all__`. Acceptable since
  package public APIs are re-exported in `__init__.py`.
- `tests/archtest/readonly_plan.py` (`_scan`): follows `self.` / `cls.` method
  calls within the same class and top-level function calls within the same
  module, but not mutating calls delegated to a separate helper module. Enough
  for cleaner classes where SDK client calls live on the cleaner or same-module
  helpers.

## Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` (`crc`, `tests`) | Success: no issues found in 56 source files |
| `lint-imports` | 1 kept, 0 broken ("Cloud packages are independent") |
| `pytest -q` | 190 passed (110 existing + 80 archtest) |

Test count: 110 on `cb21c84`, 190 after R0b and after the review fixes.

VERDICT: APPROVE
