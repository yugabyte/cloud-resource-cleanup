# crc.core

## Purpose

Pure building blocks shared by every cloud package. `crc.core.age` holds the age-gate
logic moved out of `crc/service.py` without behaviour change: validating `--age` values,
reading custom age tags, and deciding whether a resource is old enough. `crc.core.policy`
holds the tag, notags, exception, regex, state, yb_task and managed-node filtering used by
the cleaners, one explicitly named function per variant catalogued in
`docs/refactor/FILTER_VARIANTS.md`. Cleaners fetch and normalise; this layer decides. Every cleaner under `crc/aws`, `crc/azu`, `crc/gcp` and `crc/oci` calls it.

The contracts cleaners plug into live here too: `crc.core.cleaner` (the `Cleaner` protocol
and its frozen DTOs), `crc.core.registry` (decorator registration by cloud and kind) and
`crc.core.report` (the report model that reproduces what `crc.py` sends to Slack and
InfluxDB). `crc.core.engine` runs the registered cleaners of a selection and
`crc.core.selection` holds the `--cloud` / `--resource` expansion and opt-in gates of
`crc.py main()`. Every cleaner of the four cloud packages is registered (AWS: disk, ip, keypair, kms, snapshot, spot_instance_requests, vm, vpc; Azure: disk, ip, nic, spot_instances, vm; GCP: disk, ip, spot_instances, vm; OCI: disk, spot_instances, vm). `crc/cli` still runs the adapter classes and its `CRC` methods; wiring it through `engine.run` is a deferred rung (`docs/refactor/PLAN.md`). The Slack and Influx notifiers that
implement the engine's `Notifier` protocol live in `crc.notify` (R3) and `crc/cli` already
sends its notifications through them.

## Public API

An age spec is a dict with days and/or hours integer values.

- `AgeSpec`: type alias for the days/hours dict.
- `AGE_KEYS`: the allowed age keys, days and hours.
- `require_positive_age()`: normalise an int or dict age, raise ValueError when it is empty, malformed, negative, overflowing or zero.
- `lookup_custom_age_tag()`: return whether the custom age tag is present, and its raw value, for AWS list tags or dict tags.
- `validated_retention_age()`: fail-closed custom age tag parse; None when the tag is absent, ValueError when present but empty, unparseable or invalid.
- `parse_literal()`: parse a tag value with ast.literal_eval, logging and returning None on error.
- `get_retention_age()`: fail-open custom age tag parse; None when absent, empty or unparseable.
- `get_min_age()`: return the shorter of two age specs; ties return the second.
- `is_old()`: True when the resource is at least as old as the age; an empty age returns True; the MAX_AGE environment variable can replace a longer age.

Policy (module policy.py). Tags are normalised first; every function takes `Tags`
(a str to str dict) and a `TagSpec` (key to list of allowed values; an empty list means the
key must be present). Functions named for a module are exact copies of that module's behaviour.

- `Tags`: type alias for normalised tags.
- `TagSpec`: type alias for a filter, exception or notags spec.
- `UntaggedMode`: enum of untagged policies, skip, include, skip unless notags.
- `SENSITIVE_YB_TASK`: the protected yb_task values (infra, prod, vpn, ...).
- `normalize_tags()`: AWS Key/Value lists, KMS TagKey/TagValue lists and dict-like tags to `Tags`.
- `is_untagged()`: True for empty tags.
- `skip_untagged()`: apply an `UntaggedMode` to a resource without tags.
- `matches_any_key()`: True when any spec key matches; empty spec is False.
- `matches_all_keys()`: True when every spec key matches; empty spec is True.
- `filter_tags_any_key()`: filter_tags, any key (Azure disk and IP, GCP disk, OCI disk, AWS Elastic IP).
- `filter_tags_all_keys()`: filter_tags, all keys (Azure VM, GCP VM, OCI VM and every Spot VM except AWS).
- `filter_tags_glob_all_keys()`: filter_tags, all keys with fnmatch globs (AWS disk and spot requests).
- `filter_tags_exact_pairs()`: filter_tags, every key-value pair present (AWS KMS).
- `exception_tags_any()`: skip when any exception key matches.
- `notags_all()`: skip when every notags key matches; empty spec never skips.
- `should_skip_by_tags()`: exception_tags any or notags all.
- `aws_eip_should_skip()`: Elastic IP skip, exception tags only (notags never skips there).
- `name_regex_search_matches()`: regex search, any pattern, empty list matches (AWS disk, keypair).
- `exception_regex_search_matches()`: regex search, any pattern, empty list is False (AWS disk, keypair).
- `exception_regex_match_matches()`: regex match anchored at the start, any pattern (GCP disk).
- `filter_substring_matches()`: plain substring any, empty list matches (GCP IP filter_regex).
- `gcp_ip_exception_excludes()`: GCP IP exception_regex as implemented, inverted.
- `nic_name_included()`: Azure NIC name_regex, substring any, empty list is False.
- `nic_name_excluded()`: Azure NIC exception_regex, substring any.
- `compile_patterns()`: compile regexes up front, ValueError on a bad pattern (AWS disk).
- `state_in()`: exact state membership.
- `state_is_substring_of_any()`: Azure disk state test (state is a substring of an allowed entry).
- `status_contains_any_allowed()`: Azure VM state test (an allowed entry is a substring of the status).
- `is_sensitive_yb_task()`: protected yb_task value test.
- `aws_disk_yb_task()`: AWS disk yb_task lookup, key folded for case and dash.
- `aws_disk_has_sensitive_yb_task()`: AWS disk sensitive yb_task test.
- `aws_spot_has_sensitive_yb_task()`: AWS spot sensitive yb_task test, exact keys.
- `aws_is_kubernetes_volume()`: AWS disk Kubernetes CSI volume test.
- `aws_filter_wants_kubernetes()`: filter_tags names a Kubernetes key.
- `aws_disk_skips_kubernetes()`: AWS disk Kubernetes skip decision.
- `gcp_is_managed_or_gke_node()`: GCP Spot VM GKE node or managed instance group member.
- `oci_is_managed_or_oke_node()`: OCI Spot VM OKE or instance pool member.
- `effective_age()`: custom age tag replaces the default age when truthy.
- `age_to_timedelta()`: age spec to timedelta.
- `aws_disk_creation_floor()`: AWS disk CreateTime floor from age, detach_age and custom age.
- `is_older_than_floor()`: AWS disk floor comparison, ignores MAX_AGE.
- `snapshot_old_enough()`: AWS snapshot cutoff comparison.

