# crc.cli

## Purpose

The command line layer of `crc.py`: the argparse definition of every flag, the input validators,
the `CRC` class that does the work per cloud, and `main()` which wires them. `crc.py` is a thin
entry point: it imports `main`, `get_argparser`, the validators, `CRC`, `Snapshot` and the constants
back under the same names (and keeps `if __name__ == "__main__": main()`), so the tests that load
`crc.py` by path and `python crc.py <flags>` in Jenkins keep working. The parsed arguments stay a
plain `dict`, as on `main`.

- `args.py`: `get_argparser()`, split into one private `_add_*_args` function per group of flags; the flag order is the order of `main`, which fixes the `--help` text and the key order of the returned dict. JSON and Python-literal values (`--age '{"days": 1}'`, `--filter_tags "{'k': ['v']}"`) go through `ast.literal_eval`.
- `validate.py`: the type validators and the InfluxDB field checks.
- `constants.py`: `CLOUDS`, `RESOURCES`, the report labels (`DELETED`, `VMS`, ...) and the operation map.
- `base.py`: `CRCBase`, the constructor and the run settings every operation reads.
- `notify.py`: `NotifyMixin`, the Slack and InfluxDB methods (`notify_*`, `get_msg`, `ping_on_slack`, `write_influxdb`).
- `vm_ops.py`: `VmOps`, the VM, spot VM, spot instance request and stop operations; it holds the VM and spot adapter imports.
- `resource_ops.py`: `ResourceOps`, the IP, keypair, NIC, disk, VPC and KMS operations; it holds those adapter imports.
- `runner.py`: `CRC`, composed from `VmOps` and `ResourceOps`.
- `main.py`: `main()`, which composes the modules below in the order of `main` on the main branch.
- `inputs.py`: `Options` (every flag read with `.get`), `Tokens`, `Targets`, `read_options`, `read_tokens`, and the checks in the order `main` ran them (`check_tokens`, `check_flag_combinations`, `resolve_targets`, `validate_values`).
- `clients.py`: the Slack `WebClient` and `InfluxDBClient` construction (the InfluxDB field check runs right before it) and the `MAX_AGE` environment write with its print, and its reset.
- `context.py`: `RunContext` (the settings every `CRC` of a run shares) and `make_crc`, the one place that builds `CRC(...)` for `main`.
- `scan.py`: `run_scan`, the read-only `--scan_tag` path; it wraps each cloud pass in `crc.core.logger.begin()` / `finish()` (`operation=scan`), prints, notifies Slack and always exits with code 0.
- `dispatch.py`: `run_dispatch`, the cloud and resource loops: one small handler per resource wrapped in `crc.core.logger.begin()` / `finish()` (`try`/`finally`), the AWS disk opt-in circuit breaker, the spot warnings and the `Snapshot` branch.

## Public API

