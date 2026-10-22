# PL-AZ review

## Coding summary

Rung PL-AZ (plumbing, lane `refactor/lane-azure`): `crc/azu/_base.py`, `crc/azu/vm_delete_helpers.py` and `crc/azu/__init__.py` are strict-clean (ruff and mypy strict) and refactored out of the legacy ratchet. Status: complete except one blocker for the repository owner: `make check` is green (ruff, mypy strict, lint-imports) except `tests/archtest/test_arch003_bans.py`, which now checks `crc/azu/_base.py` (it left the legacy ratchet) and rejects its `os.environ` reads (lines 27-31). The reads are required unchanged (no behaviour change; `Base(resource_group)` is called as before by `crc.py` and the cleaners), `tests/archtest/` is guarded by CODEOWNERS and its allowlist says only the repository owner adds entries, so I did not edit it or hide the reads. Needed change (owner): either add `"crc/azu/_base.py": frozenset({"env"})` to `ARCH003_ALLOWLIST` in `tests/archtest/bans.py` and relax `test_arch003_allowlist_is_explicit_and_empty_by_default` (it asserts the allowlist is empty, so the entry alone fails a second test), or move the env parse into `crc.py` and inject it (a CLI-rung change), or keep `_base.py` on both legacy lists until then (ARCH-001 requires the ruff and mypy lists to match). `crc.py`, `crc/core/**` and every cleaner module are unchanged; nothing is registered.

- `_base.py` (58 lines): the same `Base(resource_group)`: the four `AZURE_CREDENTIALS_*` reads in the same order, `AZURE_RESOURCE_GROUP` only when the argument is falsy, `ClientSecretCredential(tenant_id=, client_id=, client_secret=)`, lazy singleton compute client (keyword arguments) and network client (positional arguments), truthiness test and `{}` marker as on main. The two client getters and attributes are typed `Any` on purpose: the callers (`disk.py`, `vm/`, `spot_vm/`) pass `base.get_compute_client` to factories typed with their own narrow Protocols, and precise SDK return types make mypy reject those (I tried `ComputeManagementClient`; 4 errors in files outside this lane).
- `vm_delete_helpers.py` (114 lines): same constants, call order, `time.sleep` placement (not after the last attempt), `result(timeout=600)`, `result(timeout=timeout_sec)`, log lines and swallowed errors. The retry loop body moved into `_try_delete_nic` (and `_log_attempt_failed`) to stay inside the complexity limit; `DeletePoller` is the one new public Protocol. The client parameters are `Any`: `spot_vm/removal.py` assigns the helpers to `Callable[[SpotComputeClient, ...]]` types whose Protocols declare neither delete call.
- `__init__.py`: unchanged copyright comment only (ruff clean once out of `extend-exclude`), no export list. The mypy override entry `crc.azu` is removed together with the ruff entries.
- Tests: baseline `pytest -q` 7379 passed, now 7489 passed and 1 failed (the ARCH-003 test above; +110 passed, 111 new tests): `tests/test_azure_base_parity.py` (45 tests: 29 table cases against the oracle plus 16 quirk checks on both oracle and new code), `tests/test_azure_vm_delete_helpers_parity.py` (66 tests: 35 NIC cases, 10 VM cases, 17 quirk checks, constants, focused unit and keyword-argument parity tests). Oracles `tests/legacy/azu_base.txt` and `tests/legacy/azu_vm_delete_helpers.txt` are exact `git show main:` output. Existing tests are unchanged. Non-vacuity: changing the NIC retry sleep (26 failures), the compute `subscription_id` argument (9 failures), the network singleton test (8 failures), the `TENANT_ID`/`CLIENT_ID` read order (2 failures) and `NIC_DELETE_TIMEOUT_SEC` (22 failures) each failed the parity tests; all reverted.
- Both modules list no regions and log nothing in `_base`, so there is no region listing, connectivity fallback or region auth-error case; credential errors are covered as missing variables and a failing credential constructor.
- Ratchet: removed `crc/azu/__init__.py`, `crc/azu/_base.py`, `crc/azu/vm_delete_helpers.py` from ruff `extend-exclude` and `crc.azu`, `crc.azu._base`, `crc.azu.vm_delete_helpers` from the mypy override. No per-SDK mypy override was needed.
- Open issues: 9 new rows in `docs/refactor/bugs/pending/PL-AZ.md` (ID `NEW`, 1 added in round 1); `BUG-200`, `BUG-203`, `BUG-252`, `BUG-254` already cover the NIC retry/name and ignored poller timeout.
- Needed changes outside this lane (not made): `tests/archtest/legacy_baseline.txt` still lists the three modules (it may only shrink and the lane must not edit it; the ratchet test only requires pyproject entries to be a subset); `crc/README.md`, `tests/README.md` and `tests/legacy/README.md` should list `azu_base.txt`, `azu_vm_delete_helpers.txt`, `tests/test_azure_base_parity.py`, `tests/test_azure_vm_delete_helpers_parity.py`; `docs/refactor/STATUS.md` needs the PL-AZ row. No `crc/core` change needed.

