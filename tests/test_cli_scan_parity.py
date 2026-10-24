"""Parity: `crc.cli.scan.scan_instances_with_tag` against the CRC class of main (`crc_main.txt`).

Moved out of `tests/test_cli_crc_parity.py` with the same oracle
(`load_legacy_crc()`), the same scenario grid and the same compared fields. The oracle runs
`main`'s `CRC` on fake adapters patched in the oracle module; the side under test runs
`crc.cli.runner.CRC` as context with the SDK-facing VM classes patched through their own modules
(`tests/helpers_wa2_scan.py`), so these cases do not care which module defines the function.
"""

import re
from collections.abc import Callable
from dataclasses import replace
from types import ModuleType
from typing import Any

import pytest

import crc.cli.scan as scan_module
from crc.aws.vm import scan as aws_scan
from crc.aws.vm.settings import all_regions
from crc.cli.runner import CRC as NEW_CRC
from crc.cli.scan import scan_instances_with_tag
from tests.helpers_cl2_crc import MIN_SEEN, Case, execute
from tests.helpers_oracle import load_legacy_crc
from tests.helpers_wa2_scan import execute_scan, scan_cases

SCAN_CASES = scan_cases("scan", lambda c: c.scan_instances_with_tag("auto_clean", "false"))
SCAN_UNSUPPORTED_CASES = scan_cases(
    "scan_unsupported", lambda c: c.scan_instances_with_tag("k", "v"), scan_supported=False
)


def routed_case(case: Case) -> Case:
    """The case with its act calling `crc.cli.scan.scan_instances_with_tag` on the context."""
    key, value = ("k", "v") if case.name.startswith("scan_unsupported") else ("auto_clean", "false")
    return replace(case, act=lambda crc: scan_instances_with_tag(crc, key, value))


@pytest.fixture(scope="module")
def oracle() -> ModuleType:
    return load_legacy_crc("legacy_crc_main_scan")


NO_FINDER_CLASSES = {
    "azure": "crc.azu.vm.adapter.VM",
    "gcp": "crc.gcp.vm_adapter.VM",
    "oci": "crc.oci.vm.adapter.VM",
}
FAKE_CLASS = re.compile(r"<class 'tests\.helpers_cl2_crc\.(\w+)'>")
FAKE_NAMES = {"AZU_VM": "azure", "GCP_VM": "gcp", "OCI_VM": "oci"}


def production_class(match: re.Match[str]) -> str:
    cloud = FAKE_NAMES.get(match.group(1))
    return f"<class '{NO_FINDER_CLASSES[cloud]}'>" if cloud else match.group(0)


def production_view(value: Any) -> Any:
    """The oracle's record with its fake adapter class texts spelled as the production ones.

    Only AWS has a finder, so the other clouds raise NotImplementedError naming the class of
    their former VM adapter; the oracle's fakes name their own class. The construction entries
    (`new`) are compared by `test_scan_builds_once` since the adapter classes are gone.
    """
    if isinstance(value, str):
        return FAKE_CLASS.sub(production_class, value)
    if isinstance(value, tuple):
        return tuple(production_view(v) for v in value)
    if isinstance(value, list):
        return [
            production_view(v) for v in value if not (isinstance(v, tuple) and v[:1] == ("new",))
        ]
    return value


def drop_new(adapters: list[Any]) -> list[Any]:
    return [call for call in adapters if call[:1] != ("new",)]


def compare_scan(oracle_module: ModuleType, case: Case) -> None:
    """The oracle runs without a finder for every cloud but AWS, as production does."""
    cloud = case.cloud
    if cloud not in ("aws", "other") and case.scenario.scan_supported:
        case = replace(case, scenario=replace(case.scenario, scan_supported=False))
    old = execute(oracle_module.CRC, [oracle_module], case)
    new = execute_scan(NEW_CRC, routed_case(case))
    builds = [call for call in new["adapters"] if call[:1] == ("new",)]
    expected_builds = [call[:2] for call in old["adapters"] if call[:1] == ("new",)]
    assert [call[:2] for call in builds] == expected_builds, f"{case.name}: set-up calls"
    new["adapters"] = drop_new(new["adapters"])
    for key in old:
        assert new[key] == production_view(old[key]), f"{case.name}: {key} differs"


