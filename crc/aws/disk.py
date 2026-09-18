# Copyright (c) Yugabyte, Inc.

import datetime
import logging
import re
from typing import Dict, List, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

# Unreachable opt-in regions (e.g. me-south-1) otherwise sit on 60s connect × retries.
_BOTO_CFG = Config(
    connect_timeout=8,
    read_timeout=30,
    retries={"max_attempts": 2, "mode": "standard"},
)

from crc.aws._base import get_all_regions
from crc.aws.connectivity import CONNECTIVITY_ERRORS, log_skipped_region
from crc.service import Service


class Disk(Service):
    """
    Delete unattached (available) EBS volumes that match filter/age rules.

    Two independent age gates, at least one of which must be supplied:

    * ``age`` is measured from ``CreateTime``, i.e. how long ago the volume
      was *created*. It says nothing about how long it has been unattached.
    * ``detach_age`` is measured from the last time the volume was attached,
      inferred from the presence of AWS/EBS CloudWatch metrics, which are only
      published while a volume is attached. Use this to avoid deleting a
      long-lived volume that was detached moments ago by an instance
      replacement or a snapshot/restore swap.

    When both are given, a volume must clear both gates.

    Attached volumes are never listed or deleted. Infra/prod/vpn yb_task
    values and Kubernetes CSI volumes are skipped unless the caller
    explicitly filter_tags on a kubernetes key.
    """

    service_name = "ec2"
    default_region_name = "us-west-2"

    # CloudWatch retains EBS metrics for 455 days. Past that, "no datapoints"
    # and "detached longer than the window" are the same answer, so clamping
    # the lookback is safe.
    CW_MAX_LOOKBACK = datetime.timedelta(days=455)
    CW_BATCH_SIZE = 100

    # Defensive skips even if Jenkins forgets --exception_tags. Compared
    # casefolded, since tag values are not normalized in practice.
    SENSITIVE_YB_TASK = {
        "infrastructure",
        "infra",
        "ipsec",
        "vpn",
        "prod",
        "production",
    }

    def __init__(
        self,
        dry_run: bool,
        filter_tags: Optional[Dict[str, List[str]]],
        exception_tags: Optional[Dict[str, List[str]]],
        age: Optional[Dict[str, int]],
        custom_age_tag_key: str,
        notags: Optional[Dict[str, List[str]]],
        name_regex: Optional[List[str]] = None,
        exception_regex: Optional[List[str]] = None,
        detach_age: Optional[Dict[str, int]] = None,
    ) -> None:
        super().__init__()
        self.disks_to_delete: List[str] = []
        self.dry_run = dry_run
        self.filter_tags = filter_tags or {}
        self.exception_tags = exception_tags or {}
        self.age = age or {}
        self.detach_age = detach_age or {}
        self.custom_age_tag_key = custom_age_tag_key
        self.notags = notags or {}
        self.name_regex = name_regex or []
        self.exception_regex = exception_regex or []

        # Service.is_old() returns True for an empty age, so without this an
        # age-less run would delete every available volume in every region.
        if not self.age and not self.detach_age:
            raise ValueError(
                "AWS disk cleanup requires an age gate: pass --age (measured from "
                "volume creation) and/or --detach_age (measured from last attachment)."
            )

    @property
    def get_deleted(self):
        return self.disks_to_delete

    @property
    def count(self):
        count = len(self.disks_to_delete)
        logging.info(f"count of items in disks_to_delete: {count}")
        return count

    def _tag_map(self, tags: Optional[List[Dict[str, str]]]) -> Dict[str, str]:
        return {t["Key"]: t["Value"] for t in tags or [] if t.get("Key")}

    def _filter_tags_want_kubernetes(self) -> bool:
        return any(
            key.startswith("kubernetes.io/")
            or key.startswith("ebs.csi.aws.com/")
            or key in ("KubernetesCluster", "CSIVolumeName")
            for key in self.filter_tags
        )

    def _is_kubernetes_volume(self, tag_map: Dict[str, str]) -> bool:
        for key in tag_map:
            if key.startswith("kubernetes.io/") or key.startswith("ebs.csi.aws.com/"):
                return True
            if key in ("KubernetesCluster", "CSIVolumeName"):
                return True
        return False

    def _matches_filter_tags(self, tag_map: Dict[str, str]) -> bool:
        if not self.filter_tags:
            return True
        # AWS VM filters AND across keys, OR within values.
        for key, values in self.filter_tags.items():
            if key not in tag_map:
                return False
            if values and tag_map[key] not in values:
                return False
        return True

    def _matches_name_regex(self, name: str) -> bool:
        if not self.name_regex:
            return True
        return any(re.search(pattern, name or "") for pattern in self.name_regex)

    def _matches_exception_regex(self, name: str) -> bool:
        if not self.exception_regex:
            return False
        return any(re.search(pattern, name or "") for pattern in self.exception_regex)

    def _should_skip(self, tag_map: Dict[str, str], name: str, volume_id: str) -> bool:
        yb_task = (tag_map.get("yb_task") or tag_map.get("yb-task") or "").strip()

        if yb_task.casefold() in self.SENSITIVE_YB_TASK:
            logging.info(
                "Skipping volume %s: sensitive yb_task=%s", volume_id, yb_task
            )
            return True

        if self._is_kubernetes_volume(tag_map) and not self._filter_tags_want_kubernetes():
            logging.info(
                "Skipping volume %s: Kubernetes CSI volume without kubernetes filter_tags",
                volume_id,
            )
            return True

        for key, value in tag_map.items():
            if key in self.exception_tags and (
                not self.exception_tags[key] or value in self.exception_tags[key]
            ):
                logging.info(
                    "Skipping volume %s: exception tag %s=%s", volume_id, key, value
                )
                return True

        if self.notags and all(
            key in tag_map and (not values or tag_map[key] in values)
            for key, values in self.notags.items()
        ):
            logging.info("Skipping volume %s: matched notags", volume_id)
            return True

        if self._matches_exception_regex(name):
            logging.info("Skipping volume %s: Name matched exception_regex", volume_id)
            return True

        return False

    def _volume_filters(self) -> List[Dict[str, List[str]]]:
        filters = [{"Name": "status", "Values": ["available"]}]
        for key, values in self.filter_tags.items():
            if values:
                filters.append({"Name": f"tag:{key}", "Values": values})
            else:
                filters.append({"Name": "tag-key", "Values": [key]})
        return filters

    def delete(self) -> None:
        """
        Delete available EBS volumes matching filter_tags / age / detach_age.
        dry_run only records candidates.
        """
        for region in get_all_regions(self.service_name, self.default_region_name):
            try:
                client = boto3.client(
                    self.service_name, region_name=region, config=_BOTO_CFG
                )
                candidates = []
                paginator = client.get_paginator("describe_volumes")
                for page in paginator.paginate(Filters=self._volume_filters()):
                    for volume in page.get("Volumes") or []:
                        if self._is_candidate(volume):
                            candidates.append(volume)

                for volume in self._drop_recently_attached(region, candidates):
                    self._delete_volume(client, region, volume)
            except CONNECTIVITY_ERRORS as e:
                log_skipped_region(region, "EBS disk cleanup", e)
            except ClientError as e:
                code = (e.response.get("Error") or {}).get("Code")
                if code in {
                    "AuthFailure",
                    "UnauthorizedOperation",
                    "OptInRequired",
                    "InvalidClientTokenId",
                }:
                    logging.warning(
                        "Region %s: skipped EBS disk cleanup (%s)", region, code
                    )
                    continue
                logging.error("Region %s: ClientError during EBS disk cleanup: %s", region, e)
            except Exception as e:
                logging.error("Region %s: error during EBS disk cleanup: %s", region, e)

        if not self.dry_run:
            logging.warning(
                "number of AWS EBS volumes deleted: %s", len(self.disks_to_delete)
            )
            logging.warning("List of AWS EBS volumes deleted: %s", self.disks_to_delete)
        else:
            logging.warning(
                "List of AWS EBS volumes (Total: %s) which will be deleted: %s",
                len(self.disks_to_delete),
                self.disks_to_delete,
            )

    def _is_candidate(self, volume: dict) -> bool:
        """Tag, name and creation-age checks. Detach age is applied separately."""
        volume_id = volume.get("VolumeId")
        if volume.get("State") != "available":
            return False
        if volume.get("Attachments"):
            logging.info("Skipping volume %s: still has attachments", volume_id)
            return False

        tag_map = self._tag_map(volume.get("Tags"))
        name = tag_map.get("Name") or volume_id

        if not self._matches_filter_tags(tag_map):
            return False
        if not self._matches_name_regex(name):
            return False
        if self._should_skip(tag_map, name, volume_id):
            return False

        created = volume.get("CreateTime")
        if created is None:
            logging.warning("Skipping volume %s: missing CreateTime", volume_id)
            return False

        retention_age = self.get_retention_age(volume.get("Tags") or [], self.custom_age_tag_key)
        if retention_age:
            logging.info("Updating age for volume: %s", volume_id)

        age = retention_age or self.age
        if age:
            now = datetime.datetime.now().astimezone(created.tzinfo)
            if not self.is_old(age, now, created):
                return False

        return True

    def _age_to_timedelta(self, age) -> datetime.timedelta:
        if isinstance(age, int):
            age = {"days": age}
        return datetime.timedelta(
            days=int(age.get("days", 0)), hours=int(age.get("hours", 0))
        )

    def _drop_recently_attached(self, region: str, volumes: List[dict]) -> List[dict]:
        """
        Keep only volumes with no AWS/EBS CloudWatch datapoints inside the
        detach_age window. Those metrics are published only while a volume is
        attached, so their absence means the volume has been detached for at
        least that long. Volumes we cannot get an answer for are dropped.
        """
        if not self.detach_age or not volumes:
            return volumes

        window = min(self._age_to_timedelta(self.detach_age), self.CW_MAX_LOOKBACK)
        if not window:
            return volumes

        end = datetime.datetime.now(datetime.timezone.utc)
        start = end - window
        cloudwatch = boto3.client("cloudwatch", region_name=region, config=_BOTO_CFG)

        kept = []
        for index in range(0, len(volumes), self.CW_BATCH_SIZE):
            batch = volumes[index : index + self.CW_BATCH_SIZE]
            by_query_id = {f"m{i}": volume for i, volume in enumerate(batch)}
            queries = [
                {
                    "Id": query_id,
                    "MetricStat": {
                        "Metric": {
                            "Namespace": "AWS/EBS",
                            "MetricName": "VolumeIdleTime",
                            "Dimensions": [
                                {"Name": "VolumeId", "Value": volume["VolumeId"]}
                            ],
                        },
                        "Period": 3600,
                        "Stat": "Sum",
                    },
                }
                for query_id, volume in by_query_id.items()
            ]

            try:
                results = cloudwatch.get_metric_data(
                    MetricDataQueries=queries, StartTime=start, EndTime=end
                ).get("MetricDataResults") or []
            except (ClientError, *CONNECTIVITY_ERRORS) as e:
                logging.warning(
                    "Region %s: CloudWatch detach-age lookup failed, skipping %s "
                    "volume(s): %s",
                    region,
                    len(batch),
                    e,
                )
                continue

            attached_recently = {
                result["Id"] for result in results if result.get("Values")
            }
            for query_id, volume in by_query_id.items():
                if query_id in attached_recently:
                    logging.info(
                        "Skipping volume %s: attached within the last %s",
                        volume["VolumeId"],
                        window,
                    )
                else:
                    kept.append(volume)

        return kept

    def _delete_volume(self, client, region: str, volume: dict) -> None:
        volume_id = volume.get("VolumeId")
        tag_map = self._tag_map(volume.get("Tags"))
        name = tag_map.get("Name") or volume_id

        label = f"{region}/{volume_id}"
        if tag_map.get("yb_task"):
            label = f"{label} yb_task={tag_map['yb_task']}"
        elif name != volume_id:
            label = f"{label} Name={name}"

        if self.dry_run:
            logging.info("Dry run: would delete available volume %s", label)
            self.disks_to_delete.append(label)
            return

        try:
            client.delete_volume(VolumeId=volume_id)
            logging.info("Deleted volume %s", label)
            self.disks_to_delete.append(label)
        except ClientError as e:
            code = (e.response.get("Error") or {}).get("Code")
            logging.error("Failed to delete volume %s (%s): %s", volume_id, code, e)
