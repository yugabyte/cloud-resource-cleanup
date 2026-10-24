# FIX-AZU-IP review

## Coding summary

Rung FIX-AZU-IP (wave FIX-1), branch `fix/azu-ip`. Status: complete.

- Bugs fixed: `BUG-074`, `BUG-013`, `BUG-077`, `BUG-076`.
  - BUG-074: `AzureIpCleaner.list_ips()` calls `public_ip_addresses.list(resource_group)` with
    `Base.resource_group` instead of the subscription-wide `list_all()`. Nothing outside the
    configured group is listed or acted on; the group is not derived from the listed resource.
    An empty resource group raises `ValueError` before any SDK call (fail closed).
  - BUG-013 / BUG-077: an untagged IP (`tags is None`) is an IP with no tags in the exception and
    notags checks, so it never raises. Earlier deletes are no longer followed by a `TypeError`.
    The filter check is unchanged (an untagged IP never matches a non-empty `filter_tags`).
  - BUG-076: an IP is kept (skip reason `in_use`) when `ip_configuration`, `nat_gateway`,
    `linked_public_ip_address` or `service_public_ip_address` is set; the four fields are read
    with `getattr(ip, field, None)`, so SDK models without a field still work.
- Bugs left: none.
- Files changed: `crc/azu/ip.py`, `crc/azu/README.md` (Safety Invariants / quirk notes),
  `tests/helpers_azure_ip.py` (fake gets `list(resource_group_name)`),
  `tests/test_azure_ip.py`, `tests/test_azure_ip_parity.py` (diverging cases marked with their
  bug ID in `DIVERGING`, oracle call `list_all` mapped to `list(group)`, all other cases still
  equal to the oracle), `tests/test_azure_logger.py` (the ip skip line is now
  `reason=in_use` instead of `reason=attached`), new `tests/test_azure_ip_fix.py`.
- Test count (`pytest --collect-only`): 42444 before (182466d), 42455 after (+11).
- Owner actions: none (no locked test, no `CLEANUP_SAFETY.md` change needed).

Notes for reviewers:

- Parity: every case lists `("list", group)` instead of `("list_all",)`, mapped in `_scoped`.
  Seven cases diverge on purpose and assert the new outcome plus that it differs from the
  oracle: `untagged-with-exception-raises`, `untagged-with-notags-raises`,
  `untagged-raises-in-dry-run` (BUG-013), `untagged-raises-even-if-filter-excludes`,
  `untagged-raises-after-earlier-deletes` (BUG-077),
  `untagged-with-exception-attached-ip-still-raises` (BUG-013, still kept as `in_use`) and
  `missing-ip-configuration-attribute` (BUG-076, `getattr` default).
- The `ip_configuration` skip reason changed from "attached to an IP configuration" (slug
  `attached`) to `in_use`, as the task asks. Only the skip count line and `Skipped.reason` change.
- Direction rule: the selections that change are inputs that used to abort the run
  (untagged IPs with exception/notags, a model without `ip_configuration`) and the listing
  scope (fewer resources are reachable). Production commands (`--cloud azure --resource_group
  "$RESOURCE_GROUP" --resource ip --filter_tags ...`) keep the same flags. With `--filter_tags`
  set, untagged IPs are still never selected.

## Owner decision

None.

## New bugs

- `crc/azu/ip.py` `decide()`: `filter_tags` with a string value (not a list) matches by
  substring (`tag in "testing"`), and a tag value of `None` with a string spec raises
  `TypeError` (covered by parity quirks `string-spec-value-matches-substring` and
  `tag-value-none-with-string-spec-raises`; exists on main: yes). Not fixed.
- `crc/azu/ip.py` `decide()`: no age gate; an empty `filter_tags` selects every unused IP of the
  group, including untagged ones (SAF-011; exists on main: yes). Already recorded in the README.

## Round 1 (Argon)

Scope reviewed: `git diff 182466d..HEAD` (`crc/azu/ip.py`, `crc/azu/README.md`,
`docs/refactor/reviews/FIX-AZU-IP.md`, `tests/helpers_azure_ip.py`, `tests/test_azure_ip.py`,
`tests/test_azure_ip_fix.py`, `tests/test_azure_ip_parity.py`, `tests/test_azure_logger.py`)
against `docs/refactor/BUGS.md` (`BUG-074`, `BUG-013`, `BUG-077`, `BUG-076`),
`.cursor/skills/crc-python-refactor/BEHAVIOUR_FIX.md`, and `CLEANUP_SAFETY.md`.

### Verification

