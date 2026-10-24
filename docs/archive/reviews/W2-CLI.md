# W2-CLI review

## Coding summary

Wave W2 lane W2-CLI: structure-only split of six files to 250 lines or fewer, plus dedupe of the
Slack/Influx report boilerplate. No behaviour change; no test was edited.

- **Files split (before -> after lines, `wc -l`).**
  - `crc/cli/args.py` 290 -> 139; the age, notify and resource flag groups moved to new `crc/cli/args_run.py` (157). Flag order is unchanged (`get_argparser` still calls the groups in the old order).
  - `crc/cli/dispatch.py` 267 -> 245; the pair table and unsupported-pair message moved to new `crc/cli/pairs.py` (36). `dispatch` re-exports `SUPPORTED_CLOUDS` and `is_supported_pair`; `HANDLERS`, `Snapshot`, `make_crc` and `AWS_DISK_OPT_IN_MESSAGE` stay in `dispatch`, so tests that patch them still hit the code.
  - `crc/cli/resource_ops.py` 314 -> 237 and `crc/cli/vm_ops.py` 299 -> 235 (dedupe, below). The adapter classes stay module globals of these two files and are looked up at call time, so `monkeypatch.setattr(resource_ops|vm_ops, "<Adapter>", ...)` in `tests/test_cli_crc_parity.py`, `test_core_engine.py`, `test_core_report.py` and `test_cli_dispatch_fix.py` still takes effect.
  - `crc/core/policy.py` 362 -> 16; now a facade (star re-export with `__all__` per module) over `policy_tags.py` (175), `policy_text.py` (85), `policy_managed.py` (127) and `policy_age.py` (46). `from crc.core import policy`, `crc.core.__init__` and `policy.<name>` work as before; `crc.core` imports no cloud, cli or notify package.
  - `crc/notify/slack.py` 325 -> 213; message splitting moved to `slack_chunks.py` (48), the posting, user lookup and owner-ping methods to `slack_owners.py` (96, class `SlackPoster`, base of `SlackNotifier`). `human_readable_timedelta`, `_format_item_row`, `send_slack_message` (it uses `time`), `notify_cleanup` and `datetime` stay in `slack.py` because tests patch `slack.human_readable_timedelta`, `slack.datetime` and `crc.notify.slack.time.sleep`. `SLACK_SAFE_CHARS`, `ITEMS_PER_MESSAGE`, `_chunk_lines_into_messages`, `NOT_FOUND` and `SlackClient` are re-exported from `slack`.
- **Helpers extracted (inside `crc/cli`).**
  - `report_calls.report_result(ops, resource, notify, label, result_attr)`: the `if slack_client: notify(...)` then `if influxdb_client: write_influxdb(...)` pair, used by IP, keypair, NIC, disk, KMS (resource_ops) and delete_vm, spot instance requests, stop_vm (vm_ops). The result attribute is still read only when an Influx client is set.
  - `adapter_args.vm_arguments` (the four VM and three spot VM constructors, previously seven copies of the same argument lists), `ip_arguments`, `disk_arguments` and `DiskArgs`. Only the argument lists moved; the class is chosen in `vm_ops` / `resource_ops`.
  - `vm_ops._delete_spot`: one body for the Azure, GCP and OCI spot deletes (the `resource_group` / `project_id` checks stay before it, as before).
- **Tests.** `pytest -q`: 43010 at cea0a8a, 43010 after (equal). `make check` (ruff, mypy, lint-imports, pytest) green; runtime 2m42s (baseline at cea0a8a: 2m54s; Python 3.12 in this VM).
- **READMEs updated.** `crc/cli/README.md` (new modules and public names), `crc/notify/README.md` (`SlackPoster`), `crc/core/README.md` (policy facade note).
- **Owner actions.**
  - `report_result` could become a `NotifyMixin` method in `crc/cli/notify.py` (not in this lane); it moves there unchanged. No cross-cloud helper candidates found in these files.
  - `crc/cli/main.py` was not touched; `scan.py` still imports `_run_options` from `dispatch`.
  - After the wave, the `README.md` rule "Keep each file under 400 lines" in `crc/cli/README.md` should become 250 together with ARCH-004.
  - Python in this VM is 3.12, not the 3.11 CI uses; nothing here depends on the difference.

## New bugs

Noticed, not fixed:

