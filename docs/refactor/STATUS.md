# Refactor status

Bugs and quirks: new ones go to `docs/refactor/BUGS.md` (R2c onwards). The older lists below stay as they
are; `BUGS.md` already contains all of them.

| Rung | State | Notes |
|------|-------|-------|
| R0a | done, reviewed: APPROVE | Added pyproject (ruff, mypy strict, import-linter, pytest config), requirements-dev.txt, Makefile `check`, Cursor environment, AGENTS.md/CLAUDE.md, ARCHITECTURE.md, CODEOWNERS, Cursor rules and project skill. Review (`docs/refactor/reviews/R0a.md`) replaced the whole-tree exemptions with a per-file legacy list (new files are checked), added mypy `files` guard; 110 tests before and after. |
| R0b | done, reviewed: APPROVE | Added `tests/archtest/` (ARCH-001..005, SAF-LOCK, each with self-tests, README, `legacy_baseline.txt`, `safety_lock.json`, owner-only `update_safety_lock.py`), `[SAF-001]`..`[SAF-046]` tags in `CLEANUP_SAFETY.md`, pinned `requirements-dev.txt`, `.github/workflows/ci.yml` (Python 3.11, `make check`), pointers in AGENTS.md and ARCHITECTURE.md. Review (`docs/refactor/reviews/R0b.md`) removed 4 wrong test mappings in `safety_lock.json` (`SAF-015`, `SAF-032`, `SAF-034`) and moved `SAF-034` to `gaps`. No change to `crc.py`, `crc/` or existing tests. pytest: 110 existing + 80 archtest = 190 passed. 30 of 46 SAF IDs locked to 61 existing test entries; 16 gaps. |
| R1a | done, reviewed: APPROVE | Moved age handling out of `crc/service.py` into new pure `crc/core/age.py` (`AgeSpec`, `AGE_KEYS`, `require_positive_age`, `lookup_custom_age_tag`, `validated_retention_age`, `parse_literal`, `get_retention_age`, `get_min_age`, `is_old`) with `crc/core/__init__.py` (`__all__`) and `crc/core/README.md` (ARCH-002). `Service` keeps every method and signature and delegates to `crc.core.age`; `crc/service.py` stays in the legacy ratchet. Enabled the two import-linter contracts from the TODO (core must not import `crc.aws/azu/gcp/oci/notifications/service`, nor boto3, botocore, azure, google, oci, slack_sdk, influxdb_client; `include_external_packages = true` added so SDK names are checked). Added `tests/test_core_age.py`. Review (`docs/refactor/reviews/R1a.md`) added coverage for the `not (set(age) & AGE_KEYS)` branch in `require_positive_age`. No behaviour change: an ad hoc differential run of old vs new `Service` over 2758 input combinations (return values, exception types and messages, log records) was identical. pytest: 190 before, 348 after (190 existing unchanged + 158 new). Policy (tag/regex filtering) untouched. |
| R1b | done, reviewed: APPROVE | Added `docs/refactor/FILTER_VARIANTS.md` (how every module under `crc/aws`, `azu`, `gcp`, `oci` filters today, with `file:line`, plus a Variants section) and pure `crc/core/policy.py`: `normalize_tags` (AWS Key/Value and KMS TagKey/TagValue lists, dict-like and GCP map tags), one explicitly named function per variant (`filter_tags_any_key`, `filter_tags_all_keys`, `filter_tags_glob_all_keys`, `filter_tags_exact_pairs`, `should_skip_by_tags`, `aws_eip_should_skip`, regex search / match / substring variants, Azure state substring variants, yb_task, Kubernetes, GKE / MIG, OKE, AWS disk age floor, snapshot cutoff, `UntaggedMode`). `crc/core/__init__.py` `__all__` and `crc/core/README.md` synced (ARCH-002). Added `tests/test_core_policy.py` (table-driven, every function and variant). Review (`docs/refactor/reviews/R1b.md`) verified all citations and quirks and tightened KMS/Azure/GCP disk notes in `FILTER_VARIANTS.md`. Nothing wired; no module outside `crc/core` changed, no import-linter or archtest change. pytest: 348 before, 553 after (348 existing unchanged + 205 new). |
| R2a | done, reviewed: APPROVE | Added the core contracts, nothing wired (`crc.py` and every module outside `crc/core` unchanged). `crc/core/cleaner.py`: `Cleaner` protocol (`cloud`, `kind`, `supports`, read-only `plan(context)`, `apply(plan, context)`), `Operation` (StrEnum delete/stop), frozen slotted `Candidate`, `Skipped`, `Plan` (with `related` and `by_owner`), `Failure`, `Result` (succeeded, failed, related by resource label, `by_owner`), `RunContext` (dry_run, operation, options, caller-supplied `now`); mappings are copied into read-only views. `crc/core/registry.py`: `Registry` class plus default instance and `register(cloud, kind)`, `get`, `kinds_for`, `clouds`, `DuplicateCleanerError`, `UnknownCleanerError`. `crc/core/report.py`: `Report` (from `Result` or `Plan`, including `by_owner`), `Section`, `SlackMessage`, `InfluxPoint`, `get_msg`, `influx_resource_name`, `format_owner_ping`, label constants; pure, strings checked against `crc.py` (loaded with importlib as another module name). `crc/core/__init__.py` `__all__`, `crc/core/README.md` (ARCH-002) and the `ARCHITECTURE.md` cleaner/registry/add-a-cleaner text synced (the task asked for the `ARCHITECTURE.md` edit; it is a CODEOWNERS file). Review (`docs/refactor/reviews/R2a.md`) added `by_owner` and `Plan.related`, mapped `SPOT_INSTANCE_REQUEST` to `VM` for Influx, and matched Azure VM NIC Slack/Influx parity with `crc.py`. pytest: 553 before, 767 after (553 existing unchanged + 214 new: 15 cleaner, 12 registry, 187 report). `make check` green on Python 3.12.3 (3.11 not installed here). |
| R2b | done, reviewed: APPROVE | Added the engine, nothing wired (`crc.py` and every module outside `crc/core` unchanged). `crc/core/engine.py`: `run(selection, context, registry, notifiers=())` builds each cleaner from the registry, checks `supports(operation)` (raises `ValueError` when unsupported), calls `plan()`, calls `apply()` only when `context.dry_run` is False, builds the `Report` (`from_plan` in dry run, `from_result` otherwise), calls the notifiers in the given order and returns `list[RunOutcome]` (cloud, kind, plan, result or `None`, report). No catch-all: cleaner exceptions propagate. `Notifier` protocol (`name`, `fail_open`, `send(report, context)`): a `fail_open` notifier error is logged (`logging.error`) and swallowed, any other propagates. `KIND_LABELS` maps kinds to report labels. `crc/core/selection.py`: pure `build_selection(cloud, resource, operation, options)` returning `Selection(items, warnings)`; same `ValueError` messages and order as `crc.py main()` (stop only for vm, `--resource all` needs `--cloud all`, `--slack_user_label`, KMS options, AWS EBS breaker, spot skips with the exact warning texts), `CLOUDS` / `RESOURCES` order copied from `crc.py`. Tests compare with `crc.py main()` itself (loaded under another name, `CRC` replaced by a recorder). `crc/core/__init__.py` `__all__` and `crc/core/README.md` synced (ARCH-002). Review (`docs/refactor/reviews/R2b.md`) fixed the vacuous EBS breaker call-count assertion and replaced copied warning literals in `tests/test_core_selection.py` with direct `crc.py main()` comparisons (including an exhaustive 5x11x2 grid), and added cleaner `init` failure and direct `crc.py` notifier order / Influx fail-open comparison tests in `tests/test_core_engine.py`. pytest: 767 before, 984 after (767 existing unchanged + 217 new: 25 engine, 192 selection). `make check` steps (ruff, mypy, lint-imports, pytest) green on Python 3.12 (3.11 not installed here). |
| R2c | done, reviewed: round 1 APPROVE, round 2 (Opus) APPROVE | Docs and one archtest rule only; no change to `crc.py`, `crc/**` code or existing tests apart from the ARCH-002 self-tests. Added `docs/refactor/BUGS.md` (39 rows: 2 critical, 7 high, 9 medium, 21 low, all open; round 2 added BUG-040, now 40 rows, low 22) with the `main` file:line of every bug and quirk from the R1a, R1b, R2a, R2b lists, `FILTER_VARIANTS.md` section 7 and the three Azure VM bugs, each re-checked with `git show main:<path>`. Added `README.md` (four ARCH-002 sections) to `crc/`, `crc/aws`, `crc/azu`, `crc/gcp`, `crc/oci`, `tests/`, `docs/`, `docs/refactor/`, `docs/refactor/reviews/`, `.cursor/rules/`, `.cursor/skills/crc-python-refactor/`; the four cloud READMEs state legacy, not yet refactored, with SAF IDs, `FILTER_VARIANTS.md` sections and `BUGS.md` rows. ARCH-002 now requires `README.md` with the four sections in every directory that contains files directly or in subdirectories (explicit `README_EXCLUDED_DIRS` and `README_EXEMPT_DIRS` constants); legacy-only packages skip the Public API symbol sync, non-legacy packages keep it. `AGENTS.md` hard rule 11 (BUGS.md) and the `tests/archtest/README.md` row. Review (`docs/refactor/reviews/R2c.md`) fixed `directories_with_files` in `tests/archtest/readmes.py` so parent directories with files only in subdirectories (such as `docs/`) still fire `ARCH-002` if their `README.md` is removed, added `.cursor/skills` to `README_EXEMPT_DIRS`, and added self-tests proving every checked repo directory fires when its `README.md` is removed. Round 2 (Opus) replaced an unjustified `# type: ignore` in that self-test with a real return type and recorded BUG-040 (`--cloud all --resource snapshot` runs the AWS snapshot cleanup once per cloud). pytest: 984 before, 996 after. `make check` green on Python 3.12 (3.11 not installed here). |
| R3 | done, reviewed: round 1 APPROVE, round 2 (Opus) APPROVE | Moved Slack and InfluxDB sending into `crc/notify`, no behaviour change. `crc/notify/slack.py`: `SlackNotifier(client, channel)` (`send(report, context)`, `post`, `ping_owners`, `lookup_user_by_email`, `user_groups`, `send_cleanup`; `name="slack"`, `fail_open=False`) plus the whole content of `crc/notifications.py` moved and typed (`notify_cleanup`, chunking, `human_readable_timedelta`, `send_slack_message`). `crc/notify/influx.py`: `InfluxNotifier(client, settings)` (`send`, `write`; `fail_open=True`), `InfluxSettings` protocol, `InfluxConfig`. `crc/notify/README.md` (ARCH-002) and `__init__.py` (`__all__`). `crc/notifications.py` is now a thin re-export shim and left both legacy ratchet lists (the ratchet shrank by one entry each, nothing added); `crc.py` stays in the ratchet. In `crc.py` the `notify_*` methods, `ping_on_slack`, `get_msg`, `slack_lookup_user_by_email`, `get_user_groups_list` and `write_influxdb` keep names and signatures and delegate to `crc.notify` and `crc.core.report`; both `notify_deleted_nic_via_slack` definitions are kept (BUG-028). import-linter: two new contracts (`crc.notify` must not import `crc.aws/azu/gcp/oci/service`; `crc.core` must not import `crc.notify`), 5 contracts kept. Tests: `tests/test_notify_slack.py` (70), `tests/test_notify_influx.py` (16), `tests/test_notify_parity.py` (992 after review: every notify path x 4 clouds x dry run x channel `chan`/`#chan`/`None`, Slack failure, owner pings with every lookup failure mode, `write_influxdb` with and without suffix and connection, plus direct `SlackNotifier`/`InfluxNotifier` and `engine.run` comparisons against the legacy oracle on fake-client calls, printed text and raised errors). The oracle is `tests/legacy_crc_notify_reference.txt`, a frozen copy of the old `crc.py` methods (a `.txt` so it is not linted and never imported by the product). Review (`docs/refactor/reviews/R3.md`) preserved `len(operated_list)` in `CRC.get_msg` for `None` inputs and extended `tests/test_notify_parity.py` to assert calls, stdout and errors on `SlackNotifier.send`, `InfluxNotifier.send` and `engine.run` across failure and owner-ping paths. New bugs: `BUG-041`, `BUG-042` (`BUGS.md`, 42 rows). Round 2 (Opus) made `CRC.ping_on_slack` fetch the user groups and `len()` every owner value before building the `Report` (same call order and error text as `main` for a `None` value or a non-dict), added `test_ping_on_slack_bad_input_matches_legacy`, and recorded `BUG-043` and `BUG-044` (44 rows); non-list id containers are now rendered as lists (NIT, no product path produces one). pytest: 996 before, 2074 after round 1, 2077 after round 2 (996 existing unchanged + 1081 new). `make check` green on Python 3.12 (3.11 not installed here). |
| R4a | done, reviewed: round 1 APPROVE, round 2 (Opus) APPROVE | Refactored `crc/aws/keypairs.py` into `KeyPairCleaner` (`Cleaner`, `@register("aws", "keypair")` via `build_keypair_cleaner`; read-only `plan()`, `apply()` deletes) with a typed `KeyPairClient` Protocol and an injected `KeyPairRuntime` (client factory, region lister, clock). Filtering goes through `crc.core.policy.name_regex_search_matches` / `exception_regex_search_matches`, age through `crc.core.age.is_old`. `KeyPairs` keeps its constructor (one optional extra `runtime` argument), `deleted_keypairs`, `get_deleted`, `count`, `delete()` and runs plan then apply per region, so the SDK call order, logs, dry run and errors equal `main`; `crc.py` is unchanged. `crc/aws/keypairs.py` left both legacy ratchet lists (shrank by one each; boto3/botocore got a per-SDK `ignore_missing_imports` override). Oracle: `tests/legacy/aws_keypairs.txt` (exact `git show main:crc/aws/keypairs.py`); `tests/test_aws_keypair_parity.py` (48 cases x 2 entry points, plus 18 quirk checks after review) compares ordered SDK calls, `get_deleted`, `count`, log records, stdout and errors; `tests/test_aws_keypairs.py` (10) covers `plan()` and `apply()`. Review (`docs/refactor/reviews/R4a.md`) emitted the summary warnings from `KeyPairCleaner.plan` (dry run) and `KeyPairCleaner.apply`, used `options.get("age")` in `build_keypair_cleaner`, and added `KeyPairCleaner` protocol parity and `describe_regions` (`SAF-001`, `SAF-004`) cases. New bugs `BUG-045` to `BUG-048` (48 rows). pytest: 2077 before, 2148 before review, 2201 after review (+124). Round 2 (Opus) found no blocking issue, recorded `BUG-049` and `BUG-050` (50 rows), and left a NIT: a `KeyName` of `None` is treated as `""` instead of raising `TypeError` as on `main` (this cannot happen with botocore). pytest unchanged at 2201. `make check` green on Python 3.12. |
| R4b | done, reviewed: round 1 APPROVE, round 2 (Opus) APPROVE | Refactored `crc/aws/elastic_ips.py` into `ElasticIpCleaner` (`Cleaner`, `@register("aws", "ip")` via `build_elastic_ip_cleaner`; read-only `plan()` that only calls `describe_addresses`, `apply()` calls `release_address`) with a typed `ElasticIpClient` Protocol and an injected `ElasticIpRuntime` (client factory, region lister; no clock because the module has no age gate, so `crc.core.age` is not used). Filtering goes through `crc.core.policy.filter_tags_any_key` and `policy.aws_eip_should_skip`, one tag at a time so the early return, the lazy `Value` read and the `KeyError` on a tag without `Key` or `Value` stay as on `main`; the notags `TypeError` (BUG-011) is raised by the cleaner itself because the policy function takes no notags. `ElasticIPs` keeps its constructor (one optional extra `runtime` argument), `deleted_ips`, `get_deleted`, `count`, `delete()` and `_should_skip_instance`, and runs plan then apply per region, so SDK call order, logs, dry run and errors equal `main`; `crc.py` is unchanged. `crc/aws/elastic_ips.py` left both legacy ratchet lists (one entry each; the per-SDK boto3/botocore override from R4a covers the imports). Oracle: `tests/legacy/aws_elastic_ips.txt` (exact `git show main:crc/aws/elastic_ips.py`); `tests/test_aws_ip_parity.py` (59 cases x 2 entry points: adapter against the oracle on full ordered calls, logs, stdout, errors; registered Cleaner path with `plan()` then `apply()` on results, errors, stdout, release calls and log records; plus 23 quirk checks, 42 `_should_skip_instance` combinations and 2 factory and runtime-injection checks after review); `tests/test_aws_elastic_ips.py` (11) covers `plan()` and `apply()`. The Cleaner path logs the same summary lines as `main`. Review (`docs/refactor/reviews/R4b.md`) preserved `KeyError('Value')` in `_pair` when a tag without `Value` matches a key with a non-empty value list, asserted `new_releases == old_releases` across all non-`fails_in_plan` error cases, added `apply` connectivity-error unit coverage, and sorted `BUG-053` into the `medium` risk group in `BUGS.md`. New bugs `BUG-051` to `BUG-053` (53 rows). pytest: 2201 before, 2383 before review, 2397 after review (+196, all 2201 existing unchanged). Round 2 (Opus) made `ElasticIpCleaner._select_on_filter` record an address at each matching tag as `main` does, so a missing `AllocationId` raises before a malformed later tag (2 parity cases plus quirk checks), and recorded `BUG-054` (54 rows). pytest 2403 (+6). `make check` green on Python 3.12. |
| R4c | done, reviewed: round 1 APPROVE, round 2 (Opus) APPROVE | Refactored `crc/aws/kms.py` into `KmsCleaner` (`Cleaner`, `@register("aws", "kms")` via `build_kms_cleaner`; read-only `plan()` calls `list_keys`, `list_resource_tags`, `describe_key`, `get_key_policy` only, `apply()` calls `schedule_key_deletion`) with a typed `KmsClient` / `KmsPaginator` Protocol, a frozen `KmsSettings` and an injected `KmsRuntime` (client factory, clock). Filtering goes through `crc.core.policy.filter_tags_exact_pairs` (one tag at a time, so repeated `TagKey` entries keep every value as the set on `main`), `policy.exception_tags_any` (per tag, so the `KeyError` of BUG-015 is kept) and `policy.effective_age`; age through `crc.core.age.get_retention_age` and `is_old`. `Kms` keeps its constructor (one optional extra `runtime` argument), `kms_keys_to_delete`, `get_deleted`, `delete()` and `_should_skip_kms`, and runs the cleaner in the call order of `main`; `crc.py` is unchanged (`Kms` has no `count` on `main`). `crc/aws/kms.py` left both legacy ratchet lists (one entry each, nothing added). The module is single-region (`us-west-2`) and lists no regions, so there is no `describe_regions` path. The Cleaner path logs the same lines as `main`: `plan()` logs the dry-run summary, `apply()` the real one, and an unreachable region is marked in `Plan.skipped` so `apply()` stays silent as `main` does. Oracle: `tests/legacy/aws_kms.txt` (exact `git show main:crc/aws/kms.py`); `tests/test_aws_kms_parity.py` (106 cases x 2 entry points on ordered SDK calls, logs, stdout, errors and `get_deleted`, plus 30 quirk checks, 42 `_should_skip_kms` combinations and 4 helper checks after review); `tests/test_aws_kms.py` (12) covers `plan()` and `apply()`. Review (`docs/refactor/reviews/R4c.md`) made `_has_all_pairs` construct `response_set` and `filter_set` eagerly as on `main` before checking pairs via `policy.filter_tags_exact_pairs`, added 2 parity and quirk cases, fixed the `BUGS.md` row count, and recorded `BUG-059`. New bugs `BUG-055` to `BUG-059` (59 rows). Round 2 (Opus) verified the round 1 fix by reverting it (its 6 new tests fail), changed no code and recorded `BUG-060` (`Principal.AWS` list or `"*"` never matches) and `BUG-061` (the bare `except:` swallows Ctrl-C and the run still schedules), 61 rows. pytest: 2403 before, 2697 before review, 2703 after review (+300, all 2403 existing unchanged). `make check` green on Python 3.12. |
| GCP-1 | done, reviewed: VERDICT: APPROVE; ROUND 2 VERDICT: APPROVE | Lane rung merged into `refactor/python-agentic`. Details: `docs/refactor/reviews/GCP-1.md`. New bugs: BUG-062, BUG-063, BUG-064, BUG-065, BUG-066, BUG-067, BUG-068, BUG-069, BUG-070, BUG-071, BUG-072, BUG-073. |
| AZ-1 | done, reviewed: VERDICT: APPROVE; ROUND 2 VERDICT: APPROVE | Lane rung merged into `refactor/python-agentic`. Details: `docs/refactor/reviews/AZ-1.md`. New bugs: BUG-074, BUG-075, BUG-076, BUG-077, BUG-078, BUG-079, BUG-080, BUG-081, BUG-082. |
| AWS-1 | done, reviewed: VERDICT: APPROVE; ROUND 2 VERDICT: APPROVE | Lane rung merged into `refactor/python-agentic`. Details: `docs/refactor/reviews/AWS-1.md`. New bugs: BUG-083, BUG-084, BUG-085, BUG-086, BUG-087, BUG-088, BUG-089, BUG-090, BUG-091, BUG-092, BUG-093, BUG-094, BUG-095. |
| OCI-1 | done, reviewed: VERDICT: APPROVE; ROUND 2 VERDICT: APPROVE | Lane rung merged into `refactor/python-agentic`. Details: `docs/refactor/reviews/OCI-1.md`. New bugs: BUG-096, BUG-097, BUG-098, BUG-099, BUG-100, BUG-101, BUG-102, BUG-103, BUG-104, BUG-105, BUG-106, BUG-107, BUG-108, BUG-109, BUG-110. |
| AZ-2 | done, reviewed: VERDICT: APPROVE; ROUND 2 VERDICT: APPROVE | Lane rung merged into `refactor/python-agentic`. Details: `docs/refactor/reviews/AZ-2.md`. New bugs: BUG-111, BUG-112, BUG-113, BUG-114, BUG-115, BUG-116, BUG-117, BUG-118, BUG-119, BUG-120, BUG-121, BUG-122, BUG-123. |
| GCP-2 | done, reviewed: VERDICT: APPROVE; ROUND 2 VERDICT: APPROVE | Lane rung merged into `refactor/python-agentic`. Details: `docs/refactor/reviews/GCP-2.md`. New bugs: BUG-124, BUG-125, BUG-126, BUG-127, BUG-128, BUG-129, BUG-130, BUG-131, BUG-132, BUG-133, BUG-134, BUG-135, BUG-136, BUG-137, BUG-138, BUG-139, BUG-140, BUG-141. |
| AWS-2 | done, reviewed: VERDICT: APPROVE; ROUND 2 VERDICT: APPROVE | Lane rung merged into `refactor/python-agentic`. Details: `docs/refactor/reviews/AWS-2.md`. New bugs: BUG-142, BUG-143, BUG-144, BUG-145, BUG-146, BUG-147, BUG-148, BUG-149, BUG-150, BUG-151, BUG-152, BUG-153, BUG-154, BUG-155, BUG-156, BUG-157. |
| OCI-2 | done, reviewed: VERDICT: APPROVE; ROUND 2 VERDICT: APPROVE | Lane rung merged into `refactor/python-agentic`. Details: `docs/refactor/reviews/OCI-2.md`. New bugs: BUG-158, BUG-159, BUG-160, BUG-161, BUG-162, BUG-163, BUG-164, BUG-165, BUG-166, BUG-167, BUG-168, BUG-169, BUG-170, BUG-171. |
| AZ-3 | done, reviewed: VERDICT: APPROVE; ROUND 2 VERDICT: APPROVE | Lane rung merged into `refactor/python-agentic`. Details: `docs/refactor/reviews/AZ-3.md`. New bugs: BUG-172, BUG-173, BUG-174, BUG-175, BUG-176, BUG-177, BUG-178, BUG-179, BUG-180, BUG-181, BUG-182. |
| GCP-3 | done, reviewed: VERDICT: APPROVE; ROUND 2 VERDICT: APPROVE | Lane rung merged into `refactor/python-agentic`. Details: `docs/refactor/reviews/GCP-3.md`. New bugs: BUG-183, BUG-184, BUG-185, BUG-186, BUG-187, BUG-188, BUG-189, BUG-190, BUG-191, BUG-192, BUG-193, BUG-194, BUG-195, BUG-196, BUG-197. |
| AZ-4 | done, reviewed: VERDICT: APPROVE; ROUND 2 VERDICT: APPROVE | Lane rung merged into `refactor/python-agentic`. Details: `docs/refactor/reviews/AZ-4.md`. New bugs: BUG-198, BUG-199, BUG-200, BUG-201, BUG-202, BUG-203, BUG-204, BUG-205, BUG-206, BUG-207, BUG-208, BUG-209, BUG-210, BUG-211, BUG-212. |
| AWS-3 | done, reviewed: VERDICT: APPROVE; ROUND 2 VERDICT: APPROVE | Lane rung merged into `refactor/python-agentic`. Details: `docs/refactor/reviews/AWS-3.md`. New bugs: BUG-213, BUG-214, BUG-215, BUG-216, BUG-217, BUG-218, BUG-219, BUG-220, BUG-221, BUG-222, BUG-223, BUG-224, BUG-225, BUG-226, BUG-227. |
| GCP-4 | done, reviewed: VERDICT: APPROVE; ROUND 2 VERDICT: APPROVE | Lane rung merged into `refactor/python-agentic`. Details: `docs/refactor/reviews/GCP-4.md`. New bugs: BUG-228, BUG-229, BUG-230, BUG-231, BUG-232, BUG-233, BUG-234, BUG-235, BUG-236, BUG-237, BUG-238, BUG-239, BUG-240, BUG-241, BUG-242, BUG-243. |
| AZ-5 | done, reviewed: VERDICT: APPROVE; ROUND 2 VERDICT: APPROVE | Lane rung merged into `refactor/python-agentic`. Details: `docs/refactor/reviews/AZ-5.md`. New bugs: BUG-244, BUG-245, BUG-246, BUG-247, BUG-248, BUG-249, BUG-250, BUG-251, BUG-252, BUG-253, BUG-254. |
| OCI-3 | done, reviewed: VERDICT: APPROVE; ROUND 2 VERDICT: APPROVE | Lane rung merged into `refactor/python-agentic`. Details: `docs/refactor/reviews/OCI-3.md`. New bugs: BUG-255, BUG-256, BUG-257, BUG-258, BUG-259, BUG-260, BUG-261, BUG-262, BUG-263, BUG-264, BUG-265, BUG-266, BUG-267, BUG-268, BUG-269, BUG-270, BUG-271. |
| AWS-5 | done, reviewed: VERDICT: APPROVE; ROUND 2 VERDICT: APPROVE | Lane rung merged into `refactor/python-agentic`. Details: `docs/refactor/reviews/AWS-5.md`. New bugs: BUG-272, BUG-273, BUG-274, BUG-275, BUG-276, BUG-277, BUG-278, BUG-279, BUG-280, BUG-281. |
| AWS-4 | done, reviewed: VERDICT: APPROVE; ROUND 2 VERDICT: APPROVE | Lane rung merged into `refactor/python-agentic`. Details: `docs/refactor/reviews/AWS-4.md`. New bugs: BUG-282, BUG-283, BUG-284, BUG-285, BUG-286, BUG-287, BUG-288, BUG-289, BUG-290, BUG-291, BUG-292, BUG-293, BUG-294, BUG-295, BUG-296. |
| CLI-1 | done, reviewed: VERDICT: APPROVE; ROUND 2 VERDICT: APPROVE | Lane rung merged into `refactor/python-agentic`. Details: `docs/refactor/reviews/CLI-1.md`. New bugs: BUG-333, BUG-334, BUG-335, BUG-336, BUG-337, BUG-338, BUG-339. |
| CLI-2 | done, reviewed: VERDICT: APPROVE; ROUND 2 VERDICT: APPROVE | Lane rung merged into `refactor/python-agentic`. Details: `docs/refactor/reviews/CLI-2.md`. New bugs: BUG-340, BUG-341, BUG-342, BUG-343. |
| CLI-3 | done, reviewed: VERDICT: APPROVE; ROUND 2 VERDICT: APPROVE | Lane rung merged into `refactor/python-agentic`. Details: `docs/refactor/reviews/CLI-3.md`. New bugs: BUG-344, BUG-345, BUG-346, BUG-347, BUG-348, BUG-349, BUG-350. |
| FINAL | done, reviewed: VERDICT: APPROVE; ROUND 2 VERDICT: APPROVE | Lane rung merged into `refactor/python-agentic`. Details: `docs/refactor/reviews/FINAL.md`. New bugs: none. |
| ST-OCI-DISK | done, reviewed: VERDICT: APPROVE; ROUND 2 VERDICT: APPROVE | Lane rung merged into `refactor/python-agentic`. Details: `docs/refactor/reviews/ST-OCI-DISK.md`. New bugs: BUG-353, BUG-354. |
| ST-OCI-VM | done, reviewed: VERDICT: APPROVE; ROUND 2 VERDICT: APPROVE | Lane rung merged into `refactor/python-agentic`. Details: `docs/refactor/reviews/ST-OCI-VM.md`. New bugs: BUG-352. |
| ST-AWS-VM | done, reviewed: VERDICT: APPROVE; ROUND 2 VERDICT: APPROVE | Lane rung merged into `refactor/python-agentic`. Details: `docs/refactor/reviews/ST-AWS-VM.md`. New bugs: BUG-351. |
| ST-AWS-VPC | done, reviewed: VERDICT: APPROVE; ROUND 2 VERDICT: APPROVE | Lane rung merged into `refactor/python-agentic`. Details: `docs/refactor/reviews/ST-AWS-VPC.md`. New bugs: none. |
| PL-CORE | done, reviewed: VERDICT: APPROVE; ROUND 2 VERDICT: APPROVE | Lane rung merged into `refactor/python-agentic`. Details: `docs/refactor/reviews/PL-CORE.md`. New bugs: BUG-327, BUG-328, BUG-329, BUG-330, BUG-331, BUG-332. |
| PL-AZ | done, reviewed: VERDICT: APPROVE; ROUND 2 VERDICT: CHANGES_REQUESTED | Lane rung merged into `refactor/python-agentic`. Details: `docs/refactor/reviews/PL-AZ.md`. New bugs: BUG-318, BUG-319, BUG-320, BUG-321, BUG-322, BUG-323, BUG-324, BUG-325, BUG-326. |
| PL-OCI | done, reviewed: VERDICT: APPROVE; ROUND 2 VERDICT: APPROVE | Lane rung merged into `refactor/python-agentic`. Details: `docs/refactor/reviews/PL-OCI.md`. New bugs: BUG-308, BUG-309, BUG-310, BUG-311, BUG-312, BUG-313, BUG-314, BUG-315, BUG-316, BUG-317. |
| PL-AWS | done, reviewed: VERDICT: APPROVE; ROUND 2 VERDICT: APPROVE | Lane rung merged into `refactor/python-agentic`. Details: `docs/refactor/reviews/PL-AWS.md`. New bugs: BUG-302, BUG-303, BUG-304, BUG-305, BUG-306, BUG-307. |
| PL-GCP | done, reviewed: VERDICT: APPROVE; ROUND 2 VERDICT: APPROVE | Lane rung merged into `refactor/python-agentic`. Details: `docs/refactor/reviews/PL-GCP.md`. New bugs: BUG-297, BUG-298, BUG-299, BUG-300, BUG-301. |
| ARCH-003 allowlist | done (owner decision, local driver commit `f8b6ca2`) | The owner approved option (a): `crc/azu/_base.py` and `crc/oci/_base.py` may read `os.environ` (credentials are still read when an Azure or OCI cleaner is built, as on `main`). `ARCH003_ALLOWLIST` in `tests/archtest/bans.py` holds exactly these two `env` entries and `test_arch003_allowlist_is_explicit_and_owner_approved` pins them. Both modules left the legacy ratchet; it now holds only the five hash-locked safety test files. pytest 42213. |
| Final (docs) | done, reviewed: Round 1 (Argon) APPROVE, Round 2 (Opus) APPROVE | Local driver (`4ecf41a`): `AGENTS.md`, `PLAN.md` (Final scope, deferred items, Behaviour-fix rungs section), `ARCHITECTURE.md` ratchet paragraph, `.cursor/rules/00-map.mdc`, the project skill (`SKILL.md`, `REVIEW_CHECKLIST.md`, README) and the new `.cursor/skills/crc-python-refactor/BEHAVIOUR_FIX.md` now say the refactor is done and how behaviour-fix rungs keep the parity net (frozen oracles, per-case divergence marked with the bug ID, owner relock for locked tests). Cursor rung (`7bd3202`) + Round 1 review (`docs/refactor/reviews/FINAL.md`): swept every `README.md` under `crc/`, `tests/`, `tests/legacy/` and `docs/refactor/` so no refactored code is called legacy/partly refactored, every oracle and parity test is listed, and all stale `docs/refactor/bugs/pending/*.md` paths are replaced with folded `BUGS.md` IDs. pytest 42213. |
| LOG-0 | done, reviewed: Round 1 (Argon) APPROVE, Round 2 (Opus) APPROVE | Logging rung family LOG, core lane (`docs/refactor/LOGGING.md`). New `crc/core/logger.py` (`begin`, `current`, `finish`, `RunLog`: RUN START, REGION, ACTION, SKIPPED, ERRORS, FAILED, RESULT lines, injectable clock), wired in `crc/cli/dispatch.py` around every cleaner pass in a try/finally; per-resource age-check lines of `crc/core/age.py` are DEBUG with unchanged text. `tests/helpers_log_fix.py` (`expected_logs`, BUG-356 rule) used by the `crc.service` parity tests. Handles BUG-356; BUG-360 and BUG-362 plumbing only (cloud lanes call it). BUG-357 to BUG-362 recorded in `BUGS.md`; LOG rungs added to `PLAN.md`. Details: `docs/refactor/reviews/LOG-0.md`. pytest: 42213 before, 42258 after. |
| LOG-AWS1 | done, reviewed: Round 1 (Argon) APPROVE, Round 2 (Opus) APPROVE | AWS VM, spot instance requests, disk. Branch `logging/aws-compute` (head `f156900` after merging `logging/core` and the runlog to logger rename; make check 42274 passed). Not merged into `refactor/python-agentic`: the owner merges `logging/core` first, then each cloud branch. Review: `docs/refactor/reviews/LOG-AWS1.md`. |
| LOG-AWS2 | done, reviewed: Round 1 (Argon) APPROVE, Round 2 (Opus) APPROVE | AWS KMS, snapshot, keypairs, elastic IPs, VPC. Branch `logging/aws-misc` (head `74831f8` after merging `logging/core` and the runlog to logger rename; make check 42287 passed). Not merged into `refactor/python-agentic`: the owner merges `logging/core` first, then each cloud branch. Review: `docs/refactor/reviews/LOG-AWS2.md`. |
| LOG-AZU | done, reviewed: Round 1 (Argon) APPROVE, Round 2 (Opus) APPROVE | every Azure cleaner (REGION line per resource group). Branch `logging/azure` (head `06dd9a0` after merging `logging/core` and the runlog to logger rename; make check 42275 passed). Not merged into `refactor/python-agentic`: the owner merges `logging/core` first, then each cloud branch. Review: `docs/refactor/reviews/LOG-AZU.md`. |
| LOG-GCP | done, reviewed: Round 1 (Argon) APPROVE, Round 2 (Opus) APPROVE | every GCP cleaner. Branch `logging/gcp` (head `6ec2782` after merging `logging/core` and the runlog to logger rename; make check 42288 passed). Not merged into `refactor/python-agentic`: the owner merges `logging/core` first, then each cloud branch. Review: `docs/refactor/reviews/LOG-GCP.md`. |
| LOG-OCI | done, reviewed: Round 1 (Argon) APPROVE, Round 2 (Opus) APPROVE | every OCI cleaner (also BUG-353). Branch `logging/oci` (head `2a55daf` after merging `logging/core` and the runlog to logger rename; make check 42279 passed). Not merged into `refactor/python-agentic`: the owner merges `logging/core` first, then each cloud branch. Review: `docs/refactor/reviews/LOG-OCI.md`. |
| LOG-FIX | done, reviewed: Round 1 (Argon) APPROVE, Round 2 (Opus) APPROVE | Fixes the regressions a Jenkins dry run (`crc-dry-run-compare` build 4, 2026-10-22) found in the LOG work; owner-approved 2026-10-22. BUG-363: `crc/core/logger.py` writes through the named logger `crc.run` and holds RUN START until the root logger has handlers (so no `basicConfig()` stderr handler and no duplicated lines); `crc/cli/dispatch.py` warnings use a module logger. REGION lines are DEBUG when `selected == 0`. `--scan_tag` (`crc/cli/scan.py`) logs RUN START / RESULT per cloud with `operation=scan`. Branch `logging/fix-handler`. Review: `docs/refactor/reviews/LOG-FIX.md`. pytest: 42432 before, 42438 after. |
| LOG-FIX2 | done, reviewed: Round 1 (Argon) APPROVE, Round 2 (Opus) APPROVE | Fixes the two log-only regressions of Jenkins `crc-dry-run-compare` build 5 (2026-10-23); owner-approved 2026-10-23. BUG-364: `crc.core.logger.logging_ready()` emits the held RUN START and `crc.utils.init_logging` calls it after adding the handlers (fallbacks stay). BUG-365: the `FAILED` line collapses a multi-line exception message to one line (parts stripped, empty dropped, joined with ` \| `). Branch `logging/fix-header`. Review: `docs/refactor/reviews/LOG-FIX2.md`. pytest: 42438 before, 42444 after. |

