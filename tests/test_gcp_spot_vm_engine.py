# Copyright (c) Yugabyte, Inc.

"""Cleaner-based copy of the locked `tests/test_gcp_spot_vm.py`: the same safety cases (a spot
VM is detected, the label and age filters keep what they must, only `autoDelete=false` disks are
leftovers, the instance delete is waited for before its disk, a dry run calls nothing) driven
through `SpotVMCleaner` and `RunContext` instead of the `SpotVM` adapter."""

import datetime
import unittest
from collections import UserList
from typing import Any
from unittest import mock

from crc.core.cleaner import Operation, RunContext
from crc.core.registry import DEFAULT_REGISTRY
from crc.gcp.spot_vm import SpotSettings, SpotVMCleaner, build_spot_cleaner
from crc.gcp.spot_vm import disks as leftover_disks
from crc.gcp.spot_vm import instances as spot_instances

NOW = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=datetime.UTC)


def _settings(**kwargs: Any) -> SpotSettings:
    params: dict[str, Any] = dict(
        project_id="proj",
        filter_labels={"yb_task": ["itest"]},
        exception_labels={},
        age={"days": 1},
        custom_age_tag_key=None,
        notags={},
        poll_sec=0,
    )
    params.update(kwargs)
    return SpotSettings(**params)


def _context(dry_run: bool = True, **options: Any) -> RunContext:
    return RunContext(dry_run=dry_run, operation=Operation.DELETE, options=options, now=NOW)


def _scheduling(preemptible: bool = False, provisioning_model: str | None = None) -> Any:
    scheduling = mock.Mock()
    scheduling.preemptible = preemptible
    scheduling.provisioning_model = provisioning_model
    return scheduling


def _disk(source: str, auto_delete: bool = True) -> Any:
    disk = mock.Mock()
    disk.source = source
    disk.auto_delete = auto_delete
    return disk


def _instance(
    name: str = "spot-1",
    scheduling: Any = None,
    disks: list[Any] | None = None,
    labels: dict[str, str] | None = None,
) -> Any:
    instance = mock.Mock()
    instance.name = name
    instance.scheduling = scheduling
    instance.disks = disks or []
    instance.labels = labels if labels is not None else {"yb_task": "itest"}
    instance.status = "RUNNING"
    instance.creation_timestamp = "2020-01-01T00:00:00.000000+00:00"
    instance.metadata = None
    return instance


class FakeClient:
    """Records the order of the calls; every operation is DONE at once."""

    def __init__(self, instances: list[Any], disk_labels: dict[str, str] | None = None) -> None:
        self.instances = instances
        self.disk_labels = disk_labels or {}
        self.calls: list[str] = []

    def list_zones(self, project_id: str) -> list[dict[str, str]]:
        return [{"name": "us-central1-a"}]

    def list_instances(self, project_id: str, zone: str) -> list[Any]:
        return self.instances

    def delete_instance(self, project_id: str, zone: str, instance: str) -> dict[str, str]:
        self.calls.append("instance")
        return {"name": "op-1"}

    def get_operation(self, project: str, scope: str, location: str, name: str) -> dict[str, str]:
        self.calls.append("operation")
        return {"status": "DONE"}

    def get_disk(self, project: str, scope: str, location: str, disk: str) -> dict[str, Any]:
        return {"labels": self.disk_labels}

    def delete_disk(self, project: str, scope: str, location: str, disk: str) -> dict[str, str]:
        self.calls.append("disk")
        return {}

    def close(self) -> None:
        return None


def _cleaner(client: FakeClient | None = None, **kwargs: Any) -> SpotVMCleaner:
    return SpotVMCleaner(client or FakeClient([]), _settings(**kwargs))


class GcpSpotDetectTests(unittest.TestCase):
    def test_preemptible_true_is_spot(self) -> None:
        self.assertTrue(
            spot_instances.is_spot_instance(_instance(scheduling=_scheduling(preemptible=True)))
        )

    def test_provisioning_model_spot_is_spot(self) -> None:
        self.assertTrue(
            spot_instances.is_spot_instance(
                _instance(scheduling=_scheduling(provisioning_model="SPOT"))
            )
        )

    def test_standard_vm_is_not_spot(self) -> None:
        self.assertFalse(
            spot_instances.is_spot_instance(
                _instance(scheduling=_scheduling(preemptible=False, provisioning_model="STANDARD"))
            )
        )

    def test_missing_scheduling_is_not_spot(self) -> None:
        inst = _instance()
        inst.scheduling = None
        self.assertFalse(spot_instances.is_spot_instance(inst))

    def test_empty_filter_allows_unlabeled(self) -> None:
        inst = _instance(labels={})
        settings = _settings(filter_labels={})
        self.assertTrue(spot_instances.has_matching_filter_label(inst, settings))

    def test_requires_positive_age(self) -> None:
        with self.assertRaises(ValueError):
            build_spot_cleaner(_context(project_id="proj", age={}))

    def test_registered_factory_requires_project_id(self) -> None:
        factory = DEFAULT_REGISTRY.get("gcp", "spot_instances")
        with self.assertRaises(ValueError):
            factory(_context(age={"days": 1}))

    def test_invalid_custom_age_skips_even_when_old(self) -> None:
        settings = _settings(age={"days": 7}, custom_age_tag_key="retention")
        # Old enough for --age; zero custom age must fail closed (keep).
        inst = _instance(labels={"retention": "{'days': 0}"})
        self.assertFalse(spot_instances.is_old_instance(inst, settings, lambda: NOW))


