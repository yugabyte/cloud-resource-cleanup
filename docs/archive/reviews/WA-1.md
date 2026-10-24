## Coding summary

Lane WA-1 (test only): the notification helper cases moved out of `tests/test_cli_crc_parity.py`
into `tests/test_notify_crc_parity.py`. No production file, oracle or locked test was edited.

- **What is routed.** The 15 case names that called `get_msg`, `slack_lookup_user_by_email`,
  `get_user_groups_list`, `write_influxdb` and `ping_on_slack` (4 `get_msg_*`, `lookup_known`,
  `lookup_unknown`, `groups`, 4 `write_*`, 4 `ping_*`) now compare main's `CRC` methods
  (`load_legacy_crc()`) with `crc.core.report.get_msg`, `SlackNotifier` (user lookup, groups,
  `ping_owners` from `crc.notify.slack_owners`) and `InfluxNotifier.write`. Same grid as before
  (3 Slack/Influx failure variants x dry run x 4 Slack modes x 6 Influx modes, 144 scenarios per
  name, 2160 cases). Slack calls, Influx points, stdout and the result or error are compared.
- **Adapters deleted or shimmed.** None. `tests/helpers_wa1_notify.py` is new: a small settings
  object (clients, channel, Influx bucket and suffix; named `CRC` because the BUG-030 text prints
  the class name), `execute_current`, `current_get_msg`, `current_ping` (the BUG-033 pre-loop of
  main) and `scenario_grid`. The new file does not import `crc.cli.runner`.
- **How the CLI parity tests reach the cleaners.** Unchanged: `bridge.run_routed` with the route of
  the pair and a recording fake cleaner. In `tests/test_cli_crc_parity.py` only the moved blocks
  were removed (their `DIRECT` rows, the matching `DIRECT_ACTIONS` rows, `_slack_notifier`, the
  `SlackNotifier` import, one docstring paragraph and one comment). Constructor and
  `scan_instances_with_tag` cases are untouched (WA-2). Grep
  `\.(get_msg|slack_lookup_user_by_email|get_user_groups_list|write_influxdb|ping_on_slack)\(` on
  that file finds nothing.
- **Asserted divergences.**
  - BUG-030: no `influxdb_conn`, the write prints `'CRC' object has no attribute
    'resource_suffix'` on both sides (explicit test).
  - BUG-033: `ping_on_slack({"alice": 3})` fetches the groups, then fails on `len()`; one
    `usergroups_list` call on both sides (explicit test).
  - `get_msg(None)`: main raises `TypeError: object of type 'NoneType' has no len()`, the report
    function raises `TypeError: 'NoneType' object is not iterable`. Production keeps main's text
    through `crc.cli.notify._section`. Asserted per side.
  - `get_msg` with the word "Gone": main prints it (also in dry run); the current side takes an
    `Operation` and raises `KeyError: 'Gone'` mapping the word. Unreachable in production.
- **Left in `tests/test_cli_crc_parity.py` on the current `CRC`.** The `nic_none` and `vm_none`
  rows still run `notify_deleted_nic_via_slack` / `notify_deleted_vm_via_slack` on the current
  `CRC` (their old current-side action was `get_msg`, which is gone, so they now compare the
  `notify_*` methods like any unrouted row). They are not among the five methods; a later lane that
  removes the `notify_*` methods should route them. Constructor and scan cases stay with WA-2.
- **Test count.** Before 44411, after 44414 (`make check`, 44414 passed); the 2160 moved cases are
  equal in both files, plus 3 new tests (BUG-030, BUG-033, non-vacuous guard). Floor raised to
  44414 with `make raise-test-floor`; if WA-2 raises it as well, the integrator takes the higher.
- **Owner actions.** None.

## New bugs

None found. (`get_msg(None)` text above is a message difference on an input production never
passes: `crc/core/report.py:178`.)

## Round 1 (Argon)

Scope reviewed: `git diff fc6fa4d..HEAD` on branch `wa/1` (lane `WA-1`).

### Findings & fixes made in review

1. **Prevented `nic_none` and `vm_none` from falling back to `NewCRC.notify_deleted_*_via_slack` and removed merge-conflicting hunks in `tests/test_cli_crc_parity.py`**:
   - In `c963a22`, removing `"nic_none"` and `"vm_none"` from `DIRECT_ACTIONS` caused `direct_cases()` to set `routed = None` for those 288 cases, so `compare()` fell through to `if spec is None:` and executed `NewCRC.notify_deleted_nic_via_slack` and `NewCRC.notify_deleted_vm_via_slack` on `crc/cli/notify.py` (`NotifyMixin`). In addition, `c963a22` edited the module docstring (lines 10-12) and the `if spec is None:` block in `compare()` (lines 183-184) that lane `WA-2` owns, and left dead `"ping_dict"` / `"ping_empty"` entries in `NOTIFY_SPECS`.
   - Restored `"nic_none": lambda c: len(None)` and `"vm_none": lambda c: len(None)` in `DIRECT_ACTIONS` (matching the unrepresentable `get_deleted=None` `TypeError` without calling `NewCRC.get_msg` or `NewCRC.notify_deleted_*_via_slack`), removed the dead `"ping_dict"` / `"ping_empty"` entries from `NOTIFY_SPECS`, and reverted the two hunks outside the moved notify blocks so `tests/test_cli_crc_parity.py` merges cleanly with `WA-2` and calls zero methods on `NotifyMixin`.
