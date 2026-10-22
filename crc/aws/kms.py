# Copyright (c) Yugabyte, Inc.

"""AWS KMS key cleaner.

`KmsCleaner` implements the `crc.core.cleaner.Cleaner` contract: `plan()` lists the keys
(`list_keys`) and decides on them (`list_resource_tags`, `describe_key`, `get_key_policy`),
`apply()` calls `schedule_key_deletion`. `Kms` is the legacy class `crc.py` uses; it keeps its
constructor and attributes and runs the cleaner in the exact call order of the original
implementation.
"""

import datetime
import json
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import boto3

from crc.aws import _regionlog
from crc.aws.connectivity import CONNECTIVITY_ERRORS
from crc.core import age as core_age
from crc.core import logger, policy
from crc.core.cleaner import Candidate, Cleaner, Operation, Plan, Result, RunContext, Skipped
from crc.core.registry import register
from crc.service import Service

_SERVICE_NAME = "kms"
_DEFAULT_REGION = "us-west-2"
_POLICY_NAME = "default"
_UNREACHABLE = "region unreachable"
_NOT_OURS = "not_ours"
_UNREADABLE = "unreadable"


class _NotOursError(Exception):
    """The key policy has no AWS principal to compare with `kms_user`: not one of ours."""

TagSpec = Mapping[str, Sequence[str]]
Clock = Callable[[], datetime.datetime]


class KmsPaginator(Protocol):
    """The paginator of `list_keys`."""

    def paginate(self) -> Iterable[Mapping[str, Any]]:
        """Yield the pages of keys."""
        ...


class KmsClient(Protocol):
    """The KMS calls this cleaner uses."""

    def get_paginator(self, operation_name: str) -> KmsPaginator:
        """Paginator for `list_keys`."""
        ...

    def list_resource_tags(self, *, KeyId: str) -> Mapping[str, Any]:
        """Tags of one key (`TagKey` / `TagValue` entries)."""
        ...

    def describe_key(self, *, KeyId: str) -> Mapping[str, Any]:
        """Metadata of one key."""
        ...

    def get_key_policy(self, *, KeyId: str, PolicyName: str) -> Mapping[str, Any]:
        """Key policy document (JSON text under `Policy`)."""
        ...

    def schedule_key_deletion(self, *, KeyId: str, PendingWindowInDays: int) -> Mapping[str, Any]:
        """Schedule one key for deletion."""
        ...


ClientFactory = Callable[[str], KmsClient]


def _boto3_client(region: str) -> KmsClient:
    client: KmsClient = boto3.client(_SERVICE_NAME, region_name=region)
    return client


@dataclass(frozen=True, slots=True)
class KmsRuntime:
    """Injected collaborators of `Kms`: the SDK client factory and the clock that supplies
    `now` (the cleaner itself never reads the clock)."""

    client_factory: ClientFactory = _boto3_client
    clock: Clock = datetime.datetime.now


@dataclass(frozen=True, slots=True)
class KmsSettings:
    """Filters and thresholds of one run."""

    filter_tags: TagSpec | None = None
    exception_tags: TagSpec | None = None
    kms_key_description: str = ""
    kms_user: str = ""
    kms_pending_window: int = 0
    age: core_age.AgeSpec = field(default_factory=dict)
    custom_age_tag_key: str = ""


def _log_summary(deleted: Sequence[str], *, dry_run: bool) -> None:
    items = list(deleted)
    if not dry_run:
        logging.warning(f"number of AWS KMS deleted: {len(items)}")
        logging.warning(f"List of AWS KMS deleted: {items}")
    else:
        logging.warning(f"List of AWS KMS (Total: {len(items)}) which will be deleted: {items}")


def _log_would_delete(key_ids: Sequence[str]) -> None:
    run = logger.current()
    for key_id in key_ids:
        run.action("would_delete", _DEFAULT_REGION, key_id)


