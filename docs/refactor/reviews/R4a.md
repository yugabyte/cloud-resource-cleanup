# R4a review

Scope reviewed: `git diff de2a1e0..46740e5` (commits `ee14ac6` and `46740e5`, R4a AWS EC2
key pair cleaner in `crc/aws/keypairs.py`, package READMEs, `pyproject.toml`,
`docs/refactor/BUGS.md`, `docs/refactor/FILTER_VARIANTS.md`, `docs/refactor/STATUS.md`,
and tests in `tests/legacy/aws_keypairs.txt`, `tests/test_aws_keypair_parity.py`,
`tests/test_aws_keypairs.py`, `tests/test_core_registry.py`) against
`git show main:crc/aws/keypairs.py`, `AGENTS.md`, `docs/refactor/BUGS.md`, and
`.cursor/skills/crc-python-refactor/REVIEW_CHECKLIST.md`.

## 1. Behaviour parity with `git show main:crc/aws/keypairs.py`

Compared `crc/aws/keypairs.py` against `git show main:crc/aws/keypairs.py`:

- **SDK calls (order and arguments)** (`crc/aws/keypairs.py:49-55`, `:130`, `:159`, `:241-249` vs `main:crc/aws/keypairs.py:99-142`):
  - `get_all_regions("ec2", "us-west-2")` runs first (calling `boto3.client("ec2", region_name="us-west-2").describe_regions()`, falling back to `boto3.session.Session().get_available_regions("ec2")` on `CONNECTIVITY_ERRORS` per `SAF-001` and propagating `ClientError` auth failures per `SAF-004`).
  - For each region, `KeyPairs.delete()` creates `boto3.client("ec2", region_name=region)`, calls `client.describe_key_pairs()`, filters key pairs, and when `not self.dry_run` calls `client.delete_key_pair(KeyName=candidate.id)` in `set` iteration order (`BUG-047`) before moving to the next region (`BUG-046`).
- **Filter and age decisions** (`crc/aws/keypairs.py:94`, `:135-145`, `:163-177` vs `main:crc/aws/keypairs.py:95-98`, `:104-134`):
  - Entries missing `"KeyName"` or `"CreateTime"` are silently skipped (`BUG-048`).
  - `name_regex` uses `crc.core.policy.name_regex_search_matches` (`re.search` any, empty/falsy matches all, `BUG-045`).
  - `exception_regex` deduplicates into a `set` (`self._exception_patterns`) as on `main:crc/aws/keypairs.py:95-98` (`BUG-047`) and uses `crc.core.policy.exception_regex_search_matches` (`re.search` any, empty is `False`), evaluated unconditionally even when `name_regex` did not match (`BUG-048`).
  - Age uses `crc.core.age.is_old(self._age or {}, now.astimezone(created.tzinfo), created)` (`SAF-006`, `SAF-010`, `BUG-045`).
- **Log lines and printed messages** (`crc/aws/keypairs.py:68-69`, `:72-80`, `:160`, `:171`, `:174`, `:228` vs `main:crc/aws/keypairs.py:86`, `:127-134`, `:139-141`, `:144`, `:147-155`):
  - Every `logging.debug`, `logging.info`, and `logging.warning` message is identical to `main`, including `Keypair {name} is not old enough to be deleted.`, `Keypair {name} is in exception_regex {self._exception_regex}.` (logging the original `exception_regex` sequence rather than the deduplicated `set`), `Deleted keypair: {candidate.id} with response: {response}`, `log_skipped_region(region, "keypair cleanup", e)`, `count of items in deleted_keypairs: {count}`, and the summary warnings.
- **Errors and dry-run path** (`crc/aws/keypairs.py:107-109`, `:124-125`, `:157-161`, `:248-249` vs `main:crc/aws/keypairs.py:137-145`):
  - `CONNECTIVITY_ERRORS` skip the region and log via `log_skipped_region`; mid-region connectivity errors during `delete_key_pair` keep earlier deletions in `deleted_keypairs` and skip the rest of the region (`BUG-046`).
  - Non-connectivity errors (`ClientError`, `re.error`, `TypeError` on naive `CreateTime`, `KeyError` on missing `"KeyPairs"`) propagate immediately (`BUG-046`).
  - In dry run, `delete_key_pair` is never called, matching key pairs are appended to `deleted_keypairs` / `Result.succeeded`, and the dry-run summary warning is logged.