2. **Removed test-side `BUG-033` pre-loop from `current_ping` (`tests/helpers_wa1_notify.py`, `tests/test_notify_crc_parity.py`)**:
   - `current_ping` in `c963a22` pre-called `notifier.user_groups()` and `for key in operated.keys(): len(operated[key])` in test code and passed `get_groups=lambda: user_groups` to `notifier.ping_owners(...)`, bypassing `SlackPoster.ping_owners`'s default `get_groups=None` path on `ping_dict` / `ping_empty` and never reaching `Report` or `ping_owners` on `ping_list` / `ping_no_len`.
   - Changed `current_ping` to construct `Report` and call `crc.slack().ping_owners(report, resource)` directly as production does, and asserted the `BUG-033` divergence on `ping_list` and `ping_no_len` (`assert_bug_033_ping`: `old` calls `usergroups_list` then fails in the dead `keys()`/`len()` loop; `new` fails in `Report.__post_init__` freezing `by_owner` before `ping_owners` runs). Also added `assert len(NOTIFY_CASES) == EXPECTED_CASES` (`2160`) in `test_cases_are_not_vacuous` and fixed the `BUG-030` attribute name in `CRC`'s docstring (`'resource_suffix'`).

### Checklist verification

1. **No legacy CLI imports or calls**:
   - Grepped `tests/test_cli_crc_parity.py`, `tests/test_notify_crc_parity.py`, and `tests/helpers_wa1_notify.py` for `\.(get_msg|slack_lookup_user_by_email|get_user_groups_list|write_influxdb|ping_on_slack)\(`: zero matches in `tests/test_cli_crc_parity.py` and `tests/helpers_wa1_notify.py`; in `tests/test_notify_crc_parity.py` those five methods are called only on the legacy oracle (`load_legacy_crc()`).
   - Runtime check: poisoned every method on `crc.cli.notify.NotifyMixin` to raise `AssertionError`; all 11,747 tests in `tests/test_notify_crc_parity.py` and `tests/test_cli_crc_parity.py` pass.
2. **All comparisons and case counts preserved**:
   - All 15 moved case families (`15 * 144 = 2,160` cases) in `tests/test_notify_crc_parity.py` compare `load_legacy_crc()` against `crc.core.report.get_msg`, `SlackNotifier` (`lookup_user_by_email`, `user_groups`, `ping_owners`), and `InfluxNotifier.write` across `("result", "slack", "influx", "stdout")`, with `BUG-030`, `BUG-033`, `get_msg_none`, and `get_msg_bad_operation` explicitly asserted.
   - Total test count increased from 44,411 (`fc6fa4d`) to 44,414 (`HEAD`, +3 standalone tests in `tests/test_notify_crc_parity.py`); `tests/archtest/test_count_floor.txt` raised to 44,414.
3. **Non-vacuous (mutation checks, reverted locally without committing)**:
   - Mutating `crc.core.report.get_msg` (`"following"` -> `"MUTATED"`) caused 144 failures in `tests/test_notify_crc_parity.py`.
   - Mutating `crc.notify.slack_owners.SlackPoster.post` (appending `"!"` to `text`) caused 36 failures in `tests/test_notify_crc_parity.py`.
   - Mutating `crc.notify.influx.InfluxNotifier.write` to a no-op caused 577 failures in `tests/test_notify_crc_parity.py`.
4. **Owned files only**:
   - `git diff --name-only fc6fa4d..HEAD`: `docs/archive/reviews/WA-1.md`, `tests/archtest/test_count_floor.txt`, `tests/helpers_wa1_notify.py` (130 lines), `tests/test_cli_crc_parity.py` (only the moved blocks), `tests/test_notify_crc_parity.py` (231 lines). No production files touched.

### Gates & test count

Ran `pip install -r requirements.txt -r requirements-dev.txt` followed by `make check`:

- `python3 -m ruff check .`: passed
- `python3 -m mypy`: passed (376 source files)
- `lint-imports`: passed (5 contracts kept, 0 broken)
- `python3 -m pytest -q -n auto`: **44414 passed in 61.10s** (`make check` wall time: **1m11.8s**; owned files: **11747 passed in 9.58s**)
- `python3 -m tests.archtest.test_count check`: `ARCH-008: 44414 tests, floor 44414`

VERDICT: APPROVE


## Round 2 (Opus)

Scope: `git diff fc6fa4d..HEAD` on `wa/1` (commits `c963a22`, `e3fca8f`).