Open issues (R0a):

- Core layer import-linter contract is a commented TODO until `crc/core` exists.
- `.cursor/environment.json` needs `python3.11` on the image; `make check` uses
  `.venv/bin` when present, otherwise tools on `PATH`.
- CODEOWNERS only takes effect once branch protection requires code owner review.
- `crc.py` (module) and `crc/` (package) share the name `crc`; mypy checks only
  `crc/` and `tests/` (`files`), so the CLI rung must resolve the clash.

Open issues (R0b):

- SAF gaps with no existing test (not invented): SAF-001, 002, 004, 005, 006, 009, 010,
  014, 016, 021, 022, 023, 024, 034, 036, 037. Later rungs that add tests should ask the owner
  to map them with `tests/archtest/update_safety_lock.py --map`.
- SAF-LOCK hashes use a custom version-stable AST dump; verified identical on Python 3.11.17
  and 3.12.3. `update_safety_lock.py` is owner-only and its files are in CODEOWNERS (only
  enforced with branch protection).
- Pins (pytest 9.1.1, ruff 0.17.1, mypy 2.4.0, import-linter 2.15, types-python-dateutil
  2.9.0.20260807) passed `make check` on 3.11.17 and 3.12.3. The CI workflow has not run on
  GitHub yet.
- ARCH-002 covers sub-directories of `crc/`, not `crc/` itself. ARCH-005 also follows
  `self.`/`cls.` methods and same-module functions transitively from `plan`.

