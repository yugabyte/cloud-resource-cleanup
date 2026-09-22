# Copyright (c) Yugabyte, Inc.

import datetime
import fnmatch
import logging
import re
from typing import Dict, List, Optional, Tuple

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

    * ``age`` is measured from ``CreateTime``.
    * ``detach_age`` skips a volume if AWS/EBS CloudWatch metrics show it was
      attached inside that window. Metrics are published only while a volume
      is attached to a *running* instance. Incomplete CloudWatch answers fail
      closed (the volume is kept).

    CreateTime is always applied: when ``age`` is omitted, ``detach_age`` is
    used as the creation floor so a volume created seconds ago cannot pass.

    Attached and Multi-Attach volumes are never deleted. Infra/prod/vpn
    yb_task values and Kubernetes CSI volumes are skipped unless the caller
    explicitly filter_tags on a kubernetes key. Untagged volumes are skipped
    unless ``--notags`` is set.
    """

    service_name = "ec2"
    default_region_name = "us-west-2"
    AGE_KEYS = {"days", "hours"}

    # CloudWatch retains EBS metrics for 455 days. A longer detach_age cannot
    # be answered, so it is rejected rather than silently shortened.
    CW_MAX_LOOKBACK = datetime.timedelta(days=455)
    CW_BATCH_SIZE = 50
    EBS_METRICS = ("VolumeIdleTime", "VolumeReadOps", "VolumeWriteOps")

    # Defensive skips even if Jenkins forgets --exception_tags. Keys and
    # values are compared casefolded; values also match as tokens/prefixes.
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
        self.custom_age_tag_key = custom_age_tag_key
        self.notags = notags or {}
        self.name_regex = name_regex or []
        self.exception_regex = exception_regex or []
        self._had_errors = False

        self.age = self._normalize_age(age, "age")
        self.detach_age = self._normalize_age(detach_age, "detach_age")
        self._compiled_name_regex = self._compile_regexes(self.name_regex, "name_regex")
        self._compiled_exception_regex = self._compile_regexes(
            self.exception_regex, "exception_regex"
        )

        # Service.is_old() returns True for an empty age, so without this an
        # age-less run would delete every available volume in every region.
        if not self.age and not self.detach_age:
            raise ValueError(
                "AWS disk cleanup requires an age gate: pass --age (measured from "
                "volume creation) and/or --detach_age (measured from last attachment)."
            )

        if self.detach_age:
            window = self._age_to_timedelta(self.detach_age)
            if window > self.CW_MAX_LOOKBACK:
                raise ValueError(
                    "AWS --detach_age %s exceeds CloudWatch EBS metric retention "
                    "(%s). Shorten detach_age; a longer window cannot be evaluated."
                    % (window, self.CW_MAX_LOOKBACK)
                )

    @property
    def get_deleted(self):
        return self.disks_to_delete

    @property
    def count(self):
        count = len(self.disks_to_delete)
        logging.info(f"count of items in disks_to_delete: {count}")
        return count

    def _compile_regexes(self, patterns: List[str], label: str) -> List[re.Pattern]:
        compiled = []
        for pattern in patterns:
            try:
                compiled.append(re.compile(pattern))
            except re.error as e:
                raise ValueError("Invalid %s pattern %r: %s" % (label, pattern, e)) from e
        return compiled

    def _normalize_age(self, age, label: str) -> Dict[str, int]:
        if not age:
            return {}
        if isinstance(age, int):
            age = {"days": age}
        extra = set(age) - self.AGE_KEYS
        if extra:
            raise ValueError(
                "%s has unsupported keys %s; use days and/or hours"
                % (label, sorted(extra))
            )
        if not (set(age) & self.AGE_KEYS):
            raise ValueError("%s must include days and/or hours" % label)
        normalized = {}
        for key, value in age.items():
            try:
                number = int(value)
            except (TypeError, ValueError) as e:
                raise ValueError("%s %s must be an integer" % (label, key)) from e
            if number < 0:
                raise ValueError("%s %s cannot be negative" % (label, key))
            normalized[key] = number
        if not self._age_to_timedelta(normalized):
            raise ValueError("%s must be greater than zero" % label)
        return normalized

    def _tag_map(self, tags: Optional[List[Dict[str, str]]]) -> Dict[str, str]:
        return {t["Key"]: t.get("Value", "") for t in tags or [] if t.get("Key")}

    def _lookup_tag(self, tag_map: Dict[str, str], *names: str) -> str:
        wanted = {name.casefold().replace("-", "_") for name in names}
        for key, value in tag_map.items():
            if key.casefold().replace("-", "_") in wanted:
                return (value or "").strip()
        return ""

    def _is_sensitive_yb_task(self, value: str) -> bool:
        folded = value.casefold().strip()
        if not folded:
            return False
        if folded in self.SENSITIVE_YB_TASK:
            return True
        for token in re.split(r"[-_\s/]+", folded):
            if token in self.SENSITIVE_YB_TASK:
                return True
        for sensitive in self.SENSITIVE_YB_TASK:
            if folded.startswith(sensitive + "-") or folded.startswith(sensitive + "_"):
                return True
        return False

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
        # Match EC2 tag-filter glob semantics (* and ?) on the client too.
        for key, values in self.filter_tags.items():
            if key not in tag_map:
                return False
            if values and not any(
                fnmatch.fnmatchcase(tag_map[key], pattern) for pattern in values
            ):
                return False
        return True

    def _matches_name_regex(self, name: str) -> bool:
        if not self._compiled_name_regex:
            return True
        return any(pattern.search(name or "") for pattern in self._compiled_name_regex)

    def _matches_exception_regex(self, name: str) -> bool:
        if not self._compiled_exception_regex:
            return False
        return any(
            pattern.search(name or "") for pattern in self._compiled_exception_regex
        )

    def _should_skip(self, tag_map: Dict[str, str], name: str, volume_id: str) -> bool:
        yb_task = self._lookup_tag(tag_map, "yb_task", "yb-task")

        if self._is_sensitive_yb_task(yb_task):
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
        self._had_errors = False
        for region in get_all_regions(self.service_name, self.default_region_name):
            try:
                client = boto3.client(
                    self.service_name, region_name=region, config=_BOTO_CFG
                )
                candidates = []
                paginator = client.get_paginator("describe_volumes")
                for page in paginator.paginate(Filters=self._volume_filters()):
                    for volume in page.get("Volumes") or []:
                        try:
                            if self._is_candidate(volume):
                                candidates.append(volume)
                        except Exception as e:
                            self._had_errors = True
                            logging.error(
                                "Region %s: skipping volume %s after error: %s",
                                region,
                                volume.get("VolumeId"),
                                e,
                            )

                for volume in self._drop_recently_attached(region, candidates):
                    try:
                        self._delete_volume(client, region, volume)
                    except Exception as e:
                        self._had_errors = True
                        logging.error(
                            "Region %s: failed considering volume %s: %s",
                            region,
                            volume.get("VolumeId"),
                            e,
                        )
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
                self._had_errors = True
                logging.error("Region %s: ClientError during EBS disk cleanup: %s", region, e)
            except Exception as e:
                self._had_errors = True
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

        if self._had_errors:
            raise RuntimeError(
                "AWS EBS disk cleanup did not complete for every volume; "
                "see errors above. Deleted/dry-run list is incomplete."
            )

    def _is_candidate(self, volume: dict) -> bool:
        """Tag, name and creation-age checks. Detach age is applied separately."""
        volume_id = volume.get("VolumeId")
        if volume.get("State") != "available":
            return False
        if volume.get("Attachments"):
            logging.info("Skipping volume %s: still has attachments", volume_id)
            return False
        if volume.get("MultiAttachEnabled"):
            logging.info("Skipping volume %s: Multi-Attach enabled", volume_id)
            return False

        tags = volume.get("Tags")
        if not tags:
            if not self.notags:
                logging.info("Skipping volume %s: untagged", volume_id)
                return False

        tag_map = self._tag_map(tags)
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

        # Always apply a CreateTime floor. detach_age-only mode still uses
        # detach_age here so a never-attached volume created seconds ago
        # cannot pass.
        age = retention_age or self.age or self.detach_age
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

    def _cloudwatch_period(self, window: datetime.timedelta) -> int:
        seconds = max(60, int(window.total_seconds()))
        return seconds - (seconds % 60) or 60

    def _get_metric_data(self, cloudwatch, queries, start, end) -> List[dict]:
        results = []
        next_token = None
        while True:
            kwargs = {
                "MetricDataQueries": queries,
                "StartTime": start,
                "EndTime": end,
                "ScanBy": "TimestampDescending",
            }
            if next_token:
                kwargs["NextToken"] = next_token
            response = cloudwatch.get_metric_data(**kwargs)
            results.extend(response.get("MetricDataResults") or [])
            next_token = response.get("NextToken")
            if not next_token:
                return results

    def _volume_recently_attached(
        self, volume_id: str, query_ids: List[str], results_by_id: Dict[str, dict]
    ) -> Tuple[bool, Optional[str]]:
        """
        Returns (skip_volume, reason). skip_volume True means do not delete.
        """
        for query_id in query_ids:
            result = results_by_id.get(query_id)
            if not result:
                return True, "missing CloudWatch result"
            status = result.get("StatusCode") or "Complete"
            if status != "Complete":
                return True, "CloudWatch StatusCode=%s" % status
            if result.get("Values"):
                return True, "attached recently"
        return False, None

    def _drop_recently_attached(self, region: str, volumes: List[dict]) -> List[dict]:
        """
        Drop volumes with AWS/EBS datapoints inside the detach_age window, or
        whose CloudWatch answer is incomplete. Empty Complete series means no
        metric in the window (treated as detached for that window).
        """
        if not self.detach_age or not volumes:
            return volumes

        window = self._age_to_timedelta(self.detach_age)
        if not window:
            logging.warning(
                "Region %s: detach_age window is zero, keeping all %s volume(s)",
                region,
                len(volumes),
            )
            return []

        end = datetime.datetime.now(datetime.timezone.utc)
        start = end - window
        period = self._cloudwatch_period(window)
        cloudwatch = boto3.client("cloudwatch", region_name=region, config=_BOTO_CFG)

        kept = []
        for index in range(0, len(volumes), self.CW_BATCH_SIZE):
            batch = volumes[index : index + self.CW_BATCH_SIZE]
            queries = []
            query_ids_by_volume = {}
            for i, volume in enumerate(batch):
                ids = []
                for metric in self.EBS_METRICS:
                    query_id = "m%s%s" % (i, metric.replace("Volume", "").lower()[:8])
                    ids.append(query_id)
                    queries.append(
                        {
                            "Id": query_id,
                            "MetricStat": {
                                "Metric": {
                                    "Namespace": "AWS/EBS",
                                    "MetricName": metric,
                                    "Dimensions": [
                                        {
                                            "Name": "VolumeId",
                                            "Value": volume["VolumeId"],
                                        }
                                    ],
                                },
                                "Period": period,
                                "Stat": "Sum",
                            },
                        }
                    )
                query_ids_by_volume[volume["VolumeId"]] = ids

            try:
                results = self._get_metric_data(cloudwatch, queries, start, end)
            except (ClientError, *CONNECTIVITY_ERRORS) as e:
                logging.warning(
                    "Region %s: CloudWatch detach-age lookup failed, skipping %s "
                    "volume(s): %s",
                    region,
                    len(batch),
                    e,
                )
                continue

            results_by_id = {result["Id"]: result for result in results if result.get("Id")}
            for volume in batch:
                skip, reason = self._volume_recently_attached(
                    volume["VolumeId"],
                    query_ids_by_volume[volume["VolumeId"]],
                    results_by_id,
                )
                if skip:
                    logging.info(
                        "Skipping volume %s: %s (detach_age window %s)",
                        volume["VolumeId"],
                        reason,
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
        yb_task = self._lookup_tag(tag_map, "yb_task", "yb-task")
        if yb_task:
            label = f"{label} yb_task={yb_task}"
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
            self._had_errors = True