1. **Fixed bugs (`BUG-074`, `BUG-013`, `BUG-077`, `BUG-076`):**
   - `BUG-074` (`crc/azu/ip.py:28-30`, `:125-133`): `PublicIpOperations` narrows to `list(resource_group_name)` and `AzureIpCleaner.list_ips()` calls `self._client_factory().public_ip_addresses.list(self._resource_group)` using `Base.resource_group` (never subscription-wide `list_all()`, never derived from `ip.id`). Verified against `182466d:crc/azu/ip.py` that all 16 bug-fix test cases in `tests/test_azure_ip_fix.py` fail on `182466d` and pass now.
   - `BUG-013` / `BUG-077` (`crc/azu/ip.py:156-159`): `exception_match` and `notags_match` pass `ip.tags or {}` to `policy.should_skip_by_tags`, so `ip.tags is None` is treated as having no tags and never raises `TypeError` (including when `filter_tags` already excludes the IP or after earlier deletes).
   - `BUG-076` (`crc/azu/ip.py:49-59`, `:164-165`): `_in_use(ip)` checks `ip_configuration`, `nat_gateway`, `linked_public_ip_address`, and `service_public_ip_address` via `getattr(ip, field, None) is not None` and skips with reason `"in_use"`.
2. **Direction rule and parity (`tests/test_azure_ip_parity.py`):**
   - `tests/legacy/azu_ip.txt` is untouched.
   - Scoping to `list(resource_group)` and checking the three additional in-use fields only keeps more / lists fewer resources; fixing the `TypeError` on `ip.tags is None` and `getattr` default on `_IN_USE_FIELDS` only affects inputs that previously crashed the run.
   - All non-diverging cases assert exact equality on `_scoped(old.calls, case)`, `error`, `deleted`, `count`, `logs`, and `stdout`.
3. **Production Jenkins commands:**
   - Both production `--filter_tags` invocations (`yba-crc-cleanup-notifications` and `db-stress-tests-cleanup-notifications`) run with unchanged CLI flags and select only matching unused IPs in `$RESOURCE_GROUP` while skipping untagged and in-use IPs.
4. **Fail-closed paths:**
   - `list_ips()` raises `ValueError("Azure IP cleanup needs a non-empty resource group")` when `self._resource_group` is empty or whitespace-only before calling `self._client_factory()`.
5. **Protected and forbidden files:**
   - `tests/archtest/safety_lock.json`, all five hash-locked safety test files, `docs/refactor/PLAN.md`, `docs/refactor/STATUS.md`, `docs/refactor/BUGS.md`, `CLEANUP_SAFETY.md`, `AGENTS.md`, `ARCHITECTURE.md`, `.cursor/**`, and `tests/archtest/**` are untouched.
6. **README and `ARCH-002`:**
   - `crc/azu/README.md` updates `PublicIpOperations` in `## Public API` and documents the `FIX-AZU-IP` invariants (`BUG-074`, `BUG-013`, `BUG-077`, `BUG-076`) in `## Safety Invariants`.
7. **Unfixed bugs:**
   - None in scope left unfixed; `## Owner decision` records `None.`

### Fixed in this round

- `crc/azu/ip.py:130`: tightened `if not self._resource_group or not self._resource_group.strip():` in `list_ips()` so whitespace-only resource group strings also fail closed with `ValueError` before any SDK call.
- `tests/test_azure_ip_parity.py:71-95`, `:248-308`, `:373-387`, `:450-462`, `:506-596`:
  - Added three parity cases (`in-use-nat-gateway-skipped`, `in-use-linked-ip-skipped`, `in-use-service-ip-skipped`) marked with `BUG-076` in `DIVERGING`, proving the oracle deletes IPs attached via `nat_gateway`, `linked_public_ip_address`, or `service_public_ip_address` while the fix keeps them.
  - Tightened `test_matches_main` and `test_cleaner_protocol_matches_main` on `DIVERGING` cases to assert exact `old.deleted`, `new.calls` (`_expected_calls`), `new.logs` (`_expected_logs`), and `new.stdout == ""`.
  - Added `FIXED_QUIRKS` and `test_fixed_quirk` so the six quirk checks from `AZ-1` that were removed from `QUIRKS` are replaced by checks naming `BUG-013`, `BUG-077`, and `BUG-076` and asserting both the oracle's buggy outcome and the fixed outcome (`BEHAVIOUR_FIX.md` section 3).
- `tests/test_azure_ip_fix.py:105-110`, `:182-251`: added whitespace-only resource group fail-closed coverage and `test_production_jenkins_filter_tags_selection` covering both production Jenkins `--filter_tags` payloads.

### Findings

- **BLOCKING:** none.
- **NIT:** none.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed! |
| `mypy` (`crc`, `tests`) | Success: no issues found in 229 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | 42470 passed in 139.61s |

