"""Offline smoke: `python crc.py --dry_run` per (cloud, kind) of the production Jenkins commands.

Every SDK client is an in-memory fake and sockets are denied (`scripts/offline_hook`). The hook
is reachable only through the smoke runner's `PYTHONPATH` and `CRC_OFFLINE_SMOKE=1`.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from scripts import smoke_offline
from scripts.smoke_offline import HOOK_DIR, ROOT, Command, load_commands, offline_env

COMMANDS = load_commands()


def test_smoke_covers_every_cloud_kind_pair_of_the_fixture() -> None:
    pairs = [(c.cloud, c.kind) for c in COMMANDS]
    assert len(pairs) == len(set(pairs))
    fixture = smoke_offline.FIXTURE.read_text(encoding="utf-8")
    expected = {
        (parts[parts.index("--cloud") + 1], parts[parts.index("--resource") + 1])
        for parts in (line.split() for line in fixture.splitlines() if "--scan_tag" not in line)
        if parts and parts[0] == "python3" and "--resource" in parts
    }
    assert set(pairs) == expected
    assert {c for c, _ in pairs} == {"aws", "azure", "gcp", "oci"}


def test_smoke_commands_are_all_dry_runs() -> None:
    assert all("--dry_run" in c.argv or "-d" in c.argv for c in COMMANDS)
    assert all("$" not in " ".join(c.argv) for c in COMMANDS)


@pytest.mark.parametrize("command", COMMANDS, ids=[f"{c.cloud}-{c.kind}" for c in COMMANDS])
def test_smoke_offline_pair(command: Command, tmp_path: Path) -> None:
    result = smoke_offline.run_command(command, tmp_path)
    assert smoke_offline.problems_of(command, result) == [], result.stdout + result.stderr


def _python(code: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=False
    )


def test_hook_does_nothing_without_the_switch() -> None:
    env = offline_env()
    del env["CRC_OFFLINE_SMOKE"]
    code = "import boto3, socket; print(boto3.client.__name__, socket.create_connection.__name__)"
    result = _python(code, env)
    assert result.stdout.split() == ["client", "create_connection"], result.stderr


def test_hook_fakes_clients_and_denies_sockets() -> None:
    code = (
        "import boto3, socket\n"
        "assert not list(boto3.client('ec2').describe_regions()['Regions'])\n"
        "calls = [\n"
        "    lambda: socket.create_connection(('127.0.0.1', 9)),\n"
        "    lambda: socket.socket().connect(('127.0.0.1', 9)),\n"
        "    lambda: socket.getaddrinfo('127.0.0.1', 80),\n"
        "]\n"
        "for fn in calls:\n"
        "    try:\n"
        "        fn()\n"
        "        raise AssertionError('not denied')\n"
        "    except OSError as e:\n"
        "        assert 'denied by the offline smoke hook' in str(e), e\n"
        "print('all-denied')\n"
    )
    result = _python(code, offline_env())
    assert result.returncode == 0 and result.stdout.strip() == "all-denied", result.stderr


def test_production_code_never_references_the_hook() -> None:
    for path in [*sorted((ROOT / "crc").rglob("*.py")), ROOT / "crc.py"]:
        text = path.read_text(encoding="utf-8")
        assert "CRC_OFFLINE" not in text, path
        assert "offline_hook" not in text, path
        assert "sitecustomize" not in text, path
    assert (HOOK_DIR / "sitecustomize.py").is_file()
