"""ARCH-003 check and self-tests."""

from pathlib import Path

import pytest

from tests.archtest._common import REPO_ROOT, load_legacy
from tests.archtest.bans import ARCH003_ALLOWLIST, check_bans

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
    _write(tmp_path, "print('x')\n", "crc/core/mod.py")
    assert check_bans(tmp_path, frozenset({"crc/aws/new_mod.py"})) == []


def test_arch003_selftest_allowlist_suppresses_only_listed_kind(tmp_path: Path) -> None:
    _write(tmp_path, "import re\n\nprint('x')\n")
    allow = {"crc/aws/new_mod.py": frozenset({"re"})}
    messages = check_bans(tmp_path, frozenset(), allow)
    assert len(messages) == 1
    assert "`print`" in messages[0]
