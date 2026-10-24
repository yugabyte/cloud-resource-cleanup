## Coding summary

Rung AWS-2: `crc/aws/snapshot.py` refactored into a registered `Cleaner` (`@register("aws", "snapshot")`), no behaviour change.

- **Summary.** `SnapshotCleaner` (`plan()` lists with `describe_snapshots` / `describe_images` and decides, no mutating call; `apply()` calls `delete_snapshot`) behind the typed `SnapshotClient` Protocol, injected through the constructor / `SnapshotRuntime` (client factory, region lister, UTC clock). The module stays one file (about 290 lines, no function over the complexity limits), so no subpackage split. `Snapshot` keeps its constructor `(dry_run, age_days, exception_tags)`, `deleted_snapshots`, `get_deleted`, `delete_count`, `_is_protected`, `_get_snapshots_used_by_amis` and `delete()`, and drives the cleaner region by region, snapshot by snapshot (lazy `iter_region`, delete as it goes), in the exact call and log order of `main`. Age goes through `policy.snapshot_old_enough` (the function FILTER_VARIANTS names; the cutoff is also computed once up front via `check_age`, so an invalid `age_days` fails before any call as on `main`); exception tags go through `policy.exception_tags_any` one key at a time so the log names the first matching key and malformed tag entries still raise `KeyError`. `main` has no summary log lines (`crc.py` prints), so the Cleaner path logs none either. `crc.py` is unchanged. Both ratchet lines for `crc/aws/snapshot.py` / `crc.aws.snapshot` are removed from `pyproject.toml`; nothing was added.
- **Tests.** Before: 3339 passed. After: 3628 passed (+289): `tests/test_aws_snapshot_parity.py` (275: 69 cases through the legacy adapter, the same 69 through the registered Cleaner path with split call/log stream comparison, 69 whole-log-order checks where nothing interleaves, 45 quirk checks, 14 `_is_protected` helper parity checks, 7 `_get_snapshots_used_by_amis` helper parity checks, factory checks) and `tests/test_aws_snapshot.py` (14: plan read-only, apply order, dry run, error swallowing, client reuse, adapter). The oracle `tests/legacy/aws_snapshot.txt` is byte-identical to `git show main:crc/aws/snapshot.py`. A deliberate one-character change to a log message fails the table. `make check` is green (ruff, mypy strict, import-linter, pytest, archtests).
- **Open issues.**
  - Cleaner path vs `main`, abnormal runs only: `plan()` lists and decides every region before `apply()` deletes, so (a) a listing error or `KeyError` in a later region/snapshot is raised before earlier snapshots are deleted, whereas `main` had already deleted them; (b) the `Skipping snapshot ... used by AMI` / `Snap ... has tag` lines of later snapshots come before the `Deleted snapshot` lines of earlier ones. SDK calls (client creation, describes, deletes), results, errors and every individual log stream are identical; the adapter path (`Snapshot.delete()`) is identical including interleaving. The parity test splits the call and log streams for the Cleaner path accordingly.
  - The factory defaults `age_days` to 90 when the option is absent (the value `crc.py` hard-codes, BUG-009); `crc.py` does not use the registry yet.
  - `apply()` also returns the failed deletes in `Result.failed` (main only logs them); the adapter ignores them.
  - Dry-run `apply()` makes no call and logs nothing.
  - 14 new quirks are in `docs/refactor/bugs/pending/AWS-2.md` (3 added in round 1; main one: no filter besides age and exception tags / AMI guard, so every old snapshot of the account in every region is deleted). BUG-009 and BUG-040 are kept as is.
- **Core changes needed.** None.
- **`FILTER_VARIANTS.md` change needed (not edited).** In the `aws/snapshot.py` row, add that since AWS-2 the age decision is `policy.snapshot_old_enough` and the exception check is `policy.exception_tags_any` applied one key at a time (so the first matching key is logged and malformed tag entries raise `KeyError`), main lines `:28-37`, `:59`, `:72-76`, and that the oracle is `tests/legacy/aws_snapshot.txt`.
- **`crc/README.md` / `tests/README.md` / `tests/legacy/README.md` (not edited).** `tests/legacy/README.md` should list `aws_snapshot.txt` (exact `git show main:crc/aws/snapshot.py`, run by `tests/test_aws_snapshot_parity.py`); `tests/README.md` should list `test_aws_snapshot_parity.py` and `test_aws_snapshot.py`.

