## Coding summary

Lane WB-1: the legacy CLI layer is deleted. Every (cloud, kind) pair already ran through
`crc/cli/bridge.py` and the route tables; nothing was left on a legacy path, so no kind was kept
unrouted and no partial state was needed.

**Deleted**
- `crc/cli/resource_ops.py`, `adapter_args.py`, `report_calls.py`, `vm_ops.py`, `notify.py` and
  `base.py` (`CRCBase` was only the base of the mixins; its constructor and attributes moved).
- `tests/helpers_legacy_handlers.py` (no test imported it).
- The re-exports of `crc.py` that nothing needs: it now exports `main`, `get_argparser` and the
  validators (`is_valid_*`, `are_values_of_dict_lists`, `validate_influxdb_inputs`,
  `_validate_influxdb_input`), which the tests and `python crc.py` use. The adapter classes,
  `CRC`, `Snapshot`, `WebClient`, `InfluxDBClient`, `Point`, the constants and `notify_cleanup`
  are no longer re-exported. No Jenkins command uses them (Jenkins runs `python crc.py <flags>`).

**Kept and moved**
- `crc/cli/runner.py`: `CRC` is now the run context the bridge reads: the constructor with the
  GCP `project_id` check, the attributes (`slack_client`, `slack_channel`, `influxdb_client`,
  `influxdb_bucket`, `resource_suffix` only with `influxdb_conn` (BUG-030), ...) and `get_msg`
  (the AWS `snapshot` console line). No `notify_*`, `write_influxdb`, `ping_on_slack` or
  operation method is left on it.
- `crc/cli/scan.py`: `scan_instances_with_tag(crc, tag_key, tag_value)` (was `CRC.scan_instances_with_tag`
  and `_get_vm`), same adapter selection, arguments, errors and messages. `_find_hits` looks it up
  in `scan` at call time.

**Routed, shimmed and deleted adapters**: routing unchanged (all pairs were routed). No adapter
class in `crc/<cloud>/**` was touched (the next wave deletes them per cloud); the hash-locked
safety tests and `tests/archtest/**` are untouched.

**How the CLI parity tests reach the cleaners**: unchanged for the routed pairs (the fake
cleaners of `tests/helpers_cli_routes.FakeCleaners` through `bridge.build_registry` and
`bridge.build_notifiers`). The fake `CRC` of the parity tests stays the context object. Only the
scan path moved: `test_cli_main_parity.py` (`run_new`), `test_cli_route_fix.py` and
`test_cli_logger.py` patch `crc.cli.scan.scan_instances_with_tag` (a call-time seam) with a
function that calls the fake `CRC.scan_instances_with_tag`, so the same events as the oracle
(`crc.scan_instances_with_tag` with the tag arguments) are recorded and asserted.
`test_cli_scan_parity.py` drives `crc.cli.scan.scan_instances_with_tag(context, key, value)` on
the same scenario grid as `main`'s `CRC.scan_instances_with_tag` (oracle `load_legacy_crc`) and
compares every field as before. `test_core_selection.py::test_all_expansion_order_follows_crc_py`
compares with the oracle module (`load_legacy_crc`) instead of `crc.py`'s old re-exports.
`test_crc_py_reexports_the_names_the_contract_needs` lists the names `crc.py` still exports and
checks them on both modules.

**Tests deleted**: none. Every test that touched deleted code was retargeted, not removed, so
no replacement table is needed. `tests/helpers_legacy_handlers.py` was an unreferenced helper
(no test names).

**Test count**: 44415 before, 44415 after (floor 44415, ARCH-008 green; no `raise-test-floor`
needed).

**crc/ Python lines**: 19279 before, 18563 after (-716; `crc.py` itself 102 -> 28 lines).

**Gates**: ruff, lint-imports (5 kept), the full pytest run and ARCH-008 are green. `mypy` reports
the same 7 `import-untyped` errors for `google.cloud`/`azure.*` on this VM before and after my
change (Python 3.12, SDK stubs missing; pyproject targets 3.11); I added no mypy error.
`make check` stops at that step on this VM, so the remaining steps were run by hand.

**Owner actions**: none for the locked tests or BUGS.md/CLEANUP_SAFETY.md (not touched in this
lane). Possible follow-up for the docs lane: `AGENTS.md` still names `HANDLERS`,
`crc/cli/vm_ops.py` and `crc/cli/resource_ops.py` in "Workflow: add or change a cleaner";
`ARCHITECTURE.md` and `CLEANUP_SAFETY.md` may mention the deleted modules.

## What is left

- `crc/cli/constants.py`: `DELETED`, `STOPPED`, `NICS`, `DISKS`, `VMS`, `IPS`, `KEYPAIRS`, `KMS`,
  `SPOT_INSTANCE_REQUEST` and `OPERATIONS` have no production reader now (only `CLOUDS` is used,
  by `scan.py`); its docstring still says "shared by the CRC class and `crc.py`". Not removed
  because tests may read them; remove in the docs/cleanup lane after checking.
