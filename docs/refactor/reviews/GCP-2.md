# GCP-2 review

## Coding summary

Rung GCP-2: `crc/gcp/disk.py` refactored into a `Cleaner` (lane `refactor/lane-gcp`). No behaviour change.

- **Summary.** `DiskCleaner` (`cloud="gcp"`, `kind="disk"`, registered with `@register("gcp", "disk")`
  through `build_disk_cleaner`) lists the zones and their disks in `plan()` (read-only) and calls
  `delete_disk` from `apply()` / `apply_disk()` only. Filtering uses `policy.exception_regex_match_matches`
  (`re.match`), `policy.exception_tags_any`, `policy.filter_tags_any_key`, `policy.effective_age` and
  `policy.normalize_tags`; age uses `core_age.get_retention_age` and `core_age.is_old` (`MAX_AGE` is still
  read inside `core.age`). The SDKs sit behind the typed Protocols `GcpDiskClient` and `DiskView`;
  `ComputeDiskClient` builds the discovery service lazily once per run, builds `DisksClient` right after the
  zones were listed (as main) and `close()` closes the service (the `with` of main). Settings are the frozen
  `DiskSettings`. `Disk` is the thin adapter `crc.py` uses (constructor, attributes, `get_deleted`, `count`,
  `delete(disk_state=...)` unchanged; two optional trailing arguments `client` and `clock`). `crc.py` is not
  changed. The clock comes from `RunContext.now` (Cleaner) or the injected `clock` (adapter, default
  `datetime.datetime.now` as an uncalled reference, so ARCH-003 holds). `plan_zone` is a lazy generator so the
  adapter keeps main's disk-by-disk interleaving of listing, deleting and recording.
- **Parity.** `tests/legacy/gcp_disk.txt` is the byte-identical `git show main:crc/gcp/disk.py`.
  `tests/test_gcp_disk_parity.py` runs the oracle (with a frozen `datetime`) and the adapter against the same
  fake `googleapiclient.discovery.build` and `compute_v1.DisksClient` over 112 cases (dry run, age edges,
  `MAX_AGE`, custom age label, label filter and exceptions, regexes, untagged, Slack grouping, zone listing
  errors including auth and connectivity (no fallback on main), disk listing and iteration errors, delete
  errors, empty results, malformed disks and timestamps, ignored `age` / `notags` / `name_regex`) comparing
  SDK calls in order (including lazy page iteration), results, count, logs, stdout and errors. The same table
  runs through the registered Cleaner (plan then apply) and the logged summary lines match. Cleaner path
  differences, inherent to plan-then-apply and as accepted in GCP-1: all zones are listed and filtered before
  the first delete, so an error raised while listing or filtering stops before any delete, and log lines
  interleave differently for more than one disk (compared as multisets, exact for at most one disk). 56
  quirk checks assert the oracle and the new code show the same behaviour.
- **Test counts.** Before: 2879 passed. After: 3192 passed (+313: 286 in `tests/test_gcp_disk_parity.py`,
  27 in `tests/test_gcp_disk.py`). No existing test changed.
- **Gates.** `make check`: `ruff check .`, `mypy` (strict), `lint-imports`, `pytest -q` all pass.
  `crc/gcp/disk.py` and `crc.gcp.disk` are removed from both ratchet lists in `pyproject.toml`. One per-SDK
  mypy override was added, `googleapiclient` / `googleapiclient.*` with `ignore_missing_imports` (the package
  ships no stubs or `py.typed`); `google-cloud-compute` is typed, so it needs none.
- **Open issues.** New quirks are in `docs/refactor/bugs/pending/GCP-2.md` (17 rows, none fixed): no
  positive-age check on `detach_age`, swallowed delete errors, unawaited delete operations, run-aborting
  errors outside the per-disk `try`, unpaginated zone listing, zonal disks only, and Slack-mode count quirks.
  `Result.failed` stays empty and `Plan.skipped` stays empty because main reports neither failed deletes nor
  skip reasons; behaviour fixes belong to a later rung.
- **Core change needed.** None. `crc/core/policy.py` and `crc/core/age.py` already have every function used.
- **FILTER_VARIANTS.md change needed.** None for behaviour. Optional: the `gcp/disk.py` row could say that
  `states` (`["Unattached"]`) and `delete(disk_state)` are also never applied (recorded as a pending row).
- **`crc/README.md` / `tests/legacy/README.md` / `tests/README.md` change needed.** Add `gcp_disk.txt`
  (exact `git show main:crc/gcp/disk.py`, used by `tests/test_gcp_disk_parity.py`) to
  `tests/legacy/README.md`; `crc/core/README.md` could say `gcp` / `disk` is now registered. Both are
  outside this lane's edit rights.

## Round 1 (Argon)