Open issues (R1a):

- `crc.core.age.is_old` still reads the `MAX_AGE` environment variable (kept for exact
  behaviour, as the task required). This conflicts with the "no env reads" ideal in the
  skill (3.1); moving the read to the caller (CLI boundary) is a later rung.
- `get_retention_age` is typed `-> Any` in core because it returns the parsed literal (dict,
  int, ...), not `str` as the legacy annotation claims; `Service.get_retention_age` keeps the
  old annotation. `Service._lookup_custom_age_tag` and `Service._parse_literal` remain as thin
  delegates to `lookup_custom_age_tag` and `parse_literal` (now public in core).
- Preserved quirks, covered by tests, not fixed here: `True` is accepted as age 1 day;
  `get_retention_age` returns `{'days': inf}` for `1e999` and unvalidated dicts; `MAX_AGE`
  that is not a dict raises (`SyntaxError`, `AttributeError`) instead of failing closed;
  `is_old` ignores unknown keys (all-unknown returns False); `{}` in `MAX_AGE` makes `is_old`
  return False.
- `include_external_packages = true` makes import-linter graph external packages; the SDK
  contract uses direct and indirect checks (no `allow_indirect_imports`).
- Local `make check` was run on Python 3.12.3 (3.11 not installed in this VM); CI uses 3.11.
  Tests avoid assertions on interpreter-specific `ast` error text.

