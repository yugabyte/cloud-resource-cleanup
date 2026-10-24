## Coding summary

Lane CL2-AWS (test-only): `tests/test_cli_bridge_aws.py` no longer reaches the current legacy CLI code.

- **What is routed:** nothing changed in production. Every AWS kind (ip, kms, vpc, vm delete/stop, spot_instance_requests, disk) was already routed through `crc/cli/bridge.py`; the test drives `bridge.run_routed` with a fake cleaner (and the real `VmCleaner` in the last test).
- **Adapters deleted or shimmed:** none (production untouched).
- **How the parity tests now reach the cleaners:** the legacy side was already `load_legacy_crc()` (`tests/legacy/crc_main.txt` bound to the module oracles), so no legacy-CLI behaviour had to be re-frozen and no run-log line data was needed: the compared stdout, Slack, Influx and exception records are identical to the oracle's without any run-log assertions. The only link to current legacy code was the import of fakes (`Scenario`, `Logs`, `make_fake`, `build`, `scenario_grid`, the `oracle` fixture, constants) from `tests/test_cli_crc_parity.py`, which imports `crc.cli.resource_ops` and `crc.cli.vm_ops`. Those fakes now live in `tests/helpers_cl2_aws.py` (a copy; the `oracle` fixture uses `load_legacy_crc("legacy_crc_main_cl2_aws")`). The test only imports `crc.cli.runner.CRC` as the context object for the bridge.
- **Verification:** grep of both files finds no import of `resource_ops`, `vm_ops`, `notify`, `adapter_args`, `report_calls`, `helpers_legacy_handlers` and no call of CRC `delete_*`/`stop_*`/`notify_*`. Re-running the test with all 18 `delete_*`/`stop_*`/`notify_*` methods of the current `CRC` MRO replaced by raising stubs still passes all cases.
- **Test count:** `tests/test_cli_bridge_aws.py` 675 before and after; whole suite 44400 before and after (floor unchanged, no `raise-test-floor` needed). `make check` green.
- **Owner actions:** none. Note for the integrator: `crc/cli/runner.py` still imports `ResourceOps`/`VmOps` (production), so the final deletion lane must keep `CRC` importable without them; `tests/helpers_cl2_aws.py` is independent of that.

## New bugs

None found.

## Round 1 (Argon)

Scope reviewed: `git diff 2bd8e67..HEAD` on branch `cl2/aws` (lane `CL2-AWS`, commit `fcaf6d9`).

### Findings

None (no code or test changes needed).

### Checklist verification

1. **No legacy CLI imports or calls (`grep`):** Neither `tests/test_cli_bridge_aws.py` nor `tests/helpers_cl2_aws.py` imports `crc.cli.resource_ops`, `crc.cli.vm_ops`, `crc.cli.notify`, `crc.cli.adapter_args`, `crc.cli.report_calls`, or `tests.helpers_legacy_handlers`, and neither calls any `delete_*`, `stop_*`, or `notify_*` method on the current `crc.cli.runner.CRC` (all `c.delete_*` / `c.stop_*` lambdas in `KINDS` run solely on `oracle.CRC`). `NewCRC` is only instantiated as the context object passed to `bridge.run_routed` and `bridge.build_notifiers`.
2. **Parity comparisons, case counts, and assertions preserved:** Only the import path in `tests/test_cli_bridge_aws.py:26` changed (`from tests.test_cli_crc_parity` -> `from tests.helpers_cl2_aws`). `tests/helpers_cl2_aws.py` is an exact self-contained copy of `Scenario`, `Logs`, `make_fake`, `FakeSlack`, `FakeWriter`, `FakeInflux`, `Case`, `oracle` (`load_legacy_crc("legacy_crc_main_cl2_aws")`), `build`, `scenario_grid`, `FULL`, `EMPTY`, and `AGE`. All 675 test cases and all assertions (`new == old` comparing `result`, `slack`, `influx`, `stdout`, plus `seen` context assertions, route notify modes, `REPORT_THEN_RERAISE`, `extra_notifiers`, and real `VmCleaner` through `bridge.run_routed`) are unchanged. The test never asserted run-log lines and has no fixed-bug divergences at the bridge layer.
3. **Non-vacuity (mutation test):** Verified in-memory (not committed) that:
   - Mutating `VmCleaner.apply` to drop one succeeded instance (`succeeded[:1]`) fails `test_real_vm_cleaner_through_bridge_matches_oracle_report_names_and_ids[False-delete]` and `[False-stop]` on both Slack and Influx records.
   - Mutating `SlackNotifier.send` to append text to Slack messages fails the `ip-delete-full` bridge parity cases.
