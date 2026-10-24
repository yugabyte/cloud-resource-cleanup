## Coding summary

Rung PL-CORE: `crc/service.py`, `crc/utils.py`, `crc/__init__.py` and `tests/__init__.py` are strict-clean (ruff and mypy strict), no behaviour change.

- **Summary.** `crc/service.py` (90 lines) keeps `Service` with the same class attributes, constructor, public and private methods; every method is fully annotated (`AgeSpec` / `Tags` aliases, `Optional`/`Union` replaced by `|`) and still delegates to `crc.core.age`. `get_retention_age` and `_parse_literal` are annotated `Any` instead of `Optional[str]` (they return any literal, annotation only). `crc/utils.py` (45 lines): `init_logging(...) -> None`, the noisy logger names moved to a private tuple `_NOISY_LOGGERS`, comments dropped; every statement, order, print and log line is as on main. `crc/__init__.py` and `tests/__init__.py` hold only the copyright comment and were already clean. The four ruff `extend-exclude` lines and the four mypy override lines (`crc`, `crc.service`, `crc.utils`, `tests`) were removed from `pyproject.toml`; nothing added.
- **Tests.** Before: 7379 passed. After: 8063 passed (+684). `tests/legacy/crc_service.txt` and `tests/legacy/crc_utils.txt` are byte-identical to `git show main:crc/service.py` and `git show main:crc/utils.py`. `tests/test_crc_service_parity.py` (+ `tests/helpers_crc_service.py`) compares return values, raised errors (type, message, cause) and log records of every `Service` method against the oracle: constructor / `init_logging` call, `require_positive_age`, `_lookup_custom_age_tag`, `validated_retention_age`, `get_retention_age`, `_parse_literal`, `get_min_age` over tables of tags (AWS list, dict, empty, duplicate, `1e999`, zero, negative, bool, float, string, garbage) and BUG-004/016/024/182/208/289 quirks. `tests/test_crc_service_is_old_parity.py` runs `is_old` over ages x creation offsets x `MAX_AGE` (unset, empty, valid, `{}`, non-dict, non-literal, `1e999`) plus naive/aware/None clocks (BUG-024..027). `tests/test_crc_utils_parity.py` runs `init_logging` on `tmp_path` with root logger, handlers and noisy-logger levels restored: printed text, level, handlers, formatters, files and directories, file content (truncation), invalid/special levels (`loud`, `""`, `basicConfig`, `basic_format`, `warn`, `notset`), filesystem errors, existing handlers kept, and repeated calls accumulating handlers. `tests/test_crc_utils.py` has 7 focused unit tests. Non-vacuous: `mode="w"` -> `"a"`, dropping `urllib3` from the noisy list, swapping `get_min_age` arguments, stripping the key in `get_retention_age` and swapping `is_old` times each made the parity tests fail; all reverted. All existing tests pass unchanged. `make check` steps (ruff, mypy strict, import-linter, pytest) are green.
- **Open issues.** 6 quirks in `docs/refactor/bugs/pending/PL-CORE.md` (handlers added on every `Service()` and log file truncated each time, arbitrary `logging` attribute accepted as level, file handler level, global silencing of SDK loggers and cwd-relative `logs/`, `Optional[str]` return annotation, unvalidated `get_min_age`).
- **Core changes needed.** None.
- **`crc/README.md` / other README changes needed (for integrator).** `crc/README.md` already documents `Service` and `init_logging` accurately; the integrator may optionally expand the `Public API` signature table in `crc/README.md` with the typed `Service` / `init_logging` signatures (`AgeSpec`, `Tags`, `init_logging(filename: str, log_level: str = "debug") -> None`), add `test_crc_service_parity.py`, `test_crc_service_is_old_parity.py`, `test_crc_utils_parity.py`, `test_crc_utils.py`, `helpers_crc_service.py` to `tests/README.md`, and add `crc_service.txt`, `crc_utils.txt` to `tests/legacy/README.md`.

## Round 1 (Argon)

### Findings

