# Copyright (c) Yugabyte, Inc.

"""Azure NIC cleaner.

`AzureNicCleaner` implements the `crc.core.cleaner.Cleaner` contract: `plan()` lists the NICs
(`list_all`) and filters them, `apply()` calls `begin_delete(...).result()` with the three
attempts of the original. `NIC` is the legacy class `crc.py` uses; it keeps its constructor and
attributes and runs the cleaner NIC by NIC, in the exact call order of the original.
"""

import logging
from collections.abc import Callable, Iterable, Sequence
from typing import Any, Protocol

from crc.azu import _logger
from crc.azu._base import Base
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
from crc.service import Service

DELETE_ATTEMPTS = 3


class DeletePoller(Protocol):
    """The long-running-operation handle `begin_delete` returns."""

    def result(self) -> object:
        """Wait for the deletion to finish."""
        ...


class NicOperations(Protocol):
    """The two `network_interfaces` calls this cleaner uses."""

    def list_all(self) -> Iterable[Any]:
        """List the NICs of the whole subscription."""
        ...

    def begin_delete(
        self, resource_group_name: str, network_interface_name: str, /
    ) -> DeletePoller:
        """Start the deletion of one NIC (positional arguments, as on main)."""
        ...


class NicNetworkClient(Protocol):
    """The part of the Azure network client this cleaner uses."""

    @property
    def network_interfaces(self) -> NicOperations:
        """Operations on network interfaces."""
        ...


ClientFactory = Callable[[], NicNetworkClient]


def _log_none_to_delete() -> None:
    logging.warning("No Azure NICs to delete.")


def _log_summary(deleted: Sequence[str], *, dry_run: bool) -> None:
    items = list(deleted)
    if dry_run:
        logging.warning(f"List of Azure NICs (Total: {len(items)}) which will be deleted: {items}")
    else:
        logging.warning(f"Number of Azure NICs deleted: {len(items)}")
        logging.warning(f"List of Azure NICs deleted: {items}")


class AzureNicCleaner:
    """Deletes NICs not attached to a VM whose name contains any `name_regex` entry (plain
    substring, not a regex) and none of the `exception_regex` entries."""

    cloud = "azure"
    kind = "nic"

    def __init__(
        self,
        client_factory: ClientFactory,
        resource_group: str,
        name_regex: Sequence[str] | None,
        exception_regex: Sequence[str] | None,
    ) -> None:
        self._client_factory = client_factory
        self._resource_group = resource_group
        self._name_regex = name_regex
        self._exception_regex = exception_regex

    def supports(self, operation: Operation) -> bool:
        return operation is Operation.DELETE

    def plan(self, context: RunContext) -> Plan:
        """List every NIC of the subscription and filter. Makes no mutating call."""
        if not self._name_regex:
            _log_none_to_delete()
            return Plan()
        candidates: list[Candidate] = []
        skipped: list[Skipped] = []
        started = _logger.started()
        scanned = 0
        for nic in self.list_nics():
            scanned += 1
            candidate, reason = self.decide(nic)
            if candidate is not None:
                candidates.append(candidate)
            else:
                skipped.append(Skipped(id=getattr(nic, "name", ""), reason=reason))
        _logger.region_done(self._resource_group, scanned, len(candidates), started)
        if context.dry_run:
            for candidate in candidates:
                _logger.acted("would_delete", self._resource_group, candidate.id)
            _log_summary([c.id for c in candidates], dry_run=True)
        return Plan(candidates=tuple(candidates), skipped=tuple(skipped))

    def apply(self, plan: Plan, context: RunContext) -> Result:
        """Delete the planned NICs in plan order, retrying each up to three times."""
        if not self._name_regex:
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
        _log_summary(deleted, dry_run=context.dry_run)
        return Result(succeeded=tuple(deleted), failed=tuple(failed))

    def list_nics(self) -> Iterable[Any]:
        """The (lazy) subscription-wide listing. Main requests it twice and drops the first."""
        self._client_factory().network_interfaces.list_all()
        return self._client_factory().network_interfaces.list_all()

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
                    logging.info(f"Deleted NIC: {nic_name}")
                if record:
                    kind = "would_delete" if dry_run else "deleted"
                    _logger.acted(kind, self._resource_group, nic_name)
                return None
            except Exception as e:
                failure_count -= 1
                last_error = str(e)
                last_exception = e
                logging.error(f"Error occurred while deleting NIC {nic_name}: {e}")
                if failure_count:
                    logging.info(f"Retrying deletion of NIC {nic_name}")
        logging.error(f"Failed to delete the NIC - {nic_name}")
        _logger.failed(self._resource_group, nic_name, last_exception or last_error)
        return last_error

    @staticmethod
    def _kept(reason: str) -> tuple[None, str]:
        _logger.skip(reason)
        return None, reason

    def decide(self, nic: Any) -> tuple[Candidate | None, str]:
        """Select one SDK NIC: the candidate, or None with the reason it was kept."""
        if nic.virtual_machine:
            return self._kept("attached to a VM")
        included = policy.nic_name_included(self._name_regex or (), nic.name)
        excluded = (
            policy.nic_name_excluded(self._exception_regex, nic.name)
            if self._exception_regex
            else False
        )
        if not included:
            return self._kept("no name_regex match")
        if excluded:
            return self._kept("matches exception_regex")
        return Candidate(id=nic.name, name=nic.name), ""


