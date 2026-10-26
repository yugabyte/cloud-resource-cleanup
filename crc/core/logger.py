"""Per-run log collector: RUN START header, REGION and ACTION lines, SKIPPED/ERRORS/RESULT footer.

Log text and levels only (the contract is in ``docs/logging.md``). Nothing here
decides what is selected, deleted or stopped; cleaners report what they did. Pure stdlib.
"""

import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

ACTION_KINDS = ("would_delete", "deleted", "would_stop", "stopped")
_DONE_KINDS = frozenset({"deleted", "stopped"})
_OPTION_KEYS = (
    "age",
    "filter_tags",
    "exception_tags",
    "notags",
    "name_regex",
    "exception_regex",
    "custom_age_tag_key",
    "resource_group",
    "project_id",
)

Clock = Callable[[], float]

# Never call logging.log/info/...: on a root logger without handlers they run basicConfig(),
# which adds a stderr handler that later prints every record a second time.
_LOG = logging.getLogger("crc.run")


@dataclass(frozen=True, slots=True)
class _Failure:
    region: str | None
    resource_id: str
    error: BaseException | str
    logged: bool


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _flag(value: bool) -> str:
    return "true" if value else "false"


def _one_line(text: str) -> str:
    parts = (part.strip() for part in text.splitlines())
    return " | ".join(part for part in parts if part)


def _error_text(error: BaseException | str) -> str:
    if isinstance(error, BaseException):
        return f"{type(error).__name__}: {_one_line(str(error))}"
    return _one_line(str(error))


class RunLog:
    """Counters and log lines of one cleaner run.

    A run created by ``begin`` is attached: it logs. The fresh run returned by ``current``
    when no run was begun is unattached: it only counts and logs nothing.
    """

    def __init__(
        self,
        cloud: str = "",
        resource: str = "",
        operation: str = "",
        dry_run: bool = False,
        *,
        attached: bool = False,
        clock: Clock = time.monotonic,
    ) -> None:
        self.cloud = cloud
        self.resource = resource
        self.operation = operation
        self.dry_run = dry_run
        self.attached = attached
        self._clock = clock
        self._started = clock()
        self._selected = 0
        self._done = 0
        self._regions = 0
        self._regions_skipped = 0
        self._skips: dict[str, int] = {}
        self._failures: list[_Failure] = []
        self._header: str | None = None

    def hold_header(self, line: str) -> None:
        """Keep the RUN START line until the root logger has handlers, then emit it once."""
        self._header = line
        self._emit_header(force=False)

    def release_header(self) -> None:
        """Emit the held RUN START line now (logging has just been initialised)."""
        self._emit_header(force=True)

    def _emit_header(self, force: bool) -> None:
        if self.attached and self._header is not None and (force or logging.getLogger().handlers):
            line, self._header = self._header, None
            _LOG.log(logging.INFO, line)

    def _log(self, level: int, message: str) -> None:
        if self.attached:
            self._emit_header(force=False)
            _LOG.log(level, message)

    def region(self, region: str, scanned: int, selected: int, seconds: float) -> None:
        """Count a walked region; log its line at INFO, or DEBUG when nothing was selected."""
        self._regions += 1
        self._log(
            logging.INFO if selected else logging.DEBUG,
            f"REGION cloud={self.cloud} resource={self.resource} region={region} "
            f"scanned={scanned} selected={selected} seconds={int(seconds)}",
        )

    def region_skipped(self, region: str, error: BaseException | str) -> None:
        """Count a region skipped for a connectivity error (its own warning stays)."""
        self._regions_skipped += 1

    def action(
        self, kind: str, region: str | None, resource_id: str, name: str | None = None
    ) -> None:
        """Count and log one resource acted on, or that would be in a dry run.

        An unknown kind is logged as a warning and the resource is still counted:
        logging must never stop a cleanup halfway.
        """
        if kind not in ACTION_KINDS:
            self._log(logging.WARNING, f"unknown action kind {kind!r}; use one of {ACTION_KINDS}")
        self._selected += 1
        if kind in _DONE_KINDS:
            self._done += 1
        self._log(
            logging.INFO,
            f"ACTION {kind} cloud={self.cloud} resource={self.resource} "
            f"region={region or '-'} id={resource_id} name={name or '-'}",
        )

    def skip(self, reason: str) -> None:
        """Count a skipped resource under a short snake_case reason; logs nothing."""
        self._skips[reason] = self._skips.get(reason, 0) + 1

    def fail(
        self,
        region: str | None,
        resource_id: str,
        error: BaseException | str,
        *,
        logged: bool = False,
    ) -> None:
        """Record a failure: counted in ERRORS and RESULT, and listed as a FAILED line at the
        end unless `logged` (the caller already logged an error line naming the resource)."""
        self._failures.append(_Failure(region, resource_id, error, logged))

    def finish(self) -> None:
        """Log SKIPPED lines, ERRORS and the FAILED lines not already logged, and RESULT."""
        self._emit_header(force=True)
        where = f"cloud={self.cloud} resource={self.resource}"
        for reason in sorted(self._skips):
            self._log(logging.INFO, f"SKIPPED {where} reason={reason} count={self._skips[reason]}")
        if self._failures:
            self._log(logging.ERROR, f"ERRORS count={len(self._failures)}")
            for failure in self._failures:
                if failure.logged:
                    continue
                self._log(
                    logging.ERROR,
                    f"FAILED {where} region={failure.region or '-'} id={failure.resource_id} "
                    f"error={_error_text(failure.error)}",
                )
        self._log(
            logging.INFO,
            f"RESULT {where} operation={self.operation} dry_run={_flag(self.dry_run)} "
            f"selected={self._selected} done={self._done} failed={len(self._failures)} "
            f"skipped={sum(self._skips.values())} regions={self._regions} "
            f"regions_skipped={self._regions_skipped} "
            f"duration_s={int(self._clock() - self._started)}",
        )


_current: RunLog | None = None


def begin(
    cloud: str,
    resource: str,
    operation: str,
    dry_run: bool,
    options: Mapping[str, Any],
    *,
    clock: Clock = time.monotonic,
) -> RunLog:
    """Start a run, make it the current run and log the RUN START line."""
    global _current
    run = RunLog(cloud, resource, operation, dry_run, attached=True, clock=clock)
    _current = run
    parts = [
        f"RUN START cloud={cloud} resource={resource} operation={operation}",
        f"dry_run={_flag(dry_run)}",
    ]
    parts.extend(f"{key}={_json(options[key])}" for key in _OPTION_KEYS if options.get(key))
    run.hold_header(" ".join(parts))
    return run


def logging_ready() -> None:
    """Emit the held RUN START line of the current run, if any. Call once handlers exist.

    A no-op when no run began or its header was already emitted, so calling it twice is safe.
    """
    if _current is not None:
        _current.release_header()


def current() -> RunLog:
    """Return the current run, or a fresh unattached run that logs nothing."""
    return _current if _current is not None else RunLog()


def finish() -> None:
    """End the current run: log its footer and clear it. No-op when no run began."""
    global _current
    run, _current = _current, None
    if run is not None:
        run.finish()