## 2. Oracle and parity test verification (`tests/legacy/aws_keypairs.txt`, `tests/test_aws_keypair_parity.py`)

- Diffed `tests/legacy/aws_keypairs.txt` against `git show main:crc/aws/keypairs.py`: byte-for-byte identical (SHA-256 `e61b310d9a45a30423bfdb2528c4caf75ef63d5c997579d465d29b21842b2b99`, 0 lines of diff).
- Verified `tests/test_aws_keypair_parity.py` executes both the oracle (`tests/legacy/aws_keypairs.txt`) and the refactored code against the same `FakeAws` / `FakeEc2` fakes and compares ordered SDK `calls`, `error`, `deleted`, `count`, `logs`, and `stdout`.
- Tested deliberate one-line behaviour mutations in `crc/aws/keypairs.py` (inverting `core_age.is_old` in `_skip_reason` and dropping `name_regex` in `build_keypair_cleaner`), confirmed `pytest -q tests/test_aws_keypair_parity.py` failed on both, and reverted them.
- Fixed two gaps (listed under BLOCKING below) so `KeyPairCleaner` (`plan` + `apply` via `build_keypair_cleaner`) also logs the summary warnings, accepts omitted `age` in `RunContext.options`, and is tested directly against the oracle alongside `describe_regions` `ClientError` (`SAF-004`) and `CONNECTIVITY_ERRORS` (`SAF-001`) cases.

## 3. Architecture and guardrails

- `KeyPairCleaner` implements `Cleaner` (`cloud = "aws"`, `kind = "keypair"`, `supports`, read-only `plan`, mutating `apply`) and is registered via `@register("aws", "keypair")` on `build_keypair_cleaner`.
- `plan()` never mutates (`ARCH-005` passes on `crc/aws/keypairs.py` as a non-legacy module; `delete_key_pair` is only in `apply_region`).
- Filtering uses `crc.core.policy.name_regex_search_matches` and `crc.core.policy.exception_regex_search_matches` as named in `docs/refactor/FILTER_VARIANTS.md`; age uses `crc.core.age.is_old`; no `re`, `os.environ`, `print`, or `sys.exit` in `crc/aws/keypairs.py` (`ARCH-003`).
- SDK is behind the typed `KeyPairClient` `Protocol` (`describe_key_pairs`, `delete_key_pair`).
- `crc/aws/keypairs.py` (`crc.aws.keypairs`) left both legacy ratchet lists in `pyproject.toml`; nothing was added to the ratchet (`ARCH-001`), and the `boto3`/`botocore` `ignore_missing_imports = true` override is per-SDK.

## 4. Scope and documentation

- `crc.py` and all other cleaners are untouched.
- `tests/test_core_registry.py` had the stated snapshot change (`registered_before = registry.DEFAULT_REGISTRY.clouds()`) so importing `crc.aws.keypairs` before the test does not break the leak check.
- `crc/aws/README.md`, `crc/core/README.md`, `tests/README.md`, and `tests/legacy/README.md` are accurate and pass `ARCH-002` symbol sync (`KeyPairClient`, `KeyPairRuntime`, `KeyPairCleaner`, `build_keypair_cleaner()`, `KeyPairs`).

## 5. Verification of `BUG-045` to `BUG-048` against `main`

| ID | Risk | `main` location | Verified via `git show main:crc/aws/keypairs.py` | Real-world effect |
|----|------|-----------------|--------------------------------------------------|-------------------|
| `BUG-045` | medium | `crc/aws/keypairs.py:48-69` (`__init__`), `:112-114`, `:120-124` | Confirmed (`__init__` does not validate `age` or `name_regex`; `not self.name_regex` is `True` when empty at `:112`; `self.is_old(self.age, ...)` returns `True` when `self.age` is empty at `:120-124`) | Running with empty `age` and empty `name_regex` matches and deletes every key pair in every region (`deletes what it should keep`). |
| `BUG-046` | low | `crc/aws/keypairs.py:99-146`, `:143-145`, `:147-155` | Confirmed (per-region loop describes and deletes in the same pass at `:99-142`; `except CONNECTIVITY_ERRORS` at `:143-145` skips remaining items in a region after partial deletes; non-connectivity errors abort before the `:147-155` summary logs) | Partial deletions across or within regions on error without summary logs (`cosmetic`). |
| `BUG-047` | low | `crc/aws/keypairs.py:95-98`, `:101`, `:136-141` | Confirmed (`exception_regex = set(self.exception_regex)` at `:96` and `keypairs_to_delete = set()` at `:101`, iterated at `:116` and `:136`) | Deletion order and exception pattern evaluation order depend on hash seed (`cosmetic`). |
| `BUG-048` | low | `crc/aws/keypairs.py:105-106`, `:112-118` | Confirmed (`if "KeyName" not in keypair or "CreateTime" not in keypair: continue` at `:105-106`; `match_exception_regex` evaluated unconditionally at `:115-117`) | Key pairs missing `KeyName`/`CreateTime` are skipped without logging, and an invalid `exception_regex` raises even when `name_regex` did not match (`cosmetic`). |

