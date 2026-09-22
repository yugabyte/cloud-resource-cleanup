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


def _volume(volume_id="vol-1", **kwargs):
    now = datetime.datetime.now(datetime.timezone.utc)
    volume = {
        "VolumeId": volume_id,
        "State": "available",
        "Attachments": [],
        "CreateTime": now - datetime.timedelta(days=30),
        "Tags": [{"Key": "yb_task", "Value": "itest"}, {"Key": "Name", "Value": "foo"}],
    }
    volume.update(kwargs)
    return volume


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
        self.assertTrue(d._should_skip({"YB_Task": "prod"}, "foo", "vol-1"))
        self.assertTrue(d._should_skip({"yb_task": "prod-cluster"}, "foo", "vol-1"))
        self.assertTrue(d._should_skip({"yb_task": "infrastructure-shared"}, "foo", "vol-1"))

    def test_skips_k8s_unless_filtered(self):
        tags = {
            "kubernetes.io/cluster/shubin-test-cluster": "owned",
            "Name": "shubin-test-cluster-dynamic-pvc-abc",
        }
        d = _disk()
        self.assertTrue(d._should_skip(tags, tags["Name"], "vol-1"))

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

    def test_filter_tags_honour_globs(self):
        d = _disk(filter_tags={"Name": ["jenkins-*"]})
        self.assertTrue(d._matches_filter_tags({"Name": "jenkins-abc"}))
        self.assertFalse(d._matches_filter_tags({"Name": "prod-jenkins"}))