Proposed SAF mappings (R1a; owner to apply with `tests/archtest/update_safety_lock.py --map`,
`safety_lock.json` not edited):

- SAF-006 (`is_old({})` returns True): `tests/test_core_age.py::test_is_old_empty_age_matches_all[age0]`
  (also `[0]`, `[None]`, which set `MAX_AGE` to prove the empty age ignores it).
- SAF-009 (modules calling `get_retention_age` alone fail open): `tests/test_core_age.py::test_get_retention_age`
  (unparseable, empty and TypeError-style tags return None) and `tests/test_core_age.py::test_get_retention_age_logs`.
- SAF-010 (`MAX_AGE` can shorten a floor inside `is_old`, including to zero):
  `tests/test_core_age.py::test_is_old_max_age_override[age3-{'days': 0}-delta3-outcome3]`
  and `tests/test_core_age.py::test_is_old_max_age_message_text`.

Open issues (R1b):

- Quirks are preserved, not fixed (see `FILTER_VARIANTS.md` section 7): AWS Elastic IP notags never
  skips; GCP IP exception_regex is inverted; GCP disk ignores notags and name_regex; KMS
  multi-value filter never matches; Azure disk state is a substring test (`"attached"` passes
  `"Unattached"`); Azure IP raises on `None` tags with exception_tags/notags (policy returns
  False); AWS VPC calls `_should_skip_vpc` with a resource instead of tags.
