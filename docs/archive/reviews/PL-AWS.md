# Review: PL-AWS

## Coding summary

Rung PL-AWS (plumbing): `crc/aws/_base.py`, `crc/aws/connectivity.py` and `crc/aws/__init__.py` are strict-clean, no behaviour change. Nothing is registered (they are not cleaners).

- **Summary.** `get_all_regions` and `CONNECTIVITY_ERRORS` / `log_skipped_region` keep every call, argument, log line and error path of `main`: one `boto3.client(service, region_name=default)` + `describe_regions()`, the INFO log of the list, and on a `CONNECTIVITY_ERRORS` error only the WARNING plus `boto3.session.Session().get_available_regions(service)` and the INFO "(static)" log; every other error (auth, `NoCredentialsError`, `KeyError` on a malformed response) propagates. Changes are typing only: `List` to `list`, annotated locals so the untyped boto3 results are narrowed to `list[str]`, `CONNECTIVITY_ERRORS: tuple[type[BaseException], ...]`, the import-time docstring of `_base.py` moved to a real module docstring, and the botocore import split so ruff isort passes. `__init__.py` still holds only the copyright comment (no `__all__`, no export list). No `# type: ignore`. All three files are removed from both legacy ratchet lists in `pyproject.toml` (ruff `extend-exclude`: 3 lines; mypy override: `crc.aws`, `crc.aws._base`, `crc.aws.connectivity`); nothing was added.
- **Tests.** Before: 7379 passed. After: 7523 passed (+144): `tests/test_aws_base_parity.py` (40: 38 cases, env set/unset, success, empty, malformed responses, every connectivity error in describe and in client creation, auth/credential/other errors, static fallback empty/tuple/erroring, plus a coverage check), `tests/test_aws_connectivity_parity.py` (98: tuple identity, classification of 16 exceptions, 80 log cases compared on records, args and stdout, plus a split check), `tests/test_aws_base.py` (3) and `tests/test_aws_connectivity.py` (3). Oracles `tests/legacy/aws_base.txt` and `tests/legacy/aws_connectivity.txt` are byte-identical to `git show main:<path>`; the base parity test swaps in the connectivity oracle while the base oracle runs. Non-vacuity proven: changing the "(static)" log text fails 19 cases, removing `ConnectionClosedError` from the tuple fails 7, changing the skipped-region log text fails 80; all reverted. `make check` equivalent (ruff, mypy strict on 166 files, import-linter, pytest incl. archtests) is green.
- **Tests after review.** 7536 passed (+157 over `ef1f718`): round 1 added 10 (`SSLError` and non-EC2 cases), round 2 added 3 (GovCloud / China fallback cases and one unit test).
- **Open issues.** None for the code. 6 quirks are in `docs/refactor/bugs/pending/PL-AWS.md` (static fallback lists regions the account has not enabled, ignores the partition of the default region and has no retry; `SSLError` counts as a connectivity error; unchecked response indexing; EC2-only `describe_regions` behind a generic `service_name`). BUG-227 (vm scan static list) is related and untouched.
- **Core changes needed.** None.
- **`crc/README.md` / `tests/README.md` / `tests/legacy/README.md` (not edited).** `tests/legacy/README.md` should list `aws_base.txt` (exact `git show main:crc/aws/_base.py`, run by `tests/test_aws_base_parity.py`) and `aws_connectivity.txt` (exact `git show main:crc/aws/connectivity.py`, run by `tests/test_aws_connectivity_parity.py`); `tests/README.md` should list `test_aws_base_parity.py`, `test_aws_base.py`, `test_aws_connectivity_parity.py` and `test_aws_connectivity.py`; `crc/README.md` should no longer call `crc/aws/_base.py`, `connectivity.py` and `__init__.py` legacy.

## Round 1 (Argon)

Scope reviewed: `git diff ef1f718..HEAD` (`crc/aws/_base.py`, `crc/aws/connectivity.py`, `crc/aws/__init__.py`, `crc/aws/README.md`, `pyproject.toml`, `docs/refactor/bugs/pending/PL-AWS.md`, `docs/refactor/reviews/PL-AWS.md`, `tests/legacy/aws_base.txt`, `tests/legacy/aws_connectivity.txt`, `tests/test_aws_base.py`, `tests/test_aws_base_parity.py`, `tests/test_aws_connectivity.py`, `tests/test_aws_connectivity_parity.py`) against `git show main:crc/aws/_base.py`, `git show main:crc/aws/connectivity.py`, `git show main:crc/aws/__init__.py`, `AGENTS.md`, `docs/refactor/BUGS.md`, and `.cursor/skills/crc-python-refactor/REVIEW_CHECKLIST.md`.

### 1. Behaviour parity (`git show main:<path>`)

