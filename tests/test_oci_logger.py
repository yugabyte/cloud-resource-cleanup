"""The OCI cleaners report to `crc.core.logger` (REGION, ACTION, SKIPPED, FAILED,
RESULT) and move per-resource diagnostics to DEBUG. Exact lines of an attached run."""

import datetime
import logging
import types
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

import pytest
from oci.exceptions import ConnectTimeout

from crc.core import logger
from crc.core.cleaner import Operation, RunContext
from crc.oci.disk import DiskCleaner, DiskSettings
from crc.oci.spot_vm import SpotVmCleaner
from crc.oci.spot_vm.client import RegionClients
from crc.oci.spot_vm.decide import SpotVmSettings
from crc.oci.vm import VmCleaner, VmSettings
from tests.helpers_ad_spot_engine import run_through_engine
from tests.helpers_ad_vm_engine import run_vm
from tests.helpers_wc_oci_disk import run_disk

NOW = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=datetime.UTC)
FILTER = {"env": ["ci"]}
AGE = {"hours": 5}


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 7
        return self.now


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("OCI_PRIVATE_KEY_CONTENT", "not-a-key")
    logger.finish()
    yield
    logger.finish()


def _lines(caplog: pytest.LogCaptureFixture, *prefixes: str) -> list[tuple[int, str]]:
    return [
        (r.levelno, r.getMessage()) for r in caplog.records if r.getMessage().startswith(prefixes)
    ]


def _begin(resource: str, *, dry_run: bool, operation: str = "delete") -> None:
    logger.begin("oci", resource, operation, dry_run, {"age": AGE}, clock=Clock())


def _timeout() -> Exception:
    error: Exception = ConnectTimeout("boom")
    return error


def inst(inst_id: str, hours: float = 100, state: str = "RUNNING", tags: Any = None) -> Any:
    return types.SimpleNamespace(
        id=inst_id,
        display_name=f"name-{inst_id}",
        lifecycle_state=state,
        freeform_tags={"env": "ci"} if tags is None else tags,
        time_created=NOW - datetime.timedelta(hours=hours),
    )


class VmApi:
    def __init__(self, instances: Sequence[Any], failing: Mapping[str, Exception] | None = None):
        self.instances = list(instances)
        self.failing = dict(failing or {})

    def list_instances(self) -> Sequence[Any]:
        return self.instances

    def terminate_instance(self, instance_id: str) -> None:
        if instance_id in self.failing:
            raise self.failing[instance_id]

    def stop_instance(self, instance_id: str) -> None:
        if instance_id in self.failing:
            raise self.failing[instance_id]


def _vm_cleaner(apis: Mapping[str, Any]) -> VmCleaner:
    def factory(region: str) -> Any:
        api = apis[region]
        if isinstance(api, Exception):
            raise api
        return api

    settings = VmSettings(filter_tags=FILTER, age=AGE)
    return VmCleaner(factory, lambda: list(apis), settings, engine=True)


def _vm(cleaner: VmCleaner, *, dry_run: bool, operation: str = "delete") -> Any:
    return run_vm("oci", cleaner, dry_run=dry_run, operation=Operation(operation))


VM_INSTANCES = [inst("a"), inst("b", hours=1), inst("c", state="STOPPED"), inst("d", tags={})]


