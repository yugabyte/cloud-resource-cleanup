# Copyright (c) Yugabyte, Inc.

"""Azure NIC cleaner.

`AzureNicCleaner` implements the `crc.core.cleaner.Cleaner` contract: `plan()` lists the NICs of
the configured resource group (`list(resource_group)`) and filters them, `apply()` calls
`begin_delete(...).result()` with the three attempts of the original.
"""

import logging
from collections.abc import Iterable, Sequence
from typing import Any

from crc.azu import _logger
from crc.azu._base import Base
from crc.azu._common import kept, log_delete_summary, log_would_delete, real_run_age
from crc.azu._created import NIC_TYPE, AgeGate
from crc.azu.nic.client import ClientFactory
from crc.core import policy
from crc.core.cleaner import (
    Candidate,
    Cleaner,
    Failure,
    Operation,
    Plan,
    Result,
    RunContext,
    Skipped,
)
from crc.core.registry import register

DELETE_ATTEMPTS = 3


def _compile(
    entries: Sequence[str] | str | None, label: str, *, allow_empty: bool
) -> tuple[str, ...]:
    """Compile `name_regex` / `exception_regex` entries (BUG-113); `ValueError` on a bad one.

    An empty list or string gives no patterns. An empty-string entry is dropped when
    `allow_empty` (`name_regex`: it must never match everything) and refused otherwise
    (`exception_regex`: dropping it would delete NICs main keeps). A bare string (main iterated
    it character by character) and a non-string entry are refused.
    """
    if not entries:
        return ()
    if isinstance(entries, str):
        raise ValueError(
            f"{label} must be a list of regular expressions, got the string {entries!r}"
        )
    usable: list[str] = []
    for entry in entries:
        if not isinstance(entry, str):
            raise ValueError(f"Invalid {label} entry {entry!r}: not a string")
        if entry:
            usable.append(entry)
        elif not allow_empty:
            raise ValueError(f"Invalid {label} entry '': an empty pattern would match every NIC")
    policy.compile_patterns(usable, label)
    return tuple(usable)


def log_none_to_delete() -> None:
    logging.warning("No Azure NICs to delete.")