class AwsDiskAgeGateTests(unittest.TestCase):
    def test_requires_an_age_gate(self):
        with self.assertRaises(ValueError):
            _disk(age=None)
        with self.assertRaises(ValueError):
            _disk(age={})

    def test_rejects_unknown_or_zero_age_units(self):
        with self.assertRaises(ValueError):
            _disk(age=None, detach_age={"minutes": 30})
        with self.assertRaises(ValueError):
            _disk(age=None, detach_age={"day": 3})
        with self.assertRaises(ValueError):
            _disk(age=None, detach_age={"days": 0})
        with self.assertRaises(ValueError):
            _disk(age={"hours": 0})

    def test_rejects_detach_age_past_cloudwatch_retention(self):
        with self.assertRaises(ValueError):
            _disk(age=None, detach_age={"days": 900})

    def test_detach_age_alone_is_enough(self):
        d = _disk(age=None, detach_age={"days": 3})
        self.assertEqual(d.age, {})
        self.assertEqual(d.detach_age, {"days": 3})

    def test_creation_age_gate(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        d = _disk(age={"days": 7})
        old = _volume(
            "vol-old",
            CreateTime=now - datetime.timedelta(days=30),
        )
        fresh = _volume(
            "vol-new",
            CreateTime=now - datetime.timedelta(days=1),
        )
        self.assertTrue(d._is_candidate(old))
        self.assertFalse(d._is_candidate(fresh))

    def test_detach_age_only_still_floors_on_create_time(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        d = _disk(age=None, detach_age={"days": 3})
        brand_new = _volume(
            "vol-new",
            CreateTime=now - datetime.timedelta(seconds=5),
        )
        self.assertFalse(d._is_candidate(brand_new))

    def test_attached_volume_is_never_a_candidate(self):
        d = _disk()
        in_use = _volume(
            State="in-use",
            Attachments=[{"InstanceId": "i-1"}],
        )
        available_but_attached = _volume(
            State="available",
            Attachments=[{"InstanceId": "i-1"}],
        )
        self.assertFalse(d._is_candidate(in_use))
        self.assertFalse(d._is_candidate(available_but_attached))

    def test_multi_attach_is_never_a_candidate(self):
        d = _disk()
        self.assertFalse(d._is_candidate(_volume(MultiAttachEnabled=True)))

    def test_untagged_skipped_unless_notags_set(self):
        d = _disk()
        untagged = _volume(Tags=None)
        untagged.pop("Tags")
        self.assertFalse(d._is_candidate(untagged))

        d_notags = _disk(notags={"yb_owner": []})
        self.assertTrue(d_notags._is_candidate(untagged))


class AwsDiskDetachAgeTests(unittest.TestCase):
    """detach_age is inferred from AWS/EBS metrics, published only while attached."""

    def setUp(self):
        self.volumes = [{"VolumeId": "vol-idle"}, {"VolumeId": "vol-busy"}]

    def _complete(self, query_id, values=None):
        return {"Id": query_id, "StatusCode": "Complete", "Values": values or []}

    def _ids_for(self, index):
        # m0idletime, m0readops, m0writeops — see Disk.EBS_METRICS
        suffixes = ["idletime", "readops", "writeops"]
        return ["m%s%s" % (index, suffix) for suffix in suffixes]

    def _run(self, disk, response):
        with mock.patch("crc.aws.disk.boto3.client") as client:
            client.return_value.get_metric_data.return_value = response
            return disk._drop_recently_attached("us-west-2", self.volumes)

    def test_volume_with_datapoints_is_dropped(self):
        d = _disk(detach_age={"days": 3})
        results = [self._complete(qid) for qid in self._ids_for(0)]
        results.extend(
            [
                self._complete(self._ids_for(1)[0], [3600.0]),
                self._complete(self._ids_for(1)[1]),
                self._complete(self._ids_for(1)[2]),
            ]
        )
        kept = self._run(d, {"MetricDataResults": results})
        self.assertEqual([v["VolumeId"] for v in kept], ["vol-idle"])

    def test_incomplete_status_keeps_the_volume(self):
        d = _disk(detach_age={"days": 3})
        results = [self._complete(qid) for qid in self._ids_for(0)]
        results.append(
            {"Id": self._ids_for(1)[0], "StatusCode": "Forbidden", "Values": []}
        )
        results.extend(self._complete(qid) for qid in self._ids_for(1)[1:])
        kept = self._run(d, {"MetricDataResults": results})
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

    def test_paginates_next_token(self):
        d = _disk(detach_age={"days": 3})
        page1 = {
            "MetricDataResults": [self._complete(qid) for qid in self._ids_for(0)],
            "NextToken": "t1",
        }
        page2 = {
            "MetricDataResults": [self._complete(qid) for qid in self._ids_for(1)],
        }
        with mock.patch("crc.aws.disk.boto3.client") as client:
            client.return_value.get_metric_data.side_effect = [page1, page2]
            kept = d._drop_recently_attached("us-west-2", self.volumes)
        self.assertEqual(client.return_value.get_metric_data.call_count, 2)
        self.assertEqual([v["VolumeId"] for v in kept], ["vol-idle", "vol-busy"])


class AwsDiskDeletePathTests(unittest.TestCase):
    def test_dry_run_never_calls_delete_volume(self):
        d = _disk(age={"days": 1})
        volume = _volume()
        ec2 = mock.MagicMock()
        paginator = mock.MagicMock()
        paginator.paginate.return_value = [{"Volumes": [volume]}]
        ec2.get_paginator.return_value = paginator

        def fake_client(service, **kwargs):
            self.assertEqual(service, "ec2")
            return ec2

        with mock.patch("crc.aws.disk.boto3.client", side_effect=fake_client), mock.patch(
            "crc.aws.disk.get_all_regions", return_value=["us-west-2"]
        ):
            d.delete()

        ec2.delete_volume.assert_not_called()
        self.assertEqual(len(d.get_deleted), 1)

    def test_live_delete_calls_delete_volume(self):
        d = _disk(dry_run=False, age={"days": 1})
        volume = _volume()
        ec2 = mock.MagicMock()
        paginator = mock.MagicMock()
        paginator.paginate.return_value = [{"Volumes": [volume]}]
        ec2.get_paginator.return_value = paginator

        with mock.patch("crc.aws.disk.boto3.client", return_value=ec2), mock.patch(
            "crc.aws.disk.get_all_regions", return_value=["us-west-2"]
        ):
            d.delete()

        ec2.delete_volume.assert_called_once_with(VolumeId="vol-1")

    def test_invalid_name_regex_raises_at_init(self):
        with self.assertRaises(ValueError):
            _disk(name_regex=["test-*("])

    def test_tag_without_value_does_not_raise(self):
        d = _disk()
        volume = _volume(Tags=[{"Key": "Name"}])
        self.assertTrue(d._is_candidate(volume))


if __name__ == "__main__":
    unittest.main()
