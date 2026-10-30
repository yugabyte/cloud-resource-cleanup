# Copyright (c) Yugabyte, Inc.

"""OCI preemptible instance cleaner.

`SpotVmCleaner` implements the `crc.core.cleaner.Cleaner` contract: `plan()` lists the instances
of every region, decides on them and reads their attached volumes (in a dry run it also tag-checks
the leftover volumes); `apply()` terminates each instance, waits for `TERMINATED` and deletes the
leftover volumes, in the order of the original implementation.
"""

import dataclasses
import logging
from collections.abc import Sequence

from crc.core import age as core_age
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
from crc.oci import _logger
from crc.oci._base import Base
from crc.oci.connectivity import CONNECTIVITY_ERRORS, log_skipped_region
from crc.oci.spot_vm import leftovers
from crc.oci.spot_vm.client import (
    ClientFactory,
    Pager,
    RegionClients,
    RegionLister,
    SdkInstance,
    clients_for,
    list_all,
    regions_of,
)
from crc.oci.spot_vm.decide import Clock, SpotVmSettings, skip_reason
from crc.oci.spot_vm.report import VOLUMES_LABEL, record_dry_run
from crc.oci.spot_vm.report import log_instance_error as log_instance_error
from crc.oci.spot_vm.report import log_summary as log_summary
from crc.oci.spot_vm.terminate import terminate_instance, with_checked_volumes


