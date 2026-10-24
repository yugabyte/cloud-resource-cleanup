"""Drive `CRC` of the routed code on the oracle's scan scenarios, without legacy modules.

The oracle side is `execute(oracle.CRC, [oracle], case)`: `main`'s `CRC` on fakes bound in the
oracle module. The VM adapter classes are gone: the scan calls `crc.aws.vm.scan.scan_tag` for AWS
and raises NotImplementedError for the other clouds. `execute_scan` replaces `_prepare_scan` (the
set-up the adapter constructors did) and `scan_tag` through their modules by the recording fakes
of the oracle, then drives `CRC` as `execute` does.
"""

import io
import itertools
from collections.abc import Callable, Iterator
from contextlib import contextmanager, redirect_stdout
from typing import Any

import pytest

import crc.cli.scan as scan_module
from crc.aws.vm import scan as aws_scan
from crc.cli.constants import CLOUDS as SUPPORTED_CLOUDS
from tests.helpers_cl2_crc import (
    DATA,
    INFLUX_MODES,
    SLACK_MODES,
    Case,
    Logs,
    Recorded,
    Scenario,
    build,
    make_fake,
)

VM_CLASSES: dict[str, str] = {
    "aws": "AWS_VM",
    "azure": "AZU_VM",
    "gcp": "GCP_VM",
    "oci": "OCI_VM",
}
CLOUDS = ["aws", "azure", "gcp", "oci", "other"]


def scenario_grid(values: dict[str, Any], **overrides: Any) -> Iterator[tuple[str, Scenario]]:
    for dry, slack, influx in itertools.product([False, True], SLACK_MODES, INFLUX_MODES):
        yield (
            f"dry={dry}-slack={slack}-influx={influx}",
            Scenario(dict(values), dry_run=dry, slack=slack, influx=influx, **overrides),
        )


def scan_cases(
    name: str, act: Callable[[Any], Any], clouds: list[str] = CLOUDS, **overrides: Any
) -> list[Any]:
    cases = []
    for cloud, data in itertools.product(clouds, DATA):
        for label, scenario in scenario_grid(DATA[data], **overrides):
            case = Case(f"{name}-{cloud}-{data}-{label}", cloud, act, scenario, routed=name)
            cases.append(pytest.param(case, id=case.name))
    return cases


@contextmanager
def patched_vm_classes(scenario: Scenario, logs: Logs) -> Iterator[None]:
    """Replace the two seams of `crc.cli.scan` by the recording fakes of the oracle.

    `_prepare_scan` stands for the construction of the oracle's VM class (it records the `new`
    entry with the cloud's adapter name); `crc.aws.vm.scan.scan_tag` stands for AWS's
    `find_instances_with_tag`, which the oracle's fake only has when `scan_supported`.
    """
    fake_aws = make_fake(VM_CLASSES["aws"], scenario, logs)

    def prepare(crc: Any) -> None:
        name = VM_CLASSES.get(crc.cloud)
        if name is None:
            raise ValueError(
                f"Invalid cloud provided: {crc.cloud}. Supported clouds are {SUPPORTED_CLOUDS}"
            )
        logs.adapters.append(("new", name))

    def scan_tag(tag_key: str, tag_value: str) -> Any:
        if "find_instances_with_tag" not in fake_aws.__dict__:
            raise NotImplementedError(f"{fake_aws} does not implement find_instances_with_tag")
        return fake_aws.__dict__["find_instances_with_tag"](None, tag_key, tag_value)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(scan_module, "_prepare_scan", prepare)
        patch.setattr(aws_scan, "scan_tag", scan_tag)
        yield


def execute_scan(crc_class: Any, case: Case) -> Recorded:
    """`execute` for `crc_class` with the VM classes patched through their own modules."""
    logs = Logs()
    out = io.StringIO()
    result: Any = None
    attributes: Any = None
    with patched_vm_classes(case.scenario, logs), redirect_stdout(out):
        try:
            crc = build(crc_class, case, logs)
            attributes = sorted((k, repr(v)) for k, v in vars(crc).items() if "client" not in k)
            result = ("ok", repr(case.act(crc)))
        except Exception as exc:  # noqa: BLE001 - parity harness: the exception type and message are the outcome compared with the oracle
            result = ("error", type(exc).__name__, str(exc))
    return {
        "result": result,
        "attributes": attributes,
        "adapters": logs.adapters,
        "reads": logs.reads,
        "slack": logs.slack,
        "influx": logs.influx,
        "stdout": out.getvalue(),
    }
