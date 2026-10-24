## Coding summary

Lane W2-AWS-A, structure only, no behaviour change. Every owned file is now at most 250 lines (`wc -l`). All five flat modules except `disk/cleaner.py` became subpackages whose `__init__.py` re-exports every name the old module exported, so `crc.py`, `crc/cli` and the tests import from the same paths. No test, oracle or guardrail file was edited.

### Files split (before -> after, lines)

| Old file | Before | After |
|----------|-------:|-------|
| `crc/aws/keypairs.py` | 279 | `keypairs/cleaner.py` 162, `adapter.py` 75, `client.py` 45, `__init__.py` 20 |
| `crc/aws/elastic_ips.py` | 301 | `elastic_ips/cleaner.py` 178, `adapter.py` 84, `client.py` 43, `__init__.py` 20 |
| `crc/aws/kms.py` | 378 | `kms/cleaner.py` 207, `adapter.py` 90, `client.py` 65, `tags.py` 33, `settings.py` 22, `__init__.py` 22 |
| `crc/aws/snapshot.py` | 373 | `snapshot/cleaner.py` 192, `adapter.py` 91, `protection.py` 61, `client.py` 52, `__init__.py` 20 |
| `crc/aws/disk/cleaner.py` | 277 | `disk/cleaner.py` 217, `guard.py` 66 (region error policy, summary lines), `client.py` 86 (now holds `DiskRuntime`) |

Legacy adapter classes (`KeyPairs`, `ElasticIPs`, `Kms`, `Snapshot`) each sit alone in `adapter.py`. `Disk` already lived in `disk/__init__.py`, so it was not moved.

### Helpers extracted

New `crc/aws/_common.py` (50 lines): `ec2_regions` (was `_all_regions` in 3 files), `skip_region` (was `_skip_region` in 3 files, `_regionlog.region_skipped` + `log_skipped_region`), `group_by_region` (the by-region grouping in `apply()` of 4 cleaners), `log_deleted_summary` (the three identical summary-line functions of key pairs, Elastic IPs and KMS). `disk/guard.py` holds `region_guard` and `log_summary` (moved out of `DiskCleaner`; the methods stay and delegate, so `Disk` is unchanged).

Patch targets: tests patch only `boto3.client`, `boto3.session.Session`, `crc.service.init_logging`, `crc.aws._regionlog.clock` and `crc.aws.disk.boto3.client` / `crc.aws.disk.get_all_regions`; none of them moved. `boto3.client(...)` is still looked up on the `boto3` module at call time in every new `client.py`. The legacy private helper names used by the oracle tests (`Snapshot._get_snapshots_used_by_amis`, `_is_protected`, `Kms._should_skip_kms`, `ElasticIPs._should_skip_instance`) are kept.

### Tests and runtime

- `pytest -q` at base `cea0a8a`: 43010 passed (2m39s).
- `pytest -q` after: 43010 passed (same count).
- `make check` runtime: 2m43s wall (pytest 2m37s).
- ruff, mypy (strict), lint-imports (5 kept) and ARCH-001..005 / SAF-LOCK are green.

## Owner action

- Cross-cloud candidates (not touched, `crc/core` and other clouds are out of lane): the `_log_summary` "number of X deleted / List of X deleted / will be deleted" triple exists with the same shape in every cloud; the `by_region` grouping loop is probably repeated in `crc/azu`, `crc/gcp`, `crc/oci` cleaners. A `crc/core` helper for both would remove the AWS-only `_common.log_deleted_summary` and `group_by_region`.
- Lane W2-AWS-B may reuse `crc.aws._common.group_by_region`, `skip_region` and `ec2_regions`; `crc/aws/vm`, `vpc` and `spot_instance_requests` still have their own copies (`Candidate` grouping, `get_all_regions("ec2", "us-west-2")`). Not touched here.
- Still above 250 (other lane): `spot_instance_requests/cleaner.py` 296, `spot_instance_requests/volumes.py` 320, `vm/cleaner.py` 380, `vpc/cleaner.py` 383.

## New bugs

None found. Observation only, not changed: `kms/cleaner.py` `plan_keys` catches `BaseException` (also `KeyboardInterrupt`), inherited from the bare `except:` of `main` (already BUG-055 area).

## Round 1 (Argon)

Scope reviewed: `git diff cea0a8a..HEAD` (`crc/aws/_common.py`, `crc/aws/disk/{cleaner,client,guard,README}`, `crc/aws/elastic_ips/**`, `crc/aws/keypairs/**`, `crc/aws/kms/**`, `crc/aws/snapshot/**`, `crc/aws/README.md`, `docs/refactor/reviews/W2-AWS-A.md`).

### Verification

