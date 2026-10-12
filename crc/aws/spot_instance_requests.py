# Copyright (c) Yugabyte, Inc.

import datetime
import fnmatch
import logging
import time
from typing import Dict, List, Optional, Tuple

import boto3
from botocore.exceptions import BotoCoreError, ClientError

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
    DeleteOnTermination=false is deleted explicitly. Those volumes are tagged
    ``crc_leftover_of=<spot-request-id>`` (plus filter/exception/notags keys)
    before terminate so a later run can still find *available* orphans via
    ``describe_volumes`` after AWS drops the spot request (~4h). The region
    sweep re-applies those gates; in-use volumes are not swept.

    Matching cancelled requests that still have an InstanceId are cleaned up
    too (CRC retry after a partial run, or a hand-cancelled request whose
    instance AWS left running). Untagged cancelled requests stay skipped.
    """

    service_name = "ec2"
    default_region_name = "us-west-2"
    # Tagged onto each DoT=false volume *before* terminate. Spot requests are
    # dropped by AWS ~4h after cancel+terminate, so the volume itself must
    # carry the marker for a later run to find orphans via describe_volumes.
    LEFTOVER_FROM_TAG = "crc_leftover_of"
    # Written when we cannot clear LEFTOVER_FROM_TAG after a failed terminate;
    # sweep rejects this value so an attached volume is never deleted later.
    LEFTOVER_INVALID_VALUE = "terminate-failed"
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

    def _volume_tag_keys(self, client, volume_id: str) -> Optional[set]:
        """Return existing tag keys on a volume, or None if describe fails."""
        try:
            desc = client.describe_volumes(VolumeIds=[volume_id])
            volumes = desc.get("Volumes") or []
            if not volumes:
                return set()
            return {
                t["Key"]
                for t in (volumes[0].get("Tags") or [])
                if t.get("Key")
            }
        except ClientError as e:
            logging.error(
                "describe_volumes failed for %s while preparing leftover tags: %s",
                volume_id,
                e,
            )
            return None

    def _scope_tags_for_volume(
        self,
        request_id: str,
        request_tags: Optional[List[Dict[str, str]]],
        existing_keys: Optional[set] = None,
    ) -> List[Dict[str, str]]:
        """
        Tags written onto each leftover volume: crc_leftover_of plus any
        filter/exception/notags keys from the spot request that the volume
        does not already have (never overwrites the volume's own values).
        """
        tags: List[Dict[str, str]] = [
            {"Key": self.LEFTOVER_FROM_TAG, "Value": request_id}
        ]
        keys_needed = set(self.filter_tags)
        keys_needed.update(self.exception_tags)
        keys_needed.update(self.notags)
        if not keys_needed:
            return tags
        tag_map = {
            t["Key"]: t.get("Value", "") for t in (request_tags or []) if t.get("Key")
        }
        already = existing_keys or set()
        for key in sorted(keys_needed):
            if key in tag_map and key not in already:
                tags.append({"Key": key, "Value": tag_map[key]})
        return tags

    def _invalidate_leftover_marker(self, client, volume_ids: List[str]) -> None:
        """
        Stop a volume from being swept after a failed terminate/tag.

        Prefer DeleteTags. If that fails (e.g. missing ec2:DeleteTags), overwrite
        the marker with LEFTOVER_INVALID_VALUE via CreateTags (same permission
        used to set the marker) so the sweep rejects it.
        """
        for volume_id in volume_ids:
            try:
                client.delete_tags(
                    Resources=[volume_id],
                    Tags=[{"Key": self.LEFTOVER_FROM_TAG}],
                )
                continue
            except ClientError as e:
                logging.error(
                    "Failed to clear %s from volume %s: %s — trying sentinel",
                    self.LEFTOVER_FROM_TAG,
                    volume_id,
                    e,
                )
            try:
                client.create_tags(
                    Resources=[volume_id],
                    Tags=[
                        {
                            "Key": self.LEFTOVER_FROM_TAG,
                            "Value": self.LEFTOVER_INVALID_VALUE,
                        }
                    ],
                )
                logging.info(
                    "Marked volume %s as %s=%s (sweep will skip)",
                    volume_id,
                    self.LEFTOVER_FROM_TAG,
                    self.LEFTOVER_INVALID_VALUE,
                )
            except (ClientError, BotoCoreError) as e:
                logging.error(
                    "Failed to invalidate %s on volume %s: %s",
                    self.LEFTOVER_FROM_TAG,
                    volume_id,
                    e,
                )

    def _tag_volumes_as_leftover(
        self,
        client,
        request_id: str,
        volume_ids: List[str],
        request_tags: Optional[List[Dict[str, str]]] = None,
    ) -> bool:
        """
        Mark each DoT=false volume so a later run can find it even after the
        spot request object is gone. Returns False if any CreateTags fails
        (partial markers are invalidated first).
        """
        if not volume_ids:
            return True
        tagged: List[str] = []
        for volume_id in volume_ids:
            existing = self._volume_tag_keys(client, volume_id)
            if existing is None:
                self._invalidate_leftover_marker(client, tagged)
                return False
            tags = self._scope_tags_for_volume(
                request_id, request_tags, existing_keys=existing
            )
            try:
                client.create_tags(Resources=[volume_id], Tags=tags)
                tagged.append(volume_id)
            except ClientError as e:
                logging.error(
                    "Failed to tag leftover volume %s for spot request %s: %s",
                    volume_id,
                    request_id,
                    e,
                )
                self._invalidate_leftover_marker(client, tagged)
                return False
        logging.info(
            "Tagged leftover volumes %s with %s=%s (plus missing scope tags)",
            volume_ids,
            self.LEFTOVER_FROM_TAG,
            request_id,
        )
        return True

    def _volumes_tagged_leftover(
        self, client, request_id: Optional[str] = None
    ) -> List[str]:
        """Find volumes previously marked with crc_leftover_of (any state)."""
        return [
            vol["VolumeId"]
            for vol in self._describe_tagged_leftover_volumes(
                client, request_id=request_id, statuses=["available", "in-use"]
            )
            if vol.get("VolumeId")
        ]

    def _describe_tagged_leftover_volumes(
        self,
        client,
        request_id: Optional[str] = None,
        statuses: Optional[List[str]] = None,
    ) -> List[dict]:
        """describe_volumes for crc_leftover_of, optional request id / states."""
        filters = [{"Name": "status", "Values": statuses or ["available", "in-use"]}]
        if request_id:
            filters.append(
                {"Name": f"tag:{self.LEFTOVER_FROM_TAG}", "Values": [request_id]}
            )
        else:
            filters.append({"Name": "tag-key", "Values": [self.LEFTOVER_FROM_TAG]})
        volumes: List[dict] = []
        try:
            paginator = client.get_paginator("describe_volumes")
            for page in paginator.paginate(Filters=filters):
                volumes.extend(page.get("Volumes") or [])
        except ClientError as e:
            logging.error(
                "describe_volumes for %s leftovers failed: %s",
                self.LEFTOVER_FROM_TAG,
                e,
            )
        return volumes

    def _live_spot_request_tags(
        self, client, request_id: str
    ) -> Tuple[str, Optional[List[Dict[str, str]]]]:
        """
        Look up tags for a spot request during sweep.

        Returns ``(\"live\", tags)``, ``(\"gone\", None)`` when AWS no longer
        lists the request, or ``(\"error\", None)`` on other API failures
        (caller must fail closed and skip the volume).
        """
        try:
            resp = client.describe_spot_instance_requests(
                SpotInstanceRequestIds=[request_id]
            )
        except ClientError as e:
            code = (e.response.get("Error") or {}).get("Code")
            if code in {
                "InvalidSpotInstanceRequestID.NotFound",
                "InvalidSpotInstanceRequestID.Malformed",
            }:
                return "gone", None
            logging.error(
                "describe_spot_instance_requests for %s failed during sweep: %s",
                request_id,
                e,
            )
            return "error", None
        reqs = resp.get("SpotInstanceRequests") or []
        if not reqs:
            return "gone", None
        return "live", reqs[0].get("Tags") or []

    def _volume_passes_sweep_gates(self, client, volume: dict) -> bool:
        """
        Apply the same filter/exception/notags gates as spot requests.
        Prefer live request tags when the request still exists (so a newly
        added exception tag still protects the volume); otherwise use the
        scope tags copied onto the volume at tag time.
        """
        tag_map = {
            t["Key"]: t.get("Value", "")
            for t in (volume.get("Tags") or [])
            if t.get("Key")
        }
        request_id = tag_map.get(self.LEFTOVER_FROM_TAG)
        if not request_id or request_id == self.LEFTOVER_INVALID_VALUE:
            return False
        status, live_tags = self._live_spot_request_tags(client, request_id)
        if status == "error":
            return False
        if status == "live":
            if self._should_skip_spot_request(live_tags or []):
                return False
            if self.filter_tags and not self._matches_filter_tags(live_tags or []):
                return False
            return True
        # Request gone (~4h): gate on tags copied onto the volume.
        vol_tags = volume.get("Tags") or []
        if self._should_skip_spot_request(vol_tags):
            return False
        if self.filter_tags and not self._matches_filter_tags(vol_tags):
            return False
        return True

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
        cancel and finish terminate + leftover volume deletes. Volume IDs come
        from BlockDeviceMappings when the instance is still up, otherwise from
        volumes tagged ``crc_leftover_of=<request_id>`` before the first
        terminate (survives AWS dropping the spot request after ~4h).
        """
        instance_state: Optional[str] = None
        leftover_volumes: List[str] = []
        volumes_from_bdm = False
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
            volumes_from_bdm = bool(leftover_volumes)
        if not leftover_volumes:
            leftover_volumes = self._volumes_tagged_leftover(client, request_id)

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

        # Tag BDM volumes whenever we still have them (including shutting-down)
        # so a timed-out wait remains findable after BDMs disappear. Invalidate
        # the marker if terminate then fails so an attached volume is not swept.
        tagged_for_retry = False
        if leftover_volumes and volumes_from_bdm:
            if not self._tag_volumes_as_leftover(
                client, request_id, leftover_volumes, request_tags=request_tags
            ):
                logging.error(
                    "Not terminating instance %s for spot request %s — "
                    "failed to tag leftover volumes",
                    instance_id,
                    request_id,
                )
                return
            tagged_for_retry = True

        if not instance_done:
            terminate_ok = False
            try:
                client.terminate_instances(InstanceIds=[instance_id])
                self.instance_ids_to_delete.append(instance_id)
                logging.info(
                    "Terminated instance %s for cancelled spot request %s",
                    instance_id,
                    request_id,
                )
                terminate_ok = True
            except ClientError as e:
                code = (e.response.get("Error") or {}).get("Code")
                if code in {
                    "InvalidInstanceID.NotFound",
                    "InvalidInstanceID.Malformed",
                }:
                    logging.info(
                        "Instance %s already gone for spot request %s",
                        instance_id,
                        request_id,
                    )
                    terminate_ok = True
                else:
                    logging.error(
                        "Failed to terminate instance %s after cancelling %s: %s",
                        instance_id,
                        request_id,
                        e,
                    )
            except (BotoCoreError, Exception) as e:
                # Transport timeouts etc. are not ClientError — still invalidate.
                logging.error(
                    "Failed to terminate instance %s after cancelling %s: %s",
                    instance_id,
                    request_id,
                    e,
                )
            if not terminate_ok:
                if tagged_for_retry:
                    self._invalidate_leftover_marker(client, leftover_volumes)
                return
        else:
            logging.info(
                "Instance %s already %s for spot request %s; only leftover "
                "volumes will be deleted",
                instance_id,
                instance_state or "missing",
                request_id,
            )

        deleted_any_volume = False
        for volume_id in leftover_volumes:
            result = self._wait_volume_available_then_delete(client, volume_id)
            if result == "deleted":
                self.volumes_to_delete.append(volume_id)
                deleted_any_volume = True
            # already_gone / timeout: tagged volumes remain findable by sweep

        # Count the cancelled request only when this run did real work (first
        # cancel, a terminate, or a leftover volume delete) — avoids re-reporting
        # already-finished retries in Slack/Influx.
        if not already_cancelled:
            return
        if deleted_any_volume or (instance_id in self.instance_ids_to_delete):
            self.spot_requests_to_delete.append(request_id)

    def _sweep_tagged_leftover_volumes(self, client, region: str) -> None:
        """
        Delete *available* volumes tagged crc_leftover_of from a prior run,
        only when filter/exception/notags still allow it. Skips in-use volumes
        (still attached — not leftovers yet). Covers AWS dropping the spot
        request after ~4h.
        """
        for volume in self._describe_tagged_leftover_volumes(
            client, statuses=["available"]
        ):
            volume_id = volume.get("VolumeId")
            if not volume_id:
                continue
            if not self._volume_passes_sweep_gates(client, volume):
                logging.info(
                    "Skipping tagged leftover volume %s in %s (filters/exceptions)",
                    volume_id,
                    region,
                )
                continue
            if self.dry_run:
                if volume_id not in self.volumes_to_delete:
                    self.volumes_to_delete.append(volume_id)
                logging.info(
                    "Dry run: would delete tagged leftover volume %s in %s",
                    volume_id,
                    region,
                )
                continue
            result = self._wait_volume_available_then_delete(client, volume_id)
            if result == "deleted" and volume_id not in self.volumes_to_delete:
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
                # Orphans from prior runs whose spot request AWS already dropped.
                try:
                    self._sweep_tagged_leftover_volumes(client, region)
                except Exception as e:
                    logging.error(
                        "Region %s: failed sweeping tagged leftover volumes: %s",
                        region,
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
