# AZ-3 review

## Coding summary

Rung AZ-3: `crc/azu/disk.py` is a registered `Cleaner` (`@register("azure", "disk")`), with `Disk`
kept as a thin adapter. Status: complete; ruff, mypy strict, lint-imports and pytest are green (`make check` equivalent).

- Cleaner: `AzureDiskCleaner` (`plan()` = `disks.list` plus filtering, read-only; `apply()` =
  `disks.begin_delete(resource_group, name)` in plan order). SDK behind the `DiskComputeClient` /
  `DiskOperations` / `SdkDisk` Protocols, client factory injected through the constructor.
  Filtering: `policy.filter_tags_any_key`, `policy.exception_tags_any`, `policy.notags_all` (all
  three evaluated for every disk, as main), age through `core_age.get_retention_age` and
  `core_age.is_old` with `policy.effective_age`, state through `policy.state_is_substring_of_any`
  (FILTER_VARIANTS.md section 3, `azu/disk.py`). Tags are copied with `dict(disk.tags)` instead of
  `policy.normalize_tags`, so non-string tag values behave exactly as on main. The module stays one
  file (about 300 lines, no function over the ruff limits), so no subpackage split.
- Adapter: `Disk(resource_group, dry_run, filter_tags, exception_tags, age, custom_age_tag_key,
  notags)` keeps `delete()`, `get_deleted`, `count`, `disks_names_to_delete`, `base`,
  `default_disk_state` and the constructor attributes; it adds `clock` (defaults to
  `datetime.datetime.now`, read once per disk as main does). `delete()` iterates the lazy listing and
  decides and deletes disk by disk, so call order, mid-run error behaviour and the cumulative list
  are those of main. `crc.py` is unchanged.
