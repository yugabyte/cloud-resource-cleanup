# Copyright (c) Yugabyte, Inc.

import datetime
import fnmatch
import logging
import re
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
    while the EC2 instance is untagged. DoT=false leftover volumes are also
    checked for their own exception/notags tags (and sensitive yb_task) before
    delete — same contract as ``crc/aws/disk.py``.

    Order of operations (critical for Type=persistent):
      1. tag DoT=false volumes (before cancel, so CreateTags failure is a no-op)
      2. cancel_spot_instance_requests
      3. terminate_instances
    Terminating first leaves a persistent request in open/active and AWS
    launches a replacement.

    After terminate, any EBS volume whose BlockDeviceMapping had
    DeleteOnTermination=false is deleted explicitly when CRC still saw those
    BDMs (instance running or shutting-down). Those volumes are tagged
    ``crc_leftover_of=<spot-request-id>`` (plus filter/exception/notags keys)
    before terminate so a later run can still find *available* orphans via
    ``describe_volumes`` after AWS drops the spot request (~4h). Volumes from
    instances already gone before CRC tagged them are not rediscovered. The
    region sweep re-applies filter/exception/notags/age (live request when
    present); in-use volumes are not swept.

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
    # Filter/exception/notags copies use this prefix so they never overwrite the
    # volume's own tags and can always be found/cleared on invalidate.
    LEFTOVER_SCOPE_PREFIX = "crc_leftover_tag_"
    # How long to wait for a volume to leave 'in-use' after terminate.
    VOLUME_AVAILABLE_WAIT_SEC = 180
    VOLUME_POLL_INTERVAL_SEC = 5
    # Same defensive skips as crc/aws/disk.py even if Jenkins omits
    # --exception_tags for these yb_task values.
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

                try:
                    retention_age = self.validated_retention_age(
                        tags, self.custom_age_tag_key
                    )
                except ValueError:
                    # Present but invalid custom age tag — fail closed (keep).
                    continue
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

    def _tags_have_sensitive_yb_task(self, tags: List[Dict[str, str]]) -> bool:
        tag_map = {t["Key"]: t.get("Value", "") for t in tags if t.get("Key")}
        for key in ("yb_task", "yb-task"):
            if key in tag_map and self._is_sensitive_yb_task(tag_map[key]):
                return True
        return False

    def _volume_tags_protect_from_delete(self, tags: List[Dict[str, str]]) -> bool:
        """True when volume-owned tags mean we must not delete (exception/notags/yb_task)."""
        if self._should_skip_spot_request(tags):
            return True
        return self._tags_have_sensitive_yb_task(tags)

    def _describe_volume_tag_list(
        self, client, volume_id: str
    ) -> Optional[List[Dict[str, str]]]:
        """Return the volume's Tags list, or None if describe fails."""
        try:
            desc = client.describe_volumes(VolumeIds=[volume_id])
            volumes = desc.get("Volumes") or []
            if not volumes:
                return []
            return [
                t
                for t in (volumes[0].get("Tags") or [])
                if t.get("Key")
            ]
        except (ClientError, BotoCoreError) as e:
            logging.error(
                "describe_volumes failed for %s while checking volume tags: %s",
                volume_id,
                e,
            )
            return None

    def _volume_ok_to_delete(self, client, volume_id: str) -> bool:
        """Fail closed: skip delete when tags cannot be read or protect the volume."""
        tags = self._describe_volume_tag_list(client, volume_id)
        if tags is None:
            logging.error(
                "Skipping leftover volume %s: could not read tags (fail closed)",
                volume_id,
            )
            return False
        # Ignore CRC leftover/scope keys — protection is the volume's own tags.
        own = [
            t
            for t in tags
            if t["Key"] != self.LEFTOVER_FROM_TAG
            and not t["Key"].startswith(self.LEFTOVER_SCOPE_PREFIX)
        ]
        if self._volume_tags_protect_from_delete(own):
            logging.info(
                "Skipping leftover volume %s: protected by volume tags "
                "(exception/notags/sensitive yb_task)",
                volume_id,
            )
            return False
        return True

    def _filter_deletable_leftover_volumes(
        self, client, volume_ids: List[str]
    ) -> List[str]:
        return [vid for vid in volume_ids if self._volume_ok_to_delete(client, vid)]

    def _passes_age_gate(
        self, tags: List[Dict[str, str]], create_time
    ) -> bool:
        """True if old enough to delete. Missing create time / bad custom age → keep."""
        if create_time is None:
            return False
        try:
            retention_age = self.validated_retention_age(
                tags, self.custom_age_tag_key
            )
        except ValueError:
            return False
        try:
            now = datetime.datetime.now().astimezone(create_time.tzinfo)
        except Exception:
            if getattr(create_time, "tzinfo", None) is None:
                create_time = create_time.replace(tzinfo=datetime.timezone.utc)
            now = datetime.datetime.now(datetime.timezone.utc)
        return self.is_old(retention_age or self.age, now, create_time)

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
        except BotoCoreError as e:
            logging.error(
                "describe_instances transport error for %s while checking "
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
        except (ClientError, BotoCoreError) as e:
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
    ) -> List[Dict[str, str]]:
        """
        Tags written onto each leftover volume: crc_leftover_of plus
        filter/exception/notags values under LEFTOVER_SCOPE_PREFIX (never
        overwrites the volume's own unprefixed tag keys). Prefixed scope keys
        are always written/overwritten so a prior failed invalidate cannot
        leave stale filter values sticky across retries.
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
        for key in sorted(keys_needed):
            if key not in tag_map:
                continue
            tags.append(
                {
                    "Key": self.LEFTOVER_SCOPE_PREFIX + key,
                    "Value": tag_map[key],
                }
            )
        return tags

    def _prefixed_scope_keys_on_volume(self, client, volume_id: str) -> List[str]:
        """Return crc_leftover_tag_* keys currently on the volume."""
        keys = self._volume_tag_keys(client, volume_id)
        if not keys:
            return []
        return sorted(k for k in keys if k.startswith(self.LEFTOVER_SCOPE_PREFIX))

    def _effective_tags_for_sweep(self, volume: dict) -> List[Dict[str, str]]:
        """
        Tags used for filter/exception when the spot request is gone:
        unprefixed copies of crc_leftover_tag_* plus the volume's own tags.
        """
        out: List[Dict[str, str]] = []
        for t in volume.get("Tags") or []:
            key = t.get("Key") or ""
            if not key or key == self.LEFTOVER_FROM_TAG:
                continue
            if key.startswith(self.LEFTOVER_SCOPE_PREFIX):
                out.append(
                    {
                        "Key": key[len(self.LEFTOVER_SCOPE_PREFIX) :],
                        "Value": t.get("Value", ""),
                    }
                )
            else:
                out.append(t)
        return out

    def _invalidate_leftover_marker(
        self,
        client,
        volume_ids: List[str],
        added_scope_keys: Optional[Dict[str, List[str]]] = None,
    ) -> None:
        """
        Stop a volume from being swept after a failed cancel/terminate/tag.

        Removes ``crc_leftover_of`` and every ``crc_leftover_tag_*`` key
        (from this run's map and/or still present on the volume). Prefer
        DeleteTags; if that fails, retry scope-only clear, then overwrite
        the leftover marker with LEFTOVER_INVALID_VALUE.
        """
        scope_map = added_scope_keys or {}
        for volume_id in volume_ids:
            scope_keys = list(scope_map.get(volume_id) or [])
            for key in self._prefixed_scope_keys_on_volume(client, volume_id):
                if key not in scope_keys:
                    scope_keys.append(key)
            tags_to_clear = [{"Key": self.LEFTOVER_FROM_TAG}] + [
                {"Key": k} for k in scope_keys
            ]
            try:
                client.delete_tags(Resources=[volume_id], Tags=tags_to_clear)
                continue
            except (ClientError, BotoCoreError) as e:
                logging.error(
                    "Failed to clear leftover/scope tags from volume %s: %s — "
                    "trying scope-only clear + sentinel",
                    volume_id,
                    e,
                )
            if scope_keys:
                try:
                    client.delete_tags(
                        Resources=[volume_id],
                        Tags=[{"Key": k} for k in scope_keys],
                    )
                except (ClientError, BotoCoreError) as e:
                    logging.error(
                        "Failed to clear scope tags from volume %s: %s — "
                        "trying per-key delete, then overwrite",
                        volume_id,
                        e,
                    )
                    for key in scope_keys:
                        try:
                            client.delete_tags(
                                Resources=[volume_id], Tags=[{"Key": key}]
                            )
                        except (ClientError, BotoCoreError) as per_e:
                            logging.error(
                                "Failed to clear scope tag %s from volume %s: %s",
                                key,
                                volume_id,
                                per_e,
                            )
                    # Overwrite sticky values so a later --resource disk run
                    # with the same filter_tags cannot match them.
                    try:
                        client.create_tags(
                            Resources=[volume_id],
                            Tags=[
                                {
                                    "Key": k,
                                    "Value": self.LEFTOVER_INVALID_VALUE,
                                }
                                for k in scope_keys
                            ],
                        )
                    except (ClientError, BotoCoreError) as overwrite_e:
                        logging.error(
                            "Failed to overwrite sticky scope tags on "
                            "volume %s: %s",
                            volume_id,
                            overwrite_e,
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
    ) -> Optional[Dict[str, List[str]]]:
        """
        Mark each DoT=false volume so a later run can find it even after the
        spot request object is gone.

        Returns a map of volume_id → scope keys this run added (excluding
        ``crc_leftover_of``), or None if tagging failed (partial markers are
        invalidated first).
        """
        if not volume_ids:
            return {}
        tagged_scope: Dict[str, List[str]] = {}
        for volume_id in volume_ids:
            # describe_volumes fail-closed: do not cancel if we cannot tag safely.
            if self._volume_tag_keys(client, volume_id) is None:
                self._invalidate_leftover_marker(
                    client, list(tagged_scope), tagged_scope
                )
                return None
            tags = self._scope_tags_for_volume(request_id, request_tags)
            scope_keys = [
                t["Key"] for t in tags if t["Key"] != self.LEFTOVER_FROM_TAG
            ]
            try:
                client.create_tags(Resources=[volume_id], Tags=tags)
                tagged_scope[volume_id] = scope_keys
            except (ClientError, BotoCoreError) as e:
                logging.error(
                    "Failed to tag leftover volume %s for spot request %s: %s",
                    volume_id,
                    request_id,
                    e,
                )
                # BotoCoreError may mean AWS applied the tags anyway — include
                # this volume in invalidation, not only prior successes.
                tagged_scope[volume_id] = scope_keys
                self._invalidate_leftover_marker(
                    client, list(tagged_scope), tagged_scope
                )
                return None
        logging.info(
            "Tagged leftover volumes %s with %s=%s (plus missing scope tags)",
            volume_ids,
            self.LEFTOVER_FROM_TAG,
            request_id,
        )
        return tagged_scope

    def _volumes_tagged_leftover(
        self, client, request_id: Optional[str] = None
    ) -> List[str]:
        """Find volumes marked crc_leftover_of in available or in-use state."""
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
        except (ClientError, BotoCoreError) as e:
            logging.error(
                "describe_volumes for %s leftovers failed: %s",
                self.LEFTOVER_FROM_TAG,
                e,
            )
        return volumes

    def _live_spot_request_tags(
        self, client, request_id: str
    ) -> Tuple[str, Optional[List[Dict[str, str]]], Optional[datetime.datetime]]:
        """
        Look up tags/CreateTime for a spot request during sweep.

        Returns ``(\"live\", tags, create_time)``, ``(\"gone\", None, None)``
        when AWS no longer lists the request, or ``(\"error\", None, None)``
        on other API failures (caller must fail closed and skip the volume).
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
                return "gone", None, None
            logging.error(
                "describe_spot_instance_requests for %s failed during sweep: %s",
                request_id,
                e,
            )
            return "error", None, None
        except BotoCoreError as e:
            logging.error(
                "describe_spot_instance_requests transport error for %s "
                "during sweep: %s",
                request_id,
                e,
            )
            return "error", None, None
        reqs = resp.get("SpotInstanceRequests") or []
        if not reqs:
            return "gone", None, None
        req = reqs[0]
        return "live", req.get("Tags") or [], req.get("CreateTime")

    def _volume_own_tags_for_protect(self, volume: dict) -> List[Dict[str, str]]:
        """Volume-owned tags only (exclude crc_leftover_of / crc_leftover_tag_*)."""
        out: List[Dict[str, str]] = []
        for t in volume.get("Tags") or []:
            key = t.get("Key") or ""
            if (
                not key
                or key == self.LEFTOVER_FROM_TAG
                or key.startswith(self.LEFTOVER_SCOPE_PREFIX)
            ):
                continue
            out.append(t)
        return out

    def _volume_passes_sweep_gates(self, client, volume: dict) -> bool:
        """
        Apply the same filter/exception/notags/untagged/age gates as spot
        requests, plus the volume's own exception/notags/sensitive yb_task.

        Prefer live request tags when the request still exists (so a newly
        added exception or custom age tag still protects the volume);
        otherwise use the scope tags copied onto the volume at tag time and
        the volume CreateTime for age.
        """
        tag_map = {
            t["Key"]: t.get("Value", "")
            for t in (volume.get("Tags") or [])
            if t.get("Key")
        }
        request_id = tag_map.get(self.LEFTOVER_FROM_TAG)
        if not request_id or request_id == self.LEFTOVER_INVALID_VALUE:
            return False

        # Volume-owned tags always apply (DoT=false volumes may be protected
        # even when the spot request itself is not).
        if self._volume_tags_protect_from_delete(
            self._volume_own_tags_for_protect(volume)
        ):
            return False

        status, live_tags, create_time = self._live_spot_request_tags(
            client, request_id
        )
        if status == "error":
            return False
        if status == "live":
            tags = live_tags or []
            # Untagged live requests: fail closed (same as _get_filtered_requests).
            if not tags:
                return False
            if self._should_skip_spot_request(tags):
                return False
            if self.filter_tags and not self._matches_filter_tags(tags):
                return False
            if not self._passes_age_gate(tags, create_time):
                return False
            return True
        # Request gone (~4h): gate on crc_leftover_tag_* copies (+ volume tags).
        vol_tags = self._effective_tags_for_sweep(volume)
        if self._should_skip_spot_request(vol_tags):
            return False
        if self.filter_tags and not self._matches_filter_tags(vol_tags):
            return False
        if not self._passes_age_gate(vol_tags, volume.get("CreateTime")):
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
            except BotoCoreError as e:
                logging.error(
                    "Transport error waiting/deleting volume %s: %s", volume_id, e
                )
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
            deletable = self._filter_deletable_leftover_volumes(
                client, leftover_volumes
            )
            self.spot_requests_to_delete.append(request_id)
            if instance_id and not instance_done:
                self.instance_ids_to_delete.append(instance_id)
            self.volumes_to_delete.extend(deletable)
            logging.info(
                "Dry run: would %s spot request %s (type=%s), terminate %s, "
                "delete leftover volumes %s",
                "skip-cancel (already cancelled)" if already_cancelled else "cancel",
                request_id,
                request_type,
                instance_id or "none",
                deletable,
            )
            return

        # Tag BDM volumes *before* cancel so a CreateTags failure leaves the
        # request untouched (and is not reported as deleted). Invalidate the
        # marker (+ scope keys we added) if cancel/terminate then fails.
        tagged_scope: Dict[str, List[str]] = {}
        if instance_id and leftover_volumes and volumes_from_bdm:
            tagged_scope = self._tag_volumes_as_leftover(
                client, request_id, leftover_volumes, request_tags=request_tags
            )
            if tagged_scope is None:
                logging.error(
                    "Not cancelling/terminating spot request %s — "
                    "failed to tag leftover volumes on %s",
                    request_id,
                    instance_id,
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
            except (ClientError, BotoCoreError) as e:
                logging.error(
                    "Failed to cancel spot request %s in %s: %s — not terminating "
                    "its instance (persistent requests would relaunch)",
                    request_id,
                    region,
                    e,
                )
                # Invalidate even on transport errors: a timed-out cancel may
                # still have succeeded; the next run re-tags if needed.
                if tagged_scope:
                    self._invalidate_leftover_marker(
                        client, leftover_volumes, tagged_scope
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
                # Transport error: terminate may have been accepted. Keep the
                # marker only when the instance is confirmed gone/shutting-down;
                # otherwise invalidate (unknown/running → fail closed vs sweep).
                logging.error(
                    "Failed to terminate instance %s after cancelling %s: %s",
                    instance_id,
                    request_id,
                    e,
                )
                state, _ = self._instance_state_and_dot_false_volumes(
                    client, instance_id
                )
                if state in {None, "shutting-down", "terminated"}:
                    logging.info(
                        "Instance %s state=%s after ambiguous terminate error; "
                        "keeping leftover markers",
                        instance_id,
                        state or "not-found",
                    )
                    terminate_ok = True
                    if (
                        state == "shutting-down"
                        and instance_id not in self.instance_ids_to_delete
                    ):
                        self.instance_ids_to_delete.append(instance_id)
                else:
                    logging.error(
                        "Instance %s state=%s after ambiguous terminate error; "
                        "invalidating leftover markers",
                        instance_id,
                        state,
                    )
            if not terminate_ok:
                if tagged_scope:
                    self._invalidate_leftover_marker(
                        client, leftover_volumes, tagged_scope
                    )
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
        for volume_id in self._filter_deletable_leftover_volumes(
            client, leftover_volumes
        ):
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
        only when filter/exception/notags/untagged/age still allow it (and
        the volume's own tags do not protect it). Skips in-use volumes
        (still attached — not leftovers yet). Covers AWS dropping the spot
        request after ~4h. Errors on one volume do not abort the rest.
        """
        for volume in self._describe_tagged_leftover_volumes(
            client, statuses=["available"]
        ):
            volume_id = volume.get("VolumeId")
            if not volume_id:
                continue
            try:
                if not self._volume_passes_sweep_gates(client, volume):
                    logging.info(
                        "Skipping tagged leftover volume %s in %s "
                        "(filters/exceptions)",
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
            except Exception as e:
                logging.error(
                    "Region %s: error sweeping tagged leftover volume %s: %s",
                    region,
                    volume_id,
                    e,
                )

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
