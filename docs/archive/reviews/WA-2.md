## Coding summary

Lane WA-2 (test only): the CLI parity cases for the `CRC` constructor and `scan_instances_with_tag` no longer depend on the module that defines the method.

- **Routed.** Nothing in production changed. The two case groups moved out of `tests/test_cli_crc_parity.py` (blocks removed: the `scan` and `scan_unsupported` entries of `OPERATIONS`, `test_constructor_matches_main`, and the `NewCRC` fallback in `compare` that looked up `NewCRC.scan_instances_with_tag.__module__` and patched adapter names in it; the fallback now runs `execute(NewCRC, [], case)` for the remaining `get_msg` cases, which WA-1 owns and which touch no adapter).
  - `tests/test_cli_scan_parity.py` (new): 480 `scan` + 480 `scan_unsupported` cases, same grid (5 clouds x full/empty x dry-run x 4 Slack modes x 6 Influx modes), same oracle (`load_legacy_crc()`), same compared fields (`result`, `attributes`, `adapters`, `reads`, `slack`, `influx`, `stdout`), plus one anti-vacuity test.
  - `tests/test_cli_runner_parity.py` (new): the 30 constructor cases (cloud, dry_run, notags, resource group, project id, Slack and Influx settings), same inputs and comparison.
  - `tests/helpers_wa2_scan.py` (new): `execute_scan` drives `crc.cli.runner.CRC`.
- **Adapters deleted or shimmed.** None deleted, none added. `tests/helpers_wa2_scan.py` patches the SDK-facing VM classes through their own modules: the `VM` attribute of `crc.aws.vm`, `crc.azu.vm`, `crc.gcp.vm`, `crc.oci.vm` (a seam looked up at call time) and, in place on the class object, its `__init__` and `find_instances_with_tag` (so code that already bound the class, as `vm_ops.py` does today and `scan.py` may later, is reached whichever module holds it). The class `__module__`/`__qualname__` are set to the oracle fake's so the `NotImplementedError` text (`type(vm)`) matches; `__qualname__` is restored explicitly. Only AWS defines `find_instances_with_tag`; the other clouds' real classes lack it, so the patch gives them the fake's behaviour (or removes the method for `scan_supported=False`).
- **How the CLI parity tests reach the cleaners.** Constructor cases need no patching. Scan cases build `CRC` as `crc.cli.runner.CRC`; `_get_vm`/`vm_arguments` construct the (patched) VM class of the cloud, and the call lands on the fake `find_instances_with_tag`. No test imports `crc.cli.vm_ops` or patches a name in it (`grep -rn "cli.vm_ops" tests` is empty; the only `vm_ops` hit is a local variable in `tests/test_azure_vm_delete_helpers_parity.py`). When `scan_instances_with_tag` moves to `crc/cli/scan.py` these cases keep working unchanged, provided `CRC` still builds the VM class with `vm_arguments`' argument order.
- **Test count.** Before 44411, after 44412 (+1: `test_scan_cases_are_not_vacuous`); 960 scan and 30 constructor cases moved unchanged. `tests/archtest/test_count_floor.txt` raised to 44412 with `make raise-test-floor`. `make check` green (ruff, mypy, import-linter, pytest, archtests).
- **Owner actions.** None. If WA-1 edits the same `compare` fallback in `tests/test_cli_crc_parity.py` the integrator may need to resolve a textual conflict on the three lines I changed; the floor file may conflict with other lanes (take the higher value).
- **Left over.** Nothing. Production `CRC.scan_instances_with_tag` and `crc/cli/vm_ops.py` are untouched (later lane).

## New bugs

None.

## Round 1 (Argon)

Scope reviewed: `git diff fc6fa4d..HEAD` on branch `wa/2` (lane `WA-2`).

### Findings & fixes made in review

1. **Reverted unowned `tests/README.md` edit**:
   - Commit `b6d06cf` modified `tests/README.md`, which is outside the lane's owned files (`tests/test_cli_crc_parity.py`, `tests/test_cli_scan_parity.py`, `tests/test_cli_runner_parity.py`, `tests/helpers_wa2_*.py`) and risks merge conflicts with parallel lanes. Reverted `tests/README.md` to `fc6fa4d`.