## Round 1 (Argon)

Scope reviewed: `git diff 5f82f96..HEAD` (`crc/aws/snapshot.py`, `crc/aws/README.md`, `pyproject.toml`, `docs/refactor/bugs/pending/AWS-2.md`, `docs/refactor/reviews/AWS-2.md`, `tests/legacy/aws_snapshot.txt`, `tests/test_aws_snapshot.py`, `tests/test_aws_snapshot_parity.py`) against `git show main:crc/aws/snapshot.py`, `git show main:crc.py`, `AGENTS.md`, `docs/refactor/BUGS.md`, and `.cursor/skills/crc-python-refactor/REVIEW_CHECKLIST.md`.

### 1. Behaviour parity (`git show main:crc/aws/snapshot.py`)

- **SDK calls (order and arguments)** (`crc/aws/snapshot.py:65-73`, `:104-111`, `:183-203`, `:283-299` vs `main:crc/aws/snapshot.py:46-93`):
  - Computes the cutoff `now - datetime.timedelta(days=age_days)` before listing regions (`check_age` at `:137-139`), so invalid `age_days` (`None`, `str`, overflow) raises `TypeError` / `OverflowError` before any SDK call.
  - Per region in `get_all_regions("ec2", default_region_name="us-west-2")`: connects `boto3.client("ec2", region_name=region)`, calls `describe_snapshots(OwnerIds=["self"])["Snapshots"]` then `describe_images(Owners=["self"])["Images"]`, and lazily iterates snapshots in order, calling `delete_snapshot(SnapshotId=snap_id)` (outside dry run) on each eligible snapshot before moving to the next snapshot or region.
- **Filter, protection and age decisions** (`crc/aws/snapshot.py:91-101`, `:209-221` vs `main:crc/aws/snapshot.py:28-37`, `:59`, `:68-80`):
  - Age check uses `policy.snapshot_old_enough(start_time, now, self._age_days)` (`not start_time > now - datetime.timedelta(days=days)`), keeping snapshots where `StartTime > cutoff` and treating `StartTime == cutoff` as eligible.
  - Protection check (`_is_protected`) builds `{t["Key"]: t["Value"] for t in tags}` (raising `KeyError` on missing `"Key"`/`"Value"`, only for snapshots already old enough) and evaluates `policy.exception_tags_any(tag_map, {key: values})` one `exception_tags` key at a time, logging `f"Snap {snap_id} has tag {key}"` on the first match.
  - AMI check skips snapshots in `used_by_amis` and logs `f"Skipping snapshot {snap_id}, used by AMI"`.
- **Logs, errors and dry-run path** (`crc/aws/snapshot.py:86-87`, `:192-203`, `:289-299` vs `main:crc/aws/snapshot.py:82-93`):
  - Every `logging.info`, `logging.warning`, and `logging.error` message matches `main`.
  - `CONNECTIVITY_ERRORS` during region client creation or `describe_snapshots`/`describe_images` skip the region via `log_skipped_region(region, "snapshot cleanup", e)`; non-connectivity listing errors propagate immediately; `delete_snapshot` catches `Exception` (including connectivity errors), logs `f"Failed to delete snapshot {snap_id}: {e}"`, and swallows it without appending `snap_id` to `deleted_snapshots`. In dry run `delete_snapshot` is not called and `snap_id` is appended to `deleted_snapshots`.

### 2. Oracle and parity test verification

- `diff -u <(git show main:crc/aws/snapshot.py) tests/legacy/aws_snapshot.txt` is empty (SHA-256 `642394af14ec43afc48c6581d0812bb6df67dc665b53f36ac9eec1cd972d0521`).
- `tests/test_aws_snapshot_parity.py` executes both the oracle (`tests/legacy/aws_snapshot.txt`) and the refactored `Snapshot` / registered `SnapshotCleaner` against `FakeAws` and asserts ordered SDK calls, deleted IDs, counts, log records, stdout, and exceptions across 69 cases and 45 quirk checks.
- Tested a deliberate one-line behaviour change in `crc/aws/snapshot.py` (`_DEFAULT_REGION = "us-east-1"`), confirmed `pytest -q tests/test_aws_snapshot_parity.py` failed immediately (134 failed), and reverted it.

### 3. Architecture, scope and lane compliance

