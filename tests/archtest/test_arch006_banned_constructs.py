"""ARCH-006 check and self-tests."""

from pathlib import Path

import pytest

from tests.archtest._common import REPO_ROOT
from tests.archtest.banned_constructs import check_banned_constructs

BAD_SNIPPETS = {
    "async-def": "async def f() -> None:\n    return None\n",
    "await": "async def f(g):\n    await g()\n",
    "async-for": "async def f(g):\n    async for _ in g:\n        pass\n",
    "async-with": "async def f(g):\n    async with g:\n        pass\n",
    "asyncio": "import asyncio\n",
    "asyncio-from": "from asyncio import sleep\n",
    "pydantic": "from pydantic import BaseModel\n",
    "pydantic-submodule": "import pydantic.v1\n",
    "httpx": "import httpx\n",
    "tenacity": "from tenacity import retry\n",
    "threading": "import threading\n\nLOCK = threading.Lock()\n",
    "threading-from": "from threading import local\n",
}
GOOD_SNIPPET = (
    "import time\nfrom dataclasses import dataclass\n\n\n@dataclass\nclass C:\n"
    "    x: int\n\n    def wait(self) -> None:\n        time.sleep(self.x)\n"
)


def _write(root: Path, source: str, name: str = "crc/aws/new_mod.py") -> None:
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")


def test_arch006_crc_has_no_banned_constructs() -> None:
    problems = check_banned_constructs(REPO_ROOT)
    assert not problems, "\n\n".join(problems)


@pytest.mark.parametrize("snippet", list(BAD_SNIPPETS.values()), ids=list(BAD_SNIPPETS))
def test_arch006_selftest_fires_on_bad_snippet(tmp_path: Path, snippet: str) -> None:
    _write(tmp_path, snippet)
    messages = check_banned_constructs(tmp_path)
    assert messages
    assert all(m.startswith("ARCH-006: crc/aws/new_mod.py:") for m in messages)
    assert "\n\nREMEDIATION: " in messages[0]


def test_arch006_selftest_fires_in_top_level_crc_py(tmp_path: Path) -> None:
    _write(tmp_path, "import asyncio\n", "crc.py")
    assert len(check_banned_constructs(tmp_path)) == 1


def test_arch006_selftest_fires_in_legacy_files_too(tmp_path: Path) -> None:
    _write(tmp_path, "import httpx\n", "crc/cli/legacy_mod.py")
    assert len(check_banned_constructs(tmp_path)) == 1


def test_arch006_selftest_allows_plain_synchronous_code(tmp_path: Path) -> None:
    _write(tmp_path, GOOD_SNIPPET)
    assert check_banned_constructs(tmp_path) == []


def test_arch006_selftest_ignores_tests_directory(tmp_path: Path) -> None:
    _write(tmp_path, "import asyncio\n", "tests/test_x.py")
    assert check_banned_constructs(tmp_path) == []
