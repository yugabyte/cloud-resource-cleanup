import boto3
import datetime
import logging

from crc.aws._base import get_all_regions
from crc.service import Service


class Snapshot(Service):
    service_name = "ec2"

    def __init__(self, dry_run, age_days, exception_tags):
        super().__init__()
        self.dry_run = dry_run
        self.age_days = age_days
        self.exception_tags = exception_tags
        self.deleted_snapshots = []

    @property
    def get_deleted(self):
        return self.deleted_snapshots
    
    @property
    def delete_count(self):
        return len(self.deleted_snapshots)

    def _is_protected(self, tags, snap_id):
        if not tags or not self.exception_tags:
            return False

        tag_map = {t["Key"]: t["Value"] for t in tags}
        for k, values in self.exception_tags.items():
            if k in tag_map and (not values or tag_map[k] in values):
                logging.info(f"Snap {snap_id} has tag {k}")
                return True
        return False

    # def _snapshot_used_by_ami(self, ec2, snapshot_id):
    #     images = ec2.describe_images(Owners=["self"])["Images"]
    #     for img in images:
    #         for bdm in img.get("BlockDeviceMappings", []):
    #             if bdm.get("Ebs", {}).get("SnapshotId") == snapshot_id:
    #                 return True
    #     return False
    def _get_snapshots_used_by_amis(self, ec2):
        used_snapshots = set()

        images = ec2.describe_images(Owners=["self"])["Images"]
        for img in images:
            for bdm in img.get("BlockDeviceMappings", []):
                snap_id = bdm.get("Ebs", {}).get("SnapshotId")
                if snap_id:
                    used_snapshots.add(snap_id)

        return used_snapshots

    def delete(self):
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=self.age_days)

        for region in get_all_regions(self.service_name, default_region_name="us-west-2"):
            ec2 = boto3.client("ec2", region_name=region)

            snapshots = ec2.describe_snapshots(OwnerIds=["self"])["Snapshots"]
            used_by_amis = self._get_snapshots_used_by_amis(ec2)

            for snap in snapshots:
                snap_id = snap["SnapshotId"]
                start_time = snap["StartTime"]

                if start_time > cutoff:
                    continue

                if self._is_protected(snap.get("Tags"), snap_id):
                    continue

                if snap_id in used_by_amis:
                    logging.info(f"Skipping snapshot {snap_id}, used by AMI")
                    continue

                if not self.dry_run:
                    try:
                        ec2.delete_snapshot(SnapshotId=snap_id)
                        logging.info(f"Deleted snapshot {snap_id}")
                        self.deleted_snapshots.append(snap_id)
                    except Exception as e:
                        logging.error(f"Failed to delete snapshot {snap_id}: {e}")
                else:
                    self.deleted_snapshots.append(snap_id)
