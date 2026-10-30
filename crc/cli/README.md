# crc.cli

## Purpose

The command line layer of `crc.py`: the argparse definition of every flag, the input validators,
the `CRC` run context (the settings of one cloud), the bridge to `crc.core.engine` that runs every
(cloud, kind) pair through the route tables, and `main()` which wires them. `crc.py` is a thin
entry point: it imports `main`, `get_argparser` and the validators back under the same names (and
keeps `if __name__ == "__main__": main()`), so the tests that load `crc.py` by path and
`python crc.py <flags>` in Jenkins keep working. The parsed arguments stay a
plain `dict`, as on `main`.

- `args.py` and `args_run.py`: `get_argparser()`, split into one private `_add_*_args` function per group of flags (selection and filter flags in `args.py`, age, notify and resource flags in `args_run.py`); the flag order is the order of `main`, which fixes the `--help` text and the key order of the returned dict. JSON and Python-literal values (`--age '{"days": 1}'`, `--filter_tags "{'k': ['v']}"`) go through `ast.literal_eval`.
- `validate.py`: the type validators and the InfluxDB field checks.
- `constants.py`: `CLOUDS` and `RESOURCES` (the `--cloud all` and `--resource all` order).
- `pairs.py`: the supported (cloud, resource) pairs and the unsupported-pair message; `dispatch.py` re-exports them.
- `runner.py`: `CRC`, the run context of one cloud: the settings the bridge, the notifiers and the scan read (`cloud`, `dry_run`, `notags`, `project_id`, `resource_group`, the Slack and Influx clients, `slack_channel`, `influxdb_bucket`, `resource_suffix`) and `get_msg`. It runs nothing and sends nothing itself.
- `main.py`: `main()`, which composes the modules below in the order of `main` on the main branch.
- `inputs.py`: `Options` (every flag read with `.get`), `Tokens`, `Targets`, `read_options`, `read_tokens`, and the checks in the order `main` ran them (`check_tokens`, `check_flag_combinations`, `resolve_targets`, `validate_values`).
- `clients.py`: the Slack `WebClient` and `InfluxDBClient` construction (the InfluxDB field check runs right before it) and the `MAX_AGE` environment write with its print, and its reset.
- `context.py`: `RunContext` (the settings every `CRC` of a run shares) and `make_crc`, the one place that builds `CRC(...)` for `main`; `read_max_age_env()` reads the MAX_AGE variable and `install_max_age_source()` (called first in `main`) hands it to the age module of the core package so that the core reads no environment.
- `scan.py`: `run_scan`, the read-only `--scan_tag` path, and `scan_instances_with_tag`, which calls `crc.aws.vm.scan.scan_tag` for AWS (the only cloud with a finder) and raises NotImplementedError for Azure, GCP and OCI after their set-up (`Service()` logging, Azure credentials, OCI key, GCP client; `_prepare_scan`); it wraps each cloud pass in `crc.core.logger.begin()` / `finish()` (`operation=scan`), prints, notifies Slack and always exits with code 0.
- `bridge.py`: `route_for`, `run_routed`, `init_run_logging`, `build_registry`, `build_notifiers`: the engine bridge. A pair whose kind is in the route table of its cloud (`routes/aws.py`, `crc/cli/routes/azu.py`, `crc/cli/routes/gcp.py`, `crc/cli/routes/oci.py`) initialises logging via `Service`, runs `crc.core.engine.run` with the registered cleaner, a `RunContext` and a one pair `Selection`, then the Slack and Influx notifiers (Slack first, only when a Slack client is set; Influx only when an Influx client is set) and the route's extra notifiers. The route's `NotifyMode` picks none (`snapshot`), report (default) or report then re-raise (`disk`: a `plan()` or `apply()` error is reported with `partial_succeeded`, then raised again); `Route.skip` returns True after its warning when the pair must not run (nothing built, called or reported; it runs before `Route.precheck`), `Route.precheck` is the AWS disk opt-in gate and `Route.after` prints the snapshot line. `init_run_logging`, `build_registry` and `build_notifiers` are looked up in `bridge` at call time: the seam the CLI parity tests patch (`tests/helpers_cli_routes.py`).
- `routes/`: the route tables (see `routes/README.md`).
- `dispatch.py`: `run_dispatch`, the cloud and resource loops: each pair is wrapped in `crc.core.logger.begin()` / `finish()` (`try`/`finally`) and run through `bridge`; the pair validation (`validate_targets`, including the AWS-only `snapshot`) with the AWS disk opt-in breaker. Every pair is engine-routed: the two warn-only pairs (`spot_instances` on AWS, `spot_instance_requests` off AWS) have skip-only routes (`skips.no_options`, no cleaner registered).

## Public API