- `SnapshotCleaner` implements `Cleaner`, is registered with `@register("aws", "snapshot")`, has a strictly read-only `plan()` (`ARCH-005` passes), uses `crc.core.policy.snapshot_old_enough` and `crc.core.policy.exception_tags_any` as named in `docs/refactor/FILTER_VARIANTS.md`, puts `boto3` behind the typed `SnapshotClient` `Protocol`, and was removed from both legacy ratchet lists in `pyproject.toml` with nothing added.
- `crc.py`, `crc/core/**`, other clouds, and shared docs forbidden by lane rules are untouched; `crc/aws/README.md` passes `ARCH-002` symbol sync.

### BLOCKING (fixed)

1. **Wrong `Effect` column and incomplete line citation in `docs/refactor/bugs/pending/AWS-2.md`, plus 3 missing quirks on `main`** (`docs/refactor/bugs/pending/AWS-2.md:9-23`):
   - Row 4 (`crc/aws/snapshot.py:34`, `exception_tags` values compared with `in` so a string value list matches by substring) had `Effect: deletes what it should keep`, whereas `_is_protected` returning `True` skips (keeps) the snapshot (`crc/aws/snapshot.py:75-76`); fixed to `keeps what it should delete`.
   - Row 1 cited `:65`, `:72-90` (added `:13-18` for `__init__`), and Row 7 cited `crc/aws/snapshot.py:83-88` while also describing the dry-run `else:` branch at `:89-90`; updated Row 7 to `crc/aws/snapshot.py:82-90`.
   - Added 3 `NEW` low-risk rows verified against `main`: duplicate `Key` entries in `Tags` where the last value wins (`main:crc/aws/snapshot.py:32`, `deletes what it should keep`), `"Ebs": None` in a block device mapping raising `AttributeError` (`main:crc/aws/snapshot.py:51-52`, `run fails`), and `Snapshot` exposing `delete_count` (not `count`, `BUG-092` shape), recording bare IDs without region (`BUG-050` shape), and `crc.py` printing `>{age_days} days` in dry run while `start_time > cutoff` deletes `>= age_days` (`main:crc/aws/snapshot.py:24-26`, `:72`, `:86`, `:90`; `main:crc.py:1638-1642`, `cosmetic`).
2. **Legacy adapter methods `Snapshot._is_protected` and `Snapshot._get_snapshots_used_by_amis` had no direct test coverage** (`crc/aws/snapshot.py:277-281`, `tests/test_aws_snapshot_parity.py:632-710`):
   - Because `Snapshot.delete()` delegates to `cleaner.iter_region(...)`, neither `Snapshot._is_protected` nor `Snapshot._get_snapshots_used_by_amis` was called during the test suite. Added `test_is_protected_matches_main` (14 cases) and `test_get_snapshots_used_by_amis_matches_main` (7 cases) in `tests/test_aws_snapshot_parity.py` comparing return values, log records, SDK calls, and exceptions directly against `_load_oracle().Snapshot`, and strengthened `test_factory_defaults_age_days_to_ninety`.
3. **Inaccurate `CLEANUP_SAFETY.md` citation and age-validation wording in `crc/aws/README.md`** (`crc/aws/README.md:7`, `:69`):
   - Fixed the missing comma on line 7 and removed the trailing `(CLEANUP_SAFETY.md)` citation on `delete_snapshot` error swallowing (`CLEANUP_SAFETY.md` has no snapshot section; `SAF-001` and `SAF-004` are already cited for region connectivity/listing), and clarified that non-numeric or overflowing `age_days` fails before any call while `0`, negative, or `True` are accepted by `timedelta`.

### NIT (left)

- `SnapshotCleaner.apply()` returns `Result(succeeded=tuple(deleted))` when called directly with `context.dry_run=True` (`crc/aws/snapshot.py:161-181`), though `crc.core.engine.run` skips `apply()` in dry run.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` (`crc`, `tests`) | Success: no issues found in 93 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | 3628 passed |

Test count: 3339 before AWS-2 (`5f82f96`), 3607 on `16b7571`, 3628 after review additions (+289).

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff 5f82f96..HEAD` (both commits), read against `git show main:crc/aws/snapshot.py`, `git show main:crc/aws/_base.py`, `git show main:crc.py` (`:1629-1643`), `crc/core/policy.py` and the review checklist.

### Round 1 fixes assessment

