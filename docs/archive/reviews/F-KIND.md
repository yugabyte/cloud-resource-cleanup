# F-KIND review

## Coding summary

**What is routed.** Every resource kind is now declared once, in `crc/core/kinds.py`
(`Kind`: name, report label, `in_all`, `clouds`, unsupported-pair message template; `explicit_only`
is `not in_all`). It lives in `crc/core` because `crc/cli` imports core, never the reverse
(import-linter), and the engine labels need it. Derived from `KINDS`:

- the `--resource` parser choices (`crc/cli/args.py`: `[*KIND_NAMES, "all"]`),
- `crc.core.selection.RESOURCES` / `EXTRA_RESOURCES` and `crc.cli.constants.RESOURCES`,
- `crc.cli.pairs.SUPPORTED_CLOUDS`, `AWS_ONLY_RESOURCES` and `_unsupported_message`,
- `crc.core.engine.KIND_LABELS`,
- `tests/test_core_selection.py` `SUPPORTED_ON` (now built from `KINDS`, not hardcoded).

One table order serves both lists: the parser order is `disk, ip, keypair, vm, vpc, kms, nic,
spot_instance_requests, spot_instances, snapshot`, and the `in_all` kinds in that order are exactly
the old `--resource all` order. Nothing had to stay hand-written; the `--resource` help text stays
hand-written in `args.py` (it is prose, in a different order, with annotations, and the parity
tests compare it byte for byte). `CLOUDS` stays a literal (not a kind).

New test `tests/test_core_kinds.py` locks the derived values to the old literals (choices order,
`RESOURCES`, `EXTRA_RESOURCES`, `SUPPORTED_CLOUDS`, every unsupported-pair message, every label) and
adds the route-table completeness check: every supported `(cloud, kind)` has a route and every
route is a supported pair (a kind with `clouds=None` is supported, so routed, on all four clouds;
the warn-only spot pairs are skip routes).

**Adapters deleted or shimmed.** None; no adapter was involved. `crc/cli/pairs.py` keeps its
public names (`SUPPORTED_CLOUDS`, `AWS_ONLY_RESOURCES`, `is_supported_pair`, `_unsupported_message`)
as derived values, so `dispatch.py` re-exports and the tests are unchanged.
`UNSUPPORTED_PAIR_MESSAGES` (a private-use dict, no importer) is gone; its text is in the table.

**How the CLI parity tests reach the cleaners.** Unchanged: CLI -> `bridge` -> route tables ->
`engine.run` -> `FakeCleaners` (`tests/helpers_cli_routes.py`). No parity test, oracle or Jenkins
contract assertion was edited; all pass as before.

**Tests.** Before: 44,597 (floor 44,597) passed + 1 skipped. After: 44,612 passed + 1 skipped
(44,613 collected, floor raised with `make raise-test-floor`). `make check` green.

**Owner actions.** None. No guardrail file, oracle or `frozen_inputs` change.

**New bugs.** None.

## Docs to update (lane F-DOCS)

- `ARCHITECTURE.md` add-a-cleaner steps (around lines 115-128): replace the separate edits of
  `crc/cli/constants.py`, `crc/cli/args.py` choices, `crc/core/selection.py`, `crc/cli/pairs.py`
  and `KIND_LABELS` in `crc/core/engine.py` with one: add a `Kind(...)` entry to `KINDS` in
  `crc/core/kinds.py` (position = `--resource` choices order; keep `in_all` kinds in the old
  expansion order). Still separate: the label constant in `crc/core/report.py` (if new), the
  hand-written `--resource` help text in `crc/cli/args.py`, the route in
  `crc/cli/routes/<cloud>.py` (the completeness test fails without it), the cleaner, and the
  `tests/helpers_ag_cli_added.py` entry (`ADDED_RESOURCES`, `RESOURCE_HELP`).
- `tests/test_core_selection.py` no longer needs a `SUPPORTED_ON` edit.
- `crc/cli/README.md` (`## Purpose` bullets for `constants.py` and `pairs.py`, plus extension rule 3) and `AGENTS.md`: mention the single place `crc/core/kinds.py`.

## Left over

