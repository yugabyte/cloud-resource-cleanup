# Copyright (c) Yugabyte, Inc.

"""Read-only scan of EC2 instances carrying one tag, across regions (`crc.py` tag lookup)."""

import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from crc.aws.connectivity import CONNECTIVITY_ERRORS, log_skipped_region
from crc.aws.vm.client import VmScanClient, VmSession

ScanClientFactory = Callable[[str], VmScanClient]
SessionFactory = Callable[[], VmSession]
RegionLister = Callable[[], Sequence[str]]

SERVICE_NAME = "ec2"


def instance_name(tags: Sequence[Mapping[str, Any]]) -> str | None:
    """Value of the first `Name` tag, or None. A tag without `Key` or `Value` raises KeyError."""
    for tag in tags:
        if tag["Key"] == "Name":
            value: str | None = tag["Value"]
            return value
    return None


def _record(region: str, inst: Mapping[str, Any]) -> dict[str, Any]:
    tags_list = inst.get("Tags", []) or []
    tags = {t.get("Key"): t.get("Value") for t in tags_list}
    name = instance_name(tags_list) or tags.get("Name") or ""
    state = inst.get("State", {}).get("Name", "").upper()
    return {
        "cloud": "aws",
        "region": region,
        "id": inst.get("InstanceId"),
        "name": name,
        "tags": tags,
        "state": state,
        "launch_time": inst.get("LaunchTime"),
        "instance_type": inst.get("InstanceType"),
    }


def _scan_region(
    client: VmScanClient,
    region: str,
    filters: Sequence[Mapping[str, Any]],
    results: list[dict[str, Any]],
) -> None:
    try:
        paginator = client.get_paginator("describe_instances")
        for page in paginator.paginate(Filters=filters):
            for reservation in page.get("Reservations", []):
                results.extend(_record(region, inst) for inst in reservation.get("Instances", []))
    except CONNECTIVITY_ERRORS as e:
        log_skipped_region(region, "describe_instances (scan tag)", e)
    except client.exceptions.ClientError as ce:
        logging.warning(f"[aws:{region}] ClientError while describing instances: {ce}")
    except Exception as e:
        logging.warning(f"[aws:{region}] error while describing instances: {e}")


def _regions(session_factory: SessionFactory, list_regions: RegionLister) -> Sequence[str]:
    try:
        regions = session_factory().get_available_regions(SERVICE_NAME)
        if not regions:
            regions = list_regions()
    except Exception:
        regions = list_regions()
    return regions


def find_instances_with_tag(
    tag_key: str,
    tag_value: str,
    session_factory: SessionFactory,
    client_factory: ScanClientFactory,
    list_regions: RegionLister,
) -> list[dict[str, Any]]:
    """Instances with the tag in every region, as dicts (cloud, region, id, name, tags, ...).

    Read-only. A region whose client cannot be created or listed is logged and skipped.
    """
    results: list[dict[str, Any]] = []
    for region in _regions(session_factory, list_regions):
        try:
            client = client_factory(region)
        except Exception as e:
            logging.warning(f"[aws:{region}] failed to create client: {e}")
            continue
        filters = [{"Name": f"tag:{tag_key}", "Values": [tag_value]}]
        _scan_region(client, region, filters, results)
    return results
