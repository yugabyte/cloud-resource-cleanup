"""Environment check: the Python version and the pinned dev tools of this checkout.

Usage: python scripts/doctor.py

Checks that the interpreter meets `requires-python` of pyproject.toml and that ruff, mypy and
pytest are installed at the exact versions of constraints.txt. When something is missing or at
another version, prints the one command that fixes it and exits 1.
"""

import sys
import tomllib
from collections.abc import Callable
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ("ruff", "mypy", "pytest")
INSTALL_ARGS = "install -r requirements.txt -r requirements-dev.txt -c constraints.txt"
VENV_PYTHON = ".venv/bin/python"


def pinned_versions(constraints: Path = ROOT / "constraints.txt") -> dict[str, str]:
    """Package name (lowercase) -> exact version of every `name==version` line."""
    pins: dict[str, str] = {}
    for raw in constraints.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "==" in line:
            name, _, version = line.partition("==")
            pins[name.strip().lower()] = version.strip()
    return pins


def minimum_python(pyproject: Path = ROOT / "pyproject.toml") -> tuple[int, int]:
    """The `(major, minor)` of `requires-python = ">=X.Y"`."""
    with pyproject.open("rb") as handle:
        spec = str(tomllib.load(handle)["project"]["requires-python"])
    major, _, minor = spec.removeprefix(">=").partition(".")
    return int(major), int(minor)


def installed_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def problems_of(
    pins: dict[str, str],
    minimum: tuple[int, int],
    version_info: tuple[int, int] = (sys.version_info.major, sys.version_info.minor),
    lookup: Callable[[str], str | None] = installed_version,
) -> list[str]:
    """What is wrong with this environment (empty when it is ready for `make check`)."""
    found: list[str] = []
    if version_info < minimum:
        found.append(
            f"Python {version_info[0]}.{version_info[1]} is too old: "
            f"pyproject.toml requires >= {minimum[0]}.{minimum[1]}"
        )
    for tool in TOOLS:
        wanted = pins.get(tool)
        have = lookup(tool)
        if wanted is None:
            found.append(f"{tool} has no pin in constraints.txt")
        elif have is None:
            found.append(f"{tool} is not installed (constraints.txt pins {wanted})")
        elif have != wanted:
            found.append(f"{tool} is {have}, constraints.txt pins {wanted}")
    return found


def install_command(executable: str = sys.executable, in_venv: bool | None = None) -> str:
    """The exact command that installs the pinned environment for this interpreter."""
    if in_venv is None:
        in_venv = sys.prefix != sys.base_prefix
    install = f"{executable} -m pip {INSTALL_ARGS}"
    if in_venv:
        return install
    return f"python3 -m venv .venv && {VENV_PYTHON} -m pip {INSTALL_ARGS}"


def main() -> int:
    problems = problems_of(pinned_versions(), minimum_python())
    if not problems:
        print(
            f"doctor: ok (python {sys.version.split()[0]}, {', '.join(TOOLS)} at pinned versions)"
        )
        return 0
    for problem in problems:
        print(f"doctor: {problem}")
    print(f"doctor: fix with: {install_command()}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
