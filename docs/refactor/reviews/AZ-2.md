# AZ-2 review

## Coding summary

Rung AZ-2: `crc/azu/nic.py` is a registered `Cleaner` (`@register("azure", "nic")`), with `NIC` kept
as a thin adapter. Status: complete; ruff, mypy strict, lint-imports and pytest are green (`make check` equivalent).

- Cleaner: `AzureNicCleaner` (`plan()` = `list_all` twice, as main, plus policy filtering,
  read-only; `apply()` = `begin_delete(rg, name).result()` in plan order with main's three attempts,
  swallowed errors and log lines; failed NICs are also reported in `Result.failed`). SDK behind the
  `NicNetworkClient` / `NicOperations` / `DeletePoller` Protocols; the client factory is injected
  through the constructor. Filtering: `policy.nic_name_included` and `policy.nic_name_excluded`
  (FILTER_VARIANTS.md section 3, `azu/nic.py`). `exception_regex` is still evaluated for every
  unattached NIC and guarded by truthiness, as on main. No age gate and no tag filter exist on main,
  so `crc.core.age` is not used (recorded as a pending bug).
- Adapter: `NIC(resource_group, dry_run, name_regex, exception_regex)` keeps `delete()`,
  `get_deleted`, `delete_count`, `nics_names_to_delete`, `base`, `dry_run`, `name_regex`,
  `exception_regex`. `delete()` iterates the lazy `list_all()` and decides and deletes NIC by NIC,
  so call order (paging interleaved with deletes, one client lookup per attempt), mid-run error
  behaviour and the cumulative list are those of main. `crc.py` is unchanged.
- Cleaner path: `plan()` lists and filters everything first, then `apply()` deletes. The only
  differences from main are the interleaving of paging and deletes, and that a listing or decision
  error (`None` name, non-string exception entry, paging error) now happens before any delete
  instead of after earlier deletes. Summary log lines are the same: dry run logs the "which will be
  deleted" warning from `plan()`, a real run logs the two "deleted" warnings from `apply()`; an
  empty `name_regex` logs "No Azure NICs to delete." from `plan()` and `apply()` does nothing.
- Tests: baseline `pytest -q` 3037 passed; now 3203 passed (+166): `tests/test_azure_nic_parity.py`
  (60 cases through the adapter, the same 60 through the registered Cleaner, 28 quirk checks, plus
  the double-listing, subscription-wide/resource-group, repeated-delete and factory tests, 152 in
  all), `tests/test_azure_nic.py` (14 focused `plan()` / `apply()` tests). All existing tests
  unchanged. Oracle: `tests/legacy/azu_nic.txt` is byte-identical to `git show main:crc/azu/nic.py`.
- The module has no age gate and lists no regions, so there are no age edges and no region fallback
  or region auth-error cases to cover.
- Ratchet: `crc/azu/nic.py` removed from ruff `extend-exclude` and the mypy override `crc.azu.nic`.
  No per-SDK mypy override was needed.
- Open issues: new bugs and quirks in `docs/refactor/bugs/pending/AZ-2.md` (13 rows, ID `NEW`; two added in round 2).
  No existing BUGS.md row is specific to this module (BUG-017 and BUG-028 concern `crc.py`).
- Needed changes outside this lane (not made): `crc/README.md`, `tests/README.md` and
  `tests/legacy/README.md` should list `tests/legacy/azu_nic.txt`, `tests/test_azure_nic_parity.py`,
  `tests/test_azure_nic.py` and `tests/helpers_azure_nic.py`. `FILTER_VARIANTS.md` section 3: the
  `azu/nic.py` row can cite `policy.nic_name_included` / `policy.nic_name_excluded`, and add that
  `exception_regex` is evaluated for every unattached NIC (even when `name_regex` did not match).
  No core change needed. `STATUS.md` and `BUGS.md` rows are for the integrator.

## Round 1 (Argon)

Scope reviewed: `git diff 4f5683b..HEAD` (`crc/azu/nic.py`, `crc/azu/README.md`, `pyproject.toml`,
`docs/refactor/bugs/pending/AZ-2.md`, `docs/refactor/reviews/AZ-2.md`,
`tests/legacy/azu_nic.txt`, `tests/helpers_azure_nic.py`, `tests/test_azure_nic.py`,
`tests/test_azure_nic_parity.py`) against `git show main:crc/azu/nic.py`, `AGENTS.md`,
`docs/refactor/BUGS.md` and `.cursor/skills/crc-python-refactor/REVIEW_CHECKLIST.md`.

### 1. Behaviour parity with `git show main:crc/azu/nic.py`

