"""Offline harness of the Jenkins contract test: the production commands and one CLI run.

`load_commands` reads `tests/fixtures/jenkins_commands.txt` (one `crc.py` line per production
job; `$VAR` references are replaced from `JENKINS_ENV`, the way the Jenkins shell expands them).
`run_command` runs `crc.cli.main.main` on one command with the Slack and Influx client builders,
the scan and the notifier faked and the cleaners replaced by `FakeCleaners`: no cloud call, no
credentials. `describe` is the data the contract test freezes in
`tests/helpers_ag_cli_jenkins_expected.json`.
"""

import hashlib
import os
import shlex
import string
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest

from crc.cli import bridge
from crc.cli import clients as cli_clients
from crc.cli import main as cli_main
from crc.cli import scan as cli_scan
from crc.cli.inputs import Options
from crc.cli.routes import OptionsBuilder
from tests.helpers_cli_routes import FakeCleaners

ROOT = Path(__file__).resolve().parent.parent
COMMANDS = ROOT / "tests" / "fixtures" / "jenkins_commands.txt"
EXPECTED = ROOT / "tests" / "helpers_ag_cli_jenkins_expected.json"
JENKINS_ENV = {
    "RESOURCE_GROUP": "rg-jenkins",
    "AWS_JENKINS_SLAVE": "arn:aws:iam::123456789012:user/jenkins-slave",
}
CRC_ENV = ("SLACK_BOT_TOKEN", "INFLUXDB_TOKEN", "MAX_AGE", "GOOGLE_CLOUD_PROJECT", "GCP_PROJECT")
PENDING_AGE_ROUTES = (
    ("azure", "ip", "| BUG-075 |"),
    ("azure", "nic", "| BUG-112 |"),
    ("gcp", "ip", "| BUG-062 |"),
)


def _with_age(orig: OptionsBuilder) -> OptionsBuilder:
    def builder(options: Options) -> dict[str, Any]:
        values = orig(options)
        return {**values, "age": options.age} if options.age is not None else values

    return builder


def _install_pending_age_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forward `--age` for parallel W2 lanes (`gb/azu`, `gb/gcp`) until their routes merge."""
    bugs = (ROOT / "docs" / "refactor" / "BUGS.md").read_text(encoding="utf-8")
    for cloud, kind, marker in PENDING_AGE_ROUTES:
        if any(line.startswith(marker) and line.endswith("| open |") for line in bugs.splitlines()):
            route = bridge.TABLES[cloud][kind]
            monkeypatch.setitem(
                bridge.TABLES[cloud],
                kind,
                replace(route, options=_with_age(route.options)),
            )


@dataclass(frozen=True)
class Command:
    index: int
    line: str
    argv: tuple[str, ...]

    @property
    def key(self) -> str:
        """Stable id of a command: its position and a hash of its text (changes on any edit)."""
        return f"{self.index:02d}-{hashlib.sha256(self.line.encode()).hexdigest()[:10]}"

    @property
    def label(self) -> str:
        flags = {"--cloud": "?", "--resource": "all"}
        for flag in flags:
            if flag in self.argv:
                flags[flag] = self.argv[self.argv.index(flag) + 1]
        extra = [a for a in self.argv if a in {"--dry_run", "-d", "--scan_tag"}]
        return " ".join([self.key, flags["--cloud"], flags["--resource"], *extra])


def load_commands() -> list[Command]:
    commands: list[Command] = []
    for line in COMMANDS.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        text = string.Template(line).safe_substitute(JENKINS_ENV)
        assert "$" not in text.replace("'", ""), f"unexpanded variable in {line!r}"
        words = shlex.split(text)
        assert words[:2] == ["python3", "crc.py"], line
        commands.append(Command(len(commands), line, tuple(words[2:])))
    return commands


@dataclass
class Observed:
    exit_code: int | str | None = None
    error: str | None = None
    runs: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    operations: list[str] = field(default_factory=list)
    dry_runs: list[bool] = field(default_factory=list)
    scanned: list[tuple[str, str]] = field(default_factory=list)
    clients: list[tuple[str, Any]] = field(default_factory=list)
    notified: int = 0
    max_age: list[str | None] = field(default_factory=list)
    channels: list[str | None] = field(default_factory=list)
    max_age_after: str | None = None


def run_command(command: Command, monkeypatch: pytest.MonkeyPatch) -> Observed:
    """Run `main()` on the command with every cloud and Slack call faked; record the effects."""
    seen = Observed()
    _install_pending_age_routes(monkeypatch)
    fakes = FakeCleaners().install(monkeypatch)

    class FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            seen.clients.append((type(self).__name__, args or kwargs))

    class FakeWebClient(FakeClient):
        pass

    class FakeInfluxDBClient(FakeClient):
        pass

    def scan(crc: Any, key: str, value: str) -> list[Any]:
        seen.scanned.append((key, value))
        return []

    def notify(*args: Any, **kwargs: Any) -> None:
        seen.notified += 1

    def record_max_age(context: Any) -> None:
        seen.max_age.append(os.environ.get("MAX_AGE"))

    def notifiers(crc: Any, route: Any) -> list[Any]:
        seen.channels.append(crc.slack_channel)
        return []

    for pair in [(c, k) for c, table in bridge.TABLES.items() for k in table]:
        fakes.side_effect[pair] = record_max_age
    monkeypatch.setattr(bridge, "build_notifiers", notifiers)
    monkeypatch.setattr(cli_clients, "WebClient", FakeWebClient)
    monkeypatch.setattr(cli_clients, "InfluxDBClient", FakeInfluxDBClient)
    monkeypatch.setattr(cli_scan, "scan_instances_with_tag", scan)
    monkeypatch.setattr(cli_scan, "notify_cleanup", notify)
    for name in CRC_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-offline")
    monkeypatch.setattr(sys, "argv", ["crc.py", *command.argv])
    try:
        cli_main.main()
        seen.exit_code = 0
    except SystemExit as exit_:
        seen.exit_code = exit_.code if exit_.code is not None else 0
    except Exception as error:  # noqa: BLE001 - parity harness: the exception type and message are the outcome compared with the oracle
        seen.error = f"{type(error).__name__}: {error}"
    seen.runs = list(fakes.runs)
    seen.operations = [c.operation.value for c in fakes.contexts]
    seen.dry_runs = [c.dry_run for c in fakes.contexts]
    seen.max_age_after = os.environ.get("MAX_AGE")
    return seen


def describe(seen: Observed) -> dict[str, Any]:
    """The frozen shape of one run: exit code, the pairs that ran and what each cleaner got."""
    return {
        "exit_code": seen.exit_code,
        "pairs": [[cloud, kind] for cloud, kind, _ in seen.runs],
        "operations": seen.operations,
        "dry_runs": seen.dry_runs,
        "options": [options for _, _, options in seen.runs],
        "scanned": [list(pair) for pair in seen.scanned],
        "slack_clients": sum(1 for name, _ in seen.clients if name == "FakeWebClient"),
        "influx_clients": sum(1 for name, _ in seen.clients if name == "FakeInfluxDBClient"),
        "scan_notified": seen.notified,
        "max_age_env": seen.max_age,
        "max_age_env_after_run": seen.max_age_after,
        "slack_channels": seen.channels,
    }
