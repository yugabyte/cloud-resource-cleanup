# crc.cli.routes

## Purpose

One route table per cloud (`aws.py`, `azu.py`, `gcp.py`, `oci.py`): the set of resource kinds the
CLI runs through `crc.core.engine`. `crc/cli/dispatch.py`
asks `crc.cli.bridge.route_for(cloud, kind)` for every pair. A cloud's lane
edits only its own table and `crc/<cloud>/**`.

## Public API

- `Route`: frozen value of one routed kind: a flags-to-cleaner-options function, a `NotifyMode`, an optional gate run before the cleaner, optional extra notifiers, an optional after-run hook and an optional skip.
- `NotifyMode`: the members none (no Slack, no Influx: snapshot), report (after a successful run) or report-then-reraise (also report what was done when the cleaner raises, then raise it: AWS disk).
- `ExtraNotifiers`: type alias of a function from the run object to notifiers that run after Slack and Influx (for a message the report does not carry; no route needs it yet: the Azure VM NIC Slack message after the VM message comes from the report, its slack ids).
- `AfterRun`: type alias of a function taking the run object, the flags and the engine outcomes; runs after the notifiers (the snapshot console line).
- `OptionsBuilder`: type alias of a function from the parsed flags to the options dict of the cleaner.
- `Precheck`: type alias of a gate function taking the run object, the flags and the targets.
- `explicit_cloud_spot()`: the skip of the GCP and OCI spot_instances routes; logs the SAF-037 warning and returns True under cloud all.
- `aws_spot_instances()`: the skip of the AWS spot_instances route; warns to use spot_instance_requests and returns True.
- `non_aws_spot_instance_requests()`: the skip of the spot_instance_requests routes of Azure, GCP and OCI; warns that the kind is AWS-only and returns True.
- `no_options()`: the options builder of a skip-only route (no cleaner is built, so it returns an empty dict).
- `Skip`: type alias of a function taking the cloud, the flags and the targets that logs its warning and returns True when the pair must not run (SAF-037). A route's skip runs before its precheck, and then nothing is built, called or reported.

## Safety Invariants

- The GCP and OCI spot routes use the skip function of skips.py, so `--cloud all` warns and runs nothing for them (SAF-037). Every pair that runs something is in a table: the two warn-only pairs (`spot_instances` on AWS, `spot_instance_requests` on Azure, GCP and OCI) are skip-only routes (`Route(skips.no_options, skip=skips.aws_spot_instances)` / `skips.non_aws_spot_instance_requests`) that warn and run nothing; they need no registered cleaner. `aws.py` routes `disk`, `ip`, `keypair`, `kms`, `snapshot`, `spot_instance_requests`, `spot_instances` (skip-only), and `vm`; `azu.py` (cloud name `azure`) routes `disk` (report then re-raise), `ip`, `nic`, `spot_instance_requests` (skip-only), `spot_instances` (its `precheck` raises `ValueError` for an empty `--resource_group` before the cleaner) and `vm` (delete and stop); `gcp.py` routes `disk` (report then re-raise), `ip`, `vm` (delete and stop), `spot_instance_requests` (skip-only) and `spot_instances` (`skip` under `--cloud all`, `precheck` raises `ValueError` for an empty `--project_id`, SAF-037); `oci.py` routes `disk` (report then re-raise), `vm` (delete and stop), `spot_instance_requests` (skip-only) and `spot_instances` (`skip` under `--cloud all`, SAF-037).
- The table module imports the cleaner package of its cloud, which registers the cleaner; the registry decides what runs, the route only maps flags to options and says whether to notify.
- Option values are the raw flag values of the parsed flags (`None` stays `None`); the cleaner factory normalises them.

## Extension Rules

1. To route a kind, add `kind: Route(builder)` to `ROUTES` of the cloud file and an `_kind` options builder that returns the keys the cleaner factory reads from `context.options`.
2. Import the cleaner package in the cloud file (`import crc.<cloud>.<pkg>  # noqa: F401`) so it is registered.
3. Add the kind to the CLI parity tests (see `crc/cli/README.md`, Extension Rules).
4. Keep every file at 250 lines or fewer.
