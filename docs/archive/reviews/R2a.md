# R2a review

Scope reviewed: `git diff 547ff1a..3874424` (commit `3874424`, R2a core cleaner
contract in `crc/core/cleaner.py`, decorator registry in `crc/core/registry.py`,
report model in `crc/core/report.py`, and tests in `tests/test_core_cleaner.py`,
`tests/test_core_registry.py`, `tests/test_core_report.py`) against `crc.py`,
`docs/refactor/FILTER_VARIANTS.md`, `AGENTS.md`, `ARCHITECTURE.md` and
`.cursor/skills/crc-python-refactor/REVIEW_CHECKLIST.md`.

## 1. Report parity with `crc.py` (`crc/core/report.py`, `tests/test_core_report.py`)

Checked every notification method and sink in `crc.py`:

- `CRC.get_msg` (`crc.py:311-329`) vs `crc.core.report.get_msg` (`crc/core/report.py:173-181`):
  identical strings for both `dry_run=False` and `dry_run=True`, `Operation.DELETE`
  (`"Deleted"`) and `Operation.STOP` (`"Stopped"`), and all resource labels (`NIC`,
  `Disk`, `VM`, `IP`, `Keypair`, `KMS`, `Snapshot`,
  `SPOT_INSTANCE_REQUEST_AND_ASSOCIATED_INSTANCES`).
- Single-section `notify_*` methods (`notify_deleted_vm_via_slack` on non-Azure,
  `notify_stopped_vm_via_slack`, `notify_deleted_ip_via_slack`,
  `notify_deleted_keypair_via_slack`, `notify_deleted_nic_via_slack`,
  `notify_deleted_kms_via_slack`, `notify_deleted_disk_via_slack` list branch):
  identical text and `link_names` (`True` only on disk notifications).
- `CRC.ping_on_slack` (`crc.py:230-309`) vs `format_owner_ping`
  (`crc/core/report.py:191-208`): preserves the missing space between prefix and
  `` `{count}` `` and the hard-coded word `disks` for untagged (`*key*`),
  subteam (`<!subteam^id>`) and individual user (`<@id>`) mentions.
- Three parity gaps in `Report.from_result` / `Report.from_plan` and
  `tests/test_core_report.py` were found and fixed as BLOCKING below (AWS spot
  InfluxDB label, Azure VM NIC Slack message parity and empty-list notification,
  and owner-grouped disk report).

## 2. Cleaner contract (`crc/core/cleaner.py`, `tests/test_core_cleaner.py`)

Checked `Cleaner`, `Operation`, `Candidate`, `Skipped`, `Plan`, `Failure`,
`Result`, and `RunContext` against all 20 cleaners in
`docs/refactor/FILTER_VARIANTS.md`:

- **Stop operations** (`aws/vm.py`, `azu/vm.py`, `gcp/vm.py`, `oci/vm.py`):
  covered by `Operation.STOP` (`"stop"`), `Cleaner.supports(Operation.STOP)`,
  and `Result.succeeded` / `Plan.candidates`.
- **Spot cleaners with related leftover disks** (`aws/spot_instance_requests.py`,
  `azu/spot_vm.py`, `gcp/spot_vm.py`, `oci/spot_vm.py`): `Result.related` holds
  `{DISK: (...)}` (and `{NIC: (...), DISK: (...)}` on Azure); added `related` to
  `Plan` so `--dry_run` (`plan()` without `apply()`) also carries related
  leftover disks and NICs into `Report.from_plan`.
- **Azure VM NICs** (`azu/vm.py`, `azu/spot_vm.py`): `related={NIC: (...)}` on
  `Plan` and `Result`.
- **GCP disk owner ping** (`gcp/disk.py` with `slack_notify_users=True`): added
  `by_owner: Mapping[str, tuple[str, ...]] | None = None` to `Plan`, `Result`,
  and `Report` (fixed as BLOCKING below).
- All DTOs are `@dataclass(frozen=True, slots=True)`; sequence fields are
  converted to `tuple` and mapping fields (`Candidate.tags`, `Candidate.extra`,
  `Plan.related`, `Plan.by_owner`, `Result.related`, `Result.by_owner`,
  `RunContext.options`, `Report.by_owner`) are copied into `MappingProxyType`.

## 3. Registry (`crc/core/registry.py`, `tests/test_core_registry.py`)

- Duplicate `(cloud, kind)` registration raises `DuplicateCleanerError` naming
  both the existing and new factories while keeping the first registration.
