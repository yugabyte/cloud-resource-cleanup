# GCP-1 review

## Coding summary

Rung GCP-1: `crc/gcp/ip.py` refactored into a `Cleaner` (lane `refactor/lane-gcp`). No behaviour change.

- **Summary.** `IpCleaner` (`cloud="gcp"`, `kind="ip"`, registered with `@register("gcp", "ip")`
  through `build_ip_cleaner`) lists regions and addresses in `plan()` (read-only) and calls
  `delete_address` from `apply()` / `apply_region()` only. Filtering uses
  `policy.filter_substring_matches`, `policy.gcp_ip_exception_excludes` (inverted, BUG-001) and
  `policy.state_in` (RESERVED). The SDK sits behind the typed Protocols `GcpIpClient` and
  `AddressView`; `ComputeIpClient` builds a new SDK client for every call, as main does. `IP` is
  the thin adapter `crc.py` uses (constructor, `deleted_ips`, `default_state`, `get_deleted`,
  `count`, `delete()` unchanged; optional fifth argument `client`). `crc.py` is not changed.
  The module has no age gate, so there is no clock and no `context.now` use.
- **Parity.** `tests/legacy/gcp_ip.txt` is the byte-identical `git show main:crc/gcp/ip.py`.
  `tests/test_gcp_ip_parity.py` runs the oracle and the adapter against the same fake
  `compute_v1` clients over 59 cases (dry run, filters, inverted exception, status, API, region
  listing, iteration and delete errors, empty results, malformed addresses) comparing SDK calls
  in order, results, count, logs, stdout and errors; the same table runs through the registered
  Cleaner (plan then apply) and the logged summary lines match. Region listing has no
  connectivity fallback on main (BUGS pending), so listing errors propagate in both.
  Cleaner path differences, inherent to plan-then-apply and also accepted in the AWS rungs: all
  regions are listed before the first delete, so an error while listing a later region stops
  before any delete, and log lines interleave differently for more than one region (compared as
  multisets, exact for one region).
- **Test counts.** Before: 2703 passed. After: 2879 passed (+176: 154 in `tests/test_gcp_ip_parity.py`, 22 in `tests/test_gcp_ip.py`). No existing test changed.
- **Gates.** `ruff check .`, `mypy` (strict, no new override: google-cloud-compute is typed),
  `lint-imports`, `pytest -q`. `crc/gcp/ip.py` and `crc.gcp.ip` are removed from both ratchet
  lists in `pyproject.toml`.
- **Open issues.** New quirks are in `docs/refactor/bugs/pending/GCP-1.md` (12 rows, none fixed).
  ARCH-002 requires a README with the four sections in every directory, so
  `docs/refactor/bugs/README.md` and `docs/refactor/bugs/pending/README.md` were added; other
  lanes may add the same files (merge conflicts on those two READMEs are trivial).
- **Core change needed.** None. `crc/core/policy.py` already has the three functions used.
- **FILTER_VARIANTS.md change needed.** None for behaviour. Optional: the `gcp/ip.py` row cites
  main line numbers that are still correct (`:83`, `:87-89`, `:91-95`).
- **`crc/README.md` / `tests/legacy/README.md` / `tests/README.md` change needed.** Add `gcp_ip.txt`
  (exact `git show main:crc/gcp/ip.py`, used by `tests/test_gcp_ip_parity.py`) to
  `tests/legacy/README.md`; `crc/core/README.md` could say `gcp` / `ip` is now registered
  alongside the AWS cleaners; both files are outside this lane's edit rights.

## Round 1 (Argon)