- **SDK calls (order and arguments)** (`crc/azu/nic.py:134-162`, `:249-265` vs `main:crc/azu/nic.py:83-84`, `:116-118`):
  - `NIC.delete()` returns early with `No Azure NICs to delete.` when `not self.name_regex`, else calls `self.base.get_network_client().network_interfaces.list_all()` twice (discarding the first), iterates the second lazily, and for each selected unattached NIC calls `self.base.get_network_client().network_interfaces.begin_delete(self.base.resource_group, nic.name).result()` (positional arguments) up to three attempts before advancing the iterator.
- **Filter and age decisions** (`crc/azu/nic.py:103-105`, `:164-178` vs `main:crc/azu/nic.py:63-65`, `:85-102`):
  - No age gate or tag filter exists on `main` (recorded in `docs/refactor/bugs/pending/AZ-2.md`).
  - `decide()` checks `nic.virtual_machine` first, then evaluates `policy.nic_name_included` and `policy.nic_name_excluded` (guarded by `if self._exception_regex`) in `main`'s exact order, even when `included` is `False`.
- **Log lines, printed messages, errors and dry-run path** (`crc/azu/nic.py:66-78`, `:102-132`, `:139-162`, `:233-239`, `:249-265` vs `main:crc/azu/nic.py:48-77`, `:104-129`):
  - All `logging.info`, `logging.warning` and `logging.error` messages match `main` verbatim (`No Azure NICs to delete.`, `Deleted NIC: ...`, `Error occurred while deleting NIC ...: ...`, `Retrying deletion of NIC ...`, `Failed to delete the NIC - ...`, `Count of items in nics_names_to_delete: ...`, and real/dry-run summaries).
  - Three-attempt retry, swallowed `Exception` on delete/result failure, and propagation of listing/filter errors match `main`.

### 2. Oracle and parity test verification (`tests/legacy/azu_nic.txt`, `tests/test_azure_nic_parity.py`)

- Diffed `tests/legacy/azu_nic.txt` against `git show main:crc/azu/nic.py`: byte-for-byte identical (0 lines of diff).
- Verified `tests/test_azure_nic_parity.py` runs both the oracle (`tests/legacy/azu_nic.txt`) and the refactored code (`NIC` and registered `AzureNicCleaner`) against the same `FakeAzure` fake and compares ordered SDK `calls` (including double `list_all`, lazy `yield` interleaving, `begin_delete` and `result`), `error`, `deleted`, `count`, `logs`, `count_logs` and `stdout`.
- Tested a deliberate one-line behaviour change in `crc/azu/nic.py` (`if not nic.virtual_machine:` in `decide`), confirmed `python3 -m pytest -q tests/test_azure_nic_parity.py` failed immediately, and reverted it.

### 3. Architecture and lane rules

- `AzureNicCleaner` implements `Cleaner` (`cloud = "azure"`, `kind = "nic"`, `supports`, read-only `plan`, mutating `apply`) and is registered with `@register("azure", "nic")` on `build_nic_cleaner`.
- `plan()` never mutates (`ARCH-005` passes; `begin_delete` is only in `attempt_delete`, called from `apply()` and `NIC.delete()`).
- Filtering uses `crc.core.policy.nic_name_included` and `crc.core.policy.nic_name_excluded` as named in `docs/refactor/FILTER_VARIANTS.md`.
- SDK is behind the typed `DeletePoller`, `NicOperations` and `NicNetworkClient` `Protocol`s.
- `crc/azu/nic.py` (`crc.azu.nic`) left both legacy ratchet lists in `pyproject.toml`; nothing was added.
- `crc.py`, `crc/core/**`, shared docs (`BUGS.md`, `STATUS.md`, `FILTER_VARIANTS.md`, `crc/README.md`, `tests/README.md`, `tests/legacy/README.md`), and other cloud packages are untouched. `crc/azu/README.md` is accurate and passes `ARCH-002` symbol sync.

### BLOCKING (fixed)

1. **`docs/refactor/bugs/pending/AZ-2.md` cited `crc/azu/nic.py:14-21` for the `__init__` signature instead of `crc/azu/nic.py:16-22` on `main`** (`docs/refactor/bugs/pending/AZ-2.md:10`, `:18-19`):
   - Verified all 11 rows against `git show main:crc/azu/nic.py`; fixed the `__init__` signature citation to `crc/azu/nic.py:16-22` and added `:13` (misleading class docstring) and `:35` (`nics_names_to_delete` initialization) to rows 10 and 11.
2. **`NIC.__init__` annotated `name_regex` and `exception_regex` as `list[str]` instead of `Sequence[str] | None` (`crc/azu/nic.py:203-209`)**:
   - `crc.py` passes `None` when `--name_regex` or `--exception_regex` is omitted on the CLI (`crc.py:938-952`, `:1190-1191`, `:684`), and `AzureNicCleaner.__init__` accepts `Sequence[str] | None` (`crc/azu/nic.py:91-92`). Relaxed `NIC.__init__` parameter annotations to `Sequence[str] | None` to match `AzureNicCleaner` and `crc.py`.

