## Coding summary

Lane W2-ENFORCE: prose rules turned into mechanical checks. No CLI behaviour change.

**Fast feedback**
- `requirements-dev.txt`: `pytest-xdist==3.8.0`.
- `make check` = `lint` + `pytest -q -n auto` + ARCH-008 floor check. New targets: `make lint` (ruff, mypy, lint-imports), `make check-fast` (lint + `pytest -n auto` without `tests/test_cli_main_parity.py`, `tests/test_cli_crc_parity.py`, `tests/test_cli_args_parity.py`), `make test-file F=tests/test_x.py`, `make raise-test-floor`.
- Runtime (4 cores): `make check` 2m58s before, about 1m01s after. `make check-fast` about 21s (9368 tests).
- xdist broke collection on two tests with process-dependent parametrize ids (object address in `tests/test_gcp_disk_fix.py`, set order and object address in `tests/test_cli_args_parity.py`). Fixed with stable ids only; no test logic changed, counts unchanged. `-n auto` is kept everywhere.

**Checks (all in `tests/archtest/`, documented in its README)**

| Rule | What | Module |
|------|------|--------|
| ARCH-003 (extended) | Per-package bans: `crc/core` no wall clock, `print`, `sys.exit`; `crc/cli` no `re`; `crc/notify` no `re`, env, `sys.exit` | `bans.py` (`PACKAGE_BANS`) |
| ARCH-005 (extended) | `check_plan_readonly_siblings` follows calls into sibling modules; old scan untouched; passes on current code | `readonly_plan.py` |
| ARCH-006 | No `async`/`await`/`asyncio`/`pydantic`/`httpx`/`tenacity`/`threading` in `crc/` and `crc.py` | `banned_constructs.py` |
| ARCH-007 | SHA-256 manifest of `tests/legacy/*.txt` and `requirements.txt` | `frozen_inputs.py`, `frozen_inputs.json`, owner-only `update_frozen_inputs.py` |
| ARCH-008 | Test-count floor, run by `make check` after pytest | `test_count.py`, `test_count_floor.txt` |
| ARCH-009 | Every `BUG-NNN` in `crc/**`, `tests/**` exists in `docs/refactor/BUGS.md` | `bug_refs.py` |

Each has a real check plus tmp_path self-tests that prove it fires and does not fire.

**`crc/core/age.py` split (284 to 120 lines)**: `age_spec.py` (AgeSpec, AGE_KEYS, KEEP_AGE, `require_positive_age`) and `age_tags.py` (custom age tag lookup and parsing). `age.py` re-exports every name (with `__all__`), so `crc.core.age.<name>`, `crc.core import age as core_age` and `crc.core` re-exports and mock targets on `core_age` keep working. No public name was added, so the ARCH-002 symbol sync is unchanged.

**MAX_AGE env read left in `crc/core/age.py`**: `is_old` reads `MAX_AGE` at call time. A setter that `crc/cli/main.py` calls would not keep env-only callers working (the many tests and callers that only set the env var), and the env read would still have to live in `crc/core` as the fallback. Moving it needs a read outside `crc/core` that core cannot import (layering). Not done; the `crc/core` env ban is listed under Wave B checks (no allowlist entry).

**Test count**: 43010 before (cea0a8a) and 43068 after (+58), floor file set to 43068.

**Other edits needed by the new checks**: `tests/test_log_fix_*.py` wrote the deliberately unknown ID `BUG-999` as a literal; it is now built as `"BUG-" + "999"` so ARCH-009 does not flag it (same value). `tests/archtest/test_arch003_bans.py::test_arch003_selftest_skips_legacy_and_other_packages` used `crc/core` as its "other package"; it now uses `crc/service.py` and `crc/other` because `crc/core` is now scanned.

