"""Parity: `crc.azu.vm_delete_helpers` against main (`tests/legacy/azu_vm_delete_helpers.txt`).

Both run against the same fake Azure compute and network client and the same fake clock
(`time.sleep` only records). SDK calls (in order, with arguments), recorded sleeps, the NIC
name list, log records, stdout and raised errors must be identical for every case. The module
lists no regions and reads no environment variable or credential, so there is no region
fallback or auth error to cover; delete errors are the error paths.
"""

import logging
import types
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError

from crc.azu import vm_delete_helpers as new_module

ORACLE = Path(__file__).parent / "legacy" / "azu_vm_delete_helpers.txt"
NIC = "vm-1-NIC"
ATTEMPTS = 24
SECOND = 2
Step = tuple[str, BaseException] | None


def _http(status: int | None, text: str = "http error") -> HttpResponseError:
    error = HttpResponseError(text)
    error.status_code = status
    return error


def _not_found_500() -> ResourceNotFoundError:
    error = ResourceNotFoundError("gone")
    error.status_code = 500
    return error


def _boom(text: str = "boom") -> Exception:
    return RuntimeError(text)


@dataclass
class NicCase:
    id: str
    dry_run: bool = False
    vm_name: str = "vm-1"
    suffix: str | None = None
    script: Sequence[Step] = ()
    existing: Sequence[str] = ("old",)


@dataclass
class VmCase:
    id: str
    timeout: int | None = None
    begin_error: BaseException | None = None
    result_error: BaseException | None = None
    vm_name: str = "vm-1"


def _fails(count: int, step: Step) -> list[Step]:
    return [step] * count


NIC_CASES = [
    NicCase("dry-run", dry_run=True),
    NicCase("dry-run-custom-suffix", suffix="-nic0", dry_run=True),
    NicCase("dry-run-empty-suffix", suffix="", dry_run=True),
    NicCase("dry-run-keeps-existing", existing=("a", "b"), dry_run=True),
    NicCase("dry-run-ignores-script", script=[("begin", _boom())], dry_run=True),
    NicCase("success-first-attempt"),
    NicCase("success-custom-suffix", suffix="-primary"),
    NicCase("success-empty-vm-name", vm_name=""),
    NicCase("success-empty-existing", existing=()),
    NicCase("not-found-on-begin-delete", script=[("begin", ResourceNotFoundError("gone"))]),
    NicCase("not-found-on-result", script=[("result", ResourceNotFoundError("gone"))]),
    NicCase("not-found-after-retries", script=[("begin", _boom()), ("result", _boom()), None]),
    NicCase("not-found-subclass-with-500-status", script=[("begin", _not_found_500())]),
    NicCase("http-404-on-begin-delete", script=[("begin", _http(404))]),
    NicCase("http-404-on-result", script=[("result", _http(404))]),
    NicCase("http-404-after-a-failure", script=[("begin", _http(500)), ("begin", _http(404))]),
    NicCase("http-500-then-success", script=[("begin", _http(500, "server error")), None]),
    NicCase("http-409-then-success", script=[("result", _http(409, "conflict"))]),
    NicCase("http-status-none-is-retried", script=[("begin", _http(None))]),
    NicCase("http-always-500", script=_fails(30, ("begin", _http(500, "still attached")))),
    NicCase("http-result-always-500", script=_fails(30, ("result", _http(500)))),
    NicCase("generic-error-then-success", script=[("begin", _boom("net")), None]),
    NicCase("generic-error-on-result-then-success", script=[("result", _boom("slow")), None]),
    NicCase("generic-error-always", script=_fails(30, ("begin", _boom("down")))),
    NicCase("generic-error-23-times-then-success", script=[*_fails(23, ("begin", _boom())), None]),
    NicCase("generic-error-24-times-gives-up", script=_fails(24, ("begin", _boom()))),
    NicCase("generic-error-25-times-needs-only-24", script=_fails(25, ("begin", _boom()))),
    NicCase(
        "mixed-errors-then-success",
        script=[("begin", _http(500)), ("result", _boom()), ("begin", _boom("x")), None],
    ),
    NicCase(
        "mixed-errors-then-not-found",
        script=[("begin", _boom()), ("result", _http(500)), ("begin", ResourceNotFoundError("n"))],
    ),
    NicCase("error-text-with-percent-signs", script=[("begin", _boom("100% %s %d")), None]),
    NicCase("custom-suffix-with-retries", suffix="-n", script=[("begin", _boom()), None]),
    NicCase("base-exception-propagates", script=[("begin", KeyboardInterrupt())]),
    NicCase("base-exception-on-result-propagates", script=[("result", SystemExit(2))]),
    NicCase(
        "base-exception-after-retry", script=[("begin", _boom()), ("result", KeyboardInterrupt())]
    ),
]

