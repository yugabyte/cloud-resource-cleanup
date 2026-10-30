# Copyright (c) Yugabyte, Inc.

"""AWS KMS key cleaner.

`KmsCleaner` implements the `crc.core.cleaner.Cleaner` contract: `plan()` lists the keys
(`list_keys`) and decides on them (`list_resource_tags`, `describe_key`, `get_key_policy`),
`apply()` calls `schedule_key_deletion`.
"""

import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from crc.aws import _regionlog
from crc.aws._common import DEFAULT_REGION, log_deleted_summary
from crc.aws.connectivity import CONNECTIVITY_ERRORS
from crc.core import age as core_age
from crc.core import logger, policy
from crc.core.cleaner import Candidate, Cleaner, Operation, Plan, Result, RunContext, Skipped
from crc.core.registry import register

from .client import POLICY_NAME, ClientFactory, Clock, KmsClient, boto3_client
from .settings import KmsSettings
from .tags import NotOursError, matches_filter, tag_pairs

UNREACHABLE = "region unreachable"
NOT_OURS = "not_ours"
UNREADABLE = "unreadable"


def log_summary(deleted: Sequence[str], *, dry_run: bool) -> None:
    """The summary lines `main` logs once the cleanup is done."""
    log_deleted_summary("KMS", deleted, dry_run=dry_run)


def log_would_delete(key_ids: Sequence[str]) -> None:
    """Run-log ACTION lines of the keys a dry run would schedule."""
    run = logger.current()
    for key_id in key_ids:
        run.action("would_delete", DEFAULT_REGION, key_id)


