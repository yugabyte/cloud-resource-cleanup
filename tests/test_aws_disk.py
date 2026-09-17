# Copyright (c) Yugabyte, Inc.

import unittest

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

    def test_skips_k8s_unless_filtered(self):
        tags = {
            "kubernetes.io/cluster/shubin-test-cluster": "owned",
            "Name": "shubin-test-cluster-dynamic-pvc-abc",
        }
        d = _disk()
        self.assertTrue(d._should_skip(tags, tags["Name"], "vol-1"))

        d_k8s = _disk(filter_tags={"KubernetesCluster": ["shubin-test-cluster"]})
        self.assertFalse(d_k8s._should_skip(tags, tags["Name"], "vol-1"))

    def test_exception_tags_autoclean(self):
        d = _disk(exception_tags={"autoclean": ["false", "False"]})
        self.assertTrue(d._should_skip({"yb_task": "itest", "autoclean": "false"}, "n", "vol-1"))

    def test_filter_tags_and(self):
        d = _disk(filter_tags={"yb_task": ["itest"], "yb_owner": ["qateam"]})
        self.assertTrue(d._matches_filter_tags({"yb_task": "itest", "yb_owner": "qateam"}))
        self.assertFalse(d._matches_filter_tags({"yb_task": "itest", "yb_owner": "dev"}))
        self.assertFalse(d._matches_filter_tags({"yb_task": "itest"}))


if __name__ == "__main__":
    unittest.main()