def _has_all_pairs(response_tags: Iterable[Mapping[str, Any]], spec: TagSpec | None) -> bool:
    # One tag at a time: main compares sets of (key, value) pairs, so a repeated TagKey keeps
    # every one of its values.
    response_set = {(tag["TagKey"], tag["TagValue"]) for tag in response_tags}
    filter_set = {(k, v) for k, values in spec.items() for v in values} if spec else set()
    return all(
        any(
            policy.filter_tags_exact_pairs({key: value}, {wanted_key: [wanted]})
            for key, value in response_set
        )
        for wanted_key, wanted in filter_set
    )


class KmsCleaner:
    """Schedules deletion of enabled keys whose description contains `kms_key_description`,
    that are old enough, match `filter_tags` (all (key, value) pairs) and whose default key
    policy names `kms_user`. Works in `us-west-2` only, as on `main`."""

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
            return Plan(skipped=(Skipped(id=_DEFAULT_REGION, reason=_UNREACHABLE),))
        client, keys = opened
        plan = self.plan_keys(client, keys, lambda: context.now)
        if context.dry_run:
            _log_summary([c.id for c in plan.candidates], dry_run=True)
        return plan

    def apply(self, plan: Plan, context: RunContext) -> Result:
        """Schedule the planned keys for deletion (nothing in a dry run)."""
        if any(s.reason == _UNREACHABLE for s in plan.skipped):
            return Result()
        ids = [c.id for c in plan.candidates]
        if ids and not context.dry_run:
            self.schedule(self._client_factory(_DEFAULT_REGION), ids)
        elif context.dry_run:
            _log_would_delete(ids)
        _log_summary(ids, dry_run=context.dry_run)
        return Result(succeeded=tuple(ids))

    def connect_and_list(self) -> tuple[KmsClient, list[Mapping[str, Any]]] | None:
        """Create the client and list every key; `None` when the region is unreachable."""
        kms_keys: list[Mapping[str, Any]] = []
        try:
            client = self._client_factory(_DEFAULT_REGION)
            paginator = client.get_paginator("list_keys")
            for page in paginator.paginate():
                kms_keys.extend(page["Keys"])
        except CONNECTIVITY_ERRORS as e:
            _regionlog.region_skipped(_DEFAULT_REGION, e)
            logging.warning(
                "KMS cleanup skipped — cannot reach region %s (%s): %s",
                _DEFAULT_REGION,
                type(e).__name__,
                e,
            )
            return None
        return client, kms_keys

    def plan_keys(self, client: KmsClient, keys: Sequence[Mapping[str, Any]], clock: Clock) -> Plan:
        """Decide every key. Any error on one key skips that key (a bare `except:` on `main`),
        and a key found for the user before the error stays selected."""
        logging.info(f"Total keys found = {len(keys)}")
        started = _regionlog.start()
        active: list[str] = []
        for key in keys:
            selected_before = len(active)
            try:
                self._select_key(client, key["KeyId"], active, clock)
            except BaseException as e:
                logging.debug(f"KEY SKIPPED {key['KeyId']}")
                # A key appended before the error stays selected: it is not a skip.
                if len(active) == selected_before:
                    logger.current().skip(
                        _NOT_OURS if isinstance(e, _NotOursError) else _UNREADABLE
                    )
        _regionlog.region_done(_DEFAULT_REGION, len(keys), len(active), started)
        logging.info(f"total number of active Jenkins keys = {len(active)}")
        candidates = tuple(
            Candidate(id=key_id, name=key_id, region=_DEFAULT_REGION) for key_id in active
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
                run.fail(_DEFAULT_REGION, cmk_id, e)
                raise
            logging.info(f"CMK - {cmk_id} Deleted from AWS console")
            run.action("deleted", _DEFAULT_REGION, cmk_id)

    def should_skip(self, tags: Sequence[Mapping[str, Any]]) -> bool:
        """Exception tags decision for one key (see `_should_skip`)."""
        return self._should_skip(tags)

    def _select_key(self, client: KmsClient, key_id: str, active: list[str], clock: Clock) -> None:
        response_tags = client.list_resource_tags(KeyId=key_id).get("Tags", [])
        if not _has_all_pairs(response_tags, self._settings.filter_tags):
            return
        if self._should_skip(response_tags):
            logger.current().skip("exception_tag")
            return
        metadata = client.describe_key(KeyId=key_id)
        key_state = metadata["KeyMetadata"]["KeyState"]
        key_des = metadata["KeyMetadata"]["Description"]
        created = metadata["KeyMetadata"]["CreationDate"]
        retention_age = core_age.get_retention_age(response_tags, self._settings.custom_age_tag_key)
        if retention_age:
            logging.info(f"Updating age for Key: {key_id}")
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
        document = client.get_key_policy(KeyId=key_id, PolicyName=_POLICY_NAME)["Policy"]
        for statement in json.loads(document)["Statement"]:
            try:
                user_arn = statement["Principal"]["AWS"]
            except (KeyError, TypeError) as e:
                raise _NotOursError from e
            if user_arn == self._settings.kms_user:
                logging.info(f"Key {key_id} found with user {user_arn}")
                active.append(key_id)

    def _should_skip(self, tags: Sequence[Mapping[str, Any]]) -> bool:
        exception_tags = self._settings.exception_tags
        if not exception_tags:
            logging.warning("Exception tags and not present")
            return False
        for tag in tags:
            # BUG-015: main reads `Key` / `Value`, but KMS tags carry `TagKey` / `TagValue`.
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
    return KmsCleaner(_boto3_client, settings)


class Kms(Service):
    """Legacy entry point used by `crc.py`: schedules deletion of the matching KMS keys. In dry
    run it only lists them.

    Attributes: `kms_keys_to_delete` (key ids scheduled, or listed in a dry run, accumulated over
    calls), the constructor arguments, `get_deleted` and the `delete()` method.
    """

    service_name = _SERVICE_NAME
    default_region_name = _DEFAULT_REGION

    def __init__(  # noqa: PLR0913, PLR0917 - the eight parameters are the public signature
        self,
        dry_run: bool,
        filter_tags: TagSpec | None,
        exception_tags: TagSpec | None,
        kms_key_descriptiom: str,
        kms_user: str,
        kms_pending_window: int,
        age: core_age.AgeSpec,
        custom_age_tag_key: str,
        runtime: KmsRuntime | None = None,
    ) -> None:
        super().__init__()
        self.kms_keys_to_delete: list[str] = []
        self.dry_run = dry_run
        self.filter_tags = filter_tags
        self.exception_tags = exception_tags
        self.kms_key_description = kms_key_descriptiom
        self.kms_user = kms_user
        self.kms_pending_window = kms_pending_window
        self.age = age
        self.custom_age_tag_key = custom_age_tag_key
        self._runtime = runtime or KmsRuntime()

    @property
    def get_deleted(self) -> list[str]:
        """Key ids scheduled for deletion (listed in a dry run)."""
        return self.kms_keys_to_delete

    def _cleaner(self) -> KmsCleaner:
        return KmsCleaner(
            self._runtime.client_factory,
            KmsSettings(
                self.filter_tags,
                self.exception_tags,
                self.kms_key_description,
                self.kms_user,
                self.kms_pending_window,
                self.age,
                self.custom_age_tag_key,
            ),
        )

    def _should_skip_kms(self, tags: Sequence[Mapping[str, Any]]) -> bool:
        return self._cleaner().should_skip(tags)

    def delete(self) -> None:
        """Schedule (or, in a dry run, only list) the matching keys."""
        cleaner = self._cleaner()
        opened = cleaner.connect_and_list()
        if opened is None:
            return
        client, keys = opened
        plan = cleaner.plan_keys(client, keys, self._runtime.clock)
        active = [c.id for c in plan.candidates]
        if self.dry_run:
            _log_would_delete(active)
        else:
            cleaner.schedule(client, active)
        self.kms_keys_to_delete.extend(active)
        _log_summary(self.kms_keys_to_delete, dry_run=self.dry_run)
