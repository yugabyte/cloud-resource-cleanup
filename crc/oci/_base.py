# Copyright (c) Yugabyte, Inc.

import logging
import os

import oci

from crc.oci.connectivity import CONNECTIVITY_ERRORS, log_skipped_region

HOME_REGION = "us-sanjose-1"
TENANCY_OCID = "ocid1.tenancy.oc1..aaaaaaaa6w2zfjmzecayfzpuwufo5jsiedlap7hce6chuvess7eu2mofvlva"
USER_OCID = "ocid1.user.oc1..aaaaaaaahymi5wmb25sh24g6ldpfccgngqzz2gjbnz5coruofn5jfwe2v5aq"
FINGERPRINT = "bf:15:88:6c:01:e4:53:fd:63:08:d7:83:c3:32:ec:b8"

# Static fallback list used only if the tenancy's subscribed regions can't be
# fetched (e.g. transient IdentityClient failure).
DEFAULT_REGION_FALLBACK = [HOME_REGION]


class Base:
    """
    Authenticates to OCI using an API signing key (config-based auth) and provides
    per-region ComputeClient instances. Mirrors crc/azu/_base.py's env-var based
    credential handling, and crc/aws/_base.py's per-region client pattern.
    """

    def __init__(self) -> None:
        self.tenancy_id = TENANCY_OCID
        self.user_id = USER_OCID
        self.fingerprint = FINGERPRINT
        self.region = HOME_REGION

        key_content = os.environ.get("OCI_PRIVATE_KEY_CONTENT")
        key_file = os.environ.get("OCI_PRIVATE_KEY_FILE")
        if not key_content and not key_file:
            raise KeyError(
                "Either OCI_PRIVATE_KEY_CONTENT or OCI_PRIVATE_KEY_FILE must be set"
            )

        self.config = {
            "user": self.user_id,
            "tenancy": self.tenancy_id,
            "fingerprint": self.fingerprint,
            "region": self.region,
        }
        if key_content:
            self.config["key_content"] = key_content
        else:
            self.config["key_file"] = key_file

        self._compute_clients = {}  # region -> ComputeClient, singleton per region
        self._identity_client = None

    def get_identity_client(self):
        if self._identity_client:
            return self._identity_client
        self._identity_client = oci.identity.IdentityClient(self.config)
        return self._identity_client

    def get_all_regions(self):
        """
        Returns the list of region names the tenancy is subscribed to.
        Falls back to a static single-region list on connectivity failure,
        mirroring crc/aws/_base.py::get_all_regions.
        """
        try:
            identity_client = self.get_identity_client()
            subscriptions = identity_client.list_region_subscriptions(
                self.tenancy_id
            ).data
            regions = [sub.region_name for sub in subscriptions]
            logging.info(f"Retrieved list of OCI regions: {regions}")
            return regions
        except CONNECTIVITY_ERRORS as e:
            log_skipped_region(self.region, "list_region_subscriptions", e)
            logging.warning(
                f"Falling back to static region list: {DEFAULT_REGION_FALLBACK}"
            )
            return DEFAULT_REGION_FALLBACK

    def get_compute_client(self, region: str):
        """
        Return a cached ComputeClient for the given region, creating one if needed.
        """
        if region in self._compute_clients:
            return self._compute_clients[region]
        region_config = dict(self.config, region=region)
        client = oci.core.ComputeClient(region_config)
        self._compute_clients[region] = client
        return client
