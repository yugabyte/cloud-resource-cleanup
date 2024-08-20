# Copyright (c) Yugabyte, Inc.

from google.cloud import compute_v1

def get_gcp_regions(project_id):
    client = compute_v1.RegionsClient()
    project_id = project_id
    regions = client.list(project=project_id)

    # Extract region names
    region_list = [region.name for region in regions]
    return region_list
