# Copyright (c) Yugabyte, Inc.

import datetime
import unittest
from unittest import mock

from botocore.exceptions import ClientError

from crc.aws.spot_instance_requests import SpotInstanceRequests


def _spot(
    dry_run=True,
    filter_tags=None,
    exception_tags=None,
    age=None,
    custom_age_tag_key=None,
    notags=None,
):
    return SpotInstanceRequests(
        dry_run=dry_run,
        filter_tags=filter_tags or {"yb_owner": ["qa"]},
        exception_tags=exception_tags or {},
        age=age or {"days": 1},
        custom_age_tag_key=custom_age_tag_key,
        notags=notags or {},
    )


def _request(
    request_id="sir-1",
    instance_id="i-1",
    request_type="persistent",
    days_old=7,
    tags=None,
    state="active",
):
    now = datetime.datetime.now(datetime.timezone.utc)
    return {
        "SpotInstanceRequestId": request_id,
        "InstanceId": instance_id,
        "Type": request_type,
        "State": state,
        "CreateTime": now - datetime.timedelta(days=days_old),
        "Tags": tags
        or [
            {"Key": "yb_owner", "Value": "qa"},
            {"Key": "yb_yba_url", "Value": "https://example"},
            {"Key": "universe-uuid", "Value": "u-1"},
        ],
    }


class SpotFilterTests(unittest.TestCase):
    def test_matches_tags_on_request_not_instance(self):
        s = _spot()
        matches = s._get_filtered_requests(
            {"SpotInstanceRequests": [_request(instance_id="i-untagged")]}
        )
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0][0], "sir-1")
        self.assertEqual(matches[0][1], "i-untagged")
        self.assertEqual(matches[0][2], "persistent")

    def test_includes_persistent_and_one_time(self):
        s = _spot()
        matches = s._get_filtered_requests(
            {
                "SpotInstanceRequests": [
                    _request("sir-p", "i-p", "persistent"),
                    _request("sir-o", "i-o", "one-time"),
                ]
            }
        )
        types = {m[2] for m in matches}
        self.assertEqual(types, {"persistent", "one-time"})

    def test_skips_cancelled(self):
        s = _spot()
        matches = s._get_filtered_requests(
            {"SpotInstanceRequests": [_request(state="cancelled")]}
        )
        self.assertEqual(matches, [])

    def test_skips_young_requests(self):
        s = _spot(age={"days": 10})
        matches = s._get_filtered_requests(
            {"SpotInstanceRequests": [_request(days_old=2)]}
        )
        self.assertEqual(matches, [])


class SpotCleanupOrderTests(unittest.TestCase):
    def test_cancel_before_terminate_and_delete_leftover_volume(self):
        s = _spot(dry_run=False)
        client = mock.MagicMock()
        client.describe_instances.return_value = {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "BlockDeviceMappings": [
                                {
                                    "Ebs": {
                                        "VolumeId": "vol-keep",
                                        "DeleteOnTermination": False,
                                    }
                                },
                                {
                                    "Ebs": {
                                        "VolumeId": "vol-dot",
                                        "DeleteOnTermination": True,
                                    }
                                },
                            ]
                        }
                    ]
                }
            ]
        }
        client.describe_volumes.return_value = {
            "Volumes": [{"State": "available", "VolumeId": "vol-keep"}]
        }

        call_order = []

        def track_cancel(**kwargs):
            call_order.append(("cancel", kwargs["SpotInstanceRequestIds"]))

        def track_terminate(**kwargs):
            call_order.append(("terminate", kwargs["InstanceIds"]))

        def track_delete_volume(**kwargs):
            call_order.append(("delete_volume", kwargs["VolumeId"]))

        client.cancel_spot_instance_requests.side_effect = track_cancel
        client.terminate_instances.side_effect = track_terminate
        client.delete_volume.side_effect = track_delete_volume

        with mock.patch.object(s, "VOLUME_POLL_INTERVAL_SEC", 0):
            s._cleanup_one(client, "us-west-2", "sir-1", "i-1", "persistent")

        self.assertEqual(
            call_order,
            [
                ("cancel", ["sir-1"]),
                ("terminate", ["i-1"]),
                ("delete_volume", "vol-keep"),
            ],
        )
        self.assertEqual(s.spot_requests_to_delete, ["sir-1"])
        self.assertEqual(s.instance_ids_to_delete, ["i-1"])
        self.assertEqual(s.volumes_to_delete, ["vol-keep"])

    def test_failed_cancel_does_not_terminate_persistent(self):
        s = _spot(dry_run=False)
        client = mock.MagicMock()
        client.describe_instances.return_value = {"Reservations": [{"Instances": [{}]}]}
        client.cancel_spot_instance_requests.side_effect = ClientError(
            {"Error": {"Code": "UnauthorizedOperation", "Message": "nope"}},
            "CancelSpotInstanceRequests",
        )

        s._cleanup_one(client, "us-west-2", "sir-1", "i-1", "persistent")

        client.terminate_instances.assert_not_called()
        self.assertEqual(s.spot_requests_to_delete, [])
        self.assertEqual(s.instance_ids_to_delete, [])

    def test_dry_run_lists_leftover_volumes_without_api_mutations(self):
        s = _spot(dry_run=True)
        client = mock.MagicMock()
        client.describe_instances.return_value = {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "BlockDeviceMappings": [
                                {
                                    "Ebs": {
                                        "VolumeId": "vol-keep",
                                        "DeleteOnTermination": False,
                                    }
                                }
                            ]
                        }
                    ]
                }
            ]
        }

        s._cleanup_one(client, "us-west-2", "sir-1", "i-1", "persistent")

        client.cancel_spot_instance_requests.assert_not_called()
        client.terminate_instances.assert_not_called()
        client.delete_volume.assert_not_called()
        self.assertEqual(s.volumes_to_delete, ["vol-keep"])


if __name__ == "__main__":
    unittest.main()