Scope reviewed: `git diff be0a622..HEAD` (`crc/gcp/disk.py`, `crc/gcp/README.md`, `pyproject.toml`,
`docs/refactor/bugs/pending/GCP-2.md`, `docs/refactor/reviews/GCP-2.md`, `tests/legacy/gcp_disk.txt`,
`tests/test_gcp_disk.py`, `tests/test_gcp_disk_parity.py`) against `git show main:crc/gcp/disk.py`,
`git show main:crc.py`, `AGENTS.md`, `docs/refactor/BUGS.md`, `docs/refactor/FILTER_VARIANTS.md`,
`CLEANUP_SAFETY.md`, and `.cursor/skills/crc-python-refactor/REVIEW_CHECKLIST.md`.

### 1. Behaviour parity with `git show main:crc/gcp/disk.py`

- **SDK calls (order and arguments)** (`crc/gcp/disk.py:80-113`, `:180-252`, `:382-395` vs `main:crc/gcp/disk.py:175-200`):
  - `ComputeDiskClient.list_zones` lazily builds `discovery.build("compute", "v1")` once per run, calls `service.zones().list(project=project_id).execute()["items"]`, and constructs `compute_v1.DisksClient()` right after zone listing, matching `main:crc/gcp/disk.py:175-178`.
  - `Disk.delete()` iterates zones in order, lazily iterates `plan_zone(zone, self._clock)` (which calls `disks_client.list(project=project_id, zone=zone)`), and calls `apply_disk()` immediately on each yielded candidate before advancing the zone's disk iterator.
  - When `not dry_run`, `apply_disk()` calls `service.disks().delete(project=project_id, zone=zone, disk=candidate.id).execute()` and logs `logging.info(f"Deleting disk {candidate.id}")`, then records the disk name in `disk_names_to_delete` (or owner dict when `slack_notify_users` is `True`).
  - `finally: self._client.close()` calls `service.close()` (and resets `self._service = None`) only when `discovery.build` succeeded, matching the `with discovery.build(...) as service:` block on `main:crc/gcp/disk.py:175`.
- **Filter, state, and age decisions** (`crc/gcp/disk.py:219-284` vs `main:crc/gcp/disk.py:108-161`, `:184-192`):
  - Attachment check: skips when `disk.users` is truthy (`crc/gcp/disk.py:223-224`); `default_disk_state = ["Unattached"]` and `delete(disk_state=...)` are preserved and unused, as on `main:crc/gcp/disk.py:36`, `:163-166`, `:184-185`.
  - Exception check (`_should_skip`, `crc/gcp/disk.py:254-263`): stringifies each `exception_regex` entry with `f"{pattern}"` and tests `policy.exception_regex_match_matches` (`re.match` anchored at start, any), then tests `exception_labels` via `policy.exception_tags_any(policy.normalize_tags(disk.labels), settings.exception_labels)`. `notags` (`BUG-005`) and `name_regex` (`BUG-006`) are stored on `Disk` and never applied.
  - Filter check (`_has_matching_filter_label`, `crc/gcp/disk.py:265-269`): uses `policy.filter_tags_any_key` (`empty` passes; unlabelled disks fail an active filter).
  - Detach timestamp & age check (`_older_than_detach_age`, `crc/gcp/disk.py:229-231`, `:271-284`): skips when `disk.last_detach_timestamp` is falsy; parses with `%Y-%m-%dT%H:%M:%S.%f%z`, converts `clock()` to `detached.tzinfo`, logs `logging.info(disk.labels)`, calls `core_age.get_retention_age` (fail open, `BUG-004`, `SAF-009`), logs `Updating age for disk: <name>` when truthy, and evaluates `core_age.is_old(policy.effective_age(retention_age, default_age), now, detached)` (`SAF-021` to `SAF-024`, `BUG-023` `age` ignored).
- **Log lines, errors, and dry-run path** (`crc/gcp/disk.py:152-162`, `:244-252`, `:361-367` vs `main:crc/gcp/disk.py:99-106`, `:193-244`):
  - Per-disk delete errors in `apply_disk()` are caught (`except Exception as e:`), logged at `ERROR` (`Error occurred while deleting disk <name>: <e>`), and omitted from `disk_names_to_delete`, `Result.succeeded`, and `Result.failed`.
  - Errors outside the per-disk `try` (`discovery.build`, `zones().list().execute()`, missing `"items"` or zone `"name"`, `DisksClient()`, `disks_client.list`, mid-page iteration, `strptime`, `re.match`, `MAX_AGE` parse) propagate unchanged after closing the discovery service.
  - Every `logging.info`, `logging.warning`, and `logging.error` message (including `count`, `No GCP disk to delete.`, and dry-run vs non-dry-run summaries) is identical to `main`; nothing is printed to stdout.

