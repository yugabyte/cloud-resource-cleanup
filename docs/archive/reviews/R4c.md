# R4c review

Scope reviewed: `git diff 088ee7c..7617400` (commit `7617400`, R4c AWS KMS cleaner in
`crc/aws/kms.py`, package READMEs, `pyproject.toml`, `docs/refactor/BUGS.md`,
`docs/refactor/FILTER_VARIANTS.md`, `docs/refactor/STATUS.md`, and tests in
`tests/legacy/aws_kms.txt`, `tests/test_aws_kms_parity.py`, `tests/test_aws_kms.py`) against
`git show main:crc/aws/kms.py`, `AGENTS.md`, `docs/refactor/BUGS.md`, and
`.cursor/skills/crc-python-refactor/REVIEW_CHECKLIST.md`.

## 1. Behaviour parity with `git show main:crc/aws/kms.py`

Compared `crc/aws/kms.py` against `git show main:crc/aws/kms.py`:

- **SDK calls (order and arguments)** (`crc/aws/kms.py:72-74`, `:157-173`, `:191-197`, `:204-234`, `:323-335` vs `main:crc/aws/kms.py:113-192`):
  - Single-region in `us-west-2` (`BUG-059`); `connect_and_list()` calls `boto3.client("kms", region_name="us-west-2")`, `client.get_paginator("list_keys")`, and iterates `paginator.paginate()` extending `page["Keys"]`.
  - For each key in `kms_keys`, calls `client.list_resource_tags(KeyId=key_id)`, then (if filter and exception checks pass) `client.describe_key(KeyId=key_id)`, then (if `KeyState == "Enabled"`, `kms_key_description in key_des`, and `is_old(...)` pass) `client.get_key_policy(KeyId=key_id, PolicyName="default")`.
  - When `not self.dry_run`, calls `client.schedule_key_deletion(KeyId=cmk_id, PendingWindowInDays=self.kms_pending_window)` for each entry in `active` in order (including duplicate entries when a policy has multiple matching statements, `BUG-056`).
- **Filter and age decisions** (`crc/aws/kms.py:108-120`, `:203-246` vs `main:crc/aws/kms.py:77-99`, `:134-180`):
  - `_has_all_pairs` builds `response_set = {(tag["TagKey"], tag["TagValue"]) for tag in response_tags}` and `filter_set = {(k, v) for k, values in spec.items() for v in values}` as on `main`, and evaluates subset membership via `crc.core.policy.filter_tags_exact_pairs` (`BUG-007`, `BUG-014`).
  - `_should_skip` logs `"Exception tags and not present"` when `exception_tags` is empty/falsy (`BUG-058`) and otherwise evaluates `{tag["Key"]: tag["Value"]}` per tag with `crc.core.policy.exception_tags_any` (`BUG-015`).
  - Age uses `crc.core.age.get_retention_age` (fail-open, looks up `"Key"` so `TagKey` dicts never match, `BUG-004`, `BUG-008`), `crc.core.policy.effective_age`, and `crc.core.age.is_old` (`SAF-006` to `SAF-010`).
- **Log lines and printed messages** (`crc/aws/kms.py:99-105`, `:166-171`, `:178`, `:184`, `:185`, `:197`, `:216`, `:233`, `:239` vs `main:crc/aws/kms.py:86`, `:119-124`, `:127`, `:159`, `:177-179`, `:182`, `:185`, `:192`, `:197-205`):
  - Every `logging.debug`, `logging.info`, `logging.warning`, and `logging.error` message is identical to `main`.