**Owner actions**
1. `frozen_inputs.json` was generated once by me with `update_frozen_inputs.py` from the tree at cea0a8a (the files are unmodified in git); verify the hashes against `main` if you want. After that, only the owner runs the script.
2. `frozen_inputs.json` and `test_count_floor.txt` sit under the `/tests/archtest/` CODEOWNERS entry already. The docs lane should mention ARCH-006..009 in `AGENTS.md` ("Rule IDs ARCH-001..005") and `ARCHITECTURE.md`; not touched here. Run `make raise-test-floor` at the end of each wave.
3. `.github/workflows/ci.yml` needed no change (it runs `make check`, and `pip install -r requirements-dev.txt` brings xdist).

## Wave B checks

- **ARCH-003 `env` ban for `crc/core`**: `crc/core/age.py` `_apply_max_age_override` (`os.getenv("MAX_AGE")`). Needs `MAX_AGE` to be handed to core by the CLI boundary (setter or parameter threaded through `is_old` callers) and the env-only tests updated; touches every cloud caller of `core_age.is_old`.
- **ARCH-003 `clock`/`print` in `crc/notify` and `crc/cli`**: left legitimate today (`crc/notify/slack.py:44` clock, `print` in `slack.py`, `influx.py:89`; `crc/cli/notify.py:59` clock). Candidates to ban once injected clocks and the logger are used.
- **ARCH-003 `re` in `crc/core`** is intentionally allowed (`crc/core/policy.py:12`).
- ARCH-005 sibling scan found nothing on the current code; no pending item. It does not follow methods of imported classes or calls through instances of sibling classes; a later check could.
- ARCH-006 does not forbid `concurrent.futures`; none is used in `crc/` today.

## New bugs

None found.

## Round 1 (Argon)

Reviewed `git diff cea0a8a..HEAD` against the W2-ENFORCE task and Wave W2 contract.

### Findings

- **BLOCKING (fixed):** `tests/archtest/readonly_plan.py` (`find_plan_mutations_in_siblings` / `_scan_siblings`) only followed direct sibling calls from `plan()` and 1-hop `self.<method>()` calls, missing sibling calls made from same-module helper functions (such as `list_matches()` in `crc/aws/spot_instance_requests/cleaner.py` or `_decide()` in `crc/aws/spot_instance_requests/requests.py`), multi-hop `self.<method>()` chains, and plain `import pkg.sibling` without `as`. Fixed by traversing `_local_functions` (all transitively reachable same-module functions and `self`/`cls` methods) at each module hop and supporting unaliased `import pkg.sibling` calls, with two new `tmp_path` self-tests (`via-local-func`, `import-plain`). Still passes cleanly on the repository.
- **NIT (fixed):**
  - `tests/archtest/bug_refs.py`: widened `_REF` from `\bBUG-(\d{3,})\b` to `\bBUG-(\d+)\b` so 1- or 2-digit bug-ID typos (e.g. `BUG-04`) are also caught.
  - `tests/archtest/test_count.py`: removed unused `_SUMMARY` regex.
  - Raised `tests/archtest/test_count_floor.txt` from `43068` to `43070` via `make raise-test-floor` for the 2 added ARCH-005 self-tests.

### Verification