- `crc/cli/resource_ops.py:190-198` `delete_vpc` sends no Slack message and writes no Influx points, unlike every other delete.
- `crc/notify/slack.py:177` `notify_cleanup` logs a failed part with the root `logging.exception`, not the module `logger`.
- `crc/notify/slack_owners.py:69` the owner e-mail domain `@yugabyte.com` is hard coded.
- `crc/cli/vm_ops.py:27-33` `_delete_vm` treats an empty `instance_state` list like `None` (delete all states).

## Round 1 (Argon)

### Findings

None (no blocking or non-blocking defects found).

1. **No behaviour change (`git diff --color-moved=zebra cea0a8a..HEAD` + AST check):**
   - `crc/cli/args.py` (139 lines) + `crc/cli/args_run.py` (157 lines): all 6 functions (`get_argparser` and the 5 `_add_*_args` builders) have identical ASTs and call order to `cea0a8a`.
   - `crc/cli/dispatch.py` (245 lines) + `crc/cli/pairs.py` (36 lines): all 17 functions and constants (`UNSUPPORTED_PAIR_MESSAGES`, `SUPPORTED_CLOUDS`, `AWS_ONLY_RESOURCES`, `is_supported_pair`, `_unsupported_message`) are AST-identical to `cea0a8a`.
   - `crc/core/policy.py` (16 lines) + `policy_tags.py` (175 lines), `policy_text.py` (85 lines), `policy_managed.py` (127 lines), `policy_age.py` (46 lines): all 43 functions/enums/constants are AST-identical to `cea0a8a` and re-exported from `crc.core.policy`.
   - `crc/notify/slack.py` (213 lines) + `slack_chunks.py` (48 lines), `slack_owners.py` (96 lines): all 16 functions and `SlackNotifier`/`SlackPoster` methods are AST-identical to `cea0a8a`.
   - `crc/cli/resource_ops.py` (237 lines) + `crc/cli/vm_ops.py` (235 lines) + `crc/cli/report_calls.py` (22 lines) + `crc/cli/adapter_args.py` (91 lines): `report_result` preserves the `if slack_client:` then `if influxdb_client:` sequence and only reads `getattr(resource, result_attr)` when `influxdb_client` is truthy; `vm_arguments`, `ip_arguments`, and `disk_arguments` produce the exact per-cloud positional/keyword argument tuples/dicts; `delete_disks` preserves its `try`/`except`, report-before-raise order; `delete_spot_instances` preserves the `resource_group`/`project_id` checks before adapter construction in `_delete_spot`.
2. **Import paths and monkeypatch/mock targets:**
   - Every symbol imported by `crc.py`, `crc/core/__init__.py`, `crc/notify/__init__.py`, `crc/notifications.py`, `crc/cli/*`, cloud packages, and `tests/*` resolves at its original import path.
   - All test patch targets continue to hit live code: adapter globals (`AWS_Disk`, `ElasticIPs`, `KeyPairs`, `Kms`, `VPC`, `Disk`, `AZU_IP`, `NIC`, `GCP_Disk`, `GCP_IP`, `OCI_Disk`, `SpotInstanceRequests`, `AWS_VM`, `AZU_SpotVM`, `AZU_VM`, `GCP_SpotVM`, `GCP_VM`, `OCI_SpotVM`, `OCI_VM`) remain module globals in `resource_ops` and `vm_ops` looked up at call time; `dispatch.make_crc`, `dispatch.HANDLERS`, `dispatch.Snapshot`, `dispatch.AWS_DISK_OPT_IN_MESSAGE`, `dispatch.SUPPORTED_CLOUDS`, `dispatch.is_supported_pair` remain on `crc.cli.dispatch`; `slack.human_readable_timedelta`, `slack.datetime`, `crc.notify.slack.time.sleep`, and `slack._chunk_lines_into_messages` remain on `crc.notify.slack` (and `human_readable_timedelta` is called from `_format_item_row` inside `crc.notify.slack`).
3. **Protected files and tests:**
   - Zero test files, oracle files (`tests/legacy/*.txt`), hash-locked safety tests (`tests/archtest/safety_lock.json`), or out-of-lane files (`crc/cli/main.py`, `crc/core/age.py`, docs/config) were touched.
4. **Line counts and seam quality:**
   - Every owned and newly created source file is under the 250-line cap (`wc -l` max is `crc/cli/dispatch.py` at 245 lines). Splits follow clean domain seams (`args_run`, `pairs`, `report_calls`, `adapter_args`, `policy_tags`/`policy_text`/`policy_managed`/`policy_age`, `slack_chunks`/`slack_owners`).