VM_CASES = [
    VmCase("delete"),
    VmCase("delete-custom-timeout", timeout=5),
    VmCase("delete-zero-timeout", timeout=0),
    VmCase("delete-empty-name", vm_name=""),
    VmCase("begin-delete-error", begin_error=_boom("begin failed")),
    VmCase("begin-delete-http-error", begin_error=_http(409, "conflict")),
    VmCase("result-error", result_error=_boom("lro failed")),
    VmCase("result-timeout-error", result_error=TimeoutError("timed out")),
    VmCase("result-not-found", result_error=ResourceNotFoundError("gone")),
    VmCase("base-exception", result_error=KeyboardInterrupt()),
]


class FakePoller:
    def __init__(self, calls: list[tuple[Any, ...]], kind: str, error: BaseException | None):
        self.calls = calls
        self.kind = kind
        self.error = error

    def result(self, timeout: Any = None) -> object:
        self.calls.append((f"{self.kind}.result", timeout))
        if self.error is not None:
            raise self.error
        return None


class FakeVirtualMachines:
    def __init__(self, calls: list[tuple[Any, ...]], case: VmCase) -> None:
        self.calls = calls
        self.case = case

    def begin_delete(self, *args: Any, **kwargs: Any) -> FakePoller:
        self.calls.append(("vm.begin_delete", args, kwargs))
        if self.case.begin_error is not None:
            raise self.case.begin_error
        return FakePoller(self.calls, "vm", self.case.result_error)


class FakeNicOperations:
    def __init__(self, calls: list[tuple[Any, ...]], script: Sequence[Step]) -> None:
        self.calls = calls
        self.script = list(script)

    def begin_delete(self, *args: Any, **kwargs: Any) -> FakePoller:
        self.calls.append(("nic.begin_delete", args, kwargs))
        step = self.script.pop(0) if self.script else None
        if step is not None and step[0] == "begin":
            raise step[1]
        error = step[1] if step is not None else None
        return FakePoller(self.calls, "nic", error)


@dataclass
class Outcome:
    calls: list[tuple[Any, ...]]
    names: list[str]
    logs: list[tuple[str, int, str]]
    stdout: str
    error: tuple[str, str] | None
    returned: Any = None


def _load_oracle() -> Any:
    module = types.ModuleType("legacy_azu_vm_delete_helpers")
    exec(compile(ORACLE.read_text(), str(ORACLE), "exec"), module.__dict__)
    return module


def _records(caplog: pytest.LogCaptureFixture) -> list[tuple[str, int, str]]:
    return [(r.name, r.levelno, r.getMessage()) for r in caplog.records]


def _execute(
    action: Callable[[list[tuple[Any, ...]], list[str]], Any],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    existing: Sequence[str] = (),
) -> Outcome:
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr("time.sleep", lambda seconds: calls.append(("sleep", seconds)))
    caplog.clear()
    capsys.readouterr()
    names = list(existing)
    error: tuple[str, str] | None = None
    returned: Any = None
    try:
        returned = action(calls, names)
    except BaseException as e:  # noqa: BLE001 - parity harness: the exception type and message are the outcome compared with the oracle
        error = (type(e).__name__, str(e))
    return Outcome(calls, names, _records(caplog), capsys.readouterr().out, error, returned)


def _nic_action(module: Any, case: NicCase) -> Callable[[list[tuple[Any, ...]], list[str]], Any]:
    def run(calls: list[tuple[Any, ...]], names: list[str]) -> Any:
        client = types.SimpleNamespace(network_interfaces=FakeNicOperations(calls, case.script))
        args: list[Any] = [client, "rg-test", case.vm_name, case.dry_run, names]
        if case.suffix is not None:
            return module.delete_vm_primary_nic(*args, case.suffix)
        return module.delete_vm_primary_nic(*args)

    return run


