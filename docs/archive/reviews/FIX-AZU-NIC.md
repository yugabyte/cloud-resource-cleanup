# FIX-AZU-NIC review

## Coding summary

Rung FIX-AZU-NIC (wave FIX-1), branch `fix/azu-nic`. Status: complete; `make check` green.

- Bugs fixed: `BUG-111` and `BUG-122` (the NIC listing is now `network_interfaces.list(resource_group)` with
  `Base.resource_group`, so nothing outside the configured group is listed or deleted and a "deleted" NIC
  can no longer be a no-op 204 on a missing resource); `BUG-113` (`name_regex` / `exception_regex` entries are
  regexes matched with `re.search`; patterns are compiled by `policy.compile_patterns` before any SDK call and
  an invalid one raises `ValueError`; an empty-string `name_regex` entry is ignored); `BUG-114` (a NIC with
  `virtual_machine`, `private_endpoint` or `private_link_service` set is kept, skip reason `in_use`;
  the two new attributes are read with `getattr(..., None)`).
- Bugs left: none of the four. See "Owner decision" for two deliberate deviations inside `BUG-113`.
- Files changed: `crc/azu/nic.py`, `crc/azu/README.md` (NIC Safety Invariants), `tests/test_azure_nic_parity.py`,
  `tests/test_azure_nic.py`, `tests/test_azure_logger.py` (one expected skip slug), `tests/helpers_azure_nic.py`
  (fake gets `list(resource_group_name)`), new `tests/test_azure_nic_fix.py`. No hash-locked test touched.
- Tests: `pytest -q` 42444 passed at 182466d, 42459 passed now (+15). `make check` green.
- Parity tests: the oracle's `list_all()` is mapped to `list(<group>)` (`_normalised`) so every unmarked case still
  asserts equal calls, results, logs and errors. Cases that trigger `BUG-113` are in `DIVERGING`: each asserts
  that the oracle still shows the old behaviour and asserts the exact new outcome (adapter and registered
  `Cleaner` path). The nine buggy quirk checks moved from `QUIRKS` into `DIVERGING`.
- Owner actions: none.

## Owner decision

- `exception_regex` empty-string entry: the contract says an empty entry is ignored. For `exception_regex` that
  would delete NICs that main keeps (`[""]` excludes every NIC today), which breaks the direction rule. It
  therefore raises `ValueError` before any SDK call (fail closed). `name_regex` empty entries are ignored as
  specified (an all-empty `name_regex` logs "No Azure NICs to delete." and lists nothing).
- `exception_regex` also keeps a NIC whose name contains the entry as a literal substring (`re.search(p)` or
  `p in name`), so a regex-special entry that main matched literally (`^nic` against `x^nic-a`) still excludes it
  and the fix never deletes a NIC main kept. For `name_regex` only `re.search` applies.
- A bare string instead of a list (main iterated it character by character) and non-string entries raise
  `ValueError` (fail closed). The CLI always passes a JSON list, so production is unaffected.
- `NIC.virtual_machine` is still read directly (a NIC object without the attribute raises `AttributeError` as
  today instead of being treated as free); only `private_endpoint` and `private_link_service` default to `None`.
- The NIC listing is still requested twice (`BUG-117`, not in scope); only its call changed.

## Production command check

`--resource nic --name_regex '["yb-itest", "yb-itests", "ui-auto"]'`: the values have no metacharacters, so
`re.search` selects exactly the same NICs as the substring test (`test_production_values_select_the_same_nics_BUG_113`).
The only call difference is `list(resource_group)` instead of `list_all()`. The skip count in the run log for
attached NICs is now `reason=in_use` (was `reason=attached`), log text only.

## New bugs

- `crc/core/policy.py:207` `nic_name_included` (plain-substring helper) is no longer used by `crc/azu/nic.py`
  (`nic_name_excluded` still is, for the literal `exception_regex` fallback); it is only exercised by
  `tests/test_core_policy.py` (dead code, exists on main: no, it was added by the refactor). Not removed here
  because `crc/core/` is outside this rung.
- `docs/refactor/BUGS.md` `BUG-118` (non-string `exception_regex` entry raising `TypeError` only when evaluated)
  is now a `ValueError` before any SDK call as a side effect of `BUG-113`; the `None`-name `TypeError` is kept.