- Empty `cloud` or `kind` raises `ValueError`.
- Unknown `(cloud, kind)` or unknown `cloud` raises `UnknownCleanerError` listing
  known clouds and known kinds for the requested cloud.
- Every test uses an isolated `Registry()` (or `monkeypatch.setattr` on
  `DEFAULT_REGISTRY`), and `DEFAULT_REGISTRY.clouds()` remains `()`.

## 4. Bug confirmation table (`docs/refactor/STATUS.md`)

| # | Bug / quirk in `docs/refactor/STATUS.md` | `file:line` | Status | Verification notes |
|---|------------------------------------------|-------------|--------|--------------------|
| 1 | `notify_deleted_nic_via_slack` defined twice; second wins so `get_deleted_nic` is never read; Azure VM / SpotVM delete sends VM names (`vm.get_deleted`) in the NIC Slack message while Influx uses `get_deleted_nic` | `crc.py:337-352`, `371-372`, `456-471`, `567`, `600`, `615` | Confirmed | First definition at `crc.py:337` reads `nic.get_deleted_nic` (`:344`); second definition at `crc.py:456` overwrites it and reads `nic.get_deleted` (`:463`). `notify_deleted_vm_via_slack` calls `self.notify_deleted_nic_via_slack(vm)` on Azure (`:371-372`), sending `vm.get_deleted` (VM names) as `NIC(s)`. Influx at `:567` and `:615` passes `get_deleted_nic`. |
| 2 | `ping_on_slack` hard-codes `"disks"` in the owner line and concatenates `msg + "\`N\`"` without a space (`"Deleted the following\`3\` aws Disk(s)"`) | `crc.py:248-251`, `265-275`, `289-292`, `493` | Confirmed | `msg` at `:249, :251` has no trailing space and `:267, :273, :291` append `` f"`{operated_list_length}` {self.cloud} {resource}(s):\n... disks `{operated_list[key]}`\n\n" `` directly. |
| 3 | `influxdb_bucket` and `resource_suffix` set only when `influxdb_conn` is truthy; without it `write_influxdb` raises `AttributeError` into the catch-all (`"Unable to push data to influxDB"`), which also hides `len(None)` | `crc.py:97-99`, `524-539` | Confirmed | `self.influxdb_bucket` and `self.resource_suffix` are only initialized inside `if influxdb_conn:` (`:97-99`). `write_influxdb` reads `self.resource_suffix` (`:524`), `len(resources)` (`:533`), and `self.influxdb_bucket` (`:537`) inside `try ... except Exception as e:` (`:520-539`). |
| 4 | `notify_deleted_disk_via_slack` sends nothing when `get_deleted` is neither `list` nor `dict` (`type(...) ==` rejects subclasses and tuples) and logs nothing | `crc.py:480-493` | Confirmed | Uses exact `if type(disk.get_deleted) == list:` (`:480`) and `elif type(disk.get_deleted) == dict:` (`:491`) with no `else` branch. |
| 5 | `write_influxdb` and Slack `notify_*` run in `--dry_run` mode too, so dry runs still push InfluxDB metrics | `crc.py:512-539`, `561-567`, `599-616`, `630-646`, `657-673`, `725-730`, `750-754`, `779-783`, `805-809`, `829-833`, `920-924`, `978-982` | Confirmed | Every `delete_*` / `stop_vm` method calls `write_influxdb` whenever `self.influxdb_client` is truthy without checking `self.dry_run`, and `write_influxdb` (`:512-539`) writes the same point in dry run and live run. |
| 6 | `operated_list_length` is computed twice in `ping_on_slack` (first sum is dead code); `crc.py:214` and `226` use bare `except:` | `crc.py:214`, `226`, `243-256` | Confirmed | `operated_list_length` is summed across all keys at `:243, :246-247` and overwritten before any read at `:256` (`operated_list_length = len(operated_list[key])`). `slack_lookup_user_by_email` (`:214`) and `get_user_groups_list` (`:226`) use bare `except:`. |
| 7 | `"#" + self.slack_channel` in `CRC` notification methods produces `"##name"` when `slack_channel` already starts with `"#"`, whereas `crc.py:1509` strips it for `notify_cleanup` | `crc.py:278`, `347`, `364`, `384`, `403`, `414`, `432`, `449`, `466`, `485`, `505`, `606`, `637`, `664`, `1509` | Confirmed | All 14 `chat_postMessage` calls in `CRC` use `channel="#" + self.slack_channel`, while `:1509` uses `"#" + slack_channel if not str(slack_channel).startswith("#") else slack_channel`. |