Cleaner contract (module cleaner.py). Cleaners normalise SDK data into these frozen
dataclasses; mappings are copied into read-only views and sequences become tuples.

- `Operation`: enum of run operations, delete and stop (the --operation_type values).
- `Candidate`: one normalised cloud resource (id, name, region, state, instance type, launch time, console URL, tags, cleaner-specific extra).
- `Skipped`: a resource kept by policy, with the reason.
- `Plan`: candidates to act on, skipped items, related resources and optional owner grouping; what --dry_run computes.
- `Failure`: a resource that failed, with the error text.
- `Result`: outcome of apply, succeeded ids, failures, related resources (NICs, leftover disks) grouped by resource label, and optional owner grouping.
- `RunContext`: dry run flag, operation, parsed CLI options and the caller-supplied now; cleaners never read the clock.
- `Cleaner`: protocol with cloud, kind, supports, a read-only plan and a mutating apply.
- `candidate_ids()`: ids of candidates, in order.

Registry (module registry.py). A factory is a callable taking a RunContext and returning a
Cleaner, usually the cleaner class. Tests create their own registry so registrations never leak.

- `Registry`: holds the factories; one instance is the default registry.
- `register()`: decorator that registers a cleaner factory under a cloud and kind in the default registry.
- `get()`: factory of a cloud and kind from the default registry (import it from crc.core.registry).
- `kinds_for()`: sorted kinds registered for a cloud in the default registry.
- `clouds()`: sorted clouds registered in the default registry.
- `DuplicateCleanerError`: raised when a cloud and kind is registered twice.
- `UnknownCleanerError`: raised by lookups for an unregistered cloud or kind.

Report (module report.py). Pure model and text builders; sending stays in the notifiers.

- `Report`: cloud, operation, dry run, sections, failures and optional owner grouping; builds Slack messages and Influx data from results or plans.
- `Section`: ids of one resource label, with flags for empty-message and link_names behaviour.
- `SlackMessage`: text and link_names flag of one Slack post.
- `InfluxPoint`: measurement (the cloud), resource tag, names field and count field of one point.
- `get_msg()`: Slack text identical to the get_msg method of crc.py.
- `influx_resource_name()`: resource tag of write_influxdb, the label plus an optional suffix.
- `format_owner_ping()`: text of one ping_on_slack message for an owner.
- `NIC`: label of network interfaces.
- `DISK`: label of disks and volumes.
- `VM`: label of virtual machines.
- `IP`: label of IP addresses.
- `KEYPAIR`: label of key pairs.
- `KMS`: label of KMS keys.
- `SNAPSHOT`: label of snapshots.
- `SPOT_INSTANCE_REQUEST`: label of AWS spot requests and their instances.

Selection (module selection.py). Pure port of crc.py main()'s cloud and resource rules.

