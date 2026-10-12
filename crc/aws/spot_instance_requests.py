# Copyright (c) Yugabyte, Inc.

import datetime
import logging
import time
from typing import Dict, List, Optional, Tuple

import boto3
from botocore.exceptions import ClientError

from crc.aws._base import get_all_regions
from crc.aws.connectivity import CONNECTIVITY_ERRORS, log_skipped_region
from crc.service import Service


class SpotInstanceRequests(Service):
    """
    Clean AWS Spot Instance Requests and their fulfilled instances.

    Tags are read from the **spot request** (not the instance). That matches
    YBA fleets where yb_owner / yb_yba_url / universe-uuid live on the request
    while the EC2 instance is untagged.

    Order of operations (critical for Type=persistent):
      1. cancel_spot_instance_requests
      2. terminate_instances
    Terminating first leaves a persistent request in open/active and AWS
    launches a replacement.

    After terminate, any EBS volume whose BlockDeviceMapping had
    DeleteOnTermination=false is deleted explicitly.
    """

    service_name = "ec2"
    default_region_name = "us-west-2"
    # How long to wait for a volume to leave 'in-use' after terminate.
    VOLUME_AVAILABLE_WAIT_SEC = 120
    VOLUME_POLL_INTERVAL_SEC = 5

    def __init__(
        self,
        dry_run: bool,
        filter_tags: Dict[str, List[str]],
        exception_tags: Dict[str, List[str]],
        age: Dict[str, int],
        custom_age_tag_key: str,
        notags: Dict[str, List[str]],
    ) -> None:
        super().__init__()
        self.spot_requests_to_delete: List[str] = []
        self.instance_ids_to_delete: List[str] = []
        self.volumes_to_delete: List[str] = []
        self.dry_run = dry_run
        self.filter_tags = filter_tags or {}
        self.exception_tags = exception_tags or {}
        self.age = age or {}
        self.custom_age_tag_key = custom_age_tag_key
        self.notags = notags or {}

    @property
    def get_deleted(self):
        return (
            self.spot_requests_to_delete
            + self.instance_ids_to_delete
            + self.volumes_to_delete
        )

    @property
    def delete_count(self):
        count = len(self.get_deleted)
        logging.info(
            "count of spot requests+instances+leftover volumes to delete: %s", count
        )
        return count

    def _get_filter(self) -> List[Dict[str, List[str]]]:
        filters = []
        if self.filter_tags:
            for key, value in self.filter_tags.items():
                filters.append({"Name": f"tag:{key}", "Values": value})
        logging.info("Filters created: %s", filters)
        return filters

    def _get_filtered_requests(
        self, spot_request_details: dict
    ) -> List[Tuple[str, Optional[str], str]]:
        """
        Returns list of (request_id, instance_id|None, type) for matching requests.
        Type is one-time or persistent (AWS SpotInstanceRequests.Type).
        """
        matches = []
        for request in spot_request_details.get("SpotInstanceRequests", []):
            try:
                tags = request.get("Tags") or []
                # Untagged requests cannot match filter_tags / exception / notags
                # semantics used by Jenkins; skip unless no tag filters at all.
                if not tags and (self.filter_tags or self.exception_tags or self.notags):
                    continue
                if request.get("State") == "cancelled":
                    continue
                if self._should_skip_spot_request(tags):
                    continue
                if self.filter_tags and not self._matches_filter_tags(tags):
                    continue

                request_id = request["SpotInstanceRequestId"]
                instance_id = request.get("InstanceId") or None
                create_time = request["CreateTime"]
                request_type = (request.get("Type") or "one-time").lower()

                retention_age = self.get_retention_age(tags, self.custom_age_tag_key)
                if retention_age:
                    logging.info(
                        "Updating age for SpotInstanceRequestId: %s", request_id
                    )

                if not self.is_old(
                    retention_age or self.age,
                    datetime.datetime.now().astimezone(create_time.tzinfo),
                    create_time,
                ):
                    continue

                matches.append((request_id, instance_id, request_type))
                logging.info(
                    "Spot request %s (type=%s) instance=%s queued for cleanup",
                    request_id,
                    request_type,
                    instance_id or "none",
                )
            except Exception as e:
                logging.error(
                    "Error occurred while processing spot request %s: %s", request, e
                )
        return matches

    def _matches_filter_tags(self, tags: List[Dict[str, str]]) -> bool:
        if not self.filter_tags:
            return True
        tag_map = {t["Key"]: t.get("Value", "") for t in tags if t.get("Key")}
        # AND across keys (same as other AWS resources that re-check client-side)
        for key, values in self.filter_tags.items():
            if key not in tag_map:
                return False
            if values and tag_map[key] not in values:
                return False
        return True

    def _should_skip_spot_request(self, tags: List[Dict[str, str]]) -> bool:
        if not self.exception_tags and not self.notags:
            return False

        all_tags = {}
        for tag in tags:
            k = tag.get("Key")
            if not k:
                continue
            v = tag.get("Value", "")
            if self.exception_tags:
                if k in self.exception_tags and (
                    not self.exception_tags[k] or v in self.exception_tags[k]
                ):
                    return True
            all_tags[k] = v

        if self.notags:
            return all(
                key in all_tags and (not value or all_tags[key] in value)
                for key, value in self.notags.items()
            )
        return False

    def _volumes_with_delete_on_termination_false(
        self, client, instance_id: str
    ) -> List[str]:
        """EBS VolumeIds attached with DeleteOnTermination=false."""
        try:
            resp = client.describe_instances(InstanceIds=[instance_id])
        except ClientError as e:
            logging.error(
                "describe_instances failed for %s while checking DeleteOnTermination: %s",
                instance_id,
                e,
            )
            return []

        volume_ids = []
        for reservation in resp.get("Reservations") or []:
            for instance in reservation.get("Instances") or []:
                for mapping in instance.get("BlockDeviceMappings") or []:
                    ebs = mapping.get("Ebs") or {}
                    volume_id = ebs.get("VolumeId")
                    if not volume_id:
                        continue
                    # Explicit False only — missing defaults to True for root in many AMIs
                    if ebs.get("DeleteOnTermination") is False:
                        volume_ids.append(volume_id)
                        logging.info(
                            "Instance %s volume %s has DeleteOnTermination=false",
                            instance_id,
                            volume_id,
                        )
        return volume_ids

    def _wait_volume_available_then_delete(self, client, volume_id: str) -> bool:
        deadline = time.time() + self.VOLUME_AVAILABLE_WAIT_SEC
        while time.time() < deadline:
            try:
                desc = client.describe_volumes(VolumeIds=[volume_id])
                volumes = desc.get("Volumes") or []
                if not volumes:
                    logging.info("Volume %s already gone", volume_id)
                    return True
                state = volumes[0].get("State")
                if state == "available":
                    client.delete_volume(VolumeId=volume_id)
                    logging.info("Deleted leftover volume %s", volume_id)
                    return True
                if state == "deleted":
                    return True
                logging.info(
                    "Waiting for volume %s to become available (state=%s)",
                    volume_id,
                    state,
                )
            except ClientError as e:
                code = (e.response.get("Error") or {}).get("Code")
                if code in {"InvalidVolume.NotFound"}:
                    return True
                logging.error("Error waiting/deleting volume %s: %s", volume_id, e)
                return False
            time.sleep(self.VOLUME_POLL_INTERVAL_SEC)

        logging.error(
            "Timed out waiting for volume %s to become available; not deleted",
            volume_id,
        )
        return False

    def _cleanup_one(
        self,
        client,
        region: str,
        request_id: str,
        instance_id: Optional[str],
        request_type: str,
    ) -> None:
        """
        Cancel the spot request first, then terminate its instance, then delete
        any volumes left behind by DeleteOnTermination=false.
        """
        leftover_volumes: List[str] = []
        if instance_id:
            leftover_volumes = self._volumes_with_delete_on_termination_false(
                client, instance_id
            )

        if self.dry_run:
            self.spot_requests_to_delete.append(request_id)
            if instance_id:
                self.instance_ids_to_delete.append(instance_id)
            self.volumes_to_delete.extend(leftover_volumes)
            logging.info(
                "Dry run: would cancel spot request %s (type=%s), terminate %s, "
                "delete leftover volumes %s",
                request_id,
                request_type,
                instance_id or "none",
                leftover_volumes,
            )
            return

        try:
            client.cancel_spot_instance_requests(SpotInstanceRequestIds=[request_id])
            self.spot_requests_to_delete.append(request_id)
            logging.info(
                "Cancelled spot request %s (type=%s) in %s",
                request_id,
                request_type,
                region,
            )
        except ClientError as e:
            logging.error(
                "Failed to cancel spot request %s in %s: %s — not terminating "
                "its instance (persistent requests would relaunch)",
                request_id,
                region,
                e,
            )
            return

        if not instance_id:
            return

        try:
            client.terminate_instances(InstanceIds=[instance_id])
            self.instance_ids_to_delete.append(instance_id)
            logging.info(
                "Terminated instance %s for cancelled spot request %s",
                instance_id,
                request_id,
            )
        except ClientError as e:
            logging.error(
                "Failed to terminate instance %s after cancelling %s: %s",
                instance_id,
                request_id,
                e,
            )
            return

        for volume_id in leftover_volumes:
            if self._wait_volume_available_then_delete(client, volume_id):
                self.volumes_to_delete.append(volume_id)

    def delete(self) -> None:
        spot_filter = self._get_filter()
        try:
            regions = get_all_regions(self.service_name, self.default_region_name)
        except Exception as e:
            logging.error("Failed to list AWS regions for EC2: %s", e)
            return

        for region in regions:
            try:
                client = boto3.client(self.service_name, region_name=region)
                try:
                    describe_spot_response = self._describe_all_spot_requests(
                        client, spot_filter
                    )
                except ClientError as e:
                    logging.error(
                        "Region %s: describe_spot_instance_requests failed (%s): %s",
                        region,
                        e.response.get("Error", {}).get("Code", "Unknown"),
                        e,
                    )
                    continue
                except Exception as e:
                    logging.error(
                        "Region %s: describe_spot_instance_requests failed: %s",
                        region,
                        e,
                    )
                    continue

                matches = self._get_filtered_requests(describe_spot_response)
                for request_id, instance_id, request_type in matches:
                    try:
                        self._cleanup_one(
                            client, region, request_id, instance_id, request_type
                        )
                    except Exception as e:
                        logging.error(
                            "Region %s: failed cleaning spot request %s: %s",
                            region,
                            request_id,
                            e,
                        )
            except CONNECTIVITY_ERRORS as e:
                log_skipped_region(region, "spot instance requests", e)
                continue

        if not self.spot_requests_to_delete:
            logging.warning("No SpotInstanceRequest to delete.")

        label = "which will be deleted" if self.dry_run else "deleted"
        logging.warning(
            "List of AWS SpotInstanceRequest (Total: %s) %s: %s",
            len(self.spot_requests_to_delete),
            label,
            self.spot_requests_to_delete,
        )
        logging.warning(
            "List of AWS SpotInstanceRequest Instances (Total: %s) %s: %s",
            len(self.instance_ids_to_delete),
            label,
            self.instance_ids_to_delete,
        )
        logging.warning(
            "List of AWS leftover EBS volumes (DeleteOnTermination=false) "
            "(Total: %s) %s: %s",
            len(self.volumes_to_delete),
            label,
            self.volumes_to_delete,
        )

    def _describe_all_spot_requests(
        self, client, spot_filter: List[Dict[str, List[str]]]
    ) -> dict:
        kwargs = {}
        if spot_filter:
            kwargs["Filters"] = spot_filter
        all_requests = []
        next_token = None
        while True:
            if next_token:
                kwargs["NextToken"] = next_token
            resp = client.describe_spot_instance_requests(**kwargs)
            all_requests.extend(resp.get("SpotInstanceRequests", []))
            next_token = resp.get("NextToken")
            if not next_token:
                break
        return {"SpotInstanceRequests": all_requests}
