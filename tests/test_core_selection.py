"""Selection and CLI target rules: every branch of `crc.py main()`."""

import functools
import importlib.util
import logging
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from crc.cli import bridge
from crc.cli import context as cli_context
from crc.cli import main as cli_main
from crc.core import selection as selection_module
from crc.core.cleaner import Operation
from crc.core.kinds import KINDS
from crc.core.selection import (
    CLOUDS,
    EXTRA_RESOURCES,
    RESOURCES,
    Selection,
)
from tests import helpers_ag_cli_added as added_table
from tests.helpers_cli_routes import FakeCleaners
from tests.helpers_oracle import load_legacy_crc


@dataclass(frozen=True, slots=True)
class _Options:
    kms_key_description: str | None = None
    kms_user: str | None = None
    slack_notify_users: bool = False
    slack_user_label: str | None = None


ROOT = Path(__file__).resolve().parent.parent
ORACLE_EXTRA_RESOURCES = ["spot_instance_requests", "spot_instances"]
ORACLE_KINDS = [r for r in [*RESOURCES, *EXTRA_RESOURCES] if r not in added_table.added_kinds()]
KMS_OPTIONS = _Options(kms_key_description="desc", kms_user="arn:user")
EBS_MESSAGE = "AWS EBS volume cleanup requires '--cloud aws --resource disk'"

METHOD_KINDS = {
    "delete_disks": "disk",
    "delete_ip": "ip",
    "delete_nic": "nic",
    "delete_vm": "vm",
    "stop_vm": "vm",
    "delete_vpc": "vpc",
    "delete_kms": "kms",
    "delete_spot_instance_requests": "spot_instance_requests",
    "delete_spot_instances": "spot_instances",
}


