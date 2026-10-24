"""Selection model of the engine: the `(cloud, kind)` pairs a run executes and the CLI order.

Pure and typed. `Selection` is the ordered pairs the engine runs; `crc/cli/bridge.py` builds a
one-pair `Selection` per run. `CLOUDS`, `RESOURCES` and `EXTRA_RESOURCES` record the `--cloud all`
and `--resource all` expansion order, derived from the kind table `crc/core/kinds.py`; the pair
and opt-in checks live in `crc/cli/inputs.py` and `crc/cli/dispatch.py`.
Nothing here reads the environment or logs.
"""

from dataclasses import dataclass

from crc.core.kinds import ALL_KINDS, EXPLICIT_KINDS

CLOUDS: tuple[str, ...] = ("aws", "azure", "gcp", "oci")
RESOURCES: tuple[str, ...] = ALL_KINDS
EXTRA_RESOURCES: tuple[str, ...] = EXPLICIT_KINDS


@dataclass(frozen=True, slots=True)
class Selection:
    """Ordered `(cloud, kind)` pairs to run and the warnings of the pairs that were skipped."""

    items: tuple[tuple[str, str], ...]
    warnings: tuple[str, ...] = ()
