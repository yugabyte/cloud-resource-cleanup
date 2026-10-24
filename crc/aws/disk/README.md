# crc.aws.disk

## Purpose

AWS EBS disk cleaner. Deletes available (unattached) EBS volumes in every region that pass the tag, name and age rules, with an optional CloudWatch detach-age guard. `DiskCleaner` is registered as `aws` / `disk`; `crc/cli` runs it through `crc.core.engine` (route in `crc/cli/routes/aws.py`).

Modules, one per responsibility:

- `cleaner.py`: the `Cleaner` (`plan()` lists and filters, `apply()` deletes) and its registry factory.
- `guard.py`: the region level error policy and the summary lines.
- `client.py`: typed Protocols of the boto3 calls used and the default factories.
- `settings.py`: validated settings (age gates, filters, regex) and age normalisation.
- `volumes.py`: tag, name, state and creation-age decisions for one volume, labels and server side filters.
- `detach.py`: the CloudWatch detach-age guard.

## Public API

- `region_guard()`: region level error policy (connectivity and unauthorised errors skip the region, others are logged and flagged).
- `log_summary()`: the summary lines logged once all regions are done.
- `DiskCleaner`: the Cleaner (supports, read-only plan, mutating apply, partial_succeeded for the report-then-reraise bridge mode, plus plan_region, note_dry_run, apply_region, region_guard and log_summary).
- `DiskRuntime`: frozen dataclass with the injected EC2 and CloudWatch client factories and the region lister.
- `build_disk_cleaner()`: registry factory behind the aws / disk registration; reads filter_tags, exception_tags, notags, name_regex, exception_regex, custom_age_tag_key, age and detach_age from RunContext.options.
- `VolumePaginator`: Protocol of the describe_volumes paginator (paginate).
- `Ec2Client`: Protocol of the two EC2 calls used, get_paginator and delete_volume; tests pass fakes.
- `CloudWatchClient`: Protocol of the one CloudWatch call used, get_metric_data.
- `boto3_ec2()`: default EC2 client factory (bounded timeouts and retries).
- `boto3_cloudwatch()`: default CloudWatch client factory.
- `all_regions()`: default region lister (describe_regions with the connectivity fallback).
- `DiskSettings`: frozen dataclass with the validated filters and age gates of one run.
- `build_settings()`: validate raw options in a fixed order (ages, regexes, age gate, CloudWatch window).
- `normalize_age()`: validate an int or days/hours age, an empty dict when empty, ValueError otherwise.
- `VolumeFilter`: tag, name, state and creation-age decisions for one volume (is_candidate, should_skip, matches_filter_tags).
- `tag_map()`: tag list to a key to value dict.
- `volume_label()`: region/volume-id plus yb_task or Name, the id reported as deleted.
- `server_filters()`: describe_volumes filters (status available, tag:key or tag-key).
- `older_than_floor()`: CreateTime comparison with a floor, ignoring MAX_AGE.
- `DetachGuard`: the CloudWatch detach-age guard (drop_recently_attached).
- `cloudwatch_period()`: metric period of a window in seconds.
- `build_queries()`: GetMetricData queries of a batch and their ids per volume.
- `get_metric_data()`: all result pages of one batch.
- `merge_metric_results()`: merge pages per query id (Values concatenated, last status).
- `volume_recently_attached()`: skip decision and reason from the merged results.

## Safety Invariants

- `plan()` and `plan_region()` only call `get_paginator("describe_volumes").paginate` and CloudWatch `get_metric_data`; `delete_volume` is called from `apply()` / `apply_region()` only and never in a dry run (SAF-006, SAF-014: the AWS EBS cleanup is opt-in through `--cloud aws --resource disk`).
- An age gate is required in the constructor (`age` and/or `detach_age`, SAF-006); CreateTime is always compared with a floor that ignores `MAX_AGE` (SAF-010); `detach_age` is limited to the 455 day CloudWatch retention.
- Fail closed: a missing CreateTime, an invalid custom age tag, an incomplete or failed CloudWatch answer keeps the volume. Attached and Multi-Attach volumes, sensitive `yb_task` values and Kubernetes CSI volumes (without a kubernetes filter) are never deleted; untagged volumes are skipped unless `notags` is set.
- BUG-282 (fixed): every tag whose key folds (case, `-` to `_`) to `yb_task` is judged; one sensitive value keeps the volume. The label still shows the first folded value.
- BUG-283 (wontfix: test environments; a volume detached from a stopped instance may be deleted early; the floor below stays): CloudWatch is blind to attachments to stopped instances, so when both `age` and `detach_age` are set a volume must also be older than the effective age (custom age tag or `age`) plus `detach_age`; a younger one is kept (DEBUG line, skip reason `detach_unknown_too_young`). Detach-only runs (no `age`) keep the `detach_age` floor: the real detach time is not available without CloudTrail.
- Region errors: connectivity errors skip the region, `AuthFailure`, `UnauthorizedOperation`, `OptInRequired` and `InvalidClientTokenId` skip it with a warning, every other error is logged and flagged and never raised. `describe_regions` errors (other than connectivity) propagate. Per volume errors are isolated and logged (CLEANUP_SAFETY.md).
- `plan()` lists every region before `apply()` deletes, so delete calls and `Deleted volume` lines come after all listing lines; `plan()` also logs the dry-run lines.
- Log lines, labels (`region/volume-id yb_task=...`) and quirks: see `docs/refactor/BUGS.md` (`BUG-004`, `BUG-022`, `BUG-282` to `BUG-296`; `BUG-282` is fixed and `BUG-283` is wontfix).
- Logging (BUG-356/361/362): the per-volume `Skipping volume ...` INFO lines are DEBUG with the same text (warnings keep their level); REGION, ACTION (`deleted` / `would_delete`), skip counts and failures (delete errors, per-volume and region errors, CloudWatch lookup errors) go to `crc.core.logger.current()`; an unauthorised or connectivity skip of a region is a `region_skipped`.

## Extension Rules

1. Behaviour changes need a `BUG-NNN` row in `docs/refactor/BUGS.md` and a test that fails without them.
2. Keep filtering in `crc.core.policy` and age in `crc.core.age`; no `re`, clock reads or environment reads here (ARCH-003): `now` comes from `RunContext`.
3. Keep every mutating call in `apply_region()`; `plan()` and what it calls stay read-only (ARCH-005).
4. Changes need `tests/test_aws_disk_parity.py` (oracle `tests/legacy/aws_disk.txt`, never edit it), `tests/test_aws_disk_cleaner.py` and `tests/test_aws_disk_engine.py` to stay green.
5. Every file stays at most 250 lines; split by responsibility and give a new subpackage its own README.md.