1. **No behaviour change (hunk-by-hunk comparison against `cea0a8a`):**
   - `crc/aws/_common.py` (50 lines): extracts exact shared implementations of `ec2_regions` (`get_all_regions("ec2", "us-west-2")`), `skip_region` (`_regionlog.region_skipped` + `log_skipped_region`), `group_by_region` (`setdefault(candidate.region, []).append(candidate)`), and `log_deleted_summary` (identical warning lines for `"keypairs"`, `"Elastic IPs"`, `"KMS"`).
   - `crc/aws/disk`: `DiskRuntime` moved from `cleaner.py` to `client.py` (re-exported in `cleaner.py` and `crc.aws.disk`); `region_guard` and `log_summary` moved from `DiskCleaner` into `guard.py` with `DiskCleaner.region_guard` and `DiskCleaner.log_summary` delegating unchanged; `apply()` uses `group_by_region(plan.candidates)`. `crc/aws/disk/__init__.py` (`Disk` adapter) is untouched.
   - `crc/aws/keypairs`, `crc/aws/elastic_ips`, `crc/aws/kms`, `crc/aws/snapshot`: split into subpackages with `client.py` (Protocols, `boto3_client`, `*Runtime`), `cleaner.py` (`*Cleaner` and `@register` factory), `adapter.py` (legacy `Service` subclass alone in its module), plus `kms/settings.py` (`KmsSettings`), `kms/tags.py` (`NotOursError`, `tag_pairs`, `matches_filter`), and `snapshot/protection.py` (`is_tag_list`, `read_tags`, `is_protected`, `used_by_amis`). Every condition, default, SDK call order, exception handler, and log message is identical to `cea0a8a`.
2. **Import paths and monkeypatch/mock targets:**
   - Every symbol previously exported by `crc.aws.disk`, `crc.aws.elastic_ips`, `crc.aws.keypairs`, `crc.aws.kms`, and `crc.aws.snapshot` (and `crc.aws.disk.cleaner`) is re-exported at the same import path via `__init__.py` (`__all__`).
   - Verified all test patch targets (`grep -rn "monkeypatch\.setattr\|mock\.patch" tests`): `boto3.client`, `boto3.session.Session`, `crc.service.init_logging`, `crc.aws._regionlog.clock`, `crc.aws.disk.boto3.client`, and `crc.aws.disk.get_all_regions` all resolve and take effect identically (`boto3.client` is looked up on the `boto3` module at call time in every `client.py`, and `crc/aws/disk/__init__.py` is unchanged).
3. **Tests and safety locks:**
   - Zero test files, `tests/legacy/*.txt` oracles, `tests/archtest/**`, or hash-locked safety tests (`safety_lock.json`) were modified.
4. **Line counts (`wc -l`, all <= 250) and seam quality:**
   - `_common.py`: 50
   - `disk/`: `cleaner.py` 217, `client.py` 86, `guard.py` 66 (untouched: `__init__.py` 178, `detach.py` 201, `settings.py` 99, `volumes.py` 214)
   - `elastic_ips/`: `__init__.py` 20, `adapter.py` 84, `cleaner.py` 178, `client.py` 43
   - `keypairs/`: `__init__.py` 20, `adapter.py` 75, `cleaner.py` 162, `client.py` 45
   - `kms/`: `__init__.py` 22, `adapter.py` 90, `cleaner.py` 207, `client.py` 65, `settings.py` 22, `tags.py` 33
   - `snapshot/`: `__init__.py` 20, `adapter.py` 91, `cleaner.py` 192, `client.py` 52, `protection.py` 61
5. **READMEs and ARCH-002:**
   - `crc/aws/README.md`, `crc/aws/disk/README.md`, `crc/aws/elastic_ips/README.md`, `crc/aws/keypairs/README.md`, `crc/aws/kms/README.md`, and `crc/aws/snapshot/README.md` are short and in sync with `ARCH-002`.

### Findings

- **BLOCKING:** none.
- **NIT (fixed):** `crc/aws/README.md:7` had a double comma (`W2-AWS-A,,`); `crc/aws/kms/README.md:33` and `crc/aws/snapshot/README.md:31` named `apply_region()` instead of `schedule()` and `delete_one()`. Fixed in place.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed! |
| `mypy` | Success: no issues found in 268 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` (incl. `ARCH-001..005`, `SAF-LOCK`) | 43010 passed in 154.61s |

- Test count: 43010 passed at base `cea0a8a`, 43010 passed at `HEAD` (unchanged).
- `make check` runtime: 2m38.5s wall (pytest 2m34.6s).

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff cea0a8a..HEAD` (commits `9bd55c4` coder, `e1a6d84` round 1). Round 1 fixes (README double comma, `apply_region()` naming in kms/snapshot READMEs) checked: present and correct. Round 1 findings re-done independently below.