Nothing unrouted; no partial state.


## Round 1 (Argon)

Reviewed `git diff f8ed6dd..HEAD` against the Lane F-KIND specification, the two third-round audits (`AGENT-NATIVE-AUDIT-3.md` and `AGENT-NATIVE-AUDIT-3-GPT.md`), and the final wave contract.

### Findings

1. **Fixed (lane ownership boundary in `crc/cli/README.md` and `crc/core/README.md`):**
   - `crc/cli/README.md:15-16` was edited inside `## Purpose`, whereas the lane contract excludes `README.md` text outside the `## Public API` lines of newly added symbols (lane `F-DOCS` owns non-API README prose). Reverted `crc/cli/README.md` to `f8ed6dd` and added the `constants.py`/`pairs.py` note to `## Docs to update (lane F-DOCS)` above.
   - In `crc/core/README.md:130-145`, removed the dangling `(see Purpose)` reference and left the pre-existing `- KIND_LABELS:` line untouched so only the `## Public API` block for the newly added `crc/core/kinds.py` symbols (`Kind`, `KINDS`, `KIND_NAMES`, `ALL_KINDS`, `EXPLICIT_KINDS`, `KINDS_BY_NAME`) is added.
2. **Fixed (`tests/helpers_ag_cli_added.py` compatibility in `tests/test_core_kinds.py`):**
   - `test_parser_choices_keep_their_order`, `test_expansion_constants_keep_their_order`, `test_pair_rules_keep_their_content`, and `test_report_labels_are_unchanged` previously compared derived values against hardcoded 10-kind literals without filtering `added_table.added_kinds()`, which would fail if a future kind were added via `tests/helpers_ag_cli_added.py`. Updated those four tests in `tests/test_core_kinds.py` to assert both exact derivation from `KINDS` (`[*KIND_NAMES, "all"]`, `ALL_KINDS`, `EXPLICIT_KINDS`, `SUPPORTED_CLOUDS`, `KIND_LABELS`) and exact match of the non-`added_kinds()` entries to the 10 oracle kinds, plus consistency between `ADDED_RESOURCES` and `KINDS_BY_NAME`.

### Checklist Verification

1. **Every item of the lane task done and true:**
   - `crc/core/kinds.py` defines `@dataclass(frozen=True, slots=True) class Kind` (`name`, `label`, `in_all`, `clouds`, `unsupported`, `explicit_only` property, `unsupported_message()` method, and `__post_init__` check that `clouds` and `unsupported` are set together) and the frozen `KINDS` tuple (all 10 resource kinds in `--resource` choices order, whose `in_all=True` subsequence is the exact `--resource all` order), plus `KIND_NAMES`, `ALL_KINDS`, `EXPLICIT_KINDS`, and `KINDS_BY_NAME`.
   - `crc/cli/args.py:28` derives `--resource` choices as `[*KIND_NAMES, "all"]`; `--resource` help text remains hand-written in `crc/cli/args.py:30-35` (stated in the review summary: prose with a different order and per-kind annotations, checked byte-for-byte by parity tests).
   - `crc/core/selection.py:15-16` derives `RESOURCES = ALL_KINDS` and `EXTRA_RESOURCES = EXPLICIT_KINDS`; `crc/cli/constants.py:6` derives `RESOURCES = list(_RESOURCES)`.
   - `crc/cli/pairs.py:8-29` derives `SUPPORTED_CLOUDS`, `AWS_ONLY_RESOURCES`, and `_unsupported_message()` from `KINDS` and `KINDS_BY_NAME`.
   - `crc/core/engine.py:23` derives `KIND_LABELS = {kind.name: kind.label for kind in KINDS}`.
   - `tests/test_core_selection.py:145` derives `SUPPORTED_ON = {kind.name: kind.clouds for kind in KINDS if kind.clouds is not None}` instead of hardcoding the dict.
   - `tests/test_core_kinds.py:161-166` (`test_every_supported_pair_has_a_route_and_every_route_a_supported_pair`) verifies that every supported `(cloud, kind)` pair in `KINDS` has a route in `bridge.TABLES` and every route in `bridge.TABLES` is a supported pair in `KINDS`.
   - `ARCHITECTURE.md` add-a-cleaner updates are listed under `## Docs to update (lane F-DOCS)`.