Scope reviewed: `git diff 9b5c85a..HEAD` (`crc/gcp/ip.py`, `crc/gcp/README.md`, `pyproject.toml`,
`docs/refactor/bugs/pending/GCP-1.md`, `docs/refactor/bugs/README.md`,
`docs/refactor/bugs/pending/README.md`, `tests/legacy/gcp_ip.txt`, `tests/test_gcp_ip.py`,
`tests/test_gcp_ip_parity.py`) against `git show main:crc/gcp/ip.py`,
`git show main:crc/gcp/_base.py`, `AGENTS.md`, `docs/refactor/BUGS.md`,
`docs/refactor/FILTER_VARIANTS.md`, and `.cursor/skills/crc-python-refactor/REVIEW_CHECKLIST.md`.

### 1. Behaviour parity with `git show main:crc/gcp/ip.py`

- **SDK calls (order and arguments)** (`crc/gcp/ip.py:58-74`, `:142-168`, `:241-248` vs `main:crc/gcp/ip.py:70-107`, `main:crc/gcp/_base.py:6-13`):
  - `ComputeIpClient.list_regions` calls `compute_v1.RegionsClient().list(project=project_id)` and collects `[region.name for region in regions]`, identical to `crc/gcp/_base.py:get_gcp_regions`.
  - For each region in order, `list_addresses` calls `compute_v1.AddressesClient().list(project=project_id, region=region)`, logs `logging.info(address)`, reads `address.name`, skips when `address.status != self.default_state`, and evaluates both `filter_regex` and `exception_regex` before deciding whether to collect `name`.
  - When `not self.dry_run`, `apply_region` calls `compute_v1.AddressesClient().delete(project=project_id, region=region, address=ip)` and logs `logging.info(f"Deleting IP address: {ip}")` for each collected name in order, then extends `self.deleted_ips` after the region loop completes.
- **Filter, state, and age decisions** (`crc/gcp/ip.py:145-156`, `:170-175` vs `main:crc/gcp/ip.py:83-96`):
  - State check uses `policy.state_in(address.status, (self._state,))` (`"RESERVED"` by default, honouring `self.default_state` overrides on `IP`).
  - Name filter uses `policy.filter_substring_matches` (plain substring `any`, empty passes) and `policy.gcp_ip_exception_excludes` (inverted substring `exception_regex and not any(...)`, `BUG-001`). Both are evaluated unconditionally per `RESERVED` address so non-string entries in `exception_regex` raise `TypeError` even when `filter_regex` does not match, as on `main`.
  - No age gate exists on `main` or in the refactored cleaner (`FILTER_VARIANTS.md` section 4).
