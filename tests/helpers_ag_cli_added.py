"""The one ADDED table of the CLI compatibility contract (additive flags and resource kinds).

Every flag, choice, default, type, dest and value form of the frozen oracle
`tests/legacy/crc_main.txt` must still exist and behave the same; the CLI parity tests keep
comparing all of them exactly. Anything the oracle does not have is allowed only when it is
listed here. An unlisted addition fails the parity tests with a message that points to this file.

To add an optional flag, a `--resource` kind or another choice: change `crc/**` as the package
READMEs say, then add one entry below (and nothing in the oracle):

* a new flag: `AddedFlag(option, dest, default, help, short=..., examples=...)`;
* a new `--resource` kind: `AddedResource(kind, clouds, in_all)` plus the full new help text of
  `--resource` in `RESOURCE_HELP`; `in_all=True` when `--resource all` expands to it;
* a new choice of any other flag: `AddedChoice(option, choice, help)` with the new help text
  (`help=None` keeps the oracle help).

To remove a `--resource` kind (an owner decision, never silently): delete it from `crc/**`, then
add `RemovedResource(kind, reason)` to `REMOVED_RESOURCES`. The oracle's choice may then be absent
and `--resource <kind>` exits 2 with argparse's invalid choice error; a removal that is not listed
fails the parity tests.

The Jenkins contract (`tests/test_jenkins_contract.py`) is separate and never relaxed here.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AddedFlag:
    option: str
    dest: str
    default: Any
    help: str
    short: str | None = None
    examples: tuple[tuple[tuple[str, ...], Any], ...] = ()


@dataclass(frozen=True)
class AddedChoice:
    option: str
    choice: str
    help: str | None = None


@dataclass(frozen=True)
class AddedResource:
    kind: str
    clouds: tuple[str, ...]
    in_all: bool = False


@dataclass(frozen=True)
class RemovedResource:
    kind: str
    reason: str


ADDED_FLAGS: tuple[AddedFlag, ...] = ()
ADDED_CHOICES: tuple[AddedChoice, ...] = ()
ADDED_RESOURCES: tuple[AddedResource, ...] = ()
RESOURCE_HELP: str | None = None  # None: --resource help unchanged
REMOVED_RESOURCES: tuple[RemovedResource, ...] = (
    RemovedResource("vpc", "BUG-083: --resource vpc removed (owner decision)"),
)
REMOVED_HINT = "list it in REMOVED_RESOURCES in tests/helpers_ag_cli_added.py (owner decision only)"
ADDED_HINT = "add it to the ADDED table in tests/helpers_ag_cli_added.py (with default and help)"


def removed_kinds() -> set[str]:
    return {r.kind for r in REMOVED_RESOURCES}


def added_routed_pairs() -> set[tuple[str, str]]:
    return {(cloud, r.kind) for r in ADDED_RESOURCES for cloud in r.clouds}


def added_kinds() -> set[str]:
    return {r.kind for r in ADDED_RESOURCES}
