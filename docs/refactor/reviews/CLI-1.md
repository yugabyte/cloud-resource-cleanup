## Coding summary

Rung CLI-1: argument parsing and input validation moved from `crc.py` into the new package `crc/cli/`, no behaviour change.

- **Summary.** `crc/cli/args.py` holds `get_argparser()` (290 lines), split into five private `_add_*_args` groups (selection, filter, age, notify, resource) added in the exact order of `main`, so `--help`, the usage line, the required-flag error and the key order of the returned dict are unchanged; `ast.literal_eval` stays the `type=` of the literal flags. `crc/cli/validate.py` holds `is_valid_type`, `is_valid_list`, `is_valid_dict`, `are_values_of_dict_lists`, `_validate_influxdb_input` and `validate_influxdb_inputs` (78 lines). `crc/cli/__init__.py` re-exports the public names; `crc/cli/README.md` has the four ARCH-002 sections. Both modules are ruff clean and mypy strict (the parsed args stay a plain `dict[str, Any]`). `crc.py` imports all seven names back (`from crc.cli.args import get_argparser`, `from crc.cli.validate import ...`), still holds the `CRC` class, `main()` and the constants, and `main()` still looks `get_argparser` up in the `crc.py` globals. The unused `argparse` and `ast` imports were dropped from `crc.py`. `crc.py` stays on the ratchet in `pyproject.toml` (not edited). `crc/README.md` has the `cli/` entry and the `crc.py` description.
- **Tests.** Before: 8516 passed. After: 12561 passed (+4045). New: `tests/test_cli_args_parity.py` (oracle `tests/legacy/crc_main.txt`, byte-identical to `git show main:crc.py`, loaded with `exec` into a module named `legacy_crc_main`). 1731 argv cases run through the oracle `get_argparser` and `crc.cli.args.get_argparser` with the same `sys.argv`, comparing the returned items in order, the `SystemExit` code, stdout and stderr, or the raised exception type and message: every flag (short, long, `--flag=value`), defaults, every JSON and Python-literal form for `age`, `detach_age`, `filter_tags`, `exception_tags`, `notags`, `influxdb`, `resource_states`, `name_regex`, `exception_regex` (about 60 values each, including bad literals), plain flags (`max_age`, `custom_age_tag_key`, `slack_*`, `kms_*`, `resource_group`, `scan_tag`, `project_id`), the KMS pending-window range, unknown choices, missing required flags, abbreviations, repeated and combined flags, `-h`. Validator tables (about 2,400 cases) compare return value, exception type and message for all six validators, plus pinned-message tests and a test that `crc.py` loaded by path exposes the same function objects. All existing tests pass unchanged. `make check` (ruff, mypy strict, import-linter, pytest incl. archtests) is green.
- **Non-vacuity.** One-line changes made locally, each failed the parity test and was reverted: `kms_pending_window` default 7 to 8 (1243 failures), an extra `--operation_type` choice (8), `type=ast.literal_eval` added to `--max_age` (36), a changed `--dry_run` help word (7), the validator message "should be" to "must be" (1618), the `org` check removed from `validate_influxdb_inputs` (3), the order of two flag groups swapped (1543), `is_valid_list` accepting tuples (157).
- **Monkeypatch retargets.** None. The four path-loading tests (`tests/test_core_report.py`, `tests/test_core_engine.py`, `tests/test_core_selection.py`, `tests/test_notify_parity.py`) are unchanged; every name they use stays importable from `crc.py`.
- **Open issues.**
  - 5 quirks recorded in `docs/refactor/bugs/pending/CLI-1.md` (all kept as on main): a literal that is a Python syntax error ends in a `SyntaxError` traceback with exit 1 instead of a usage error with exit 2; `--max_age` is unvalidated; `--notags` is never validated; `validate_influxdb_inputs` uses a bare `in` test on any input; `--resource` help omits `vpc`; `allow_abbrev` is on (flag prefixes work).
  - Not edited (outside the lane): `tests/legacy/README.md` should list `crc_main.txt` (exact `git show main:crc.py`, run by `tests/test_cli_args_parity.py`) and `tests/README.md` should list `test_cli_args_parity.py`.
  - The environment ran Python 3.12 (3.11 not installed); `make check` was run with it.
