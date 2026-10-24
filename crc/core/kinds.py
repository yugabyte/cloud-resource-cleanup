"""The one table of resource kinds: every consumer derives its list from `KINDS`.

A kind is declared once here (name, the clouds it is limited to, whether `--resource all`
includes it, its report label and the message of an unsupported pair). The CLI parser choices,
`RESOURCES` and `EXTRA_RESOURCES` of `crc.core.selection`, the pair rules of `crc/cli/pairs.py`
and `KIND_LABELS` of the engine are computed from it. The table order is the order of the
`--resource` choices; the kinds with `in_all` keep their relative order as the `--resource all`
expansion. Pure data: nothing here reads the environment or logs.
"""

from dataclasses import dataclass

from crc.core.report import DISK, IP, KEYPAIR, KMS, NIC, SNAPSHOT, SPOT_INSTANCE_REQUEST, VM

AWS_ONLY_MESSAGE = "--resource {kind} is AWS-only; use --cloud aws (got --cloud {cloud})"


@dataclass(frozen=True, slots=True)
class Kind:
    """One resource kind.

    `clouds` is None when every cloud has a route for the kind (a warn-only skip route counts),
    else the clouds that support it; any other cloud is an unsupported pair and `unsupported`
    is the message template for it (`{kind}`, `{cloud}` and `{clouds}`, the list of all clouds,
    are filled in). `in_all` is True when `--resource all` expands to the kind; a kind that is
    not in it is explicit-only. `label` is the report label the notifiers receive.
    """

    name: str
    label: str
    in_all: bool = True
    clouds: tuple[str, ...] | None = None
    unsupported: str | None = None

    def __post_init__(self) -> None:
        if (self.clouds is None) != (self.unsupported is None):
            raise ValueError(f"kind {self.name!r}: clouds and unsupported are set together")

    @property
    def explicit_only(self) -> bool:
        """True when only `--resource <name>` selects the kind, never `--resource all`."""
        return not self.in_all

    def unsupported_message(self, cloud: object, all_clouds: object) -> str:
        """The error text for `cloud`, which does not support this kind."""
        if self.unsupported is None:
            raise ValueError(f"kind {self.name!r} is supported on every cloud")
        return self.unsupported.format(kind=self.name, cloud=cloud, clouds=all_clouds)


KINDS: tuple[Kind, ...] = (
    Kind("disk", DISK),
    Kind(
        "ip",
        IP,
        clouds=("aws", "azure", "gcp"),
        unsupported="Invalid cloud provided: {cloud}. Supported clouds are {clouds}",
    ),
    Kind(
        "keypair",
        KEYPAIR,
        clouds=("aws",),
        unsupported="Keypair operation is only supported on AWS.",
    ),
    Kind("vm", VM),
    Kind(
        "kms",
        KMS,
        clouds=("aws",),
        unsupported="KMS operation is only supported on AWS.",
    ),
    Kind(
        "nic",
        NIC,
        clouds=("azure",),
        unsupported="NICs operation is only supported on Azure.",
    ),
    Kind("spot_instance_requests", SPOT_INSTANCE_REQUEST, in_all=False),
    Kind("spot_instances", VM, in_all=False),
    Kind("snapshot", SNAPSHOT, clouds=("aws",), unsupported=AWS_ONLY_MESSAGE),
)

KIND_NAMES: tuple[str, ...] = tuple(kind.name for kind in KINDS)
ALL_KINDS: tuple[str, ...] = tuple(kind.name for kind in KINDS if kind.in_all)
EXPLICIT_KINDS: tuple[str, ...] = tuple(kind.name for kind in KINDS if kind.explicit_only)
KINDS_BY_NAME: dict[str, Kind] = {kind.name: kind for kind in KINDS}
