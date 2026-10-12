# Copyright (c) Yugabyte, Inc.

import datetime
import sys
import types
import unittest
from unittest import mock

# oci may be absent in local/dev environments; stub enough for import + helpers.
if "oci" not in sys.modules or not getattr(sys.modules["oci"], "__path__", None):
    oci_stub = types.ModuleType("oci")
    oci_stub.__path__ = []  # mark as package so submodule imports work
    oci_exceptions = types.ModuleType("oci.exceptions")

    class _OciExc(Exception):
        pass

    oci_exceptions.ConnectTimeout = type("ConnectTimeout", (_OciExc,), {})
    oci_exceptions.RequestException = type("RequestException", (_OciExc,), {})
    oci_exceptions.ServiceError = type("ServiceError", (_OciExc,), {})
    oci_stub.exceptions = oci_exceptions
    oci_stub.pagination = types.SimpleNamespace(list_call_get_all_results=mock.Mock())
    oci_stub.core = types.ModuleType("oci.core")
    oci_stub.identity = types.ModuleType("oci.identity")
    sys.modules["oci"] = oci_stub
    sys.modules["oci.exceptions"] = oci_exceptions
    sys.modules["oci.core"] = oci_stub.core
    sys.modules["oci.identity"] = oci_stub.identity
    sys.modules["oci.pagination"] = oci_stub.pagination


def _oci_spot(**kwargs):
    from crc.oci.spot_vm import SpotVM
    from crc.service import Service

    params = dict(
        dry_run=True,
        filter_tags={"yb_task": ["itest"]},
        exception_tags={},
        age={"days": 1},
        custom_age_tag_key=None,
        notags={},
    )
    params.update(kwargs)

    s = object.__new__(SpotVM)
    Service.__init__(s)
    s.age = s.require_positive_age(params["age"])
    s.instance_names_to_delete = []
    s.volumes_to_delete = []
    s.dry_run = params["dry_run"]
    s.filter_tags = params["filter_tags"]
    s.exception_tags = params["exception_tags"]
    s.custom_age_tag_key = params["custom_age_tag_key"]
    s.notags = params["notags"]
    s.compartment_id = "ocid1.compartment.oc1..test"
    return s


def _instance(
    display_name="spot-1",
    preemptible=True,
    tags=None,
    days_old=7,
    state="RUNNING",
):
    inst = mock.Mock()
    inst.id = "ocid1.instance.oc1..test"
    inst.display_name = display_name
    inst.lifecycle_state = state
    inst.freeform_tags = tags if tags is not None else {"yb_task": "itest"}
    inst.preemptible_instance_config = mock.Mock() if preemptible else None
    inst.time_created = datetime.datetime.now(
        datetime.timezone.utc
    ) - datetime.timedelta(days=days_old)
    return inst


def _attachment(volume_id, lifecycle_state="ATTACHED"):
    att = mock.Mock()
    att.volume_id = volume_id
    att.lifecycle_state = lifecycle_state
    return att


class OciPreemptibleDetectTests(unittest.TestCase):
    def test_preemptible_config_present(self):
        s = _oci_spot()
        self.assertTrue(s._is_preemptible(_instance(preemptible=True)))

    def test_regular_instance_not_preemptible(self):
        s = _oci_spot()
        self.assertFalse(s._is_preemptible(_instance(preemptible=False)))


