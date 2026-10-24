# R4b review

Scope reviewed: `git diff 7f98d3a..2d1dd05` (commit `2d1dd05`, R4b AWS Elastic IP cleaner in
`crc/aws/elastic_ips.py`, package READMEs, `pyproject.toml`, `docs/refactor/BUGS.md`,
`docs/refactor/FILTER_VARIANTS.md`, `docs/refactor/STATUS.md`, and tests in
`tests/legacy/aws_elastic_ips.txt`, `tests/test_aws_ip_parity.py`,
`tests/test_aws_elastic_ips.py`) against `git show main:crc/aws/elastic_ips.py`, `AGENTS.md`,
`docs/refactor/BUGS.md`, and `.cursor/skills/crc-python-refactor/REVIEW_CHECKLIST.md`.

## 1. Behaviour parity with `git show main:crc/aws/elastic_ips.py`

Compared `crc/aws/elastic_ips.py` against `git show main:crc/aws/elastic_ips.py`:

- **SDK calls (order and arguments)** (`crc/aws/elastic_ips.py:49-55`, `:144`, `:172`, `:271-277` vs `main:crc/aws/elastic_ips.py:117-144`):
  - `get_all_regions("ec2", "us-west-2")` runs first (calling `boto3.client("ec2", region_name="us-west-2").describe_regions()`, falling back to `boto3.session.Session().get_available_regions("ec2")` on `CONNECTIVITY_ERRORS` per `SAF-001` and propagating `ClientError` auth failures per `SAF-004`).
  - For each region, `ElasticIPs.delete()` creates `boto3.client("ec2", region_name=region)`, calls `client.describe_addresses()["Addresses"]`, selects unassociated tagged addresses in response order (last `AllocationId` wins for duplicate `PublicIp` keys in the dict), and when `not self.dry_run` calls `client.release_address(AllocationId=...)` in insertion order before advancing to the next region (`BUG-051`).
- **Filter and age decisions** (`crc/aws/elastic_ips.py:81-84`, `:147-159`, `:182-201` vs `main:crc/aws/elastic_ips.py:81-109`, `:124-139`):
  - Only addresses with `"NetworkInterfaceId" not in eip and "Tags" in eip` are considered (`BUG-020`).
  - No age gate exists (`BUG-053`).
  - `_should_skip` evaluates `crc.core.policy.aws_eip_should_skip` one tag at a time so early return on the first matching exception tag, `KeyError` on missing `"Key"` or `"Value"`, and `TypeError("'bool' object is not iterable")` when `notags` is non-empty (`BUG-011`) match `main`.
  - `_matches_filter` evaluates `crc.core.policy.filter_tags_any_key` one tag at a time (or returns `True` immediately when `filter_tags` is empty/falsy).
- **Log lines and printed messages** (`crc/aws/elastic_ips.py:66-78`, `:173`, `:252` vs `main:crc/aws/elastic_ips.py:78`, `:144`, `:149`, `:152-160`):
  - Every `logging.info` and `logging.warning` message is identical to `main`: `Deleted IP: {ip}`, `log_skipped_region(region, "elastic IP cleanup", e)`, `count of items in deleted_ips: {count}`, and the summary warnings (`number of AWS Elastic IPs deleted: ...` / `List of AWS Elastic IPs deleted: ...` and dry-run `List of AWS Elastic IPs (Total: ...) which will be deleted: ...`).
- **Errors and dry-run path** (`crc/aws/elastic_ips.py:117-119`, `:136-137`, `:170-174`, `:279-280` vs `main:crc/aws/elastic_ips.py:141-150`):
  - `CONNECTIVITY_ERRORS` skip the region and log via `log_skipped_region`; a connectivity error mid-region during `release_address` aborts the region before `deleted_ips.extend` runs, losing the record of addresses already released in that region (`BUG-052`).
  - Non-connectivity errors (`ClientError`, `KeyError` on missing `"Addresses"`/`"AllocationId"`/`"PublicIp"`/`"Key"`/`"Value"`, `TypeError` from `notags` or `Tags: None`) propagate immediately (`BUG-051`).
  - In dry run, `release_address` is never called, matching public IPs are recorded in `deleted_ips` / `Plan.candidates`, and the dry-run summary warning is logged.

## 2. Oracle and parity test verification (`tests/legacy/aws_elastic_ips.txt`, `tests/test_aws_ip_parity.py`)

- Diffed `tests/legacy/aws_elastic_ips.txt` against `git show main:crc/aws/elastic_ips.py`: byte-for-byte identical (SHA-256 `7bf96eb6caae2068a232267df0853a5278d427fdc8a19a67ee80c8bbc5a78657`, 0 lines of diff).
- Verified `tests/test_aws_ip_parity.py` executes both the oracle (`tests/legacy/aws_elastic_ips.txt`) and the refactored code (`ElasticIPs` and registered `ElasticIpCleaner`) against the same `FakeAws` / `FakeEc2` fakes and compares ordered SDK `calls`, `error`, `deleted`, `count`, `logs`, and `stdout`.
- Tested a deliberate one-line behaviour change in `crc/aws/elastic_ips.py` (`if not self._filter_tags: return False` in `_matches_filter`), confirmed `pytest -q tests/test_aws_ip_parity.py` failed immediately, and reverted it.