## BLOCKING (fixed)

1. **`KeyPairCleaner` (`plan` / `apply`) omitted the summary `logging.warning` lines (`main:crc/aws/keypairs.py:147-155`) and `build_keypair_cleaner` raised `KeyError` when `age` was omitted from `RunContext.options`** (`crc/aws/keypairs.py:72-80`, `:116-135`, `:178-206`):
   - Moved `_log_summary` to a module helper called by `KeyPairs.delete()`, by `KeyPairCleaner.plan()` when `context.dry_run` is `True`, and by `KeyPairCleaner.apply()`, so the `Cleaner` protocol path logs the exact summary warnings of `main:crc/aws/keypairs.py:147-155`.
   - Changed `build_keypair_cleaner` to read `options.get("age")` and widened the `name_regex`, `exception_regex`, and `age` annotations on `KeyPairCleaner` / `KeyPairs` to accept `None` and `int` as on `main`.
2. **`tests/test_aws_keypair_parity.py` only tested the `KeyPairs` adapter, not `KeyPairCleaner` / `build_keypair_cleaner` or `describe_regions` error handling (`SAF-001`, `SAF-004`), and `tests/test_aws_keypairs.py` did not cover `KeyPairCleaner.apply` connectivity errors** (`tests/test_aws_keypair_parity.py:58`, `:166-175`, `:186-214`, `:313-405`, `tests/test_aws_keypairs.py:129-157`):
   - Added `describe-regions-client-error-propagates` (`SAF-004`) and `describe-regions-connection-error-falls-back` (`SAF-001`) to `CASES` and `QUIRKS` in `tests/test_aws_keypair_parity.py`.
   - Added `test_cleaner_protocol_matches_main` in `tests/test_aws_keypair_parity.py` to run `build_keypair_cleaner(RunContext(...))` (`plan` and `apply`) across all 48 cases and compare errors, stdout, deleted key pairs, SDK calls, and logs against the oracle.
   - Added `test_apply_skips_region_on_connectivity_error` and omitted-`age` factory coverage in `tests/test_aws_keypairs.py`.

## NIT (left)

- `KeyPairRuntime.clock` defaults to `datetime.datetime.now` (as a function reference, not a call, so `ARCH-003` passes) to keep `KeyPairs()` backwards-compatible until `crc.py` is wired to `engine.run` in the CLI rung (already noted in `docs/refactor/STATUS.md`).

## 6. Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` (`crc`, `tests`) | Success: no issues found in 79 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | 2201 passed in 5.54s |

