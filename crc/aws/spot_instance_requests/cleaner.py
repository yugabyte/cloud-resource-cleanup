# Copyright (c) Yugabyte, Inc.

"""AWS spot instance request cleaner (the `Cleaner` contract).

`plan()` lists the regions and the spot requests and filters them (and, in a dry run, also
records what a real run would do, reads only). `apply()` cancels, terminates and deletes the
leftover volumes, region by region, in the order of the original implementation.
"""

import datetime
import logging
from collections.abc import Sequence
from functools import partial

from crc.aws.connectivity import CONNECTIVITY_ERRORS, log_skipped_region
from crc.aws.spot_instance_requests.cleanup import CleanupEnv, cleanup_one, sweep_region
from crc.aws.spot_instance_requests.client import SpotClient
from crc.aws.spot_instance_requests.model import (
    SKIP_CONTEXT,
    RequestMatch,
    SpotSettings,
    SpotState,
)
from crc.aws.spot_instance_requests.preview import preview_request, preview_sweep
from crc.aws.spot_instance_requests.regions import guarded_cleanup, guarded_sweep, list_matches
from crc.aws.spot_instance_requests.requests import build_filter
from crc.aws.spot_instance_requests.runtime import SpotRuntime, settings_from_options
from crc.aws.spot_instance_requests.summary import log_summary
from crc.core import logger
from crc.core.cleaner import (
    Candidate,
    Cleaner,
    Operation,
    Plan,
    Result,
    RunContext,
)
from crc.core.registry import register
from crc.core.report import DISK

__all__ = [
    "SpotRequestCleaner",
    "SpotRuntime",
    "build_spot_request_cleaner",
    "guarded_cleanup",
    "guarded_sweep",
    "list_matches",
    "log_summary",
    "settings_from_options",
]


def _candidate(match: RequestMatch, region: str) -> Candidate:
    return Candidate(
        id=match.request_id,
        region=region,
        state="cancelled" if match.already_cancelled else "",
        extra={
            "instance_id": match.instance_id,
            "request_type": match.request_type,
            "already_cancelled": match.already_cancelled,
            "tags": tuple(match.tags),
        },
    )


def _match(candidate: Candidate) -> RequestMatch:
    extra = candidate.extra
    return RequestMatch(
        candidate.id,
        extra["instance_id"],
        extra["request_type"],
        extra["already_cancelled"],
        extra["tags"],
    )


class SpotRequestCleaner:
    """Cancels old tagged spot requests, terminates their instances and deletes the leftover
    DeleteOnTermination=false volumes in every region the injected lister returns."""

    cloud = "aws"
    kind = "spot_instance_requests"

    def __init__(self, settings: SpotSettings, runtime: SpotRuntime | None = None) -> None:
        self.settings = settings
        self.runtime = runtime or SpotRuntime()
        self._clients: dict[str, SpotClient] = {}
        self._regions: list[str] | None = None

    def supports(self, operation: Operation) -> bool:
        return operation is Operation.DELETE

    def plan(self, context: RunContext) -> Plan:
        """List regions and requests and filter. Makes no mutating call. In a dry run it also
        records the requests, instances and volumes a real run would act on."""
        self._clients = {}
        self._regions = None
        spot_filter = build_filter(self.settings)
        try:
            regions = list(self.runtime.list_regions())
        except Exception as e:  # noqa: BLE001 - region listing failure recorded, empty plan, nothing is deleted
            logging.error("Failed to list AWS regions for EC2: %s", e)
            logger.current().fail(None, "-", e, logged=True)
            return Plan()
        state = SpotState()
        candidates: list[Candidate] = []
        planned: list[str] = []
        for region in regions:
            try:
                client = self.runtime.client_factory(region)
                matches = list_matches(self.settings, client, region, context.now, spot_filter)
                if matches is None:
                    continue
                planned.append(region)
                self._clients[region] = client
                if context.dry_run:
                    self._preview_region(state, client, region, matches, context.now)
                else:
                    candidates.extend(_candidate(m, region) for m in matches)
            except CONNECTIVITY_ERRORS as e:
                log_skipped_region(region, SKIP_CONTEXT, e)
                logger.current().region_skipped(region, e)
        self._regions = planned
        if context.dry_run:
            log_summary(state, dry_run=True)
            deleted = state.spot_requests_to_delete + state.instance_ids_to_delete
            return Plan(
                candidates=tuple(Candidate(id=rid) for rid in deleted),
                related={DISK: tuple(state.volumes_to_delete)},
            )
        return Plan(candidates=tuple(candidates))

    def _preview_region(
        self,
        state: SpotState,
        client: SpotClient,
        region: str,
        matches: Sequence[RequestMatch],
        now: datetime.datetime,
    ) -> None:
        for match in matches:
            guarded_cleanup(
                region,
                match.request_id,
                partial(preview_request, self.settings, state, client, match, region),
            )
        guarded_sweep(region, partial(preview_sweep, self.settings, state, client, region, now))

    def apply(self, plan: Plan, context: RunContext) -> Result:
        """Cancel, terminate and delete region by region (nothing in a dry run)."""
        if context.dry_run:
            return Result(
                succeeded=tuple(c.id for c in plan.candidates),
                related=dict(plan.related),
            )
        if self._regions is None and not plan.candidates:
            return Result()
        state = SpotState()
        by_region: dict[str, list[Candidate]] = {r: [] for r in (self._regions or ())}
        for candidate in plan.candidates:
            by_region.setdefault(candidate.region, []).append(candidate)
        for region, items in by_region.items():
            try:
                client = self._clients.get(region) or self.runtime.client_factory(region)
            except CONNECTIVITY_ERRORS as e:
                log_skipped_region(region, SKIP_CONTEXT, e)
                logger.current().region_skipped(region, e)
                continue
            env = CleanupEnv(self.settings, state, self.runtime.waiter, region)
            self.apply_region(env, client, [_match(c) for c in items], context.now)
        log_summary(state, dry_run=False)
        deleted = state.spot_requests_to_delete + state.instance_ids_to_delete
        return Result(
            succeeded=tuple(deleted),
            related={DISK: tuple(state.volumes_to_delete)},
        )

    def apply_region(
        self,
        env: CleanupEnv,
        client: SpotClient,
        matches: Sequence[RequestMatch],
        now: datetime.datetime,
    ) -> None:
        """Clean each request, then sweep the region's tagged leftover volumes."""
        for match in matches:
            guarded_cleanup(env.region, match.request_id, partial(cleanup_one, env, client, match))
        guarded_sweep(env.region, partial(sweep_region, env, client, now))


@register("aws", "spot_instance_requests")
def build_spot_request_cleaner(context: RunContext) -> Cleaner:
    """Registry factory. Options: filter_tags, exception_tags, notags, age (required,
    positive) and custom_age_tag_key."""
    return SpotRequestCleaner(settings_from_options(context.options))