- **Log lines, errors, and dry-run path** (`crc/gcp/ip.py:76-82`, `:143`, `:167`, `:228` vs `main:crc/gcp/ip.py:62`, `:79`, `:106`, `:109-115`):
  - Every `logging.info` and `logging.warning` message is identical to `main`; nothing is printed to stdout.
  - Region listing, address listing/iteration, and delete errors propagate unchanged with no connectivity fallback; a mid-region delete error loses the record of earlier deletes in that region (`deleted_ips.extend` runs after the region's delete loop) and skips the summary warning.
  - In dry run, `AddressesClient().delete` is never called, matching names are collected into `deleted_ips` / `Plan.candidates`, and the dry-run summary warning is logged.

### 2. Oracle and parity test verification (`tests/legacy/gcp_ip.txt`, `tests/test_gcp_ip_parity.py`)

- Diffed `tests/legacy/gcp_ip.txt` against `git show main:crc/gcp/ip.py`: byte-identical (SHA-256 `c42ff12424ae5b56cab98329c67051a553e3936a80ff7fa82f9f797018699776`, 0 lines of diff).
- Verified `tests/test_gcp_ip_parity.py` executes the oracle (`tests/legacy/gcp_ip.txt`) and the new `IP` adapter and registered `IpCleaner` against the same `FakeGcp` clients and compares ordered SDK `calls`, `error`, `deleted`, `count`, `logs` (at `DEBUG`), and `stdout`.
- Tested a deliberate one-line behaviour change in `crc/gcp/ip.py` (inverting `policy.state_in` in `plan_region`): `pytest -q tests/test_gcp_ip_parity.py` failed with 89 failures immediately; reverted.

### 3. Architecture and guardrails

- `IpCleaner` implements `Cleaner` (`cloud = "gcp"`, `kind = "ip"`, `supports`, read-only `plan`, mutating `apply`) and is registered with `@register("gcp", "ip")` on `build_ip_cleaner`.
- `plan()` never mutates (`ARCH-005` passes on `crc/gcp/ip.py` as a non-legacy module; `delete_address` is only in `apply_region`, called from `apply` and `IP.delete`).
- Filtering uses `policy.filter_substring_matches`, `policy.gcp_ip_exception_excludes`, and `policy.state_in` as named in `docs/refactor/FILTER_VARIANTS.md`; no banned constructs (`ARCH-003`).
- SDK is behind the typed `GcpIpClient` and `AddressView` `Protocol`s.
- `crc/gcp/ip.py` (`crc.gcp.ip`) was removed from both ratchet lists in `pyproject.toml`; nothing was added (`ARCH-001`), and no mypy SDK override was needed because `google-cloud-compute` ships inline types.

### 4. Scope, lane rules, and documentation

- Verified `git diff --name-only 9b5c85a..HEAD`: none of the lane-forbidden files (`docs/refactor/BUGS.md`, `docs/refactor/STATUS.md`, `docs/refactor/FILTER_VARIANTS.md`, `crc/README.md`, `tests/README.md`, `tests/legacy/README.md`, `crc.py`, `crc/core/**`, or other cloud packages) were modified.
- `crc/gcp/README.md` is accurate and passes `ARCH-002` symbol sync (`AddressView`, `GcpIpClient`, `ComputeIpClient`, `IpCleaner`, `build_ip_cleaner()`, `IP`).
- Verified all 11 rows in `docs/refactor/bugs/pending/GCP-1.md` against `git show main:crc/gcp/ip.py`, `git show main:crc/gcp/_base.py`, and `git show main:crc.py`.

### BLOCKING (fixed)

None.

### NIT (left)

- Calling `IpCleaner.apply()` directly with `context.dry_run=True` logs the dry-run summary warning a second time after `plan()` and returns candidate IDs in `Result.succeeded` (`crc/gcp/ip.py:126-135`, `:159-168`); `crc.core.engine.run` never calls `apply()` when `context.dry_run` is `True` (`crc/core/engine.py:100`).
- `ComputeIpClient.list_regions` calls `compute_v1.RegionsClient().list(project=project_id)` directly (`crc/gcp/ip.py:58-60`) rather than `crc.gcp._base.get_gcp_regions` because `crc/gcp/_base.py` is still untyped in the legacy ratchet (`[no-untyped-call]` under `mypy --strict`); the SDK call sequence and return value are identical.
- Recorded two additional `main` quirks in `docs/refactor/bugs/pending/GCP-1.md` (multi-region non-atomicity on `AddressesClient().list` failure in a later region, `crc/gcp/ip.py:70-107`, `:109-115`; and bare-name reporting across regions in `deleted_ips`, `crc/gcp/ip.py:96`, `:107`, `crc.py:422`, `:780-783`) and added the parity case `non-string-exception-evaluated-when-filter-misses` in `tests/test_gcp_ip_parity.py:114-120`, `:413-416`.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` (`crc`, `tests`) | Success: no issues found in 85 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | 2879 passed |

Test count: 2703 before GCP-1 (`9b5c85a`), 2876 on `792d82d`, 2879 after Round 1 additions (+3).

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff 9b5c85a..HEAD` (coding commit `792d82d` and round 1 commit `ac72809`), checked
against `git show main:crc/gcp/ip.py`, `git show main:crc/gcp/_base.py`, `git show main:crc.py`,
`crc/core/policy.py`, `crc/core/cleaner.py`, `crc/core/engine.py` and `REVIEW_CHECKLIST.md`.

### Round 1 fixes assessment

- Round 1 made no code change to `crc/gcp/ip.py`. It added the parity case
  `non-string-exception-evaluated-when-filter-misses` (`tests/test_gcp_ip_parity.py:114-120`,
  quirk check `:413-416`) and two pending rows. The case is correct and not vacuous: changing
  `crc/gcp/ip.py:149` to `excluded = matches_filter and self._excluded(name)` (short-circuit,
  which main does not do) fails 3 tests; reverted. No existing assertion was weakened or removed.
- Both round 1 pending rows (multi-region non-atomicity, bare-name reporting) are correct; their
  citations `crc/gcp/ip.py:70-107`, `:96`, `:107`, `:109-115` and `crc.py:422`, `:780-783` match
  `git show main:...`.

### Independent review

- **Parity.** `state_in(status, (state,))` is `status in (state,)`, the negation of main's
  `status != default_state` for strings; `filter_substring_matches(x or (), name)` and
  `gcp_ip_exception_excludes(x or (), name)` (`crc/core/policy.py:195-204`) give the same truth
  values as main `crc/gcp/ip.py:87-93`, both evaluated unconditionally. Call order in
  `IP.delete()` (`crc/gcp/ip.py:241-248`) is main's per region: list, log, filter, delete, then
  `deleted_ips.extend` after the region. `default_state`, `dry_run`, `project_id` and the filters
  are read at `delete()` time as on main. Summary log lines are identical. `Candidate` / `Skipped`
  do no validation, so malformed names behave as on main.
- **Oracle.** `tests/legacy/gcp_ip.txt` is identical to `git show main:crc/gcp/ip.py` (empty diff).
  A second mutation (stop after the first region in `IP.delete()`) fails 12 parity tests.
- **Safety / architecture.** `plan()` only lists; `delete_address` is reachable only from
  `apply_region()` and never with `dry_run=True`. No age gate exists on main, none is added; no
  error is swallowed; no retry added. SDK types stay behind `GcpIpClient` / `AddressView`.
  Registration via `@register("gcp", "ip")`, discovered on import as for the AWS cleaners.
  Ratchet: only `crc/gcp/ip.py` / `crc.gcp.ip` removed, nothing added, no mypy override. No
  lane-forbidden file touched. `crc/gcp/README.md` sections and symbol list are accurate.
- **Pending rows.** All 11 earlier rows re-verified against main line numbers; none duplicates a
  `BUGS.md` row (only `BUG-001` concerns GCP IP).

### BLOCKING (fixed)

None.

### Fixed (docs only)

- Coding summary said "58 cases"; the table has 59 since round 1 (`tests/test_gcp_ip_parity.py`
  `CASES`). Updated, and the open-issues row count updated to 12.

### NIT (left)

- `IP.__init__` uses `client or ComputeIpClient()` (`crc/gcp/ip.py:218`); `if client is None`
  would not replace an injected client that happens to be falsy. Test-only seam, no CLI effect.
- `plan_region` reads `address.status` a second time for `Candidate.state`
  (`crc/gcp/ip.py:152`); main reads it once. Harmless for proto-plus `Address` objects.
- `IP.get_deleted` is typed `list[str]` (`crc/gcp/ip.py:221`) but, as on main, a non-string
  address name (malformed input, parity case `non-string-name-no-filter`) is returned as is.

### New pending bug

- `docs/refactor/bugs/pending/GCP-1.md`: every address is logged in full at INFO before any
  filter (`logging.info(address)`, main `crc/gcp/ip.py:78-79`), cosmetic, low.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` | Success: no issues found in 85 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | 2879 passed |

Test count: 2879 (154 in `tests/test_gcp_ip_parity.py`, 22 in `tests/test_gcp_ip.py`; 2703 before
the rung).

ROUND 2 VERDICT: APPROVE