class OciLeftoverVolumeTests(unittest.TestCase):
    def test_attached_volumes_collected_detached_skipped(self):
        s = _oci_spot()
        compute = mock.Mock()
        with mock.patch(
            "crc.oci.spot_vm.oci.pagination.list_call_get_all_results"
        ) as listed:
            listed.return_value = mock.Mock(
                data=[
                    _attachment("vol-keep", "ATTACHED"),
                    _attachment("vol-gone", "DETACHED"),
                    _attachment("vol-detaching", "DETACHING"),
                ]
            )
            ids = s._attached_block_volume_ids(compute, "ocid1.instance.oc1..test")
        self.assertEqual(ids, ["vol-keep"])

    def test_dry_run_delete_volume_records_without_api(self):
        s = _oci_spot(dry_run=True)
        block = mock.Mock()
        s._delete_volume(block, "vol-keep")
        block.delete_volume.assert_not_called()
        self.assertEqual(s.volumes_to_delete, ["vol-keep"])

    def test_filter_requires_tags_on_instance(self):
        s = _oci_spot()
        self.assertFalse(s._has_matching_filter_tags(_instance(tags={})))
        self.assertTrue(
            s._has_matching_filter_tags(_instance(tags={"yb_task": "itest"}))
        )

    def test_empty_filter_allows_untagged(self):
        s = _oci_spot(filter_tags={})
        self.assertTrue(s._has_matching_filter_tags(_instance(tags={})))
        self.assertTrue(
            s._has_matching_filter_tags(_instance(tags={"yb_task": "itest"}))
        )


    def test_requires_positive_age(self):
        with self.assertRaises(ValueError):
            _oci_spot(age={})

    def test_delete_terminates_waits_then_deletes_volume(self):
        s = _oci_spot(dry_run=False)
        s.INSTANCE_POLL_INTERVAL_SEC = 0
        call_order = []

        compute = mock.Mock()
        block = mock.Mock()

        def terminate(instance_id, preserve_boot_volume=False):
            call_order.append("terminate")

        def wait(client, instance_id):
            call_order.append("wait")
            return True

        def delete_vol(client, volume_id):
            call_order.append(("volume", volume_id))
            s.volumes_to_delete.append(volume_id)

        compute.terminate_instance.side_effect = terminate
        inst = _instance()
        with mock.patch.object(s, "get_all_regions", return_value=["us-ashburn-1"]), mock.patch.object(
            s, "get_compute_client", return_value=compute
        ), mock.patch.object(
            s, "get_blockstorage_client", return_value=block
        ), mock.patch(
            "crc.oci.spot_vm.oci.pagination.list_call_get_all_results"
        ) as listed, mock.patch.object(
            s, "_attached_block_volume_ids", return_value=["vol-keep"]
        ), mock.patch.object(
            s, "_wait_instance_terminated", side_effect=wait
        ), mock.patch.object(
            s, "_delete_volume", side_effect=delete_vol
        ):
            # First call: list_instances; ignore subsequent pagination uses
            listed.return_value = mock.Mock(data=[inst])
            s.delete()

        self.assertEqual(
            call_order, ["terminate", "wait", ("volume", "vol-keep")]
        )
        self.assertEqual(s.instance_names_to_delete, ["spot-1"])

    def test_delete_still_tries_volumes_when_wait_fails(self):
        s = _oci_spot(dry_run=False)
        compute = mock.Mock()
        block = mock.Mock()
        call_order = []

        def terminate(instance_id, preserve_boot_volume=False):
            call_order.append("terminate")

        compute.terminate_instance.side_effect = terminate
        inst = _instance()
        with mock.patch.object(s, "get_all_regions", return_value=["us-ashburn-1"]), mock.patch.object(
            s, "get_compute_client", return_value=compute
        ), mock.patch.object(
            s, "get_blockstorage_client", return_value=block
        ), mock.patch(
            "crc.oci.spot_vm.oci.pagination.list_call_get_all_results",
            return_value=mock.Mock(data=[inst]),
        ), mock.patch.object(
            s, "_attached_block_volume_ids", return_value=["vol-keep"]
        ), mock.patch.object(
            s, "_wait_instance_terminated", return_value=False
        ), mock.patch.object(
            s, "_delete_volume", side_effect=lambda c, v: call_order.append(("volume", v))
        ):
            s.delete()

        self.assertEqual(call_order, ["terminate", ("volume", "vol-keep")])


if __name__ == "__main__":
    unittest.main()