### Round 1 fixes re-checked

- `nic_none` / `vm_none` (`tests/test_cli_crc_parity.py:355-356`): the current side is now
  `len(None)` instead of `c.get_msg(..., None)` on the current `CRC`. It no longer calls any of
  the five methods, and it does not fall through to `NewCRC.notify_deleted_*`. In practice it is a
  frozen expectation: no Slack, no Influx, no stdout, and `TypeError: object of type 'NoneType'
  has no len()`, compared with main's `notify_deleted_{nic,vm}_via_slack(get_deleted=None)`.
  Accepted, because `get_deleted=None` cannot reach any routed cleaner.
- `current_ping` (`tests/helpers_wa1_notify.py:99-108`): goes through `Report` and
  `SlackNotifier.ping_owners(report, resource)` with the default lookups, which matches what
  `SlackNotifier.send` does in production (`crc/notify/slack.py:194-197`). Before this lane,
  `ping_list` / `ping_no_len` were compared against `ping_owners_raw`, which copies main's
  pre-loop. They are now asserted as the BUG-033 divergence (`assert_bug_033_ping`). Main's side
  is pinned to frozen data: one `usergroups_list` call, the groups-failure stdout and the exact
  error. The routed side is pinned to its own error, with no Slack, Influx or stdout. That is not
  weaker than before. Note: `docs/refactor/BUGS.md:140` still lists BUG-033 as `open`. The routed
  path has dropped the pre-loop, but `crc/cli/notify.py:75-79` keeps it. Updating the status is
  outside this lane, so it is left to the integrator.
- The dead `ping_dict` / `ping_empty` rows in `NOTIFY_SPECS` were removed, and both cases remain
  in the new file.

### Findings & fixes made in review

1. **Two compared keys were lost** (`tests/test_notify_crc_parity.py:165`). At `fc6fa4d`, the
   moved rows went through `compare` with `routed=None`, or through `compare_notify` with
   `for key in old`. That compared every recorded key: `result`, `attributes`, `adapters`,
   `reads`, `slack`, `influx`, `stdout`. The new `compare` only checks `COMPARED` (4 keys).
   I added `assert old["adapters"] == [] == old["reads"]` on main's side as a frozen expectation
   (the routed side has no adapters or reads), so those two keys are covered again. `attributes`
   is a snapshot of the constructor taken before the action runs. It is constructor parity,
   covered by `test_constructor_matches_main` (WA-2's area), and it cannot be compared without
   the current `CRC`. Not restored.

### Checklist

1. Grep for `crc\.cli|NewCRC` in `tests/test_notify_crc_parity.py` and
   `tests/helpers_wa1_notify.py`: docstrings only, no imports. The five methods appear in the new
   file only in the legacy column of `CASES` (main's `CRC` through `oracle` ->
   `load_legacy_crc("legacy_crc_main_crc")`). Grep
   `\.(get_msg|slack_lookup_user_by_email|get_user_groups_list|write_influxdb|ping_on_slack)\(`
   on `tests/test_cli_crc_parity.py`: no matches.
2. Same 15 case names, same grid (3 failure variants x 2 dry-run settings x 4 Slack modes x
   6 Influx modes = 144 per name), 2160 cases (pinned by `EXPECTED_CASES`). The expected values of
   divergent cases are frozen literals, not derived from the routed side: `get_msg_none`
   (`NONE_LEN` / `NONE_ITER`), `get_msg_bad_operation` (`BAD_WORD_TEXT` / `KeyError`), BUG-033
   (`BUG_033_PING_DIVERGENT`). BUG-030 has an explicit test.
3. Mutations, run locally and reverted, not committed:
   - `crc/core/report.py:181,207`: changed the dry-run text and the "the following" text of
     `get_msg` / the owner ping. 180 failures in `tests/test_notify_crc_parity.py`.
   - `crc/notify/influx.py:77`: changed the suffix separator `_` to `-`. 72 failures.
4. Changed files: `docs/archive/reviews/WA-1.md`, `tests/archtest/test_count_floor.txt`,
   `tests/helpers_wa1_notify.py`, `tests/test_cli_crc_parity.py` (moved blocks only; constructor
   and `scan_instances_with_tag` cases untouched), `tests/test_notify_crc_parity.py`. No
   production code.
6. `pip install -r requirements.txt -r requirements-dev.txt`, then `make check` (before my fix):
   ruff, mypy, import-linter (5 kept), **44414 passed in 53.80s**, ARCH-008 44414 / floor 44414,
   wall time 1m13.0s. After my fix: see gates below.

## New bugs

None.

### Gates after the round 2 fix

`make check`: ruff passed, mypy passed, import-linter 5 kept / 0 broken, **44414 passed in
50.96s**, ARCH-008 44414 / floor 44414 (test count unchanged, so the floor needs no change), wall
time 0m57.5s.

ROUND 2 VERDICT: APPROVE