- `build_selection()`: expand cloud, resource and operation into the ordered (cloud, kind) pairs plus warnings; raises the ValueError messages of crc.py (stop only for vm, --resource all needs --cloud all, --slack_user_label, KMS options, the AWS EBS opt-in breaker).
- `Selection`: the ordered pairs (items) and the warning texts of skipped pairs (warnings).
- `SelectionOptions`: KMS description and user, slack_notify_users and slack_user_label validated with the selection.
- `CLOUDS`: the --cloud all order of crc.py.
- `RESOURCES`: the --resource all order of crc.py (spot resources and vpc are not part of it).
- `EXTRA_RESOURCES`: resources selectable by name only (vpc, spot_instance_requests, spot_instances).

Engine (module engine.py). Orchestration only; Slack and Influx senders are injected.

- `run()`: for each selected pair build the cleaner from a Registry, check supports, plan(), apply() only when not a dry run, build the Report and call the notifiers in order; cleaner errors propagate.
- `RunOutcome`: cloud, kind, plan, result (None in a dry run) and report of one cleaner.
- `Notifier`: protocol with name, fail_open and send(report, context); a fail_open notifier error is logged and swallowed, any other propagates.
- `KIND_LABELS`: report label of each resource kind.

Run log (module logger.py, imported as "from crc.core import logger"). Pure stdlib collector of the
per-run lines of the LOG rungs; the contract is in docs/refactor/LOGGING.md. Log text only: it never
decides what a cleaner selects or deletes.

- `RunLog`: counters and log lines of one cleaner run (methods region, region_skipped, action, skip, fail, finish); a run made by `current()` without `begin()` is unattached and logs nothing.
- `begin()`: start a run, make it current and log the RUN START line (non-empty options as sorted compact JSON); takes an injectable clock.
- `current()`: the current run, or a fresh unattached `RunLog` so cleaners and tests never crash.
- `finish()`: end the current run and log the SKIPPED lines per reason, the ERRORS and FAILED lines when a failure was recorded, then the RESULT line; a no-op when no run began.

## Safety Invariants

- `is_old` with an empty age matches every resource (SAF-006). Destructive callers must
  reject empty ages first with `require_positive_age`.
- Custom age tags: callers that must fail closed use `validated_retention_age`; callers
  using `get_retention_age` alone fail open to `--age` (SAF-008, SAF-009). Do not change
  either in a refactor rung.
- `MAX_AGE` can still shorten a floor inside `is_old`, including to zero (SAF-010).
  Never add another place where it shortens a floor set by another flag.
- This package never imports `crc.aws`, `crc.azu`, `crc.gcp`, `crc.oci`,
  `crc.notifications`, `crc.notify`, `crc.service` or any cloud, Slack or Influx SDK
  (import-linter contracts in `pyproject.toml`).
- Log messages are part of the behaviour; keep their text identical. The per-resource age-check
  lines of `crc.core.age` are logged at DEBUG (BUG-356, LOG-0); levels only change in a LOG rung
  (`docs/refactor/LOGGING.md`).
- `Cleaner.plan()` never mutates cloud state, since `--dry_run` is `plan()` without `apply()`
  (ARCH-005 checks real implementations). `apply()` acts only on items of the plan it is given.
- Report text and Influx data keep `crc.py` strings exactly (label spelling, "disks" in owner
  pings, `Dry Run` wording); tests compare against `crc.py` itself. Do not tidy them in a
  refactor rung.
- The engine calls `apply()` only when `context.dry_run` is False, has no catch-all around
  cleaners and swallows a notifier error only when the notifier is `fail_open` (Influx, and
  Slack sends, which `crc.py` catches per message). Pass notifiers in `crc.py` order: Slack
  first, then Influx. An operation the cleaner does not support raises `ValueError`.
- `build_selection()` raises the AWS EBS breaker before returning any pair, so no other AWS
  resource is selected; call it after the input validators of `main()` to keep error order.
- Registering the same cloud and kind twice raises; lookups of unknown names raise with the
  known names. No import-time cloud calls, no other global state than the default registry.
- Policy keeps every quirk of the catalogue (inverted GCP IP exception, Elastic IP notags that
  never match, KMS multi-value filters that never match, empty `notags` never skips). A quirk
  is only fixed in a named behaviour-fix rung, not while wiring.
- An empty `exception_tags` or `notags` never skips; an untagged resource never matches a
  non-empty `notags`. Wiring must keep the catalogued untagged mode of each module.

## Extension Rules

1. Keep modules pure: typed plain functions, no SDK calls, no clock reads. The only
   environment read is `MAX_AGE` in `is_old`; moving it to the caller is a later rung.
2. Declare every public name in `__all__` of `__init__.py` and list it under Public API.
3. Add table-driven tests under `tests/` for every branch, and never weaken them.
4. Do not edit the import-linter contracts to make an import pass.