- Adapter classes in `crc/<cloud>/**` (next wave); `scan.py` still builds the VM adapters of the
  four clouds, so that wave must give `--scan_tag` a Cleaner or keep the VM adapter's
  `find_instances_with_tag`.
- Tests and test docstrings that mention "legacy `CRC` methods" as oracle history are left, since
  the oracle is `tests/legacy/crc_main.txt`.

## New bugs

None found.

## Round 1 (Argon)

### Findings

- **Check (1) Production paths**: Verified `python3 crc.py --help` and dry-run argument parsing across production command shapes (AWS/GCP/Azure/OCI cleanup and `--scan_tag`), plus confirmed `crc.cli.scan` (`run_scan`, `scan_instances_with_tag`) and all 4 cloud route tables (`crc.cli.bridge.TABLES`) import and execute cleanly in tests. `crc.cli.runner.CRC` retains the exact constructor, attributes (including BUG-030 `influxdb_bucket` / `resource_suffix` conditional assignment) and `get_msg` helper used by the bridge, notifiers and scan; `scan_instances_with_tag` lives in `crc/cli/scan.py` with identical VM adapter selection, arguments and error messages.
- **Check (2) Tests & oracle parity**: Zero tests were deleted (`tests/helpers_legacy_handlers.py` was an unreferenced helper module with no test functions). `test_cli_scan_parity.py` was retargeted to `crc.cli.scan.scan_instances_with_tag` against `load_legacy_crc()`; `test_cli_main_parity.py`, `test_cli_route_fix.py` and `test_cli_logger.py` patch `crc.cli.scan.scan_instances_with_tag` at call time; `test_core_selection.py::test_all_expansion_order_follows_crc_py` compares against `load_legacy_crc()`. No oracle comparison was dropped.
- **Check (3) Deleted files, names & READMEs**: Confirmed `crc/cli/resource_ops.py`, `crc/cli/adapter_args.py`, `crc/cli/report_calls.py`, `crc/cli/vm_ops.py`, `crc/cli/notify.py`, `crc/cli/base.py`, `tests/helpers_legacy_handlers.py` and the unused `crc.py` re-exports are deleted and unreferenced across `crc/`, `tests/` and `crc.py`. Also removed the unused legacy report label constants (`DELETED`, `STOPPED`, `NICS`, `DISKS`, `VMS`, `IPS`, `KEYPAIRS`, `KMS`, `SPOT_INSTANCE_REQUEST`, `OPERATIONS`) from `crc/cli/constants.py` after verifying no production or test file imported them, and synced `crc/cli/README.md` and `crc/cli/pairs.py` docstring. `crc/cli/README.md` and `crc/cli/routes/README.md` have no legacy handler/path/method/rung wording.
- **Check (4) Behaviour & locked files**: No CLI flag, output line, Slack message, Influx point, exception or exit code changed. No hash-locked safety test, `tests/legacy/*.txt` oracle or `tests/archtest/**` file was touched.
- **Check (5) Size limits & line counts**: Every touched non-test file is well within ARCH-004 limits (max 250 lines/file, max 60 lines/function). `crc/` Python line count: 19,279 before (`7b1f9d4`) -> 18,548 after (-731 lines; `crc.py` 102 -> 31 lines).
- **Check (6) Gates & test count**: After `pip install -r requirements.txt -r requirements-dev.txt`, full `make check` is green:
  - `ruff check .`: passed
  - `mypy`: passed (`Success: no issues found in 372 source files`)
  - `lint-imports`: 5 kept, 0 broken
  - `pytest -q -n auto`: `44415 passed in 54.96s`
  - `ARCH-008`: 44415 tests, floor 44415 (ARCH-001..011 and SAF-LOCK all green)

VERDICT: APPROVE


## Round 2 (Opus)

### Round 1 fixes and findings

- Round 1's only code change (dropping the unread report-label constants from
  `crc/cli/constants.py`, the `pairs.py` docstring) is correct: nothing in `crc/`, `tests/` or
  `crc.py` reads `DELETED`, `STOPPED`, `NICS`, `DISKS`, `VMS`, `IPS`, `KEYPAIRS`, `KMS`,
  `SPOT_INSTANCE_REQUEST` or `OPERATIONS`; the oracle keeps its own copies
  (`test_all_expansion_order_follows_crc_py` reads `CLOUDS` / `RESOURCES` from `load_legacy_crc`).
