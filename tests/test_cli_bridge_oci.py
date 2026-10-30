"""Parity: the engine bridge with the real OCI cleaners against main's frozen oracles.

The legacy side is `tests.helpers_oracle.load_legacy_crc()`: `tests/legacy/crc_main.txt` with its
`Disk`, `VM` and `SpotVM` bound to `tests/legacy/oci_*.txt`, driven through a fake OCI SDK
(`tests.helpers_cl2_oci`) over the same fake API objects. The bridge runs `DiskCleaner`,
`VmCleaner` and `SpotVmCleaner` built by their registered factories; both read the same fake
API, Slack and Influx clients. For `disk`, `vm` (delete and stop) and `spot_instances`, in a dry
run and a real run, the other log records, Slack, Influx, stdout and the raised error must equal
the oracle's, and so must the mutating SDK calls. The oracles emit no run-log lines (RUN START,
ACTION, SKIPPED, ERRORS, FAILED, RESULT, REGION), so the routed lines are compared with the lines
the legacy adapters produced, frozen in `tests/helpers_cl2_oci_runlog.py`. Allowed differences: a
listing error in a later region now stops the pair before any mutation (BUG-408), and the log
levels of BUG-353 and BUG-358.
"""

import logging
import re
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager, redirect_stdout
from dataclasses import dataclass
from io import StringIO
from types import ModuleType
from typing import Any

import oci
import pytest

from crc.cli import bridge, dispatch
from crc.cli.inputs import Targets, read_options
from crc.cli.runner import CRC as NEW_CRC
from crc.oci._base import COMPARTMENT_ID
from crc.oci.spot_vm.client import RegionClients
from tests.helpers_cl2_oci import install_sdk, paginate
from tests.helpers_cl2_oci_runlog import FROZEN_RUN_LINES
from tests.helpers_log_fix import expected_logs
from tests.log_fix_oci import RULES
from tests.test_cli_crc_parity import Case, Logs, Scenario, build, oracle
from tests.test_oci_disk import NOW as DISK_NOW
from tests.test_oci_disk import FakeApi as DiskApi
from tests.test_oci_disk import att, vol
from tests.test_oci_spot_instances import FakeClient as SpotApi
from tests.test_oci_spot_instances import inst as spot_inst
from tests.test_oci_vm import FakeApi as VmApi
from tests.test_oci_vm import inst

__all__ = ["oracle"]

TAGS = {"env": ["ci"]}
FLAGS: dict[str, Any] = {
    "cloud": "oci",
    "filter_tags": TAGS,
    "exception_tags": {},
    "notags": {"owner": ["x"]},
    "age": {"hours": 5},
    "custom_age_tag_key": "age_tag",
}
MODES = [
    ("both", "on", "on"),
    ("off", "off", "off"),
    ("slack", "on", "off"),
    ("sfx", "off", "on_suffix"),
]
REGIONS_LINE = "Retrieved list of OCI regions: "
MUTATING = ("delete_volume", "delete_boot_volume", "terminate_instance", "stop_instance")


class Down(DiskApi, VmApi):
    """An API whose listing raises `error`."""

    def __init__(self, error: Exception) -> None:
        DiskApi.__init__(self)
        self.error = error

    def list_volumes(self) -> Sequence[Any]:
        raise self.error

    def list_instances(self, *args: Any, **kwargs: Any) -> Sequence[Any]:
        raise self.error


@dataclass(frozen=True)
class Kind:
    name: str
    operation: str
    act: Callable[[Any], Any]
    apis: Callable[[str | None], dict[str, Any]]


def _disk_apis(second: str | None) -> dict[str, Any]:
    first = DiskApi(
        [vol("a"), vol("young", hours=1), vol("att")],
        [vol("b"), vol("c")],
        [att("att")],
        failing={"c": RuntimeError("boom")},
    )
    return {"r1": first, "r2": _second(second, DiskApi([vol("d")], [vol("e")]))}