- `crc/aws/_base.py` (`crc/aws/_base.py:14-42` vs `main:crc/aws/_base.py:15-43`):
  - `get_all_regions(service_name: str, default_region_name: str) -> list[str]` keeps the exact signature, `boto3.client(service_name, region_name=default_region_name)` call, `[region["RegionName"] for region in client.describe_regions()["Regions"]]` comprehension, `logging.info(f"Retrieved list of regions: {regions}")` log line, `except CONNECTIVITY_ERRORS as e:` clause, `logging.warning("describe_regions failed for %s via %s (%s); using boto3 static region list", service_name, default_region_name, type(e).__name__)` log line, `boto3.session.Session().get_available_regions(service_name)` fallback, and `logging.info(f"Retrieved list of regions (static): {static_regions}")` log line.
  - Non-connectivity errors (`ClientError` such as `AuthFailure`, `NoCredentialsError`, `KeyError`, `TypeError`) propagate without fallback (SAF-001, SAF-004).
- `crc/aws/connectivity.py` (`crc/aws/connectivity.py:10-32` vs `main:crc/aws/connectivity.py:10-31`):
  - `CONNECTIVITY_ERRORS: tuple[type[BaseException], ...] = (BotoConnectionError, ReadTimeoutError, ConnectionClosedError)` keeps the exact tuple elements and order.
  - `log_skipped_region(region: str, context: str, exc: BaseException) -> None` keeps the exact signature and `logging.warning("Region %s: skipped %s — network/connectivity (%s): %s", region, context, type(exc).__name__, exc)` call.
- `crc/aws/__init__.py` (`crc/aws/__init__.py:1` vs `main:crc/aws/__init__.py:1`): byte-identical (`# Copyright (c) Yugabyte, Inc.`).
- Every symbol callers import (`get_all_regions`, `CONNECTIVITY_ERRORS`, `log_skipped_region`) works with the same signature and types.

### 2. Oracles and parity tests

- `diff -u <(git show main:crc/aws/_base.py) tests/legacy/aws_base.txt` and `diff -u <(git show main:crc/aws/connectivity.py) tests/legacy/aws_connectivity.txt` are both empty (byte-identical).
- Verified non-vacuous parity by mutating the static-fallback log line in `crc/aws/_base.py:41` and removing `ConnectionClosedError` from `CONNECTIVITY_ERRORS` in `crc/aws/connectivity.py:22`: 22 parity tests failed immediately across `tests/test_aws_base_parity.py` and `tests/test_aws_connectivity_parity.py`; reverted both changes.

### 3. Typing, lint, scope, and pending bugs

- `crc/aws/__init__.py`, `crc/aws/_base.py`, and `crc/aws/connectivity.py` (`crc.aws`, `crc.aws._base`, `crc.aws.connectivity`) were removed from both ratchet lists in `pyproject.toml` (`pyproject.toml:24-43`, `:67-86`) with nothing added; `mypy` strict and `ruff check .` pass with zero `# type: ignore` or `# noqa` comments in the refactored modules and their tests.
- `crc.py`, `crc/core/**`, all cleaners, and all files forbidden by the lane rules are untouched; `crc/aws/README.md` edits only the sections describing `_base.py`, `connectivity.py`, and `__init__.py` and passes `ARCH-002` symbol sync.
- Verified every row in `docs/refactor/bugs/pending/PL-AWS.md` against `git show main:crc/aws/_base.py` and `git show main:crc/aws/connectivity.py`.

### Findings

- **BLOCKING (fixed): `SSLError` quirk in `docs/refactor/bugs/pending/PL-AWS.md` was not covered by a parity test (`tests/test_aws_base_parity.py:44-52`, `tests/test_aws_connectivity_parity.py:31-51`, `:113-119`, `docs/refactor/bugs/pending/PL-AWS.md:9`).**
  `docs/refactor/BUGS.md` rule 2 requires every recorded quirk to be covered by a test, and row 3 of `docs/refactor/bugs/pending/PL-AWS.md` documents `botocore.exceptions.SSLError` being classified as a connectivity error via `BotoConnectionError`, yet neither `tests/test_aws_connectivity_parity.py` nor `tests/test_aws_base_parity.py` included `SSLError`, and `tests/test_aws_base_parity.py:_run` only compared `stdout` (`capsys.readouterr().out`) without `stderr`. Added `"ssl"` (`SSLError`) to `EXCEPTIONS` and `test_connectivity_split_is_not_vacuous` in `tests/test_aws_connectivity_parity.py` and to `conn_errors()` and `test_cases_cover_both_paths_and_errors` in `tests/test_aws_base_parity.py`, added `stderr` comparison to `tests/test_aws_base_parity.py:201-210`, added a `no-describe-regions-method` parity case (`tests/test_aws_base_parity.py:95-99`) plus its corresponding `NEW` low-risk `cosmetic` row in `docs/refactor/bugs/pending/PL-AWS.md:11` (`main:crc/aws/_base.py:15-30`, `:40-41`: `get_all_regions` docstring names `'s3'` alongside `'ec2'`, but `client.describe_regions()` is EC2-only and raises `AttributeError` on non-EC2 clients), and cited the `ssl` test cases in row 3 of `docs/refactor/bugs/pending/PL-AWS.md`.
