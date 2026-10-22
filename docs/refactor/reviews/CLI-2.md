## Coding summary

Rung CLI-2: the `CRC` class and the `_section` helper moved from `crc.py` into `crc/cli/`, no behaviour change.

- **Summary.** `crc/cli/` now holds the class split by responsibility, each file under 400 lines, ruff clean and mypy strict (`crc.cli` is not in any exemption list): `constants.py` (19 lines: `CLOUDS`, `RESOURCES`, the report labels, the operation map), `base.py` (62: `CRCBase`, the unchanged constructor and attributes), `notify.py` (about 200: `NotifyMixin` with `_section`, `slack_lookup_user_by_email`, `get_user_groups_list`, `_slack_notifier`, `_slack_send`, `ping_on_slack`, `get_msg`, every `notify_*`, `write_influxdb`), `vm_ops.py` (about 300: `VmOps` with `_delete_vm`, `_get_vm`, `scan_instances_with_tag`, `delete_vm`, `delete_spot_instances`, `delete_azure_spot_instances`, `delete_spot_instance_requests`, `stop_vm`; VM and spot adapter imports), `resource_ops.py` (about 310: `ResourceOps` with `_get_ip`, `delete_ip`, `delete_keypairs`, `delete_nic`, `delete_disks`, `delete_vpc`, `delete_kms`; the other adapter imports) and `runner.py` (`class CRC(VmOps, ResourceOps)`). Class name, constructor signature, every public and private method name and attribute, and the call order (adapter constructor, `delete()` / `stop()`, Slack, Influx; `delete_disks` reports before it re-raises) are as on `main`. Helpers added to satisfy the ruff limits only: `_send_spot_disk_message`, `_report_spot_delete`, `_delete_azure_spot`, `_delete_gcp_spot`, `_delete_oci_spot` (the three spot branches of `delete_spot_instances`), `_make_disk` and the `_DiskArgs` dataclass (the per-cloud disk constructor of `delete_disks`); attribute reads and arguments keep their order. `get_msg` was restored to the exact `main` f-strings (the delegating version differed for a non-list value and for an unknown operation). The dead first `notify_deleted_nic_via_slack` was dropped (ruff F811; the second always won on main). `crc.py` keeps `main()`, `if __name__ == "__main__": main()`, and re-exports `CRC`, the 20 adapter names, `Snapshot`, the 11 constants, `Point`, `WebClient`, `InfluxDBClient`, `get_argparser`, the validators and `notify_cleanup`; `main()` still looks `CRC` and `Snapshot` up in the `crc.py` globals. `crc.py` stays on the ratchet (`pyproject.toml` not edited). `crc/cli/README.md` (four ARCH-002 sections, new Public API names) and `crc/README.md` (cli entry, `crc.py` description) updated.
- **Tests.** Before: 12562 passed. After: 24498 passed (+11936), `make check` (ruff, mypy strict, import-linter, pytest incl. archtests) green; the environment ran Python 3.12 (3.11 not installed). New: `tests/test_cli_crc_parity.py`: the oracle `tests/legacy/crc_main.txt` CRC and `crc.cli.runner.CRC` run the same call on recording fake adapters (patched in each module that looks them up: the oracle module; `crc.cli.vm_ops` and `crc.cli.resource_ops`), a fake Slack client and a fake Influx client, and the ordered adapter constructor args and method calls, the attribute reads, the Slack calls and payloads, the Influx calls (points as line protocol), stdout, the constructor attributes and the return value or raised error are compared. Tables: 52 (operation, cloud) rows (`delete_vm`, `stop_vm`, `scan_instances_with_tag` with and without `find_instances_with_tag`, `delete_spot_instances`, the Azure alias, `delete_spot_instance_requests`, `delete_ip`, `delete_keypairs`, `delete_nic`, `delete_disks` incl. a raising `delete()`, `delete_vpc`, `delete_kms`, each on aws, azure, gcp, oci and an unknown cloud where it applies) x empty and non-empty results x dry run on/off x Slack off / on / post failing / client without channel x Influx off / on / with suffix / `write_api` failing / `write` failing / no `influxdb_conn`; 46 direct-method rows (`get_msg`, `ping_on_slack` incl. `slack_notify_users`-style owner dicts, user lookup, user groups, every `notify_*`, `write_influxdb`, disk result shapes list, empty, dict, tuple, None, str) x groups-list failing / `conversations_open` failing x the same Slack and Influx grid; constructor and disk-shape tables. About 11,900 cases.
- **Non-vacuity.** One-line changes made locally in `crc/cli/`, each failed the parity test and was reverted: `vm.stop()` called twice (384 failures), the Influx write of `volumes_to_delete` removed (80), AWS `detach_age=args.age` (264), `type(...) is list` changed to `tuple` in `notify_deleted_disk_via_slack` (1368), the KMS error text reworded (96), the Azure spot NIC Influx write removed (160), `(s)` changed to `s` in `get_msg` (288).
- **Monkeypatch retargets.** `tests/test_core_report.py` lines 186-188: `monkeypatch.setattr(crc_cli, "AZU_SpotVM" / "GCP_SpotVM" / "OCI_SpotVM", ...)` now patch `vm_ops` (plus the import `from crc.cli import vm_ops`). `tests/test_core_engine.py` line 275: `monkeypatch.setattr(crc_cli, attr, FakeResource)` now patches `vm_ops if hasattr(vm_ops, attr) else resource_ops` (plus the import `from crc.cli import resource_ops, vm_ops`). No assertion, case table or expected value changed. `tests/test_core_selection.py` and `tests/test_notify_parity.py` are unchanged (`main`, `get_argparser`, `CRC`, `Snapshot`, `CLOUDS`, `RESOURCES` stay in the `crc.py` globals).
- **Open issues.**
  - 3 rows in `docs/refactor/bugs/pending/CLI-2.md`. One is a real difference from `main` found by the parity test: `ping_on_slack` with an `operation_type` other than `Deleted` / `Stopped` raises `KeyError` (the delegating notifier in `crc/notify`, outside this lane) where `main` formats the string; unreachable from the CLI, so the case is not in the parity table. The other two are an unreachable GCP `project_id` check and the dropped dead `notify_deleted_nic_via_slack`.
  - Adapter parameters of `crc.cli` are typed `Any` where the adapters declare narrower types than the unvalidated CLI values (`age` as `int` in the GCP adapters, `None` where `str` is declared); the calls are as on `main` (`BUG-197` shape).
  - Not edited (outside the lane): `tests/legacy/README.md` and `tests/README.md` should list `crc_main.txt` / `test_cli_crc_parity.py` (also noted in CLI-1).