- Bug file corrections (row 4 effect `keeps what it should delete`, row 1 and row 7 line ranges, 3 added rows): correct; I checked every cited line with `git show main:`. `crc.py:1630-1634` and `:1638-1642` are correct as well.
- Added helper parity tests (`test_is_protected_matches_main`, `test_get_snapshots_used_by_amis_matches_main`) and the stronger factory test: they compare return values, logs, calls and exceptions with the oracle. They are not vacuous and change no behaviour.
- README wording fixes (`crc/aws/README.md`): accurate. `CLEANUP_SAFETY.md` has no snapshot section, so removing that citation was right.
- None of the round 1 changes touches `crc/aws/snapshot.py`, so they cannot change behaviour.

### Independent parity check

- Age: `policy.snapshot_old_enough` is `not start_time > now - timedelta(days)`, which is exactly main's `if start_time > cutoff: continue` (`main:crc/aws/snapshot.py:59`, `:72`). `check_age` reads the clock and builds the `timedelta` in the same order as main and before `get_all_regions`.
- Exception tags: `policy.exception_tags_any(tag_map, {key: values})` goes through `_key_matches` (`key in tags and (not values or tags[key] in values)`), the same expression as `main:crc/aws/snapshot.py:34`. It is applied one key at a time in dict order, so the same key is logged.
- Calls: the default `_boto3_client` / `_all_regions` resolve `boto3.client` and `get_all_regions` at call time. The parity harness patches `boto3.client` / `boto3.session.Session` and so runs the production defaults (only the clock is injected). `describe_images` is still called when a region has no snapshots, because the generator runs both describes on its first `next()`. The `try` covers the client, both describes and the whole snapshot loop, as on main (`:61-93`).
- Safety (`CLEANUP_SAFETY.md`): `plan()` makes no mutating call. `delete_snapshot` is reached only from `delete_one`, which returns before the call in a dry run. `describe_regions` auth errors still propagate (`_base.py` unchanged). No age gate is weakened.
- Architecture: import-linter 5/5 kept, archtests green, `crc/aws/README.md` symbol list synced (ARCH-002). `pyproject.toml` only drops the two `snapshot` ratchet lines. `crc.py`, `crc/core/**` and the shared docs are untouched.

### BLOCKING (fixed)

1. **Vacuous parametrizations in `test_cleaner_protocol_logs_in_order_when_nothing_interleaves`** (`tests/test_aws_snapshot_parity.py:507-528`). 43 of its 69 cases returned before any assertion: 33 because of an error, more than one region or delete errors, and 10 because of skip log lines. They still counted as passing tests, which inflated the "69 whole-log-order checks" in the coding summary. Skipping them is not allowed (`AGENTS.md` rule 1, `tests/archtest/safety_lock.py`). Instead, those cases now assert the documented Cleaner-path ordering: the whole log stream equals listing lines followed by delete lines, and after an error there is no delete line. I checked this by mutation: an extra listing-style log line emitted by `apply()` fails 40 cases. Test count is unchanged (3628).

### NIT (left)

- `SnapshotCleaner.__init__` and `_is_protected` type `age_days` / `exception_tags` / `tags` as `Any` (`crc/aws/snapshot.py:91`, `:121-127`). This is outside an SDK edge, but it is deliberate: it keeps main's `TypeError` / `KeyError` / `AttributeError` behaviour for malformed legacy input. A later typed-options rung can narrow it.
- `plan()` caches clients in `self._clients` (`crc/aws/snapshot.py:153`). This is instance state, not a cloud mutation, but it makes a `SnapshotCleaner` single-run. That is fine for the registry factory, which builds one cleaner per run.
- Round 1 NIT still applies: `apply()` called directly with `dry_run=True` reports every candidate as succeeded (`crc/aws/snapshot.py:161-181`). The engine never calls it that way.

### New quirks (added to `docs/refactor/bugs/pending/AWS-2.md`)

- Unpaginated `describe_snapshots` / `describe_images` (`main:crc/aws/snapshot.py:49`, `:65`), run fails, low.
- Disabled AMIs left out of `describe_images` by default, so their snapshots are attempted, refused and logged as errors, and dry runs count them (`main:crc/aws/snapshot.py:49-54`, `:78-90`), cosmetic, low.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`: `ruff check .` passed, `mypy` passed (strict, `crc` and `tests`), `lint-imports` 5 kept / 0 broken, `pytest -q` 3628 passed, including archtests.

Test count: 3339 before AWS-2, 3628 after (+289, unchanged by round 2).

ROUND 2 VERDICT: APPROVE