### Checks

1. **No behaviour change.** Compared every function body of the base modules (`keypairs.py`, `elastic_ips.py`, `kms.py`, `snapshot.py`, `disk/*.py` at `cea0a8a`) with the new packages at AST level (docstrings dropped, `_`-prefix renames mapped), plus every module/class level assignment, signature, default and decorator. The only differences are:
   - `_all_regions` -> `_common.ec2_regions` (same `get_all_regions("ec2", "us-west-2")`), `_skip_region(r, e)` -> `_common.skip_region(r, SKIP_CONTEXT, e)` (same two calls, same order, same context strings), the `by_region` loop -> `_common.group_by_region` (same `setdefault` order), the three `_log_summary` bodies -> `_common.log_deleted_summary(label, ...)` (same f-strings, labels `keypairs` / `Elastic IPs` / `KMS`).
   - `snapshot` `iter_region`: local variable `used_by_amis` renamed to `used` (pure rename).
   - `disk`: `DiskCleaner.region_guard` (a `@contextmanager` method) now returns `guard.region_guard(region, self._flag_error)`; the except chain, `flag_error()` before `logging.error` (was `self.had_errors = True`), log texts and the early `return` for unauthorised codes are unchanged. `DiskCleaner.log_summary` delegates to `guard.log_summary` with the same texts. Callers only use it as `with self.region_guard(region):`.
   - KMS keeps its own `us-west-2` constant (now `_common.DEFAULT_REGION`, same value) and its own skip WARNING text (different from `log_skipped_region`, correctly not folded into `skip_region`).
   No changed condition, default, SDK call order, exception handling or log text. Nothing BLOCKING.
2. **Import paths and patch targets.** Imported every base module and the new package and diffed `dir()`: all public names of `crc.aws.{keypairs,elastic_ips,kms,snapshot,disk}` and `crc.aws.disk.cleaner` (`DiskCleaner`, `DiskRuntime`, `build_disk_cleaner`) still resolve. Names no longer on the old paths are only private helpers / re-imported stdlib names (`_boto3_client`, `_all_regions`, `_is_protected`, `_NotOursError`, `boto3_ec2`, ...); `rg` finds no reference to any of them in `tests/`, `crc.py`, `crc/cli`, `crc/core`. String patch targets naming these modules: only `crc.aws.disk.boto3.client` and `crc.aws.disk.get_all_regions`, both on the unchanged `disk/__init__.py`; no `patch.object` / `monkeypatch.setattr` on the moved modules. New `client.py` files call `boto3.client(...)` through the module, so `boto3.client` patches still apply.
3. **Tests untouched.** `git diff --name-only cea0a8a..HEAD` outside `crc/aws/` is only this review file: no test, no `tests/legacy/*.txt`, no `tests/archtest/**`, no hash-locked test, no guarded doc/config file.
4. **Size and seams.** Every owned file <= 250 (`wc -l`; largest `disk/cleaner.py` 217, `disk/volumes.py` 214 untouched, `kms/cleaner.py` 207). Splits follow real seams: `client.py` (Protocols, factory, runtime), `cleaner.py` (Cleaner + registry factory), `adapter.py` (legacy class alone, deletable in one move), plus domain modules `kms/settings.py`, `kms/tags.py`, `snapshot/protection.py`, `disk/guard.py`. `_common.py` helpers are each used by 2+ owned packages (`ec2_regions`/`skip_region`: keypairs, elastic_ips, snapshot; `group_by_region`: those three + disk; `log_deleted_summary`: keypairs, elastic_ips, kms).
5. **READMEs.** `crc/aws/README.md` and the five package READMEs list the public names of their modules (ARCH-002 green) and are short.

### Findings

- **BLOCKING:** none.
- **NIT (not changed):** the old flat modules' private helpers (`crc.aws.snapshot._is_protected`, `crc.aws.kms._tag_pairs`, ...) no longer import from the old path; nothing in the repo uses them and the contract covers names imported today, so acceptable.
- No new bugs beyond the coder's `BaseException` note.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt`, then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed! |
| `mypy` (strict) | Success: no issues found in 268 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` (incl. ARCH-001..005, SAF-LOCK) | 43010 passed in 158.96s |

- Test count: 43010 collected at `cea0a8a` (`pytest --collect-only` in a worktree) and 43010 passed at `HEAD`.
- `make check` runtime: about 2m40s wall (pytest 2m39s; ruff, mypy and lint-imports under 1s with a warm mypy cache).

ROUND 2 VERDICT: APPROVE

