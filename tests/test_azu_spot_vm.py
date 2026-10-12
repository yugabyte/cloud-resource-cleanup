# Copyright (c) Yugabyte, Inc.

import unittest
from unittest import mock


def _azu_spot(**kwargs):
    from crc.azu.spot_vm import SpotVM

    params = dict(
        resource_group="rg",
        dry_run=True,
        filter_tags={"yb_task": ["itest"]},
        exception_tags={},
        age={"days": 1},
        custom_age_tag_key=None,
        notags={},
    )
    params.update(kwargs)
    with mock.patch("crc.azu.spot_vm.Base"):
        return SpotVM(**params)


def _disk(name=None, disk_id=None, delete_option="Detach"):
    managed = mock.Mock()
    managed.id = disk_id
    disk = mock.Mock()
    disk.name = name
    disk.delete_option = delete_option
    disk.managed_disk = managed if disk_id is not None else None
    return disk


def _vm(name="spot-1", os_disk=None, data_disks=None, priority="Spot"):
    vm = mock.Mock()
    vm.name = name
    vm.priority = priority
    vm.tags = {"yb_task": "itest"}
    profile = mock.Mock()
    profile.os_disk = os_disk
    profile.data_disks = data_disks or []
    vm.storage_profile = profile
    return vm


class AzureDetachedDiskTests(unittest.TestCase):
    def test_collects_detach_os_and_data_disks(self):
        s = _azu_spot()
        vm = _vm(
            os_disk=_disk(
                name="os-disk",
                disk_id="/subscriptions/x/resourceGroups/rg/providers/Microsoft.Compute/disks/os-disk",
                delete_option="Detach",
            ),
            data_disks=[
                _disk(name="data-1", disk_id=None, delete_option="Detach"),
                _disk(name="data-dot", disk_id=None, delete_option="Delete"),
            ],
        )
        self.assertEqual(s._detached_disk_names(vm), ["os-disk", "data-1"])

    def test_delete_option_skips_empty_and_delete(self):
        s = _azu_spot()
        vm = _vm(
            os_disk=_disk(name="os", delete_option=""),
            data_disks=[_disk(name="d1", delete_option="Delete")],
        )
        self.assertEqual(s._detached_disk_names(vm), [])

    def test_disk_name_from_managed_id(self):
        s = _azu_spot()
        self.assertEqual(
            s._disk_name_from_id(
                "/subscriptions/x/resourceGroups/rg/providers/Microsoft.Compute/disks/vol-a"
            ),
            "vol-a",
        )
        self.assertIsNone(s._disk_name_from_id(None))

    def test_untagged_always_skipped(self):
        s = _azu_spot(filter_tags={})
        vm = _vm()
        vm.tags = None
        self.assertFalse(s._should_include_by_tags(vm))

    def test_requires_positive_age(self):
        with self.assertRaises(ValueError):
            _azu_spot(age={})


class AzureDeleteOrderTests(unittest.TestCase):
    def test_delete_vm_removes_leftover_disks_after_vm(self):
        s = _azu_spot(dry_run=False)
        vm = _vm(
            os_disk=_disk(name="os-detach", delete_option="Detach"),
            data_disks=[],
        )
        call_order = []

        def track_vm_delete(*args, **kwargs):
            call_order.append("vm")

        def track_disk(name):
            call_order.append(("disk", name))
            s.disks_names_to_delete.append(name)

        with mock.patch("crc.azu.spot_vm.begin_delete_vm_and_wait", side_effect=track_vm_delete), mock.patch(
            "crc.azu.spot_vm.delete_vm_primary_nic"
        ), mock.patch.object(s, "_delete_disk", side_effect=track_disk):
            s._delete_vm(vm)

        self.assertEqual(call_order, ["vm", ("disk", "os-detach")])
        self.assertEqual(s.instance_names_to_delete, ["spot-1"])

    def test_dry_run_records_leftover_disks_without_deleting_vm(self):
        s = _azu_spot(dry_run=True)
        vm = _vm(os_disk=_disk(name="os-detach", delete_option="Detach"))
        with mock.patch("crc.azu.spot_vm.begin_delete_vm_and_wait") as delete_vm, mock.patch(
            "crc.azu.spot_vm.delete_vm_primary_nic"
        ):
            s._delete_vm(vm)
        delete_vm.assert_not_called()
        self.assertEqual(s.instance_names_to_delete, ["spot-1"])
        self.assertEqual(s.disks_names_to_delete, ["os-detach"])


if __name__ == "__main__":
    unittest.main()