class GcpNonAutodeleteDiskTests(unittest.TestCase):
    def test_collects_auto_delete_false_only(self) -> None:
        inst = _instance(
            disks=[
                _disk("projects/p/zones/z/disks/keep-me", auto_delete=False),
                _disk("projects/p/zones/z/disks/gone-with-vm", auto_delete=True),
            ]
        )
        refs = leftover_disks.non_autodelete_disks(inst, "proj")
        self.assertEqual([r["name"] for r in refs], ["keep-me"])
        self.assertEqual(refs[0]["scope"], "zone")

    def test_disk_name_from_source(self) -> None:
        ref = leftover_disks.disk_ref_from_source("projects/p/zones/us-central1-a/disks/d1", "proj")
        self.assertEqual(ref["name"] if ref else None, "d1")
        self.assertIsNone(leftover_disks.disk_ref_from_source(None, "proj"))

    def test_disk_ref_parses_regional(self) -> None:
        ref = leftover_disks.disk_ref_from_source(
            "projects/p/regions/us-central1/disks/rd1", "proj"
        )
        self.assertEqual(
            ref,
            {"project": "p", "scope": "region", "location": "us-central1", "name": "rd1"},
        )

    def test_dry_run_delete_disk_records_without_api(self) -> None:
        client = FakeClient([])
        label = leftover_disks.remove_leftover_disk(
            client,
            _settings(),
            {"project": "proj", "scope": "zone", "location": "us-central1-a", "name": "keep-me"},
            "us-central1-a",
            dry_run=True,
        )
        self.assertEqual(client.calls, [])
        self.assertEqual(label, "proj/us-central1-a/keep-me")


class GcpDeleteOrderTests(unittest.TestCase):
    def _instance_with_leftover_disk(self) -> Any:
        return _instance(
            scheduling=_scheduling(preemptible=True),
            disks=[_disk("projects/p/zones/z/disks/keep-me", auto_delete=False)],
        )

    def test_delete_waits_for_zone_op_before_disk(self) -> None:
        client = FakeClient([self._instance_with_leftover_disk()])
        cleaner = _cleaner(client)
        context = _context(dry_run=False)
        result = cleaner.apply(cleaner.plan(context), context)
        self.assertEqual(client.calls, ["instance", "operation", "disk"])
        self.assertEqual(result.related["Disk"], ("p/z/keep-me",))
        self.assertEqual(result.succeeded, ("spot-1",))

    def test_dry_run_plan_calls_no_delete(self) -> None:
        client = FakeClient([self._instance_with_leftover_disk()])
        plan = _cleaner(client).plan(_context(dry_run=True))
        self.assertEqual(client.calls, [])
        self.assertEqual([c.id for c in plan.candidates], ["spot-1"])
        self.assertEqual(plan.related["Disk"], ("p/z/keep-me",))

    def test_disk_exception_labels_skip_delete(self) -> None:
        client = FakeClient([], disk_labels={"keep": "true"})
        self.assertFalse(
            leftover_disks.disk_ok_to_delete(
                client,
                _settings(exception_labels={"keep": ["true"]}),
                {"project": "p", "scope": "zone", "location": "z", "name": "d1"},
                "z",
            )
        )

    def test_gke_node_skipped(self) -> None:
        inst = _instance(
            scheduling=_scheduling(preemptible=True),
            labels={"yb_task": "itest", "goog-gke-node": ""},
        )
        self.assertTrue(spot_instances.is_managed_or_gke_node(inst))

    def test_mig_created_by_non_list_sequence(self) -> None:
        # proto-plus RepeatedComposite is a MutableSequence, not list/tuple.
        item = mock.Mock()
        item.key = "created-by"
        item.value = "projects/p/zones/z/instanceGroupManagers/mig-1"
        inst = _instance(scheduling=_scheduling(preemptible=True))
        meta = mock.Mock()
        meta.items = UserList([item])
        inst.metadata = meta
        self.assertTrue(spot_instances.is_managed_or_gke_node(inst))


if __name__ == "__main__":
    unittest.main()