1. **Checks enforce rules, fire in self-tests, and provide usable `REMEDIATION`:**
   - **ARCH-003 (`tests/archtest/bans.py`):** extended via `PACKAGE_BANS` to `crc/core` (`clock`, `print`, `sys.exit`), `crc/cli` (`re`), and `crc/notify` (`re`, `env`, `sys.exit`), keeping `re` in `crc/core/policy.py` and legitimate boundary constructs in `crc/cli` and `crc/notify`. `crc/core` `env` ban is deferred to `## Wave B checks` (no allowlist entry added; `APPROVED_ALLOWLIST` remains pinned to the 2 owner-approved `_base.py` entries).
   - **ARCH-005 (`tests/archtest/readonly_plan.py`):** existing `check_plan_readonly` is untouched; `check_plan_readonly_siblings` follows relative and package-local sibling imports (`from .x import f`, `from . import x`, `from pkg.x import f`, `import pkg.x [as x]`) transitively across local helpers, `self`/`cls` methods, and sibling chains, and passes on the repository.
   - **ARCH-006 (`tests/archtest/banned_constructs.py`):** bans `async def`, `await`, `async for`, `async with`, and imports of `asyncio`, `pydantic`, `httpx`, `tenacity`, and `threading` across `crc/` and `crc.py` (including legacy files).
   - **ARCH-007 (`tests/archtest/frozen_inputs.py`, `frozen_inputs.json`, `update_frozen_inputs.py`):** verifies SHA-256 hashes of all 31 `tests/legacy/*.txt` files and `requirements.txt`; self-tests cover modified, added, and deleted files; remediation explicitly forbids agents from running `update_frozen_inputs.py` or editing `frozen_inputs.json`.
   - **ARCH-008 (`tests/archtest/test_count.py`, `test_count_floor.txt`):** `make check` runs `python -m tests.archtest.test_count check` after `pytest`; `make raise-test-floor` only raises the floor (`43070`).
   - **ARCH-009 (`tests/archtest/bug_refs.py`):** scans all `.py` and `.md` files under `crc/**` and `tests/**` for `BUG-NNN` references and verifies each has a row in `docs/refactor/BUGS.md`.
   - All rules are documented in the `tests/archtest/README.md` table and pass `ARCH-002`.
2. **No existing check or test weakened; no hash-locked file or oracle edited:**
   - Verified `safety_lock.json`, `update_safety_lock.py`, the 5 hash-locked test files, and `tests/legacy/*.txt` are untouched.
   - The edits in `tests/test_cli_args_parity.py` and `tests/test_gcp_disk_fix.py` only stabilize parametrize IDs (`set` ordering and `object()` memory addresses) for `pytest-xdist` collection determinism, and `tests/test_log_fix_*.py` construct the synthetic unknown `"BUG-" + "999"` string so `ARCH-009` does not flag it.
3. **`crc/core/age.py` split and `MAX_AGE`:**
   - Split `crc/core/age.py` (284 -> 120 lines) into `crc/core/age_spec.py` (55 lines) and `crc/core/age_tags.py` (145 lines); all three are well under 250 lines.
   - `crc/core/age.py` re-exports all public symbols (`__all__`), preserving `from crc.core import age as core_age`, `from crc.core.age import ...`, `crc.core` re-exports, `crc.service.Service` delegation, and `core_age` mock targets. `MAX_AGE` remains read at call time in `crc/core/age.py` with identical behaviour and is listed under `## Wave B checks`.
4. **Fast feedback & runtimes:**
   - `requirements-dev.txt` pins `pytest-xdist==3.8.0`.
   - `Makefile` defines `make check`, `make check-fast`, `make lint`, `make test-file F=...`, and `make raise-test-floor`.
   - Runtimes on this VM: `make check-fast` runs 9,368 tests in **20.3s** total (19.3s pytest, under the 30s target); `make check` runs all 43,070 tests + lint + ARCH-008 in **1m00.8s** (down from ~2m58s sequential).

### Gates

Ran `pip install -r requirements.txt -r requirements-dev.txt && make check`:
- `ruff check .`: All checks passed!
- `mypy`: Success: no issues found in 262 source files
- `lint-imports`: Contracts: 5 kept, 0 broken
- `pytest -q -n auto`: **43070 passed in 53.92s** (43010 at `cea0a8a`, +60)
- `python -m tests.archtest.test_count check`: `ARCH-008: 43070 tests, floor 43070`

VERDICT: APPROVE


## Round 2 (Opus)

Reviewed `git diff cea0a8a..HEAD` (coder commit plus round 1 fixes) independently.

### Round 1 fixes

- ARCH-005 sibling scan now walks same-module helpers and multi-hop `self`/`cls` chains at
  every hop and handles unaliased `import pkg.sibling`; self-tests `via-local-func` and
  `import-plain` cover it. Confirmed.
