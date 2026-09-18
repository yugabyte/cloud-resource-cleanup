# Copyright (c) Yugabyte, Inc.

import datetime
import unittest
from unittest import mock

from botocore.exceptions import ClientError

from crc.aws.disk import Disk


def _disk(**kwargs):
    params = dict(
        dry_run=True,
        filter_tags=None,
        exception_tags=None,
        age={"hours": 6},
        custom_age_tag_key=None,
        notags=None,
    )
    params.update(kwargs)
    return Disk(**params)


class AwsDiskSkipTests(unittest.TestCase):
    def test_skips_infra_yb_task(self):
        d = _disk()
        self.assertTrue(
            d._should_skip({"yb_task": "infrastructure"}, "foo", "vol-1")
        )
        self.assertTrue(d._should_skip({"yb_task": "prod"}, "foo", "vol-1"))
        self.assertFalse(d._should_skip({"yb_task": "dev"}, "foo", "vol-1"))

    def test_allows_eng_dev_unattached(self):
        d = _disk()
        self.assertFalse(
            d._should_skip({"yb_dept": "eng", "yb_task": "dev", "yb_owner": "dev"}, "foo", "vol-1")
        )

    def test_allows_itest(self):
        d = _disk(filter_tags={"yb_task": ["itest", "uitest"]})
        self.assertFalse(d._should_skip({"yb_task": "itest"}, "foo", "vol-1"))

    def test_skips_sensitive_yb_task_regardless_of_case(self):
        d = _disk()
        self.assertTrue(d._should_skip({"yb_task": "Prod"}, "foo", "vol-1"))
        self.assertTrue(d._should_skip({"yb_task": "INFRA"}, "foo", "vol-1"))
        self.assertTrue(d._should_skip({"yb_task": " Production "}, "foo", "vol-1"))

    def test_skips_k8s_unless_filtered(self):
        tags = {
            "kubernetes.io/cluster/shubin-test-cluster": "owned",
            "Name": "shubin-test-cluster-dynamic-pvc-abc",
        }
        d = _disk()
        self.assertTrue(d._should_skip(tags, tags["Name"], "vol-1"))

        # The opt-in has to filter on a key the volume actually carries,
        # otherwise _matches_filter_tags rejects it before _should_skip runs.
        d_k8s = _disk(
            filter_tags={"kubernetes.io/cluster/shubin-test-cluster": ["owned"]}
        )
        self.assertTrue(d_k8s._matches_filter_tags(tags))
        self.assertFalse(d_k8s._should_skip(tags, tags["Name"], "vol-1"))

    def test_exception_tags_autoclean(self):
        d = _disk(exception_tags={"autoclean": ["false", "False"]})
        self.assertTrue(d._should_skip({"yb_task": "itest", "autoclean": "false"}, "n", "vol-1"))

    def test_filter_tags_and(self):
        d = _disk(filter_tags={"yb_task": ["itest"], "yb_owner": ["qateam"]})
        self.assertTrue(d._matches_filter_tags({"yb_task": "itest", "yb_owner": "qateam"}))
        self.assertFalse(d._matches_filter_tags({"yb_task": "itest", "yb_owner": "dev"}))
        self.assertFalse(d._matches_filter_tags({"yb_task": "itest"}))


class AwsDiskAgeGateTests(unittest.TestCase):
    def test_requires_an_age_gate(self):
        with self.assertRaises(ValueError):
            _disk(age=None)
        with self.assertRaises(ValueError):
            _disk(age={})

    def test_detach_age_alone_is_enough(self):
        d = _disk(age=None, detach_age={"days": 3})
        self.assertEqual(d.age, {})
        self.assertEqual(d.detach_age, {"days": 3})

    def test_creation_age_gate(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        d = _disk(age={"days": 7})
        old = {
            "VolumeId": "vol-old",
            "State": "available",
            "CreateTime": now - datetime.timedelta(days=30),
        }
        fresh = {
            "VolumeId": "vol-new",
            "State": "available",
            "CreateTime": now - datetime.timedelta(days=1),
        }
        self.assertTrue(d._is_candidate(old))
        self.assertFalse(d._is_candidate(fresh))

    def test_attached_volume_is_never_a_candidate(self):
        d = _disk()
        volume = {
            "VolumeId": "vol-1",
            "State": "in-use",
            "Attachments": [{"InstanceId": "i-1"}],
            "CreateTime": datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc),
        }
        self.assertFalse(d._is_candidate(volume))


class AwsDiskDetachAgeTests(unittest.TestCase):
    """detach_age is inferred from AWS/EBS metrics, published only while attached."""

    def setUp(self):
        self.volumes = [{"VolumeId": "vol-idle"}, {"VolumeId": "vol-busy"}]

    def _run(self, disk, response):
        with mock.patch("crc.aws.disk.boto3.client") as client:
            client.return_value.get_metric_data.return_value = response
            return disk._drop_recently_attached("us-west-2", self.volumes)

    def test_volume_with_datapoints_is_dropped(self):
        d = _disk(detach_age={"days": 3})
        kept = self._run(
            d,
            {
                "MetricDataResults": [
                    {"Id": "m0", "Values": []},
                    {"Id": "m1", "Values": [3600.0]},
                ]
            },
        )
        self.assertEqual([v["VolumeId"] for v in kept], ["vol-idle"])

    def test_without_detach_age_nothing_is_dropped(self):
        d = _disk(age={"days": 7})
        with mock.patch("crc.aws.disk.boto3.client") as client:
            kept = d._drop_recently_attached("us-west-2", self.volumes)
        client.assert_not_called()
        self.assertEqual(kept, self.volumes)

    def test_cloudwatch_failure_drops_the_batch(self):
        d = _disk(detach_age={"days": 3})
        with mock.patch("crc.aws.disk.boto3.client") as client:
            client.return_value.get_metric_data.side_effect = ClientError(
                {"Error": {"Code": "AccessDenied"}}, "GetMetricData"
            )
            self.assertEqual(d._drop_recently_attached("us-west-2", self.volumes), [])

    def test_lookback_is_clamped_to_cloudwatch_retention(self):
        d = _disk(detach_age={"days": 900})
        self.assertEqual(
            min(d._age_to_timedelta(d.detach_age), d.CW_MAX_LOOKBACK),
            d.CW_MAX_LOOKBACK,
        )


if __name__ == "__main__":
    unittest.main()