## BLOCKING (fixed)

1. **`Plan`, `Result`, and `Report` could not represent GCP disk owner-grouped deletions (`--slack_notify_users`) or dry-run `related` resources** (`crc/core/cleaner.py:61-99`, `crc/core/report.py:60-155`):
   - In `crc/gcp/disk.py:85-88, 201-223`, when `slack_notify_users=True`, `disk.get_deleted` is a `dict[str, list[str]]` keyed by owner label (or `"not_tagged"`). `crc.py:491-493` suppresses the channel summary message and calls `ping_on_slack`, while `crc.py:924` passes the `dict` to `write_influxdb(DISKS, disk.get_deleted)` (`names=str(dict)`, `count=len(dict)`).
   - Added `by_owner: Mapping[str, tuple[str, ...]] | None = None` to `Plan`, `Result`, and `Report` (copied into `MappingProxyType` of tuples), and `related: Mapping[str, tuple[str, ...]]` to `Plan` so `--dry_run` (`plan()` without `apply()`) carries related NICs/disks into `Report.from_plan`.
2. **AWS `spot_instance_requests` InfluxDB resource tag mismatch** (`crc/core/report.py:22, 146-170`, `crc.py:398-400, 729-730`):
   - `crc.py:399` sends Slack messages with label `SPOT_INSTANCE_REQUEST` (`"SPOT_INSTANCE_REQUEST_AND_ASSOCIATED_INSTANCES"`), but `crc.py:729` calls `self.write_influxdb(VMS, spot_instance_requests.get_deleted)` with resource tag `"VM"`. `Report.influx_points()` and `Report.influx_payload()` previously emitted `"SPOT_INSTANCE_REQUEST_AND_ASSOCIATED_INSTANCES"` instead of `"VM"`.
   - Mapped `SPOT_INSTANCE_REQUEST` to `VM` for Influx points and payloads in `crc/core/report.py` and verified parity against `crc.py` in `tests/test_core_report.py`.
3. **Azure VM / SpotVM `NIC` Slack notification parity and direct `crc.py` test comparison** (`crc/core/report.py:25-66, 125-138`, `tests/test_core_report.py:153-216`):
   - In `crc.py:371-372`, `notify_deleted_vm_via_slack` on Azure always sends the `NIC` Slack message even when empty (`notify_empty=True`, unlike Spot `Disk` messages which are guarded by `if disks:`), and formats `vm.get_deleted` in the Slack `NIC` message while `write_influxdb(NICS, vm.get_deleted_nic)` writes `vm.get_deleted_nic` to InfluxDB.
   - Updated `_build_sections` in `crc/core/report.py` so `NIC` related sections keep `notify_empty=True` and, on Azure `VM`, carry `slack_ids=primary_ids` while keeping `ids` for InfluxDB. Updated `tests/test_core_report.py` to test `delete_spot_instances` (for `azure`, `gcp`, `oci`), Azure VM NIC notifications, dry-run plans, and owner-grouped disk reports directly against `crc.py`.

## NIT (left)

- `crc/core/cleaner.py:114-120`: `RunContext.now` does not enforce timezone-awareness at construction time; cleaners comparing against cloud timestamps must pass timezone-aware datetimes consistently.

## 5. Scope and ratchet

- Only `ARCHITECTURE.md` (requested by the R2a prompt), `crc/core/__init__.py`, `crc/core/README.md`, `crc/core/cleaner.py`, `crc/core/registry.py`, `crc/core/report.py`, `docs/refactor/STATUS.md`, `docs/refactor/reviews/R2a.md`, and `tests/test_core_{cleaner,registry,report}.py` changed.
- `pyproject.toml` legacy ratchet lists and `tests/archtest/` baselines are untouched.

## 6. Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` (`crc`, `tests`) | Success: no issues found in 67 source files |
| `lint-imports` | 3 kept, 0 broken ("Cloud packages are independent", "Core never imports cloud packages or services", "Core never imports cloud or telemetry SDKs") |
| `pytest -q` | 767 passed (553 pre-R2a + 214 R2a tests) |

Test count: 553 on `547ff1a`, 758 on `3874424`, 767 after review fixes.

VERDICT: APPROVE