def _vm_apis(second: str | None) -> dict[str, Any]:
    first = VmApi(
        [inst("a"), inst("young", hours=1), inst("st", state="STOPPED"), inst("f")],
        failing={"f": RuntimeError("boom")},
    )
    return {"r1": first, "r2": _second(second, VmApi([inst("d")]))}


def _spot_apis(second: str | None) -> dict[str, Any]:
    od = spot_inst("od")
    od.preemptible_instance_config = None
    first = SpotApi(
        [spot_inst("a"), spot_inst("young", hours=1), od],
        {"a": ["vol-a", "vol-keep"]},
        {"vol-keep": {"owner": "x"}},
    )
    return {"r1": first, "r2": _second(second, SpotApi([spot_inst("d")], {"d": ["vol-d"]}))}


def _second(error: str | None, normal: Any) -> Any:
    if error == "connect":
        return Down(oci.exceptions.ConnectTimeout("t"))
    if error == "boom":
        return Down(RuntimeError("list down"))
    return normal


KINDS = [
    Kind(
        "disk",
        "delete",
        lambda c: c.delete_disks(TAGS, {}, {"hours": 5}, "age_tag", {}, [], [], False, None),
        _disk_apis,
    ),
    Kind("vm", "delete", lambda c: c.delete_vm(TAGS, {}, {"hours": 5}, "age_tag", None), _vm_apis),
    Kind("vm", "stop", lambda c: c.stop_vm(TAGS, {}, {"hours": 5}, "age_tag"), _vm_apis),
    Kind(
        "spot_instances",
        "delete",
        lambda c: c.delete_spot_instances(TAGS, {}, {"hours": 5}, "age_tag"),
        _spot_apis,
    ),
]


