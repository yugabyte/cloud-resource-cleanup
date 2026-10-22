# Copyright (c) Yugabyte, Inc.

"""OCI preemptible instance cleaner.

`SpotVmCleaner` implements the `crc.core.cleaner.Cleaner` contract: `plan()` lists the instances
of every region, decides on them and reads their attached volumes (in a dry run it also tag-checks
the leftover volumes); `apply()` terminates each instance, waits for `TERMINATED` and deletes the
leftover volumes, in the order of the original implementation.
"""

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

VOLUMES_LABEL = "Disk"


def log_summary(names: list[str], volumes: list[str], *, dry_run: bool) -> None:
    """The closing lines of main. The lists are logged as they are (main logs its own lists)."""
    if not names:
        logging.warning("No OCI preemptible instances to delete.")
    label = "which will be deleted" if dry_run else "deleted"
    logging.warning(
        "List of OCI preemptible instances (Total: %s) %s: %s", len(names), label, names
    )
    logging.warning(
        "List of OCI leftover block volumes (Total: %s) %s: %s", len(volumes), label, volumes
    )


def log_instance_error(name: object, error: Exception) -> None:
    logging.error("Error processing OCI preemptible instance %s: %s", name, error)


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
    ) -> None:
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
                        candidates.append(self._checked(clients, decision, context.dry_run))
                except Exception as e:
                    log_instance_error(getattr(instance, "display_name", "?"), e)
                    _logger.record_failure(region, getattr(instance, "id", "?"), e)
            _logger.region_done(region, len(instances), len(candidates) - before, started)
        if context.dry_run:
            volumes = [v for c in candidates for v in c.extra["volumes"]]
            log_summary([c.name for c in candidates], volumes, dry_run=True)
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
            for candidate in plan.candidates:
                _logger.record_action(
                    Operation.DELETE,
                    dry_run=True,
                    region=candidate.region,
                    resource_id=candidate.id,
                    name=candidate.name,
                )
                for volume_id in candidate.extra["volumes"]:
                    _logger.record_action(
                        Operation.DELETE,
                        dry_run=True,
                        region=candidate.region,
                        resource_id=volume_id,
                        name=None,
                    )
            return Result(succeeded=tuple(names), related={VOLUMES_LABEL: tuple(volumes)})
        done: list[str] = []
        deleted: list[str] = []
        failed: list[Failure] = []
        for candidate in plan.candidates:
            clients = self._clients_of(candidate.region)
            try:
                self.terminate(clients, candidate, done, deleted)
            except Exception as e:
                log_instance_error(candidate.name, e)
                _logger.record_failure(candidate.region, candidate.id, e)
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
        except Exception as e:
            logging.error("Region %s: failed listing instances: %s", region, e)
            _logger.record_failure(region, "-", e)
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
        leftover: Sequence[str] = candidate.extra["leftover"]
        clients.compute.terminate_instance(candidate.id, preserve_boot_volume=False)
        logging.info("Terminating OCI preemptible instance %s", candidate.name)
        if leftover and not leftovers.wait_terminated(
            clients.compute, candidate.id, self._settings, candidate.region
        ):
            logging.error(
                "Instance %s not TERMINATED yet; attempting leftover volume deletes anyway",
                candidate.name,
            )
        done.append(candidate.name)
        _logger.record_action(
            Operation.DELETE,
            dry_run=False,
            region=candidate.region,
            resource_id=candidate.id,
            name=candidate.name,
        )
        for volume_id in leftover:
            if leftovers.volume_passes_tag_check(
                clients.block, volume_id, self._settings, candidate.region
            ):
                before = len(deleted)
                leftovers.delete_leftover(clients.block, volume_id, deleted, candidate.region)
                if len(deleted) > before:
                    _logger.record_action(
                        Operation.DELETE,
                        dry_run=False,
                        region=candidate.region,
                        resource_id=volume_id,
                        name=None,
                    )

    def _checked(self, clients: RegionClients, candidate: Candidate, dry_run: bool) -> Candidate:
        if not dry_run:
            return candidate
        volumes = leftovers.checked_volumes(
            clients.block, candidate.extra["leftover"], self._settings, candidate.region
        )
        return Candidate(
            id=candidate.id,
            name=candidate.name,
            region=candidate.region,
            state=candidate.state,
            launch_time=candidate.launch_time,
            tags=candidate.tags,
            extra={"leftover": candidate.extra["leftover"], "volumes": tuple(volumes)},
        )

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
    return SpotVmCleaner(clients_for(base), regions_of(base), settings)