- Untagged handling of AWS VM and Elastic IP depends on the `Tags` key being absent; the wiring
  rung must decide that before `normalize_tags`.
- The EC2 server-side `tag:key` filter (AWS VM, VPC, spot) stays in the cleaners; its behaviour
  with an empty value list was not verified.
- Policy covers filtering only. Age functions stay in `crc.core.age`; `effective_age`,
  `aws_disk_creation_floor`, `is_older_than_floor` and `snapshot_old_enough` are thin helpers.
- Local `make check` ran on Python 3.12.3 (3.11 not installed), dependencies installed with
  `pip --user`.

Proposed SAF mappings (R1b; owner to apply with `tests/archtest/update_safety_lock.py --map`,
`safety_lock.json` not edited). Policy-level tests that back existing SAF IDs (cleaner-level
tests still own the wired behaviour):

- SAF-042 (Azure skips untagged Spot VMs): `tests/test_core_policy.py::test_skip_untagged[tags0-UntaggedMode.SKIP-False-True]`
  (policy only; the Azure wiring rung must add the cleaner test).
- SAF-045 (GCP skips GKE / MIG, OCI skips OKE / instance-pool): `tests/test_core_policy.py::test_gcp_managed_or_gke`
  and `tests/test_core_policy.py::test_oci_managed_or_oke` (all parametrised cases).