### 2. Oracle and parity test verification (`tests/legacy/gcp_disk.txt`, `tests/test_gcp_disk_parity.py`)

- Diffed `tests/legacy/gcp_disk.txt` against `git show main:crc/gcp/disk.py`: byte-identical (SHA-256 `c9c62cbc07164483906a5567eab37de1b0ed2c10245a068d6afc61f46a9ae183`, 0 lines of diff).
- Verified `tests/test_gcp_disk_parity.py` executes the oracle (`tests/legacy/gcp_disk.txt`) and both the new `Disk` adapter and registered `DiskCleaner` against the same `FakeGcp` (`discovery.build` and `compute_v1.DisksClient`) over 112 cases, comparing ordered SDK `calls` (including per-disk generator steps `disks.next`), `error`, `deleted`, `count`, `logs` (at `DEBUG`), and `stdout`.
- Tested a deliberate one-line behaviour change in `crc/gcp/disk.py` (`if disk.users:` -> `if not disk.users:` in `plan_zone`): `pytest -q tests/test_gcp_disk_parity.py` failed immediately with 222 failures; reverted.

### 3. Architecture and guardrails

- `DiskCleaner` implements `Cleaner` (`cloud = "gcp"`, `kind = "disk"`, `supports`, read-only `plan`, mutating `apply`) and is registered with `@register("gcp", "disk")` on `build_disk_cleaner`.
- `plan()` never mutates (`ARCH-005` passes on `crc/gcp/disk.py`; `delete_disk` is only called in `apply_disk`, invoked from `apply` and `Disk.delete`).
- Filtering uses `policy.exception_regex_match_matches`, `policy.exception_tags_any`, `policy.filter_tags_any_key`, `policy.effective_age`, and `policy.normalize_tags` as named in `docs/refactor/FILTER_VARIANTS.md`; no banned constructs (`ARCH-003`).
- SDK is behind the typed `GcpDiskClient` and `DiskView` `Protocol`s.
- `crc/gcp/disk.py` (`crc.gcp.disk`) was removed from both ratchet lists in `pyproject.toml`; nothing was added (`ARCH-001`), and the only mypy override added is per-SDK (`googleapiclient`, `googleapiclient.*` with `ignore_missing_imports = true`).

### 4. Scope, lane rules, and documentation

- Verified `git diff be0a622..HEAD --name-status`: none of the lane-forbidden files (`docs/refactor/BUGS.md`, `docs/refactor/STATUS.md`, `docs/refactor/FILTER_VARIANTS.md`, `crc/README.md`, `tests/README.md`, `tests/legacy/README.md`, `crc.py`, `crc/core/**`, or other cloud packages) were modified.
- `crc/gcp/README.md` is accurate and passes `ARCH-002` symbol sync (`DiskView`, `GcpDiskClient`, `ComputeDiskClient`, `DiskSettings`, `DiskCleaner`, `build_disk_cleaner()`, `Disk`).
- Verified all rows in `docs/refactor/bugs/pending/GCP-2.md` against `git show main:crc/gcp/disk.py` and `git show main:crc.py`, tightened the `main` line ranges on three rows (`:41-88` for `__init__`, `:85-88` for `disk_names_to_delete` init), and added one missing quirk row (bare disk name recorded without zone across zones, `crc/gcp/disk.py:201-224`, `crc.py:473-493`, `:920-924`) plus its `same-name-two-zones` assertion in `QUIRKS` (`tests/test_gcp_disk_parity.py:848`).

### BLOCKING (fixed)

None.

### NIT (left)

- Calling `DiskCleaner.apply()` directly with `context.dry_run=True` logs the dry-run summary warning a second time after `plan()` and returns candidate IDs in `Result.succeeded` (`crc/gcp/disk.py:198-199`, `:209-214`, `:244-248`); `crc.core.engine.run` never calls `apply()` when `context.dry_run` is `True` (`crc/core/engine.py:100`).
- `Disk.__init__` uses `client or ComputeDiskClient()` (`crc/gcp/disk.py:352`); `client if client is not None else ComputeDiskClient()` would avoid replacing a falsy custom test client.
- `DiskCleaner._should_skip` materializes `patterns = [f"{pattern}" for pattern in settings.exception_regex]` up front before `policy.exception_regex_match_matches` (`crc/gcp/disk.py:257-258`), whereas `main` formats each `ex_reg` inside the loop (`main:crc/gcp/disk.py:151-152`); `re.match` still short-circuits on the first matching pattern.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` (`crc`, `tests`) | Success: no issues found in 87 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | 3192 passed |

Test count: 2879 before GCP-2 (`be0a622`), 3191 on `043e0aa`, 3192 after Round 1 (+313 total: 286 in `tests/test_gcp_disk_parity.py`, 27 in `tests/test_gcp_disk.py`).

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff be0a622..HEAD` (both commits `d144823` and `569ea19`), read against
`git show main:crc/gcp/disk.py`, `git show main:crc.py`, `crc/core/policy.py`, `crc/core/age.py`,
`AGENTS.md`, `docs/refactor/BUGS.md`, `CLEANUP_SAFETY.md` and `REVIEW_CHECKLIST.md`.