class AzureNicCleaner:
    """Deletes NICs that are not in use (no VM, private endpoint or private link service) whose
    name matches (`re.search`) any `name_regex` entry and none of the `exception_regex` entries.
    Only the configured resource group is listed."""

    cloud = "azure"
    kind = "nic"

    def __init__(
        self,
        client_factory: ClientFactory,
        resource_group: str,
        name_regex: Sequence[str] | None,
        exception_regex: Sequence[str] | None,
        age_gate: AgeGate | None = None,
    ) -> None:
        self._client_factory = client_factory
        self._resource_group = resource_group
        self._name_regex = name_regex
        self._exception_regex = exception_regex
        self._age_gate = age_gate
        self._include: tuple[str, ...] | None = None
        self._exclude: tuple[str, ...] = ()

    def prepare(self) -> bool:
        """Compile the patterns before any SDK call (`ValueError` if invalid).

        False when no usable `name_regex` entry is left: nothing can be selected, so the caller
        logs "No Azure NICs to delete." and does not list.
        """
        if self._include is None:
            self._include = _compile(self._name_regex, "name_regex", allow_empty=True)
            self._exclude = _compile(self._exception_regex, "exception_regex", allow_empty=False)
        return bool(self._include)

    def supports(self, operation: Operation) -> bool:
        return operation is Operation.DELETE

    def plan(self, context: RunContext) -> Plan:
        """List every NIC of the resource group and filter. Makes no mutating call."""
        if not self.prepare():
            log_none_to_delete()
            return Plan()
        candidates: list[Candidate] = []
        skipped: list[Skipped] = []
        started = _logger.started()
        scanned = 0
        if self._age_gate is not None:
            self._age_gate.load(context.now)
        for nic in self.list_nics():
            scanned += 1
            candidate, reason = self.decide(nic)
            if candidate is not None:
                candidates.append(candidate)
            else:
                skipped.append(Skipped(id=getattr(nic, "name", ""), reason=reason))
        _logger.region_done(self._resource_group, scanned, len(candidates), started)
        if context.dry_run:
            log_would_delete(self._resource_group, candidates)
            log_delete_summary(
                "NICs", [c.id for c in candidates], dry_run=True, number_word="Number"
            )
        return Plan(candidates=tuple(candidates), skipped=tuple(skipped))

    def apply(self, plan: Plan, context: RunContext) -> Result:
        """Delete the planned NICs in plan order, retrying each up to three times."""
        if not self.prepare():
            return Result()
        deleted: list[str] = []
        failed: list[Failure] = []
        for candidate in plan.candidates:
            error = self.attempt_delete(
                candidate.id, dry_run=context.dry_run, record=not context.dry_run
            )
            if error is None:
                deleted.append(candidate.id)
            else:
                failed.append(Failure(id=candidate.id, error=error))
        log_delete_summary("NICs", deleted, dry_run=context.dry_run, number_word="Number")
        return Result(succeeded=tuple(deleted), failed=tuple(failed))

    def list_nics(self) -> Iterable[Any]:
        """The (lazy) listing of the configured resource group (BUG-111). Main requests its
        listing twice and drops the first; so does this."""
        self._client_factory().network_interfaces.list(self._resource_group)
        return self._client_factory().network_interfaces.list(self._resource_group)

    def attempt_delete(self, nic_name: str, *, dry_run: bool, record: bool = True) -> str | None:
        """Delete one NIC with up to three attempts, logging as main does.

        Returns None once it is deleted (always in a dry run), else the last error text.
        Errors are swallowed; they are recorded for the end-of-run list. `record` False leaves
        the ACTION line to the caller (`plan()` logs it in a dry run).
        """
        failure_count = DELETE_ATTEMPTS
        last_error = ""
        last_exception: Exception | None = None
        while failure_count:
            try:
                if not dry_run:
                    self._client_factory().network_interfaces.begin_delete(
                        self._resource_group, nic_name
                    ).result()
                    logging.info("Deleted NIC: %s", nic_name)
                if record:
                    kind = "would_delete" if dry_run else "deleted"
                    _logger.acted(kind, self._resource_group, nic_name)
                return None
            except Exception as e:  # noqa: BLE001 - per-NIC isolation, last error kept and recorded after the retries
                failure_count -= 1
                last_error = str(e)
                last_exception = e
                logging.error("Error occurred while deleting NIC %s: %s", nic_name, e)
                if failure_count:
                    logging.info("Retrying deletion of NIC %s", nic_name)
        logging.error("Failed to delete the NIC - %s", nic_name)
        _logger.failed(self._resource_group, nic_name, last_exception or last_error)
        return last_error

    def decide(self, nic: Any) -> tuple[Candidate | None, str]:
        """Select one SDK NIC: the candidate, or None with the reason it was kept."""
        if (
            nic.virtual_machine
            or getattr(nic, "private_endpoint", None)
            or getattr(nic, "private_link_service", None)
        ):
            return kept("in_use")
        self.prepare()
        name = nic.name
        if not isinstance(name, str):
            raise TypeError(f"argument of type '{type(name).__name__}' is not iterable")
        included = bool(self._include) and policy.name_regex_search_matches(
            self._include or (), name
        )
        excluded = policy.exception_regex_search_matches(
            self._exclude, name
        ) or policy.nic_name_excluded(self._exclude, name)
        if not included:
            return kept("no name_regex match")
        if excluded:
            return kept("matches exception_regex")
        if self._age_gate is not None:
            reason = self._age_gate.keep_reason(getattr(nic, "id", None))
            if reason:
                return kept(reason)
        return Candidate(id=name, name=name), ""


@register("azure", "nic")
def build_nic_cleaner(context: RunContext) -> Cleaner:
    """Registry factory. Options: `resource_group`, `name_regex`, `exception_regex`, `age`
    (positive on a real run)."""
    options = context.options
    age = real_run_age(context, "NIC")
    base = Base(options.get("resource_group") or "")
    gate = AgeGate(age, base.get_resource_client, base.resource_group, NIC_TYPE) if age else None
    return AzureNicCleaner(
        client_factory=base.get_network_client,
        resource_group=base.resource_group,
        name_regex=options.get("name_regex"),
        exception_regex=options.get("exception_regex"),
        age_gate=gate,
    )
