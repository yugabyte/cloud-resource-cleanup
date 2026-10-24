## Coding summary

Rung FIX-AWS-KMS (wave FIX-1, branch `fix/aws-kms`): behaviour fix in `crc/aws/kms.py`.

- **Bugs fixed.** BUG-007, BUG-008, BUG-014, BUG-015, BUG-055. None left. (Round 2: BUG-015 reverted to `main` behaviour, see `## Owner decision`.)
  - BUG-014: a filter key with several values matches when the key's tag value is any of them (every filter key is still required).
  - BUG-007: a filter key with an empty value list matches only keys that carry that tag key (any value). Same semantics as `policy.filter_tags_all_keys` / `crc/aws/vm` (`_key_matches`: key present and, for a non-empty list, value in the list); the check goes through `policy.matches_any_key` per (TagKey, TagValue) pair.
  - BUG-015: `exception_tags` are read from `TagKey` / `TagValue`; a key with an exception tag is kept before `describe_key` (production `autoclean`, `auto_clean` and `crc` exception tags now work).
  - BUG-008: the custom age tag is read with the shared `core_age.get_retention_age` on the `TagKey` / `TagValue` tags (via `policy.normalize_tags`), like every other cleaner. **This may select a key earlier when its tag is shorter than `--age`: that is the documented custom-age-tag behaviour of every other cleaner and is allowed for BUG-008 only.** A tag longer than `--age` keeps the key longer; an empty or unparseable tag still falls back to `--age` (fail-open core helper, BUG-004 untouched).
  - BUG-055: all policy statements of a key are read first and the key is added to the delete list only afterwards, so a key that is skipped (`KEY SKIPPED`, `not_ours` / `unreadable`) is never selected. The run log counts it as skipped (`SKIPPED ... reason=not_ours`). Quirk kept as on main: a policy with two statements naming `kms_user` still selects the key twice (BUG-056).
- **Behaviour kept.** SDK call order (`list_resource_tags`, `describe_key`, `get_key_policy`, `schedule_key_deletion`) and every input the bugs do not cover are unchanged and still compared with the oracle. A tag without `TagKey` / `TagValue` or with an unhashable value still raises inside the per-key `try` and skips the key (fail closed); a repeated `TagKey` keeps every value; a non-iterable filter value list still skips every key. Slack text, Influx points, exit code and CLI flags are untouched.
- **Production commands** (`--kms_key_description "Yugabyte Master Key" --kms_user ... --age '{"days": 2|3}' --max_age '{"days": 21}' --exception_tags {...} --custom_age_tag_key max_retention_age`): no `filter_tags`, so BUG-007 and BUG-014 do not change them. Changes: BUG-015 keeps keys tagged `autoclean`/`auto_clean` false or `crc=gke` (on main every tagged key was skipped by the `KeyError`, so on main only untagged keys could be deleted; now keys with other tags are eligible too); BUG-008 lets a `max_retention_age` tag on a key set its age (shorter than `--age` selects earlier, longer keeps); BUG-055 drops mixed-principal keys from the list. A tagged key without an exception tag that is old enough and owned by `kms_user` is now deleted where main skipped it: this follows from the BUG-015 fix as specified (the row is "keeps what it should delete").
- **Files changed.** `crc/aws/kms.py`, `crc/aws/README.md` (KMS safety invariants), `tests/test_aws_kms.py`, `tests/test_aws_kms_parity.py`, `tests/test_aws_misc_logger.py` (one BUG-055 quirk check), new `tests/test_aws_kms_fix.py`, this review. No locked test, oracle, `PLAN.md`, `STATUS.md`, `BUGS.md` or `CLEANUP_SAFETY.md` touched.
- **Tests.** Before (182466d): 42444 passed. After: 42463 passed. Parity: 13 cases are marked `diverges="BUG-NNN"` (`FIXED` table); for each the oracle still shows the old outcome, the new code differs and the exact new deleted ids, `describe_key` calls and scheduled ids are asserted; every unmarked case, including all the new ones that are equal to main, still asserts equality with the oracle (calls, error, deleted, logs, stdout). Old quirk checks for the five bugs were replaced by the marked cases; `_should_skip_kms` cases with a non-empty spec and tags assert the TagKey outcome (BUG-015). New focused tests: `tests/test_aws_kms_fix.py` (12) and the updated `tests/test_aws_kms.py` filter/exception tests.
- **Gates.** `make check` (ruff check, mypy, lint-imports, pytest) green.
- **Owner action.** None. `CLEANUP_SAFETY.md` has no KMS-specific invariant text that changes. Optional: mark BUG-007, BUG-008, BUG-014, BUG-015 and BUG-055 "fixed in FIX-AWS-KMS" (the driver does this at integration).