- **Core changes needed.** None.

## Round 1 (Argon)

Reviewed `git diff d1918f3..HEAD` against the CLI-1 task and `REVIEW_CHECKLIST.md`.

### Findings

- **BLOCKING (fixed):** `docs/refactor/bugs/pending/CLI-1.md:13-14` had slight off-by-one/two line references against `git show main:crc.py` (`validate_influxdb_inputs` call site in `main()` is `:1415-1416`, not `:1413-1414`; `--resource` `choices` list is `:1011-1023` and `help` is `:1025-1030`). Corrected in `docs/refactor/bugs/pending/CLI-1.md`.
- **NIT (left):** `docs/refactor/reviews/CLI-1.md:10` says "5 quirks recorded" in the coding summary while listing all 6 rows present in `docs/refactor/bugs/pending/CLI-1.md`.
- **NIT (left):** `tests/test_cli_args_parity.py:52-55` loads `tests/legacy/crc_main.txt` via `ModuleType("legacy_crc_main")` + `exec(compile(...))` rather than `importlib.util.spec_from_file_location` with a `SourceFileLoader` (since `.txt` is not in default source suffixes); functionally identical and `test_crc_py_reexports_the_same_functions` (`tests/test_cli_args_parity.py:513-535`) also loads `crc.py` via `spec_from_file_location`.

### Verification