- **Errors and dry-run path** (`crc/aws/kms.py:149-155`, `:165-172`, `:180-184`, `:191-197`, `:326-335` vs `main:crc/aws/kms.py:118-125`, `:130-183`, `:187-195`):
  - `CONNECTIVITY_ERRORS` during client creation or `list_keys` pagination log `"KMS cleanup skipped — cannot reach region %s (%s): %s"` and return without logging a summary (`SAF-001`). Non-connectivity list errors (`ClientError`, `KeyError('Keys')`) propagate (`SAF-004`).
  - Per-key loop catches `BaseException` (matching `main`'s bare `except:`), logging `f"KEY SKIPPED {key['KeyId']}"`; if `"KeyId"` itself is absent from `key`, `KeyError('KeyId')` escapes from the `except` block as on `main`; a key already appended to `active` before a later policy statement raises stays in `active` (`BUG-055`).
  - Errors during `schedule_key_deletion` propagate immediately before `kms_keys_to_delete.extend` and before the summary log (`BUG-056`, `BUG-057`).
  - In dry run, `schedule_key_deletion` is never called, matching key IDs are recorded in `kms_keys_to_delete` / `Plan.candidates`, and the dry-run summary warning is logged.

## 2. Oracle and parity test verification (`tests/legacy/aws_kms.txt`, `tests/test_aws_kms_parity.py`)

- Diffed `tests/legacy/aws_kms.txt` against `git show main:crc/aws/kms.py`: byte-for-byte identical (SHA-256 `8ff9974a2961927ead4afdcbd6f917a82ff7939fc6ec64a542ff5a0e39bbfbcf`, 0 lines of diff).
- Verified `tests/test_aws_kms_parity.py` executes both the oracle (`tests/legacy/aws_kms.txt`) and the refactored code (`Kms` and registered `KmsCleaner`) against the same `FakeAws` / `FakeKms` fakes and compares ordered SDK `calls`, `error`, `deleted`, `logs`, and `stdout`.
- Tested a deliberate one-line behaviour change in `crc/aws/kms.py` (`key_state != "Enabled"` in `_select_key`), confirmed `pytest -q tests/test_aws_kms_parity.py` failed immediately, and reverted it.

## 3. Architecture and guardrails

- `KmsCleaner` implements `Cleaner` (`cloud = "aws"`, `kind = "kms"`, `supports`, read-only `plan`, mutating `apply`) and is registered via `@register("aws", "kms")` on `build_kms_cleaner`.
- `plan()` never mutates (`ARCH-005` passes on `crc/aws/kms.py` as a non-legacy module; `schedule_key_deletion` is only in `schedule`, called from `apply` and `Kms.delete`).
- Filtering uses `crc.core.policy.filter_tags_exact_pairs`, `crc.core.policy.exception_tags_any`, and `crc.core.policy.effective_age` as named in `docs/refactor/FILTER_VARIANTS.md`; age uses `crc.core.age.get_retention_age` and `crc.core.age.is_old`; no `re`, `os.environ`, `print`, or `sys.exit` in `crc/aws/kms.py` (`ARCH-003`).
- SDK is behind the typed `KmsClient` and `KmsPaginator` `Protocol`s.
- `crc/aws/kms.py` (`crc.aws.kms`) left both legacy ratchet lists in `pyproject.toml`; nothing was added to the ratchet (`ARCH-001`), and the `boto3`/`botocore` `ignore_missing_imports = true` override is per-SDK.

## 4. Scope and documentation

- `crc.py` and all other cleaners are untouched.
- `crc/aws/README.md`, `crc/core/README.md`, `tests/README.md`, and `tests/legacy/README.md` are accurate and pass `ARCH-002` symbol sync (`KmsClient`, `KmsPaginator`, `KmsRuntime`, `KmsSettings`, `KmsCleaner`, `build_kms_cleaner()`, `Kms`).

## 5. Verification of `BUG-055` to `BUG-058` against `main` (plus `BUG-059`)

| ID | Risk | `main` location | Verified via `git show main:crc/aws/kms.py` | Real-world effect |
|----|------|-----------------|---------------------------------------------|-------------------|
| `BUG-055` | medium | `crc/aws/kms.py:174-183` (append `:180`, schedule `:189-191`) | Confirmed (`active_keys.append(keys["KeyId"])` runs at `:180` inside `for ids in policy_json["Statement"]:`; a later statement without `Principal.AWS` raises `KeyError`, caught by `:181-183` which logs `KEY SKIPPED` while the key stays in `active_keys` and is scheduled at `:189-191`) | Key logged as `KEY SKIPPED` is still scheduled for deletion (`deletes what it should keep`). |
| `BUG-056` | low | `crc/aws/kms.py:174-180`, `:188-192` | Confirmed (`for ids in policy_json["Statement"]:` appends `keys["KeyId"]` on every matching statement without `break`; `:188-192` calls `schedule_key_deletion` for each entry) | Duplicate `schedule_key_deletion` call on the same key fails on the second call (`KMSInvalidStateException`) and aborts the run (`run fails`). |
| `BUG-057` | low | `crc/aws/kms.py:188-196` | Confirmed (`schedule_key_deletion` loop at `:188-192` has no `try`/`except`, and `self.kms_keys_to_delete.extend(list(active_keys))` at `:195` runs only after the loop) | A scheduling error mid-loop loses the record of already-scheduled keys from `get_deleted`, Slack, and InfluxDB (`cosmetic`). |
| `BUG-058` | low | `crc/aws/kms.py:84-86`, called `:147` | Confirmed (`_should_skip_kms` logs `logging.warning("Exception tags and not present")` at `:86` whenever `not self.exception_tags`, called per key at `:147`) | Logs a garbled warning for every key when `exception_tags` is empty or `None` (`cosmetic`). |
| `BUG-059` | low | `crc/aws/kms.py:10`, `:21`, `:113` | Added in review (`get_all_regions` is imported at `:10` and never called; `:113` connects only to `default_region_name = "us-west-2"`) | KMS keys outside `us-west-2` are never cleaned (`keeps what it should delete`). |

## BLOCKING (fixed)

1. **`_has_all_pairs` built a `list` of pairs instead of a `set` (`response_set`) and lazily iterated `filter_tags` instead of eagerly building `filter_set` (`main:crc/aws/kms.py:134-142`)** (`crc/aws/kms.py:108-120`, `:205-207`, `tests/test_aws_kms_parity.py:196-209`, `:711-718`):
   - On `main`, an unhashable `TagKey` or `TagValue` raises `TypeError` when constructing `response_set` (skipping the key with `KEY SKIPPED`), whereas the refactor built a `list` and scheduled the key for deletion when `filter_tags` and `exception_tags` were empty. Similarly, when an earlier `filter_tags` entry failed to match and a later `filter_tags` entry had a non-iterable value (such as `None`), `main` raised `TypeError` while building `filter_set` and logged `KEY SKIPPED`, whereas `_has_all_pairs` short-circuited silently.
   - Updated `_has_all_pairs` to construct `response_set` and `filter_set` eagerly as on `main` before checking pairs via `policy.filter_tags_exact_pairs`, and added `filter-non-iterable-second-key-after-mismatch-skips-with-warning` and `tag-unhashable-value-skips` to `CASES` and `QUIRKS` in `tests/test_aws_kms_parity.py`.
2. **`docs/refactor/BUGS.md` `## Counts` was not updated after adding `BUG-055`..`BUG-058`** (`docs/refactor/BUGS.md:90-94`):
   - `## Counts` still read `54 rows: critical 2, high 7, medium 11, low 34. All open.` Updated to `59 rows: critical 2, high 7, medium 12, low 38. All open.` (including `BUG-059`).

## NIT (left)

- If `KmsCleaner.apply()` is called directly with `dry_run=True`, the dry-run summary warning is logged a second time after `plan()`. `crc.core.engine.run` never calls `apply()` in dry run (`crc/core/engine.py:99-100`).
- `KmsRuntime.clock` defaults to `datetime.datetime.now` (as a function reference, not a call, so `ARCH-003` passes) to keep `Kms()` backwards-compatible until `crc.py` is wired to `engine.run` in the CLI rung (noted in `docs/refactor/STATUS.md`).

## 6. Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` (`crc`, `tests`) | Success: no issues found in 83 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | 2703 passed in 7.82s |

Test count: 2403 before R4c (`088ee7c`), 2697 on `7617400`, 2703 after review additions (+6).

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff 088ee7c..HEAD` (`7617400` plus round 1 `710bf75`) against `git show main:crc/aws/kms.py`,
`AGENTS.md`, `ARCHITECTURE.md`, `docs/refactor/BUGS.md` and `REVIEW_CHECKLIST.md`.

### Round 1 fixes assessment

- `_has_all_pairs` (`crc/aws/kms.py:108-119`): correct. It now builds `response_set` then `filter_set`
  in the order of `main:crc/aws/kms.py:134-142`, so an unhashable tag value or a non-iterable filter
  value raises inside the per-key `try` exactly as on `main`. The pair check
  `{k: v}.get(wanted_key) == wanted` over the set equals `filter_set.issubset(response_set)` for every
  hashable value (an empty `filter_set` gives `True` in both). Verified by reverting to the lazy
  list form: exactly the 6 new tests fail (`filter-non-iterable-second-key-after-mismatch-skips-with-warning`
  and `tag-unhashable-value-skips` in `test_matches_main`, `test_cleaner_protocol_matches_main`,
  `test_quirk_is_preserved`); restored.
- Round 1 test changes are additions only (2 `CASES`, 2 `QUIRKS`, one extra `client` region assertion);
  no existing assertion was removed or relaxed. `BUGS.md` counts (59 rows: 2/7/12/38) were correct.

### Independent review

- Oracle `tests/legacy/aws_kms.txt` is byte-identical to `git show main:crc/aws/kms.py`.
- Parity: SDK call order and arguments, `_should_skip` key-before-value evaluation
  (`{tag["Key"]: tag["Value"]}` matches `main:crc/aws/kms.py:90-91`), fail-open custom age via
  `core_age._find_tag_value` (same `.get("Key")` lookup as `main:crc/service.py:302`),
  `retention_age or age`, clock call after the description test, the `KeyError('KeyId')` escape from the
  `except` block, accumulation over repeated `delete()` calls and the dry-run/real summary lines all
  match `main`. The harness compares ordered calls, logs (DEBUG), stdout, errors and `get_deleted`
  for both entry points, so the tests assert outcomes and are not vacuous.
- Safety (`CLEANUP_SAFETY.md` SAF-001, SAF-004, SAF-006 to SAF-010): connectivity errors skip the region,
  other list errors propagate, no age gate changed, `MAX_AGE` still applied through `core_age.is_old`
  (case `max-age-shortens-threshold`). `plan()` makes no mutating call; `schedule_key_deletion` is
  only in `KmsCleaner.schedule` (`crc/aws/kms.py:192-198`), reached from `apply()` and `Kms.delete()`.
- Architecture: `crc/aws/kms.py` removed from both ratchet lists, nothing added; decorator
  registration; injected client factory and clock; typed Protocols at the SDK edge; README symbol
  lists in sync (ARCH-002 green). `crc.py` unchanged.

### BLOCKING (fixed)

None.

### NIT (left)

- `KmsCleaner.apply()` returns `Result(succeeded=ids)` in a dry run although nothing was scheduled
  (`crc/aws/kms.py:152-156`); only reachable by calling `apply()` directly, since
  `crc/core/engine.py:100` skips `apply()` in a dry run. Same root as round 1's double-log nit.
- `BUG-060` and `BUG-061` were already pinned by parity cases (`policy-list-principal-never-equals`,
  `policy-string-principal-skips`, `policy-keyboard-interrupt-is-swallowed` in
  `tests/test_aws_kms_parity.py`) but not recorded; now added to `docs/refactor/BUGS.md` (61 rows:
  critical 2, high 7, medium 12, low 40) and referenced from `crc/aws/README.md`.

### New bugs recorded (verified with `git show main:crc/aws/kms.py`)

| ID | `main` location | Effect | Risk | Rung |
|----|-----------------|--------|------|------|
| `BUG-060` | `crc/aws/kms.py:174-176`, `:181-183` | `Principal.AWS` as a list never equals `kms_user`; `"Principal": "*"` raises `TypeError` and the key is skipped (keeps what it should delete) | low | R4c (round 2) |
| `BUG-061` | `crc/aws/kms.py:181-183`, `:187-192` | bare `except:` swallows `KeyboardInterrupt`; the run goes on and schedules the keys already selected (deletes what it should keep) | low | R4c (round 2) |

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check` (Python 3.12):
`ruff check .` passed, `mypy` no issues, `lint-imports` 5 kept / 0 broken, `pytest -q` 2703 passed.

Test count: 2703 (unchanged by round 2; 2403 before R4c).

ROUND 2 VERDICT: APPROVE