## Round 1 (Argon)

Scope reviewed: `git diff 182466d..HEAD` (`crc/azu/nic.py`, `crc/azu/README.md`,
`docs/refactor/reviews/FIX-AZU-NIC.md`, `tests/helpers_azure_nic.py`, `tests/test_azure_logger.py`,
`tests/test_azure_nic.py`, `tests/test_azure_nic_fix.py`, `tests/test_azure_nic_parity.py`).

### Checks

1. **Fixed bugs (`BUG-111`, `BUG-122`, `BUG-113`, `BUG-114`)**:
   - `BUG-111` & `BUG-122` (`crc/azu/nic.py:45,187-190`): `NicOperations.list(resource_group_name)` and `AzureNicCleaner.list_nics()` list only `self._resource_group` (`Base.resource_group`, falling back to `AZURE_RESOURCE_GROUP` when `resource_group` is falsy) instead of subscription-wide `list_all()`. Covered by `test_only_the_configured_group_is_listed_and_acted_on_BUG_111_BUG_122`, `test_group_comes_from_env_when_not_given_BUG_111`, and `test_list_is_scoped_to_the_configured_group_BUG_111` (all fail on `182466d`, pass now).
   - `BUG-113` (`crc/azu/nic.py:68-93,130-140,240-244`): `name_regex` and `exception_regex` entries are validated and compiled with `policy.compile_patterns` before any SDK call (`ValueError` on invalid regex, bare string, non-string entry, or empty-string `exception_regex` entry), empty-string `name_regex` entries are ignored (never matching all NICs), and matching uses `re.search` via `policy.name_regex_search_matches` / `policy.exception_regex_search_matches` (plus literal substring fallback via `policy.nic_name_excluded` for `exception_regex` so no NIC kept on `main` is deleted). Covered by `test_name_regex_is_a_regular_expression_BUG_113`, `test_exception_regex_is_a_regular_expression_BUG_113`, `test_invalid_patterns_raise_value_error_before_any_sdk_call_BUG_113`, `test_adapter_invalid_pattern_raises_before_any_sdk_call_BUG_113`, and `test_empty_string_entry_is_ignored_BUG_113`.
   - `BUG-114` (`crc/azu/nic.py:231-236`): `decide()` skips with reason `"in_use"` (`_logger.skip("in_use")`) when `nic.virtual_machine`, `getattr(nic, "private_endpoint", None)`, or `getattr(nic, "private_link_service", None)` is truthy. Covered by `test_nic_in_use_by_private_endpoint_or_link_service_is_kept_BUG_114`, `test_vm_attached_nic_is_in_use_and_missing_attributes_default_to_free_BUG_114`, `test_adapter_keeps_nic_in_use_BUG_114`, and parity case `in-use-private-endpoint-or-link-service`.
2. **Direction rule & parity (`tests/test_azure_nic_parity.py`, `tests/legacy/azu_nic.txt`)**:
   - Frozen oracle `tests/legacy/azu_nic.txt` is untouched (`git diff 182466d..HEAD -- tests/legacy/` is empty).
   - Every unmarked parity case asserts equality of `calls` (with `list_all` mapped to `list(group)` for `BUG-111`), `error`, `deleted`, `count`, `logs`, `count_logs`, and `stdout`.
   - Every diverging case in `DIVERGING` is tagged with `BUG-113` or `BUG-114`, asserts `old_shows(old)`, asserts the full new outcome (`calls`, `deleted`, `count`, `logs`, `stdout`, `error`), and asserts `(new.calls, new.error, new.deleted) != (old.calls, old.error, old.deleted)`.
3. **Production command**:
   - `python3 crc.py --cloud azure --resource_group "$RESOURCE_GROUP" --resource nic --name_regex '["yb-itest", "yb-itests", "ui-auto"]' --slack_channel yba-crc-cleanup-notifications` runs with identical CLI flags and selects the same NICs (`test_production_values_select_the_same_nics_BUG_113`), scoping `list()` to `$RESOURCE_GROUP`.
