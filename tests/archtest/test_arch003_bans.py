"""ARCH-003 check and self-tests."""

from pathlib import Path

import pytest

from tests.archtest._common import REPO_ROOT, load_legacy
from tests.archtest.bans import (
    ARCH003_ALLOWLIST,
    PACKAGE_BANS,
    check_bans,
)

BAD_SNIPPETS = {
    "re": "import re\n",
    "re-from": "from re import compile\n",
    "clock": "import datetime\n\nx = datetime.datetime.now()\n",
    "clock-utcnow": "from datetime import datetime\n\nx = datetime.utcnow()\n",
    "clock-time": "import time\n\nx = time.time()\n",
    "env": "import os\n\nx = os.environ['A']\n",
    "env-getenv": "import os\n\nx = os.getenv('A')\n",
    "print": "print('hi')\n",
    "sys.exit": "import sys\n\nsys.exit(1)\n",
}
GOOD_SNIPPET = (
    "import time\n\n\ndef wait() -> float:\n    time.sleep(1)\n    return time.monotonic()\n"
)


def _write(root: Path, source: str, name: str = "crc/aws/new_mod.py") -> None:
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")


def test_arch003_cloud_modules_have_no_banned_constructs() -> None:
    problems = check_bans(REPO_ROOT, load_legacy(REPO_ROOT))
    assert not problems, "\n\n".join(problems)


# The owner-approved exceptions, pinned: any other entry fails here, so an agent cannot widen
# the allowlist without also editing this owner-only file.
APPROVED_ALLOWLIST = {
    "crc/azu/_base.py": frozenset({"env"}),
    "crc/oci/_base.py": frozenset({"env"}),
}


def test_arch003_allowlist_is_explicit_and_owner_approved() -> None:
    assert isinstance(ARCH003_ALLOWLIST, dict)
    assert dict(ARCH003_ALLOWLIST) == APPROVED_ALLOWLIST


@pytest.mark.parametrize("snippet", list(BAD_SNIPPETS.values()), ids=list(BAD_SNIPPETS))
def test_arch003_selftest_fires_on_bad_snippet(tmp_path: Path, snippet: str) -> None:
    _write(tmp_path, snippet)
    messages = check_bans(tmp_path, frozenset())
    assert len(messages) == 1
    assert messages[0].startswith("ARCH-003: crc/aws/new_mod.py:")
    assert "\n\nREMEDIATION: " in messages[0]


def test_arch003_selftest_allows_polling_helpers(tmp_path: Path) -> None:
    _write(tmp_path, GOOD_SNIPPET)
    assert check_bans(tmp_path, frozenset()) == []


@pytest.mark.parametrize("package", ["crc/azu", "crc/gcp", "crc/oci"])
def test_arch003_selftest_covers_every_cloud_package(tmp_path: Path, package: str) -> None:
    _write(tmp_path, "print('x')\n", f"{package}/mod.py")
    assert len(check_bans(tmp_path, frozenset())) == 1


def test_arch003_selftest_skips_legacy_and_other_packages(tmp_path: Path) -> None:
    _write(tmp_path, "print('x')\n")
    _write(tmp_path, "print('x')\n", "crc/service.py")
    _write(tmp_path, "print('x')\n", "crc/other/mod.py")
    assert check_bans(tmp_path, frozenset({"crc/aws/new_mod.py"})) == []


def test_arch003_selftest_allowlist_suppresses_only_listed_kind(tmp_path: Path) -> None:
    _write(tmp_path, "import re\n\nprint('x')\n")
    allow = {"crc/aws/new_mod.py": frozenset({"re"})}
    messages = check_bans(tmp_path, frozenset(), allow)
    assert len(messages) == 1
    assert "`print`" in messages[0]


CORE_BAD = {
    "clock": "import time\n\nx = time.time()\n",
    "print": "print('x')\n",
    "sys.exit": "import sys\n\nsys.exit(1)\n",
}


@pytest.mark.parametrize("snippet", list(CORE_BAD.values()), ids=list(CORE_BAD))
def test_arch003_selftest_core_is_pure(tmp_path: Path, snippet: str) -> None:
    _write(tmp_path, snippet, "crc/core/new_mod.py")
    messages = check_bans(tmp_path, frozenset())
    assert len(messages) == 1
    assert messages[0].startswith("ARCH-003: crc/core/new_mod.py:")


def test_arch003_selftest_core_may_use_re(tmp_path: Path) -> None:
    _write(tmp_path, "import re\n\nX = re.compile('a')\n", "crc/core/new_mod.py")
    assert check_bans(tmp_path, frozenset()) == []


def test_arch003_core_bans_env_and_clock_values() -> None:
    assert {"env", "clock", "clock_ref"} <= PACKAGE_BANS["crc/core"]
    assert "clock_ref" in PACKAGE_BANS["crc/cli"]


CORE_ENV_BAD = {
    "environ": "import os\n\nx = os.environ['A']\n",
    "environ-get": "import os\n\nx = os.environ.get('A')\n",
    "getenv": "import os\n\nx = os.getenv('A')\n",
    "from-environ": "from os import environ\n",
    "from-getenv": "from os import getenv\n",
}


@pytest.mark.parametrize("snippet", list(CORE_ENV_BAD.values()), ids=list(CORE_ENV_BAD))
def test_arch003_selftest_core_reads_no_environment(tmp_path: Path, snippet: str) -> None:
    _write(tmp_path, snippet, "crc/core/new_mod.py")
    messages = check_bans(tmp_path, frozenset())
    assert len(messages) == 1
    assert "`env`" in messages[0]


def test_arch003_selftest_cli_may_read_environment(tmp_path: Path) -> None:
    _write(tmp_path, "import os\n\nx = os.environ.get('A')\n", "crc/cli/new_mod.py")
    assert check_bans(tmp_path, frozenset()) == []