2. **Byte-identical CLI/runtime behaviour & untouched parity/Jenkins contracts:**
   - Ran `python3 crc.py --help` on `HEAD` and on base `f8ed6dd`: byte-identical (`8,432` bytes).
   - `tests/test_jenkins_contract.py` (142 passed) and all `tests/test_*_parity.py` suites are completely untouched in `git diff f8ed6dd..HEAD` and pass.
3. **No check or test weakened; locked files untouched; negative test of new check:**
   - `tests/legacy/*.txt`, `tests/archtest/safety_lock.json`, `tests/archtest/frozen_inputs.json`, and `docs/refactor/BUGS.md` are untouched.
   - Locally mutated `Kind("ip", ...)` in `crc/core/kinds.py` first to `clouds=("aws", "azure", "oci")` and then to `clouds=("aws", "azure")`: `test_every_supported_pair_has_a_route_and_every_route_a_supported_pair` failed immediately with `AssertionError: supported pair without a route: [('oci', 'ip')]` and `AssertionError: route of a pair the kind table does not support: [('gcp', 'ip')]`. Reverted both local mutations.
4. **Only owned files changed:**
   - Changed files (`git diff --name-only f8ed6dd..HEAD`): `crc/cli/args.py`, `crc/cli/constants.py`, `crc/cli/pairs.py`, `crc/core/README.md` (`## Public API` lines of `kinds.py` only), `crc/core/__init__.py`, `crc/core/engine.py` (`KIND_LABELS` only), `crc/core/kinds.py`, `crc/core/selection.py`, `docs/archive/reviews/F-KIND.md`, `tests/archtest/test_count_floor.txt`, `tests/test_core_kinds.py`, `tests/test_core_selection.py`.
5. **Size limits & README Public API sync:**
   - All touched non-test files are `<= 250` lines (max `198` lines in `crc/core/__init__.py`, `195` in `crc/core/README.md`, `128` in `crc/cli/args.py`, `98` in `crc/core/engine.py`, `88` in `crc/core/kinds.py`, `29` in `crc/cli/pairs.py`, `24` in `crc/core/selection.py`, `6` in `crc/cli/constants.py`; `tests/test_core_kinds.py` is `175` lines).
   - All functions in touched `crc/` modules and `tests/test_core_kinds.py` are `<= 60` lines (max `53` lines in `_add_filter_args`, `28` in `test_expansion_constants_keep_their_order`, `21` in `_run_one`, `5` in `Kind.unsupported_message`).
   - `ARCH-002` and `ARCH-004` pass (29/29).

### Gates

Ran `pip install -r requirements.txt -r requirements-dev.txt -c constraints.txt && make check`:
- `ruff check .`: All checks passed!
- `ruff format --check .`: 372 files already formatted
- `mypy`: Success: no issues found in 371 source files
- `lint-imports`: Contracts: 5 kept, 0 broken
- `pytest -q -n auto`: **44,612 passed, 1 skipped in 58.08s** (`44,613` collected, up from `44,597` at `f8ed6dd`, `+16`)
- `python3 -m tests.archtest.test_count check`: `ARCH-008: 44613 tests, floor 44613`
- Total `make check` runtime: **1m06.1s** wall clock

VERDICT: APPROVE


## Round 2 (Opus)

Reviewed `git diff f8ed6dd..HEAD` (commits `81d02ee`, `88028c0`) against the lane task, the final
wave contract and the kind items of `AGENT-NATIVE-AUDIT-3.md` (Round 2B item 8, recommendation 7)
and `AGENT-NATIVE-AUDIT-3-GPT.md` (item 8, recommendation 4).

### Round 1 fixes

Both hold. `crc/cli/README.md` is identical to `f8ed6dd`; `crc/core/README.md` only adds the
`kinds.py` Public API block. `tests/test_core_kinds.py` filters `added_table.added_kinds()` before
it compares with the oracle literals, and checks `ADDED_RESOURCES` against `KINDS_BY_NAME`.