2. **Strengthened `test_constructor_matches_main` client attribute comparison** (`tests/test_cli_runner_parity.py:27`):
   - `execute()` filters `"client" not in k` out of `attributes`, so `case.act = lambda c: None` never compared `c.slack_client` or `c.influxdb_client` on `NewCRC`. Updated `case.act` to `lambda c: (type(c.slack_client).__name__, type(c.influxdb_client).__name__)` so both client attributes are compared against `oracle.CRC` via `result`.

### Checklist verification

1. **No legacy CLI imports or calls (`grep`)**:
   - Grepped `tests/` for `vm_ops`, `resource_ops`, `adapter_args`, `report_calls`, and `__module__`: zero tests import `crc.cli.vm_ops` or patch a name in it.
   - `tests/helpers_wa2_scan.py` patches the SDK-facing VM classes through `crc.aws.vm`, `crc.azu.vm`, `crc.gcp.vm`, and `crc.oci.vm` (both module attribute `VM` and in-place on the class object), independent of which module defines `CRC.scan_instances_with_tag`. Verified by replacing `CRC.scan_instances_with_tag` with a function whose `__module__` is `crc.cli.scan` and poisoning all `crc.cli.vm_ops.VmOps` methods: all 991 tests in `test_cli_scan_parity.py` and `test_cli_runner_parity.py` pass.
2. **Parity comparisons, case counts, and assertions preserved**:
   - All 960 scan cases (`480` `scan` + `480` `scan_unsupported`) in `tests/test_cli_scan_parity.py` and all 30 constructor cases in `tests/test_cli_runner_parity.py` compare every recorded key (`result`, `attributes`, `adapters`, `reads`, `slack`, `influx`, `stdout`) against `tests.helpers_oracle.load_legacy_crc()`.
   - In `tests/test_cli_crc_parity.py`, only the moved `scan`/`scan_unsupported` operations, `test_constructor_matches_main`, and the `sys.modules[NewCRC.scan_instances_with_tag.__module__]` patch in `compare` were touched; all `get_msg` / Slack / Influx / ping cases owned by lane WA-1 are untouched.
   - Total suite count rose by 1 (`44411` -> `44412`) from adding `test_scan_cases_are_not_vacuous` in `tests/test_cli_scan_parity.py`; `tests/archtest/test_count_floor.txt` updated to `44412`.
3. **Non-vacuous (deliberate mutation checks, reverted without committing)**:
   - Mutating `crc.core.report.get_msg` Slack text (`"the following"` -> `"these"`) fails `tests/test_cli_crc_parity.py` immediately.
   - Mutating `CRC.scan_instances_with_tag` to pass a wrong `tag_value` fails `tests/test_cli_scan_parity.py` immediately.
   - Mutating `CRCBase.__init__` to set `self.slack_client = None` fails `tests/test_cli_runner_parity.py` immediately.
4. **Owned files only**:
   - `git diff --stat fc6fa4d..HEAD` touches only `tests/test_cli_crc_parity.py`, `tests/test_cli_scan_parity.py`, `tests/test_cli_runner_parity.py`, `tests/helpers_wa2_scan.py`, `tests/archtest/test_count_floor.txt`, and `docs/archive/reviews/WA-2.md`. No production files or other tests touched.

### Gates & test count

Ran `pip install -r requirements.txt -r requirements-dev.txt && make check`:
- `python3 -m ruff check .`: passed
- `python3 -m mypy`: passed (377 source files)
- `lint-imports`: passed (5 contracts kept, 0 broken)
- `python3 -m pytest -q -n auto`: **44412 passed in 55.70s** (`make check` total 74.9s; `test_cli_scan_parity.py` + `test_cli_runner_parity.py`: **991 passed in 3.09s**)
- `python3 -m tests.archtest.test_count check`: `ARCH-008: 44412 tests, floor 44412`

VERDICT: APPROVE


## Round 2 (Opus)

Scope: `git diff fc6fa4d..HEAD` on `wa/2` (commits `b6d06cf`, `69544b0`).

### Round 1 fixes and findings re-checked

