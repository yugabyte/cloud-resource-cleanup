# AZ-1 review

## Coding summary

Rung AZ-1: `crc/azu/ip.py` is a registered `Cleaner` (`@register("azure", "ip")`), with `IP` kept as
a thin adapter. Status: complete, `make check` equivalent green (ruff, mypy strict, lint-imports,
pytest).

- Cleaner: `AzureIpCleaner` (`plan()` = `list_all` + policy, read-only; `apply()` = `begin_delete`
  in plan order). SDK behind the `IpNetworkClient` / `PublicIpOperations` Protocols; the client
  factory is injected through the constructor. Filtering: `policy.filter_tags_any_key` and
  `policy.should_skip_by_tags` (FILTER_VARIANTS.md section 3, `azu/ip.py`) over `normalize_tags`.
  No age gate exists on main, so `crc.core.age` is not used (recorded as a pending bug).
- Adapter: `IP(resource_group, dry_run, filter_tags, exception_tags, notags)` keeps `delete()`,
  `get_deleted`, `count`, `deleted_ips`, `base`, `dry_run`, `filter_tags`, `exception_tags`,
  `notags`. `delete()` iterates the lazy `list_all()` and decides and deletes IP by IP, so the call
  order (paging interleaved with `begin_delete`, one `get_network_client()` per delete), the
  mid-run error behaviour and the cumulative `deleted_ips` are those of main. `crc.py` is unchanged.
- Cleaner path: `plan()` lists and filters everything first, then `apply()` deletes. The only
  differences from main are therefore the interleaving of paging and deletes, and that a decision
  error (BUG-013 `TypeError`, a missing attribute, a paging error) now happens before any delete
  instead of after earlier deletes. Summary log lines are the same: dry run logs the "which will be
  deleted" warning from `plan()`, a real run logs the two "deleted" warnings from `apply()`.
- Tests: baseline `pytest -q` 2703 passed; now 2840 passed (+137): `tests/test_azure_ip_parity.py`
  (50 cases through the adapter, the same 50 through the registered Cleaner, 23 quirk checks, plus
  the subscription-wide/resource-group, repeated-delete and factory tests, 126 in all),
  `tests/test_azure_ip.py` (11 focused `plan()` / `apply()` tests). All existing tests unchanged.
  Oracle: `tests/legacy/azu_ip.txt` is byte-identical to `git show main:crc/azu/ip.py`.
- The module lists no regions, so there is no region fallback or auth-error case to cover.
- Ratchet: `crc/azu/ip.py` removed from ruff `extend-exclude` and the mypy override `crc.azu.ip`.
  No per-SDK mypy override was needed.
- Open issues: new bugs and quirks in `docs/refactor/bugs/pending/AZ-1.md` (9 rows, ID `NEW`; one added in round 2).
  Existing BUG-013 is kept as is.
- ARCH-002 requires a README in every directory with files, so `docs/refactor/bugs/README.md` and `docs/refactor/bugs/pending/README.md` were added; other lanes adding the same paths will conflict on merge (keep either copy).
- Needed changes outside this lane (not made): `crc/README.md` and `tests/README.md` /
  `tests/legacy/README.md` should list `tests/legacy/azu_ip.txt`, `tests/test_azure_ip_parity.py`,
  `tests/test_azure_ip.py` and `tests/helpers_azure_ip.py`. `FILTER_VARIANTS.md` section 3: the
  `azu/ip.py` row can now cite `policy.filter_tags_any_key` and `policy.should_skip_by_tags`, and
  note that the `TypeError` for untagged IPs is kept in the cleaner (as BUG-011 for AWS), not in
  policy, so item 3 of section 7 is "kept in the cleaner, fix in a behaviour rung". No core change
  needed. `STATUS.md` and `BUGS.md` rows are for the integrator.

## Round 1 (Argon)

