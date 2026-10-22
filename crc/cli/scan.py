"""The read-only `--scan_tag` path: list instances that carry a tag, then exit."""

import os
import sys
from dataclasses import replace
from typing import Any, NoReturn

from crc.cli.context import RunContext, make_crc
from crc.cli.inputs import Options, Targets
from crc.notifications import notify_cleanup


def _console_url(cloud: Any, project_id: Any, hit: Any, region: Any, name: Any, rid: Any) -> Any:
    """Return the console link of one hit, or None when the link cannot be built."""
    console_url = None
    try:
        if cloud == "aws":
            if region and rid:
                console_url = (
                    f"https://{region}.console.aws.amazon.com/ec2/v2/home?region={region}"
                    f"#Instances:instanceId={rid}"
                )
        elif cloud == "gcp":
            project = (
                project_id
                or os.environ.get("GOOGLE_CLOUD_PROJECT")
                or os.environ.get("GCP_PROJECT")
            )
            if project and region and name:
                console_url = (
                    "https://console.cloud.google.com/compute/instancesDetail/zones/"
                    f"{region}/instances/{name}?project={project}"
                )
        elif cloud == "azure":
            resource_id = hit.get("id") or hit.get("resource_id")
            if resource_id:
                console_url = f"https://portal.azure.com/#resource{resource_id}"
    except Exception:
        console_url = None
    return console_url


def _normalize_hit(cloud: Any, project_id: Any, hit: Any) -> dict[str, Any]:
    """Turn one provider result (name, id, region or zone, state, ...) into a report item."""
    name = hit.get("name") or hit.get("display_name") or ""
    rid = hit.get("id") or hit.get("instance_id") or name
    region = hit.get("region") or hit.get("zone") or hit.get("location") or ""
    state = (hit.get("state") or "").upper()
    launch_time = hit.get("launch_time")
    instance_type = hit.get("instance_type")
    console_url = _console_url(cloud, project_id, hit, region, name, rid)
    return {
        "name": name,
        "id": rid,
        "region": region,
        "console_url": console_url,
        "state": state,
        "launch_time": launch_time,
        "instance_type": instance_type,
    }


def _find_hits(crc: Any, cloud: Any, tag_key: str, tag_value: str) -> Any:
    """Ask the cloud for the tagged instances; a failing scan counts as no hits."""
    try:
        return crc.scan_instances_with_tag(tag_key, tag_value) or []
    except NotImplementedError as e:
        print(f"[{cloud}] scan not implemented: {e}")
        return []
    except Exception as e:
        print(f"[{cloud}] error scanning: {e}")
        return []


def _print_items(items: list[dict[str, Any]]) -> None:
    for it in items:
        print(f" - {it['region']:15} {it['id']:20} {it['name']}")


def _report_items(cloud: Any, items: list[dict[str, Any]], context: RunContext, op: Any) -> None:
    """Send the items to Slack when it is configured, else print them."""
    slack_channel = context.slack_channel
    if context.slack_client and slack_channel:
        try:
            notify_cleanup(
                context.slack_client,
                "#" + slack_channel if not str(slack_channel).startswith("#") else slack_channel,
                "delete" if op == "delete" else op,
                cloud,
                "vm",
                items,
                context.dry_run,
            )
            print(f"[{cloud}] Sent Slack notification to {slack_channel} ({len(items)} items).")
        except Exception as e:
            print(f"[{cloud}] Failed to send Slack notification: {e}")
            _print_items(items)
    else:
        print(f"✅ Found {len(items)} instance(s) in {cloud}:")
        for it in items:
            print(f"  • {it['region']:15} {it['id']:20} {it['name']} {it['console_url'] or ''}")


def _scan_cloud(cloud: Any, context: RunContext, options: Options, tag: tuple[str, str]) -> bool:
    """Scan one cloud and report; return True when it has hits."""
    tag_key, tag_value = tag
    print(f"\nScanning {cloud.upper()} for tag {tag_key}={tag_value}...")
    crc = make_crc(context, cloud)
    hits = _find_hits(crc, cloud, tag_key, tag_value)
    items = [_normalize_hit(cloud, context.project_id, h) for h in hits]
    if items:
        _report_items(cloud, items, context, options.operation_type)
        return True
    print(f"No instances found in {cloud} with {tag_key}={tag_value}.")
    return False


def run_scan(context: RunContext, options: Options, targets: Targets) -> NoReturn:
    """Scan the selected clouds for the tag and exit with code 0, never deleting anything."""
    scan_tag = options.scan_tag
    if "=" not in scan_tag:
        raise ValueError("--scan_tag must be in KEY=VALUE format, e.g. auto_clean=false")
    tag_key, tag_value = scan_tag.split("=", 1)

    if not context.dry_run:
        print("For safety, scan_tag enforces dry_run. Running in dry_run mode.")
        context = replace(context, dry_run=True)

    any_hits = False
    for cloud in list(targets.clouds):
        if _scan_cloud(cloud, context, options, (tag_key, tag_value)):
            any_hits = True

    if any_hits:
        print("\n Instances found with that tag. Review before running cleanup.")
        sys.exit(0)
    else:
        print("\n No matching instances found. Nothing to clean.")
        sys.exit(0)