- `read_digest_target()`: the Slack digest target (the CRC_SLACK_DIGEST_DIR directory and the Jenkins STAGE_NAME, BUILD_URL, BUILD_NUMBER, JOB_NAME), or None when the directory variable is unset or empty (module digest_target.py).
- `main()` of module digest.py (run by the root script crc_digest.py with --dir and --dry_run) returns the exit status: reads SLACK_BOT_TOKEN (2 when missing, except in dry run), drops records whose build is not BUILD_NUMBER (when set), builds the WebClient, posts the digest (1 when a channel's top message failed, else 0); dry run prints the Block Kit JSON.
- `attach_digest()`: gives each Slack notifier of a pair the digest target; with it the notifier writes a digest record instead of posting the channel messages (module bridge.py).
- `route_for()`: the route of a (cloud, kind) pair, or None when the pair is not in the route tables (module bridge.py).
- `is_routed_kind()`: True when at least one cloud routes the kind.
- `run_routed()`: run one routed pair through the engine with its notifiers.
- `init_run_logging()`: initialise the log file and root handlers; patched by the parity tests.
- `build_registry()`: the registry the routed cleaners come from; patched by the parity tests.
- `build_notifiers()`: the Slack then Influx notifiers of a routed pair; patched by the parity tests.
- `main()`: the CLI entry (module main.py); crc.py re-exports it. It is not re-exported from the package init module, so the name `main` on the package stays the module.
- `run_scan()`: the read-only scan path of the scan_tag flag; prints, notifies Slack and exits with code 0.
- `run_dispatch()`: the cloud and resource loops, with the AWS disk breaker.
- `validate_targets()`: check every (cloud, resource) pair before the first cleaner (BUG-017, BUG-018, BUG-009, BUG-084); raise an error, or return the pairs to skip under cloud all.
- `is_supported_pair()`: False for a pair the cloud does not have (keypair and kms off AWS, nic off Azure, ip on OCI) or that is AWS-only (snapshot off AWS).
- `Options`: frozen value of every parsed flag; `read_options()` builds it from the argument dict.
- `Tokens`: frozen value of the two environment tokens; `read_tokens()` builds it.
- `Targets`: frozen value of the expanded clouds and resources; `resolve_targets()` builds it.
- `check_tokens()`: raise OSError when a Slack channel or InfluxDB is set without its token.
- `check_flag_combinations()`: raise ValueError on stop for non-vm, all without all, user label, KMS flags.
- `check_scan_tag()`: raise ValueError for an empty or blank scan_tag flag (BUG-344).
- `validate_values()`: the list and dict type checks of the flag values.
- `build_slack_client()`, `build_influxdb_client()`: construct the clients (InfluxDB field check first), or return None.
- `set_max_age()`, `reset_max_age()`: write and remove the MAX_AGE environment variable, with the prints.
- `RunContext`: frozen value of the settings every `CRC` of a run shares; `make_crc()` builds `CRC` from it. `install_max_age_source()` and `read_max_age_env()` install the reader of the MAX_AGE variable into the core age module.
- `scan_instances_with_tag()`: the instances of the cloud of a `CRC` that carry a tag (module scan.py); raises NotImplementedError for every cloud but AWS and ValueError for an unknown cloud.
- `get_argparser()`: parse the process arguments and return them as a dict; exits with code 2 on a usage error.
- `is_valid_type()`: raise ValueError when a value is not an instance of the expected type.
- `is_valid_list()`: no value or a list passes, anything else raises ValueError.
- `is_valid_dict()`: no value or a dict passes, anything else raises ValueError.
- `are_values_of_dict_lists()`: no value or a dict whose values are all lists passes, anything else raises ValueError.
- `validate_influxdb_inputs()`: require url, org and bucket in the InfluxDB dict, else raise ValueError.
- `CRC`: the run context built per cloud (module runner.py); it holds the settings and the snapshot message helper, and runs nothing.

## Safety Invariants

- Every flag, default, choice, metavar, help text, literal value form, error type and message,
  and the order of the flags stay exactly as on `main` (`tests/legacy/crc_main.txt` is the
  oracle, `tests/test_cli_args_parity.py` compares both).
- `ast.literal_eval` is the `type=` of the literal flags and must stay that function (argparse
  prints its name in the usage error).
- Jenkins jobs run `python crc.py <flags>`; a flag may never be renamed, removed or re-typed here.
- A pair runs in the order of `main`: `RUN START`, the route's skip and precheck, `engine.run`
  (`plan()`, then `apply()` unless dry run), Slack, then Influx, `RESULT`; a `REPORT_THEN_RERAISE`
  route (disks) runs Slack and Influx before it re-raises a cleaner error. `build_registry` and
  `build_notifiers` are looked up in `bridge` at call time; `tests/test_cli_crc_parity.py` and
  `tests/test_cli_main_parity.py` run the oracle (`tests/legacy/crc_main.txt`) and this path on
  the same fakes.
- `main()` keeps the order of `main` on the main branch: parse flags, read the two tokens, `SLACK_BOT_TOKEN` then `INFLUXDB_TOKEN` errors, stop-only-for-vm, all-needs-all, slack user label, KMS description and user, the list and dict type checks, the Slack client, the InfluxDB field check and client, `MAX_AGE` set, then the scan path (exit) or the loops, then `MAX_AGE` reset. Errors keep their type (`OSError`, which `EnvironmentError` is an alias of) and message.
- `scan.py` never deletes: it forces `dry_run`, builds `CRC` per cloud and only calls `scan_instances_with_tag` (looked up in `scan` at call time); it always ends in `sys.exit(0)`.
- The AWS EBS breaker lives in `Route.precheck` of the AWS disk route (`routes/aws.py`) and, since FIX-CLI-ALL (BUG-018), also in `dispatch.validate_targets`: any `aws` + `disk` pair that is not `--cloud aws --resource disk` (so `--cloud all --resource disk|all`) raises `ValueError` before any cleaner of any cloud runs; it does not run.
- One pair rule (FIX-CLI-ALL BUG-017 merged with FIX-CLI-ROUTE BUG-009/BUG-084, as FIX-CLI-MERGE): `pairs.SUPPORTED_CLOUDS` (re-exported by `dispatch`) lists the clouds each resource runs on, and `dispatch.validate_targets` checks every (cloud, resource) pair before the first cleaner. `main()` calls it before `MAX_AGE` is set (not on the read-only `--scan_tag` path) and `run_dispatch` calls it again. The pairs are `keypair` and `kms` (AWS), `nic` (Azure), `ip` (not OCI) and `snapshot` (AWS only). An unsupported pair of an explicit cloud raises `ValueError` up front (the unsupported-pair message; `--resource <r> is AWS-only; use --cloud aws (got --cloud <c>)` for `snapshot`), so nothing runs and `MAX_AGE` is untouched. Under `--cloud all` it also raises up front when a supported pair would run after it (`--cloud all --resource nic`: `main` stopped on aws, so Azure NICs are never deleted); otherwise it is skipped with a WARNING (`<r> is not supported on <cloud>; skipped under --cloud all`) plus a `RUN START` / `SKIPPED reason=unsupported_cloud_resource` / `RESULT` log triple. So `--cloud all --resource snapshot` runs once, for AWS. `CRC`, `WebClient`, `InfluxDBClient` and `scan_instances_with_tag` are looked up in `context`, `clients` and `scan` at call time; `tests/test_cli_main_parity.py` runs the oracle `main()` and this one on recording fakes.
- An empty or blank `--scan_tag` raises `ValueError` from `inputs.check_scan_tag` before `MAX_AGE` is set and before any cleaner runs (BUG-344, fixed in FIX-CLI-ROUTE); it used to be falsy, skip the read-only scan and run the destructive cleanup.
- Known quirks (kept): `docs/refactor/BUGS.md` rows `BUG-028` to `BUG-044`, `BUG-333` to `BUG-350` except `BUG-344`. `BUG-009` and `BUG-084` keep only the hard-coded `age_days=90` and the missing Slack and Influx notification of `snapshot`.

## Extension Rules

1. A new flag is added to the matching `_add_*_args` group in `args.py` or `args_run.py`, as an
   optional flag whose default keeps today's behaviour. New flags are not blocked: add one entry to
   the ADDED tables of `tests/helpers_ag_cli_added.py` (`ADDED_FLAGS`, `ADDED_CHOICES`,
   `ADDED_RESOURCES`, `RESOURCE_HELP`); the parity tests still compare every existing flag with
   the oracle `tests/legacy/crc_main.txt`, never edit it. Check the new flag against the
   production Jenkins commands in `tests/fixtures/jenkins_commands.txt`
   (`tests/test_jenkins_contract.py`), then run `make smoke-offline`. Setup: `pip install -r
   requirements.txt -r requirements-dev.txt -c constraints.txt` (see the flag workflow in `AGENTS.md`).
2. Keep each file at 250 lines or fewer and each function within the ruff limits; split by group of
   flags or by responsibility, not by line count.
3. Route a kind through the engine (pattern: AWS `keypair`, `routes/aws.py`). In the cloud's route file
   add an options builder (flags to the keys the registered factory reads from `RunContext.options`)
   and `"kind": Route(builder)` to `ROUTES`, and import the cleaner package so it registers. A
   supported pair without a route runs nothing between RUN START and RESULT, so every supported
   pair needs one. Gates (the AWS disk opt-in) go in `Route.precheck`. Test it with the CLI parity
   tests (`tests/test_cli_main_parity.py` `ROUTED_CALLS` (AWS; every other cloud has its own
   `tests/helpers_routed_<cloud>.py` with a `ROUTED_CALLS` table), `tests/test_cli_logger.py`), which use
   `FakeCleaners` of `tests/helpers_cli_routes.py`: it patches `bridge.build_registry` and
   `bridge.build_notifiers` so the fake cleaner records the flags it received; add the kind to
   the per-cloud `tests/test_cli_bridge_<cloud>.py` (`tests/test_cli_bridge_parity.py` for `keypair`) to compare Slack, Influx, output and errors with the oracle.
4. `args.py`, `validate.py`, `constants.py` and `inputs.py` import no cloud package, no `crc.notify`, no SDK; the SDK client and
   notifier imports live in `clients.py`, `bridge.py`, `scan.py` and `dispatch.py` only.