- The coder reported 7 `import-untyped` mypy errors (`google.cloud`, `azure.*`) as a VM issue.
  They come from a stale `.mypy_cache` written before the SDKs were installed: with the cache
  removed, mypy is clean on HEAD (372 files) and on `7b1f9d4` (379 files; the 7 extra are the 6
  deleted `crc/cli` modules and `tests/helpers_legacy_handlers.py`). Round 1's "mypy passed" holds.

### Independent checks

1. **Production paths kept.** `python3 crc.py --help` is byte-identical on `7b1f9d4` and HEAD.
   A dry-run `main()` of six production commands (aws vm with filter tags and Slack channel,
   azure vm, oci vm, aws snapshot, `--cloud all --resource all`, aws `--scan_tag`), with
   `tests.helpers_cli_routes.FakeCleaners` in place of the registry and no cloud calls, gives the
   same stdout, exit code or exception, and the same (cloud, kind, options) at each fake cleaner on
   both commits. `crc.cli.runner.CRC` keeps the constructor of the deleted `CRCBase` unchanged
   (GCP `project_id` check, BUG-030 conditional `influxdb_bucket` / `resource_suffix`) and
   `get_msg` unchanged; those are the only things `bridge.py`, `crc.notify` and the routes read
   (`slack_client`, `slack_channel`, `influxdb_client`, `influxdb_bucket`, `resource_suffix`,
   `dry_run`, `cloud`, `get_msg`). `crc.cli.scan.scan_instances_with_tag` / `_scan_vm` match the old
   `VmOps._get_vm` + `vm_arguments` + `scan_instances_with_tag` exactly (class choice, positional
   order per cloud, ValueError and NotImplementedError texts). The scan and every route table are
   imported and run by the suite (`test_cli_scan_parity.py` 961 cases green).
2. **Tests and oracle.** No `def test_` or test class was removed (diff of `tests/`). The scan
   parity test is not vacuous: `helpers_wa2_scan` patches `__init__` / `find_instances_with_tag`
   on the real VM classes in place, so the aliases imported by `scan.py` reach the fakes, and
   `routed_case` keeps the original tag arguments (`auto_clean=false`, `k=v` for the unsupported
   cases). `test_cli_main_parity.py`, `test_cli_route_fix.py` and `test_cli_logger.py` patch
   `crc.cli.scan.scan_instances_with_tag` with a forwarder to the fake `CRC`, so the same
   `scan_instances_with_tag` events are compared with the oracle; the real function is covered by
   `test_cli_scan_parity.py`. `test_crc_py_reexports_the_names_the_contract_needs` still checks
   every name `crc.py` exports on both modules. No oracle comparison was dropped.
3. **Deleted names gone.** `rg` for `resource_ops`, `adapter_args`, `report_calls`, `vm_ops`,
   `crc.cli.notify`, `crc.cli.base`, `CRCBase`, `VmOps`, `ResourceOps`, `NotifyMixin`,
   `vm_arguments`, `DiskArgs`, `report_result`, `helpers_legacy_handlers` over `crc/`, `tests/`,
   `crc.py`: only a local variable `vm_ops` in `tests/test_azure_vm_delete_helpers_parity.py` and
   the docstring of `tests/test_notify_crc_parity.py`, which names `crc.cli.notify` of `main` as
   the oracle side (history, correct). No `from crc import` / `crc.<re-export>` user outside
   `docs/archive`, and no Jenkinsfile in the repo (Jenkins runs `python crc.py <flags>`).
   `crc/cli/README.md` and `crc/cli/routes/README.md` have no HANDLERS / legacy handler / legacy
   path / CRC method / rung wording; the only "legacy" hits are the oracle path
   `tests/legacy/crc_main.txt`. The Public API lists `scan_instances_with_tag` and the `CRC` context.
4. **Behaviour and locked files.** No change to `tests/legacy/*`, `tests/archtest/**`, the five
   hash-locked test files, BUGS.md, CLEANUP_SAFETY.md, AGENTS.md, ARCHITECTURE.md, pyproject,
   Makefile or requirements. Help text, dry-run output, exit codes and exceptions identical (check 1);
   the bridge, routes and notifiers are not in the diff, so Slack text, Influx points and run-log
   lines are unchanged, and the parity suites that compare them with the oracle are green.
5. **Size.** No file in `crc/` or `crc.py` over 250 lines, no function over 60 (AST scan).
   `crc/` Python lines 19,279 before, 18,548 after; `crc.py` 102 -> 31.
6. **Gates** (after `pip install -r requirements.txt -r requirements-dev.txt`, clean mypy cache):
   ruff green; mypy `Success: no issues found in 372 source files`; lint-imports 5 kept, 0 broken;
   `make check` exit 0 in 1m00s wall: `pytest -q -n auto` **44415 passed in 53.21s**; ARCH-008
   44415 tests, floor 44415.

### Changes in round 2

None to code or tests; this section only.

## New bugs (round 2)

None found.

ROUND 2 VERDICT: APPROVE