def _vm_action(module: Any, case: VmCase) -> Callable[[list[tuple[Any, ...]], list[str]], Any]:
    def run(calls: list[tuple[Any, ...]], names: list[str]) -> Any:
        client = types.SimpleNamespace(virtual_machines=FakeVirtualMachines(calls, case))
        if case.timeout is None:
            return module.begin_delete_vm_and_wait(client, "rg-test", case.vm_name)
        return module.begin_delete_vm_and_wait(client, "rg-test", case.vm_name, case.timeout)

    return run


@pytest.fixture
def debug_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)


def _nic_pair(
    case: NicCase,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> tuple[Outcome, Outcome]:
    old = _execute(_nic_action(_load_oracle(), case), monkeypatch, caplog, capsys, case.existing)
    new = _execute(_nic_action(new_module, case), monkeypatch, caplog, capsys, case.existing)
    return old, new


@pytest.mark.parametrize("case", NIC_CASES, ids=[c.id for c in NIC_CASES])
def test_nic_matches_main(
    case: NicCase,
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    old, new = _nic_pair(case, monkeypatch, caplog, capsys)
    assert new.calls == old.calls
    assert new.names == old.names
    assert new.logs == old.logs
    assert new.stdout == old.stdout
    assert new.error == old.error
    assert new.returned == old.returned


@pytest.mark.parametrize("case", VM_CASES, ids=[c.id for c in VM_CASES])
def test_vm_matches_main(
    case: VmCase,
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    old = _execute(_vm_action(_load_oracle(), case), monkeypatch, caplog, capsys)
    new = _execute(_vm_action(new_module, case), monkeypatch, caplog, capsys)
    assert new.calls == old.calls
    assert new.logs == old.logs
    assert new.stdout == old.stdout
    assert new.error == old.error
    assert new.returned == old.returned


def _nic_case(case_id: str) -> NicCase:
    return next(c for c in NIC_CASES if c.id == case_id)


def _sleeps(o: Outcome) -> list[Any]:
    return [c[1] for c in o.calls if c[0] == "sleep"]


def _begins(o: Outcome) -> int:
    return len([c for c in o.calls if c[0] == "nic.begin_delete"])


def _gave_up(o: Outcome, attempts: int) -> bool:
    return (
        o.error is None
        and o.names == ["old"]
        and _begins(o) == attempts
        and _sleeps(o) == [15] * (attempts - 1)
        and o.logs[-1]
        == ("root", logging.ERROR, f"Failed to delete the NIC - {NIC} after 24 attempts")
    )


NIC_QUIRKS: Sequence[tuple[str, Callable[[Outcome], bool]]] = [
    ("dry-run", lambda o: o.names == ["old", NIC] and o.calls == [] and o.logs == []),
    ("dry-run-custom-suffix", lambda o: o.names == ["old", "vm-1-nic0"]),
    ("dry-run-empty-suffix", lambda o: o.names == ["old", "vm-1"]),
    (
        "success-first-attempt",
        lambda o: (
            o.names == ["old", NIC]
            and o.calls == [("nic.begin_delete", ("rg-test", NIC), {}), ("nic.result", 600)]
            and o.logs == [("root", logging.INFO, f"Deleted the NIC - {NIC}")]
        ),
    ),
    (
        "not-found-on-begin-delete",
        lambda o: (
            o.names == ["old", NIC]
            and o.logs == [("root", logging.INFO, f"NIC {NIC} already removed (or never existed)")]
        ),
    ),
    (
        "not-found-subclass-with-500-status",
        lambda o: o.names == ["old", NIC] and _sleeps(o) == [],
    ),
    (
        "http-404-on-begin-delete",
        lambda o: (
            o.names == ["old", NIC]
            and o.logs == [("root", logging.INFO, f"NIC {NIC} not found (404)")]
        ),
    ),
    (
        "http-404-after-a-failure",
        lambda o: o.names == ["old", NIC] and _sleeps(o) == [15] and _begins(o) == SECOND,
    ),
    (
        "http-500-then-success",
        lambda o: (
            o.names == ["old", NIC]
            and o.logs[0]
            == ("root", logging.WARNING, f"NIC delete attempt 1/24 for {NIC}: server error")
            and _sleeps(o) == [15]
        ),
    ),
    ("http-status-none-is-retried", lambda o: o.names == ["old", NIC] and _begins(o) == SECOND),
    ("http-always-500", lambda o: _gave_up(o, ATTEMPTS)),
    ("generic-error-always", lambda o: _gave_up(o, ATTEMPTS)),
    ("generic-error-24-times-gives-up", lambda o: _gave_up(o, ATTEMPTS)),
    ("generic-error-25-times-needs-only-24", lambda o: _gave_up(o, ATTEMPTS)),
    (
        "generic-error-23-times-then-success",
        lambda o: (
            o.names == ["old", NIC]
            and _begins(o) == ATTEMPTS
            and _sleeps(o) == [15] * (ATTEMPTS - 1)
        ),
    ),
    (
        "generic-error-on-result-then-success",
        lambda o: _begins(o) == SECOND and o.names == ["old", NIC],
    ),
    (
        "base-exception-propagates",
        lambda o: o.error == ("KeyboardInterrupt", "") and o.names == ["old"] and o.logs == [],
    ),
    (
        "base-exception-after-retry",
        lambda o: o.error == ("KeyboardInterrupt", "") and _sleeps(o) == [15],
    ),
]


@pytest.mark.parametrize(
    ("case_id", "check"),
    NIC_QUIRKS,
    ids=[q[0] for q in NIC_QUIRKS],
)
def test_nic_quirk_is_preserved(
    case_id: str,
    check: Callable[[Outcome], bool],
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    old, new = _nic_pair(_nic_case(case_id), monkeypatch, caplog, capsys)
    assert check(old)
    assert check(new)


def test_vm_delete_waits_for_the_poller_with_the_timeout(
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    oracle = _load_oracle()
    for mod in (oracle, new_module):
        out = _execute(_vm_action(mod, VmCase("x")), monkeypatch, caplog, capsys)
        assert out.calls == [("vm.begin_delete", ("rg-test", "vm-1"), {}), ("vm.result", 3600)]
        assert out.logs == [("root", logging.INFO, "VM delete completed: vm-1")]
        custom = _execute(_vm_action(mod, VmCase("x", timeout=7)), monkeypatch, caplog, capsys)
        assert custom.calls[-1] == ("vm.result", 7)


def test_vm_delete_failure_is_not_swallowed_and_logs_nothing(
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = VmCase("x", result_error=_boom("lro failed"))
    for mod in (_load_oracle(), new_module):
        out = _execute(_vm_action(mod, case), monkeypatch, caplog, capsys)
        assert out.error == ("RuntimeError", "lro failed")
        assert out.logs == []


def test_keyword_arguments_match_main(
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def run_kw(mod: Any) -> Callable[[list[tuple[Any, ...]], list[str]], Any]:
        def action(calls: list[tuple[Any, ...]], names: list[str]) -> Any:
            vm_ops = FakeVirtualMachines(calls, VmCase("x"))
            vm_client = types.SimpleNamespace(virtual_machines=vm_ops)
            nic_client = types.SimpleNamespace(network_interfaces=FakeNicOperations(calls, ()))
            mod.begin_delete_vm_and_wait(
                compute_client=vm_client,
                resource_group="rg-kw",
                vm_name="vm-kw",
                timeout_sec=120,
            )
            mod.delete_vm_primary_nic(
                network_client=nic_client,
                resource_group="rg-kw",
                vm_name="vm-kw",
                dry_run=False,
                nics_names_to_delete=names,
                nic_name_suffix="-nic-kw",
            )

        return action

    old = _execute(run_kw(_load_oracle()), monkeypatch, caplog, capsys)
    new = _execute(run_kw(new_module), monkeypatch, caplog, capsys)
    assert new.calls == old.calls
    assert new.names == old.names == ["vm-kw-nic-kw"]
    assert new.logs == old.logs
    assert new.stdout == old.stdout
    assert new.error == old.error is None


def test_module_constants_are_unchanged() -> None:
    old = _load_oracle()
    for name in (
        "DEFAULT_VM_DELETE_TIMEOUT_SEC",
        "NIC_DELETE_ATTEMPTS",
        "NIC_DELETE_RETRY_INTERVAL_SEC",
    ):
        assert getattr(new_module, name) == getattr(old, name)