class SpotVmCleaner:
    """Terminates preemptible instances (`RUNNING` / `STOPPED`) that match the filter tags, are
    old enough and are not OKE or instance pool members, then deletes their leftover volumes."""

    cloud = "oci"
    kind = "spot_instances"

    def __init__(
        self,
        clients_factory: ClientFactory,
        list_regions: RegionLister,
        settings: SpotVmSettings,
        pager: Pager = list_all,
        *,
        engine: bool = False,
    ) -> None:
        """`engine` is True for the cleaner `crc.core.engine` builds: a dry-run `plan()` then
        logs the ACTION lines per region and names its candidates by display name (the engine
        never calls `apply()` in a dry run)."""
        self._engine = engine
        self._clients_factory = clients_factory
        self._list_regions = list_regions
        self._settings = settings
        self._pager = pager
        self._clients: dict[str, RegionClients] = {}

    def supports(self, operation: Operation) -> bool:
        return operation is Operation.DELETE

    def plan(self, context: RunContext) -> Plan:
        """List every region and decide on each instance. Makes no mutating call."""
        candidates: list[Candidate] = []
        skipped: list[Skipped] = []
        for region in self._list_regions():
            started = _logger.region_started()
            listed = self.list_region(region)
            if listed is None:
                continue
            clients, instances = listed
            before = len(candidates)
            for instance in instances:
                try:
                    decision = self.consider(clients, instance, region, lambda: context.now)
                    if isinstance(decision, Skipped):
                        skipped.append(decision)
                    else:
                        checked = with_checked_volumes(
                            clients,
                            decision,
                            self._settings,
                            self._pager,
                            dry_run=context.dry_run,
                        )
                        candidates.append(checked)
                        if context.dry_run and self._engine:
                            record_dry_run(Plan(candidates=(checked,)))
                except Exception as e:  # noqa: BLE001 - SAF-040 per-instance isolation, failure logged and recorded, instance kept
                    log_instance_error(getattr(instance, "display_name", "?"), e)
                    _logger.record_failure(region, getattr(instance, "id", "?"), e, logged=True)
            _logger.region_done(region, len(instances), len(candidates) - before, started)
        if context.dry_run:
            volumes = [v for c in candidates for v in c.extra["volumes"]]
            log_summary([c.name for c in candidates], volumes, dry_run=True)
            if self._engine:
                candidates = [dataclasses.replace(c, id=c.name) for c in candidates]
            return Plan(
                candidates=tuple(candidates),
                skipped=tuple(skipped),
                related={VOLUMES_LABEL: tuple(volumes)},
            )
        return Plan(candidates=tuple(candidates), skipped=tuple(skipped))

    def apply(self, plan: Plan, context: RunContext) -> Result:
        """Terminate the planned instances and delete their leftover volumes (nothing in a dry
        run). A failure on one instance is logged and does not stop the others."""
        names = [c.name for c in plan.candidates]
        if context.dry_run:
            volumes = [v for c in plan.candidates for v in c.extra["volumes"]]
            if not self._engine:
                record_dry_run(plan)
            return Result(succeeded=tuple(names), related={VOLUMES_LABEL: tuple(volumes)})
        done: list[str] = []
        deleted: list[str] = []
        failed: list[Failure] = []
        for candidate in plan.candidates:
            clients = self._clients_of(candidate.region)
            try:
                self.terminate(clients, candidate, done, deleted)
            except Exception as e:  # noqa: BLE001 - SAF-040 per-instance isolation, failure logged and recorded
                log_instance_error(candidate.name, e)
                _logger.record_failure(candidate.region, candidate.id, e, logged=True)
                failed.append(Failure(id=candidate.id, error=str(e)))
        log_summary(done, deleted, dry_run=False)
        return Result(
            succeeded=tuple(done), failed=tuple(failed), related={VOLUMES_LABEL: tuple(deleted)}
        )

    def list_region(self, region: str) -> tuple[RegionClients, Sequence[SdkInstance]] | None:
        """Clients and instances of one region, or `None` (logged) when the region is skipped."""
        try:
            clients = self._clients_factory(region)
            instances: Sequence[SdkInstance] = self._pager(
                clients.compute.list_instances, compartment_id=clients.compartment_id
            ).data
        except CONNECTIVITY_ERRORS as e:
            log_skipped_region(region, "list_instances spot/preemptible cleanup", e)
            _logger.region_skipped(region, e)
            return None
        except Exception as e:  # noqa: BLE001 - per-region isolation, failure logged and recorded, region skipped
            logging.error("Region %s: failed listing instances: %s", region, e)
            _logger.record_failure(region, "-", e, logged=True)
            return None
        self._clients[region] = clients
        return clients, instances

    def consider(
        self, clients: RegionClients, instance: SdkInstance, region: str, clock: Clock
    ) -> Candidate | Skipped:
        """Run the checks of main, then read the attached volumes. A failed attachment read keeps
        the instance (terminating would lose the leftovers)."""
        reason = skip_reason(self._settings, instance, clock)
        if reason is not None:
            return Skipped(id=instance.id, reason=reason, name=instance.display_name)
        leftover = leftovers.attached_volume_ids(
            clients.compute, clients.compartment_id, instance.id, self._pager, region
        )
        if leftover is None:
            logging.error(
                "Skipping terminate for %s: volume attachment list failed", instance.display_name
            )
            return Skipped(
                id=instance.id, reason="volume attachment list failed", name=instance.display_name
            )
        return Candidate(
            id=instance.id,
            name=instance.display_name,
            region=region,
            state=instance.lifecycle_state,
            launch_time=instance.time_created,
            tags=policy.normalize_tags(instance.freeform_tags),
            extra={"leftover": tuple(leftover), "volumes": ()},
        )

    def terminate(
        self,
        clients: RegionClients,
        candidate: Candidate,
        done: list[str],
        deleted: list[str],
    ) -> None:
        """Terminate one planned instance, wait for it, and delete its leftover volumes."""
        terminate_instance(clients, candidate, self._settings, self._pager, done, deleted)

    def _clients_of(self, region: str) -> RegionClients:
        clients = self._clients.get(region)
        return clients if clients is not None else self._clients_factory(region)


@register("oci", "spot_instances")
def build_spot_vm_cleaner(context: RunContext) -> Cleaner:
    """Registry factory. Options: `filter_tags`, `exception_tags`, `notags` (dicts), `age` (a
    positive days/hours dict, required) and `custom_age_tag_key`."""
    options = context.options
    base = Base()
    settings = SpotVmSettings(
        filter_tags=options.get("filter_tags") or {},
        exception_tags=options.get("exception_tags") or {},
        notags=options.get("notags") or {},
        age=core_age.require_positive_age(options.get("age")),
        custom_age_tag_key=options.get("custom_age_tag_key") or "",
    )
    return SpotVmCleaner(clients_for(base), regions_of(base), settings, engine=True)