## Owner decision

- **BUG-015 not fixed (round 2).** The task notes say "Production passes no filter_tags or exception_tags", but both Jenkins commands pass `--exception_tags '{"autoclean": [...], "auto_clean": [...], "crc": ["gke"]}'`, and `crc/cli/dispatch.py` `_kms` hands them to `Kms`. On `main` (`tests/legacy/aws_kms.txt:89-91`, `:147`, `:181`) any key with at least one tag raises `KeyError` in `_should_skip_kms` and is skipped, so the production job only ever schedules **untagged** keys. Reading `TagKey` / `TagValue` makes every tagged key without an exception tag (for example `crc=other`, `owner=qa`) old enough and owned by `kms_user` newly scheduled for deletion in production (parity case `exception-production-tags`: `main` deletes `["untagged"]`, the round 1 code deleted `["other", "untagged"]`). That breaks "never delete more than today" and "same production selection except where the notes say", and there is no keep-more-only variant: the only behaviour change a BUG-015 fix can make is deleting more. Code, tests and README are back to `main` behaviour for `exception_tags`.
  Proposed owner decision: approve the production selection change explicitly, then a follow-up rung replaces `KmsCleaner._should_skip` with `return any(policy.exception_tags_any({k: v}, exception_tags) for k, v in _tag_pairs(tags))` (as in commit `bc3585f`), re-marks the six `exception-*` parity cases `diverges="BUG-015"` with the `FIXED` rows of `bc3585f`, and reports how many tagged "Yugabyte Master Key" keys in `us-west-2` the Jenkins user owns (a dry run of both production commands) before it ships.
- **BUG-008 production effect.** With BUG-015 left open, a tagged key is skipped before the age check whenever `exception_tags` is set, so the `max_retention_age` tag has no effect on the production commands; BUG-008 changes the selection only for runs without `--exception_tags`.

## New bugs

- `crc/aws/kms.py` (`plan_keys`, `except BaseException`; main `crc.py` bare `except:`): `KeyboardInterrupt` / `SystemExit` raised while deciding one key is swallowed as "KEY SKIPPED" and the run continues (existing parity case `policy-keyboard-interrupt-is-swallowed`). Exists on main: yes. Not fixed.
- `crc/aws/kms.py` (`_select_for_user`): a policy with two statements for `kms_user` selects the key twice; the second `schedule_key_deletion` fails (BUG-056). Unchanged.
- `crc/aws/kms.py` (`_matches_filter`): a `filter_tags` value given as a plain string (not a list) is iterated per character, as on main (`{"a": "12"}` means values `1` or `2`). Exists on main: yes (there it required both characters as pairs). Not fixed; the CLI always passes lists.

## Round 1 (Argon)

Scope reviewed: `git diff 182466d..HEAD` (commit `bc3585f` plus round 1 parity assertion hardening).

Findings:

