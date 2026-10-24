# FIX-AWS-EIP review (wave FIX-1)

## Coding summary

- Bug fixed: `BUG-011`. `ElasticIpCleaner._should_skip` (`crc/aws/elastic_ips.py`) no longer raises
  `TypeError("'bool' object is not iterable")` when `notags` is set. It now uses the same
  semantics as AWS VMs (`crc/aws/vm/cleaner.py`): exception tags (any key, tag by tag, first
  hit returns) still skip first; otherwise all tags are collected and `policy.notags_all`
  decides, so an address is kept (skipped) when every notags key matches (an empty value list
  means "key present"). An address with an empty tag list is not skipped by notags. A tag without
  `Key` or `Value` raises `KeyError` while `notags` is set (fail closed, before any release,
  as for VMs). With `notags` empty or `None` nothing changes: same calls, order, logs, errors.
- Production: neither Jenkins command passes `notags`, so their selection is unchanged.
- Bugs left: none in scope. `BUG-053` (age) is not in this rung.
- Files changed: `crc/aws/elastic_ips.py`, `crc/aws/README.md` (Elastic IP safety note),
  `tests/test_aws_ip_parity.py` (4 cases marked `diverges="BUG-011"`, new-outcome table,
  `_should_skip_instance` divergence table; every other case still equals the oracle),
  `tests/test_aws_elastic_ips.py` (the `TypeError` test replaced by a fixed-behaviour test),
  new `tests/test_aws_elastic_ips_fix.py` (9 tests), this file. Oracle `tests/legacy/aws_elastic_ips.txt`
  and hash-locked tests untouched.
- Direction rule: the fix turns a run that failed on every tagged, unfiltered-out address into
  one that skips addresses matching notags and releases the others. This follows the owner's task
  text ("keeps addresses as the other AWS cleaners do"). Addresses matching notags are kept; the
  run no longer aborts, so addresses that are not kept by notags and match the filter are now released
  where `main` stopped with an error (only reachable with `notags`, which production never passes).
- Tests: `pytest -q` 42444 collected at 182466d, 42454 passed after (+10). `make check` green.
- Owner actions: none (no locked test or `CLEANUP_SAFETY.md` change needed).

## Owner decision

None.

## Owner action

None.

## New bugs

- `docs/refactor/FILTER_VARIANTS.md` (line 33) and the `crc/core/policy.py` docstring of
  `aws_eip_should_skip` still say notags never skips / raises for Elastic IPs. Both are outside this
  rung's files; the driver or a docs rung should update them. Exists on main: the docs yes (stale
  after this fix only).

## Round 1 (Argon)

### Findings

1. **(1) Bug fix (`BUG-011`)**: `ElasticIpCleaner._should_skip` (`crc/aws/elastic_ips.py:193-204`) now collects `all_tags[tag["Key"]] = tag["Value"]` when `self._notags` is truthy (after checking `self._exception_tags` tag by tag as before) and evaluates `policy.notags_all(all_tags, self._notags or {})`, matching `crc/aws/vm/cleaner.py` semantics. Verified with unit tests in `tests/test_aws_elastic_ips_fix.py` and `tests/test_aws_ip_parity.py` that fail on `182466d` and pass now.
2. **(2) Direction rule & parity tests**: When `notags` is empty or `None`, `if self._notags:` does not execute, so every SDK call, call order, selection, log line, stdout and exception matches the frozen oracle `tests/legacy/aws_elastic_ips.txt` (untouched). In `tests/test_aws_ip_parity.py`, tightened `DIVERGED` from boolean predicates to exact `Outcome` instances (asserting `calls`, `deleted`, `count`, `logs`, `stdout`, and `error`) for all 4 `diverges="BUG-011"` cases across both `test_matches_main` and `test_cleaner_protocol_matches_main`.
3. **(3) Production Jenkins commands**: Neither production command passes `--notags`; added `test_production_jenkins_filter_tags_selection_unchanged` in `tests/test_aws_elastic_ips_fix.py` pinning both Jenkins `filter_tags` payloads.
4. **(4) Fail-closed paths**: Malformed tag dicts missing `"Key"` or `"Value"` when `notags` is set raise `KeyError` during `plan_region` before any `release_address` call.
5. **(5) Protected files**: `tests/archtest/safety_lock.json`, the 5 locked safety test files, `docs/refactor/PLAN.md`, `docs/refactor/STATUS.md`, `docs/refactor/BUGS.md`, and `CLEANUP_SAFETY.md` are untouched.
6. **(6) Package README**: `crc/aws/README.md` Safety Invariants updated for `BUG-011` (`ARCH-002` passes).
7. **(7) Unfixed bugs**: None in scope (`BUG-053` is explicitly out of scope for this rung).

### Gates & Test Count

- `ruff check .`: clean
- `mypy`: clean (`Success: no issues found in 229 source files`)
- `lint-imports`: `8 contracts kept, 0 broken`
- `pytest -q`: `42456 passed` (42444 on `182466d`, +12 net)

VERDICT: APPROVE


## Round 2 (Opus)

Scope checked: `git diff 182466d..HEAD` (2 commits: the fix and the Round 1 review).