### NIT (left)

- Calling `AzureNicCleaner.apply()` directly with `context.dry_run=True` when `name_regex` is non-empty logs the dry-run summary warning a second time after `plan()` and returns `Result(succeeded=...)` (`crc/azu/nic.py:119-132`); `crc.core.engine.run` does not call `apply()` in a dry run (`crc/core/engine.py:100`).

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` (`crc`, `tests`) | Success: no issues found in 91 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | 3203 passed |

Test count: 3037 before AZ-2 (`4f5683b`), 3203 now (+166).

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff 4f5683b..HEAD` (both AZ-2 commits), read against `git show main:crc/azu/nic.py`,
`git show main:crc.py`, `crc/core/policy.py`, `crc/core/engine.py`, `crc/core/cleaner.py` and
`REVIEW_CHECKLIST.md`.

### Round 1 fixes

- `NIC.__init__` annotations `list[str]` to `Sequence[str] | None` (`crc/azu/nic.py:203-209`):
  correct and type-only. `crc.py:684` passes `args.get("name_regex")` / `args.get("exception_regex")`
  (`crc.py:1190-1191`), which are `None` when the flags are omitted, and the `None` cases are covered
  by parity cases `name-regex-none-returns-early` and `exception-none`. No behaviour change.
- `docs/refactor/bugs/pending/AZ-2.md` citations: re-verified against `git show main:crc/azu/nic.py`
  (`:13` docstring, `:16-22` signature, `:35` list init, `:63-65`, `:83-87`, `:96-102`, `:113-129`).
  All correct. No test was touched by round 1.

### Independent review

- Parity: `decide()` keeps main's evaluation order (`virtual_machine`, then `name_regex`, then
  `exception_regex` under truthiness even when not included); `policy.nic_name_included` /
  `nic_name_excluded` are `any(p in text for p in patterns)`, the same expression and short-circuit
  as main `:96-101`. `attempt_delete` keeps three attempts, the same log lines and the swallow of
  `Exception` only (not `BaseException`). The adapter iterates `list_nics()` lazily and appends after
  each successful attempt, so call order and the cumulative list match main; `list_all` is still
  requested twice. `tests/legacy/azu_nic.txt` is byte-identical to main.
- Tests assert outcomes: `test_matches_main` compares ordered SDK calls (including lazy `yield`
  interleaving), errors, deleted list, count, log records and stdout; the Cleaner path compares
  list/delete/result/client calls and logs; every quirk check also asserts the oracle shows it.
  No skip, xfail or vacuous assertion found.
- Safety: `plan()` makes no mutating call (`begin_delete` only in `attempt_delete`, reached from
  `apply()` and `NIC.delete()`); the engine never calls `apply()` in a dry run
  (`crc/core/engine.py:100`). No age or tag gate existed on main, none weakened.
- Architecture and typing: decorator registration, constructor-injected client factory, SDK behind
  Protocols, `Any` only for SDK NIC objects; ratchet lines removed only. README symbol list and
  safety section match the code.

### BLOCKING (fixed)

None in code or tests. One docs fix: the pending row about `.result()` failure retrying
`begin_delete` had effect `run fails`, but every error is swallowed (`main:crc/azu/nic.py:122-129`),
so the run cannot fail there; changed to `cosmetic` with the actual effect (a deleted NIC can be
reported as failed).

### New findings recorded (`docs/refactor/bugs/pending/AZ-2.md`)

- A NIC listed from another resource group is reported as deleted when `begin_delete` on the
  configured group hits a missing resource (Azure returns 204): `main:crc/azu/nic.py:83-84`, `:116-121`.
- `--name_regex` help says it only applies to AWS keypairs and GCP IPs, yet it is the only NIC
  selector: `main:crc.py:1084`, `:826`, `:1573-1574`.

### NIT (left)

- `AzureNicCleaner.plan()` records `Skipped(id=getattr(nic, "name", ""))`, so an attached NIC whose
  `name` is `None` or an int yields a `Skipped` with a non-`str` id (`crc/azu/nic.py:114`); harmless
  today (no consumer on the legacy path) but violates the dataclass annotation.
- Round 1's NIT stands: `apply()` called directly in a dry run logs the dry-run summary again
  (`crc/azu/nic.py:119-132`); unreachable through `crc.core.engine.run`.
- `ClientFactory` and `DELETE_ATTEMPTS` are module-level public names not in the README symbol list
  (ARCH-002 passes; `ip.py` follows the same pattern).

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`: `ruff check .` all
checks passed; `mypy` no issues in 91 source files; `lint-imports` 5 kept, 0 broken;
`pytest -q` 3203 passed.

Test count: 3203 (3037 before AZ-2, +166; unchanged by round 2).

ROUND 2 VERDICT: APPROVE
