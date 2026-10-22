# R2b review

Scope reviewed: `git diff 558c032..792f947` (commit `792f947`, R2b core selection
rules in `crc/core/selection.py`, orchestration engine in `crc/core/engine.py`,
and tests in `tests/test_core_selection.py`, `tests/test_core_engine.py`)
against `crc.py`, `AGENTS.md`, `ARCHITECTURE.md` and
`.cursor/skills/crc-python-refactor/REVIEW_CHECKLIST.md`.

## 1. Selection parity with `crc.py main()` (`crc/core/selection.py`, `tests/test_core_selection.py`)

Checked every branch, expansion order, opt-in gate, warning and error message in
`crc/core/selection.py` against `crc.py`:

- **Cloud and resource expansion & order** (`crc/core/selection.py:12-14, 104-105` vs `crc.py:39-40, 1000-1023, 1397-1400`):
  - `CLOUDS = ("aws", "azure", "gcp", "oci")` matches `crc.py:39`.
  - `RESOURCES = ("disk", "ip", "keypair", "vm", "kms", "nic", "snapshot")` matches `crc.py:40` (`--resource all` expansion).
  - `EXTRA_RESOURCES = ("vpc", "spot_instance_requests", "spot_instances")` matches the remaining `get_argparser()` `--resource` choices (`crc.py:1016, 1019-1020`) that are excluded from `RESOURCES`.
  - Outer loop iterates `clouds`, inner loop iterates `kinds` (`crc/core/selection.py:108-109` vs `crc.py:1532, 1544`).
- **Validation order & verbatim error messages** (`crc/core/selection.py:62-75` vs `crc.py:1363-1385`):
  1. `operation == Operation.STOP and resource != "vm"` -> `"Stop is supported only for vm resource"` (`crc.py:1363-1364`).
  2. `resource == "all" and cloud != "all"` -> `"All Resources cleanup is supported only with all Clouds. Format: --cloud all --resources all"` (`crc.py:1367-1370`).
  3. `options.slack_notify_users and not options.slack_user_label` -> `"--slack_user_label is mandatory when passing --slack_notify_user"` (`crc.py:1372-1374`).
  4. `resource in ("kms", "all")` without `options.kms_key_description` -> `"Key description string is required for deleting KMS keys."` (`crc.py:1377-1381`).
  5. `resource in ("kms", "all")` without `options.kms_user` -> `"Jenkins user ARN is reuired for deleting associated KMS keys."` (`crc.py:1382-1385`, keeping the `"reuired"` typo verbatim).
- **AWS EBS opt-in circuit breaker** (`crc/core/selection.py:16-21, 103, 110-111` vs `crc.py:1394, 1545-1552`):
  - `aws_disk_opt_in = cloud == "aws" and resource == "disk"`.
  - When `each_cloud == "aws"` and `kind == "disk"` and `not aws_disk_opt_in` (`--cloud all --resource all` or `--cloud all --resource disk`), raises `ValueError` with the exact message of `crc.py:1547-1552` on the first pair `("aws", "disk")`, so no other AWS resource and no other cloud's disk is selected or executed.
- **`spot_instance_requests` and `spot_instances` per-cloud rules** (`crc/core/selection.py:22-34, 78-87` vs `crc.py:1598-1628`):
  - `spot_instance_requests` on `azure`, `gcp`, `oci` (explicit or under `--cloud all`) is skipped with warning `"spot_instance_requests is AWS-only; skipped for {cloud} (use --resource spot_instances)"` (`crc.py:1599-1605`); only `("aws", "spot_instance_requests")` is selected.
  - `spot_instances` on `aws` (explicit or under `--cloud all`) is skipped with warning `"For AWS use --resource spot_instance_requests (cancel request before terminate). Skipping spot_instances."` (`crc.py:1610-1615`).
  - `spot_instances` on `gcp` and `oci` when `cloud == "all"` is skipped with warning `"GCP/OCI spot_instances require explicit --cloud {cloud} (not --cloud all); skipped. Azure spot_instances still runs under --cloud all for backward compatibility."` (`crc.py:1616-1623`).
  - `spot_instances` on `azure` runs both when `--cloud azure` and under `--cloud all`; `spot_instances` on `gcp` and `oci` runs when `--cloud gcp` or `--cloud oci` is explicit.