- SAF-044 and SAF-032 (volume-owned exception/notags and sensitive yb_task protection):
  `tests/test_core_policy.py::test_should_skip_by_tags`, `::test_yb_task_variants` and
  `::test_is_sensitive_yb_task` (policy only).

Gaps: `CLEANUP_SAFETY.md` has no SAF ID for the generic "empty exception_tags or notags never
skips, untagged never matches notags" rule, for the AWS disk Kubernetes CSI skip, or for the
quirks listed above. Proposed new IDs (owner decides): SAF-047 policy tag skip rules
(`test_should_skip_by_tags`, `test_exception_and_notags`, `test_aws_eip_should_skip_ignores_notags`),
SAF-048 AWS disk Kubernetes volume skip (`test_aws_disk_skips_kubernetes`,
`test_aws_kubernetes_volume`).

Open issues (R2a):

- The registry decorator is `register(cloud, kind)` as the task specified. `ARCHITECTURE.md`
  now uses it; `.cursor/skills/crc-python-refactor/SKILL.md` (1.3) and `AGENTS.md` still say
  `register_cleaner(cloud=..., resource=...)` (guardrail files, not edited). Owner to align.
- `crc.core.__init__` exports `register` but not `get`, `kinds_for`, `clouds` (too generic as
  package-level names); use `crc.core.registry.get` etc.