- **BLOCKING (fixed): Lane-rule violation on `crc/README.md` (`crc/README.md:1-66`, `docs/refactor/reviews/PL-CORE.md:9`).** The lane rules explicitly state: *"Do not edit docs/refactor/BUGS.md, docs/refactor/STATUS.md, docs/refactor/FILTER_VARIANTS.md, crc/README.md, tests/README.md, tests/legacy/README.md..."* and *"Put the rung status (summary, test counts, open issues, any needed crc/README.md or core change) at the top of docs/refactor/reviews/PL-CORE.md under '## Coding summary'"*. Moreover, the edit to `crc/README.md` invented `SAF-LOG-001` and `SAF-AGE-001` safety-invariant IDs not registered in `docs/refactor/SAFETY_INVARIANTS.md`. Reverted `crc/README.md` to `ef1f718` and recorded the optional integrator `crc/README.md` / `tests/README.md` / `tests/legacy/README.md` updates under `## Coding summary`.
- **BLOCKING (fixed): Inaccurate `main` line citations and quirk description in `docs/refactor/bugs/pending/PL-CORE.md` (`docs/refactor/bugs/pending/PL-CORE.md:9-13`).** `git show main:crc/service.py` is 318 lines long, but row 5 cited `crc/service.py:299-300`, `:420-426`, `:429-430` and row 6 cited `crc/service.py:295-330` (line numbers from `crc/core/age.py` on `ef1f718`, not `crc/service.py` on `main`). In addition, row 2 claimed `warn`, `basicConfig`, and `info` pass `getattr(logging, log_level.upper())` as functions and fail in `setLevel`, whereas `.upper()` maps `warn` to `logging.WARN` (`30`, succeeds), `info` to `logging.INFO` (`20`, succeeds), and `basicConfig` to `BASICCONFIG` (`AttributeError`), while uppercase non-level attributes like `basic_format` (`BASIC_FORMAT`) pass `getattr` and fail in `setLevel` with `ValueError`. Corrected row 2, row 3 (`crc/utils.py:11`, `:32-34`, `:39`, `:50-52`; `crc/service.py:28-29`), row 5 (`crc/service.py:145`, `:268-274`, `:276-278`, `:306`, `:314`), and row 6 (`crc/service.py:145-178`, `:222-225`, `:238-239`, `:250`, `:257`).
- **NIT (fixed while verifying parity): Explicit parity coverage for repeated `init_logging` calls and invalid level error types (`tests/test_crc_utils_parity.py:108-110`, `:206-270`).** Added `basic_format` to `CASES`, added `test_init_logging_repeated_calls_accumulate_handlers_parity` (directly testing multi-call handler accumulation and log truncation from row 1 of `docs/refactor/bugs/pending/PL-CORE.md`), and added `test_init_logging_invalid_levels_raise_expected_error_type` (explicitly pinning `AttributeError` vs `ValueError` for `loud`, `""`, `basicConfig`, and `basic_format` alongside oracle parity).

### Checklist Verification

1. **Behaviour parity with `main`:**
   - `crc/service.py`: `Service` preserves `logs_dir = "logs"`, `logs_file = "crc.log"`, `AGE_KEYS = frozenset({"days", "hours"})`, `__init__` (`init_logging(os.path.join(self.logs_dir, self.logs_file))`), `require_positive_age`, `_lookup_custom_age_tag`, `validated_retention_age`, `get_min_age`, `is_old`, `_parse_literal`, and `get_retention_age` (delegating to `crc.core.age` with identical arguments, return values, log lines, `MAX_AGE` env read, and error propagation for `BUG-004`, `BUG-016`, `BUG-024`..`BUG-027`, `BUG-182`, `BUG-208`, `BUG-289`).
   - `crc/utils.py`: `LOG_FORMATTER` and `init_logging(filename, log_level="debug")` preserve exact print output (`Setting loglevel to logging: <LEVEL>`), `getattr(logging, log_level.upper())`, `os.makedirs`, `FileHandler(filename, mode="w")` (DEBUG only when level is DEBUG, else INFO), `StreamHandler` (always INFO), silencing of `("azure", "msrest", "urllib3")` to `WARNING`, and final `logging.info` message.
   - `crc/__init__.py` and `tests/__init__.py`: byte-identical to `main`.
2. **Oracles and non-vacuous parity tests:**
   - `diff -u <(git show main:crc/service.py) tests/legacy/crc_service.txt` and `diff -u <(git show main:crc/utils.py) tests/legacy/crc_utils.txt` are empty (byte-identical).
   - Deliberate one-line mutations in `crc/service.py` (`get_min_age(dict2, dict1)`) and `crc/utils.py` (`fh.setLevel(logging.INFO)`) immediately failed 13 parity tests across `tests/test_crc_service_parity.py` and `tests/test_crc_utils_parity.py`; reverted.
3. **Ratchet and typing:**
   - `crc/__init__.py`, `crc/service.py`, `crc/utils.py`, and `tests/__init__.py` removed from ruff `extend-exclude` and `crc`, `crc.service`, `crc.utils`, `tests` removed from mypy `ignore_errors` overrides in `pyproject.toml`. No new exclusions, overrides, `# type: ignore`, or `# noqa` added.
