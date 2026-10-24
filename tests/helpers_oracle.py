"""Load the frozen `main` oracles in `tests/legacy/` without the current adapter classes.

`load_module_oracle("aws_vm")` executes `tests/legacy/aws_vm.txt` as a fresh module.
`load_legacy_crc()` executes `tests/legacy/crc_main.txt` with every adapter import bound to the
class of the matching module oracle, so the legacy side of a parity test is `main`'s CLI driving
`main`'s adapters, and it keeps working after an adapter is deleted from `crc/`. The oracle files
are only read, never edited. The module oracles still import the shared bases (`crc.service`,
`crc.<cloud>._base`, `crc.<cloud>.connectivity`, `crc.azu.vm_delete_helpers`) from `crc/`.
"""

import re
import sys
import types
from functools import cache
from pathlib import Path

LEGACY = Path(__file__).parent / "legacy"

# `from <module> import <name>` in crc_main.txt -> the module oracle file that defines it.
ADAPTER_ORACLES: dict[str, str] = {
    "crc.aws.disk": "aws_disk",
    "crc.aws.elastic_ips": "aws_elastic_ips",
    "crc.aws.keypairs": "aws_keypairs",
    "crc.aws.kms": "aws_kms",
    "crc.aws.snapshot": "aws_snapshot",
    "crc.aws.spot_instance_requests": "aws_spot_instance_requests",
    "crc.aws.vm": "aws_vm",
    "crc.aws.vpc": "aws_vpc",
    "crc.azu.disk": "azu_disk",
    "crc.azu.ip": "azu_ip",
    "crc.azu.nic": "azu_nic",
    "crc.azu.spot_vm": "azu_spot_vm",
    "crc.azu.vm": "azu_vm",
    "crc.gcp.disk": "gcp_disk",
    "crc.gcp.ip": "gcp_ip",
    "crc.gcp.spot_vm": "gcp_spot_vm",
    "crc.gcp.vm": "gcp_vm",
    "crc.oci.disk": "oci_disk",
    "crc.oci.spot_vm": "oci_spot_vm",
    "crc.oci.vm": "oci_vm",
}

_IMPORT = re.compile(r"^from (crc\.\w+\.\w+) import (\w+)(?: as (\w+))?$", re.MULTILINE)


def load_module_oracle(name: str) -> types.ModuleType:
    """A fresh module executed from `tests/legacy/<name>.txt`."""
    path = LEGACY / f"{name}.txt"
    module = types.ModuleType(f"legacy_{name}")
    module.__file__ = str(path)
    exec(compile(path.read_text(), str(path), "exec"), module.__dict__)
    return module


@cache
def _cached_module_oracle(name: str) -> types.ModuleType:
    return load_module_oracle(name)


def load_legacy_crc(module_name: str = "legacy_crc_main") -> types.ModuleType:
    """`tests/legacy/crc_main.txt` with its adapter imports bound to the module oracles.

    Each call returns a new module (tests patch its globals); the module oracles are shared,
    because tests patch the SDK, not the adapter classes. The module is registered in
    `sys.modules` under `module_name` so dataclasses and pickling inside it work.
    """
    path = LEGACY / "crc_main.txt"
    source = path.read_text()
    module = types.ModuleType(module_name)
    module.__file__ = str(path)

    def bind(match: re.Match[str]) -> str:
        source_module, name, alias = match.group(1), match.group(2), match.group(3)
        oracle = ADAPTER_ORACLES.get(source_module)
        if oracle is None:
            return match.group(0)
        module.__dict__[alias or name] = getattr(_cached_module_oracle(oracle), name)
        return f"pass  # {match.group(0)} (bound to tests/legacy/{oracle}.txt)"

    source = _IMPORT.sub(bind, source)
    sys.modules[module_name] = module
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module