- Report covers the list, owner-grouped (`by_owner` on `Plan`, `Result`, `Report` plus
  `format_owner_ping`) and Influx cases. The Slack user and group lookups (`users_lookupByEmail`,
  `usergroups_list`, `conversations_open`) stay with the notifier.
- `Section.notify_empty` / `link_names` / `slack_ids` encode today's per-call differences: the
  extra disk message of spot VMs is sent only when non-empty (`notify_empty=False`), the Azure VM
  NIC section is sent even when empty and uses `vm.get_deleted` on Slack (`slack_ids`) while
  writing `vm.get_deleted_nic` (`ids`) to Influx, `SPOT_INSTANCE_REQUEST` writes `VM` to Influx,
  and `link_names=True` is set only on the disk list message; Influx points are written even when
  empty and in dry run.
- Whether `succeeded` holds ids or names per cloud (today `get_deleted` is whatever each
  cleaner stores) is decided by each cleaner rung; `Report.from_plan` uses `Candidate.id`.
- `datetime` values are not checked for timezone in `RunContext`; cleaners must pass aware or
  naive values consistently with launch times.
- Frozen+slots dataclasses raise `TypeError` (not `FrozenInstanceError`) when a new attribute
  is set on CPython 3.12; the test accepts both. A hash of a dataclass with a mapping field
  raises `TypeError` (read-only mappings are not hashable); nothing hashes DTOs today.
- Local `make check` ran on Python 3.12.3 with `pip --user` installs; CI uses 3.11.

Bugs and quirks found (R2a), recorded and not fixed:

1. `crc.py:337` and `crc.py:456` define `notify_deleted_nic_via_slack` twice. The second wins, so
   `get_deleted_nic` (used at `crc.py:344`) is never read. `crc.py:371-372` (Azure VM and Azure
   spot VM delete) therefore sends the "NIC" Slack message with `vm.get_deleted`, the VM names,
   not the NIC names. Influx at `crc.py:567` and `crc.py:615` uses the real `get_deleted_nic`.
2. `crc.py:267`, `273`, `291`: `ping_on_slack` hard-codes the word "disks" in the owner line and
   builds `msg + "`N`"` without a space ("Deleted the following`3` aws Disk(s)"). Only disks use
   it today, so it is harmless there. `format_owner_ping` keeps both.
3. `crc.py:98-99`, `524-537`: `influxdb_bucket` and `resource_suffix` exist only when
   `influxdb_conn` is passed. Without it `write_influxdb` raises `AttributeError`, swallowed by
   the catch-all at `crc.py:538-539` ("Unable to push data to influxDB"). The same catch-all hides
   `len(None)` when a list attribute is `None`.
4. `crc.py:480-493`: `notify_deleted_disk_via_slack` sends nothing when `get_deleted` is neither a
   `list` nor a `dict` (`type(...) ==` also rejects subclasses and tuples), and never says so.
5. `crc.py:565-567` and the other `write_influxdb` calls: Influx points are written in dry run
   too, and Slack is notified in dry run ("Dry Run" text), so a dry run still pushes metrics.
6. `crc.py:243-256`: `operated_list_length` is computed twice in `ping_on_slack`; the first sum
   is dead code. `crc.py:214` and `crc.py:226` use bare `except:`.
7. `crc.py:347` (and the other `"#" + self.slack_channel` sends): a channel already starting
   with `#` becomes `##name`, while `crc.py:1509` strips that case for `notify_cleanup`.

Open issues (R2b):

- Slack: the task said `crc.py` catches Influx failures only, but every Slack `chat_postMessage` in
  `crc.py` is wrapped in `try/except Exception: print` too (`crc.py:345-507`, `604-666`), so both
  senders are `fail_open` today. The wiring rung must set `fail_open=True` on both notifiers to keep
  behaviour; the engine only enforces the flag. `crc.py` prints these errors, the engine logs them
  with `logging.error`.
- `ValueError` for an unknown cloud or resource in `build_selection` is new but unreachable from the
  CLI (argparse `choices`); `crc.py` would silently do nothing.
- Selection warnings are logged when `run()` starts, `crc.py` logs each when its loop reaches the pair.
- `build_selection` raises the EBS breaker before returning pairs, `crc.py` raises it in the loop, after
  the `is_valid_*` validators, the Slack and Influx client creation, and the `scan_tag` branch. The CLI
  rung must call `build_selection` at the same point to keep the order of errors.
- Selection returns pairs with no cleaner (for example `azure keypair`, `aws nic`, `gcp snapshot`); the
  engine then raises `UnknownCleanerError` where `crc.py` raised `ValueError` at that pair
  (`crc.py:800`, `823`) or ran the AWS class (snapshot). The wiring rung must register or reject them.
- `vpc` and `snapshot` produce no Slack or Influx output in `crc.py`; the engine builds a report for
  them (`KIND_LABELS`, `vpc` label is a placeholder). The notifier wiring must skip them.
- `Operation` has no per-kind support table here; each cleaner decides in `supports()`.

Bugs and quirks found (R2b), recorded and not fixed:

1. `crc.py:1394`, `1546-1552`: `--cloud all --resource all` and `--cloud all --resource disk` can never
   run: `aws` is the first cloud and `disk` the first resource, so the EBS breaker raises on the first
   pair. `--resource all` is effectively unusable (after the KMS option checks), and `--cloud aws
   --resource all` fails earlier at `crc.py:1367`.
2. `crc.py:1630-1643`: `--resource snapshot` always runs the AWS `Snapshot` class with a hard-coded
   `age_days=90`, for every cloud (`--cloud azure --resource snapshot` touches AWS), and sends nothing
   to Slack or Influx, it only prints.
3. `crc.py:799-800`, `823`: `--cloud all --resource keypair` deletes AWS keypairs, then raises
   `ValueError("Keypair operation is only supported on AWS.")` on Azure; the same shape for `nic` on
   non-Azure clouds. The run fails after the first cloud has already deleted resources.
4. `crc.py:1377-1385`: KMS description and user are required for `--resource kms` on every cloud,
   including clouds that have no KMS cleaner; the error text has the typo "reuired" (kept verbatim).