Test count: 42444 on base (`182466d`), 42455 before review, 42470 after review additions (+26 total, +15 in round 1).

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff 182466d..HEAD`, round 1 fixes and findings re-checked first, then the eight
checks done independently.

### Round 1 fixes re-checked

- Whitespace-only resource group fail-closed (`crc/azu/ip.py:134-135`): correct, raises
  `ValueError` before `self._client_factory()` is called (`azure.calls == []`).
- Added parity cases `in-use-nat-gateway-skipped`, `in-use-linked-ip-skipped`,
  `in-use-service-ip-skipped` (BUG-076), the tightened `DIVERGING` assertions and
  `FIXED_QUIRKS`: correct; every diverging case asserts the oracle still shows the bug and the
  exact new calls/logs.
- Production `--filter_tags` coverage in `tests/test_azure_ip_fix.py`: correct.

### Findings

- **BLOCKING (fixed in this round), direction rule:** round 1 extended the `getattr(..., None)`
  default to `ip_configuration` too. For a model without `ip_configuration` (parity case
  `missing-ip-configuration-attribute`), main raised `AttributeError` and deleted nothing, while
  the branch deleted the IP: it deleted more than today. That broke the direction rule, and
  BUG-076 is a "deletes what it should keep" row, so its fix may only keep more. The owner note
  ("so SDK models without the field still work") only needs the default for the three newer
  fields. Fixed in `crc/azu/ip.py:49-62`: `_in_use()` reads `ip.ip_configuration` directly (as
  main does) and uses `getattr` default `None` only for `nat_gateway`,
  `linked_public_ip_address` and `service_public_ip_address`. The parity case is back in
  `QUIRKS` (equal to the oracle: `AttributeError`, nothing deleted) and has been removed from
  `DIVERGING` and `FIXED_QUIRKS`. In `tests/test_azure_ip_fix.py`, the bare-model test now has
  `ip_configuration=None` and is renamed `test_sdk_model_without_the_newer_in_use_fields_still_works`,
  and the new test `test_model_without_ip_configuration_still_raises_before_any_delete` checks
  the preserved behaviour. The `crc/azu/README.md` Safety Invariants line is updated to match.
  The coding summary's mention of this case as diverging (above) is superseded by this round.
- **Note (no change), BUG-013/BUG-077 direction:** where main raised `TypeError`, an untagged
  IP is now treated as having no tags. With `exception_tags`/`notags` set and an empty
  `filter_tags`, the IP becomes a candidate, and IPs listed after it are no longer cut off by
  the crash. That is the owner-approved outcome in the task notes ("treated as having no tags,
  never raises"), and it equals main's outcome for an IP with `tags == {}` (parity quirks
  `exception-empty-tags-dict`, `notags-empty-tags-dict`). Production is not affected:
  both Jenkins commands set `--filter_tags` and pass neither `--exception_tags` nor
  `--notags` (argparse default `None`), so main never raised there, and untagged IPs still never
  match a non-empty `filter_tags`.
- **NIT:** none.

### Checks

1. Each fixed bug is covered by tests that fail on `182466d` and pass now: with
   `182466d:crc/azu/ip.py` in place, `tests/test_azure_ip_fix.py` gave 19 failed, 1 passed (the
   untagged-with-no-specs control case, which is not bug-specific). BUG-074 lists only
   `list(Base.resource_group)` and never derives the group from the IP id.
2. Direction rule: holds after the fix above. The oracle `tests/legacy/azu_ip.txt` is unchanged.
   Diverging parity cases are marked BUG-013, BUG-077 or BUG-076 and assert the new outcome. The
   only global mapping is `("list_all",)` to `("list", group)` (BUG-074); every other case
   asserts exact equality.
3. Production commands: same flags. Selection is now the unused, filter-matching IPs of
   `$RESOURCE_GROUP` only (BUG-074, intended), minus IPs held by a NAT gateway or linked/service
   IP (BUG-076, intended).
4. Fail closed: an empty or whitespace resource group raises `ValueError` before any SDK call;
   a missing `ip_configuration` raises `AttributeError` in `plan()` before any `begin_delete`.
5. Protected files untouched: `tests/archtest/**`, `safety_lock.json`, the five locked tests,
   `tests/legacy/**`, PLAN.md, STATUS.md, BUGS.md, CLEANUP_SAFETY.md, AGENTS.md,
   ARCHITECTURE.md, `.cursor/**`.
6. README: Public API (`PublicIpOperations.list`) and Safety Invariants (AZ-1 line) are updated.
7. Unfixed bugs: none in scope. `## Owner decision` says "None.", which is correct.
8. Gates: see below.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff` | All checks passed |
| `mypy` | Success: no issues found |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | 42471 passed |

Test count: 42444 on base (`182466d`), 42470 after round 1, 42471 after round 2 (one new
fix test; the moved parity case keeps the count).

ROUND 2 VERDICT: APPROVE