CLOCK_REF_BAD = {
    "default-arg": "import datetime\n\n\ndef f(now=datetime.datetime.now):\n    return now\n",
    "from-import-attr": "from datetime import datetime\n\nF = datetime.utcnow\n",
    "dataclass-factory": (
        "import datetime\nfrom dataclasses import dataclass, field\n\n\n"
        "@dataclass\nclass A:\n    t: object = field(default_factory=datetime.datetime.utcnow)\n"
    ),
    "time-value": "import time\n\nCLOCK = time.time\n",
    "call-argument": "import datetime\n\nx = sorted([], key=datetime.datetime.now)\n",
    "module-alias": "import datetime as dt\n\n\ndef f(now=dt.datetime.now):\n    return now\n",
    "class-alias": "from datetime import datetime as DT\n\nF = DT.utcnow\n",
}

CLI_CLOCK_REF_BAD = {
    "from-time-name": "from time import time\n\n\ndef f(now=time):\n    return now\n",
    "from-time-alias": "from time import time as wall\n\nCLOCK = wall\n",
}


@pytest.mark.parametrize("snippet", list(CLI_CLOCK_REF_BAD.values()), ids=list(CLI_CLOCK_REF_BAD))
def test_arch003_selftest_cli_clock_name_passed_as_value(tmp_path: Path, snippet: str) -> None:
    _write(tmp_path, snippet, "crc/cli/new_mod.py")
    messages = check_bans(tmp_path, frozenset())
    assert len(messages) == 1
    assert "`clock_ref`" in messages[0]


def test_arch003_selftest_cli_may_call_an_imported_clock_name(tmp_path: Path) -> None:
    source = "from time import time\n\nx = time()\ntime_s = 1\n"
    _write(tmp_path, source, "crc/cli/m.py")
    assert check_bans(tmp_path, frozenset()) == []


@pytest.mark.parametrize("package", ["crc/core", "crc/cli"])
@pytest.mark.parametrize("snippet", list(CLOCK_REF_BAD.values()), ids=list(CLOCK_REF_BAD))
def test_arch003_selftest_clock_passed_as_value(tmp_path: Path, package: str, snippet: str) -> None:
    _write(tmp_path, snippet, f"{package}/new_mod.py")
    messages = check_bans(tmp_path, frozenset())
    assert len(messages) == 1
    assert "`clock_ref`" in messages[0]


def test_arch003_selftest_clock_ref_is_not_reported_twice_for_a_call(tmp_path: Path) -> None:
    _write(tmp_path, "import datetime\n\nx = datetime.datetime.now()\n", "crc/core/new_mod.py")
    messages = check_bans(tmp_path, frozenset())
    assert len(messages) == 1
    assert "`clock`" in messages[0]


def test_arch003_selftest_cli_may_call_the_clock(tmp_path: Path) -> None:
    source = "from datetime import UTC, datetime\n\nx = datetime.now(UTC)\n"
    _write(tmp_path, source, "crc/cli/m.py")
    assert check_bans(tmp_path, frozenset()) == []


def test_arch003_cloud_packages_ban_the_clock_as_a_value() -> None:
    for package in ("crc/aws", "crc/azu", "crc/gcp", "crc/oci"):
        assert {"clock", "clock_ref"} <= PACKAGE_BANS[package]


@pytest.mark.parametrize("package", ["aws", "azu", "gcp", "oci"])
@pytest.mark.parametrize("snippet", list(CLOCK_REF_BAD.values()), ids=list(CLOCK_REF_BAD))
def test_arch003_selftest_cloud_package_fails_on_a_clock_value(
    tmp_path: Path, package: str, snippet: str
) -> None:
    _write(tmp_path, snippet, f"crc/{package}/new_mod.py")
    messages = check_bans(tmp_path, frozenset(), {})
    assert messages
    assert all(m.startswith(f"ARCH-003: crc/{package}/new_mod.py:") for m in messages)
    assert any("`clock_ref`" in m for m in messages)


def test_arch003_selftest_cloud_package_may_take_a_clock_parameter(tmp_path: Path) -> None:
    source = (
        "import datetime\nfrom collections.abc import Callable\n\n"
        "Clock = Callable[[], datetime.datetime]\n\n\n"
        "def age(clock: Clock) -> datetime.datetime:\n    return clock()\n"
    )
    _write(tmp_path, source, "crc/gcp/new_mod.py")
    assert check_bans(tmp_path, frozenset(), {}) == []


@pytest.mark.parametrize("package", ["crc/cli", "crc/notify"])
def test_arch003_selftest_boundary_packages_ban_re(tmp_path: Path, package: str) -> None:
    _write(tmp_path, "import re\n", f"{package}/new_mod.py")
    assert len(check_bans(tmp_path, frozenset())) == 1


@pytest.mark.parametrize(
    "snippet", ["import os\n\nx = os.getenv('A')\n", "import sys\n\nsys.exit(1)\n"]
)
def test_arch003_selftest_notify_gets_config_injected(tmp_path: Path, snippet: str) -> None:
    _write(tmp_path, snippet, "crc/notify/new_mod.py")
    assert len(check_bans(tmp_path, frozenset())) == 1


def test_arch003_selftest_boundary_keeps_its_legitimate_constructs(tmp_path: Path) -> None:
    source = (
        "import os\nimport sys\nimport time\n\n"
        "x = os.environ['A']\nprint(time.time())\nsys.exit(1)\n"
    )
    _write(tmp_path, source, "crc/cli/new_mod.py")
    _write(tmp_path, "import time\n\nprint(time.time())\n", "crc/notify/new_mod.py")
    assert check_bans(tmp_path, frozenset()) == []