5. **READMEs:**
   - `crc/cli/README.md`, `crc/core/README.md`, and `crc/notify/README.md` are concise and pass ARCH-002 Public API synchronization.

### Gates & Test Count

- `python3 -m ruff check .`: PASS
- `python3 -m mypy`: PASS (`Success: no issues found in 114 source files`)
- `lint-imports`: PASS (`10 contracts kept, 0 broken`)
- `pytest tests/archtest/ -q`: PASS (ARCH-001..005, SAF-LOCK all green)
- `make check`: PASS — **43,010 passed** in 156.44s (`make check` total wall time: **2m43s**; base `cea0a8a` count: 43,010).

VERDICT: APPROVE

## Round 2 (Opus)

### Round 1 check

Round 1 reported no findings and made no fixes, so there was nothing to re-verify beyond its claims. Its
claims hold, with two small corrections to the gate output: in this VM `lint-imports` reports
`5 kept, 0 broken` (not 10) and mypy checks 261 source files (not 114). Neither changes the verdict.

### Findings

No blocking findings. I changed no code.

1. **No behaviour change.** I compared every top-level def, class, method and constant of the six base
   modules with its new location by AST. `args`, `dispatch`, `policy` and the `SlackNotifier` methods
   (now on `SlackPoster` in `slack_owners.py`) are identical. I reviewed the `resource_ops` / `vm_ops`
   dedupe hunk by hunk:
   - Slack still runs before Influx. The Influx attribute is still read only when an Influx client is set.
   - `delete_vm` still writes the Azure NIC point after the VM point.
   - Spot requests still write VMS, then DISKS.
   - The `delete_disks` report still runs before the re-raise.
   - The Azure and GCP spot pre-checks still run before the adapter is built.
   - Unsupported clouds still raise before any constructor argument is read.
   - The per-cloud positional and keyword argument lists are the same as at the base.

   One small difference: `vm_arguments`, `ip_arguments` and `disk_arguments` now read `ops.notags`
   before `dry_run` / `resource_group`. These are plain instance attributes (no property or
   `__getattr__` in `crc/cli`), so nothing observable changes.
2. **Import paths and patch targets.**
   - Every name that `tests/`, `crc.py` and `crc/` import from the six old paths still resolves.
   - Some names no longer appear on the old modules: `dispatch.UNSUPPORTED_PAIR_MESSAGES`,
     `dispatch.AWS_ONLY_RESOURCES` and `dispatch.CLOUDS`, the private policy helpers (`_K8S_KEYS`,
     `_folded_key`, `_key_matches`, `_looks_oke`, `_text`, ...), `slack._split_lines`,
     `slack.format_owner_ping` and `resource_ops._DiskArgs`. A repo-wide grep finds no reference to any
     of them through the old module, so the contract holds.
   - Patch targets still hit live code. That covers `dispatch.make_crc`, `dispatch.HANDLERS`,
     `dispatch.Snapshot`, `slack.datetime`, `slack.human_readable_timedelta` and
     `"crc.notify.slack.time.sleep"`. It also covers the adapter globals of `vm_ops` and `resource_ops`,
     which are still looked up at call time.
3. **Protected files.** `git diff cea0a8a..HEAD` touches no test, oracle, hash-locked file,
   `crc/cli/main.py`, `crc/core/age.py` or config/doc file outside this review.
4. **Size and seams.** The largest file is `crc/cli/dispatch.py` at 245 lines (`wc -l`); every owned and
   new file is at 250 or fewer. The splits follow real concerns: flag groups, pair table, adapter
   arguments, report call, policy by topic, Slack chunking vs posting. `report_result` has 9 callers and
   `vm_arguments` 7. The name `args_run.py` is vague; this is non-blocking.
5. **READMEs.** The cli, core and notify READMEs list the new public names (ARCH-002 green) and stay short.

### Gates

- `pip install -r requirements.txt -r requirements-dev.txt`, then `make check`: PASS, run here.
  - ruff: clean.
  - mypy strict: no issues in 261 files.
  - lint-imports: 5 kept, 0 broken.
  - pytest: green, including ARCH-001..005 and SAF-LOCK.
- Test count: **43010 passed** (43010 collected at `cea0a8a`, checked in a temporary worktree).
- `make check` runtime: **180 s** wall (pytest 161 s), Python 3.12.3.

### New bugs

None beyond those listed above.

ROUND 2 VERDICT: APPROVE
