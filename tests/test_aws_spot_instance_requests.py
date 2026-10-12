# Copyright (c) Yugabyte, Inc.

import datetime
import unittest
from unittest import mock

from botocore.exceptions import ClientError

from crc.aws.spot_instance_requests import SpotInstanceRequests


def _spot(
    dry_run=True,
    filter_tags={"yb_owner": ["qa"]},
    exception_tags=None,
    age={"days": 1},
    custom_age_tag_key=None,
    notags=None,
):
    return SpotInstanceRequests(
        dry_run=dry_run,
        filter_tags=filter_tags,
        exception_tags={} if exception_tags is None else exception_tags,
        age=age,
        custom_age_tag_key=custom_age_tag_key,
        notags={} if notags is None else notags,
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
        "Tags": (
            [
                {"Key": "yb_owner", "Value": "qa"},
                {"Key": "yb_yba_url", "Value": "https://example"},
                {"Key": "universe-uuid", "Value": "u-1"},
            ]
            if tags is None
            else tags
        ),
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
        self.assertEqual(matches[0][3], False)

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

    def test_skips_cancelled_without_instance(self):
        s = _spot()
        matches = s._get_filtered_requests(
            {
                "SpotInstanceRequests": [
                    _request(state="cancelled", instance_id=None)
                ]
            }
        )
        self.assertEqual(matches, [])

    def test_retries_cancelled_with_instance(self):
        s = _spot()
        matches = s._get_filtered_requests(
            {"SpotInstanceRequests": [_request(state="cancelled", instance_id="i-1")]}
        )
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0][3], True)

    def test_skips_untagged_even_without_filters(self):
        s = _spot(filter_tags={})
        matches = s._get_filtered_requests(
            {"SpotInstanceRequests": [_request(tags=[])]}
        )
        self.assertEqual(matches, [])

    def test_requires_positive_age(self):
        with self.assertRaises(ValueError):
            _spot(age={})
        with self.assertRaises(ValueError):
            _spot(age={"days": 0})

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
                            "State": {"Name": "running"},
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

        def track_create_tags(**kwargs):
            call_order.append(("create_tags", kwargs["Resources"], kwargs["Tags"]))

        client.cancel_spot_instance_requests.side_effect = track_cancel
        client.terminate_instances.side_effect = track_terminate
        client.delete_volume.side_effect = track_delete_volume
        client.create_tags.side_effect = track_create_tags

        with mock.patch.object(s, "VOLUME_POLL_INTERVAL_SEC", 0):
            s._cleanup_one(client, "us-west-2", "sir-1", "i-1", "persistent")

        self.assertEqual(
            call_order,
            [
                ("cancel", ["sir-1"]),
                (
                    "create_tags",
                    ["vol-keep"],
                    [{"Key": s.LEFTOVER_FROM_TAG, "Value": "sir-1"}],
                ),
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
        client.describe_instances.return_value = {
            "Reservations": [{"Instances": [{"State": {"Name": "running"}, "BlockDeviceMappings": []}]}]
        }
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
                            "State": {"Name": "running"},
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


    def test_already_cancelled_skips_cancel_then_terminates(self):
        s = _spot(dry_run=False)
        client = mock.MagicMock()
        client.describe_instances.return_value = {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "State": {"Name": "running"},
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
        client.describe_volumes.return_value = {
            "Volumes": [{"State": "available", "VolumeId": "vol-keep"}]
        }
        with mock.patch.object(s, "VOLUME_POLL_INTERVAL_SEC", 0):
            s._cleanup_one(
                client,
                "us-west-2",
                "sir-1",
                "i-1",
                "one-time",
                already_cancelled=True,
            )
        client.cancel_spot_instance_requests.assert_not_called()
        client.terminate_instances.assert_called_once()
        client.delete_volume.assert_called_once_with(VolumeId="vol-keep")


    def test_skips_cancelled_when_instance_already_done(self):
        s = _spot(dry_run=False)
        client = mock.MagicMock()
        client.describe_instances.return_value = {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "State": {"Name": "terminated"},
                            "BlockDeviceMappings": [],
                        }
                    ]
                }
            ]
        }
        s._cleanup_one(
            client,
            "us-west-2",
            "sir-1",
            "i-1",
            "one-time",
            already_cancelled=True,
        )
        client.terminate_instances.assert_not_called()
        client.delete_volume.assert_not_called()
        self.assertEqual(s.spot_requests_to_delete, [])
        self.assertEqual(s.instance_ids_to_delete, [])

    def test_retry_uses_tagged_leftover_volumes(self):
        s = _spot(dry_run=False)
        client = mock.MagicMock()
        # Instance gone — BDM empty; rediscover via crc_leftover_of on the volume
        client.describe_instances.return_value = {"Reservations": []}
        page = {
            "Volumes": [
                {
                    "VolumeId": "vol-keep",
                    "State": "available",
                    "Tags": [{"Key": s.LEFTOVER_FROM_TAG, "Value": "sir-1"}],
                }
            ]
        }
        paginator = mock.MagicMock()
        paginator.paginate.return_value = [page]
        client.get_paginator.return_value = paginator
        client.describe_volumes.return_value = {
            "Volumes": [{"State": "available", "VolumeId": "vol-keep"}]
        }
        with mock.patch.object(s, "VOLUME_POLL_INTERVAL_SEC", 0):
            s._cleanup_one(
                client,
                "us-west-2",
                "sir-1",
                "i-1",
                "one-time",
                already_cancelled=True,
            )
        client.terminate_instances.assert_not_called()
        client.delete_volume.assert_called_once_with(VolumeId="vol-keep")
        self.assertEqual(s.volumes_to_delete, ["vol-keep"])
        self.assertEqual(s.spot_requests_to_delete, ["sir-1"])

    def test_retry_already_gone_volume_not_recounted(self):
        s = _spot(dry_run=False)
        client = mock.MagicMock()
        client.describe_instances.return_value = {"Reservations": []}
        # No volumes still carry crc_leftover_of — prior run finished cleanup
        paginator = mock.MagicMock()
        paginator.paginate.return_value = [{"Volumes": []}]
        client.get_paginator.return_value = paginator
        with mock.patch.object(s, "VOLUME_POLL_INTERVAL_SEC", 0):
            s._cleanup_one(
                client,
                "us-west-2",
                "sir-1",
                "i-1",
                "one-time",
                already_cancelled=True,
            )
        client.delete_volume.assert_not_called()
        self.assertEqual(s.volumes_to_delete, [])
        self.assertEqual(s.spot_requests_to_delete, [])

    def test_failed_volume_tag_does_not_terminate(self):
        s = _spot(dry_run=False)
        client = mock.MagicMock()
        client.describe_instances.return_value = {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "State": {"Name": "running"},
                            "BlockDeviceMappings": [
                                {
                                    "Ebs": {
                                        "VolumeId": "vol-keep",
                                        "DeleteOnTermination": False,
                                    }
                                }
                            ],
                        }
                    ]
                }
            ]
        }
        client.create_tags.side_effect = ClientError(
            {"Error": {"Code": "UnauthorizedOperation", "Message": "nope"}},
            "CreateTags",
        )
        s._cleanup_one(client, "us-west-2", "sir-1", "i-1", "persistent")
        client.cancel_spot_instance_requests.assert_called_once()
        client.terminate_instances.assert_not_called()
        client.delete_volume.assert_not_called()

    def test_sweep_tagged_leftover_volumes(self):
        s = _spot(dry_run=False)
        client = mock.MagicMock()
        page = {
            "Volumes": [
                {"VolumeId": "vol-orphan", "State": "available"},
            ]
        }
        paginator = mock.MagicMock()
        paginator.paginate.return_value = [page]
        client.get_paginator.return_value = paginator
        client.describe_volumes.return_value = {
            "Volumes": [{"State": "available", "VolumeId": "vol-orphan"}]
        }
        with mock.patch.object(s, "VOLUME_POLL_INTERVAL_SEC", 0):
            s._sweep_tagged_leftover_volumes(client, "us-west-2")
        client.delete_volume.assert_called_once_with(VolumeId="vol-orphan")
        self.assertEqual(s.volumes_to_delete, ["vol-orphan"])

    def test_abort_when_describe_instances_fails(self):
        s = _spot(dry_run=False)
        client = mock.MagicMock()
        client.describe_instances.side_effect = ClientError(
            {"Error": {"Code": "UnauthorizedOperation", "Message": "nope"}},
            "DescribeInstances",
        )
        s._cleanup_one(client, "us-west-2", "sir-1", "i-1", "persistent")
        client.cancel_spot_instance_requests.assert_not_called()
        client.terminate_instances.assert_not_called()

    def test_filter_tags_glob_match(self):
        s = _spot(filter_tags={"yb_task": ["itest*"]})
        matches = s._get_filtered_requests(
            {
                "SpotInstanceRequests": [
                    _request(
                        tags=[
                            {"Key": "yb_owner", "Value": "qa"},
                            {"Key": "yb_task", "Value": "itest-123"},
                        ]
                    )
                ]
            }
        )
        self.assertEqual(len(matches), 1)


if __name__ == "__main__":
    unittest.main()
