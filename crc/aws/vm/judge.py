# Copyright (c) Yugabyte, Inc.

"""Read-only decision on each instance of a region: tags, exception tags, Name tag and age."""

import datetime
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from crc.aws.vm import scan as vm_scan
from crc.aws.vm.client import VmClient
from crc.aws.vm.settings import VmSettings
from crc.core import age as core_age
from crc.core import logger, policy
from crc.core.age import AgeSpec
from crc.core.cleaner import Candidate, Skipped

Decision = Candidate | Skipped

_SKIP_REASONS = {
    "no Tags key": "no_tags",
    "exception tag or notags": "exception_tag",
    "younger than age": "too_young",
}


def _attach_time(client: VmClient, inst: Mapping[str, Any]) -> datetime.datetime:
    nic_id = inst["NetworkInterfaces"][0]["NetworkInterfaceId"]
    details = client.describe_network_interfaces(NetworkInterfaceIds=[nic_id])
    attached: datetime.datetime = details["NetworkInterfaces"][0]["Attachment"]["AttachTime"]
    return attached


def _safe_id(inst: Any) -> str:
    return str(inst.get("InstanceId", "")) if isinstance(inst, Mapping) else ""


class InstanceJudge:
    """Decides every instance of a `describe_instances` response against the run settings."""

    def __init__(self, settings: VmSettings) -> None:
        self._settings = settings

    def filter_instances(
        self, client: VmClient, details: Mapping[str, Any], region: str, now: datetime.datetime
    ) -> tuple[list[Candidate], list[Skipped]]:
        """Decide every instance of a `describe_instances` response, in order."""
        found: list[Candidate] = []
        kept: list[Skipped] = []
        for reservation in details["Reservations"]:
            for inst in reservation["Instances"]:
                decision = self._decide(client, inst, region, now)
                if isinstance(decision, Candidate):
                    found.append(decision)
                else:
                    kept.append(decision)
                    self._note_skipped(decision, region)
        return found, kept

    @staticmethod
    def _note_skipped(decision: Skipped, region: str) -> None:
        """Count a kept instance under its logger reason. Errors were recorded by `_decide`;
        the missing Name tag is logged at ERROR, so it is also a failure."""
        run = logger.current()
        if decision.reason == "no Name tag":
            run.fail(region or None, decision.id or "-", "instance doesn't have Name Tag")
        elif decision.reason in _SKIP_REASONS:
            run.skip(_SKIP_REASONS[decision.reason])

    def should_skip(self, tags: Sequence[Mapping[str, str]]) -> bool:
        """Exception tags (any key, checked tag by tag) or notags (all keys) skip an instance.
        A tag without `Key` or `Value` raises KeyError."""
        settings = self._settings
        if not settings.exception_tags and not settings.notags:
            logging.warning("Tags and notags not present")
            return False
        all_tags: dict[str, str] = {}
        for tag in tags:
            key = tag["Key"]
            value = tag["Value"]
            if settings.exception_tags and policy.exception_tags_any(
                {key: value}, settings.exception_tags
            ):
                return True
            all_tags[key] = value
        return policy.notags_all(all_tags, settings.notags or {})

    def _decide(
        self, client: VmClient, inst: Mapping[str, Any], region: str, now: datetime.datetime
    ) -> Decision:
        name: str | None = None
        try:
            screened = self._screen(inst)
            if isinstance(screened, Skipped):
                return screened
            name, tags = screened
            return self._judge(client, inst, (name, region), tags, now)
        except Exception as e:  # noqa: BLE001 - per-instance isolation, instance skipped and recorded
            logging.error("Error occurred while processing %s instance: %s", name, e)
            logger.current().fail(region or None, _safe_id(inst) or "-", e)
            return Skipped(id=_safe_id(inst), reason=f"error: {e}")

    def _screen(self, inst: Mapping[str, Any]) -> Skipped | tuple[str, Any]:
        if "Tags" not in inst:
            return Skipped(id=_safe_id(inst), reason="no Tags key")
        tags = inst["Tags"]
        if self.should_skip(tags):
            return Skipped(id=_safe_id(inst), reason="exception tag or notags")
        name = vm_scan.instance_name(tags)
        if not name:
            logging.error("%s instance doesn't have Name Tag. Skipping it", name)
            return Skipped(id=_safe_id(inst), reason="no Name tag")
        return name, tags

    def _judge(
        self,
        client: VmClient,
        inst: Mapping[str, Any],
        where: tuple[str, str],
        tags: Any,
        now: datetime.datetime,
    ) -> Decision:
        name, region = where
        instance_id = inst["InstanceId"]
        attached = _attach_time(client, inst)
        logging.debug(tags)
        retention_age: AgeSpec | None = core_age.get_retention_age(
            tags, self._settings.custom_age_tag_key
        )
        if retention_age:
            logging.info("Updating age for instance_id: %s", instance_id)
        age = policy.effective_age(retention_age, self._settings.age)
        if self._settings.require_age and not age:
            return Skipped(id=instance_id, name=name, reason="no age")
        if not core_age.is_old(age, now.astimezone(attached.tzinfo), attached):
            return Skipped(id=instance_id, name=name, reason="younger than age")
        logging.info(
            "Instance %s with ID %s added to list of instances to be cleaned up.", name, instance_id
        )
        return Candidate(
            id=instance_id,
            name=name,
            region=region,
            tags=policy.normalize_tags(tags),
            launch_time=attached,
        )