5. `crc.py:1366-1371`: the message says `--resources all` but the flag is `--resource` (kept verbatim).
6. `crc.py:1421-1424`, `1645-1647`: `MAX_AGE` is set in `os.environ` and only deleted at the end of
   `main()`, not in a `finally`, so any exception (including the EBS breaker) leaves it set.
7. `crc.py:1439`: `clouds == "all"` is always False there (`clouds` is already a list), so the
   scan uses `[c for c in clouds]`; same result as `CLOUDS` when `--cloud all`, dead branch otherwise.
8. `crc.py:1581-1583`: `--operation_type stop` with `--resource vm` is the only stop path; an
   operation value other than `delete` or `stop` would silently do nothing for `vm` (unreachable,
   argparse `choices`).

Open issues (R2c):

- Guardrail files edited because the task asked for it: `AGENTS.md`, `tests/archtest/`,
  `.cursor/rules/README.md` and `.cursor/skills/.../README.md` (new files under CODEOWNERS paths).
- ARCH-002 exempts the root (user-facing `README.md`), `.cursor` itself (only `environment.json`), `.cursor/skills` (only skill subdirectories), and the
  trees `.git`, `.github`, `.claude` (symlink mirror), caches, virtualenvs and `.tgrep`; the task listed
  no README for `.github`, `.github/workflows`, `.claude/skills` and `.cursor/skills`, so they are not required.
  The walk looks at files on disk (including subdirectories), not `git ls-files`: an untracked, not excluded directory fails the check.
- `BUGS.md` found one correction: AWS Elastic IP `notags` raises `TypeError` instead of never skipping
  (`crc/aws/elastic_ips.py:102-107`, BUG-011). `FILTER_VARIANTS.md` and `crc.core.policy.aws_eip_should_skip`
  still describe "never skips"; the behaviour-fix rung must decide. Not edited here.
- `FILTER_VARIANTS.md` section 7 says the fail-open age tag is in 11 modules; `git grep` on `main` finds 9 call
  sites (BUG-004).
- The cloud READMEs are not symbol-synced; they will be when each package's last legacy file leaves the ratchet.
- Local `make check` ran on Python 3.12.3 with `pip --user` installs; CI uses 3.11.

Open issues (R3):

- R2b expected both notifiers to be `fail_open`. `SlackNotifier.fail_open` is False: every
  `chat_postMessage` error is caught and printed inside the notifier (as in `crc.py`), and the one
  Slack error `crc.py` lets escape (`conversations_open` while pinging a user, BUG-042) must still
  propagate through the engine. `InfluxNotifier.fail_open` is True and also catches per point, so
  later points are still written after a failure. Printed messages stay `print`, not `logging`.
- `crc.py` still builds the `Report` itself from the cleaner attributes (`get_deleted`,
  `get_deleted_nic`, `volumes_to_delete`) per call; nothing in `crc.py` uses the engine yet. The
  CRC methods build the sections explicitly, so the BUG-028 NIC list and the spot disk message are
  as before. `_section` in `crc.py` calls `len(ids)` first only to keep the old
  `TypeError` text for a `None` id list.
- `CRC.get_msg` and `CRC.ping_on_slack` now map the operation text to `Operation`
  (`"Deleted"`, `"Stopped"`); any other string raises `KeyError`. Only those two constants are
  used and nothing else calls them, but a future caller passing another word needs a mapping.
- `CRC.ping_on_slack` passes its own `slack_lookup_user_by_email` and `get_user_groups_list` to
  `SlackNotifier.ping_owners`, because existing tests patch those methods; the dead
  `operated_list_length` sum of the old method was not carried over (BUG-033, no observable effect
  unless a value has no `len`, which fails later anyway).
- The scan-tag path of `main()` still calls `notify_cleanup` through the `crc.notifications` shim;
  `SlackNotifier.send_cleanup` implements the same call (adds `#` only when missing) and is tested,
  but `main()` was left untouched. Its logger name is now `crc.notify.slack` (log format has no
  logger name).
- The two bare `except` clauses of the Slack lookups keep their behaviour with `# noqa: E722`
  (they also swallow `KeyboardInterrupt`, BUG-033). `notify_cleanup` has a `# noqa: PLR0913,
  PLR0917` because its seven positional parameters are the public signature.
- `crc.py` still imports `Point` and `SYNCHRONOUS` (a test reads `crc_cli.Point`).
- Local `make check` ran on Python 3.12.3 with `pip --user` installs; CI uses 3.11.

Open issues (R4a):

- The one existing test `tests/test_core_registry.py::test_default_registry_functions_use_default_instance` asserted that the default registry is empty after the monkeypatch is undone. Registering `aws` / `keypair` at import makes that false, so it now compares with the registrations present before the test (same leak check, strengthened to a snapshot). Owner to confirm.
- `KeyPairs` takes `datetime.datetime.now` as the default clock (a reference, not a call, in `KeyPairRuntime`): ARCH-003 only flags calls, but a cloud module still owns the wall clock. Move the default to the CLI boundary when `crc.py` is wired to the engine.
- Per region order: `KeyPairs.delete()` interleaves plan and apply per region (describe r1, delete r1, describe r2) as on `main`. `KeyPairCleaner.plan()` lists all regions first, `apply()` deletes after, so the engine path differs in call order and in what a later-region error leaves behind. Not used by `crc.py` yet.
- `plan()` records skipped regions only in the log (as on `main`); `Result.failed` stays empty. Plain `logging.info` (root logger) is kept so records equal `main`; no logger is injected.
- `Plan.candidates` follow `set` iteration order per region on purpose (BUG-047).
- Round 2 NIT: a `KeyName` of `None` (or another falsy value that is not a string) is searched as `""` by the `crc.core.policy` regex helpers. With a non-empty regex, `main` raises `TypeError` instead. botocore never returns that shape (`docs/refactor/reviews/R4a.md`, Round 2).
- Local tools run on Python 3.12.3; CI uses 3.11.

Open issues (R4b):

- `ElasticIpCleaner.plan()` lists all regions first, `apply()` releases after, so the engine path differs from `ElasticIPs.delete()` (describe r1, release r1, describe r2) in call order and in what a later-region error leaves behind. Not used by `crc.py` yet (same as R4a).
- `FILTER_VARIANTS.md` and `crc.core.policy.aws_eip_should_skip` still say notags never skips; `main` raises `TypeError` (BUG-011). The cleaner preserves the raise; the behaviour-fix rung must decide.
- `Result.failed` stays empty: errors propagate as on `main`. Skipped candidates are recorded in `Plan.skipped` only (the log has no per-address lines on `main`).
- Local tools run on Python 3.12.3; CI uses 3.11.

Open issues (R4c):

- `KmsCleaner.plan()` is not used by `crc.py` yet (same as R4a, R4b); `Kms.delete()` is plan then schedule on one client, the engine path creates a second client in `apply()`.
- `FILTER_VARIANTS.md` calls the KMS exception filter "any key", but `main` raises `KeyError` for every tagged key (BUG-015); the cleaner preserves it. The custom age tag never matches (BUG-008) and the per-key bare `except:` can leave a key selected (BUG-055); a behaviour-fix rung must decide.
- `Result.failed` stays empty: per-key errors are swallowed as on `main` and scheduling errors propagate.
- Local tools run on Python 3.12.3; CI uses 3.11.
