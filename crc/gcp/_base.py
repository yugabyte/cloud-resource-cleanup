# Copyright (c) Yugabyte, Inc.

from google.cloud import compute_v1


def get_gcp_regions(project_id: str) -> list[str]:
    client = compute_v1.RegionsClient()
    regions = client.list(project=project_id)
    return [region.name for region in regions]