Scope reviewed: `git diff 9b5c85a..HEAD` (`crc/azu/ip.py`, `crc/azu/README.md`, `pyproject.toml`,
`docs/refactor/bugs/README.md`, `docs/refactor/bugs/pending/README.md`,
`docs/refactor/bugs/pending/AZ-1.md`, `docs/refactor/reviews/AZ-1.md`,
`tests/legacy/azu_ip.txt`, `tests/helpers_azure_ip.py`, `tests/test_azure_ip.py`,
`tests/test_azure_ip_parity.py`) against `git show main:crc/azu/ip.py`, `AGENTS.md`,
`docs/refactor/BUGS.md` and `.cursor/skills/crc-python-refactor/REVIEW_CHECKLIST.md`.

### 1. Behaviour parity with `git show main:crc/azu/ip.py`

- **SDK calls (order and arguments)** (`crc/azu/ip.py:107-117`, `:203-224` vs `main:crc/azu/ip.py:59`, `:87-90`):
  - `IP.delete()` calls `self.base.get_network_client().public_ip_addresses.list_all()`, iterates lazily, and for each selected address when `not self.dry_run` calls `self.base.get_network_client().public_ip_addresses.begin_delete(resource_group_name=self.base.resource_group, public_ip_address_name=ip.name)` before advancing the iterator.
- **Filter and age decisions** (`crc/azu/ip.py:119-138` vs `main:crc/azu/ip.py:62-85`):
  - No age gate exists on `main` (recorded in `docs/refactor/bugs/pending/AZ-1.md`).
  - `decide()` evaluates `filter_tags` (`policy.filter_tags_any_key`), `exception_tags` (`policy.should_skip_by_tags`), and `notags` (`policy.should_skip_by_tags`) in `main`'s exact order, then checks `ip.ip_configuration is None`.
- **Log lines, printed messages, errors and dry-run path** (`crc/azu/ip.py:50-56`, `:93-105`, `:117`, `:195-201`, `:221-224` vs `main:crc/azu/ip.py:47-53`, `:86-99`):
  - All `logging.info` and `logging.warning` messages match `main` verbatim (`Deleted IP address: ...`, `count of items in deleted_ips: ...`, real and dry-run summaries).
  - `BUG-013` (`TypeError: argument of type 'NoneType' is not iterable` when `ip.tags is None` and `exception_tags` or `notags` is non-empty) and mid-run error propagation match `main`.

### 2. Oracle and parity test verification (`tests/legacy/azu_ip.txt`, `tests/test_azure_ip_parity.py`)

- Diffed `tests/legacy/azu_ip.txt` against `git show main:crc/azu/ip.py`: byte-for-byte identical (0 lines of diff).
- Verified `tests/test_azure_ip_parity.py` runs both the oracle (`tests/legacy/azu_ip.txt`) and the refactored code (`IP` and registered `AzureIpCleaner`) against the same `FakeAzure` fake and compares ordered SDK `calls` (including lazy `yield` interleaving), `error`, `deleted`, `count`, `logs` and `stdout`.
- Tested a deliberate one-line behaviour change in `crc/azu/ip.py` (`if ip.ip_configuration is None:` in `decide`), confirmed `python3 -m pytest -q tests/test_azure_ip_parity.py` failed immediately, and reverted it.

### 3. Architecture and lane rules

- `AzureIpCleaner` implements `Cleaner` (`cloud = "azure"`, `kind = "ip"`, `supports`, read-only `plan`, mutating `apply`) and is registered with `@register("azure", "ip")` on `build_ip_cleaner`.
- `plan()` never mutates (`ARCH-005` passes; `begin_delete` is only in `delete_candidate`, called from `apply()` and `IP.delete()`).
- Filtering uses `crc.core.policy.filter_tags_any_key`, `crc.core.policy.should_skip_by_tags`, and `crc.core.policy.normalize_tags` as named in `docs/refactor/FILTER_VARIANTS.md`.
- SDK is behind the typed `PublicIpOperations` and `IpNetworkClient` `Protocol`s.
- `crc/azu/ip.py` (`crc.azu.ip`) left both legacy ratchet lists in `pyproject.toml`; nothing was added.
- `crc.py`, `crc/core/**`, shared docs (`BUGS.md`, `STATUS.md`, `FILTER_VARIANTS.md`, `crc/README.md`, `tests/README.md`, `tests/legacy/README.md`), and other cloud packages are untouched. `crc/azu/README.md` is accurate and passes `ARCH-002` symbol sync.