- **`snapshot`, `keypair`, and `nic` per-cloud behaviour in `main()`** (`crc/core/selection.py:108-117` vs `crc.py:799-800, 823-824, 1571-1574, 1629-1643`):
  - `crc.py main()` does not skip `snapshot`, `keypair`, or `nic` for any cloud in the `main()` loop: it dispatches `crc.delete_keypairs(...)`, `crc.delete_nic(...)`, and `Snapshot(...).delete()` for every cloud in `clouds` (`CRC.delete_keypairs` and `CRC.delete_nic` raise `ValueError` inside the method when `self.cloud` is not `"aws"` / `"azure"`, while `Snapshot` runs AWS snapshot deletion unconditionally). `build_selection()` matches `main()` by emitting `(each_cloud, kind)` for each cloud in `clouds`.
- Two test issues in `tests/test_core_selection.py` (vacuous `calls == []` assertion on EBS breaker and copied warning/pair literals instead of comparing against `crc.py`) were fixed as BLOCKING below.

## 2. Engine orchestration (`crc/core/engine.py`, `tests/test_core_engine.py`)

Checked all four requirements on `crc/core/engine.py`:

- **`apply()` is never called in dry run** (`crc/core/engine.py:99-105`):
  - `result = None if context.dry_run else cleaner.apply(plan, context)`.
  - When `context.dry_run` is `True`, `cleaner.apply` is not called, `RunOutcome.result` is `None`, and `Report.from_plan(...)` is passed to notifiers.
- **Cleaner exceptions propagate (no catch-all)** (`crc/core/engine.py:94-100`):
  - No `try/except` wraps `registry.get(cloud, kind)(context)`, `cleaner.supports(context.operation)`, `cleaner.plan(context)`, or `cleaner.apply(plan, context)`. Any exception aborts the run before notifiers or subsequent cleaners execute.
- **Only `fail_open` notifiers (Influx) are swallowed and logged** (`crc/core/engine.py:77-84`):
  - `_notify` catches `Exception as e`, re-raises when `not notifier.fail_open`, and logs `logger.error("Notifier %s failed for %s report: %s", notifier.name, report.cloud, e)` when `notifier.fail_open` is `True`.
- **Notifier order matches `crc.py`** (`crc/core/engine.py:77-84, 106` vs `crc.py:561-567, 599-616, 630-646, 657-673, 725-730, 750-754, 779-783, 805-809, 829-833, 920-924, 978-982`):
  - After each cleaner completes, `_notify` runs notifiers sequentially before advancing to the next `(cloud, kind)` pair. Missing direct comparison tests against `crc.py`'s per-cleaner `cleaner -> slack -> influx` order and Influx fail-open behaviour were added as BLOCKING below.

## 3. Bug confirmation table (`docs/refactor/STATUS.md`)