4. **File ownership & CLEAN-2 contract:** Only `tests/test_cli_bridge_aws.py`, `tests/helpers_cl2_aws.py`, and `docs/archive/reviews/CL2-AWS.md` were modified; no production code (`crc/**`), other tests, hash-locked safety tests, `tests/legacy/*.txt`, or `tests/archtest/**` were touched.

### Gates & test count

Ran `pip install -r requirements.txt -r requirements-dev.txt && make check`:
- `python3 -m ruff check .`: passed
- `python3 -m mypy`: passed (no issues found in 364 source files)
- `lint-imports`: passed (5 contracts kept, 0 broken)
- `python3 -m pytest -q -n auto`: **44400 passed in 55.70s** (`tests/test_cli_bridge_aws.py`: **675 passed in 2.37s**)
- `python3 -m tests.archtest.test_count check`: `ARCH-008: 44400 tests, floor 44400`

VERDICT: APPROVE


## Round 2 (Opus)

Scope: `git diff 2bd8e67..HEAD` on `cl2/aws` (commits `fcaf6d9`, `9d11156`), plus this round's fix.

### Round 1 check

Round 1 made no fixes and reported no findings. Its claims hold: the diff of
`tests/test_cli_bridge_aws.py` is the single import line (`tests.test_cli_crc_parity` ->
`tests.helpers_cl2_aws`), and an AST comparison of `tests/helpers_cl2_aws.py` against
`tests/test_cli_crc_parity.py` shows every copied definition (`FULL`, `EMPTY`, `SLACK_MODES`,
`INFLUX_MODES`, `Scenario`, `Logs`, `make_fake`, `FakeSlack`, `FakeWriter`, `FakeInflux`, `Case`,
`build`, `scenario_grid`, `AGE`) byte-identical; only the `oracle` fixture differs (module name
`legacy_crc_main_cl2_aws`).

### Findings

1. **Fixed (test strengthening, no assertion removed):** the real `VmCleaner` test compared only
   result/Slack/Influx/stdout, so dropping an SDK `terminate_instances`/`stop_instances` call in
   `crc/aws/vm/cleaner.py` passed all 675 cases (pre-existing gap, not introduced by the lane; the
   oracle side drives fake adapters, so its SDK calls cannot be compared). Added a fixed expectation
   in `test_real_vm_cleaner_through_bridge_matches_oracle_report_names_and_ids`: no mutating call
   in a dry run, otherwise exactly `(method, ("i-1",))`, `(method, ("i-2",))` in that order.
2. Note (no action): importing the test still loads `crc.cli.resource_ops`, `vm_ops`, `notify`,
   `adapter_args`, `report_calls` transitively via production `crc.cli.runner` (the allowed `CRC`
   context object). The deletion lane must cut that import in `runner.py`; the test files themselves
   do not reference them.

### Checks

1. **grep:** neither owned file imports `crc.cli.resource_ops|vm_ops|notify|adapter_args|report_calls`,
   `tests.helpers_legacy_handlers` or `tests.test_cli_crc_parity`; every `delete_*`/`stop_*` call is
   in a `KINDS` lambda run only on `oracle.CRC` (`_legacy`); `NewCRC` is only constructed as context.
2. **Comparisons:** all comparisons are unchanged and still against `load_legacy_crc()`; the file
   never asserted run-log lines (the bridge's `init_run_logging` is patched out and stdout equals the
   oracle's), and exact equality passes, so there is no fixed-bug divergence needing a BUG-NNN.
   675 cases before and after.
3. **Mutations (local, reverted):** Slack text in `crc/core/report.py:182` ("following" ->
   "followng") -> 220 failed; `VmCleaner.apply` dropping the last succeeded name -> 2 failed;
   skipping `terminate_instances` for `i-2` -> passed before the fix, 1 failed after it.
4. **Files changed:** `tests/test_cli_bridge_aws.py`, `tests/helpers_cl2_aws.py`, this review file.
   No `crc/**`, other tests, hash-locked tests, `tests/legacy/*.txt` or `tests/archtest/**`.
6. **Gates:** `pip install -r requirements.txt -r requirements-dev.txt && make check`: ruff, mypy,
   lint-imports pass; **44400 passed in 53.49s** (`make check` 1m12.6s wall);
   `ARCH-008: 44400 tests, floor 44400`. `tests/test_cli_bridge_aws.py`: 675 passed in 2.5s.

## New bugs

None found.

ROUND 2 VERDICT: APPROVE