- **Core changes needed.** None (the `ping_on_slack` unknown-operation difference would need a change in `crc/notify/slack.py` in a later rung).

## Round 1 (Argon)

Reviewed `git diff fe63352..HEAD` against the CLI-2 task, the CLI contract and `REVIEW_CHECKLIST.md`.

### Findings

- **NIT (left):** `crc/cli/__init__.py` does not re-export `CRC`, `CRCBase`, `NotifyMixin`, `VmOps` or `ResourceOps` in `__all__` (though `crc/cli/README.md` documents them under `## Public API` and `crc.py` imports `CRC` from `crc.cli.runner`). `ARCH-002` checks the union of `__all__` and public top-level definitions across `crc/cli/*.py` and passes.
- **NIT (left):** `crc/cli/notify.py:78-89` delegates `ping_on_slack` to `SlackNotifier.ping_owners` via `OPERATIONS[operation_type]`, which raises `KeyError` on an unknown `operation_type` string instead of formatting it like `main:crc.py:248-251` (`get_msg` in `crc/cli/notify.py:91-115` was restored to the exact `main` f-strings so it accepts arbitrary strings). Unreachable from the CLI (the only caller passes `DELETED`), introduced in R3 when `crc/notify` was wired into `crc.py`, and recorded in `docs/refactor/bugs/pending/CLI-2.md:8`.
- **NIT (left):** `tests/legacy/README.md` and `tests/README.md` do not yet list `crc_main.txt` and `test_cli_crc_parity.py` (outside this lane's editable files, noted under Open issues).

### Verification

1. **CLI contract & behaviour parity (`crc.py`, `crc/cli/base.py`, `crc/cli/constants.py`, `crc/cli/notify.py`, `crc/cli/vm_ops.py`, `crc/cli/resource_ops.py`, `crc/cli/runner.py`):**
   - `CRC` class name, 9-parameter `__init__` signature and defaults, attribute names (`cloud`, `dry_run`, `project_id`, `notags`, `slack_client`, `influxdb_client`, `slack_channel`, `resource_group`, and conditional `influxdb_bucket` / `resource_suffix`), and every public/private method from `main:crc.py` are preserved.
   - Adapter construction arguments (positional vs keyword for `GCP_Disk`), method calls (`delete()`, `delete(instance_state)`, `stop()`, `find_instances_with_tag`), Slack notification order (including Azure VM NIC follow-up and Spot VM leftover disk messages), InfluxDB write order, and `delete_disks` reporting before re-raising `delete_error` match `git show main:crc.py`.
   - `crc.py` retains `main()`, `if __name__ == "__main__": main()`, and re-exports `CRC`, `_section` (in `crc.cli.notify`), the 20 adapter classes, `Snapshot`, the 11 constants (`CLOUDS`, `RESOURCES`, `DELETED`, `STOPPED`, `NICS`, `DISKS`, `VMS`, `IPS`, `KEYPAIRS`, `KMS`, `SPOT_INSTANCE_REQUEST`), `Point`, `WebClient`, `InfluxDBClient`, `get_argparser`, the validators and `notify_cleanup`. `main()` continues to look up `CRC` and `Snapshot` in `crc.py` globals.
2. **Oracle & non-vacuity (`tests/legacy/crc_main.txt`, `tests/test_cli_crc_parity.py`):**
   - `git show main:crc.py | cmp - tests/legacy/crc_main.txt` is byte-identical.
   - `tests/test_cli_crc_parity.py` loads `tests/legacy/crc_main.txt` via `importlib.util.spec_from_file_location("legacy_crc_main_crc", ...)` with `SourceFileLoader` and runs both `oracle.CRC` and `crc.cli.runner.CRC` across 11,936 parametrized cases covering all operations, clouds, empty/non-empty results, dry-run modes, Slack modes (`off`, `on`, `post_fail`, `no_channel`), Influx modes (`off`, `on`, `on_suffix`, `api_error`, `write_error`, `no_conn`), direct notify/lookup/ping methods, constructor validation, and disk result shapes.
   - Confirmed non-vacuity locally by duplicating `vm.stop()` in `crc/cli/vm_ops.py:293`, verifying `pytest tests/test_cli_crc_parity.py -k "stop_vm"` failed immediately on adapter call log mismatch, and reverting.
3. **Existing tests (`tests/test_core_report.py`, `tests/test_core_engine.py`, `tests/test_core_selection.py`, `tests/test_notify_parity.py`):**
   - `tests/test_core_report.py:13,186-188` only retargets `AZU_SpotVM`, `GCP_SpotVM`, `OCI_SpotVM` monkeypatches from `crc_cli` to `vm_ops`.
   - `tests/test_core_engine.py:14,275` only retargets the adapter monkeypatch from `crc_cli` to `vm_ops if hasattr(vm_ops, attr) else resource_ops`.
   - `tests/test_core_selection.py` and `tests/test_notify_parity.py` and all other existing tests are completely untouched. No assertion, case table or expected value was changed.
4. **Architecture, typing, sizes, guardrails:**
   - All modules in `crc/cli/` are under the 400-line limit (`resource_ops.py`: 314, `vm_ops.py`: 299, `args.py`: 290, `notify.py`: 208, `validate.py`: 78, `base.py`: 62, `constants.py`: 19, `__init__.py`: 19, `runner.py`: 11) and pass ruff and mypy strict.
   - `crc/cli/README.md` has the four `ARCH-002` sections and its `## Public API` matches all public symbols in `crc/cli/`.
   - `pyproject.toml`, `docs/refactor/BUGS.md`, `docs/refactor/STATUS.md`, `crc/core/**`, `crc/notify/**`, cloud packages, and all `.github/CODEOWNERS` guardrail files are untouched.
5. **Bug register (`docs/refactor/bugs/pending/CLI-2.md`):**
   - Verified all 3 rows against `git show main:crc.py`: row 1 (`ping_on_slack` at `:249`, `:251`, `:493`), row 2 (`CRC.__init__` GCP `project_id` check at `:88` and `delete_spot_instances` GCP check at `:618-619`), row 3 (duplicate `notify_deleted_nic_via_slack` at `:337-352` and `:456-471`).
6. **Gates (`make check`):**
   - `ruff check .`: passed (`All checks passed!`)
   - `mypy`: passed (`Success: no issues found in 189 source files`)
   - `lint-imports`: passed (`5 kept, 0 broken`)
   - `pytest -q`: `24498 passed` (up from 12562, +11936)

VERDICT: APPROVE

## Round 2 (Opus)

Reviewed `git diff fe63352..HEAD` (the rung plus round 1) against the CLI contract,
`REVIEW_CHECKLIST.md` and `git show main:crc.py`.

### Round 1 fixes assessment

Round 1 made no code or test change (only its review section in this file). Its three NITs were
checked and still hold: `crc/cli/__init__.py` exports only the args and validator names; the
`ping_on_slack` unknown-operation `KeyError` is recorded in `docs/refactor/bugs/pending/CLI-2.md`;
`tests/README.md` and `tests/legacy/README.md` are outside the lane. Its citations were checked
and are correct: the retarget lines `tests/test_core_report.py:13,186-188` and
`tests/test_core_engine.py:14,275`, and the bug rows `main:crc.py:88`, `:249`, `:251`, `:493`,
`:337-352`, `:456-471`, `:618-619`. Round 1 introduced no behaviour change and weakened no test.

### Independent review

- `tests/legacy/crc_main.txt` is byte-identical to `git show main:crc.py`. `main()` in `crc.py` is
  character-identical to `main()` at `fe63352`. `tests/test_core_selection.py`,
  `tests/test_notify_parity.py`, `tests/legacy/`, `pyproject.toml`, `docs/refactor/BUGS.md`,
  `docs/refactor/STATUS.md`, `crc/core/`, `crc/notify/` and `.github/` are unchanged by the rung.
- Compared line by line with `main:crc.py`:
  - The three spot branches (`:585-679`) now live in `crc/cli/vm_ops.py:127-226`. Each reads the
    leftover-disk attribute once for Slack and once for Influx, as on main. The Azure branch still
    checks `resource_group`, still has the GCP `project_id` check, and still writes the NIC point.
    The empty-list skip and the `"#" + None` `TypeError` raised inside the `try` are kept.
  - `get_msg` (`:311-329`) is the exact main f-strings.
  - `delete_disks` keeps the cloud check, reports before it re-raises, and still passes keywords to
    `GCP_Disk`.
  - The Azure VM NIC message still reads `get_deleted` (`BUG-028`).
- Influx points carry no timestamp (`main:crc.py:529-534`, `crc/notify/influx.py`), so the line
  protocol comparison in `tests/test_cli_crc_parity.py` is deterministic.
- `tests/test_cli_crc_parity.py` asserts outcomes. It compares adapter constructor arguments,
  calls, attribute reads, Slack calls, Influx calls, stdout, constructor attributes and the
  result or error, and `test_tables_are_not_vacuous` guards against empty logs. My own mutation
  passed `"get_deleted_volumes"` instead of `"get_deleted_disks"` to the GCP spot report in
  `crc/cli/vm_ops.py:182`: 92 of the 96 `spot-gcp` cases failed. I reverted it.
- The retargeted monkeypatches patch the module that `CRC` now looks the name up in.
  `setattr(..., raising=True)` fails loudly if the name is missing, so the retarget cannot become
  a silent no-op.
- Architecture: no import-linter contract covers `crc.cli`, so the adapter imports in `vm_ops.py`
  and `resource_ops.py` are allowed. `crc/cli/README.md` Extension rule 3 now limits the
  no-cloud-import rule to `args.py` and `validate.py`, which matches the code. ARCH-002 README
  sections are present. Every file is under 400 lines.
- CLEANUP_SAFETY parity: the rung moves the `CRC` class only, so no age gate, tag parsing,
  circuit breaker or AWS spot order is touched. The aws+disk opt-in stays in `main()`, which is
  unchanged.

### Findings

- **BLOCKING:** none.
- **NIT (fixed, docs only):** the coding summary cited the retargeted lines as
  `tests/test_core_report.py` 185-187 and `tests/test_core_engine.py` 274. The real lines are
  186-188 and 275, and the summary now says so.
- **NIT (left):** the `crc/cli/__init__.py` docstring still reads "Command line parsing and input
  validation of crc.py". It does not mention the `CRC` class now in `crc.cli`. Re-exporting `CRC`
  there would import every cloud adapter on `import crc.cli`, so I left both as they are.
- **NIT (left):** `crc/cli/base.py`, `vm_ops.py` and `resource_ops.py` type `age`,
  `custom_age_tag_key`, `notags` and the clients as `Any`. That is broader than "Any only at SDK
  edges", but it is justified in `base.py:11-12` (`BUG-197` shape), and narrowing it would change
  what mypy accepts at the adapter calls.
- **New bug (pending):** `docs/refactor/bugs/pending/CLI-2.md`, 4th row. The `--scan_tag` loop on
  main builds `CRC(...)` outside its `try` (`main:crc.py:1442-1462`). A GCP scan without
  `--project_id` then aborts the read-only scan with a `ValueError` traceback, and OCI is never
  scanned. It is not in `BUGS.md`; `BUG-038` covers only the dead `clouds == "all"` branch.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt`, then `make check` (Python 3.12):
- `ruff check .`: all checks passed.
- `mypy`: no issues in 189 source files.
- import-linter: 5 kept, 0 broken.
- `pytest -q`: 24498 passed, the same as the coding summary and up from 12562 before the rung.

ROUND 2 VERDICT: APPROVE
