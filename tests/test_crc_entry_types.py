"""Type-check the top-level `crc.py` entry point.

`crc.py` cannot be listed in mypy's `files`: as a top-level module it is also named `crc` and
clashes with the `crc/` package. These tests type-check a copy named `crc_entry.py` (strict, the
project's mypy settings) with the repository root on `MYPYPATH`, so the real `crc` package
resolves and the entry point's imports and re-exports are checked like any other module.
"""

import os
from pathlib import Path

import pytest
from mypy import api

from tests.archtest._common import REPO_ROOT


def _type_check(source: str, workdir: Path, filename: str = "crc_entry.py") -> tuple[str, int]:
    entry = workdir / filename
    entry.write_text(source, encoding="utf-8")
    old = os.environ.get("MYPYPATH")
    os.environ["MYPYPATH"] = str(REPO_ROOT)
    try:
        stdout, _stderr, status = api.run(
            [
                "--config-file",
                str(REPO_ROOT / "pyproject.toml"),
                "--cache-dir",
                str(workdir / "mypy_cache"),
                str(entry),
            ]
        )
    finally:
        if old is None:
            del os.environ["MYPYPATH"]
        else:
            os.environ["MYPYPATH"] = old
    return stdout, status


@pytest.fixture(scope="module")
def workdir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("crc_entry_types")


def test_crc_py_passes_strict_mypy(workdir: Path) -> None:
    source = (REPO_ROOT / "crc.py").read_text(encoding="utf-8")
    stdout, status = _type_check(source, workdir, "crc_entry.py")
    assert status == 0, stdout


def test_crc_py_type_check_fires_on_a_type_error(workdir: Path) -> None:
    source = (REPO_ROOT / "crc.py").read_text(encoding="utf-8")
    stdout, status = _type_check(
        source + "\nBROKEN: int = 'text'\n", workdir, "crc_entry_broken.py"
    )
    assert status == 1
    assert "BROKEN" in stdout or "crc_entry_broken.py" in stdout
