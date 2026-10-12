# Copyright (c) Yugabyte, Inc.

import datetime
import fnmatch
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

    Matching cancelled requests that still have an InstanceId are cleaned up
    too (CRC retry after a partial run, or a hand-cancelled request whose
    instance AWS left running). Untagged cancelled requests stay skipped.
    """

    service_name = "ec2"
    default_region_name = "us-west-2"
    # Persisted on the spot request before terminate so a later retry can still
    # find DeleteOnTermination=false volumes after BlockDeviceMappings are gone.
    LEFTOVER_VOLUMES_TAG = "crc_leftover_ebs"
    # How long to wait for a volume to leave 'in-use' after terminate.
    VOLUME_AVAILABLE_WAIT_SEC = 180
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
        self.age = self.require_positive_age(age)
        self.custom_age_tag_key = custom_age_tag_key
        self.notags = notags or {}

    @property
    def get_deleted(self):
        # Requests + instances only; leftover EBS reported via volumes_to_delete.
        return self.spot_requests_to_delete + self.instance_ids_to_delete

    @property
    def delete_count(self):
        count = len(self.get_deleted) + len(self.volumes_to_delete)
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
    ) -> List[Tuple[str, Optional[str], str, bool, List[Dict[str, str]]]]:
        """
        Returns list of
        (request_id, instance_id|None, type, already_cancelled, tags).
        Type is one-time or persistent (AWS SpotInstanceRequests.Type).

        Cancelled requests with an InstanceId are also queued. That covers a
        prior CRC run that cancelled but failed terminate / leftover-volume
        delete, **and** hand-cancelled requests whose instance AWS left
        running — matching filter/age requests in ``cancelled`` with an
        InstanceId will have that instance terminated and DoT=false volumes
        deleted. Untagged cancelled requests remain skipped.
        """
        matches = []
        for request in spot_request_details.get("SpotInstanceRequests", []):
            try:
                tags = request.get("Tags") or []
                # Untagged requests are never cleaned (fail-closed), same as the
                # original module guard — Jenkins always tags spot requests.
                if not tags:
                    continue

                already_cancelled = request.get("State") == "cancelled"
                instance_id = request.get("InstanceId") or None
                if already_cancelled and not instance_id:
                    continue

                if self._should_skip_spot_request(tags):
                    continue
                if self.filter_tags and not self._matches_filter_tags(tags):
                    continue

                request_id = request["SpotInstanceRequestId"]
                create_time = request["CreateTime"]
                request_type = (request.get("Type") or "one-time").lower()

                retention_age = self.validated_retention_age(
                    tags, self.custom_age_tag_key
                )
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

                matches.append(
                    (request_id, instance_id, request_type, already_cancelled, tags)
                )
                logging.info(
                    "Spot request %s (type=%s, cancelled=%s) instance=%s queued",
                    request_id,
                    request_type,
                    already_cancelled,
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
        # Match EC2 tag-filter glob semantics (* and ?) on the client too
        # (same as crc/aws/disk.py).
        for key, values in self.filter_tags.items():
            if key not in tag_map:
                return False
            if values and not any(
                fnmatch.fnmatchcase(tag_map[key], pattern) for pattern in values
            ):
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

    def _instance_state_and_dot_false_volumes(
        self, client, instance_id: str
    ) -> Tuple[Optional[str], List[str]]:
        """
        Return (instance_state|None, DeleteOnTermination=false volume ids).
        state is None when the instance is not found.
        """
        try:
            resp = client.describe_instances(InstanceIds=[instance_id])
        except ClientError as e:
            code = (e.response.get("Error") or {}).get("Code")
            if code in {
                "InvalidInstanceID.NotFound",
                "InvalidInstanceID.Malformed",
            }:
                return None, []
            logging.error(
                "describe_instances failed for %s while checking "
                "DeleteOnTermination: %s",
                instance_id,
                e,
            )
            return "unknown", []

        volume_ids: List[str] = []
        state = None
        for reservation in resp.get("Reservations") or []:
            for instance in reservation.get("Instances") or []:
                state = (instance.get("State") or {}).get("Name")
                for mapping in instance.get("BlockDeviceMappings") or []:
                    ebs = mapping.get("Ebs") or {}
                    volume_id = ebs.get("VolumeId")
                    if not volume_id:
                        continue
                    # Explicit False only — missing defaults to True for root
                    if ebs.get("DeleteOnTermination") is False:
                        volume_ids.append(volume_id)
                        logging.info(
                            "Instance %s volume %s has DeleteOnTermination=false",
                            instance_id,
                            volume_id,
                        )
        return state, volume_ids

    def _leftover_volumes_from_request_tags(
        self, tags: List[Dict[str, str]]
    ) -> List[str]:
        tag_map = {t["Key"]: t.get("Value", "") for t in tags if t.get("Key")}
        raw = (tag_map.get(self.LEFTOVER_VOLUMES_TAG) or "").strip()
        if not raw:
            return []
        return [v.strip() for v in raw.split(",") if v.strip()]

    def _tag_leftover_volumes_on_request(
        self, client, request_id: str, volume_ids: List[str]
    ) -> bool:
        """Persist leftover volume IDs. Returns False if tagging failed."""
        if not volume_ids:
            return True
        try:
            client.create_tags(
                Resources=[request_id],
                Tags=[
                    {
                        "Key": self.LEFTOVER_VOLUMES_TAG,
                        "Value": ",".join(volume_ids),
                    }
                ],
            )
            logging.info(
                "Tagged spot request %s with leftover volumes %s",
                request_id,
                volume_ids,
            )
            return True
        except ClientError as e:
            logging.error(
                "Failed to tag leftover volumes on spot request %s: %s",
                request_id,
                e,
            )
            return False

    def _clear_leftover_volumes_tag(self, client, request_id: str) -> None:
        try:
            client.delete_tags(
                Resources=[request_id],
                Tags=[{"Key": self.LEFTOVER_VOLUMES_TAG}],
            )
            logging.info(
                "Cleared %s tag on spot request %s",
                self.LEFTOVER_VOLUMES_TAG,
                request_id,
            )
        except ClientError as e:
            logging.error(
                "Failed to clear %s on spot request %s: %s",
                self.LEFTOVER_VOLUMES_TAG,
                request_id,
                e,
            )

    def _wait_volume_available_then_delete(
        self, client, volume_id: str
    ) -> Optional[str]:
        """
        Wait for available and delete. Returns ``\"deleted\"`` when this run
        issued delete_volume, ``\"already_gone\"`` when the volume is already
        missing/deleted (do not count as a new deletion), or None on failure.
        """
        deadline = time.time() + self.VOLUME_AVAILABLE_WAIT_SEC
        while time.time() < deadline:
            try:
                desc = client.describe_volumes(VolumeIds=[volume_id])
                volumes = desc.get("Volumes") or []
                if not volumes:
                    logging.info("Volume %s already gone", volume_id)
                    return "already_gone"
                state = volumes[0].get("State")
                if state == "available":
                    client.delete_volume(VolumeId=volume_id)
                    logging.info("Deleted leftover volume %s", volume_id)
                    return "deleted"
                if state == "deleted":
                    return "already_gone"
                logging.info(
                    "Waiting for volume %s to become available (state=%s)",
                    volume_id,
                    state,
                )
            except ClientError as e:
                code = (e.response.get("Error") or {}).get("Code")
                if code in {"InvalidVolume.NotFound"}:
                    return "already_gone"
                logging.error("Error waiting/deleting volume %s: %s", volume_id, e)
                return None
            time.sleep(self.VOLUME_POLL_INTERVAL_SEC)

        logging.error(
            "Timed out waiting for volume %s to become available; not deleted",
            volume_id,
        )
        return None

    def _cleanup_one(
        self,
        client,
        region: str,
        request_id: str,
        instance_id: Optional[str],
        request_type: str,
        already_cancelled: bool = False,
        request_tags: Optional[List[Dict[str, str]]] = None,
    ) -> None:
        """
        Cancel the spot request first, then terminate its instance, then delete
        any volumes left behind by DeleteOnTermination=false.

        When ``already_cancelled`` is True (retry after a prior cancel), skip
        cancel and finish terminate + leftover volume deletes. Volume IDs are
        read from BlockDeviceMappings when present, otherwise from the
        ``crc_leftover_ebs`` tag written before the first terminate.
        """
        request_tags = request_tags or []
        instance_state: Optional[str] = None
        leftover_volumes: List[str] = []
        if instance_id:
            instance_state, leftover_volumes = (
                self._instance_state_and_dot_false_volumes(client, instance_id)
            )
            # Fail closed: if describe failed, do not cancel/terminate — we
            # would lose DeleteOnTermination=false volume IDs permanently.
            if instance_state == "unknown":
                logging.error(
                    "Aborting cleanup of spot request %s: describe_instances "
                    "failed for %s (volume list unreliable)",
                    request_id,
                    instance_id,
                )
                return
        if not leftover_volumes:
            leftover_volumes = self._leftover_volumes_from_request_tags(request_tags)

        instance_done = instance_state in {None, "terminated", "shutting-down"}
        if already_cancelled and instance_done and not leftover_volumes:
            logging.info(
                "Skipping cancelled spot request %s: instance %s already "
                "gone/terminated and no leftover volumes to delete",
                request_id,
                instance_id or "none",
            )
            return

        if self.dry_run:
            self.spot_requests_to_delete.append(request_id)
            if instance_id and not instance_done:
                self.instance_ids_to_delete.append(instance_id)
            self.volumes_to_delete.extend(leftover_volumes)
            logging.info(
                "Dry run: would %s spot request %s (type=%s), terminate %s, "
                "delete leftover volumes %s",
                "skip-cancel (already cancelled)" if already_cancelled else "cancel",
                request_id,
                request_type,
                instance_id or "none",
                leftover_volumes,
            )
            return

        if not already_cancelled:
            try:
                client.cancel_spot_instance_requests(
                    SpotInstanceRequestIds=[request_id]
                )
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
        else:
            logging.info(
                "Spot request %s already cancelled in %s; finishing "
                "terminate/leftover volumes",
                request_id,
                region,
            )

        if not instance_id:
            return

        # Persist volume ids before terminate so a later retry can find them
        # after BlockDeviceMappings disappear. If tagging fails, do not
        # terminate — a timed-out volume wait would then orphan them.
        if leftover_volumes and not instance_done:
            if not self._tag_leftover_volumes_on_request(
                client, request_id, leftover_volumes
            ):
                logging.error(
                    "Not terminating instance %s for spot request %s — "
                    "failed to persist leftover volume IDs",
                    instance_id,
                    request_id,
                )
                return

        if not instance_done:
            try:
                client.terminate_instances(InstanceIds=[instance_id])
                self.instance_ids_to_delete.append(instance_id)
                logging.info(
                    "Terminated instance %s for cancelled spot request %s",
                    instance_id,
                    request_id,
                )
            except ClientError as e:
                code = (e.response.get("Error") or {}).get("Code")
                if code not in {
                    "InvalidInstanceID.NotFound",
                    "InvalidInstanceID.Malformed",
                }:
                    logging.error(
                        "Failed to terminate instance %s after cancelling %s: %s",
                        instance_id,
                        request_id,
                        e,
                    )
                    return
                logging.info(
                    "Instance %s already gone for spot request %s",
                    instance_id,
                    request_id,
                )
        else:
            logging.info(
                "Instance %s already %s for spot request %s; only leftover "
                "volumes will be deleted",
                instance_id,
                instance_state or "missing",
                request_id,
            )

        deleted_any_volume = False
        still_pending: List[str] = []
        for volume_id in leftover_volumes:
            result = self._wait_volume_available_then_delete(client, volume_id)
            if result == "deleted":
                self.volumes_to_delete.append(volume_id)
                deleted_any_volume = True
            elif result == "already_gone":
                continue
            else:
                still_pending.append(volume_id)

        # Keep the tag only for volumes that still need work; clear when done
        # so later runs do not re-report already-deleted volumes.
        if still_pending:
            self._tag_leftover_volumes_on_request(client, request_id, still_pending)
        elif leftover_volumes:
            self._clear_leftover_volumes_tag(client, request_id)

        # Count the cancelled request only when this run did real work (first
        # cancel, a terminate, or a leftover volume delete) — avoids re-reporting
        # already-finished retries in Slack/Influx.
        if not already_cancelled:
            return
        if deleted_any_volume or (instance_id in self.instance_ids_to_delete):
            self.spot_requests_to_delete.append(request_id)

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
                for (
                    request_id,
                    instance_id,
                    request_type,
                    already_cancelled,
                    request_tags,
                ) in matches:
                    try:
                        self._cleanup_one(
                            client,
                            region,
                            request_id,
                            instance_id,
                            request_type,
                            already_cancelled=already_cancelled,
                            request_tags=request_tags,
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