## 3. Architecture and guardrails

- `ElasticIpCleaner` implements `Cleaner` (`cloud = "aws"`, `kind = "ip"`, `supports`, read-only `plan`, mutating `apply`) and is registered via `@register("aws", "ip")` on `build_elastic_ip_cleaner`.
- `plan()` never mutates (`ARCH-005` passes on `crc/aws/elastic_ips.py` as a non-legacy module; `release_address` is only in `apply_region`).
- Filtering uses `crc.core.policy.filter_tags_any_key` and `crc.core.policy.aws_eip_should_skip` as named in `docs/refactor/FILTER_VARIANTS.md`; no `re`, `datetime.now`, `os.environ`, `print`, or `sys.exit` in `crc/aws/elastic_ips.py` (`ARCH-003`).
- SDK is behind the typed `ElasticIpClient` `Protocol` (`describe_addresses`, `release_address`).
- `crc/aws/elastic_ips.py` (`crc.aws.elastic_ips`) left both legacy ratchet lists in `pyproject.toml`; nothing was added to the ratchet (`ARCH-001`), and the `boto3`/`botocore` `ignore_missing_imports = true` override is per-SDK.

## 4. Scope and documentation

- `crc.py` and all other cleaners are untouched.
- `crc/aws/README.md`, `crc/core/README.md`, `tests/README.md`, and `tests/legacy/README.md` are accurate and pass `ARCH-002` symbol sync (`ElasticIpClient`, `ElasticIpRuntime`, `ElasticIpCleaner`, `build_elastic_ip_cleaner()`, `ElasticIPs`).

## 5. Verification of `BUG-051` to `BUG-053` against `main`

| ID | Risk | `main` location | Verified via `git show main:crc/aws/elastic_ips.py` | Real-world effect |
|----|------|-----------------|-----------------------------------------------------|-------------------|
| `BUG-051` | low | `crc/aws/elastic_ips.py:119-147`, `:152-160` | Confirmed (per-region loop describes at `:123` and releases at `:143`; `:148-150` catches only `CONNECTIVITY_ERRORS`, so any other error aborts after earlier regions were released and before `:152-160` summary logs) | Partial releases across regions on non-connectivity error without summary logs (`cosmetic`). |
| `BUG-052` | low | `crc/aws/elastic_ips.py:141-147`, `:148-150` | Confirmed (`self.deleted_ips.extend(list(eips_to_delete.keys()))` at `:147` runs only after the `:142-144` release loop; `except CONNECTIVITY_ERRORS` at `:148-150` skips the region) | Connectivity error mid-region loses the record of already-released IPs in that region from `get_deleted`, Slack, and InfluxDB (`cosmetic`). |
| `BUG-053` | medium | `crc/aws/elastic_ips.py:125-144` (no age check before `release_address` at `:143`) | Confirmed (`:125-139` checks only `NetworkInterfaceId`, `Tags`, `_should_skip_instance`, and `filter_tags` before releasing at `:143`) | Newly allocated unassociated Elastic IPs matching the tag filter are released immediately (`deletes what it should keep`). |

## BLOCKING (fixed)

1. **`_pair` defaulted a missing tag `"Value"` to `""` (`tag.get("Value", "")`) instead of raising `KeyError('Value')` when the key matched `filter_tags` or `exception_tags` with a non-empty value list (`main:crc/aws/elastic_ips.py:94-97`, `:135-138`)** (`crc/aws/elastic_ips.py:81-84`, `:186`, `:198`, `tests/test_aws_ip_parity.py:267-276`, `:518-522`, `:546`):
   - Updated `_pair(tag, spec)` to evaluate `tag["Value"]` when `spec and key in spec and spec[key]`, matching `main`'s short-circuit evaluation (`not spec[key] or tag["Value"] in spec[key]`).
   - Added `tag-key-without-value-value-filter-raises` and `tag-key-without-value-exception-value-filter-raises` to `CASES` and `QUIRKS`, and `[{"Key": "owner"}]` to `SKIP_TAGS` in `tests/test_aws_ip_parity.py`.
2. **`test_cleaner_protocol_matches_main` only asserted `new_releases == old_releases` on `ClientError` or `release*` error cases, and `tests/test_aws_elastic_ips.py` lacked a direct `ElasticIpCleaner.apply` connectivity-error unit test** (`tests/test_aws_ip_parity.py:479-480`, `tests/test_aws_elastic_ips.py:135-141`):
   - Changed the `elif` in `test_cleaner_protocol_matches_main` to `else: assert new_releases == old_releases` so every non-`fails_in_plan` error case verifies release calls.
   - Added `test_apply_skips_region_on_connectivity_error` in `tests/test_aws_elastic_ips.py`.