### BLOCKING (fixed)

1. **`AzureIpCleaner.decide` normalised `ip.tags` before tag matching (converting `None` tag values to `""`) and returned early on `not filter_match` before evaluating `exception_tags` and `notags` (`main:crc/azu/ip.py:62-78`)** (`crc/azu/ip.py:119-138`, `tests/test_azure_ip_parity.py:159-197`, `:387-402`):
   - On `main`, a tag with value `None` (`{"env": None}`) does not match `[""]` (`None in [""]` is `False`) and raises `TypeError` against a string spec (`None in "testing"`), whereas normalising `None` to `""` before `filter_tags_any_key` / `should_skip_by_tags` made `"" in [""]` and `"" in "testing"` return `True`. Furthermore, `main` evaluates `exception_tags_match` and `no_tags_match` unconditionally before the combined `if`, so a `TypeError` in `exception_tags` or `notags` (for example a non-iterable spec value on a present key) raises even when `filter_tags_match` is `False` or `exception_tags_match` is already `True`.
   - Updated `decide` to evaluate `policy.filter_tags_any_key` and `policy.should_skip_by_tags` directly against `ip.tags` in `main`'s exact order and normalize tags with `policy.normalize_tags` when constructing `Candidate`. Added 7 parity cases and quirk checks to `tests/test_azure_ip_parity.py`.
2. **`test_cleaner_protocol_matches_main` used `assert new.logs == old.logs[: len(new.logs)]` on error cases, which would pass vacuously if `new.logs` were empty** (`tests/test_azure_ip_parity.py:331-339`):
   - Tightened `test_cleaner_protocol_matches_main` to assert `new.logs == []` when `case.fails_in_plan` and `new.logs == old.logs[:-1]` for all other cases (including mid-apply delete errors).
3. **`docs/refactor/bugs/pending/AZ-1.md` cited `crc/azu/ip.py:21-26` for the `__init__` signature instead of `crc/azu/ip.py:14-21` on `main`, and row 8 noted the pre-fix `normalize_tags` discrepancy** (`docs/refactor/bugs/pending/AZ-1.md:12`, `:18`):
   - Verified all 8 rows against `git show main:crc/azu/ip.py`; fixed the `__init__` signature citation to `crc/azu/ip.py:14-21` and updated row 8 to reflect preserved `main` semantics.

### NIT (left)