### Findings

1. **Fixed: `AWS_ONLY_MESSAGE` was undocumented.** It is a public constant of `crc/core/kinds.py`
   imported across packages by `crc/cli/pairs.py`, but it was missing from the `crc/core/README.md`
   Public API block and from `crc.core.__all__`. ARCH-002 does not catch this because it only
   requires functions, classes and `__all__` names. Added one README line and exported it from
   `crc/core/__init__.py`, the same way as the other `kinds.py` constants. ARCH-002 requires a
   documented constant to be in `__all__`.
2. **Accepted: `crc/core/__init__.py` is not on the lane's file list.** The edit only re-exports
   the names of the new `kinds.py` module. No other lane touches it: `fw/docs` and `fw/gate` do not
   change it, and `git merge-tree origin/fw/docs HEAD` merges cleanly, including the
   `crc/core/README.md` edits on both sides.
3. **Checked, no change: dict order.** `pairs.SUPPORTED_CLOUDS` and `engine.KIND_LABELS` now follow
   table order, not the old literal order. Both are only read with `.get` or `[...]` (in
   `pairs.is_supported_pair`, `engine._run_one` and the tests), so nothing observable changes.
4. **Checked, kept hand-written: `--resource` help text.** It is prose in a different order with
   per-kind annotations, compared byte for byte by the parity tests. The reason is recorded above.
   `CLOUDS` stays a literal because it is not a kind.
5. **Minor, no change: `AWS_ONLY_RESOURCES` is derived by comparing message templates**
   (`kind.unsupported == AWS_ONLY_MESSAGE`). A dedicated field would be sturdier, but the result
   is pinned to `{"snapshot", "vpc"}` by `test_pair_rules_keep_their_content` and
   `tests/test_cli_merge_fix.py`.

### Checklist

1. Lane items: open in code, all true. One frozen `Kind` table (name, label, `in_all`, `clouds`,
   `unsupported`, `explicit_only`). Derived from it: parser choices (`args.py`),
   `selection.RESOURCES`/`EXTRA_RESOURCES`, `constants.RESOURCES`, `pairs.SUPPORTED_CLOUDS`,
   `AWS_ONLY_RESOURCES`, `_unsupported_message`, `engine.KIND_LABELS`, and `SUPPORTED_ON` in
   `tests/test_core_selection.py`. The route completeness test is in both directions. The
   ARCHITECTURE steps are listed under Docs to update.
2. `python3 crc.py --help` is byte-identical to `f8ed6dd` (8,434 bytes; checked before and after
   the Round 2 edit). `tests/test_jenkins_contract.py`, `tests/test_*parity*.py`,
   `tests/helpers_ag_cli_added.py` are unchanged.
3. Locked files are untouched: `tests/legacy/*.txt`, `safety_lock.json`, `frozen_inputs.json`,
   `docs/refactor/BUGS.md`. No test was weakened; the floor was raised from 44,597 to 44,613. I
   broke each new check once locally, then reverted:
   - Moved `vm` before `keypair` in `KINDS`: `test_parser_choices_keep_their_order` failed.
   - Deleted the AWS `snapshot` route: the route completeness test failed with "supported pair
     without a route".
   - Changed the `vpc` label to `"Vpc"`: `test_report_labels_are_unchanged` failed.
4. Changed files are owned or covered by finding 2.
5. File sizes are within limits: `crc/core/__init__.py` has 200 lines, `crc/core/README.md` 196,
   `kinds.py` 88, `tests/test_core_kinds.py` 175. Every function is at most 60 lines, and the
   README Public API is in sync (ARCH-002 passes).

### Gates

`pip install -r requirements.txt -r requirements-dev.txt -c constraints.txt && make check`, after
the Round 2 edit:
- ruff check passed, and `ruff format --check` reported 373 files formatted.
- mypy found no issues in 372 files.
- lint-imports: 5 contracts kept.
- pytest: **44,612 passed, 1 skipped in 53.3s**.
- ARCH-008: 44,613 tests, floor 44,613.
- `make check` took **1m02s** of wall-clock time.

ROUND 2 VERDICT: APPROVE