| # | Bug / quirk in `docs/refactor/STATUS.md` | `file:line` | Status | Real-world effect |
|---|------------------------------------------|-------------|--------|-------------------|
| 1 | `--cloud all --resource all` and `--cloud all --resource disk` can never run because `aws` is the first cloud and `disk` the first resource, so the EBS breaker raises on the first pair; `--cloud aws --resource all` fails earlier at `crc.py:1367` | `crc.py:39-40`, `1010`, `1367-1370`, `1394`, `1532`, `1544-1552` | Confirmed | `--resource all` (also the `argparse` default at `:1010`) is completely unusable: with a single cloud it raises `ValueError` at `:1367`, and with `--cloud all` (after KMS flags are supplied) it hits `("aws", "disk")` on iteration 1 and raises the EBS opt-in `ValueError` at `:1547` before any resource on any cloud is cleaned. Likewise, `--cloud all --resource disk` always aborts on `aws` before reaching `azure`, `gcp`, or `oci`. |
| 2 | `--resource snapshot` always runs the AWS `Snapshot` class with hard-coded `age_days=90` for every cloud (`--cloud azure --resource snapshot` touches AWS), and sends nothing to Slack or Influx, only prints | `crc.py:33`, `87-88`, `1629-1643`; `crc/aws/snapshot.py:13-18`, `58-90` | Confirmed | Running `--cloud azure --resource snapshot` or `--cloud oci --resource snapshot` (or `--cloud gcp --project_id ... --resource snapshot`) deletes AWS EC2 snapshots older than 90 days across all AWS regions instead of acting on or rejecting the target cloud. Under `--cloud all --resource snapshot` (with `--project_id`), AWS snapshot deletion runs four times in a row while printing `azure`/`gcp`/`oci` in stdout and sending no Slack or InfluxDB telemetry. |
| 3 | `--cloud all --resource keypair` deletes AWS keypairs, then raises `ValueError("Keypair operation is only supported on AWS.")` on Azure; same shape for `nic` on non-Azure clouds | `crc.py:39`, `799-800`, `823-824`, `1532`, `1571-1574` | Confirmed | Because `main()` does not validate or skip unsupported clouds for `keypair` or `nic` up front, `--cloud all --resource keypair` mutates AWS state (deletes AWS keypairs and sends AWS notifications) on the first loop iteration and then crashes with `ValueError` on the second iteration (`azure`). `--cloud all --resource nic` crashes immediately on the first iteration (`aws`) at `:824` before ever reaching `azure`. |
| 4 | KMS description and user are required for `--resource kms` on every cloud, including clouds that have no KMS cleaner; error text has typo `"reuired"` | `crc.py:963-964`, `1377-1385` | Confirmed | Running `--resource kms` on `azure`, `gcp`, or `oci` still demands `--kms_key_description` and `--kms_user` in `main()` before `CRC.delete_kms` can reject the non-AWS cloud at `:963-964`, and omitting `--kms_user` raises `ValueError("Jenkins user ARN is reuired for deleting associated KMS keys.")`. |
| 5 | Error message says `--resources all` but the CLI flag is `--resource` | `crc.py:1008-1010`, `1366-1371` | Confirmed | Running `--cloud <single-cloud>` with `--resource all` (or omitting `-r`/`--resource`, which defaults to `"all"`) raises `ValueError` telling the user `Format: --cloud all --resources all`, naming a non-existent `--resources` flag. |
| 6 | `MAX_AGE` is set in `os.environ` and only deleted at the end of `main()`, not in a `finally`, so any exception (including the EBS breaker) leaves it set | `crc.py:1422-1424`, `1526`, `1529`, `1645-1647` | Confirmed | Any exception raised after line 1423 (or `sys.exit(0)` in `--scan_tag` at `:1526, :1529`) skips `del os.environ["MAX_AGE"]` at `:1646`, leaking `MAX_AGE` in the process environment where `is_old` reads it and can shorten retention floors (`SAF-010`). |
| 7 | `crc.py:1439`: `clouds == "all"` is always `False` there (`clouds` is already a list), so the scan uses `[c for c in clouds]` | `crc.py:1397`, `1439` | Confirmed | `clouds` is converted to `list[str]` at `:1397` (`CLOUDS if clouds == "all" else [clouds]`), so `clouds == "all"` at `:1439` is always `False`. The true branch is dead code, though `[c for c in clouds]` evaluates to the same list as `CLOUDS` when `--cloud all` was passed. |
| 8 | `--operation_type stop` with `--resource vm` is the only stop path; an operation value other than `delete` or `stop` would silently do nothing for `vm` (unreachable via `argparse` `choices`) | `crc.py:1045-1046`, `1363-1364`, `1575-1584` | Confirmed | For `resource == "vm"`, `:1576-1584` uses `if operation_type == "delete": ... elif operation_type == "stop": ...` with no `else`, so any other `operation_type` value silently does nothing for `vm` (and for non-VM resources `:1363` only guards `operation_type == "stop"`, so another value would fall through to `delete_*()`). Unreachable from CLI because `argparse` enforces `choices=["delete", "stop"]` at `:1046`. |

## BLOCKING (fixed)