- ARCH-009 `_REF` widened to `\bBUG-(\d+)\b`: correct, but nothing proved it. **Fixed:** added
  `test_arch009_selftest_fires_on_short_id_typo` (`BUG-1`, `BUG-04`), floor raised to 43072
  with `make raise-test-floor`.
- Unused `_SUMMARY` removal in `test_count.py`: fine.

### Findings

1. **Checks enforce their rules.** ARCH-003 `PACKAGE_BANS` filters per package and keeps the
   allowlist untouched; ARCH-006 catches `async def`/`await`/`async for`/`async with` and
   plain, dotted and `from` imports of the banned roots (relative imports are not mistaken for
   them); ARCH-007 reports changed, added and missing files; ARCH-008 counts node ids from
   `--collect-only -q` (matches pytest's own count: 43070 collected = 43070 passed) and
   `raise_floor` never lowers; ARCH-009 scans `.py`/`.md` under `crc/` and `tests/` (the
   `SKIP_DIRS - {"tests"} & parts` expression is `(SKIP_DIRS - {"tests"}) & parts` by
   precedence, so it is correct). Every rule has firing and non-firing `tmp_path` self-tests and
   a REMEDIATION that fixes the root cause and forbids weakening the check or running the
   owner-only updater.
2. **Nothing weakened, no locked file touched.** `safety_lock.json`, `update_safety_lock.py`,
   the 5 locked test files (`test_aws_disk.py`, `test_aws_spot_instance_requests.py`,
   `test_azu_spot_vm.py`, `test_gcp_spot_vm.py`, `test_oci_spot_vm.py`), `tests/legacy/*` and
   `requirements.txt` have no diff. The ARCH-003 "other packages" self-test swapped `crc/core`
   (now scanned) for `crc/service.py` and `crc/other/mod.py`: the same thing is still asserted.
   The edits to `test_cli_args_parity.py`/`test_gcp_disk_fix.py` change only parametrize ids
   (same values, same count) and `test_log_fix_*.py` builds the same `"BUG-999"` string.
3. **age.py split changes no behaviour.** An AST comparison of every function in the old
   `crc/core/age.py` with the ones in `age.py`/`age_spec.py`/`age_tags.py` shows all 13 are
   identical. `age.py` re-exports every public name; the private helpers that moved
   (`_normalize_age_values`, `_checked_retention_age`, `_find_tag_value`) are referenced
   nowhere else; no test patches a `crc.core.age.*` string or a `core_age` attribute.
   Line counts: 120 / 55 / 145. `MAX_AGE` is still read at call time in `is_old`; leaving it
   (crc/core env ban under Wave B checks, no allowlist entry) follows the task's fallback.
4. **Not brittle.** All new checks read the repository only and write only under `tmp_path`;
   no test depends on order (the archtest suite passes alone and under `-n auto`). ARCH-008
   runs outside pytest, so `-n auto` cannot change its count.
5. **Minor, not blocking (noted for Wave B):** ARCH-006 does not see dynamic imports
   (`importlib.import_module("httpx")`, `__import__`) or `concurrent.futures`; ARCH-005
   siblings do not follow methods of sibling classes (already listed by the coder).

### New bugs

None found.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt`, then:

- `make check` (before my edit): ruff clean, mypy 262 files clean, lint-imports 5 kept,
  **43070 passed in 52.6s**, ARCH-008 43070 / 43070; total **1m10.6s** wall clock (4 cores).
- `make check-fast`: **9370 passed in 18.6s**, total **19.4s** with lint (under 30s).
- `make test-file F=tests/archtest/test_arch009_bug_refs.py`: 10 passed; without `F` it
  prints the usage line and exits 2.
- After my edit: `make check` exit 0: ruff, mypy, lint-imports clean, **43072 passed in 51.2s**
  (43010 at `cea0a8a`, +62), ARCH-008 43072 / 43072, total **57.5s**.

ROUND 2 VERDICT: APPROVE