### Round 1 fixes assessment

Round 1 (`569ea19`) changed only docs plus one test line:
- `docs/refactor/bugs/pending/GCP-2.md`: the tightened ranges (`main:crc/gcp/disk.py:41-88` for
  `__init__`, `:85-88` for the `disk_names_to_delete` init, `:201-224` for recording) are correct
  (checked with `git show main:crc/gcp/disk.py`). The new bare-name-without-zone row is correct;
  its `crc.py:473-493` (`notify_deleted_disk_via_slack`) and `:920-924` (Slack and InfluxDB use
  of `get_deleted`) citations match main.
- `tests/test_gcp_disk_parity.py:848`: adds the `same-name-two-zones` quirk check. It is an added
  assertion, and it runs against both the oracle and the adapter. No test was weakened or removed.
- Correct, and no behaviour changed. Round 1's three NITs stand. None of them can be reached
  through `crc.py`.

### Independent review

- **Parity.** `crc/gcp/disk.py` matches main's flow step by step: the `users`, exception, filter,
  `last_detach_timestamp` and age order (`main:crc/gcp/disk.py:183-192`); `clock()` read after
  `strptime` (`:118-119`); raw `disk.labels` passed to `get_retention_age` (`:121`);
  `effective_age` = `retention_age or detach_age` (`:124`); delete then log (`:195-200`);
  `close()` after the summary, as the `with` block of main does (`:175`, `:231-244`). The
  `policy.*` helpers behave the same as main's inline expressions for `str` to `str` label maps.
  `tests/legacy/gcp_disk.txt` is byte-identical to `git show main:crc/gcp/disk.py`.
- **Safety.** The age gate is unchanged (SAF-021, SAF-022: `--detach_age` only, `MAX_AGE` still
  inside `is_old`). The custom age label is still fail open (BUG-004, as on main). `plan()` makes no
  mutating call, and `delete_disk` is called only from `apply_disk()`, never in a dry run.
  Per-disk error isolation is the same as main's (`main:crc/gcp/disk.py:193-230`).
- **Tests.** Every test function in `tests/test_gcp_disk.py` and `tests/test_gcp_disk_parity.py`
  asserts something (checked with an AST scan). The parity tests compare exact SDK calls, logs,
  results, count, stdout and errors, and the quirk checks assert concrete outcomes against both
  the oracle and the new code.
- **Architecture and typing.** `lint-imports` keeps all 5 contracts and ARCH-001 to ARCH-005 pass.
  The ratchet only removes `crc/gcp/disk.py` and `crc.gcp.disk`. The one added override is the
  per-SDK `googleapiclient` override. `Any` appears only at the discovery-service edge.
- **Docs.** `crc/gcp/README.md` matches the code. The `crc.py:1129-1146` and `:1409` citations in
  the pending file match main.

### BLOCKING (fixed)

None.

### NIT (left)

- `plan_zone` stores `policy.normalize_tags(disk.labels)` in `Candidate.tags`, and `_record` groups
  Slack owners from those tags (`crc/gcp/disk.py:236`, `:140-149`). Main groups them from the raw
  `disk.labels` (`main:crc/gcp/disk.py:201-222`). The two differ only for labels outside the
  `DiskView.labels: Mapping[str, str]` contract:
  - With `--slack_notify_users` and `labels=None`, main deletes the disk, then logs a delete error
    and does not record it. The new code records it under `not_tagged`.
  - A non-`str` owner value (for example `1`) becomes the key `"1"` in the new code instead of `1`.

  I confirmed both with a throwaway probe against the oracle. The real `compute_v1.Disk.labels` is
  always a `str` to `str` map, so this cannot be reached in production. I left it because matching
  main exactly would mean exposing the raw SDK container in `Candidate`. The main-side quirk (a
  recording error after a successful delete is reported as a delete error) is a new row in
  `docs/refactor/bugs/pending/GCP-2.md`.
- The Coding summary says the test count was 3191 on `043e0aa`, but that commit is not in this
  branch's history (`d144823` is the coding commit). The final count, 3192, is correct.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` (`crc`, `tests`) | Success: no issues found in 87 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | 3192 passed |

Test count: 3192 (2879 before the rung, +313). Round 2 changed no code or tests.

ROUND 2 VERDICT: APPROVE
