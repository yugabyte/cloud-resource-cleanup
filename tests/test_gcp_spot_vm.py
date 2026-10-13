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

    def test_requires_positive_age(self):
        with self.assertRaises(ValueError):
            _gcp_spot(age={})

    def test_invalid_custom_age_falls_back_to_cli_age(self):
        s = _gcp_spot(age={"days": 7}, custom_age_tag_key="retention")
        inst = _instance(labels={"retention": "{'days': 0}"})
        # zero custom age ignored; 7-day floor keeps young instance
        inst.creation_timestamp = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).strftime(s.time_format)
        self.assertFalse(s._is_old_instance(inst))


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
        refs = s._non_autodelete_disks(inst)
        self.assertEqual([r["name"] for r in refs], ["keep-me"])
        self.assertEqual(refs[0]["scope"], "zone")

    def test_disk_name_from_source(self):
        s = _gcp_spot()
        self.assertEqual(
            s._disk_name_from_source("projects/p/zones/us-central1-a/disks/d1"),
            "d1",
        )
        self.assertIsNone(s._disk_name_from_source(None))

    def test_disk_ref_parses_regional(self):
        s = _gcp_spot()
        ref = s._disk_ref_from_source("projects/p/regions/us-central1/disks/rd1")
        self.assertEqual(
            ref,
            {
                "project": "p",
                "scope": "region",
                "location": "us-central1",
                "name": "rd1",
            },
        )

    def test_dry_run_delete_disk_records_without_api(self):
        s = _gcp_spot(dry_run=True)
        service = mock.MagicMock()
        s._delete_disk(
            service,
            {
                "project": "proj",
                "scope": "zone",
                "location": "us-central1-a",
                "name": "keep-me",
            },
            "us-central1-a",
        )
        service.disks.assert_not_called()
        self.assertEqual(s.disks_to_delete, ["proj/us-central1-a/keep-me"])


class GcpDeleteOrderTests(unittest.TestCase):
    def test_delete_waits_for_zone_op_before_disk(self):
        s = _gcp_spot(dry_run=False)
        s.INSTANCE_POLL_INTERVAL_SEC = 0
        call_order = []

        zone_ops = mock.MagicMock()
        zone_ops.get.return_value.execute.return_value = {"status": "DONE"}

        disks = mock.MagicMock()
        disks.get.return_value.execute.return_value = {"labels": {}}

        def disk_delete(**kwargs):
            call_order.append("disk")
            return mock.Mock(execute=mock.Mock(return_value={}))

        disks.delete.side_effect = disk_delete

        instances_api = mock.MagicMock()
        def inst_delete(**kwargs):
            call_order.append("instance")
            return mock.Mock(
                execute=mock.Mock(return_value={"name": "op-1"})
            )
        instances_api.delete.side_effect = inst_delete

        service = mock.MagicMock()
        service.zones.return_value.list.return_value.execute.return_value = {
            "items": [{"name": "us-central1-a"}]
        }
        service.instances.return_value = instances_api
        service.disks.return_value = disks
        service.zoneOperations.return_value = zone_ops

        inst = _instance(
            scheduling=_scheduling(preemptible=True),
            disks=[_disk("projects/p/zones/z/disks/keep-me", auto_delete=False)],
        )

        with mock.patch("crc.gcp.spot_vm.discovery.build") as build, mock.patch(
            "crc.gcp.spot_vm.compute_v1.InstancesClient"
        ) as client_cls:
            build.return_value.__enter__.return_value = service
            build.return_value.__exit__.return_value = False
            client_cls.return_value.list.return_value = [inst]
            s.delete()

        self.assertEqual(call_order, ["instance", "disk"])
        zone_ops.get.assert_called()
        self.assertEqual(s.disks_to_delete, ["p/z/keep-me"])

    def test_disk_exception_labels_skip_delete(self):
        s = _gcp_spot(exception_labels={"keep": ["true"]})
        service = mock.MagicMock()
        service.disks.return_value.get.return_value.execute.return_value = {
            "labels": {"keep": "true"}
        }
        self.assertFalse(
            s._disk_ok_to_delete(
                service,
                {
                    "project": "p",
                    "scope": "zone",
                    "location": "z",
                    "name": "d1",
                },
                "z",
            )
        )

    def test_gke_node_skipped(self):
        s = _gcp_spot()
        inst = _instance(
            scheduling=_scheduling(preemptible=True),
            labels={"yb_task": "itest", "goog-gke-node": ""},
        )
        self.assertTrue(s._is_managed_or_gke_node(inst))


if __name__ == "__main__":
    unittest.main()