- `main()`: the CLI entry (module main.py); crc.py re-exports it. It is not re-exported from the package init module, so the name `main` on the package stays the module.
- `run_scan()`: the read-only scan path of the scan_tag flag; prints, notifies Slack and exits with code 0.
- `run_dispatch()`: the cloud and resource loops, with the AWS disk breaker.
- `Options`: frozen value of every parsed flag; `read_options()` builds it from the argument dict.
- `Tokens`: frozen value of the two environment tokens; `read_tokens()` builds it.
- `Targets`: frozen value of the expanded clouds and resources; `resolve_targets()` builds it.
- `check_tokens()`: raise OSError when a Slack channel or InfluxDB is set without its token.
- `check_flag_combinations()`: raise ValueError on stop for non-vm, all without all, user label, KMS flags.
- `validate_values()`: the list and dict type checks of the flag values.
- `build_slack_client()`, `build_influxdb_client()`: construct the clients (InfluxDB field check first), or return None.
- `set_max_age()`, `reset_max_age()`: write and remove the MAX_AGE environment variable, with the prints.
- `RunContext`: frozen value of the settings every `CRC` of a run shares; `make_crc()` builds `CRC` from it.
- `get_argparser()`: parse the process arguments and return them as a dict; exits with code 2 on a usage error.
- `is_valid_type()`: raise ValueError when a value is not an instance of the expected type.
- `is_valid_list()`: no value or a list passes, anything else raises ValueError.
- `is_valid_dict()`: no value or a dict passes, anything else raises ValueError.
- `are_values_of_dict_lists()`: no value or a dict whose values are all lists passes, anything else raises ValueError.
- `validate_influxdb_inputs()`: require url, org and bucket in the InfluxDB dict, else raise ValueError.
- `CRC`: the cleanup object built per cloud (module runner.py); every public method of the run keeps its name, signature and call order.
- `CRCBase`: constructor and run settings (cloud, dry run, notags, the Slack and Influx clients).
- `NotifyMixin`: Slack and Influx methods of `CRC`.
- `VmOps`: VM, spot and stop operations of `CRC`.
- `ResourceOps`: IP, keypair, NIC, disk, VPC and KMS operations of `CRC`.

## Safety Invariants

- Every flag, default, choice, metavar, help text, literal value form, error type and message,
  and the order of the flags stay exactly as on `main` (`tests/legacy/crc_main.txt` is the
  oracle, `tests/test_cli_args_parity.py` compares both).
- `ast.literal_eval` is the `type=` of the literal flags and must stay that function (argparse
  prints its name in the usage error).
- Jenkins jobs run `python crc.py <flags>`; a flag may never be renamed, removed or re-typed here.
- `CRC` keeps the call order of `main`: adapter constructor, `delete()` / `stop()`, Slack, then
  Influx; `delete_disks` runs Slack and Influx before it re-raises a `delete()` error. The adapter
  classes are looked up in the module of the mixin that uses them (`vm_ops`, `resource_ops`) at
  call time. `tests/test_cli_crc_parity.py` runs the oracle CRC and this one on the same fakes.
- `main()` keeps the order of `main` on the main branch: parse flags, read the two tokens, `SLACK_BOT_TOKEN` then `INFLUXDB_TOKEN` errors, stop-only-for-vm, all-needs-all, slack user label, KMS description and user, the list and dict type checks, the Slack client, the InfluxDB field check and client, `MAX_AGE` set, then the scan path (exit) or the loops, then `MAX_AGE` reset. Errors keep their type (`OSError`, which `EnvironmentError` is an alias of) and message.
- `scan.py` never deletes: it forces `dry_run`, builds `CRC` per cloud and only calls `scan_instances_with_tag`; it always ends in `sys.exit(0)`.
- The AWS EBS breaker lives in `dispatch._disk`: any `aws` + `disk` pair that is not `--cloud aws --resource disk` raises before a disk call. `CRC`, `Snapshot`, `WebClient`, `InfluxDBClient` and `notify_cleanup` are looked up in `context`, `dispatch`, `clients` and `scan` at call time; `tests/test_cli_main_parity.py` runs the oracle `main()` and this one on recording fakes.
- Known quirks (kept): `docs/refactor/BUGS.md` rows `BUG-009`, `BUG-028` to `BUG-044`, `BUG-084`, `BUG-333` to `BUG-350`.

## Extension Rules

1. A new flag is added to the matching `_add_*_args` group in `args.py`, with a row in the parity
   table of `tests/test_cli_args_parity.py`, and only in a rung that allows a new flag.
2. Keep each file under 400 lines and each function within the ruff limits; split by group of
   flags or by responsibility (notify, vm, resources), not by line count.
3. `args.py`, `validate.py`, `constants.py` and `inputs.py` import no cloud package, no `crc.notify`, no SDK; the SDK client, adapter and
   notifier imports live in `clients.py`, `notify.py`, `vm_ops.py`, `resource_ops.py`, `scan.py` and `dispatch.py` only.