- Cleaner path: `plan()` lists and decides every disk first, then `apply()` deletes, using the single
  `RunContext.now`. Differences from main: paging is no longer interleaved with deletes, and a
  listing or decision error now happens before any delete (and decision logs precede the "Deleted
  disk" logs). Summary lines are the same: dry run logs the "which will be deleted" warning from
  `plan()`, a real run logs the two "deleted" warnings from `apply()`.
- Tests: baseline `pytest -q` 3715 passed; now 3919 passed (+204): `tests/test_azure_disk_parity.py`
  (77 cases through the adapter, the same 77 through the registered Cleaner, 34 quirk checks, plus
  interleaving, repeated-delete and factory tests), `tests/test_azure_disk.py` (13 focused `plan()` /
  `apply()` / adapter tests). All existing tests unchanged. Oracle: `tests/legacy/azu_disk.txt` is
  byte-identical to `git show main:crc/azu/disk.py`, run with a frozen clock. The module lists no
  regions, so there is no region fallback or region auth-error case to cover.
- Ratchet: `crc/azu/disk.py` removed from ruff `extend-exclude` and the mypy override `crc.azu.disk`.
  No per-SDK mypy override was needed.
- Open issues: new bugs and quirks in `docs/refactor/bugs/pending/AZ-3.md` (10 rows, ID `NEW`; one added in round 1);
  existing rows `BUG-004` and `BUG-019` apply and are unchanged.
- Needed changes outside this lane (not made): `crc/README.md`, `tests/README.md` and
  `tests/legacy/README.md` should list `tests/legacy/azu_disk.txt`, `tests/test_azure_disk_parity.py`,
  `tests/test_azure_disk.py` and `tests/helpers_azure_disk.py`. `FILTER_VARIANTS.md` section 3: the
  `azu/disk.py` row can cite `policy.filter_tags_any_key`, `policy.exception_tags_any`,
  `policy.notags_all`, `policy.state_is_substring_of_any` and `policy.effective_age`. No core change
  needed. `STATUS.md` and `BUGS.md` rows are for the integrator.

## Round 1 (Argon)

Scope reviewed: `git diff 2762dc3..HEAD` (`crc/azu/disk.py`, `crc/azu/README.md`, `pyproject.toml`,
`docs/refactor/bugs/pending/AZ-3.md`, `docs/refactor/reviews/AZ-3.md`,
`tests/legacy/azu_disk.txt`, `tests/helpers_azure_disk.py`, `tests/test_azure_disk.py`,
`tests/test_azure_disk_parity.py`) against `git show main:crc/azu/disk.py`, `AGENTS.md`,
`docs/refactor/BUGS.md`, `docs/refactor/FILTER_VARIANTS.md` and
`.cursor/skills/crc-python-refactor/REVIEW_CHECKLIST.md`.

### 1. Behaviour parity with `git show main:crc/azu/disk.py`

- **SDK calls (order and arguments)** (`crc/azu/disk.py:150-158`, `:285-297` vs `main:crc/azu/disk.py:78`, `:129-131`):
  - `Disk.delete()` calls `self.base.get_compute_client().disks.list()`, iterates lazily, and for each selected disk when `not self.dry_run` calls `self.base.get_compute_client().disks.begin_delete(self.base.resource_group, disk.name)` (positional arguments) before advancing the iterator.
- **Filter, age and state decisions** (`crc/azu/disk.py:160-195` vs `main:crc/azu/disk.py:83-126`):
  - Evaluates `filter_tags` (`policy.filter_tags_any_key`), `exception_tags` (`policy.exception_tags_any`), and `notags` (`policy.notags_all`) on `dict(disk.tags)` for every disk without short-circuiting between the three checks, matching `main:crc/azu/disk.py:83-112`.
  - Logs `logging.info(disk.tags)`, reads custom retention age via `core_age.get_retention_age` (fail open, `BUG-004`), logs `Updating age for disk: <name>` when truthy, evaluates `core_age.is_old(policy.effective_age(retention_age, settings.age or {}), current, disk.time_created)`, and then checks `policy.state_is_substring_of_any(disk.disk_state, self._settings.disk_states)` (`BUG-019`).
- **Log lines, printed messages, errors and dry-run path** (`crc/azu/disk.py:99-105`, `:154-195`, `:266-272`, `:285-297` vs `main:crc/azu/disk.py:61-69`, `:113-145`):
  - All `logging.debug`, `logging.info`, `logging.warning` and `logging.error` messages, dry-run behaviour, `count` property side-effect log, and unisolated error propagation match `main` verbatim.

### 2. Oracle and parity test verification (`tests/legacy/azu_disk.txt`, `tests/test_azure_disk_parity.py`)

- Diffed `tests/legacy/azu_disk.txt` against `git show main:crc/azu/disk.py`: byte-for-byte identical (0 lines of diff).
- Verified `tests/test_azure_disk_parity.py` runs both the oracle (`tests/legacy/azu_disk.txt`) and the refactored code (`Disk` and registered `AzureDiskCleaner`) against the same `FakeAzure` fake and compares ordered SDK `calls` (including lazy `yield` interleaving), `error`, `deleted`, `count`, `logs`, `count_logs` and `stdout`.
- Tested a deliberate one-line behaviour change in `crc/azu/disk.py` (`if policy.state_is_substring_of_any(...)` in `decide`), confirmed `python3 -m pytest -q tests/test_azure_disk_parity.py` failed immediately, and reverted it.

### 3. Architecture and lane rules

- `AzureDiskCleaner` implements `Cleaner` (`cloud = "azure"`, `kind = "disk"`, `supports`, read-only `plan`, mutating `apply`) and is registered with `@register("azure", "disk")` on `build_disk_cleaner`.
- `plan()` never mutates (`ARCH-005` passes; `begin_delete` is only in `delete_disk`, called from `apply()` and `Disk.delete()`).
- Filtering uses `crc.core.policy.filter_tags_any_key`, `crc.core.policy.exception_tags_any`, `crc.core.policy.notags_all`, `crc.core.policy.state_is_substring_of_any` and `crc.core.policy.effective_age` as named in `docs/refactor/FILTER_VARIANTS.md`.
- SDK is behind the typed `SdkDisk`, `DiskOperations` and `DiskComputeClient` `Protocol`s.
- `crc/azu/disk.py` (`crc.azu.disk`) left both legacy ratchet lists in `pyproject.toml`; nothing was added, and no SDK mypy override was needed.
- `crc.py`, `crc/core/**`, shared docs (`BUGS.md`, `STATUS.md`, `FILTER_VARIANTS.md`, `crc/README.md`, `tests/README.md`, `tests/legacy/README.md`), and other cloud packages are untouched. `crc/azu/README.md` is accurate and passes `ARCH-002` symbol sync.

### BLOCKING (fixed)

1. **`docs/refactor/bugs/pending/AZ-3.md` classified the subscription-wide `disks.list()` vs `self.base.resource_group` `begin_delete` row as `medium` / `run fails`, and omitted `__init__` (`:21-51`) on the missing-age-validation row** (`docs/refactor/bugs/pending/AZ-3.md:9-10`):
   - Verified all rows against `git show main:crc/azu/disk.py`. When an unattached disk in another resource group shares a name with an unrelated disk in `self.base.resource_group`, `begin_delete(self.base.resource_group, disk.name)` (`main:crc/azu/disk.py:78`, `:129-131`) deletes the unrelated disk in the configured group — the exact same shape as `BUG-002`, `BUG-074` and `BUG-111` (`critical` / `deletes what it should keep`). Updated the row's risk, effect and description to match `BUG-002`/`BUG-074`/`BUG-111`, added `crc/azu/disk.py:21-51` (`__init__`) to the missing-age-validation row, and added a pending row for `Disk.count` side-effect logging / `disks_names_to_delete` accumulation across calls and duplicate names (`main:crc/azu/disk.py:44`, `:61-69`, `:78`, `:134`, `:135-145`).
2. **`Disk.__init__` annotated `filter_tags`, `exception_tags`, `age`, `custom_age_tag_key` and `notags` as non-optional (`dict[...]` / `str`) instead of `TagSpec | None`, `AgeSpec | None` and `str | None` (`crc/azu/disk.py:226-235`)**:
   - `crc.py` passes `None` when the corresponding CLI flags are omitted (`crc.py:745-753`, `:1188-1197`), and `DiskSettings` (`crc/azu/disk.py:90-94`) already accepts `TagSpec | None`, `AgeSpec | None` and `str | None`. Relaxed `Disk.__init__` annotations to match `DiskSettings` and `crc.py`.
3. **`test_cleaner_protocol_matches_main` returned before checking the `"Deleted disk"` and summary log groups when `case.error_phase == "apply"` (`tests/test_azure_disk_parity.py:456-458`)**:
   - Added `assert _groups(new.logs)[1:] == _groups(old.logs)[1:]` on `case.error_phase == "apply"` so mid-apply failures also verify that earlier `"Deleted disk: ..."` logs match the oracle and no summary warning is logged.

### NIT (left)

- Calling `AzureDiskCleaner.apply()` directly with `context.dry_run=True` logs the dry-run summary warning a second time after `plan()` and returns `Result(succeeded=...)` (`crc/azu/disk.py:141-148`); `crc.core.engine.run` does not call `apply()` in a dry run (`crc/core/engine.py:100`).

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` (`crc`, `tests`) | Success: no issues found in 100 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | 3919 passed |

Test count: 3715 before AZ-3 (`2762dc3`), 3919 now (+204).

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff 2762dc3..HEAD` (both commits, including round 1's fixes), re-read against
`git show main:crc/azu/disk.py`, `git show main:crc/service.py`, `crc/core/policy.py`,
`crc/core/age.py`, `CLEANUP_SAFETY.md` and `REVIEW_CHECKLIST.md`.

### Round 1 fixes assessment

1. Pending-row risk change (subscription-wide `list()` vs `begin_delete(self.base.resource_group, ...)`
   to `critical` / `deletes what it should keep`): correct and consistent with `BUG-111`
   (`main:crc/azu/disk.py:78`, `:129-131` verified). Adding `:21-51` to the age row and the new
   `count` / accumulation row: correct (`main:crc/azu/disk.py:44`, `:61-69`, `:134`).
2. `Disk.__init__` annotations relaxed to `TagSpec | None` / `AgeSpec | None` / `str | None`
   (`crc/azu/disk.py:226-235`): annotation only, no runtime change; matches what `crc.py` passes.
3. `test_cleaner_protocol_matches_main` now compares the "Deleted disk" and summary log groups on
   `error_phase == "apply"` (`tests/test_azure_disk_parity.py:456-458`): strengthens the test, no
   assertion removed.

No round 1 fix changed behaviour or weakened a test.

### Independent review

- Parity, re-derived by hand: `filter_tags_any_key` / `exception_tags_any` / `notags_all` on
  `dict(disk.tags) if disk.tags else {}` give the same truth values and the same exceptions as
  `main:crc/azu/disk.py:83-109` for `None`, `{}` and populated tags (the `{}`-tags path short-circuits
  on `key in tags` before `tags[key] in values`, as main's `disk.tags and ...` does). `effective_age(r, age or {})`
  vs `retention_age or self.age`: `is_old({})` and `is_old(None)` both return True with the same
  debug log. `core_age.get_retention_age(disk.tags or {}, key or "")` logs and returns as
  `Service.get_retention_age` does for `None` tags / key. State test, log lines, summary lines and
  SDK argument order match.
- Safety: `plan()` makes only `disks.list`; `begin_delete` only in `delete_disk` (from `apply()` and
  `Disk.delete()`), never in a dry run. No age gate weakened, custom age tag still fails open as on
  main (`BUG-004`), no new per-resource isolation. Ratchet only shrinks; no SDK mypy override.
- Tests: parity cases compare calls, errors, deleted list, count, every log record and stdout with
  the frozen oracle; quirk checks assert the quirk on both oracle and new code; no skip/xfail;
  interleaving test pins the exact call sequence. Not vacuous.

### BLOCKING (fixed)

None.

### NIT

- Fixed (docs only): `crc/azu/README.md` said the adapter reads the clock "once per disk as main
  does"; main reads `datetime.datetime.now()` only for disks that pass the tag filters
  (`main:crc/azu/disk.py:112`, `:121`), the adapter calls `self.clock()` for every listed disk
  (`crc/azu/disk.py:292`). Not observable (the default clock has no side effects); wording corrected.
  The Coding summary line "read once per disk as main does" has the same imprecision (left).
- Left: `AzureDiskCleaner.apply()` in a dry run logs the summary a second time (round 1 NIT; the
  engine never calls `apply()` in a dry run).
- Left: `_is_old_enough` types `retention_age` as `Any` (`crc/azu/disk.py:187`); acceptable since
  `core_age.get_retention_age` returns `Any` (the parsed literal).

### New bugs

- One row added to `docs/refactor/bugs/pending/AZ-3.md`: a custom age tag that parses to a truthy
  non-dict (`True`, a float, a string) reaches `is_old` unchecked (`main:crc/service.py:300-318`,
  `:214-220`, `:237-262`); verified by running main's `Service` (`True` means 1 day, `1.5` raises
  `TypeError`).

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` | Success: no issues found in 100 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | 3919 passed |

Test count: 3919 (3715 at `2762dc3`, +204).

ROUND 2 VERDICT: APPROVE