### Round 1 check

- Round 1's change (exact `Outcome` per diverging case instead of boolean predicates in
  `tests/test_aws_ip_parity.py`) is correct and stronger than before. Its findings hold, with one
  correction: `lint-imports` reports `Contracts: 5 kept, 0 broken`, not 8. The test count
  (42456) matches.

### Findings

1. **(1) BUG-011 fixed.** `ElasticIpCleaner._should_skip` (`crc/aws/elastic_ips.py:193-204`)
   checks exception tags one tag at a time as before, collects `Key -> Value` while `notags` is
   set, and returns `policy.notags_all(...)`. This is the same as
   `crc/aws/vm/cleaner.py:278-294`. The legacy adapter `ElasticIPs` goes through the cleaner, so
   `crc.py` gets the fix too. I copied the three changed or new test files onto a 182466d worktree:
   28 tests fail there (9 in `test_aws_elastic_ips_fix.py`, 4+4+4 diverging parity cases, 6
   `_should_skip_instance` cells, 1 in `test_aws_elastic_ips.py`). All of them pass on HEAD.
2. **(2) Direction rule and parity.** With `notags` empty or `None`, the new branch never runs and
   `notags_all(..., {})` is `False`, the same as the old `return False`. Every case without notags
   still asserts equality with the frozen oracle, and `tests/legacy/aws_elastic_ips.txt` is
   untouched. Exactly 4 cases are marked `diverges="BUG-011"`. Each one asserts that the oracle
   still raises the `TypeError` and gives the exact new `Outcome`. The `_BUG_011_SKIP` table
   covers only the cells where the oracle raises `TypeError`, and every other cell is compared for
   equality. Nothing is loosened globally.
   - **Direction-rule note (owner confirmation, not blocking).** With `notags` set, `main` raised
     the `TypeError` in `plan` for the first tagged address that no exception tag skipped. It
     released nothing except addresses with `Tags: []` in earlier regions. After the fix,
     addresses that do not match notags (and do match the filter) are released. One example is
     the case `notags-raises-in-first-region-skips-later-regions`: `main` released 0 addresses
     there, and the fix releases 2. Read on its own, the wave contract line "never release more
     than today" forbids that. The task notes for this rung, however, set exactly this target
     ("keeps addresses as the other AWS cleaners do, same semantics as crc/aws/vm.py"). Any fix
     that lets a failing run finish has to release something the failing run did not, and no
     production command passes notags. So I accept it as the owner-specified intent. The only
     fix that meets the contract word for word would be to fail closed with a `ValueError` when
     `notags` is set for `--resource ip`. If the owner prefers that, it is a follow-up and needs
     a change to the task notes.
3. **(3) Production.** Neither Jenkins command passes `--notags`, so `_should_skip` returns
   `False` before the changed code, as before. Their selection is unchanged.
   `test_production_jenkins_filter_tags_selection_unchanged` pins both `filter_tags` payloads.
4. **(4) Fail closed.** A tag without `Key` or `Value` raises `KeyError` inside `plan`, before any
   `release_address` call (`test_notags_tag_without_value_fails_closed`). On `main` the same
   inputs also raised (`TypeError`, or `KeyError` for a missing `Key`), so nothing that ran before
   stops running. NIT: the error is a `KeyError` and not a clear `ValueError`. That matches the
   VM cleaner and the module's existing exception-tags path, so I leave it.
5. **(5) Protected files.** No change to `tests/archtest/**`, `safety_lock.json`, the 5 locked
   tests, `tests/legacy/**`, `PLAN.md`, `STATUS.md`, `BUGS.md`, `CLEANUP_SAFETY.md`, `AGENTS.md`,
   `ARCHITECTURE.md` or `.cursor/**`.
6. **(6) README / ARCH-002.** The Elastic IP Safety Invariants line in `crc/aws/README.md` is
   updated. No public name or signature changed, so Public API needs no edit. ARCH-002 passes.
7. **(7) Unfixed bugs.** None in scope. BUG-053 is correctly left out.

### NITs (not fixed: they change log text or lines that parallel FIX branches also edit)

- `crc/aws/elastic_ips.py:139-141`: an address kept by notags is logged as
  `skip("exception_tag")` with the reason `"matches exception_tags"`. The VM cleaner uses
  `"exception tag or notags"`. Proposed (in a later rung, because it changes the skip reason text):
  `reason="matches exception_tags or notags"`.
- `crc/aws/README.md:72`: the "Known bugs and quirks" list still names `BUG-011`. Proposed: the
  driver drops `BUG-011` from that list at integration, since several FIX branches edit this line.
- The stale `crc/core/policy.py:168-174` docstring ("notags never skips an Elastic IP") and
  `docs/refactor/FILTER_VARIANTS.md:33` are already listed under "## New bugs" by the coder.

### Gates

- `pip install -r requirements.txt -r requirements-dev.txt`: ok.
- `make check`: `ruff` All checks passed; `mypy` Success, no issues in 229 source files;
  `lint-imports` 5 kept, 0 broken; `pytest -q` **42456 passed** (42444 on 182466d, +12).

ROUND 2 VERDICT: APPROVE