4. **Fail-closed before SDK mutation**:
   - `prepare()` runs at the start of `plan()`, `apply()`, and `NIC.delete()` before `self._client_factory()` is called, so invalid patterns raise `ValueError` with `azure.calls == []`.
5. **Protected files**:
   - No hash-locked safety test, `tests/archtest/safety_lock.json`, `docs/refactor/PLAN.md`, `docs/refactor/STATUS.md`, `docs/refactor/BUGS.md`, or `CLEANUP_SAFETY.md` is edited in `182466d..HEAD`.
6. **README (`ARCH-002`)**:
   - `crc/azu/README.md` `## Purpose`, `## Public API`, and `## Safety Invariants` match `crc/azu/nic.py` (`ARCH-002` passes).
7. **Owner decisions**:
   - The fail-closed handling of empty-string `exception_regex` entries, literal-substring fallback in `exception_regex`, bare-string / non-string `ValueError`, and direct `nic.virtual_machine` attribute read are documented under `## Owner decision` above.

### BLOCKING (fixed in Round 1)

1. **Out-of-scope formatting edits in `a9dfcf2`**:
   - Commit `a9dfcf2` swept in 25 unrelated formatting changes (`ARCHITECTURE.md`, `crc/aws/**`, `crc/cli/inputs.py`, `crc/core/**`, `crc/notify/**`, and 12 non-Azure test files). Restored all 25 out-of-scope files to `182466d` so `git diff 182466d..HEAD` touches only the 8 `FIX-AZU-NIC` files.
2. **Stale `NicOperations` and `nic.py` descriptions in `crc/azu/README.md` (`crc/azu/README.md:14,35`)**:
   - Updated `## Purpose` (`nic.py` bullet) from "not attached to a VM ... plain substring name filters" to "not in use ... regex name filters" and `## Public API` (`NicOperations` bullet) from `list_all and begin_delete` to `list and begin_delete`.
3. **Unnecessary `Any` in pattern types and `exception_regex` skipped when `name_regex` is empty (`crc/azu/nic.py:70,127-140,240-244`)**:
   - Narrowed `_compile` return type and `self._include` / `self._exclude` from `tuple[Any, ...]` to `tuple[str, ...]`, delegating matching in `decide()` to `policy.name_regex_search_matches`, `policy.exception_regex_search_matches`, and `policy.nic_name_excluded` so `Any` stays at SDK boundaries (`SKILL.md` 2.2, 3.1).
   - Compiled `self._include` and `self._exclude` in `prepare()` before checking `bool(self._include)` so an invalid `exception_regex` raises `ValueError` even when `name_regex` is empty (`test_invalid_patterns_raise_value_error_before_any_sdk_call_BUG_113`).
4. **Parity coverage for `BUG-114` and full outcome assertions in `DIVERGING` (`tests/test_azure_nic_parity.py`)**:
   - Added `in-use-private-endpoint-or-link-service` (`BUG-114`) to `CASES` and `DIVERGING`, strengthened `DIVERGING` predicates (`_deleted_outcome`, `_value_error`) to assert `calls`, `deleted`, `count`, `logs`, `stdout`, and `error`, and added `test_buggy_quirk_is_fixed` parametrized over `DIVERGING` (`BEHAVIOUR_FIX.md` section 3).

### NIT

None. No issues in do-not-edit files (`AGENTS.md`, `ARCHITECTURE.md`, `CLEANUP_SAFETY.md`, `docs/refactor/PLAN.md`, `.cursor/**`, `tests/archtest/**`).

### Gates & test count

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed! |
| `mypy` (`crc`, `tests`) | Success: no issues found in 229 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | 42474 passed in 146.62s |

Test count: 42444 passed at `182466d`, 42474 passed now (+30).

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff 182466d..HEAD` (same 8 files as Round 1; Round 1's restore of the 25 out-of-scope files holds).
Round 1 fixes checked: README `## Purpose` / `NicOperations` wording, `tuple[str, ...]` patterns, `exception_regex`
compiled even when `name_regex` is empty, `BUG-114` parity case and full-outcome `DIVERGING` predicates: all present
and correct.

### Checks