@pytest.mark.parametrize(
    ("dry_run", "operation", "kind", "done"),
    [
        (False, "delete", "deleted", 1),
        (True, "delete", "would_delete", 0),
        (False, "stop", "stopped", 1),
        (True, "stop", "would_stop", 0),
    ],
)
def test_vm_engine_reports_regions_actions_skips_and_result(
    caplog: pytest.LogCaptureFixture, dry_run: bool, operation: str, kind: str, done: int
) -> None:
    caplog.set_level(logging.DEBUG)
    _begin("vm", dry_run=dry_run, operation=operation)
    _vm(
        _vm_cleaner({"r1": VmApi(VM_INSTANCES), "r2": VmApi([])}),
        dry_run=dry_run,
        operation=operation,
    )
    logger.finish()
    action = (logging.INFO, f"ACTION {kind} cloud=oci resource=vm region=r1 id=a name=name-a")
    regions = [
        (logging.INFO, "REGION cloud=oci resource=vm region=r1 scanned=4 selected=1 seconds=0"),
        (logging.DEBUG, "REGION cloud=oci resource=vm region=r2 scanned=0 selected=0 seconds=0"),
    ]
    ordered = [action, *regions] if dry_run else [*regions, action]
    assert _lines(caplog, "REGION", "ACTION", "SKIPPED", "ERRORS", "FAILED", "RESULT") == [
        *ordered,
        (logging.INFO, "SKIPPED cloud=oci resource=vm reason=filter_mismatch count=1"),
        (logging.INFO, "SKIPPED cloud=oci resource=vm reason=state count=1"),
        (logging.INFO, "SKIPPED cloud=oci resource=vm reason=too_young count=1"),
        (
            logging.INFO,
            f"RESULT cloud=oci resource=vm operation={operation} "
            f"dry_run={'true' if dry_run else 'false'} selected=1 "
            f"done={done} failed=0 "
            "skipped=3 regions=2 regions_skipped=0 duration_s=7",
        ),
    ]


