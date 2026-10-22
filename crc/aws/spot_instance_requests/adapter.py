# Copyright (c) Yugabyte, Inc.

"""`SpotInstanceRequests`: the legacy entry point `crc.py` calls, a thin adapter over the
cleaner that keeps its constructor, attributes and the call order of the original."""

import datetime
import logging
from functools import partial
from typing import Any

from crc.aws.connectivity import CONNECTIVITY_ERRORS, log_skipped_region
from crc.aws.spot_instance_requests import model
from crc.aws.spot_instance_requests.cleaner import (
    SpotRequestCleaner,
    SpotRuntime,
    guarded_cleanup,
    guarded_sweep,
    list_matches,
    log_summary,
)
from crc.aws.spot_instance_requests.cleanup import CleanupEnv, cleanup_one, sweep_region
from crc.aws.spot_instance_requests.client import SpotClient
from crc.aws.spot_instance_requests.model import RequestMatch, SpotSettings, SpotState, TagList
from crc.aws.spot_instance_requests.preview import preview_request, preview_sweep
from crc.aws.spot_instance_requests.requests import build_filter, filtered_requests
from crc.aws.spot_instance_requests.rules import scope_tags_for_volume
from crc.core import logger, policy
from crc.service import Service


class SpotInstanceRequests(Service):
    """Clean AWS Spot Instance Requests and their fulfilled instances.

    Tags are read from the spot request (not the instance). Order of operations: tag
    DoT=false volumes, cancel the request, terminate the instance, delete leftover volumes
    (see `cleanup.py`). Attributes `spot_requests_to_delete`, `instance_ids_to_delete` and
    `volumes_to_delete` hold the result (what would be deleted in a dry run).
    """

    service_name = model.SERVICE_NAME
    default_region_name = model.DEFAULT_REGION
    LEFTOVER_FROM_TAG = model.LEFTOVER_FROM_TAG
    LEFTOVER_INVALID_VALUE = model.LEFTOVER_INVALID_VALUE
    LEFTOVER_SCOPE_PREFIX = model.LEFTOVER_SCOPE_PREFIX
    VOLUME_AVAILABLE_WAIT_SEC = model.VOLUME_AVAILABLE_WAIT_SEC
    VOLUME_POLL_INTERVAL_SEC = model.VOLUME_POLL_INTERVAL_SEC
    SENSITIVE_YB_TASK = policy.SENSITIVE_YB_TASK

    def __init__(
        self,
        dry_run: bool,
        filter_tags: dict[str, list[str]],
        exception_tags: dict[str, list[str]],
        age: dict[str, int],
        custom_age_tag_key: str | None,
        notags: dict[str, list[str]],
    ) -> None:
        super().__init__()
        self.spot_requests_to_delete: list[str] = []
        self.instance_ids_to_delete: list[str] = []
        self.volumes_to_delete: list[str] = []
        self.dry_run = dry_run
        self.filter_tags = filter_tags or {}
        self.exception_tags = exception_tags or {}
        self.age = self.require_positive_age(age)
        self.custom_age_tag_key = custom_age_tag_key
        self.notags = notags or {}
        self._runtime = SpotRuntime()

    @property
    def get_deleted(self) -> list[str]:
        # Requests + instances only; leftover EBS reported via volumes_to_delete.
        return self.spot_requests_to_delete + self.instance_ids_to_delete

    @property
    def delete_count(self) -> int:
        count = len(self.get_deleted) + len(self.volumes_to_delete)
        logging.info("count of spot requests+instances+leftover volumes to delete: %s", count)
        return count

    def _settings(self) -> SpotSettings:
        return SpotSettings(
            filter_tags=self.filter_tags,
            exception_tags=self.exception_tags,
            notags=self.notags,
            age=self.age,
            custom_age_tag_key=self.custom_age_tag_key,
            poll_interval_sec=self.VOLUME_POLL_INTERVAL_SEC,
            wait_sec=self.VOLUME_AVAILABLE_WAIT_SEC,
        )

    def _state(self) -> SpotState:
        return SpotState(
            self.spot_requests_to_delete, self.instance_ids_to_delete, self.volumes_to_delete
        )

    def _now(self) -> datetime.datetime:
        return self._runtime.clock()

    def _get_filter(self) -> list[dict[str, Any]]:
        return build_filter(self._settings())

    def _get_filtered_requests(
        self, spot_request_details: dict[str, Any]
    ) -> list[tuple[str, str | None, str, bool, TagList]]:
        """(request_id, instance_id|None, type, already_cancelled, tags) per matching request."""
        matches = filtered_requests(self._settings(), self._now(), spot_request_details)
        return [
            (m.request_id, m.instance_id, m.request_type, m.already_cancelled, m.tags)
            for m in matches
        ]

    def _scope_tags_for_volume(
        self, request_id: str, request_tags: TagList | None
    ) -> list[dict[str, str]]:
        return scope_tags_for_volume(self._settings(), request_id, request_tags)

    def _env(self, region: str) -> CleanupEnv:
        return CleanupEnv(self._settings(), self._state(), self._runtime.waiter, region)

    def _cleanup_one(  # noqa: PLR0913, PLR0917 - signature kept for existing callers
        self,
        client: SpotClient,
        region: str,
        request_id: str,
        instance_id: str | None,
        request_type: str,
        already_cancelled: bool = False,
        request_tags: TagList | None = None,
    ) -> None:
        match = RequestMatch(
            request_id, instance_id, request_type, already_cancelled, request_tags or []
        )
        if self.dry_run:
            preview_request(self._settings(), self._state(), client, match, region)
        else:
            cleanup_one(self._env(region), client, match)

    def _sweep_tagged_leftover_volumes(self, client: SpotClient, region: str) -> None:
        if self.dry_run:
            preview_sweep(self._settings(), self._state(), client, region, self._now())
        else:
            sweep_region(self._env(region), client, self._now())

    def _process_region(
        self, client: SpotClient, region: str, spot_filter: list[dict[str, Any]]
    ) -> None:
        now = self._now()
        matches = list_matches(self._settings(), client, region, now, spot_filter)
        if matches is None:
            return
        for match in matches:
            guarded_cleanup(
                region,
                match.request_id,
                partial(
                    self._cleanup_one,
                    client,
                    region,
                    match.request_id,
                    match.instance_id,
                    match.request_type,
                    already_cancelled=match.already_cancelled,
                    request_tags=match.tags,
                ),
            )
        # Orphans from prior runs whose spot request AWS already dropped.
        guarded_sweep(region, partial(self._sweep_tagged_leftover_volumes, client, region))

    def delete(self) -> None:
        """Cancel, terminate and clean up (only list in a dry run), region by region."""
        spot_filter = self._get_filter()
        try:
            regions = self._runtime.list_regions()
        except Exception as e:
            logging.error("Failed to list AWS regions for EC2: %s", e)
            logger.current().fail(None, "-", e)
            return
        for region in regions:
            try:
                client = self._runtime.client_factory(region)
                self._process_region(client, region, spot_filter)
            except CONNECTIVITY_ERRORS as e:
                log_skipped_region(region, model.SKIP_CONTEXT, e)
                logger.current().region_skipped(region, e)
                continue
        log_summary(self._state(), dry_run=self.dry_run)

    def cleaner(self) -> SpotRequestCleaner:
        """The cleaner for this configuration (same settings and runtime)."""
        return SpotRequestCleaner(self._settings(), self._runtime)