4. **Scope & lane boundaries:**
   - `crc.py`, `crc/core/**`, `crc/README.md`, `tests/README.md`, `tests/legacy/README.md`, `docs/refactor/BUGS.md`, `docs/refactor/STATUS.md`, `docs/refactor/FILTER_VARIANTS.md`, and all cleaner/helper packages outside PL-CORE are untouched in `git diff ef1f718..HEAD`.

### Gate Results & Test Count

- `ruff check .`: passed (`All checks passed!`)
- `mypy`: passed (`Success: no issues found in 167 source files`)
- `lint-imports`: passed (`5 kept, 0 broken`)
- `pytest -q`: **8063 passed**

VERDICT: APPROVE

## Round 2 (Opus)

### Round 1 fixes assessment

- **`crc/README.md` revert: correct.** `git diff ef1f718..HEAD -- crc/README.md` is empty; the invented `SAF-LOG-001` / `SAF-AGE-001` IDs are gone and the integrator notes sit under `## Coding summary`.
- **Pending-row citations: correct.** Checked every location in `docs/refactor/bugs/pending/PL-CORE.md` against `git show main:crc/utils.py` (52 lines) and `git show main:crc/service.py` (318 lines): `utils.py:11`, `:21-22`, `:31-34`, `:38-39`, `:43-48`, `:50-52` and `service.py:19`, `:28-29`, `:145-178`, `:222-225`, `:238-239`, `:250`, `:257`, `:268-278`, `:306`, `:314` all point at the described code. The `warn` / `info` / `basicConfig` / `basic_format` corrections are right (`WARN` = 30, `BASICCONFIG` missing, `BASIC_FORMAT` is a string so `setLevel` raises `ValueError`).
- **Added tests: correct and not weakened.** `basic_format` case, `test_init_logging_repeated_calls_accumulate_handlers_parity` and `test_init_logging_invalid_levels_raise_expected_error_type` (`tests/test_crc_utils_parity.py:109`, `:207-270`) compare oracle and new outcomes and also assert concrete values (4 handlers, error type). No existing assertion changed. No production code changed in round 1.

### New findings

- **BLOCKING (fixed): wrong `notset` effect in pending row 2 (`docs/refactor/bugs/pending/PL-CORE.md:9`).** Round 1's rewrite said `NOTSET` makes the root logger "delegate to `WARNING`". The root logger has no parent: at level 0 `getEffectiveLevel()` is 0 and `isEnabledFor(DEBUG)` is True (checked), so every record reaches the handlers. Reworded the row.
- **NIT (left): new public aliases are not documented.** `crc/service.py:10-11` adds module-level `AgeSpec` and `Tags`; `crc/README.md` (outside this lane) does not list them. The Coding summary already asks the integrator to add the typed signatures; `ARCH-002` passes.
- **NIT (left): `Tags` duplicates the core's inline union.** `crc/core/age.py:215`, `:227` spell `dict[str, str] | list[dict[str, str]]` inline; a `Tags` alias belongs in `crc.core.age` next to `AgeSpec` (core change, out of lane).
- **NIT (left): test name.** `tests/test_crc_utils.py:86` `test_service_class_attributes_and_delegation` asserts only the class attributes; delegation is covered by the parity tests.

### Independent review

- **Parity.** `tests/legacy/crc_service.txt` and `tests/legacy/crc_utils.txt` are byte-identical to `main`. `crc/utils.py` keeps every statement and its order (print before `getattr`, `makedirs` before `FileHandler(mode="w")`, handler levels, noisy loggers, final `logging.info`); only comments, line wrapping and the private `_NOISY_LOGGERS` tuple changed. `crc/service.py` changes are annotations and one docstring line; every method still delegates to `crc.core.age` with the same arguments.
- **Non-vacuous.** My own mutations: `FileHandler(mode="a")` failed 2 utils parity tests; `is_old(age, current_time, current_time)` failed 168 `is_old` parity tests. Both reverted.
- **Safety (`CLEANUP_SAFETY.md`).** No age gate, `validated_retention_age` skip path, `MAX_AGE` behaviour or safety test touched; `tests/archtest/` and `.github/` unchanged.
- **Architecture and typing.** import-linter 5 kept / 0 broken; ratchet only shrank (4 ruff lines, 4 mypy module lines removed, nothing added); no `# type: ignore` or `# noqa`. `Any` on `_parse_literal` / `get_retention_age` matches `crc/core/age.py:207`, `:227` and the real return type (any literal, BUG-182).
- **Lane.** Only lane files, their tests and oracles, the pending-bug file, this review and `pyproject.toml` ratchet lines changed in `ef1f718..HEAD`.

### Gate results

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`: `ruff check .` passed, `mypy` passed, `lint-imports` 5 kept / 0 broken, `pytest -q` **8063 passed**.

ROUND 2 VERDICT: APPROVE
