"""Route tables: how the CLI runs each (cloud, kind) pair through `crc.core.engine`.

One module per cloud (`aws`, `azu`, `gcp`, `oci`) holds `ROUTES`, a dict from resource kind to a
`Route`. Every supported pair and the two warn-only spot pairs are in their cloud's table.
"""

import enum
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from crc.cli.inputs import Options, Targets
from crc.core.engine import Notifier, RunOutcome

__all__ = [
    "AfterRun",
    "ExtraNotifiers",
    "NotifyMode",
    "OptionsBuilder",
    "Precheck",
    "Route",
    "Skip",
]

OptionsBuilder = Callable[[Options], dict[str, Any]]
Precheck = Callable[[Any, Options, Targets], None]
ExtraNotifiers = Callable[[Any], Sequence[Notifier]]
AfterRun = Callable[[Any, Options, Sequence[RunOutcome]], None]
Skip = Callable[[str, Options, Targets], bool]


class NotifyMode(enum.Enum):
    """What a routed pair does with Slack and Influx.

    `NONE` sends nothing (`snapshot`, BUG-009). `REPORT` sends after a
    successful run and sends nothing when the cleaner raises. `REPORT_THEN_RERAISE` also sends
    when `plan()` or `apply()` raises (what was done so far), then raises the same error
    (AWS `disk`, as `delete_disks` did).
    """

    NONE = "none"
    REPORT = "report"
    REPORT_THEN_RERAISE = "report_then_reraise"


@dataclass(frozen=True, slots=True)
class Route:
    """How one engine-routed kind is run.

    `options` maps the parsed flags to the `RunContext.options` of the registered cleaner (raw
    flag values, as the legacy handler passed them). `notify` is the `NotifyMode`. `precheck`
    runs between RUN START and the cleaner, for gates that the legacy handler raised itself (the
    AWS disk opt-in). `extra_notifiers` builds notifiers that run after Slack and Influx (a
    message the report does not carry); `after` runs after the notifiers with the outcomes (the
    console line of `snapshot`). `skip` runs first, between RUN START and the precheck: it returns
    True after logging its warning when the pair must not run (SAF-037: GCP and OCI
    `spot_instances` under `--cloud all`; the kinds a cloud does not have), and then nothing is
    called, nothing is reported and no cleaner is built.
    """

    options: OptionsBuilder
    notify: NotifyMode = NotifyMode.REPORT
    precheck: Precheck | None = None
    extra_notifiers: ExtraNotifiers | None = None
    after: AfterRun | None = None
    skip: Skip | None = None