3. **`BUG-053` (risk `medium`) was appended at the end of the `low` group in `docs/refactor/BUGS.md`, violating Rule 4 ("inserted at the end of its risk group, so the table stays sorted by risk")** (`docs/refactor/BUGS.md:51`):
   - Moved `BUG-053` to the end of the `medium` risk group right after `BUG-045`.

## NIT (left)

- If `ElasticIpCleaner.apply()` is called directly with `dry_run=True`, the dry-run summary warning is logged a second time after `plan()`. `crc.core.engine.run` never calls `apply()` in dry run (`crc/core/engine.py:99-100`).

## 6. Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` (`crc`, `tests`) | Success: no issues found in 81 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | 2397 passed in 6.26s |

Test count: 2201 before R4b (`7f98d3a`), 2383 on `2d1dd05`, 2397 after review additions (+14).

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff 7f98d3a..ed11b07` (R4b commit plus round 1 fixes), against
`git show main:crc/aws/elastic_ips.py`, `crc/core/policy.py`, `AGENTS.md`, `ARCHITECTURE.md`,
`docs/refactor/BUGS.md`, `CLEANUP_SAFETY.md` and `REVIEW_CHECKLIST.md`.

### Round 1 fixes assessment

- `_pair` lazy `Value` read (`crc/aws/elastic_ips.py`, `_pair`): correct. It reads `tag["Value"]`
  exactly when `main` does (`main:crc/aws/elastic_ips.py:95-98`, `:135-137`: the spec is set, the key
  is in the spec, and its value list is truthy), and `policy.matches_any_key` on the single-key
  pair gives the same `in` test as `main`, including substring matching when a spec value is a string.
  No behaviour change in other paths.
- `elif` to `else` in `test_cleaner_protocol_matches_main`: makes the check stricter, nothing weakened.
  The new `test_apply_skips_region_on_connectivity_error` asserts an outcome (`succeeded == ("2.2.2.2",)`).
- `BUG-053` moved into the `medium` group: correct, counts line matches (medium 11).
- Oracle `tests/legacy/aws_elastic_ips.txt` re-checked with `cmp` against `git show main:crc/aws/elastic_ips.py`: identical.

### BLOCKING (fixed)

1. **Error order differed from `main` for an address that matches the filter, has no `AllocationId`,
   and has a malformed later tag.** `main` stores the address at the first matching tag
   (`main:crc/aws/elastic_ips.py:139`, reading `AllocationId`, then `PublicIp`) and still reads every
   later tag (`:132-138`). The refactor read all tags first and `AllocationId` afterwards, so it raised
   `KeyError('Key')` or `KeyError('Value')` where `main` raises `KeyError('AllocationId')`. Fix:
   `ElasticIpCleaner._select_on_filter` (`crc/aws/elastic_ips.py`) replaces `_matches_filter` and
   records the address at each matching tag, the same way `main` does. Added cases
   `missing-allocation-id-then-tag-without-key` and `missing-allocation-id-then-tag-without-value`
   to `CASES` and `QUIRKS` in `tests/test_aws_ip_parity.py`. All 6 new parametrised checks fail on the
   pre-fix code and pass with the fix. Other shapes probed against the oracle gave the same results:
   string specs, `Value: None` with a string spec, `Tags: None` or a string, and a missing `PublicIp`.

### NIT (left)

- Round 1's NIT stands: `ElasticIpCleaner.apply()` called with `dry_run=True` logs the dry-run summary
  a second time. `crc.core.engine.run` does not call `apply` in a dry run.
- `ElasticIpCleaner.should_skip` is public only so the adapter can keep `_should_skip_instance`.
  That is acceptable, but it is not listed in the `crc/aws/README.md` Public API (ARCH-002 checks
  only classes and functions at module level).

### New bugs

- `BUG-054` (low, run fails): an unassociated, tagged address without `PublicIp` (for example a
  Wavelength carrier IP) that passes the filter raises `KeyError('PublicIp')` at
  `main:crc/aws/elastic_ips.py:130` / `:139` and aborts the run. The refactor preserves this and
  `missing-public-ip` pins it. `BUGS.md` now has 54 rows (low 34); `crc/aws/README.md` cites
  `BUG-051` to `BUG-054`.

### Checklist

Safety: no age gate exists on `main` (BUG-053) and none was added or weakened. `describe_regions`
auth errors still propagate (SAF-004, `describe-regions-client-error-propagates`). Connectivity
errors fall back or skip as on `main` (SAF-001). No safety test was changed. Architecture: `plan()`
only calls `describe_addresses`, and `release_address` is only in `apply_region`. Registration uses
the decorator. The module left both ratchet lists and nothing was added to them. `crc.py`,
`requirements.txt` and the guardrail files are untouched. No new dependency.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check` (Python 3.12):

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` | Success: no issues found in 81 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | 2403 passed |

Test count: 2397 after round 1, 2403 after round 2 (+6).

ROUND 2 VERDICT: APPROVE
