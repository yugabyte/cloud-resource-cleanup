# Copyright (c) Yugabyte, Inc.

import unittest
from unittest import mock


def _gcp_spot(**kwargs):
    from crc.gcp.spot_vm import SpotVM

    params = dict(
        dry_run=True,
        project_id="proj",
        filter_labels={"yb_task": ["itest"]},
        exception_labels={},
        age={"days": 1},
        custom_age_tag_key=None,
        notags={},
    )
    params.update(kwargs)
    return SpotVM(**params)


def _scheduling(preemptible=False, provisioning_model=None):
    scheduling = mock.Mock()
    scheduling.preemptible = preemptible
    scheduling.provisioning_model = provisioning_model
    return scheduling


def _disk(source, auto_delete=True):
    disk = mock.Mock()
    disk.source = source
    disk.auto_delete = auto_delete
    return disk


def _instance(name="spot-1", scheduling=None, disks=None, labels=None):
    instance = mock.Mock()
    instance.name = name
    instance.scheduling = scheduling
    instance.disks = disks or []
    instance.labels = labels if labels is not None else {"yb_task": "itest"}
    instance.status = "RUNNING"
    instance.creation_timestamp = "2020-01-01T00:00:00.000000+00:00"
    return instance


class GcpSpotDetectTests(unittest.TestCase):
    def test_preemptible_true_is_spot(self):
        s = _gcp_spot()
        self.assertTrue(
            s._is_spot_instance(_instance(scheduling=_scheduling(preemptible=True)))
        )

    def test_provisioning_model_spot_is_spot(self):
        s = _gcp_spot()
        self.assertTrue(
            s._is_spot_instance(
                _instance(scheduling=_scheduling(provisioning_model="SPOT"))
            )
        )

    def test_standard_vm_is_not_spot(self):
        s = _gcp_spot()
        self.assertFalse(
            s._is_spot_instance(
                _instance(scheduling=_scheduling(preemptible=False, provisioning_model="STANDARD"))
            )
        )

    def test_missing_scheduling_is_not_spot(self):
        s = _gcp_spot()
        inst = _instance()
        inst.scheduling = None
        self.assertFalse(s._is_spot_instance(inst))

    def test_empty_filter_allows_unlabeled(self):
        s = _gcp_spot(filter_labels={})
        inst = _instance(labels={})
        self.assertTrue(s._has_matching_filter_label(inst))


class GcpNonAutodeleteDiskTests(unittest.TestCase):
    def test_collects_auto_delete_false_only(self):
        s = _gcp_spot()
        inst = _instance(
            disks=[
                _disk(
                    "projects/p/zones/z/disks/keep-me",
                    auto_delete=False,
                ),
                _disk(
                    "projects/p/zones/z/disks/gone-with-vm",
                    auto_delete=True,
                ),
            ]
        )
        self.assertEqual(s._non_autodelete_disks(inst), ["keep-me"])

    def test_disk_name_from_source(self):
        s = _gcp_spot()
        self.assertEqual(
            s._disk_name_from_source("projects/p/zones/us-central1-a/disks/d1"),
            "d1",
        )
        self.assertIsNone(s._disk_name_from_source(None))

    def test_dry_run_delete_disk_records_without_api(self):
        s = _gcp_spot(dry_run=True)
        service = mock.MagicMock()
        s._delete_disk(service, "us-central1-a", "keep-me")
        service.disks.assert_not_called()
        self.assertEqual(s.disks_to_delete, ["us-central1-a/keep-me"])


if __name__ == "__main__":
    unittest.main()