@pytest.mark.parametrize("case", SCAN_CASES)
def test_scan_matches_main(oracle: ModuleType, case: Case) -> None:
    compare_scan(oracle, case)


@pytest.mark.parametrize("case", SCAN_UNSUPPORTED_CASES)
def test_scan_unsupported_matches_main(oracle: ModuleType, case: Case) -> None:
    compare_scan(oracle, case)


def test_scan_cases_are_not_vacuous(oracle: ModuleType) -> None:
    seen = {"hit": 0, "unsupported": 0, "invalid_cloud": 0}
    for param in SCAN_CASES[::7] + SCAN_UNSUPPORTED_CASES[::7]:
        outcome = execute_scan(NEW_CRC, routed_case(param.values[0]))
        result = outcome["result"]
        if result[0] == "ok":
            assert any(call[1] == "find_instances_with_tag" for call in outcome["adapters"][1:])
            seen["hit"] += 1
        elif "does not implement" in result[2]:
            seen["unsupported"] += 1
        else:
            seen["invalid_cloud"] += 1
    assert all(count > MIN_SEEN for count in seen.values()), seen


class FakeCrc:
    def __init__(self, cloud: str, resource_group: str = "") -> None:
        self.cloud = cloud
        self.resource_group = resource_group


@pytest.mark.parametrize("cloud", ["azure", "gcp", "oci"])
def test_scan_names_the_former_adapter_class(cloud: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scan_module, "_prepare_scan", lambda crc: None)
    with pytest.raises(NotImplementedError) as raised:
        scan_instances_with_tag(FakeCrc(cloud), "k", "v")
    assert str(raised.value) == (
        f"<class '{NO_FINDER_CLASSES[cloud]}'> does not implement find_instances_with_tag"
    )


def test_scan_calls_the_aws_scan_directly(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    def fake_scan(key: str, value: str) -> list[dict[str, str]]:
        calls.append((key, value))
        return [{"id": "i-1"}]

    monkeypatch.setattr(scan_module, "_prepare_scan", lambda crc: None)
    monkeypatch.setattr(aws_scan, "scan_tag", fake_scan)
    assert scan_instances_with_tag(FakeCrc("aws"), "auto_clean", "false") == [{"id": "i-1"}]
    assert calls == [("auto_clean", "false")]


def test_prepare_scan_keeps_the_setup_of_the_adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[str] = []

    def record(entry: str) -> Callable[..., None]:
        def run(*args: Any) -> None:
            built.append(":".join([entry, *args]))

        return run

    monkeypatch.setattr(scan_module, "Service", record("service"))
    monkeypatch.setattr(scan_module, "AzureBase", record("azure"))
    monkeypatch.setattr(scan_module, "OciBase", record("oci"))
    monkeypatch.setattr(scan_module, "ComputeVmClient", record("gcp"))
    for cloud in ("aws", "azure", "gcp", "oci"):
        scan_module._prepare_scan(FakeCrc(cloud, "rg"))
    assert built == ["service", "service", "azure:rg", "service", "gcp", "service", "oci"]
    with pytest.raises(ValueError, match="Invalid cloud provided: other"):
        scan_module._prepare_scan(FakeCrc("other"))
    assert len(built) == len(set(range(7)))


@pytest.mark.parametrize("cloud", ["azure", "oci"])
def test_prepare_scan_reports_missing_credentials(
    cloud: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (
        "AZURE_CREDENTIALS_SUBSCRIPTION_ID",
        "OCI_PRIVATE_KEY_CONTENT",
        "OCI_PRIVATE_KEY_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(scan_module, "Service", lambda: None)
    with pytest.raises(KeyError):
        scan_module._prepare_scan(FakeCrc(cloud))


def test_aws_scan_tag_delegates_to_find_instances_with_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, str, Any, Any, Any]] = []

    def fake_find(
        key: str, value: str, session_f: Any, client_f: Any, regions_f: Any
    ) -> list[dict[str, str]]:
        captured.append((key, value, session_f, client_f, regions_f))
        return [{"id": "i-42"}]

    monkeypatch.setattr(aws_scan, "find_instances_with_tag", fake_find)
    assert aws_scan.scan_tag("auto_clean", "false") == [{"id": "i-42"}]
    assert captured == [
        (
            "auto_clean",
            "false",
            aws_scan._boto3_session,
            aws_scan._boto3_scan_client,
            all_regions,
        )
    ]