class Collector(logging.Handler):
    def __init__(self) -> None:
        super().__init__(0)
        self.lines: list[tuple[str, str, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        text = re.sub(r"seconds=\d+", "seconds=0", record.getMessage())
        text = re.sub(r"duration=\S+", "duration=0", text)
        self.lines.append((record.name, record.levelname, text))


def _patch_routed(mp: pytest.MonkeyPatch, apis: dict[str, Any]) -> None:
    mp.setattr("crc.service.Service.__init__", lambda self: None)
    mp.setattr("crc.oci._base.Base.__init__", lambda self: None)
    mp.setattr("crc.oci.disk.cleaner.Base", lambda: object())
    mp.setattr("crc.oci.vm.cleaner.Base", lambda: object())
    mp.setattr("crc.oci.spot_vm.cleaner.Base", lambda: object())

    def factory(base: Any) -> Callable[[str], Any]:
        return lambda region: apis[region]

    def spot_factory(region: str) -> RegionClients:
        return RegionClients(apis[region], apis[region], COMPARTMENT_ID)

    def regions(base: Any) -> Callable[[], list[str]]:
        return lambda: list(apis)

    for name in ("crc.oci.disk.cleaner", "crc.oci.vm.cleaner", "crc.oci.spot_vm.cleaner"):
        mp.setattr(f"{name}.regions_of", regions)
    mp.setattr("crc.oci.disk.cleaner.api_for", factory)
    mp.setattr("crc.oci.vm.cleaner.compute_api_for", factory)
    mp.setattr("crc.oci.spot_vm.cleaner.clients_for", lambda base: spot_factory)
    mp.setattr("crc.oci.spot_vm.client.oci.pagination.list_call_get_all_results", paginate)
    mp.setattr(bridge, "init_run_logging", lambda: None)
    mp.setattr(bridge, "datetime", type("D", (), {"now": staticmethod(lambda tz=None: DISK_NOW)}))


def _patch_legacy(mp: pytest.MonkeyPatch, apis: dict[str, Any], oracle_module: ModuleType) -> None:
    adapters = (oracle_module.OCI_Disk, oracle_module.OCI_VM, oracle_module.OCI_SpotVM)
    install_sdk(mp, apis, adapters, DISK_NOW)


@dataclass(frozen=True)
class Run:
    """One side of a comparison: the kind, dry run, Slack and Influx mode, the second region's
    listing error (`connect`, `boom` or none) and whether the bridge or the legacy adapter runs."""

    kind: Kind
    dry: bool
    mode: tuple[str, str, str]
    second: str | None
    routed: bool


@contextmanager
def _capture() -> Iterator[Collector]:
    collector = Collector()
    root, run_log = logging.getLogger(), logging.getLogger("crc.run")
    saved = (root.level, run_log.level)
    root.addHandler(collector)
    root.setLevel(logging.DEBUG)
    run_log.setLevel(logging.DEBUG)
    try:
        yield collector
    finally:
        root.removeHandler(collector)
        root.setLevel(saved[0])
        run_log.setLevel(saved[1])


def _act(run: Run, crc: Any, options: Any, targets: Targets) -> None:
    kind = run.kind
    if run.routed:
        assert bridge.route_for("oci", kind.name) is not None
        dispatch._run_pair(crc, "oci", kind.name, options, targets)
        return
    kind.act(crc)


def _run(run: Run, oracle_module: ModuleType) -> dict[str, Any]:
    with pytest.MonkeyPatch.context() as mp:
        return _run_patched(run, oracle_module, mp)


def _run_patched(run: Run, oracle_module: ModuleType, mp: pytest.MonkeyPatch) -> dict[str, Any]:
    kind, dry, mode = run.kind, run.dry, run.mode
    apis = kind.apis(run.second)
    if run.routed:
        _patch_routed(mp, apis)
    else:
        _patch_legacy(mp, apis, oracle_module)
    case = Case(mode[0], "oci", lambda c: None, Scenario({}, dry, mode[1], mode[2]))
    logs = Logs()
    flags = {**FLAGS, "resource": kind.name, "operation_type": kind.operation, "dry_run": dry}
    options = read_options(flags)
    targets = Targets("oci", False, ["oci"], [kind.name])
    out = StringIO()
    result: Any = ("ok",)
    with _capture() as collector, redirect_stdout(out):
        try:
            crc = build(NEW_CRC if run.routed else oracle_module.CRC, case, logs)
            _act(run, crc, options, targets)
        except Exception as exc:  # noqa: BLE001 - parity harness: the exception type and message are the outcome compared with the oracle
            result = ("error", type(exc).__name__, str(exc))
    if run.routed:
        run_lines = [f"{lvl} {t}" for n, lvl, t in collector.lines if n == "crc.run"]
    else:
        assert not [t for n, _, t in collector.lines if n == "crc.run"], (
            "the oracle logs no run lines"
        )
        run_lines = list(FROZEN_RUN_LINES[(kind.name, kind.operation, dry, run.second)])
    return {
        "result": result,
        "slack": logs.slack,
        "influx": logs.influx,
        "stdout": out.getvalue(),
        "run": run_lines,
        "other": [(n, lvl, t) for n, lvl, t in collector.lines if n != "crc.run"],
        "calls": {r: [c for c in a.calls if c[0] in MUTATING] for r, a in apis.items()},
        "all_calls": sorted(c for a in apis.values() for c in a.calls),
    }


def _expected_other(oracle_logs: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    """The oracle's non-run log records as the routed path emits them.

    BUG-353 and BUG-358 move the raw tag dumps (and the OCI leftover-volume skip) to DEBUG. The
    routed path takes its regions from the patched `regions_of` seam instead of
    `Base.get_all_regions`, so the oracle's one "Retrieved list of OCI regions" line is not
    emitted; nothing else may differ.
    """
    records = expected_logs(oracle_logs, {"BUG-353", "BUG-358"}, RULES)
    return sorted(r for r in records if not str(r[-1]).startswith(REGIONS_LINE))


def _expected_all_calls(
    kind: Kind, second: str | None, oracle_calls: list[tuple[Any, ...]]
) -> list[tuple[Any, ...]]:
    """The oracle's SDK calls plus, for spot instances, BUG-255: before a leftover volume is
    deleted the routed path also reads the volume's attachments (one read per leftover volume
    the tag check lets through: `vol-a` in `r1`, and `vol-d` in `r2` when it lists)."""
    if kind.name != "spot_instances":
        return oracle_calls
    reads = [("list_volume_attachments", "vol-a")]
    if second is None:
        reads.append(("list_volume_attachments", "vol-d"))
    return sorted([*oracle_calls, *reads])


def _by_kind(lines: list[str], prefix: str) -> list[str]:
    return [line for line in lines if prefix in line]


PARAMS = [
    pytest.param(
        kind, dry, mode, second, id=f"{kind.name}-{kind.operation}-dry={dry}-{mode[0]}-{second}"
    )
    for kind in KINDS
    for dry in (True, False)
    for mode in MODES
    for second in (None, "connect", "boom")
]


@pytest.mark.parametrize(("kind", "dry", "mode", "second"), PARAMS)
def test_routed_pair_matches_the_legacy_adapter(
    kind: Kind,
    dry: bool,
    mode: tuple[str, str, str],
    second: str | None,
    oracle: ModuleType,
) -> None:
    old = _run(Run(kind, dry, mode, second, False), oracle)
    new = _run(Run(kind, dry, mode, second, True), oracle)
    assert new["result"] == old["result"]
    assert new["stdout"] == old["stdout"]
    assert new["run"][0] == old["run"][0]
    if second == "boom" and not dry and kind.name != "spot_instances":
        assert new["calls"] == {"r1": [], "r2": []}
        assert old["calls"]["r1"], "the legacy path deleted region r1 before the listing error"
        assert not _by_kind(new["run"], "ACTION")
        assert not any("name-" in str(call) for call in new["slack"] + new["influx"])
        if kind.name == "vm":
            assert new["slack"] == old["slack"] == [] and new["influx"] == old["influx"] == []
        return
    assert new["run"][-1] == old["run"][-1]
    for tag in ("ACTION", "SKIPPED", "ERRORS", "FAILED"):
        assert _by_kind(new["run"], tag) == _by_kind(old["run"], tag)
    assert new["slack"] == old["slack"]
    assert new["influx"] == old["influx"]
    assert new["calls"] == old["calls"]
    assert new["all_calls"] == _expected_all_calls(kind, second, old["all_calls"])
    assert sorted(new["run"]) == sorted(old["run"])
    assert sorted(new["other"]) == _expected_other(old["other"])


@pytest.mark.parametrize("kind", KINDS, ids=lambda k: f"{k.name}-{k.operation}")
@pytest.mark.parametrize("dry", [True, False])
def test_the_comparison_is_not_vacuous(kind: Kind, dry: bool, oracle: ModuleType) -> None:
    """Both paths log ACTION lines, send Slack and Influx with display names, and mutate."""
    new = _run(Run(kind, dry, MODES[0], None, True), oracle)
    old = _run(Run(kind, dry, MODES[0], None, False), oracle)
    assert new["result"] == old["result"] == ("ok",)
    assert _by_kind(new["run"], "ACTION") and len(new["slack"]) >= 1 and len(new["influx"]) >= 1
    assert any("name-" in str(call) for call in new["slack"] + new["influx"])
    assert not any("ocid" in str(call) for call in new["slack"] + new["influx"])
    assert bool(new["calls"]["r1"]) is (not dry)


@pytest.mark.parametrize(
    ("name", "file"),
    [("OCI_Disk", "oci_disk"), ("OCI_VM", "oci_vm"), ("OCI_SpotVM", "oci_spot_vm")],
)
def test_the_legacy_side_runs_the_frozen_oracles(oracle: ModuleType, name: str, file: str) -> None:
    """The adapters the oracle CRC builds come from `tests/legacy/`, not from `crc/oci`."""
    adapter = getattr(oracle, name)
    assert adapter.__init__.__globals__["__file__"].endswith(f"tests/legacy/{file}.txt")