- Calling `AzureIpCleaner.apply()` directly with `context.dry_run=True` logs the dry-run summary warning a second time after `plan()` and returns `Result(succeeded=...)` (`crc/azu/ip.py:97-105`); `crc.core.engine.run` does not call `apply()` in a dry run (`crc/core/engine.py:99-100`).

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` (`crc`, `tests`) | Success: no issues found in 86 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | 2861 passed in 9.66s |

Test count: 2703 before AZ-1 (`9b5c85a`), 2840 before review, 2861 after review additions (+21).

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff 9b5c85a..HEAD` (both commits, including round 1's fixes), checked against
`git show main:crc/azu/ip.py`, `git show main:crc.py`, `CLEANUP_SAFETY.md`, `crc/core/policy.py`
and the review checklist.

### Round 1 fixes assessment

1. **`decide()` tag parity: correct.** `crc/azu/ip.py:117-136` now passes the raw `ip.tags` to
   `policy.filter_tags_any_key` / `policy.should_skip_by_tags`, and both reduce to
   `policy._key_matches` (`crc/core/policy.py:89-90`), the same `key in tags and (not value or
   tags[key] in value)` expression as `main:crc/azu/ip.py:65`, `:71`, `:76`. `should_skip_by_tags`
   with one empty spec adds only `any(())` / `bool({}) and ...` (both `False`, no evaluation), so
   the generator short-circuiting is the same. All three matches are still computed before the
   decision (main `:62-78`), and `ip.ip_configuration` is read only when filter passes and
   neither skip matches (main `:80-85`). `normalize_tags` runs only after the IP is selected and
   only fills `Candidate.tags`. The `self._x or {}` defaults change only falsy specs (`None`/`{}`),
   which main also treats as "no spec". No behaviour change; the 7 added cases do fail on the
   pre-fix code.
2. **Tightened `test_cleaner_protocol_matches_main`: correct and not vacuous.**
   `old.logs[:-1]` drops exactly the `count` log that `_run` produces and `_run_cleaner` does
   not, so the assertion is an exact equality. Checked by hand for `delete-error-mid-run`
   (two `Deleted` lines in both) and `list-all-error` (no lines in either).
3. **Pending-bug citations: correct.** All 8 round 1 rows re-checked against
   `git show main:crc/azu/ip.py`; every line range matches.

No test was weakened, skipped or deleted. Existing tests are unchanged
(`git diff 9b5c85a..HEAD --stat` touches only new test files).

### Independent review

- Legacy path parity: `IP.delete()` (`crc/azu/ip.py:210-222`) makes the same factory calls as
  main (one `get_network_client()` to list, one per delete, `:107`, `:111`), interleaves lazy
  paging with deletes, and evaluates `ip.name` after `get_network_client()` on the delete path.
  `Base` caches the client, so the extra lookup is not observable. `crc.py` still calls
  `AZU_IP(resource_group, dry_run, filter_tags, exception_tags, notags)` positionally
  (`crc.py:204-210`, same as `main:crc.py:190-196`). Constructor, attributes and log text are unchanged.
- Safety: `plan()` makes no mutating call; `begin_delete` runs only from `delete_candidate`
  (via `apply()` and `IP.delete()`) and never in a dry run. No new exception handling, so no
  errors are swallowed. No age gate is added or weakened (none exists on main). The registry
  factory builds `Base` only when it is called, so there is no import-time cloud call.
- Tests: the parity suite compares ordered SDK calls (including `yield` events), errors, the
  deleted list, `count`, log records (name, level, text) and stdout against the byte-identical
  oracle. Quirk checks also run against the oracle, so a broken oracle cannot pass them by accident.
- Architecture and typing: Protocols for the SDK edge, `Any` only for SDK items, injected factory,
  decorator registration. Only `crc/azu/ip.py` / `crc.azu.ip` were removed from the ratchet, and
  nothing was added. The README symbol list is synced (ARCH-002 passes).

### BLOCKING (fixed)

1. **Wrong safety ID for the missing age gate** (`docs/refactor/bugs/pending/AZ-1.md:12`,
   `crc/azu/README.md:37`). Both said SAF-006, but `CLEANUP_SAFETY.md:46-47` (SAF-006) only states
   that `Service.is_old({})` returns True. The rule that a destructive module needs an age gate in
   `__init__` is SAF-011 (`CLEANUP_SAFETY.md:63`). Both now cite SAF-011. Doc-only change.

### NIT (left)

- `crc/azu/ip.py:90`: `Skipped(id=getattr(ip, "name", ""))` handles a missing `name`, but
  `decide()` at `:136` reads `ip.name` directly. The IP is already skipped at that point, so the
  difference is harmless.
- `tests/test_azure_ip_parity.py:383`: the `missing-ip-configuration-attribute` quirk check only
  asserts `o.error is not None`. The parity test pins the exact `AttributeError`, so nothing is lost.
- `tests/test_azure_ip_parity.py:421-431`: membership checks (`in azure.calls`). The exact call
  sequence is already pinned by `test_matches_main`.
- Pending row 1 (subscription-wide list, resource-group delete) could also say that when the
  same-named IP in the configured group is attached, Azure rejects the delete and the run aborts
  (row 6 shape). Not verified against live Azure; left to the integrator.
- Round 1's NIT (`apply()` called directly in a dry run logs the summary twice) stands:
  `crc/core/engine.py:99-100` never calls `apply()` in a dry run.

### New bugs

One quirk added to `docs/refactor/bugs/pending/AZ-1.md`: the `count` property logs on every
read (`main:crc/azu/ip.py:46-53`, log at `:52`), and `crc.py` on main never reads it. The new
code keeps this, and the parity log comparison pins it.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check` (after the fix):

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` (`crc`, `tests`) | Success: no issues found in 86 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | 2861 passed |

Test count: 2703 at `9b5c85a`, 2861 now (+158). Unchanged by round 2, which changed docs only.

ROUND 2 VERDICT: APPROVE