@register("azure", "nic")
def build_nic_cleaner(context: RunContext) -> Cleaner:
    """Registry factory. Options: `resource_group`, `name_regex`, `exception_regex`."""
    options = context.options
    base = Base(options.get("resource_group") or "")
    return AzureNicCleaner(
        client_factory=base.get_network_client,
        resource_group=base.resource_group,
        name_regex=options.get("name_regex"),
        exception_regex=options.get("exception_regex"),
    )


class NIC(Service):
    """
    This class provides an implementation of the Service class for managing NICs on Azure Cloud.
    By default, it cleans all unattached NICs.

    Attributes: `nics_names_to_delete` (names deleted, or listed in a dry run), `base`,
    `dry_run`, `name_regex`, `exception_regex`. `get_deleted` and `delete_count` read the result.
    """

    def __init__(
        self,
        resource_group: str,
        dry_run: bool,
        name_regex: Sequence[str] | None,
        exception_regex: Sequence[str] | None,
    ) -> None:
        """
        Initializes the object with filter and exception tags to be used when searching for NICs.

        :param dry_run: When True the class only lists the NICs that match the filter and not the
                        exception names and performs no operation on them.
        :param name_regex: List containing filter regex.
        :param exception_regex: List containing exception names.
        """
        super().__init__()
        self.nics_names_to_delete: list[str] = []
        self.base = Base(resource_group)
        self.dry_run = dry_run
        self.name_regex = name_regex
        self.exception_regex = exception_regex

    @property
    def get_deleted(self) -> list[str]:
        """
        Returns the list of items in the nics_names_to_delete list.
        """
        return self.nics_names_to_delete

    @property
    def delete_count(self) -> int:
        """
        Returns the count of items in the nics_names_to_delete list.
        """
        count = len(self.nics_names_to_delete)
        logging.info(f"Count of items in nics_names_to_delete: {count}")
        return count

    def _cleaner(self) -> AzureNicCleaner:
        return AzureNicCleaner(
            self.base.get_network_client,
            self.base.resource_group,
            self.name_regex,
            self.exception_regex,
        )

    def delete(self) -> None:
        """
        Deletes NICs that match name_regex and do not match exception_regex.
        In dry_run mode it only lists the matching NICs and performs no operation on them.
        """
        if not self.name_regex:
            _log_none_to_delete()
            return

        cleaner = self._cleaner()
        started = _logger.started()
        scanned = selected = 0
        for nic in cleaner.list_nics():
            scanned += 1
            candidate, _ = cleaner.decide(nic)
            if candidate is None:
                continue
            selected += 1
            if cleaner.attempt_delete(candidate.id, dry_run=self.dry_run) is None:
                self.nics_names_to_delete.append(candidate.id)
        _logger.region_done(self.base.resource_group, scanned, selected, started)

        _log_summary(self.nics_names_to_delete, dry_run=self.dry_run)
