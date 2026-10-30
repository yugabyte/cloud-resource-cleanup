"""`tests/helpers_oracle.py` binds the adapters of crc_main.txt to the frozen module oracles."""

import crc.aws.vm
import crc.oci.spot_vm
from tests.helpers_oracle import ADAPTER_ORACLES, load_legacy_crc, load_module_oracle


def test_legacy_crc_uses_the_module_oracles_not_the_current_adapters() -> None:
    legacy = load_legacy_crc()
    assert not hasattr(crc.aws.vm, "VM")
    assert not hasattr(crc.oci.spot_vm, "SpotVM")
    assert legacy.AWS_VM.__module__ == "legacy_aws_vm"
    assert legacy.OCI_SpotVM.__module__ == "legacy_oci_spot_vm"
    assert hasattr(legacy, "CRC") and hasattr(legacy, "main")


def test_every_adapter_import_of_crc_main_has_an_oracle() -> None:
    legacy = load_legacy_crc()
    bound = {value.__module__ for value in vars(legacy).values() if isinstance(value, type)}
    assert {f"legacy_{name}" for name in ADAPTER_ORACLES.values()} <= bound


def test_each_load_is_a_fresh_module() -> None:
    assert load_legacy_crc() is not load_legacy_crc()
    assert load_module_oracle("aws_kms") is not load_module_oracle("aws_kms")