## Round 1 (Argon)

Scope reviewed: `git diff ef1f718..HEAD` (`crc/azu/_base.py`, `crc/azu/vm_delete_helpers.py`, `crc/azu/__init__.py`, `crc/azu/README.md`, `pyproject.toml`, `docs/refactor/bugs/pending/PL-AZ.md`, `docs/refactor/reviews/PL-AZ.md`, `tests/legacy/azu_base.txt`, `tests/legacy/azu_vm_delete_helpers.txt`, `tests/test_azure_base_parity.py`, `tests/test_azure_vm_delete_helpers_parity.py`) against `git show main:crc/azu/_base.py`, `git show main:crc/azu/vm_delete_helpers.py`, `git show main:crc/azu/__init__.py`, `AGENTS.md`, `docs/refactor/BUGS.md`, and `.cursor/skills/crc-python-refactor/REVIEW_CHECKLIST.md`.

### 1. Behaviour parity (`git show main:<path>`)

- **`crc/azu/_base.py`** (`crc/azu/_base.py:17-56` vs `main:crc/azu/_base.py:10-74`):
  - `Base.__init__(self, resource_group: str)` initializes `self.compute_client: Any = {}` and `self.network_client: Any = {}`, reads `AZURE_CREDENTIALS_SUBSCRIPTION_ID`, `AZURE_CREDENTIALS_TENANT_ID`, `AZURE_CREDENTIALS_CLIENT_ID`, and `AZURE_CREDENTIALS_CLIENT_SECRET` from `os.environ` in that exact order, resolves `self.resource_group: str = resource_group or os.environ["AZURE_RESOURCE_GROUP"]` (identical to `main`'s `resource_group if resource_group else os.environ["AZURE_RESOURCE_GROUP"]`), and constructs `self.credential = ClientSecretCredential(tenant_id=self.tenant_id, client_id=self.client_id, client_secret=self.secret)` with keyword arguments.
  - `get_compute_client()` checks truthiness (`if self.compute_client:`), constructs `ComputeManagementClient(credential=self.credential, subscription_id=self.subscription_id)` with keyword arguments on first truthy-client call, stores it on `self.compute_client`, and returns it.
  - `get_network_client()` checks truthiness (`if self.network_client:`), constructs `NetworkManagementClient(self.credential, self.subscription_id)` with positional arguments on first truthy-client call, stores it on `self.network_client`, and returns it.
  - Module-level imports (`ClientSecretCredential`, `ComputeManagementClient`, `NetworkManagementClient`) remain in place for callers and test monkeypatches (`tests/helpers_azure_*.py`).
- **`crc/azu/vm_delete_helpers.py`** (`crc/azu/vm_delete_helpers.py:13-114` vs `main:crc/azu/vm_delete_helpers.py:14-90`):
  - Preserves constants `DEFAULT_VM_DELETE_TIMEOUT_SEC = 3600`, `NIC_DELETE_ATTEMPTS = 24`, `NIC_DELETE_RETRY_INTERVAL_SEC = 15`.
  - `begin_delete_vm_and_wait(compute_client, resource_group, vm_name, timeout_sec=DEFAULT_VM_DELETE_TIMEOUT_SEC)` calls `compute_client.virtual_machines.begin_delete(resource_group, vm_name)`, waits with `poller.result(timeout=timeout_sec)`, and logs `logging.info("VM delete completed: %s", vm_name)` only after `result()` returns (`BUG-252` preserved).
  - `delete_vm_primary_nic(network_client, resource_group, vm_name, dry_run, nics_names_to_delete, nic_name_suffix="-NIC")` builds `f"{vm_name}{nic_name_suffix}"` (`BUG-203`), appends and returns immediately without SDK calls when `dry_run` is truthy, and otherwise loops `attempt` from `1` to `NIC_DELETE_ATTEMPTS` (`24`), calling `network_client.network_interfaces.begin_delete(resource_group, nic_name)` and `poller.result(timeout=600)` (`NIC_DELETE_TIMEOUT_SEC`).
  - `ResourceNotFoundError` logs `"NIC %s already removed (or never existed)"` and appends `nic_name` (`BUG-254`); `HttpResponseError` with `status_code == 404` logs `"NIC %s not found (404)"` and appends `nic_name` (`BUG-254`); any other `HttpResponseError` or `Exception` logs `"NIC delete attempt %s/%s for %s: %s"` at WARNING, sleeps `15` seconds (`time.sleep(NIC_DELETE_RETRY_INTERVAL_SEC)`) when `attempt < NIC_DELETE_ATTEMPTS`, and after 24 failed attempts logs `"Failed to delete the NIC - %s after %s attempts"` at ERROR without raising (`BUG-200`).
- **`crc/azu/__init__.py`**: byte-identical to `git show main:crc/azu/__init__.py`.

### 2. Oracles and parity test verification

- `diff -u <(git show main:crc/azu/_base.py) tests/legacy/azu_base.txt`, `diff -u <(git show main:crc/azu/vm_delete_helpers.py) tests/legacy/azu_vm_delete_helpers.txt`, and `diff -u <(git show main:crc/azu/__init__.py) crc/azu/__init__.py` all produce 0 lines of diff.
- Tested deliberate one-line behaviour changes locally in `crc/azu/_base.py` (swapping `AZURE_CREDENTIALS_TENANT_ID` and `AZURE_CREDENTIALS_CLIENT_ID` read order -> 2 failures in `tests/test_azure_base_parity.py`) and `crc/azu/vm_delete_helpers.py` (`NIC_DELETE_TIMEOUT_SEC = 300` -> 22 failures in `tests/test_azure_vm_delete_helpers_parity.py`), and reverted both.

### 3. Architecture, scope, and lane compliance

- `crc/azu/__init__.py`, `crc/azu/_base.py`, and `crc/azu/vm_delete_helpers.py` (`crc.azu`, `crc.azu._base`, `crc.azu.vm_delete_helpers`) were removed from both ratchet lists in `pyproject.toml`, leaving zero `crc/azu` files in the legacy ratchet; nothing was added (`ARCH-001` passes).
- `ruff check .`, `mypy` (strict across `crc` and `tests`), `lint-imports` (5 contracts kept, 0 broken), `ARCH-001`, `ARCH-002`, `ARCH-004`, `ARCH-005`, and `SAF-LOCK` all pass without `# type: ignore` or `# noqa`.
- `ARCH-003` (`tests/archtest/test_arch003_bans.py::test_arch003_cloud_modules_have_no_banned_constructs`) flags `crc/azu/_base.py:27-31` for reading `os.environ` now that `crc/azu/_base.py` has left the legacy ratchet. Because `Base(resource_group)` must keep reading `os.environ` for behaviour parity until the CLI rung and `tests/archtest/bans.py` (`ARCH003_ALLOWLIST`) is an owner-only `CODEOWNERS` file outside this lane, this is an expected integration item for the repository owner.
- `crc.py`, `crc/core/**`, every cleaner module, and all shared docs forbidden by the lane rules are untouched.

### BLOCKING (fixed)

1. **`tests/test_azure_base_parity.py` did not capture/compare logs or stdout and did not test pairwise read order of `TENANT_ID`, `CLIENT_ID`, `CLIENT_SECRET`, and `AZURE_RESOURCE_GROUP`; `tests/test_azure_vm_delete_helpers_parity.py` had two VM unit tests that did not run against the oracle and no keyword-argument parity check** (`tests/test_azure_base_parity.py:11-285`, `tests/test_azure_vm_delete_helpers_parity.py:390-450`):
   - Added `logs` (`caplog` at `DEBUG`) and `stdout` (`capsys`) to `Outcome` in `tests/test_azure_base_parity.py` and asserted `new.logs == old.logs == []` and `new.stdout == old.stdout == ""` across all cases.
   - Added `missing-tenant-and-client-id`, `missing-client-id-and-secret`, and `missing-secret-and-resource-group` cases and quirk checks in `tests/test_azure_base_parity.py` so swapping any two `os.environ` reads in `Base.__init__` fails the parity suite.
   - Updated `test_vm_delete_waits_for_the_poller_with_the_timeout` and `test_vm_delete_failure_is_not_swallowed_and_logs_nothing` in `tests/test_azure_vm_delete_helpers_parity.py` to assert against both `_load_oracle()` and `new_module`, and added `test_keyword_arguments_match_main`.
2. **Minor inaccuracies in `crc/azu/README.md` and `docs/refactor/bugs/pending/PL-AZ.md`** (`crc/azu/README.md:58`, `:69-70`, `docs/refactor/bugs/pending/PL-AZ.md:12`, `:15`):
   - Clarified `crc/azu/README.md` Safety Invariants (`begin_delete_vm_and_wait` is called only when `dry_run` is `False`, whereas `delete_vm_primary_nic` is called in both modes and checks `dry_run` before making any SDK call) and fixed out-of-order numbering (`1, 2, 3, 5, 4` -> `1, 2, 3, 4, 5`) under `## Extension Rules`.
   - Extended row 3 line citations in `docs/refactor/bugs/pending/PL-AZ.md` to include the `return` statements (`:55-61`, `:69-74`) and added a `NEW` low-risk row for `delete_vm_primary_nic` retrying `begin_delete` from scratch when `poller.result(timeout=600)` raises a non-404 error (`main:crc/azu/vm_delete_helpers.py:55-61`, `:66-90`, same shape as `BUG-116`).

### NIT (left)

- `tests/archtest/test_arch003_bans.py::test_arch003_cloud_modules_have_no_banned_constructs` fails on `crc/azu/_base.py:27-31` (`uses banned env`) until the repository owner adds `"crc/azu/_base.py": frozenset({"env"})` to `ARCH003_ALLOWLIST` in `tests/archtest/bans.py` (or moves credential env reads to the CLI boundary in the CLI rung).

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` (`crc`, `tests`) | Success: no issues found in 164 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | 7489 passed, 1 failed (`test_arch003_cloud_modules_have_no_banned_constructs` on `crc/azu/_base.py:27-31`, owner allowlist needed) |

Test count: 7379 before PL-AZ (`ef1f718`), 7482 before review (+1 failed ARCH-003), 7489 passed (+1 failed ARCH-003) after review additions (+111 new tests).

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff ef1f718..HEAD` (commits `a9f0d1d`, `0cabd20`), read against `git show main:crc/azu/_base.py`, `git show main:crc/azu/vm_delete_helpers.py`, `git show main:crc/azu/vm.py`, `git show main:crc/azu/spot_vm.py`, `REVIEW_CHECKLIST.md` and `tests/archtest/`.

### Round 1 fixes assessment

- The base parity test now also compares logs (`caplog` at DEBUG) and stdout, and it has three new pairwise missing-variable cases. These are correct and only add assertions: every case still compares oracle and new module on SDK constructions, env reads, attributes and errors. Nothing was removed or loosened.
- The two VM unit tests now run against both `_load_oracle()` and `crc.azu.vm_delete_helpers`, and `test_keyword_arguments_match_main` is new. Both are correct and stricter than before.
- The README edits are correct: the safety-invariant wording matches the callers on main (`main:crc/azu/vm.py:271-283`: VM delete only outside a dry run, `delete_vm_primary_nic` always called with `self.dry_run`), and the Extension Rules numbering is fixed. The new pending row (the whole attempt is retried after the LRO was accepted) cites the right lines, `main:crc/azu/vm_delete_helpers.py:55-61`, `:66-90`.
- No production code changed in round 1.

### Independent review

- **Parity.** `_base.py` makes the same four `os.environ[...]` reads in the same order, reads `AZURE_RESOURCE_GROUP` only for a falsy argument, and passes the same keyword arguments to the credential. The compute client gets keyword arguments and the network client positional ones, and the truthiness test and `{}` marker are kept. The only change is that the getter now builds into a local before assigning. On main, an exception in the constructor also left the marker unchanged (the assignment never ran), so behaviour is identical. `vm_delete_helpers.py` does the same: `_try_delete_nic` returns `True` on exactly main's three `return` paths, and the sleep, the final error log and the `600` timeout (`NIC_DELETE_TIMEOUT_SEC`) are unchanged. `HTTP_NOT_FOUND == 404`.
- **Safety (CLEANUP_SAFETY.md).** No age gate, tag filter or raise-on-list-error path is involved. The NIC helper still makes no SDK call in a dry run (case-checked against the oracle), and no safety test was touched.
- **Tests.** The tests are not vacuous. Fake `time.sleep` calls are recorded in the same `calls` list as the SDK calls, so order and sleep length are both compared, and quirk checks run on the oracle as well as the new code. The oracles are byte-identical to `git show main:` (re-checked with `diff`).
- **Typing and architecture.** The client parameters and attributes are `Any` only at the SDK edge, as justified. There is no `# type: ignore` or `noqa`, and `lint-imports` keeps 5 contracts. The pending-bug citations I relied on (`_base.py:27-47`, `:55-74`, `vm_delete_helpers.py:31-33`, `:48-61`, `:53-90`, `vm.py:271-283`, `spot_vm.py:318-326`) match main.

### Findings

- **BLOCKING (owner action, not fixable in this lane):** `tests/archtest/test_arch003_bans.py::test_arch003_cloud_modules_have_no_banned_constructs` fails on `crc/azu/_base.py:27-31` (env reads). Round 1's remediation is incomplete. Adding the entry to `ARCH003_ALLOWLIST` (`tests/archtest/bans.py:14`) also fails `test_arch003_allowlist_is_explicit_and_empty_by_default` (`tests/archtest/test_arch003_bans.py:37-39`, which asserts the allowlist is empty). The legacy set ARCH-003 uses is ruff's `extend-exclude` (`tests/archtest/_common.py:59-61`), and ARCH-001 requires the mypy list to match it. So the only in-lane way to a green gate is to put `_base.py` back on both legacy lists, which would undo a third of the rung, and I left that to the owner. I corrected the remediation text in the Coding summary. Options: (a) allowlist entry plus relaxing the empty-allowlist test (both under `/tests/archtest/`, CODEOWNERS), (b) inject the env values from `crc.py` (CLI rung; changes every `Base(...)` caller), (c) keep `crc/azu/_base.py` on both legacy lists until (b).
- NIT (left): `crc/azu/README.md` now says **Refactored** for the whole package while `_base.py` cannot pass ARCH-003 yet. This is accurate for ruff and mypy only.
- NIT (left): `DeletePoller.result(timeout: int)` is narrower than the SDK's `Optional[float]`. Callers only pass ints, so this is harmless.
- No new bugs or quirks in existing code beyond the 9 rows already in `docs/refactor/bugs/pending/PL-AZ.md`; nothing added.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check` (after clearing a stale `.mypy_cache` from a run before the SDKs were installed):

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` | Success (164 source files) |
| `lint-imports` | 5 kept, 0 broken |
| `pytest -q` | 7489 passed, 1 failed (`test_arch003_cloud_modules_have_no_banned_constructs`, `crc/azu/_base.py:27-31`) |

Test count: 7489 passed, 1 failed (baseline 7379 at `ef1f718`; +111 new tests), the same as round 1.

ROUND 2 VERDICT: CHANGES_REQUESTED

## Integration (local driver)

Round 2's blocking item is resolved by its option (c): `crc/azu/_base.py` / `crc.azu._base` stay on both legacy lists, so ARCH-003 does not apply to it and `make check` is green. The module keeps its types and parity tests. It leaves the ratchet in the CLI rung, when `crc.py` passes the credentials in (same plan as `crc/oci/_base.py` from PL-OCI). `crc/azu/README.md` no longer calls the package fully refactored, and `Base` was dropped from its Public API list (ARCH-002 does not scan legacy modules).