- **NIT (left): None.**

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` (`crc`, `tests`) | Success: no issues found in 166 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | 7533 passed |

Test count: 7379 before PL-AWS (`ef1f718`), 7523 before review, 7533 after review additions (+154).

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff ef1f718..HEAD` (both commits: `5b6f878` coding, `6175ae4` round 1), checked against `git show main:crc/aws/_base.py`, `git show main:crc/aws/connectivity.py` and `git show main:crc/aws/__init__.py`, `REVIEW_CHECKLIST.md`, `CLEANUP_SAFETY.md` (SAF-001, SAF-004) and `docs/refactor/BUGS.md`.

### Round 1 fixes assessment

- The `ssl` entries in `conn_errors()` (`tests/test_aws_base_parity.py:52`) and `EXCEPTIONS` (`tests/test_aws_connectivity_parity.py:38`) only add cases. They assert the `main` behaviour: `SSLError` falls back to the static list or counts as a connectivity error. No existing assertion was removed or relaxed.
- The `stderr` key in `_run` (`tests/test_aws_base_parity.py:216`) makes the comparison stricter.
- The `no-describe-regions-method` case (`tests/test_aws_base_parity.py:97-101`) uses a fake `AttributeError`, and it propagates on both sides. The row it backs (`docs/refactor/bugs/pending/PL-AWS.md:11`) cites `main:crc/aws/_base.py:15-30` and `:40-41`, which I verified: the docstring `'s3'` is at `:19`, `describe_regions` at `:29` and `get_available_regions` at `:41`.
- No production code changed in round 1. Verdict: all fixes are correct, behaviour is unchanged and no test was weakened.

### Independent review

- **Parity.** `crc/aws/_base.py` and `crc/aws/connectivity.py` differ from `main` only in annotations, the `List` to `list` change, the module docstring moved above the imports (which only sets `__doc__`) and the botocore import split. Calls, arguments, log formats, the `except CONNECTIVITY_ERRORS` scope (client creation plus `describe_regions`) and the propagation of every other error (SAF-004) are the same. `crc/aws/__init__.py` is unchanged. Both oracles are byte-identical to `git show main:<path>`.
- **Non-vacuity.** I passed a `partition_name` to `get_available_regions` in `crc/aws/_base.py`, which failed 27 tests across `tests/test_aws_base.py` and `tests/test_aws_base_parity.py`, then reverted it.
- **Ratchet and archtest.** Exactly 3 ruff lines and 3 mypy modules were removed, and nothing was added. `tests/archtest/legacy_baseline.txt` still lists the two files, which is fine because the ratchet only requires `pyproject.toml` to be a subset of it.
- **README.** `crc/aws/README.md` edits only the PL-AWS lines and sections, and ARCH-002 passes.
- **Bug rows.** I verified every row's `main` file:line citation. None duplicates `BUGS.md`; BUG-227 is related but concerns `crc.py`.

### Findings

- **BLOCKING (fixed): a missed quirk with no test.** The static fallback calls `Session().get_available_regions(service_name)` with botocore's default `partition_name='aws'` (`main:crc/aws/_base.py:40-41`). A GovCloud or China `default_region_name` therefore falls back to the commercial region list. I added the parity cases `fallback-govcloud-default-region` and `fallback-china-default-region` (`tests/test_aws_base_parity.py:131-140`), the unit test `test_static_fallback_ignores_partition_of_default_region` (`tests/test_aws_base.py`) and a `NEW` row in `docs/refactor/bugs/pending/PL-AWS.md`.
- **BLOCKING (fixed): stale coding summary.** It said 4 quirks and gave no count after review. I updated it in place: 6 quirks, 7536 tests.
- **NIT (left): stale docstring.** The docstring of `get_all_regions` still says `:rtype: List[str]` (`crc/aws/_base.py:22`). Text only, kept as on `main`.
- **NIT (left): count wording.** The coding summary's "40: 38 cases" for `tests/test_aws_base_parity.py` was 39 cases plus a coverage test at `5b6f878`. Only the wording is off.
- **NIT (left, outside lane).** `tests/README.md`, `tests/legacy/README.md` and `crc/README.md` still need the entries listed in the coding summary.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` | Success: no issues found in 166 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | 7536 passed |

Test count: 7379 at `ef1f718`, 7533 after round 1, 7536 after round 2.

ROUND 2 VERDICT: APPROVE