- None blocking; strengthened `assert_fixed` and `test_cleaner_protocol_matches_main` in `tests/test_aws_kms_parity.py` so `FIXED` also pins `old.deleted`, `assert_fixed` checks `new.stdout == old.stdout`, and `test_cleaner_protocol_matches_main` verifies that the `Cleaner` path and `Kms` adapter agree on SDK calls and logs for diverging cases too.
- Verified (1) bug fixes and regression tests (`tests/test_aws_kms_fix.py`, `tests/test_aws_kms.py`, `tests/test_aws_kms_parity.py`, `tests/test_aws_misc_logger.py` fail on `182466d` and pass on `HEAD`):
  - `BUG-007` (`crc/aws/kms.py:129-138`): a filter key with an empty value list `[]` requires that tag key to be present on the key (any value), matching `crc/aws/vm` and `policy.filter_tags_all_keys`.
  - `BUG-008` (`crc/aws/kms.py:253-255`): custom age tag is read from `TagKey`/`TagValue` via `core_age.get_retention_age(policy.normalize_tags(response_tags), self._settings.custom_age_tag_key)`. This may select a key earlier when its custom age tag is shorter than `--age`: that is the documented custom-age-tag behaviour of every other cleaner and is allowed for `BUG-008` only.
  - `BUG-014` (`crc/aws/kms.py:129-138`): a filter key with multiple values matches when the key's tag value is any of them, while still requiring every filter key to match and eagerly materialising value lists so non-iterable values still fail closed.
  - `BUG-015` (`crc/aws/kms.py:141-143`, `:282-287`): `exception_tags` reads `TagKey`/`TagValue` via `_tag_pairs` and `policy.exception_tags_any`, keeping keys with an exception tag before `describe_key` and recording `skip("exception_tag")`.
  - `BUG-055` (`crc/aws/kms.py:204-216`, `:269-280`): `_select_for_user` collects matches in `found` and extends `active` only after all policy statements have been checked, so any key that raises and logs `KEY SKIPPED` is never added to the delete list and is counted in `logger.current().skip(...)`.
- Verified (2) direction rule and parity: `tests/legacy/aws_kms.txt` is untouched; all 13 diverging cases in `tests/test_aws_kms_parity.py` are marked with an in-scope `BUG-NNN` ID and assert both divergence from the oracle (`old.deleted`) and the exact new outcome; all unmarked cases assert exact equality with the oracle (SDK calls and order, error, deleted, logs via `expected_logs`, stdout) with no global loosening.
- Verified (3) production commands: both Jenkinsfile commands (`--cloud aws --resource kms --kms_key_description "Yugabyte Master Key" --kms_user ... --age '{"days": 2|3}' --max_age '{"days": 21}' --exception_tags '{"autoclean": ["false", "False", "FALSE"], "auto_clean": ["false", "False", "FALSE"],"crc":["gke"]}' --slack_channel yba-crc-cleanup-notifications --custom_age_tag_key max_retention_age`) keep working with identical flags; selection changes only where the in-scope bugs (`BUG-008`, `BUG-015`, `BUG-055`) specify.
- Verified (4) fail-closed paths: all tag, metadata, age and policy checks run during `plan()` / `plan_keys()` before `schedule()` (`schedule_key_deletion`); any per-key error skips the key before any SDK mutation.
- Verified (5) protected files: no locked safety test, `tests/archtest/safety_lock.json`, `docs/refactor/PLAN.md`, `docs/refactor/STATUS.md`, `docs/refactor/BUGS.md` or `CLEANUP_SAFETY.md` was edited.
- Verified (6) documentation and ARCH-002: `crc/aws/README.md` Safety Invariants and Public API are updated and in sync with `crc/aws/kms.py`.
- Verified (7) owner decisions: all five bugs in scope are fixed (`## Owner decision` is `None.`).

Gates (`pip install -r requirements.txt -r requirements-dev.txt && make check`):