1. **Bugs fixed, test fails on `182466d`**: `tests/test_azure_nic_fix.py` run against `crc/azu/nic.py` from
   `182466d` (rest of HEAD unchanged): 22 failed, 3 passed. The 3 that pass are by design
   (`yb-itest` regex case, `test_production_values_select_the_same_nics_BUG_113`,
   `test_exception_literal_substring_still_excludes_BUG_113`: parity guards). `BUG-111` / `BUG-122`: both
   `list_nics()` calls are `network_interfaces.list(self._resource_group)`; the group is never derived from the
   listed resource. `BUG-113`: `re.search`, `ValueError` before any SDK call, empty `name_regex` entry dropped.
   `BUG-114`: `virtual_machine` / `private_endpoint` / `private_link_service`, skip reason `in_use`.
2. **Direction rule / parity**: `tests/legacy/` untouched. Unmarked cases assert full equality with only the
   `BUG-111` call rename (`_normalised`), which is justified because `BUG-111` changes the listing call for
   every input. Every `DIVERGING` entry is tagged `BUG-113` or `BUG-114` and asserts the exact new outcome. Nothing
   main kept is deleted for `exception_regex` (regex OR literal substring; `[""]` fails closed) or for in-use NICs.
   One input deletes more than main: a `name_regex` entry with regex metacharacters (`nic-.*` now selects
   `nic-1`; parity case `filter-regex-syntax-is-literal`). That is the owner-approved `BUG-113` decision
   ("entries are regular expressions matched with re.search") and cannot be done any other way; noted under
   Owner decision below so the owner sees it. Production values have no metacharacters.
3. **Production command**: `--name_regex '["yb-itest", "yb-itests", "ui-auto"]'` with `--exception_regex` unset
   (`crc/cli/args.py:111`, default `None`, so `_compile` gives `()`): same flags, same NIC selection; the
   listing is now scoped to `$RESOURCE_GROUP` as the notes require. Run-log skip slug `attached` becomes `in_use`.
4. **Fail closed**: `prepare()` runs first in `plan()`, `apply()` and `NIC.delete()`; `NIC._cleaner()` builds no
   client (`get_network_client` is passed as a factory), so `ValueError` comes before any SDK call
   (`azure.calls == []` asserted).
5. **Protected files**: no diff in `PLAN.md`, `STATUS.md`, `BUGS.md`, `CLEANUP_SAFETY.md`, `tests/archtest/**`,
   `tests/legacy/**`, the five hash-locked tests, `AGENTS.md`, `ARCHITECTURE.md`, `.cursor/**`.
6. **README (`ARCH-002`)**: Safety Invariants are correct. Fixed in Round 2: the `AzureNicCleaner` Public API bullet
   lacked the new public `prepare()`.
7. **Owner decisions**: documented. Addition: `name_regex` regex semantics can select more NICs than main for
   entries containing regex metacharacters (see check 2); owner-approved in the task notes, no production impact.

### Fixed in Round 2

- `crc/azu/README.md:37`: `AzureNicCleaner` bullet now lists `prepare` (compiles the patterns, `ValueError` before
  any SDK call).
- `crc/azu/nic.py` `NIC` class docstring: "cleans all unattached NICs" became "cleans all NICs of the resource
  group that are not in use".
- This file, "New bugs": `nic_name_excluded` is still used (literal `exception_regex` fallback); only
  `nic_name_included` is dead.

### BLOCKING

None.

### NIT

- `tests/test_azure_nic_parity.py` `test_matches_main` / `test_cleaner_protocol_matches_main`: the
  `(new.calls, new.error, new.deleted) != (old...)` check is always true because of the `BUG-111` call rename.
  It is harmless because every `new_shows` predicate asserts the exact outcome. Left as is.
- No issues in do-not-edit files.

### Gates & test count

`pip install -r requirements.txt -r requirements-dev.txt` then `make check` (HEAD `9c3a2d0`): ruff all checks
passed; mypy no issues in 229 source files; lint-imports 5 kept, 0 broken; pytest 42474 passed. After the Round 2
doc-only edits: `ruff check .` clean, `tests/archtest` + Azure NIC/logger tests 304 passed.

Test count: 42474 passed.

ROUND 2 VERDICT: APPROVE