- Reverting `tests/README.md` (unowned) is correct; it is absent from the diff now.
- The constructor `act` now returns `(type(c.slack_client).__name__, type(c.influxdb_client).__name__)`. This adds a comparison and drops none: before, `execute` filtered the client attributes out of `attributes` and `result` was always `("ok", "None")`. Both sides build the same fake clients, so it compares only whether the clients are stored, which is what main does.
- Round 1's claims (counts, grep, gates) reproduce. See below.

### Independent checks

1. **No legacy CLI code in owned files.** `grep -rn "vm_ops\|cli.adapter_args\|report_calls\|resource_ops"` over `tests/test_cli_crc_parity.py`, `tests/test_cli_scan_parity.py`, `tests/test_cli_runner_parity.py` and `tests/helpers_wa2_*.py` is empty. `grep -rln "cli.vm_ops" tests/` is also empty. The `sys.modules[NewCRC.scan_instances_with_tag.__module__]` lookup and the `import sys` are gone. `helpers_wa2_scan` patches only `crc.{aws,azu,gcp,oci}.vm`, both the `VM` module attribute and the class object in place, and it restores `__qualname__`.
2. **Comparisons preserved.** `scan_cases` reproduces `operation_cases()` for `scan` and `scan_unsupported` exactly: 5 clouds x `DATA` x (2 dry x 4 Slack x 6 Influx), with the same names, `routed=` and `scan_supported=False` override. Collection at `fc6fa4d`: `tests/test_cli_crc_parity.py` has 11744 tests, 990 of which match scan/constructor. At HEAD the three files have 11745 (960 scan + 30 constructor + 1 new non-vacuity test). The old side is still `execute(oracle.CRC, [oracle], case)` with `load_legacy_crc()`. The assertion is still `for key in old: new[key] == old[key]` over all seven recorded fields, and nothing is derived from the routed side. The moved blocks had no BUG-NNN divergences. The WA-1 blocks (get_msg/Slack/Influx/ping) are untouched; the only shared edit is the `spec is None` fallback, now `execute(NewCRC, [], case)` with `assert case.routed is None`.
3. **Mutations** (local, reverted with `git checkout`, not committed):
   - `crc/cli/vm_ops.py` `_get_vm`: Azure builds `GCP_VM`. Result: `test_cli_scan_parity.py` has 192 failed.
   - `crc/cli/vm_ops.py` `scan_instances_with_tag` returns a constant instead of calling `find_instances_with_tag`. Result: 385 failed.
   - `crc/cli/base.py`: `dry_run` inverted for OCI. Result: scan plus runner files have 198 failed (192 scan + 6 constructor).
   - `crc/core/report.py` `get_msg` dry-run text `Will be` -> `Would be` (routed Slack text). Result: `test_cli_crc_parity.py` has 2196 failed.
   - Note: changing the header text of `notify_cleanup` in `crc/notify/slack.py` failed nothing in `test_cli_crc_parity.py`. That function is not on the routed `CRC` path (`crc/cli/scan.py` imports `crc.notifications.notify_cleanup`). This is coverage outside this lane, not a regression.
4. **Owned files only.** The diff touches `tests/test_cli_crc_parity.py`, `tests/test_cli_scan_parity.py`, `tests/test_cli_runner_parity.py`, `tests/helpers_wa2_scan.py`, `tests/archtest/test_count_floor.txt` (44411 -> 44412) and this review. No production code.
6. **Gates.** `pip install -r requirements.txt -r requirements-dev.txt && make check`: ruff passed; mypy passed (377 files); lint-imports passed; pytest **44412 passed in 52.60s**; `ARCH-008: 44412 tests, floor 44412`. `make check` wall time 72s.

### Minor (not blocking, not fixed)

- `tests/helpers_wa2_scan.py:151`: `patch.delattr(real, "find_instances_with_tag")` only deletes from the class's own `__dict__`. If a later lane moves the method to a base class or mixin, `scan_unsupported` setup raises `AttributeError` instead of comparing. Today only `crc/aws/vm/adapter.py:130` defines it, on the class itself.

## New bugs

None.

ROUND 2 VERDICT: APPROVE