1. **Vacuous assertion in `test_ebs_breaker_selects_nothing_before_raising` and copied literals in `tests/test_core_selection.py`** (`tests/test_core_selection.py:53-86, 155-295`):
   - In `test_ebs_breaker_selects_nothing_before_raising`, `calls: list[tuple[str, str]] = []` was assigned via `calls = run_crc_main(...)` inside `with pytest.raises(ValueError):`. Because `run_crc_main` raised `ValueError`, the assignment never executed and `assert calls == []` was always vacuous.
   - `FakeSnapshot.delete` recorded `calls[-1][0] if calls else "?"`, requiring a special-case `if resource == "snapshot":` in `test_build_selection_matches_crc_main`, and `test_warning_texts_match_crc_py` / `test_single_pair` compared against copied literals instead of `crc.py main()`.
   - Fixed `run_crc_main` to record calls into `LAST_RECORDED_CALLS` (asserted empty on every raising test case, including both `("all", "all")` and `("all", "disk")` EBS circuit-breaker cases) and to capture `current_cloud` in `FakeCRC.__init__` so `FakeSnapshot.delete` records `(current_cloud, "snapshot")`. Replaced the copied warning literals in `test_warning_texts_match_crc_py` with direct comparisons against `crc.py main()`'s `caplog` warnings, updated `test_single_pair` to compare against `run_crc_main` across `ip`, `vm`, `nic`, `keypair`, `snapshot`, and `vpc`, and added `test_exhaustive_grid_matches_crc_main` covering all `5 clouds x 11 resources x 2 operations = 110` combinations against `crc.py main()`.
2. **Missing direct `crc.py` notifier order / fail-open comparison and cleaner `init` exception test in `tests/test_core_engine.py`** (`tests/test_core_engine.py:25-60, 155-296`):
   - `test_cleaner_exceptions_propagate_and_stop_the_run` tested `plan` and `apply` failures but not cleaner factory (`init`) failures.
   - `test_fail_open_notifier_error_is_logged_and_swallowed` only tested `[influx, slack]` rather than `crc.py`'s `[slack, influx]` order, and no test compared `engine.run` against `crc.py`'s `CRC` methods to verify that `cleaner -> slack -> influx` order and Influx fail-open error swallowing match `crc.py`.
   - Added `"init"` to `test_cleaner_exceptions_propagate_and_stop_the_run`, parametrised `test_fail_open_notifier_error_is_logged_and_swallowed` over both `[slack, influx]` and `[influx, slack]`, and added `test_notifier_order_and_influx_fail_open_match_crc_py` comparing `engine.run` directly against `crc.py`'s `CRC` methods (`delete_vm`, `stop_vm`, `delete_disks`, `delete_ip`, `delete_keypairs`, `delete_nic`, `delete_kms`, `delete_spot_instance_requests`, `delete_spot_instances`).

## NIT (left)

- `crc/core/engine.py:34-45`: `KIND_LABELS` includes `"vpc": "VPC"` and `"snapshot": SNAPSHOT` so `engine.run` can build a `Report` for every selectable kind, whereas `crc.py` sends no Slack or InfluxDB notifications for `vpc` or `snapshot`; the notifier/CLI wiring rungs must preserve that skip.

## 4. Scope and ratchet

- Only `crc/core/README.md`, `crc/core/__init__.py`, `crc/core/engine.py`, `crc/core/selection.py`, `docs/refactor/STATUS.md`, `docs/refactor/reviews/R2b.md`, `tests/test_core_engine.py`, and `tests/test_core_selection.py` changed.
- No module outside `crc/core`, `docs` and `tests` changed; `pyproject.toml` legacy ratchet lists and `tests/archtest/` baselines and safety locks are untouched.

## 5. Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` (`crc`, `tests`) | Success: no issues found in 71 source files |
| `lint-imports` | 3 kept, 0 broken ("Cloud packages are independent", "Core never imports cloud packages or services", "Core never imports cloud or telemetry SDKs") |
| `pytest -q` | 984 passed in 3.16s (767 pre-R2b + 217 R2b tests) |

Test count: 767 on `558c032`, 849 on `792f947`, 984 after review fixes.

VERDICT: APPROVE