def test_vm_engine_counts_a_skipped_region_and_a_failed_terminate(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    _begin("vm", dry_run=False)
    apis = {"r1": _timeout(), "r2": VmApi([inst("a"), inst("b")], {"a": RuntimeError("denied")})}
    _vm(_vm_cleaner(apis), dry_run=False)
    logger.finish()
    assert _lines(caplog, "Region ", "Error occurred") == [
        (
            logging.WARNING,
            "Region r1: skipped list_instances vm delete — network/connectivity "
            "(ConnectTimeout): boom",
        ),
        (logging.ERROR, "Error occurred while delete instance name-a: denied"),
    ]
    assert _lines(caplog, "ACTION", "REGION", "ERRORS", "FAILED", "RESULT") == [
        (logging.INFO, "REGION cloud=oci resource=vm region=r2 scanned=2 selected=2 seconds=0"),
        (logging.INFO, "ACTION deleted cloud=oci resource=vm region=r2 id=b name=name-b"),
        (logging.ERROR, "ERRORS count=1"),
        (logging.ERROR, "FAILED cloud=oci resource=vm region=r2 id=a error=RuntimeError: denied"),
        (
            logging.INFO,
            "RESULT cloud=oci resource=vm operation=delete dry_run=false selected=1 done=1 "
            "failed=1 skipped=0 regions=1 regions_skipped=1 duration_s=7",
        ),
    ]


def test_vm_tag_dict_is_debug_and_no_run_means_no_new_lines(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    _vm(_vm_cleaner({"r1": VmApi([inst("a")])}), dry_run=True)
    records = [(r.levelno, r.getMessage()) for r in caplog.records]
    assert (logging.DEBUG, "{'env': 'ci'}") in records
    assert (logging.INFO, "{'env': 'ci'}") not in records
    assert _lines(caplog, "REGION", "ACTION", "SKIPPED", "RESULT") == []


def test_vm_cleaner_plan_and_dry_run_apply_report(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    _begin("vm", dry_run=True)
    cleaner = VmCleaner(
        lambda region: VmApi(VM_INSTANCES), lambda: ["r1"], VmSettings(FILTER, age=AGE)
    )
    context = RunContext(dry_run=True, operation=Operation.DELETE, options={}, now=NOW)
    cleaner.apply(cleaner.plan(context), context)
    logger.finish()
    assert _lines(caplog, "ACTION", "REGION") == [
        (logging.INFO, "REGION cloud=oci resource=vm region=r1 scanned=4 selected=1 seconds=0"),
        (logging.INFO, "ACTION would_delete cloud=oci resource=vm region=r1 id=a name=name-a"),
    ]


def vol(vol_id: str, hours: float = 100, state: str = "AVAILABLE") -> Any:
    return inst(vol_id, hours=hours, state=state)


class DiskApi:
    def __init__(
        self,
        volumes: Sequence[Any],
        boot_volumes: Sequence[Any] = (),
        attached: Sequence[str] = (),
        failing: Mapping[str, Exception] | None = None,
    ) -> None:
        self.volumes = list(volumes)
        self.boot_volumes = list(boot_volumes)
        self.attached = list(attached)
        self.failing = dict(failing or {})

    def list_volumes(self) -> Sequence[Any]:
        return self.volumes

    def list_boot_volumes(self) -> Sequence[Any]:
        return self.boot_volumes

    def list_volume_attachments(self) -> Sequence[Any]:
        return [
            types.SimpleNamespace(lifecycle_state="ATTACHED", volume_id=v) for v in self.attached
        ]

    def list_availability_domains(self) -> Sequence[str]:
        return ["AD-1"]

    def list_boot_volume_attachments(self, availability_domain: str) -> Sequence[Any]:
        return []

    def delete_volume(self, volume_id: str) -> None:
        if volume_id in self.failing:
            raise self.failing[volume_id]

    def delete_boot_volume(self, volume_id: str) -> None:
        if volume_id in self.failing:
            raise self.failing[volume_id]


def _disk(apis: Mapping[str, Any], *, dry_run: bool) -> None:
    run_disk(apis, dry_run=dry_run, settings=DiskSettings(FILTER, {}, {}, AGE, ""), now=NOW)


DISKS = [vol("a"), vol("b", hours=1), vol("c", state="CREATING"), vol("att")]


@pytest.mark.parametrize(
    ("dry_run", "kind", "done"), [(False, "deleted", 2), (True, "would_delete", 0)]
)
def test_disk_engine_reports_regions_actions_skips_and_result(
    caplog: pytest.LogCaptureFixture, dry_run: bool, kind: str, done: int
) -> None:
    caplog.set_level(logging.DEBUG)
    _begin("disk", dry_run=dry_run)
    api = DiskApi(DISKS, [vol("boot")], attached=["att"])
    _disk({"r1": api, "r2": _timeout()}, dry_run=dry_run)
    logger.finish()
    actions = [
        (logging.INFO, f"ACTION {kind} cloud=oci resource=disk region=r1 id=a name=name-a"),
        (logging.INFO, f"ACTION {kind} cloud=oci resource=disk region=r1 id=boot name=name-boot"),
    ]
    region = (
        logging.INFO,
        "REGION cloud=oci resource=disk region=r1 scanned=5 selected=2 seconds=0",
    )
    # plan() logs REGION before apply() deletes; a dry run logs its ACTION lines in plan().
    ordered = [region, *actions] if not dry_run else [*actions, region]
    assert _lines(caplog, "ACTION", "REGION", "SKIPPED", "ERRORS", "RESULT") == [
        *ordered,
        (logging.INFO, "SKIPPED cloud=oci resource=disk reason=attached count=1"),
        (logging.INFO, "SKIPPED cloud=oci resource=disk reason=state count=1"),
        (logging.INFO, "SKIPPED cloud=oci resource=disk reason=too_young count=1"),
        (
            logging.INFO,
            "RESULT cloud=oci resource=disk operation=delete "
            f"dry_run={'true' if dry_run else 'false'} selected=2 done={done} failed=0 "
            "skipped=3 regions=1 regions_skipped=1 duration_s=7",
        ),
    ]
    assert (logging.DEBUG, "{'env': 'ci'}") in [(r.levelno, r.getMessage()) for r in caplog.records]


def test_disk_failed_delete_is_listed_at_the_end(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    _begin("disk", dry_run=False)
    api = DiskApi([vol("a"), vol("b")], failing={"a": RuntimeError("busy")})
    _disk({"r1": api}, dry_run=False)
    logger.finish()
    assert _lines(caplog, "ERRORS", "FAILED", "RESULT") == [
        (logging.ERROR, "ERRORS count=1"),
        (logging.ERROR, "FAILED cloud=oci resource=disk region=r1 id=a error=RuntimeError: busy"),
        (
            logging.INFO,
            "RESULT cloud=oci resource=disk operation=delete dry_run=false selected=1 done=1 "
            "failed=1 skipped=0 regions=1 regions_skipped=0 duration_s=7",
        ),
    ]


def test_disk_cleaner_plan_reports_region_and_skipped_region(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    _begin("disk", dry_run=True)
    apis: dict[str, Any] = {"r1": DiskApi(DISKS), "r2": _timeout()}

    def factory(region: str) -> Any:
        if isinstance(apis[region], Exception):
            raise apis[region]
        return apis[region]

    cleaner = DiskCleaner(factory, lambda: list(apis), DiskSettings(FILTER, age=AGE))
    cleaner.plan(RunContext(dry_run=True, operation=Operation.DELETE, options={}, now=NOW))
    logger.finish()
    assert _lines(caplog, "REGION", "RESULT") == [
        (logging.INFO, "REGION cloud=oci resource=disk region=r1 scanned=4 selected=2 seconds=0"),
        (
            logging.INFO,
            "RESULT cloud=oci resource=disk operation=delete dry_run=true selected=0 done=0 "
            "failed=0 skipped=2 regions=1 regions_skipped=1 duration_s=7",
        ),
    ]


class SpotClient:
    def __init__(self, instances: Sequence[Any], volume_tags: Mapping[str, Any] | None = None):
        self.instances = list(instances)
        self.volume_tags = dict(volume_tags or {})
        self.fail_attachments = False

    def list_instances(self, compartment_id: str) -> Any:
        return types.SimpleNamespace(data=self.instances)

    def list_volume_attachments(
        self, compartment_id: str, instance_id: str = "", volume_id: str = ""
    ) -> Any:
        if volume_id:
            owner = volume_id.removeprefix("v-")
            data = [
                types.SimpleNamespace(
                    volume_id=volume_id, lifecycle_state="ATTACHED", instance_id=owner
                )
            ]
            return types.SimpleNamespace(data=data)
        if self.fail_attachments:
            raise RuntimeError("no attachments")
        data = [types.SimpleNamespace(volume_id=f"v-{instance_id}", lifecycle_state="ATTACHED")]
        return types.SimpleNamespace(data=data)

    def get_instance(self, instance_id: str) -> Any:
        return types.SimpleNamespace(data=types.SimpleNamespace(lifecycle_state="TERMINATED"))

    def terminate_instance(self, instance_id: str, preserve_boot_volume: bool) -> None:
        return None

    def get_volume(self, volume_id: str) -> Any:
        tags = self.volume_tags.get(volume_id, {})
        return types.SimpleNamespace(
            data=types.SimpleNamespace(freeform_tags=tags, time_created=NOW)
        )

    def delete_volume(self, volume_id: str) -> None:
        return None


def spot_inst(inst_id: str, hours: float = 100, preemptible: bool = True) -> Any:
    return types.SimpleNamespace(
        id=inst_id,
        display_name=f"name-{inst_id}",
        lifecycle_state="RUNNING",
        freeform_tags={"env": "ci"},
        defined_tags={},
        metadata={},
        preemptible_instance_config=object() if preemptible else None,
        time_created=NOW - datetime.timedelta(hours=hours),
    )


def _paginate(call: Any, **kwargs: Any) -> Any:
    return call(**kwargs)


def _run_spot(
    client: SpotClient,
    *,
    dry_run: bool,
    exception_tags: Mapping[str, Sequence[str]] | None = None,
) -> None:
    settings = SpotVmSettings(
        filter_tags=FILTER,
        exception_tags=exception_tags or {},
        age=AGE,
    )
    cleaner = SpotVmCleaner(
        lambda region: RegionClients(client, client, "cmp"),
        lambda: ["r1"],
        settings,
        _paginate,
        engine=True,
    )
    ctx = RunContext(dry_run=dry_run, operation=Operation.DELETE, options={}, now=NOW)
    run_through_engine("oci", "spot_instances", lambda _c: cleaner, ctx)


@pytest.mark.parametrize(("dry_run", "kind"), [(False, "deleted"), (True, "would_delete")])
def test_spot_vm_engine_reports_instance_and_volume_actions(
    caplog: pytest.LogCaptureFixture, dry_run: bool, kind: str
) -> None:
    caplog.set_level(logging.DEBUG)
    _begin("spot_instances", dry_run=dry_run)
    client = SpotClient(
        [spot_inst("a"), spot_inst("young", hours=1), spot_inst("od", preemptible=False)]
    )
    client.volume_tags = {"v-a": {}}
    _run_spot(client, dry_run=dry_run)
    logger.finish()
    done = 0 if dry_run else 2
    actions = [
        (
            logging.INFO,
            f"ACTION {kind} cloud=oci resource=spot_instances region=r1 id=a name=name-a",
        ),
        (logging.INFO, f"ACTION {kind} cloud=oci resource=spot_instances region=r1 id=v-a name=-"),
    ]
    region = (
        logging.INFO,
        "REGION cloud=oci resource=spot_instances region=r1 scanned=3 selected=1 seconds=0",
    )
    ordered = [region, *actions] if not dry_run else [*actions, region]
    assert _lines(caplog, "ACTION", "REGION", "SKIPPED", "RESULT") == [
        *ordered,
        (logging.INFO, "SKIPPED cloud=oci resource=spot_instances reason=not_preemptible count=1"),
        (logging.INFO, "SKIPPED cloud=oci resource=spot_instances reason=too_young count=1"),
        (
            logging.INFO,
            "RESULT cloud=oci resource=spot_instances operation=delete "
            f"dry_run={'true' if dry_run else 'false'} selected=2 done={done} failed=0 "
            "skipped=2 regions=1 regions_skipped=0 duration_s=7",
        ),
    ]


def test_spot_vm_protected_leftover_volume_is_debug_and_counted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    _begin("spot_instances", dry_run=False)
    client = SpotClient([spot_inst("a")], {"v-a": {"keep": "1"}})
    _run_spot(client, dry_run=False, exception_tags={"keep": ["1"]})
    logger.finish()
    assert _lines(caplog, "Skipping leftover") == [
        (logging.DEBUG, "Skipping leftover volume v-a: exception_tags/notags")
    ]
    assert _lines(caplog, "SKIPPED") == [
        (
            logging.INFO,
            "SKIPPED cloud=oci resource=spot_instances reason=leftover_exception_tag count=1",
        )
    ]


def test_spot_vm_attachment_failure_is_a_listed_failure(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    _begin("spot_instances", dry_run=False)
    client = SpotClient([spot_inst("a")])
    client.fail_attachments = True
    _run_spot(client, dry_run=False)
    logger.finish()
    assert _lines(caplog, "ERRORS", "FAILED", "RESULT") == [
        (logging.ERROR, "ERRORS count=1"),
        (
            logging.ERROR,
            "FAILED cloud=oci resource=spot_instances region=r1 id=a "
            "error=RuntimeError: no attachments",
        ),
        (
            logging.INFO,
            "RESULT cloud=oci resource=spot_instances operation=delete dry_run=false selected=0 "
            "done=0 failed=1 skipped=0 regions=1 regions_skipped=0 duration_s=7",
        ),
    ]


@pytest.mark.parametrize(("dry_run", "kind"), [(False, "deleted"), (True, "would_delete")])
def test_spot_vm_cleaner_plan_apply_reports(
    caplog: pytest.LogCaptureFixture, dry_run: bool, kind: str
) -> None:
    caplog.set_level(logging.INFO)
    _begin("spot_instances", dry_run=dry_run)
    client = SpotClient([spot_inst("a")])
    cleaner = SpotVmCleaner(
        lambda region: RegionClients(client, client, "cmp"),
        lambda: ["r1"],
        SpotVmSettings(filter_tags=FILTER, age=AGE),
        _paginate,
    )
    context = RunContext(dry_run=dry_run, operation=Operation.DELETE, options={}, now=NOW)
    cleaner.apply(cleaner.plan(context), context)
    logger.finish()
    assert _lines(caplog, "ACTION", "REGION") == [
        (
            logging.INFO,
            "REGION cloud=oci resource=spot_instances region=r1 scanned=1 selected=1 seconds=0",
        ),
        (
            logging.INFO,
            f"ACTION {kind} cloud=oci resource=spot_instances region=r1 id=a name=name-a",
        ),
        (logging.INFO, f"ACTION {kind} cloud=oci resource=spot_instances region=r1 id=v-a name=-"),
    ]
