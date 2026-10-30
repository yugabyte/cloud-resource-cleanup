"""Offline smoke: `python crc.py` once per (cloud, kind) of the production Jenkins commands.

Each command of `tests/fixtures/jenkins_commands.txt` (the first per pair, `--scan_tag` lines
skipped) runs in a subprocess with `--dry_run`, every SDK client replaced by an in-memory fake
and sockets denied (`scripts/offline_hook/sitecustomize.py`). A pair passes when the process
exits 0 and prints exactly one `RUN START` and one `RESULT` line for that pair.

Usage: python scripts/smoke_offline.py
"""

import os
import re
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "jenkins_commands.txt"
HOOK_DIR = ROOT / "scripts" / "offline_hook"
TIMEOUT_S = 120
SUBSTITUTIONS = {"${AWS_JENKINS_SLAVE}": "smoke-user", "$RESOURCE_GROUP": "smoke-rg"}
DUMMY_ENV = {
    "AZURE_CREDENTIALS_SUBSCRIPTION_ID": "00000000-0000-0000-0000-000000000000",
    "AZURE_CREDENTIALS_TENANT_ID": "00000000-0000-0000-0000-000000000000",
    "AZURE_CREDENTIALS_CLIENT_ID": "00000000-0000-0000-0000-000000000000",
    "AZURE_CREDENTIALS_CLIENT_SECRET": "smoke",
    "AZURE_RESOURCE_GROUP": "smoke-rg",
    "OCI_PRIVATE_KEY_CONTENT": "smoke",
    "SLACK_BOT_TOKEN": "xoxb-smoke",
    "INFLUXDB_TOKEN": "smoke",
    "AWS_DEFAULT_REGION": "us-west-2",
}


@dataclass(frozen=True)
class Command:
    cloud: str
    kind: str
    argv: tuple[str, ...]


def _flag_value(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def load_commands(fixture: Path = FIXTURE) -> list[Command]:
    """One dry-run command per (cloud, kind), in fixture order."""
    seen: set[tuple[str, str]] = set()
    commands: list[Command] = []
    for raw in fixture.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        for placeholder, value in SUBSTITUTIONS.items():
            line = line.replace(placeholder, value)
        argv = shlex.split(line)[2:]
        if "--scan_tag" in argv:
            continue
        pair = (_flag_value(argv, "--cloud"), _flag_value(argv, "--resource"))
        if pair in seen:
            continue
        seen.add(pair)
        if "--dry_run" not in argv and "-d" not in argv:
            argv.append("--dry_run")
        commands.append(Command(pair[0], pair[1], tuple(argv)))
    return commands


def offline_env() -> dict[str, str]:
    """The subprocess environment: dummy credentials and the hook; nothing else from the caller."""
    env = {key: value for key, value in os.environ.items() if key in {"PATH", "HOME", "LANG"}}
    env.update(DUMMY_ENV)
    env["PYTHONPATH"] = str(HOOK_DIR)
    env["CRC_OFFLINE_SMOKE"] = "1"
    return env


def run_command(command: Command, workdir: Path) -> subprocess.CompletedProcess[str]:
    """Run `python crc.py <argv>` offline from `workdir` (the run log lands there)."""
    return subprocess.run(
        [sys.executable, str(ROOT / "crc.py"), *command.argv],
        cwd=workdir,
        env=offline_env(),
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        check=False,
    )


def problems_of(command: Command, result: subprocess.CompletedProcess[str]) -> list[str]:
    """What is wrong with one run; empty when it passed."""
    problems: list[str] = []
    if result.returncode != 0:
        problems.append(f"exit code {result.returncode}")
    output = result.stdout + result.stderr
    for marker in ("RUN START", "RESULT"):
        pattern = rf"^.*\b{marker} cloud={command.cloud} resource={command.kind} .*$"
        count = len(re.findall(pattern, output, flags=re.MULTILINE))
        if count != 1:
            problems.append(f"{count} `{marker}` lines (want 1)")
    return problems


def main() -> int:
    failed = 0
    commands = load_commands()
    with tempfile.TemporaryDirectory() as workdir:
        for command in commands:
            result = run_command(command, Path(workdir))
            problems = problems_of(command, result)
            status = "ok  " if not problems else "FAIL"
            print(f"{status} {command.cloud}/{command.kind} {'; '.join(problems)}")
            if problems:
                failed += 1
                print((result.stdout + result.stderr)[-2000:])
    print(f"{len(commands) - failed}/{len(commands)} pairs passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