Test count: 2077 before R4a (`de2a1e0`), 2148 on `46740e5`, 2201 after review additions (+53).

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff de2a1e0..5ea28f1` (R4a plus round 1 fixes), read against `AGENTS.md`,
`ARCHITECTURE.md`, `CLEANUP_SAFETY.md`, `docs/refactor/BUGS.md`, the review checklist and
`git show main:crc/aws/keypairs.py`.

### Round 1 fixes assessment

- Summary warnings on the `Cleaner` path (`crc/aws/keypairs.py:72-80`, `:124`, `:139`, `:264`):
  correct. The text equals `main:crc/aws/keypairs.py:147-155`. `crc.core.engine.run` calls
  `apply()` only when `dry_run` is False (`crc/core/engine.py:99-100`), so the dry-run summary
  is logged once, from `plan()`. `KeyPairs.delete()` output is unchanged; its parity test still
  compares every log record.
- `options.get("age")` in `build_keypair_cleaner` and the widened `| None` / `| int`
  annotations: these match `main`, where `KeyPairs.__init__` stores whatever it gets and
  `is_old` treats a falsy age as "matches everything". `self._age or {}` gives the same
  result for `None`, `0` and `{}` because `core_age.is_old` returns True for any falsy age
  (`crc/core/age.py:191-193`). `MAX_AGE` is still applied inside `core_age.is_old` (SAF-010).
- New tests: `test_cleaner_protocol_matches_main` asserts error and stdout on every case,
  `deleted` on every case without an error, and the full log and SDK call lists (excluding
  `client` creation) for cases with one region or none. For the two cases where main deletes
  some key pairs and then fails, it asserts that the protocol path made no
  `delete_key_pair` call at all, which is the documented, safer plan-then-apply difference.
  The test is not vacuous. The `describe_regions` `ClientError` (SAF-004) and connectivity
  fallback (SAF-001) cases also run against `KeyPairs`, and two new quirk checks back them.
  The `tests/test_core_registry.py` change (compare against a snapshot taken before the test,
  not `()`) keeps the same leak check.
- I re-checked the oracle: `diff <(git show main:crc/aws/keypairs.py) tests/legacy/aws_keypairs.txt`
  is empty.

No round 1 fix changed behaviour or weakened a test.

### Independent review

- Parity (`KeyPairs.delete`, the path `crc.py:660-661` uses): the SDK call order, the per-region
  `set` iteration (same names inserted in the same order, so the iteration order is the same
  as `main`), the exception patterns deduplicated once before `get_all_regions` (a bad
  `exception_regex` element still raises before any region call), the log text, the dry-run
  path and the error propagation all match `main:crc/aws/keypairs.py:95-155`. I also ran a
  string, rather than a list, `name_regex` / `exception_regex` against the oracle; the results
  are identical.
- Safety: SAF-001, SAF-004, SAF-006 and SAF-010 hold. `plan()` and `plan_region()` only call
  `describe_key_pairs` (ARCH-005). No safety-locked test was touched.
- Architecture: import-linter 5 kept. ARCH-002 to ARCH-005 pass. `crc.aws.keypairs` left both
  ratchet lists and nothing was added. The new `boto3`/`botocore` mypy override is limited to
  the SDKs (`ignore_missing_imports`, not `ignore_errors`), so it is not a ratchet entry.
- Docs: I checked the round 1 BUG-045 to BUG-048 `main` citations line by line; they are
  accurate. The `crc/aws/README.md` symbol list and safety notes are accurate.

### BLOCKING (fixed)

None.

### NIT (left)

1. **`KeyName` present but `None` (or another falsy value that is not a string).**
   `policy.name_regex_search_matches` and `policy.exception_regex_search_matches` search
   `text or ""` (`crc/core/policy.py:182`, `:187`), and `KeyPairCleaner._skip_reason` calls
   them (`crc/aws/keypairs.py:180-181`). With a non-empty `name_regex` or `exception_regex`,
   `main` raises `TypeError` from `re.search(p, None)` (`main:crc/aws/keypairs.py:112-117`),
   while the refactor treats the name as `""`. With `[".*"]` it selects the key pair: in a dry
   run it is listed as `None`, and outside a dry run `delete_key_pair(KeyName=None)` is called.
   I confirmed this with an ad-hoc oracle comparison that I did not commit. botocore parses
   `KeyName` as a string or leaves it out, so this cannot happen against real EC2. The real
   client would also reject `KeyName=None` with `ParamValidationError` before sending a
   request. I left it instead of copying CPython's version-specific `TypeError` text into a
   cloud module. Fix this when the policy `None` handling is revisited.
2. If `KeyPairCleaner.apply()` is called directly with `dry_run=True`, the summary is logged a
   second time, after the one from `plan()`. The engine never makes that call
   (`crc/core/engine.py:100`).
3. `KeyPairRuntime.clock` defaults to `datetime.datetime.now` (round 1 NIT; still open in
   STATUS).

### New bugs recorded (`docs/refactor/BUGS.md`, now 50 rows)

- `BUG-049`: there is no in-use check (instances, launch templates) before `delete_key_pair`
  (`main:crc/aws/keypairs.py:104-142`, `:138`).
- `BUG-050`: deleted key pairs are reported by bare name without region
  (`main:crc/aws/keypairs.py:142`, `main:crc.py:446`).

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check` (Python 3.12.3):

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` (`crc`, `tests`) | Success: no issues found |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | 2201 passed |

Test count: 2201, the same as after round 1. Round 2 changed docs only.

ROUND 2 VERDICT: APPROVE