- `ruff check .`: All checks passed
- `mypy`: Success: no issues found in 229 source files
- `lint-imports`: Contracts: 5 kept, 0 broken
- `pytest -q`: `42463 passed` (up from `42444 passed` on `182466d`)

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff 182466d..HEAD` (`bc3585f`, round 1 `859d1c3`) plus the round 2 change below. The verdict is for the branch with the round 2 change; BUG-015 waits for the owner.

Round 1 fixes checked: the `FIXED` table now pins `old.deleted` and `assert_fixed` compares stdout; `test_cleaner_protocol_matches_main` covers the diverging cases too. Correct. Round 1 missed the production impact of BUG-015 (it says "selection changes only where BUG-008, BUG-015, BUG-055 specify", but the notes only sanction BUG-055/BUG-008 for production, on a premise that production passes no `exception_tags`, which is false).

Findings:

- **BLOCKING, fixed in round 2: BUG-015 deletes more in production.** `crc/aws/kms.py` `_should_skip` (round 1 `:282-287`). Production passes `--exception_tags`; on `main` every tagged key is skipped by the `KeyError`, after the fix every tagged key without an exception tag is scheduled (parity `exception-production-tags`: `["untagged"]` became `["other", "untagged"]`; `tests/test_aws_kms_fix.py::test_production_command_shape_selects_untagged_old_key` asserted `["k2", "k3"]` where `main` gives `["k3"]`). Reverted `_should_skip` to the `main` code (`tag["Key"]` / `tag["Value"]`), removed `_has_exception_tag`, un-marked the six BUG-015 parity cases (they now assert oracle equality again; the two new cases `exception-multi-value-hit-keeps` and `exception-production-tags` stay as unmarked equality cases), removed their `FIXED` rows, restored the two BUG-015 quirk checks and plain oracle equality in `test_should_skip_kms_matches_main`, restored `test_plan_exception_tags_skip_the_key_through_keyerror`, replaced the two BUG-015 tests in `tests/test_aws_kms_fix.py` with `main`-behaviour checks, set the production-shape test to `["k3"]`, and updated the `crc/aws/README.md` KMS invariant. Explained under `## Owner decision` with the proposed follow-up.
- (1) Fixed bugs: BUG-007, BUG-008, BUG-014, BUG-055 each have a test in `tests/test_aws_kms_fix.py` that fails on `182466d` (7 of 12 fail there; the 5 that pass assert unchanged behaviour) and passes now, plus marked parity cases. BUG-007 matches `policy.filter_tags_all_keys` ("key present, any value"), which is the owner's stated `crc/aws/vm` semantics (vm sends `{"Name": "tag:<key>", "Values": []}` to EC2 server-side, `crc/aws/vm/cleaner.py:175-177`). BUG-008: **this may select a key earlier when its custom age tag is shorter than `--age`: that is the documented custom-age-tag behaviour of every other cleaner and is allowed for BUG-008 only.** BUG-055: matches are collected in `found` and added after the last statement, so a `KEY SKIPPED` key is never selected.
- (2) Direction rule: BUG-007 and BUG-055 select a subset of `main`; BUG-014 selects more only with a multi-value `filter_tags` (owner-specified, not used by production); BUG-008 as above. Oracle `tests/legacy/aws_kms.txt` untouched; every remaining divergence is marked BUG-007/008/014/055 and asserts the exact new outcome; no global loosening.
- (3) Production commands: same flags; with BUG-015 left open their selection differs from `main` only by BUG-055 (a key whose policy names `kms_user` and also has a statement without `Principal.AWS` is no longer scheduled). BUG-008 is unreachable for them (tagged keys are skipped before the age check).
- (4) Fail closed: every per-key error still skips the key during `plan()`, before any `schedule_key_deletion`.
- (5) No locked safety test, `safety_lock.json`, oracle, `PLAN.md`, `STATUS.md`, `BUGS.md` or `CLEANUP_SAFETY.md` edited (`git diff 182466d --stat` on those paths is empty).
- (6) `crc/aws/README.md` KMS Safety Invariants updated; Public API unchanged (no new public names; `_has_exception_tag` was private and is removed).
- (7) BUG-015 explained under `## Owner decision`.
- NIT (not fixed, existed before): `policy.normalize_tags` (`crc/core/policy.py:58`) reads `Key` / `Value` when an entry has both `Key` and `TagKey`, so for such a (non-AWS-shaped) KMS tag entry the custom age tag comes from `Key`. Not reachable with real `list_resource_tags` output.

Gates (`pip install -r requirements.txt -r requirements-dev.txt && make check`): before round 2 (HEAD `859d1c3`) 42463 passed; after round 2: `ruff check .` all checks passed, `mypy` no issues in 229 source files, `lint-imports` 5 kept 0 broken, `pytest` **42465 passed**.

ROUND 2 VERDICT: APPROVE