class KmsCleaner:
    """Schedules deletion of enabled keys whose description contains `kms_key_description`,
    that are old enough, match `filter_tags` (every key present with any of its values) and
    whose default key policy names `kms_user`. Works in `us-west-2` only, as on `main`."""

    cloud = "aws"
    kind = "kms"

    def __init__(self, client_factory: ClientFactory, settings: KmsSettings) -> None:
        self._client_factory = client_factory
        self._settings = settings

    def supports(self, operation: Operation) -> bool:
        return operation is Operation.DELETE

    def plan(self, context: RunContext) -> Plan:
        """List and decide. Makes no mutating call."""
        opened = self.connect_and_list()
        if opened is None:
            return Plan(skipped=(Skipped(id=DEFAULT_REGION, reason=UNREACHABLE),))
        client, keys = opened
        plan = self.plan_keys(client, keys, lambda: context.now)
        if context.dry_run:
            ids = [c.id for c in plan.candidates]
            log_would_delete(ids)
            log_summary(ids, dry_run=True)
        return plan

    def apply(self, plan: Plan, context: RunContext) -> Result:
        """Schedule the planned keys for deletion (nothing in a dry run)."""
        if any(s.reason == UNREACHABLE for s in plan.skipped):
            return Result()
        ids = [c.id for c in plan.candidates]
        if context.dry_run:
            return Result(succeeded=tuple(ids))
        if ids:
            self.schedule(self._client_factory(DEFAULT_REGION), ids)
        log_summary(ids, dry_run=False)
        return Result(succeeded=tuple(ids))

    def connect_and_list(self) -> tuple[KmsClient, list[Mapping[str, Any]]] | None:
        """Create the client and list every key; `None` when the region is unreachable."""
        kms_keys: list[Mapping[str, Any]] = []
        try:
            client = self._client_factory(DEFAULT_REGION)
            paginator = client.get_paginator("list_keys")
            for page in paginator.paginate():
                kms_keys.extend(page["Keys"])
        except CONNECTIVITY_ERRORS as e:
            _regionlog.region_skipped(DEFAULT_REGION, e)
            logging.warning(
                "KMS cleanup skipped — cannot reach region %s (%s): %s",
                DEFAULT_REGION,
                type(e).__name__,
                e,
            )
            return None
        return client, kms_keys

    def plan_keys(self, client: KmsClient, keys: Sequence[Mapping[str, Any]], clock: Clock) -> Plan:
        """Decide every key. Any error on one key skips that key (a bare `except:` on `main`);
        a skipped key is never selected (BUG-055)."""
        logging.info("Total keys found = %s", len(keys))
        started = _regionlog.start()
        active: list[str] = []
        for key in keys:
            try:
                self._select_key(client, key["KeyId"], active, clock)
            except BaseException as e:  # noqa: BLE001 - per-key isolation, an unreadable key is skipped (fail closed)
                logging.debug("KEY SKIPPED %s", key["KeyId"])
                logger.current().skip(NOT_OURS if isinstance(e, NotOursError) else UNREADABLE)
        _regionlog.region_done(DEFAULT_REGION, len(keys), len(active), started)
        logging.info("total number of active Jenkins keys = %s", len(active))
        candidates = tuple(
            Candidate(id=key_id, name=key_id, region=DEFAULT_REGION) for key_id in active
        )
        return Plan(candidates=candidates)

    def schedule(self, client: KmsClient, key_ids: Sequence[str]) -> None:
        """Schedule `key_ids` one by one; the first error propagates."""
        run = logger.current()
        for cmk_id in key_ids:
            try:
                client.schedule_key_deletion(
                    KeyId=cmk_id, PendingWindowInDays=self._settings.kms_pending_window
                )
            except Exception as e:
                run.fail(DEFAULT_REGION, cmk_id, e)
                raise
            logging.info("CMK - %s Deleted from AWS console", cmk_id)
            run.action("deleted", DEFAULT_REGION, cmk_id)

    def should_skip(self, tags: Sequence[Mapping[str, Any]]) -> bool:
        """Exception tags decision for one key (see `_should_skip`)."""
        return self._should_skip(tags)

    def _select_key(self, client: KmsClient, key_id: str, active: list[str], clock: Clock) -> None:
        response_tags = client.list_resource_tags(KeyId=key_id).get("Tags", [])
        pairs = tag_pairs(response_tags)
        if not matches_filter(pairs, self._settings.filter_tags):
            return
        if self._should_skip(response_tags):
            logger.current().skip("exception_tag")
            return
        metadata = client.describe_key(KeyId=key_id)
        key_state = metadata["KeyMetadata"]["KeyState"]
        key_des = metadata["KeyMetadata"]["Description"]
        created = metadata["KeyMetadata"]["CreationDate"]
        retention_age = core_age.get_retention_age(
            policy.normalize_tags(response_tags), self._settings.custom_age_tag_key
        )
        if retention_age:
            logging.info("Updating age for Key: %s", key_id)
        if (
            key_state == "Enabled"
            and self._settings.kms_key_description in key_des
            and core_age.is_old(
                policy.effective_age(retention_age, self._settings.age),
                clock().astimezone(created.tzinfo),
                created,
            )
        ):
            self._select_for_user(client, key_id, active)

    def _select_for_user(self, client: KmsClient, key_id: str, active: list[str]) -> None:
        document = client.get_key_policy(KeyId=key_id, PolicyName=POLICY_NAME)["Policy"]
        found: list[str] = []
        for statement in json.loads(document)["Statement"]:
            try:
                user_arn = statement["Principal"]["AWS"]
            except (KeyError, TypeError) as e:
                raise NotOursError from e
            if user_arn == self._settings.kms_user:
                logging.info("Key %s found with user %s", key_id, user_arn)
                found.append(key_id)
        active.extend(found)

    def _should_skip(self, tags: Sequence[Mapping[str, Any]]) -> bool:
        exception_tags = self._settings.exception_tags
        if not exception_tags:
            logging.warning("Exception tags and not present")
            return False
        for tag in tags:
            # BUG-015 (owner decision): main reads `Key` / `Value`, but KMS tags carry `TagKey` /
            # `TagValue`, so every tagged key raises KeyError and is skipped.
            pair = {tag["Key"]: tag["Value"]}
            if policy.exception_tags_any(pair, exception_tags):
                return True
        return False


@register("aws", "kms")
def build_kms_cleaner(context: RunContext) -> Cleaner:
    """Registry factory. Options: `filter_tags`, `exception_tags`, `kms_key_description`,
    `kms_user`, `kms_pending_window`, `age` and `custom_age_tag_key`."""
    options = context.options
    settings = KmsSettings(
        filter_tags=options.get("filter_tags"),
        exception_tags=options.get("exception_tags"),
        kms_key_description=options.get("kms_key_description", ""),
        kms_user=options.get("kms_user", ""),
        kms_pending_window=options.get("kms_pending_window", 0),
        age=options.get("age") or {},
        custom_age_tag_key=options.get("custom_age_tag_key") or "",
    )
    return KmsCleaner(boto3_client, settings)