@pytest.fixture
def crc_cli() -> Iterator[ModuleType]:
    spec = importlib.util.spec_from_file_location("crc_cli_selection", ROOT / "crc.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["crc_cli_selection"] = module
    spec.loader.exec_module(module)
    yield module
    del sys.modules["crc_cli_selection"]


LAST_RECORDED_CALLS: list[tuple[str, str]] = []


def run_crc_main(
    crc_cli: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    cloud: str,
    resource: str,
    operation: str,
    options: dict[str, Any],
) -> list[tuple[str, str]]:
    """Run crc.py main() with a recording CRC; return the (cloud, kind) pairs it reached."""
    LAST_RECORDED_CALLS.clear()
    calls = LAST_RECORDED_CALLS
    current_cloud = "?"

    class FakeCRC:
        def __init__(self, cloud: str, *args: Any, **kwargs: Any) -> None:
            nonlocal current_cloud
            self.cloud = cloud
            current_cloud = cloud

        def __getattr__(self, name: str) -> Any:
            kind = METHOD_KINDS[name]

            def record(*args: Any, **kwargs: Any) -> None:
                calls.append((self.cloud, kind))

            return record

    args = dict.fromkeys(
        [
            "project_id",
            "resource_states",
            "filter_tags",
            "exception_tags",
            "name_regex",
            "exception_regex",
            "age",
            "custom_age_tag_key",
            "max_age",
            "detach_age",
            "notags",
            "slack_channel",
            "slack_notify_users",
            "slack_user_label",
            "influxdb",
            "kms_pending_window",
            "kms_key_description",
            "kms_user",
            "scan_tag",
        ]
    )
    args.update(
        project_id="proj",
        resource_group="rg",
        cloud=cloud,
        resource=resource,
        operation_type=operation,
        dry_run=True,
    )
    args.update(options)
    monkeypatch.setattr(cli_main, "get_argparser", lambda: args)
    monkeypatch.setattr(cli_context, "CRC", FakeCRC)
    fakes = FakeCleaners().install(monkeypatch)
    for routed_cloud, table in bridge.TABLES.items():
        for routed_kind in table:
            fakes.side_effect[(routed_cloud, routed_kind)] = functools.partial(
                lambda pair, ctx: calls.append(pair), (routed_cloud, routed_kind)
            )
    crc_cli.main()
    return calls


SUPPORTED_ON = {kind.name: kind.clouds for kind in KINDS if kind.clouds is not None}


def unsupported_items(items: Any) -> list[tuple[str, str]]:
    """BUG-017, BUG-009, BUG-084: the pairs `main` ran for a cloud that does not support them."""
    return [(c, k) for c, k in items if c not in SUPPORTED_ON.get(k, (c,))]


def raises_up_front(cloud: str, items: Any) -> bool:
    """An explicit cloud, or `--cloud all` with a supported pair after the first unsupported one."""
    bad = unsupported_items(items)
    if not bad:
        return False
    rest = list(items)[list(items).index(bad[0]) :]
    return cloud != "all" or len(rest) > len(bad)


def run_main_after_fix(  # noqa: PLR0913, PLR0917 - mirrors run_crc_main plus the items
    crc_cli: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    cloud: str,
    resource: str,
    operation: str,
    options: dict[str, Any],
    items: Any,
) -> tuple[list[tuple[str, str]], list[str]]:
    """Run crc.py main() and return the (pairs reached, warnings) that `items` must give.

    `items` is what `main` on the main branch reached. BUG-017: an unsupported pair of an
    explicit cloud now raises before anything runs (nothing reached); under `--cloud all` it is
    skipped with one warning.
    """
    bad = unsupported_items(items)
    if raises_up_front(cloud, items):
        with pytest.raises(ValueError):
            run_crc_main(crc_cli, monkeypatch, cloud, resource, operation, options)
        assert LAST_RECORDED_CALLS == []
        return [], []
    with caplog.at_level(logging.WARNING):
        reached = run_crc_main(crc_cli, monkeypatch, cloud, resource, operation, options)
    logged = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    expected_skips = [f"{k} is not supported on {c}; skipped under --cloud all" for c, k in bad]
    assert logged[len(logged) - len(expected_skips) :] == expected_skips
    assert reached == [item for item in items if item not in bad]
    return list(items), logged[: len(logged) - len(expected_skips)]


# (cloud, resource, operation, options, expected items, expected warning substrings)
OK_CASES: list[tuple[str, str, Operation, _Options | None, list[tuple[str, str]], int]] = [
    ("aws", "disk", Operation.DELETE, None, [("aws", "disk")], 0),
    ("azure", "disk", Operation.DELETE, None, [("azure", "disk")], 0),
    ("gcp", "disk", Operation.DELETE, None, [("gcp", "disk")], 0),
    ("oci", "disk", Operation.DELETE, None, [("oci", "disk")], 0),
    ("aws", "ip", Operation.DELETE, None, [("aws", "ip")], 0),
    ("azure", "keypair", Operation.DELETE, None, [("azure", "keypair")], 0),
    ("aws", "nic", Operation.DELETE, None, [("aws", "nic")], 0),
    ("aws", "snapshot", Operation.DELETE, None, [("aws", "snapshot")], 0),
    ("azure", "snapshot", Operation.DELETE, None, [("azure", "snapshot")], 0),
    ("aws", "vm", Operation.DELETE, None, [("aws", "vm")], 0),
    ("gcp", "vm", Operation.STOP, None, [("gcp", "vm")], 0),
    ("aws", "kms", Operation.DELETE, KMS_OPTIONS, [("aws", "kms")], 0),
    ("azure", "kms", Operation.DELETE, KMS_OPTIONS, [("azure", "kms")], 0),
    (
        "aws",
        "spot_instance_requests",
        Operation.DELETE,
        None,
        [("aws", "spot_instance_requests")],
        0,
    ),
    ("azure", "spot_instance_requests", Operation.DELETE, None, [], 1),
    ("gcp", "spot_instance_requests", Operation.DELETE, None, [], 1),
    ("oci", "spot_instance_requests", Operation.DELETE, None, [], 1),
    ("aws", "spot_instances", Operation.DELETE, None, [], 1),
    ("azure", "spot_instances", Operation.DELETE, None, [("azure", "spot_instances")], 0),
    ("gcp", "spot_instances", Operation.DELETE, None, [("gcp", "spot_instances")], 0),
    ("oci", "spot_instances", Operation.DELETE, None, [("oci", "spot_instances")], 0),
    ("all", "vm", Operation.DELETE, None, [(c, "vm") for c in CLOUDS], 0),
    ("all", "vm", Operation.STOP, None, [(c, "vm") for c in CLOUDS], 0),
    ("all", "ip", Operation.DELETE, None, [(c, "ip") for c in CLOUDS], 0),
    ("all", "snapshot", Operation.DELETE, None, [(c, "snapshot") for c in CLOUDS], 0),
    ("all", "keypair", Operation.DELETE, None, [(c, "keypair") for c in CLOUDS], 0),
    ("all", "kms", Operation.DELETE, KMS_OPTIONS, [(c, "kms") for c in CLOUDS], 0),
    (
        "all",
        "spot_instance_requests",
        Operation.DELETE,
        None,
        [("aws", "spot_instance_requests")],
        3,
    ),
    ("all", "spot_instances", Operation.DELETE, None, [("azure", "spot_instances")], 3),
    (
        "aws",
        "vm",
        Operation.DELETE,
        _Options(slack_notify_users=True, slack_user_label="owner"),
        [("aws", "vm")],
        0,
    ),
]


@pytest.mark.parametrize("case", OK_CASES)
def test_build_selection_matches_crc_main(
    crc_cli: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[str, str, Operation, _Options | None, list[tuple[str, str]], int],
    caplog: pytest.LogCaptureFixture,
) -> None:
    cloud, resource, operation, options, expected, warns = case
    extra: dict[str, Any] = {}
    if options is not None:
        extra = {
            "kms_key_description": options.kms_key_description,
            "kms_user": options.kms_user,
            "slack_notify_users": options.slack_notify_users,
            "slack_user_label": options.slack_user_label,
        }
    reached, logged = run_main_after_fix(
        crc_cli, monkeypatch, caplog, cloud, resource, operation.value, extra, expected
    )
    if not raises_up_front(cloud, expected):
        selection = Selection(tuple(reached), tuple(logged))
        assert list(selection.items) == expected
        assert len(selection.warnings) == warns


ERROR_CASES: list[tuple[str, str, Operation, _Options | None, str]] = [
    (
        "aws",
        "all",
        Operation.DELETE,
        KMS_OPTIONS,
        "All Resources cleanup is supported only with all Clouds. "
        "Format: --cloud all --resources all",
    ),
    ("azure", "all", Operation.DELETE, KMS_OPTIONS, "All Resources cleanup is supported only"),
    ("aws", "disk", Operation.STOP, None, "Stop is supported only for vm resource"),
    ("all", "all", Operation.STOP, KMS_OPTIONS, "Stop is supported only for vm resource"),
    ("aws", "ip", Operation.STOP, None, "Stop is supported only for vm resource"),
    (
        "aws",
        "vm",
        Operation.DELETE,
        _Options(slack_notify_users=True),
        "--slack_user_label is mandatory",
    ),
    ("aws", "kms", Operation.DELETE, None, "Key description string is required"),
    (
        "aws",
        "kms",
        Operation.DELETE,
        _Options(kms_user="u"),
        "Key description string is required",
    ),
    (
        "aws",
        "kms",
        Operation.DELETE,
        _Options(kms_key_description="d"),
        "Jenkins user ARN is reuired",
    ),
    ("all", "all", Operation.DELETE, None, "Key description string is required"),
    (
        "all",
        "all",
        Operation.DELETE,
        _Options(kms_key_description="d"),
        "Jenkins user ARN is reuired",
    ),
    ("all", "all", Operation.DELETE, KMS_OPTIONS, EBS_MESSAGE),
    ("all", "disk", Operation.DELETE, None, EBS_MESSAGE),
]


@pytest.mark.parametrize("case", ERROR_CASES)
def test_build_selection_errors_match_crc_main(
    crc_cli: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[str, str, Operation, _Options | None, str],
) -> None:
    cloud, resource, operation, options, message = case
    extra: dict[str, Any] = {}
    if options is not None:
        extra = {
            "kms_key_description": options.kms_key_description,
            "kms_user": options.kms_user,
            "slack_notify_users": options.slack_notify_users,
            "slack_user_label": options.slack_user_label,
        }
    with pytest.raises(ValueError) as theirs:
        run_crc_main(crc_cli, monkeypatch, cloud, resource, operation.value, extra)
    assert message in str(theirs.value)
    assert LAST_RECORDED_CALLS == []


@pytest.mark.parametrize(
    ("cloud", "resource", "options", "extra"),
    [
        ("all", "all", KMS_OPTIONS, {"kms_key_description": "desc", "kms_user": "arn:user"}),
        ("all", "disk", None, {}),
    ],
)
def test_ebs_breaker_selects_nothing_before_raising(
    crc_cli: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    cloud: str,
    resource: str,
    options: _Options | None,
    extra: dict[str, Any],
) -> None:
    assert options is None or options.kms_user == extra["kms_user"]
    with pytest.raises(ValueError) as theirs:
        run_crc_main(crc_cli, monkeypatch, cloud, resource, "delete", extra)
    assert EBS_MESSAGE in str(theirs.value)
    assert LAST_RECORDED_CALLS == []


def test_all_expansion_order_follows_crc_py() -> None:
    """The oracle order of `--cloud all` and `--resource all` stays; extras must be in ADDED."""
    oracle = load_legacy_crc("crc_cli_selection_order")
    try:
        assert list(CLOUDS) == oracle.CLOUDS
        in_all = [r.kind for r in added_table.ADDED_RESOURCES if r.in_all]
        outside_all = [r.kind for r in added_table.ADDED_RESOURCES if not r.in_all]
        unlisted = [r for r in RESOURCES if r not in oracle.RESOURCES and r not in in_all]
        assert not unlisted, f"--resource all expands to {unlisted}; {added_table.ADDED_HINT}"
        assert [r for r in RESOURCES if r in oracle.RESOURCES] == oracle.RESOURCES
        assert list(RESOURCES[: len(oracle.RESOURCES)]) == oracle.RESOURCES
        assert sorted(RESOURCES) == sorted([*oracle.RESOURCES, *in_all])
        unlisted_extra = [r for r in EXTRA_RESOURCES if r not in ORACLE_EXTRA_RESOURCES]
        assert sorted(unlisted_extra) == sorted(outside_all), (
            f"extra resources {unlisted_extra} differ from ADDED_RESOURCES; "
            f"{added_table.ADDED_HINT}"
        )
        assert [r for r in EXTRA_RESOURCES if r in ORACLE_EXTRA_RESOURCES] == ORACLE_EXTRA_RESOURCES
    finally:
        sys.modules.pop("crc_cli_selection_order", None)


@pytest.mark.parametrize("cloud", ["aws", "azure", "gcp", "oci"])
@pytest.mark.parametrize("resource", ["ip", "vm", "nic", "keypair", "snapshot"])
def test_single_pair(
    crc_cli: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    cloud: str,
    resource: str,
) -> None:
    expected = [(cloud, resource)]
    reached, _ = run_main_after_fix(
        crc_cli, monkeypatch, caplog, cloud, resource, "delete", {}, expected
    )
    if raises_up_front(cloud, expected):
        assert reached == []
        return
    selection = Selection(tuple(reached))
    assert selection == Selection(((cloud, resource),))
    assert list(selection.items) == reached


@pytest.mark.parametrize(
    ("cloud", "resource"), [("bogus", "vm"), ("aws", "bogus"), ("", "vm"), ("aws", "")]
)
def test_unknown_values_raise(
    crc_cli: ModuleType, monkeypatch: pytest.MonkeyPatch, cloud: str, resource: str
) -> None:
    assert not hasattr(selection_module, "build_selection")
    assert not hasattr(selection_module, "SelectionOptions")
    assert bridge.route_for(cloud, resource) is None
    argv = ["crc.py", "--cloud", cloud, "--resource", resource, "--operation_type", "delete"]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as exited:
        crc_cli.get_argparser()
    assert exited.value.code == len(("cloud", "resource"))


@pytest.mark.parametrize(
    ("cloud", "resource"),
    [
        ("azure", "spot_instance_requests"),
        ("gcp", "spot_instance_requests"),
        ("oci", "spot_instance_requests"),
        ("all", "spot_instance_requests"),
        ("aws", "spot_instances"),
        ("all", "spot_instances"),
    ],
)
def test_warning_texts_match_crc_py(
    crc_cli: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    cloud: str,
    resource: str,
) -> None:
    with caplog.at_level(logging.WARNING):
        reached = run_crc_main(crc_cli, monkeypatch, cloud, resource, "delete", {})
    logged = tuple(r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)
    selection = Selection(tuple(reached), logged)
    assert selection.warnings == _expected_warnings(cloud, resource)
    assert list(selection.items) == reached


def _expected_warnings(cloud: str, resource: str) -> tuple[str, ...]:
    clouds = CLOUDS if cloud == "all" else (cloud,)
    if resource == "spot_instance_requests":
        return tuple(
            f"spot_instance_requests is AWS-only; skipped for {c} (use --resource spot_instances)"
            for c in clouds
            if c != "aws"
        )
    aws = (
        "For AWS use --resource spot_instance_requests "
        "(cancel request before terminate). Skipping spot_instances."
    )
    others = tuple(
        f"GCP/OCI spot_instances require explicit --cloud {c} (not --cloud all); skipped. "
        "Azure spot_instances still runs under --cloud all for backward compatibility."
        for c in clouds
        if cloud == "all" and c in ("gcp", "oci")
    )
    return (aws, *others)


@pytest.mark.parametrize("cloud", [*CLOUDS, "all"])
@pytest.mark.parametrize("resource", [*ORACLE_KINDS, "all"])
@pytest.mark.parametrize("operation", [Operation.DELETE, Operation.STOP])
def test_exhaustive_grid_matches_crc_main(
    crc_cli: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    cloud: str,
    resource: str,
    operation: Operation,
) -> None:
    extra = {"kms_key_description": "desc", "kms_user": "arn:user"}
    if (
        (operation == Operation.STOP and resource != "vm")
        or (resource == "all" and cloud != "all")
        or (cloud == "all" and resource in ("disk", "all"))
    ):
        with pytest.raises(ValueError):
            run_crc_main(crc_cli, monkeypatch, cloud, resource, operation.value, extra)
        assert LAST_RECORDED_CALLS == []
        return
    clouds = CLOUDS if cloud == "all" else (cloud,)
    kinds = RESOURCES if resource == "all" else (resource,)
    expected = [
        (c, k)
        for c in clouds
        for k in kinds
        if not (k == "spot_instance_requests" and c != "aws")
        and not (k == "spot_instances" and (c == "aws" or (cloud == "all" and c in ("gcp", "oci"))))
    ]
    reached, logged_list = run_main_after_fix(
        crc_cli, monkeypatch, caplog, cloud, resource, operation.value, extra, expected
    )
    logged = tuple(logged_list)
    if not raises_up_front(cloud, expected):
        selection = Selection(tuple(reached), logged)
        assert list(selection.items) == expected
        assert selection.warnings == logged