1. **CLI contract & behaviour parity (`crc/cli/args.py`, `crc/cli/validate.py`, `crc.py`):** Every flag, short/long option, `choices`, `default`, `metavar`, `help` string, `type=ast.literal_eval` / `type=int` / `type=str`, flag registration order, and validator (`is_valid_type`, `is_valid_list`, `is_valid_dict`, `are_values_of_dict_lists`, `_validate_influxdb_input`, `validate_influxdb_inputs`) match `git show main:crc.py`. `crc.py` imports all seven functions into module globals so `main()` and path-loading callers see identical objects.
2. **Oracle & non-vacuity (`tests/legacy/crc_main.txt`, `tests/test_cli_args_parity.py`):** `cmp -s <(git show main:crc.py) tests/legacy/crc_main.txt` is byte-identical. Confirmed non-vacuity by mutating `kms_pending_window` default `7 -> 8` in `crc/cli/args.py:234` and `"should be" -> "must be"` in `crc/cli/validate.py:14`, observing 2861 test failures in `tests/test_cli_args_parity.py`, then reverting.
3. **Existing tests:** None of the four path-loading tests (`tests/test_core_report.py`, `tests/test_core_engine.py`, `tests/test_core_selection.py`, `tests/test_notify_parity.py`) or any other existing test was modified.
4. **Architecture, typing, sizes, guardrails:** `crc/cli/args.py` (290 lines), `crc/cli/validate.py` (78 lines), `crc/cli/__init__.py` (19 lines) are mypy-strict and ruff clean; `crc/cli/README.md` has all four `ARCH-002` sections and synced `Public API`; `pyproject.toml` and all `.github/CODEOWNERS` files are untouched.
5. **Bug register (`docs/refactor/bugs/pending/CLI-1.md`):** All 6 rows verified against `git show main:crc.py`.
6. **Gates (`make check`):**
   - `ruff check .`: passed
   - `mypy`: passed (`Success: no issues found in 75 source files`)
   - `lint-imports`: passed (`7 contracts kept, 0 broken`)
   - `pytest -q`: `12561 passed` (up from 8516, +4045)

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff d1918f3..HEAD` (commits `8d18379`, `4dc6717`), read against `AGENTS.md`,
`ARCHITECTURE.md`, `docs/refactor/BUGS.md`, `REVIEW_CHECKLIST.md` and the CLI contract.

### Round 1 fixes

- The only code-free fix (line references in `docs/refactor/bugs/pending/CLI-1.md`) is correct:
  re-checked with `git show main:crc.py`, `validate_influxdb_inputs` is called at `:1415-1416`,
  the `--resource` choices are `:1011-1023` and its help `:1025-1030`. No code or test was changed
  by round 1, so no behaviour change and no weakened test.

### Independent verification

- Oracle: `cmp <(git show main:crc.py) tests/legacy/crc_main.txt` is byte-identical.
- Parser: built both parsers (oracle and `crc.cli.args`) by intercepting `parse_args` and compared
  every parser attribute and all 25 actions field by field (`option_strings`, `dest`, `nargs`,
  `const`, `default`, `type`, `choices`, `required`, raw `help`, `metavar`, action class): all equal.
- Validators: the six functions in `crc/cli/validate.py` have the same AST as `main:crc.py:1241-1315`
  once annotations and docstrings are stripped.
- `crc.py`: the diff only removes `import argparse`, `import ast` (no other use left) and the
  moved functions, and adds the imports from `crc.cli`; `main()` (`crc.py:856`) still looks
  `get_argparser` up in the module globals, the validator calls are untouched. The four
  path-loading tests are unchanged and every name they patch is still in `crc.py`.
- Safety (`CLEANUP_SAFETY.md`): no cleaner, age, tag or notification code changed; `--max_age`
  stays an untyped string exported as `MAX_AGE`. Not applicable beyond that.
- Architecture: `crc/cli/README.md` has the four ARCH-002 sections, Public API matches `__all__`;
  `crc.cli` imports only `argparse`, `ast`, `typing`. Files are 290/78/19 lines, mypy strict and ruff
  clean. `pyproject.toml` and CODEOWNERS files untouched.

### Findings

- **BLOCKING (fixed):** `tests/test_cli_args_parity.py` loaded the oracle with `exec` into a bare
  `ModuleType`, while the lane contract requires `importlib.util.spec_from_file_location` (round 1
  left it as a NIT). Now loaded with `spec_from_file_location("legacy_crc_main", ORACLE,
  loader=SourceFileLoader(...))` (the explicit loader is needed for the `.txt` suffix); the
  function-local `import importlib.util` moved to the module imports. Same 4045 cases pass.
- **BLOCKING (fixed), test gap:** the parity test compared only the formatted `-h` output, which
  argparse reflows, so a whitespace-only change to a raw help string passed all 4045 tests
  (checked: `Enable dry_run only mode` to `Enable  dry_run only mode`). Added
  `test_raw_parser_definition_matches_main`, which compares the parser attributes and every action
  field against the oracle; the same mutation now fails it, then reverted.
- **NIT (left):** the summary says "5 quirks" and lists 6 (round 1 noted it); the file now has 7.
- **NIT (left):** round 1 reported `mypy` on "75 source files" and `lint-imports` "7 contracts kept"; this run checks 182 files and import-linter reports 5 contracts kept, 0 broken.
- **NIT (left):** the rule "`crc.cli` imports no cloud package, no `crc.notify`, no SDK"
  (`crc/cli/README.md:39`) is not enforced by an import-linter contract; adding one needs a
  `pyproject.toml` change, which is outside this lane.
- **NIT (left):** `tests/legacy/README.md` and `tests/README.md` do not mention `crc_main.txt` and
  `test_cli_args_parity.py` (outside the lane's editable files, already listed under Open issues).
- **New quirk recorded** in `docs/refactor/bugs/pending/CLI-1.md`: `ArgumentDefaultsHelpFormatter`
  prints the default twice for `--resource` / `--operation_type` and `(default: None)` on 15 flags
  (`main:crc.py:992`, `:1029`, `:1048`).

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check` (Python 3.12.3; 3.11 is
not installed): `ruff check .` passed, `mypy` passed (182 source files), `lint-imports` 5 contracts
kept, `pytest -q` 12562 passed (12561 before this round, +1 new test).

ROUND 2 VERDICT: APPROVE
