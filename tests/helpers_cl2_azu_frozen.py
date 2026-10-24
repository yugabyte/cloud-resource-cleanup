"""Frozen expectations of `tests/test_cli_bridge_azu.py`, captured from the former legacy
handlers (the current adapters, driven through the legacy `CRC` methods) before their removal.

`RUN_LOG[(kind, variant, dry)]` holds the run-log lines (ordered, REGION set); `FIXED` holds, per
case, the fields where the fixed-bug outcome differs from `main` (BUG-003/013/077/113/174).
"""

from typing import Any

RUN_LOG: dict[tuple[str, str, bool], tuple[list[str], list[str]]] = {
    ("vm-delete-running", "plain", False): (
        [
            "RUN START cloud=azure resource=vm operation=delete "
            'dry_run=false age={"days":1} '
            'filter_tags={"env":["ci"]} notags={"owner":["x"]} '
            'resource_group="rg"',
            "ACTION deleted cloud=azure resource=vm region=rg id=a name=a",
            "ACTION deleted cloud=azure resource=vm region=rg id=e name=e",
            "SKIPPED cloud=azure resource=vm reason=exception_or_notags count=1",
            "SKIPPED cloud=azure resource=vm reason=filter_tags count=1",
            "SKIPPED cloud=azure resource=vm reason=state count=1",
            "SKIPPED cloud=azure resource=vm reason=untagged count=1",
            "RESULT cloud=azure resource=vm operation=delete "
            "dry_run=false selected=2 done=2 failed=0 skipped=4 "
            "regions=1 regions_skipped=0 duration_s=S",
        ],
        ["REGION cloud=azure resource=vm region=rg scanned=6 selected=2 seconds=S"],
    ),
    ("vm-delete-running", "plain", True): (
        [
            "RUN START cloud=azure resource=vm operation=delete "
            'dry_run=true age={"days":1} '
            'filter_tags={"env":["ci"]} notags={"owner":["x"]} '
            'resource_group="rg"',
            "ACTION would_delete cloud=azure resource=vm region=rg id=a name=a",
            "ACTION would_delete cloud=azure resource=vm region=rg id=e name=e",
            "SKIPPED cloud=azure resource=vm reason=exception_or_notags count=1",
            "SKIPPED cloud=azure resource=vm reason=filter_tags count=1",
            "SKIPPED cloud=azure resource=vm reason=state count=1",
            "SKIPPED cloud=azure resource=vm reason=untagged count=1",
            "RESULT cloud=azure resource=vm operation=delete "
            "dry_run=true selected=2 done=0 failed=0 skipped=4 "
            "regions=1 regions_skipped=0 duration_s=S",
        ],
        ["REGION cloud=azure resource=vm region=rg scanned=6 selected=2 seconds=S"],
    ),
    ("vm-delete-running", "empty", False): (
        [
            "RUN START cloud=azure resource=vm operation=delete "
            'dry_run=false age={"days":1} '
            'filter_tags={"env":["ci"]} notags={"owner":["x"]} '
            'resource_group="rg"',
            "RESULT cloud=azure resource=vm operation=delete "
            "dry_run=false selected=0 done=0 failed=0 skipped=0 "
            "regions=1 regions_skipped=0 duration_s=S",
        ],
        ["REGION cloud=azure resource=vm region=rg scanned=0 selected=0 seconds=S"],
    ),
    ("vm-delete-running", "empty", True): (
        [
            "RUN START cloud=azure resource=vm operation=delete "
            'dry_run=true age={"days":1} '
            'filter_tags={"env":["ci"]} notags={"owner":["x"]} '
            'resource_group="rg"',
            "RESULT cloud=azure resource=vm operation=delete "
            "dry_run=true selected=0 done=0 failed=0 skipped=0 "
            "regions=1 regions_skipped=0 duration_s=S",
        ],
        ["REGION cloud=azure resource=vm region=rg scanned=0 selected=0 seconds=S"],
    ),
    ("vm-delete-running", "list_error", False): (
        [
            "RUN START cloud=azure resource=vm "
            'operation=delete dry_run=false age={"days":1} '
            'filter_tags={"env":["ci"]} '
            'notags={"owner":["x"]} resource_group="rg"',
            "RESULT cloud=azure resource=vm "
            "operation=delete dry_run=false selected=0 "
            "done=0 failed=0 skipped=0 regions=0 "
            "regions_skipped=0 duration_s=S",
        ],
        [],
    ),
    ("vm-delete-running", "list_error", True): (
        [
            "RUN START cloud=azure resource=vm "
            'operation=delete dry_run=true age={"days":1} '
            'filter_tags={"env":["ci"]} '
            'notags={"owner":["x"]} resource_group="rg"',
            "RESULT cloud=azure resource=vm operation=delete "
            "dry_run=true selected=0 done=0 failed=0 "
            "skipped=0 regions=0 regions_skipped=0 "
            "duration_s=S",
        ],
        [],
    ),
    ("vm-delete-running", "delete_error", False): (
        [
            "RUN START cloud=azure resource=vm "
            "operation=delete dry_run=false "
            'age={"days":1} filter_tags={"env":["ci"]} '
            'notags={"owner":["x"]} resource_group="rg"',
            "ACTION deleted cloud=azure resource=vm region=rg id=e name=e",
            "SKIPPED cloud=azure resource=vm reason=exception_or_notags count=1",
            "SKIPPED cloud=azure resource=vm reason=filter_tags count=1",
            "SKIPPED cloud=azure resource=vm reason=state count=1",
            "SKIPPED cloud=azure resource=vm reason=untagged count=1",
            "ERRORS count=1",
            "FAILED cloud=azure resource=vm region=rg id=a error=HttpResponseError: boom",
            "RESULT cloud=azure resource=vm "
            "operation=delete dry_run=false selected=1 "
            "done=1 failed=1 skipped=4 regions=1 "
            "regions_skipped=0 duration_s=S",
        ],
        ["REGION cloud=azure resource=vm region=rg scanned=6 selected=2 seconds=S"],
    ),
    ("vm-delete-running", "delete_error", True): (
        [
            "RUN START cloud=azure resource=vm "
            'operation=delete dry_run=true age={"days":1} '
            'filter_tags={"env":["ci"]} '
            'notags={"owner":["x"]} resource_group="rg"',
            "ACTION would_delete cloud=azure resource=vm region=rg id=a name=a",
            "ACTION would_delete cloud=azure resource=vm region=rg id=e name=e",
            "SKIPPED cloud=azure resource=vm reason=exception_or_notags count=1",
            "SKIPPED cloud=azure resource=vm reason=filter_tags count=1",
            "SKIPPED cloud=azure resource=vm reason=state count=1",
            "SKIPPED cloud=azure resource=vm reason=untagged count=1",
            "RESULT cloud=azure resource=vm "
            "operation=delete dry_run=true selected=2 "
            "done=0 failed=0 skipped=4 regions=1 "
            "regions_skipped=0 duration_s=S",
        ],
        ["REGION cloud=azure resource=vm region=rg scanned=6 selected=2 seconds=S"],
    ),
    ("vm-stop-stop", "plain", False): (
        [
            "RUN START cloud=azure resource=vm operation=stop "
            'dry_run=false age={"days":1} filter_tags={"env":["ci"]} '
            'notags={"owner":["x"]} resource_group="rg"',
            "ACTION stopped cloud=azure resource=vm region=rg id=a name=a",
            "ACTION stopped cloud=azure resource=vm region=rg id=e name=e",
            "SKIPPED cloud=azure resource=vm reason=exception_or_notags count=1",
            "SKIPPED cloud=azure resource=vm reason=filter_tags count=1",
            "SKIPPED cloud=azure resource=vm reason=state count=1",
            "SKIPPED cloud=azure resource=vm reason=untagged count=1",
            "RESULT cloud=azure resource=vm operation=stop "
            "dry_run=false selected=2 done=2 failed=0 skipped=4 "
            "regions=1 regions_skipped=0 duration_s=S",
        ],
        ["REGION cloud=azure resource=vm region=rg scanned=6 selected=2 seconds=S"],
    ),
    ("vm-stop-stop", "plain", True): (
        [
            "RUN START cloud=azure resource=vm operation=stop "
            'dry_run=true age={"days":1} filter_tags={"env":["ci"]} '
            'notags={"owner":["x"]} resource_group="rg"',
            "ACTION would_stop cloud=azure resource=vm region=rg id=a name=a",
            "ACTION would_stop cloud=azure resource=vm region=rg id=e name=e",
            "SKIPPED cloud=azure resource=vm reason=exception_or_notags count=1",
            "SKIPPED cloud=azure resource=vm reason=filter_tags count=1",
            "SKIPPED cloud=azure resource=vm reason=state count=1",
            "SKIPPED cloud=azure resource=vm reason=untagged count=1",
            "RESULT cloud=azure resource=vm operation=stop "
            "dry_run=true selected=2 done=0 failed=0 skipped=4 "
            "regions=1 regions_skipped=0 duration_s=S",
        ],
        ["REGION cloud=azure resource=vm region=rg scanned=6 selected=2 seconds=S"],
    ),
    ("vm-stop-stop", "empty", False): (
        [
            "RUN START cloud=azure resource=vm operation=stop "
            'dry_run=false age={"days":1} filter_tags={"env":["ci"]} '
            'notags={"owner":["x"]} resource_group="rg"',
            "RESULT cloud=azure resource=vm operation=stop "
            "dry_run=false selected=0 done=0 failed=0 skipped=0 "
            "regions=1 regions_skipped=0 duration_s=S",
        ],
        ["REGION cloud=azure resource=vm region=rg scanned=0 selected=0 seconds=S"],
    ),
    ("vm-stop-stop", "empty", True): (
        [
            "RUN START cloud=azure resource=vm operation=stop "
            'dry_run=true age={"days":1} filter_tags={"env":["ci"]} '
            'notags={"owner":["x"]} resource_group="rg"',
            "RESULT cloud=azure resource=vm operation=stop "
            "dry_run=true selected=0 done=0 failed=0 skipped=0 "
            "regions=1 regions_skipped=0 duration_s=S",
        ],
        ["REGION cloud=azure resource=vm region=rg scanned=0 selected=0 seconds=S"],
    ),
    ("vm-stop-stop", "list_error", False): (
        [
            "RUN START cloud=azure resource=vm operation=stop "
            'dry_run=false age={"days":1} '
            'filter_tags={"env":["ci"]} notags={"owner":["x"]} '
            'resource_group="rg"',
            "RESULT cloud=azure resource=vm operation=stop "
            "dry_run=false selected=0 done=0 failed=0 skipped=0 "
            "regions=0 regions_skipped=0 duration_s=S",
        ],
        [],
    ),
    ("vm-stop-stop", "list_error", True): (
        [
            "RUN START cloud=azure resource=vm operation=stop "
            'dry_run=true age={"days":1} '
            'filter_tags={"env":["ci"]} notags={"owner":["x"]} '
            'resource_group="rg"',
            "RESULT cloud=azure resource=vm operation=stop "
            "dry_run=true selected=0 done=0 failed=0 skipped=0 "
            "regions=0 regions_skipped=0 duration_s=S",
        ],
        [],
    ),
    ("vm-stop-stop", "delete_error", False): (
        [
            "RUN START cloud=azure resource=vm operation=stop "
            'dry_run=false age={"days":1} '
            'filter_tags={"env":["ci"]} notags={"owner":["x"]} '
            'resource_group="rg"',
            "ACTION stopped cloud=azure resource=vm region=rg id=a name=a",
            "ACTION stopped cloud=azure resource=vm region=rg id=e name=e",
            "SKIPPED cloud=azure resource=vm reason=exception_or_notags count=1",
            "SKIPPED cloud=azure resource=vm reason=filter_tags count=1",
            "SKIPPED cloud=azure resource=vm reason=state count=1",
            "SKIPPED cloud=azure resource=vm reason=untagged count=1",
            "RESULT cloud=azure resource=vm operation=stop "
            "dry_run=false selected=2 done=2 failed=0 "
            "skipped=4 regions=1 regions_skipped=0 "
            "duration_s=S",
        ],
        ["REGION cloud=azure resource=vm region=rg scanned=6 selected=2 seconds=S"],
    ),
    ("vm-stop-stop", "delete_error", True): (
        [
            "RUN START cloud=azure resource=vm operation=stop "
            'dry_run=true age={"days":1} '
            'filter_tags={"env":["ci"]} notags={"owner":["x"]} '
            'resource_group="rg"',
            "ACTION would_stop cloud=azure resource=vm region=rg id=a name=a",
            "ACTION would_stop cloud=azure resource=vm region=rg id=e name=e",
            "SKIPPED cloud=azure resource=vm reason=exception_or_notags count=1",
            "SKIPPED cloud=azure resource=vm reason=filter_tags count=1",
            "SKIPPED cloud=azure resource=vm reason=state count=1",
            "SKIPPED cloud=azure resource=vm reason=untagged count=1",
            "RESULT cloud=azure resource=vm operation=stop "
            "dry_run=true selected=2 done=0 failed=0 skipped=4 "
            "regions=1 regions_skipped=0 duration_s=S",
        ],
        ["REGION cloud=azure resource=vm region=rg scanned=6 selected=2 seconds=S"],
    ),
    ("vm-delete-stopped", "plain", False): (
        [
            "RUN START cloud=azure resource=vm operation=delete "
            'dry_run=false age={"days":1} '
            'filter_tags={"env":["ci"]} notags={"owner":["x"]} '
            'resource_group="rg"',
            "ACTION deleted cloud=azure resource=vm region=rg id=a name=a",
            "ACTION deleted cloud=azure resource=vm region=rg id=b name=b",
            "ACTION deleted cloud=azure resource=vm region=rg id=e name=e",
            "SKIPPED cloud=azure resource=vm reason=exception_or_notags count=1",
            "SKIPPED cloud=azure resource=vm reason=filter_tags count=1",
            "SKIPPED cloud=azure resource=vm reason=untagged count=1",
            "RESULT cloud=azure resource=vm operation=delete "
            "dry_run=false selected=3 done=3 failed=0 skipped=3 "
            "regions=1 regions_skipped=0 duration_s=S",
        ],
        ["REGION cloud=azure resource=vm region=rg scanned=6 selected=3 seconds=S"],
    ),
    ("vm-delete-stopped", "plain", True): (
        [
            "RUN START cloud=azure resource=vm operation=delete "
            'dry_run=true age={"days":1} '
            'filter_tags={"env":["ci"]} notags={"owner":["x"]} '
            'resource_group="rg"',
            "ACTION would_delete cloud=azure resource=vm region=rg id=a name=a",
            "ACTION would_delete cloud=azure resource=vm region=rg id=b name=b",
            "ACTION would_delete cloud=azure resource=vm region=rg id=e name=e",
            "SKIPPED cloud=azure resource=vm reason=exception_or_notags count=1",
            "SKIPPED cloud=azure resource=vm reason=filter_tags count=1",
            "SKIPPED cloud=azure resource=vm reason=untagged count=1",
            "RESULT cloud=azure resource=vm operation=delete "
            "dry_run=true selected=3 done=0 failed=0 skipped=3 "
            "regions=1 regions_skipped=0 duration_s=S",
        ],
        ["REGION cloud=azure resource=vm region=rg scanned=6 selected=3 seconds=S"],
    ),
    ("vm-delete-stopped", "empty", False): (
        [
            "RUN START cloud=azure resource=vm operation=delete "
            'dry_run=false age={"days":1} '
            'filter_tags={"env":["ci"]} notags={"owner":["x"]} '
            'resource_group="rg"',
            "RESULT cloud=azure resource=vm operation=delete "
            "dry_run=false selected=0 done=0 failed=0 skipped=0 "
            "regions=1 regions_skipped=0 duration_s=S",
        ],
        ["REGION cloud=azure resource=vm region=rg scanned=0 selected=0 seconds=S"],
    ),
    ("vm-delete-stopped", "empty", True): (
        [
            "RUN START cloud=azure resource=vm operation=delete "
            'dry_run=true age={"days":1} '
            'filter_tags={"env":["ci"]} notags={"owner":["x"]} '
            'resource_group="rg"',
            "RESULT cloud=azure resource=vm operation=delete "
            "dry_run=true selected=0 done=0 failed=0 skipped=0 "
            "regions=1 regions_skipped=0 duration_s=S",
        ],
        ["REGION cloud=azure resource=vm region=rg scanned=0 selected=0 seconds=S"],
    ),
    ("vm-delete-stopped", "list_error", False): (
        [
            "RUN START cloud=azure resource=vm "
            'operation=delete dry_run=false age={"days":1} '
            'filter_tags={"env":["ci"]} '
            'notags={"owner":["x"]} resource_group="rg"',
            "RESULT cloud=azure resource=vm "
            "operation=delete dry_run=false selected=0 "
            "done=0 failed=0 skipped=0 regions=0 "
            "regions_skipped=0 duration_s=S",
        ],
        [],
    ),
    ("vm-delete-stopped", "list_error", True): (
        [
            "RUN START cloud=azure resource=vm "
            'operation=delete dry_run=true age={"days":1} '
            'filter_tags={"env":["ci"]} '
            'notags={"owner":["x"]} resource_group="rg"',
            "RESULT cloud=azure resource=vm operation=delete "
            "dry_run=true selected=0 done=0 failed=0 "
            "skipped=0 regions=0 regions_skipped=0 "
            "duration_s=S",
        ],
        [],
    ),
    ("vm-delete-stopped", "delete_error", False): (
        [
            "RUN START cloud=azure resource=vm "
            "operation=delete dry_run=false "
            'age={"days":1} filter_tags={"env":["ci"]} '
            'notags={"owner":["x"]} resource_group="rg"',
            "ACTION deleted cloud=azure resource=vm region=rg id=b name=b",
            "ACTION deleted cloud=azure resource=vm region=rg id=e name=e",
            "SKIPPED cloud=azure resource=vm reason=exception_or_notags count=1",
            "SKIPPED cloud=azure resource=vm reason=filter_tags count=1",
            "SKIPPED cloud=azure resource=vm reason=untagged count=1",
            "ERRORS count=1",
            "FAILED cloud=azure resource=vm region=rg id=a error=HttpResponseError: boom",
            "RESULT cloud=azure resource=vm "
            "operation=delete dry_run=false selected=2 "
            "done=2 failed=1 skipped=3 regions=1 "
            "regions_skipped=0 duration_s=S",
        ],
        ["REGION cloud=azure resource=vm region=rg scanned=6 selected=3 seconds=S"],
    ),
    ("vm-delete-stopped", "delete_error", True): (
        [
            "RUN START cloud=azure resource=vm "
            'operation=delete dry_run=true age={"days":1} '
            'filter_tags={"env":["ci"]} '
            'notags={"owner":["x"]} resource_group="rg"',
            "ACTION would_delete cloud=azure resource=vm region=rg id=a name=a",
            "ACTION would_delete cloud=azure resource=vm region=rg id=b name=b",
            "ACTION would_delete cloud=azure resource=vm region=rg id=e name=e",
            "SKIPPED cloud=azure resource=vm reason=exception_or_notags count=1",
            "SKIPPED cloud=azure resource=vm reason=filter_tags count=1",
            "SKIPPED cloud=azure resource=vm reason=untagged count=1",
            "RESULT cloud=azure resource=vm "
            "operation=delete dry_run=true selected=3 "
            "done=0 failed=0 skipped=3 regions=1 "
            "regions_skipped=0 duration_s=S",
        ],
        ["REGION cloud=azure resource=vm region=rg scanned=6 selected=3 seconds=S"],
    ),
    ("ip-delete", "plain", False): (
        [
            "RUN START cloud=azure resource=ip operation=delete "
            'dry_run=false age={"days":1} filter_tags={"env":["ci"]} '
            'notags={"owner":["x"]} name_regex=["n"] '
            'exception_regex=["x"] resource_group="rg"',
            "ACTION deleted cloud=azure resource=ip region=rg id=a name=a",
            "ACTION deleted cloud=azure resource=ip region=rg id=e name=e",
            "SKIPPED cloud=azure resource=ip reason=exception_or_notags count=1",
            "SKIPPED cloud=azure resource=ip reason=filter_tags count=2",
            "SKIPPED cloud=azure resource=ip reason=in_use count=1",
            "RESULT cloud=azure resource=ip operation=delete "
            "dry_run=false selected=2 done=2 failed=0 skipped=4 "
            "regions=1 regions_skipped=0 duration_s=S",
        ],
        ["REGION cloud=azure resource=ip region=rg scanned=6 selected=2 seconds=S"],
    ),
    ("ip-delete", "plain", True): (
        [
            "RUN START cloud=azure resource=ip operation=delete "
            'dry_run=true age={"days":1} filter_tags={"env":["ci"]} '
            'notags={"owner":["x"]} name_regex=["n"] '
            'exception_regex=["x"] resource_group="rg"',
            "ACTION would_delete cloud=azure resource=ip region=rg id=a name=a",
            "ACTION would_delete cloud=azure resource=ip region=rg id=e name=e",
            "SKIPPED cloud=azure resource=ip reason=exception_or_notags count=1",
            "SKIPPED cloud=azure resource=ip reason=filter_tags count=2",
            "SKIPPED cloud=azure resource=ip reason=in_use count=1",
            "RESULT cloud=azure resource=ip operation=delete dry_run=true "
            "selected=2 done=0 failed=0 skipped=4 regions=1 "
            "regions_skipped=0 duration_s=S",
        ],
        ["REGION cloud=azure resource=ip region=rg scanned=6 selected=2 seconds=S"],
    ),
    ("ip-delete", "empty", False): (
        [
            "RUN START cloud=azure resource=ip operation=delete "
            'dry_run=false age={"days":1} filter_tags={"env":["ci"]} '
            'notags={"owner":["x"]} name_regex=["n"] '
            'exception_regex=["x"] resource_group="rg"',
            "RESULT cloud=azure resource=ip operation=delete "
            "dry_run=false selected=0 done=0 failed=0 skipped=0 "
            "regions=1 regions_skipped=0 duration_s=S",
        ],
        ["REGION cloud=azure resource=ip region=rg scanned=0 selected=0 seconds=S"],
    ),
    ("ip-delete", "empty", True): (
        [
            "RUN START cloud=azure resource=ip operation=delete "
            'dry_run=true age={"days":1} filter_tags={"env":["ci"]} '
            'notags={"owner":["x"]} name_regex=["n"] '
            'exception_regex=["x"] resource_group="rg"',
            "RESULT cloud=azure resource=ip operation=delete dry_run=true "
            "selected=0 done=0 failed=0 skipped=0 regions=1 "
            "regions_skipped=0 duration_s=S",
        ],
        ["REGION cloud=azure resource=ip region=rg scanned=0 selected=0 seconds=S"],
    ),
    ("ip-delete", "list_error", False): (
        [
            "RUN START cloud=azure resource=ip operation=delete "
            'dry_run=false age={"days":1} '
            'filter_tags={"env":["ci"]} notags={"owner":["x"]} '
            'name_regex=["n"] exception_regex=["x"] '
            'resource_group="rg"',
            "RESULT cloud=azure resource=ip operation=delete "
            "dry_run=false selected=0 done=0 failed=0 skipped=0 "
            "regions=0 regions_skipped=0 duration_s=S",
        ],
        [],
    ),
    ("ip-delete", "list_error", True): (
        [
            "RUN START cloud=azure resource=ip operation=delete "
            'dry_run=true age={"days":1} filter_tags={"env":["ci"]} '
            'notags={"owner":["x"]} name_regex=["n"] '
            'exception_regex=["x"] resource_group="rg"',
            "RESULT cloud=azure resource=ip operation=delete "
            "dry_run=true selected=0 done=0 failed=0 skipped=0 "
            "regions=0 regions_skipped=0 duration_s=S",
        ],
        [],
    ),
    ("ip-delete", "delete_error", False): (
        [
            "RUN START cloud=azure resource=ip operation=delete "
            'dry_run=false age={"days":1} '
            'filter_tags={"env":["ci"]} notags={"owner":["x"]} '
            'name_regex=["n"] exception_regex=["x"] '
            'resource_group="rg"',
            "ACTION deleted cloud=azure resource=ip region=rg id=a name=a",
            "SKIPPED cloud=azure resource=ip reason=exception_or_notags count=1",
            "SKIPPED cloud=azure resource=ip reason=filter_tags count=2",
            "SKIPPED cloud=azure resource=ip reason=in_use count=1",
            "RESULT cloud=azure resource=ip operation=delete "
            "dry_run=false selected=1 done=1 failed=0 skipped=4 "
            "regions=0 regions_skipped=0 duration_s=S",
        ],
        [],
    ),
    ("ip-delete", "delete_error", True): (
        [
            "RUN START cloud=azure resource=ip operation=delete "
            'dry_run=true age={"days":1} '
            'filter_tags={"env":["ci"]} notags={"owner":["x"]} '
            'name_regex=["n"] exception_regex=["x"] '
            'resource_group="rg"',
            "ACTION would_delete cloud=azure resource=ip region=rg id=a name=a",
            "ACTION would_delete cloud=azure resource=ip region=rg id=e name=e",
            "SKIPPED cloud=azure resource=ip reason=exception_or_notags count=1",
            "SKIPPED cloud=azure resource=ip reason=filter_tags count=2",
            "SKIPPED cloud=azure resource=ip reason=in_use count=1",
            "RESULT cloud=azure resource=ip operation=delete "
            "dry_run=true selected=2 done=0 failed=0 skipped=4 "
            "regions=1 regions_skipped=0 duration_s=S",
        ],
        ["REGION cloud=azure resource=ip region=rg scanned=6 selected=2 seconds=S"],
    ),
    ("nic-delete", "plain", False): (
        [
            "RUN START cloud=azure resource=nic operation=delete "
            'dry_run=false name_regex=["^nic"] exception_regex=["keep"] '
            'resource_group="rg"',
            "ACTION deleted cloud=azure resource=nic region=rg id=nic-a name=nic-a",
            "SKIPPED cloud=azure resource=nic reason=exception_regex count=1",
            "SKIPPED cloud=azure resource=nic reason=in_use count=1",
            "SKIPPED cloud=azure resource=nic reason=name_regex count=1",
            "RESULT cloud=azure resource=nic operation=delete "
            "dry_run=false selected=1 done=1 failed=0 skipped=3 "
            "regions=1 regions_skipped=0 duration_s=S",
        ],
        ["REGION cloud=azure resource=nic region=rg scanned=4 selected=1 seconds=S"],
    ),
    ("nic-delete", "plain", True): (
        [
            "RUN START cloud=azure resource=nic operation=delete "
            'dry_run=true name_regex=["^nic"] exception_regex=["keep"] '
            'resource_group="rg"',
            "ACTION would_delete cloud=azure resource=nic region=rg id=nic-a name=nic-a",
            "SKIPPED cloud=azure resource=nic reason=exception_regex count=1",
            "SKIPPED cloud=azure resource=nic reason=in_use count=1",
            "SKIPPED cloud=azure resource=nic reason=name_regex count=1",
            "RESULT cloud=azure resource=nic operation=delete "
            "dry_run=true selected=1 done=0 failed=0 skipped=3 regions=1 "
            "regions_skipped=0 duration_s=S",
        ],
        ["REGION cloud=azure resource=nic region=rg scanned=4 selected=1 seconds=S"],
    ),
    ("nic-delete", "empty", False): (
        [
            "RUN START cloud=azure resource=nic operation=delete "
            'dry_run=false name_regex=["^nic"] exception_regex=["keep"] '
            'resource_group="rg"',
            "RESULT cloud=azure resource=nic operation=delete "
            "dry_run=false selected=0 done=0 failed=0 skipped=0 "
            "regions=1 regions_skipped=0 duration_s=S",
        ],
        ["REGION cloud=azure resource=nic region=rg scanned=0 selected=0 seconds=S"],
    ),
    ("nic-delete", "empty", True): (
        [
            "RUN START cloud=azure resource=nic operation=delete "
            'dry_run=true name_regex=["^nic"] exception_regex=["keep"] '
            'resource_group="rg"',
            "RESULT cloud=azure resource=nic operation=delete "
            "dry_run=true selected=0 done=0 failed=0 skipped=0 regions=1 "
            "regions_skipped=0 duration_s=S",
        ],
        ["REGION cloud=azure resource=nic region=rg scanned=0 selected=0 seconds=S"],
    ),
    ("nic-delete", "list_error", False): (
        [
            "RUN START cloud=azure resource=nic operation=delete "
            'dry_run=false name_regex=["^nic"] '
            'exception_regex=["keep"] resource_group="rg"',
            "RESULT cloud=azure resource=nic operation=delete "
            "dry_run=false selected=0 done=0 failed=0 skipped=0 "
            "regions=0 regions_skipped=0 duration_s=S",
        ],
        [],
    ),
    ("nic-delete", "list_error", True): (
        [
            "RUN START cloud=azure resource=nic operation=delete "
            'dry_run=true name_regex=["^nic"] '
            'exception_regex=["keep"] resource_group="rg"',
            "RESULT cloud=azure resource=nic operation=delete "
            "dry_run=true selected=0 done=0 failed=0 skipped=0 "
            "regions=0 regions_skipped=0 duration_s=S",
        ],
        [],
    ),
    ("nic-delete", "delete_error", False): (
        [
            "RUN START cloud=azure resource=nic operation=delete "
            'dry_run=false name_regex=["^nic"] '
            'exception_regex=["keep"] resource_group="rg"',
            "SKIPPED cloud=azure resource=nic reason=exception_regex count=1",
            "SKIPPED cloud=azure resource=nic reason=in_use count=1",
            "SKIPPED cloud=azure resource=nic reason=name_regex count=1",
            "ERRORS count=1",
            "FAILED cloud=azure resource=nic region=rg id=nic-a error=HttpResponseError: boom",
            "RESULT cloud=azure resource=nic operation=delete "
            "dry_run=false selected=0 done=0 failed=1 skipped=3 "
            "regions=1 regions_skipped=0 duration_s=S",
        ],
        ["REGION cloud=azure resource=nic region=rg scanned=4 selected=1 seconds=S"],
    ),
    ("nic-delete", "delete_error", True): (
        [
            "RUN START cloud=azure resource=nic operation=delete "
            'dry_run=true name_regex=["^nic"] '
            'exception_regex=["keep"] resource_group="rg"',
            "ACTION would_delete cloud=azure resource=nic region=rg id=nic-a name=nic-a",
            "SKIPPED cloud=azure resource=nic reason=exception_regex count=1",
            "SKIPPED cloud=azure resource=nic reason=in_use count=1",
            "SKIPPED cloud=azure resource=nic reason=name_regex count=1",
            "RESULT cloud=azure resource=nic operation=delete "
            "dry_run=true selected=1 done=0 failed=0 skipped=3 "
            "regions=1 regions_skipped=0 duration_s=S",
        ],
        ["REGION cloud=azure resource=nic region=rg scanned=4 selected=1 seconds=S"],
    ),
    ("disk-delete", "plain", False): (
        [
            "RUN START cloud=azure resource=disk operation=delete "
            'dry_run=false age={"days":1} filter_tags={"env":["ci"]} '
            'notags={"owner":["x"]} resource_group="rg"',
            "ACTION deleted cloud=azure resource=disk region=rg id=a name=a",
            "ACTION deleted cloud=azure resource=disk region=rg id=d name=d",
            "SKIPPED cloud=azure resource=disk reason=filter_tags count=1",
            "SKIPPED cloud=azure resource=disk reason=notags count=1",
            "SKIPPED cloud=azure resource=disk reason=state count=1",
            "RESULT cloud=azure resource=disk operation=delete "
            "dry_run=false selected=2 done=2 failed=0 skipped=3 "
            "regions=1 regions_skipped=0 duration_s=S",
        ],
        ["REGION cloud=azure resource=disk region=rg scanned=5 selected=2 seconds=S"],
    ),
    ("disk-delete", "plain", True): (
        [
            "RUN START cloud=azure resource=disk operation=delete "
            'dry_run=true age={"days":1} filter_tags={"env":["ci"]} '
            'notags={"owner":["x"]} resource_group="rg"',
            "ACTION would_delete cloud=azure resource=disk region=rg id=a name=a",
            "ACTION would_delete cloud=azure resource=disk region=rg id=d name=d",
            "SKIPPED cloud=azure resource=disk reason=filter_tags count=1",
            "SKIPPED cloud=azure resource=disk reason=notags count=1",
            "SKIPPED cloud=azure resource=disk reason=state count=1",
            "RESULT cloud=azure resource=disk operation=delete "
            "dry_run=true selected=2 done=0 failed=0 skipped=3 "
            "regions=1 regions_skipped=0 duration_s=S",
        ],
        ["REGION cloud=azure resource=disk region=rg scanned=5 selected=2 seconds=S"],
    ),
    ("disk-delete", "empty", False): (
        [
            "RUN START cloud=azure resource=disk operation=delete "
            'dry_run=false age={"days":1} filter_tags={"env":["ci"]} '
            'notags={"owner":["x"]} resource_group="rg"',
            "RESULT cloud=azure resource=disk operation=delete "
            "dry_run=false selected=0 done=0 failed=0 skipped=0 "
            "regions=1 regions_skipped=0 duration_s=S",
        ],
        ["REGION cloud=azure resource=disk region=rg scanned=0 selected=0 seconds=S"],
    ),
    ("disk-delete", "empty", True): (
        [
            "RUN START cloud=azure resource=disk operation=delete "
            'dry_run=true age={"days":1} filter_tags={"env":["ci"]} '
            'notags={"owner":["x"]} resource_group="rg"',
            "RESULT cloud=azure resource=disk operation=delete "
            "dry_run=true selected=0 done=0 failed=0 skipped=0 "
            "regions=1 regions_skipped=0 duration_s=S",
        ],
        ["REGION cloud=azure resource=disk region=rg scanned=0 selected=0 seconds=S"],
    ),
    ("disk-delete", "list_error", False): (
        [
            "RUN START cloud=azure resource=disk operation=delete "
            'dry_run=false age={"days":1} '
            'filter_tags={"env":["ci"]} notags={"owner":["x"]} '
            'resource_group="rg"',
            "RESULT cloud=azure resource=disk operation=delete "
            "dry_run=false selected=0 done=0 failed=0 skipped=0 "
            "regions=0 regions_skipped=0 duration_s=S",
        ],
        [],
    ),
    ("disk-delete", "list_error", True): (
        [
            "RUN START cloud=azure resource=disk operation=delete "
            'dry_run=true age={"days":1} '
            'filter_tags={"env":["ci"]} notags={"owner":["x"]} '
            'resource_group="rg"',
            "RESULT cloud=azure resource=disk operation=delete "
            "dry_run=true selected=0 done=0 failed=0 skipped=0 "
            "regions=0 regions_skipped=0 duration_s=S",
        ],
        [],
    ),
    ("disk-delete", "delete_error", False): (
        [
            "RUN START cloud=azure resource=disk "
            'operation=delete dry_run=false age={"days":1} '
            'filter_tags={"env":["ci"]} notags={"owner":["x"]} '
            'resource_group="rg"',
            "ACTION deleted cloud=azure resource=disk region=rg id=a name=a",
            "SKIPPED cloud=azure resource=disk reason=filter_tags count=1",
            "SKIPPED cloud=azure resource=disk reason=notags count=1",
            "SKIPPED cloud=azure resource=disk reason=state count=1",
            "ERRORS count=1",
            "FAILED cloud=azure resource=disk region=rg id=d error=HttpResponseError: boom",
            "RESULT cloud=azure resource=disk operation=delete "
            "dry_run=false selected=1 done=1 failed=1 skipped=3 "
            "regions=1 regions_skipped=0 duration_s=S",
        ],
        ["REGION cloud=azure resource=disk region=rg scanned=5 selected=2 seconds=S"],
    ),
    ("disk-delete", "delete_error", True): (
        [
            "RUN START cloud=azure resource=disk "
            'operation=delete dry_run=true age={"days":1} '
            'filter_tags={"env":["ci"]} notags={"owner":["x"]} '
            'resource_group="rg"',
            "ACTION would_delete cloud=azure resource=disk region=rg id=a name=a",
            "ACTION would_delete cloud=azure resource=disk region=rg id=d name=d",
            "SKIPPED cloud=azure resource=disk reason=filter_tags count=1",
            "SKIPPED cloud=azure resource=disk reason=notags count=1",
            "SKIPPED cloud=azure resource=disk reason=state count=1",
            "RESULT cloud=azure resource=disk operation=delete "
            "dry_run=true selected=2 done=0 failed=0 skipped=3 "
            "regions=1 regions_skipped=0 duration_s=S",
        ],
        ["REGION cloud=azure resource=disk region=rg scanned=5 selected=2 seconds=S"],
    ),
    ("spot_instances-delete", "plain", False): (
        [
            "RUN START cloud=azure resource=spot_instances "
            'operation=delete dry_run=false age={"days":1} '
            'filter_tags={"yb_task":["itest"]} '
            'notags={"owner":["x"]} resource_group="rg"',
            "ACTION deleted cloud=azure resource=spot_instances region=rg id=s1 name=s1",
            "ACTION deleted cloud=azure resource=spot_instances region=rg id=s2 name=s2",
            "SKIPPED cloud=azure resource=spot_instances reason=exception_or_notags count=1",
            "SKIPPED cloud=azure resource=spot_instances reason=filter_tags count=1",
            "SKIPPED cloud=azure resource=spot_instances "
            "reason=leftover_disk_outside_resource_group "
            "count=2",
            "SKIPPED cloud=azure resource=spot_instances reason=not_spot count=1",
            "SKIPPED cloud=azure resource=spot_instances reason=state count=1",
            "RESULT cloud=azure resource=spot_instances "
            "operation=delete dry_run=false selected=2 "
            "done=2 failed=0 skipped=6 regions=1 "
            "regions_skipped=0 duration_s=S",
        ],
        ["REGION cloud=azure resource=spot_instances region=rg scanned=6 selected=2 seconds=S"],
    ),
    ("spot_instances-delete", "plain", True): (
        [
            "RUN START cloud=azure resource=spot_instances "
            'operation=delete dry_run=true age={"days":1} '
            'filter_tags={"yb_task":["itest"]} '
            'notags={"owner":["x"]} resource_group="rg"',
            "ACTION would_delete cloud=azure resource=spot_instances region=rg id=s1 name=s1",
            "ACTION would_delete cloud=azure resource=spot_instances region=rg id=s2 name=s2",
            "SKIPPED cloud=azure resource=spot_instances reason=exception_or_notags count=1",
            "SKIPPED cloud=azure resource=spot_instances reason=filter_tags count=1",
            "SKIPPED cloud=azure resource=spot_instances "
            "reason=leftover_disk_outside_resource_group "
            "count=2",
            "SKIPPED cloud=azure resource=spot_instances reason=not_spot count=1",
            "SKIPPED cloud=azure resource=spot_instances reason=state count=1",
            "RESULT cloud=azure resource=spot_instances "
            "operation=delete dry_run=true selected=2 done=0 "
            "failed=0 skipped=6 regions=1 regions_skipped=0 "
            "duration_s=S",
        ],
        ["REGION cloud=azure resource=spot_instances region=rg scanned=6 selected=2 seconds=S"],
    ),
    ("spot_instances-delete", "empty", False): (
        [
            "RUN START cloud=azure resource=spot_instances "
            'operation=delete dry_run=false age={"days":1} '
            'filter_tags={"yb_task":["itest"]} '
            'notags={"owner":["x"]} resource_group="rg"',
            "RESULT cloud=azure resource=spot_instances "
            "operation=delete dry_run=false selected=0 "
            "done=0 failed=0 skipped=0 regions=1 "
            "regions_skipped=0 duration_s=S",
        ],
        ["REGION cloud=azure resource=spot_instances region=rg scanned=0 selected=0 seconds=S"],
    ),
    ("spot_instances-delete", "empty", True): (
        [
            "RUN START cloud=azure resource=spot_instances "
            'operation=delete dry_run=true age={"days":1} '
            'filter_tags={"yb_task":["itest"]} '
            'notags={"owner":["x"]} resource_group="rg"',
            "RESULT cloud=azure resource=spot_instances "
            "operation=delete dry_run=true selected=0 done=0 "
            "failed=0 skipped=0 regions=1 regions_skipped=0 "
            "duration_s=S",
        ],
        ["REGION cloud=azure resource=spot_instances region=rg scanned=0 selected=0 seconds=S"],
    ),
    ("spot_instances-delete", "list_error", False): (
        [
            "RUN START cloud=azure "
            "resource=spot_instances operation=delete "
            'dry_run=false age={"days":1} '
            'filter_tags={"yb_task":["itest"]} '
            'notags={"owner":["x"]} resource_group="rg"',
            "RESULT cloud=azure resource=spot_instances "
            "operation=delete dry_run=false selected=0 "
            "done=0 failed=0 skipped=0 regions=0 "
            "regions_skipped=0 duration_s=S",
        ],
        [],
    ),
    ("spot_instances-delete", "list_error", True): (
        [
            "RUN START cloud=azure "
            "resource=spot_instances operation=delete "
            'dry_run=true age={"days":1} '
            'filter_tags={"yb_task":["itest"]} '
            'notags={"owner":["x"]} resource_group="rg"',
            "RESULT cloud=azure resource=spot_instances "
            "operation=delete dry_run=true selected=0 "
            "done=0 failed=0 skipped=0 regions=0 "
            "regions_skipped=0 duration_s=S",
        ],
        [],
    ),
    ("spot_instances-delete", "delete_error", False): (
        [
            "RUN START cloud=azure "
            "resource=spot_instances operation=delete "
            'dry_run=false age={"days":1} '
            'filter_tags={"yb_task":["itest"]} '
            'notags={"owner":["x"]} '
            'resource_group="rg"',
            "ACTION deleted cloud=azure resource=spot_instances region=rg id=s1 name=s1",
            "SKIPPED cloud=azure resource=spot_instances reason=exception_or_notags count=1",
            "SKIPPED cloud=azure resource=spot_instances reason=filter_tags count=1",
            "SKIPPED cloud=azure "
            "resource=spot_instances "
            "reason=leftover_disk_outside_resource_group "
            "count=2",
            "SKIPPED cloud=azure resource=spot_instances reason=not_spot count=1",
            "SKIPPED cloud=azure resource=spot_instances reason=state count=1",
            "ERRORS count=1",
            "FAILED cloud=azure "
            "resource=spot_instances region=rg id=s2 "
            "error=HttpResponseError: boom",
            "RESULT cloud=azure "
            "resource=spot_instances operation=delete "
            "dry_run=false selected=1 done=1 failed=1 "
            "skipped=6 regions=1 regions_skipped=0 "
            "duration_s=S",
        ],
        ["REGION cloud=azure resource=spot_instances region=rg scanned=6 selected=2 seconds=S"],
    ),
    ("spot_instances-delete", "delete_error", True): (
        [
            "RUN START cloud=azure "
            "resource=spot_instances operation=delete "
            'dry_run=true age={"days":1} '
            'filter_tags={"yb_task":["itest"]} '
            'notags={"owner":["x"]} '
            'resource_group="rg"',
            "ACTION would_delete cloud=azure resource=spot_instances region=rg id=s1 name=s1",
            "ACTION would_delete cloud=azure resource=spot_instances region=rg id=s2 name=s2",
            "SKIPPED cloud=azure resource=spot_instances reason=exception_or_notags count=1",
            "SKIPPED cloud=azure resource=spot_instances reason=filter_tags count=1",
            "SKIPPED cloud=azure "
            "resource=spot_instances "
            "reason=leftover_disk_outside_resource_group "
            "count=2",
            "SKIPPED cloud=azure resource=spot_instances reason=not_spot count=1",
            "SKIPPED cloud=azure resource=spot_instances reason=state count=1",
            "RESULT cloud=azure "
            "resource=spot_instances operation=delete "
            "dry_run=true selected=2 done=0 failed=0 "
            "skipped=6 regions=1 regions_skipped=0 "
            "duration_s=S",
        ],
        ["REGION cloud=azure resource=spot_instances region=rg scanned=6 selected=2 seconds=S"],
    ),
    ("spot_instances-delete-no_group", "empty", False): (
        [
            "RUN START cloud=azure "
            "resource=spot_instances "
            "operation=delete dry_run=false "
            'age={"days":1} '
            'filter_tags={"yb_task":["itest"]} '
            'notags={"owner":["x"]}',
            "RESULT cloud=azure "
            "resource=spot_instances "
            "operation=delete dry_run=false "
            "selected=0 done=0 failed=0 skipped=0 "
            "regions=0 regions_skipped=0 "
            "duration_s=S",
        ],
        [],
    ),
    ("spot_instances-delete-no_group", "empty", True): (
        [
            "RUN START cloud=azure "
            "resource=spot_instances "
            "operation=delete dry_run=true "
            'age={"days":1} '
            'filter_tags={"yb_task":["itest"]} '
            'notags={"owner":["x"]}',
            "RESULT cloud=azure "
            "resource=spot_instances "
            "operation=delete dry_run=true "
            "selected=0 done=0 failed=0 skipped=0 "
            "regions=0 regions_skipped=0 "
            "duration_s=S",
        ],
        [],
    ),
}

FIXED: dict[tuple[str, str, bool, str, str], dict[str, Any]] = {
    ("ip-delete", "plain", False, "off", "off"): {
        "result": ("ok",),
        "logs": [
            (20, "Deleted IP address: a"),
            (20, "Deleted IP address: e"),
            (30, "number of Azure IPs deleted: 2"),
            (30, "List of Azure IPs deleted: ['a', 'e']"),
        ],
        "mutations": [("begin_delete", "rg", "a"), ("begin_delete", "rg", "e")],
    },
    ("ip-delete", "plain", False, "on", "on"): {
        "result": ("ok",),
        "slack": [
            (
                "chat_postMessage",
                {
                    "channel": "#chan",
                    "text": "Deleted the following `2` azure IP(s):\n`['a', 'e']`",
                },
            )
        ],
        "influx": [
            ("write_api", {"write_options": "SYNCHRONOUS"}),
            (
                "write",
                (),
                {"bucket": "bkt", "record": "azure,resource=IP count=2i,names=\"['a', 'e']\""},
            ),
        ],
        "logs": [
            (20, "Deleted IP address: a"),
            (20, "Deleted IP address: e"),
            (30, "number of Azure IPs deleted: 2"),
            (30, "List of Azure IPs deleted: ['a', 'e']"),
        ],
        "mutations": [("begin_delete", "rg", "a"), ("begin_delete", "rg", "e")],
    },
    ("ip-delete", "plain", False, "post_fail", "write_error"): {
        "result": ("ok",),
        "slack": [
            (
                "chat_postMessage",
                {
                    "channel": "#chan",
                    "text": "Deleted the following `2` azure IP(s):\n`['a', 'e']`",
                },
            )
        ],
        "influx": [
            ("write_api", {"write_options": "SYNCHRONOUS"}),
            (
                "write",
                (),
                {"bucket": "bkt", "record": "azure,resource=IP count=2i,names=\"['a', 'e']\""},
            ),
        ],
        "stdout": "Failed to send message "
        "'Deleted the following "
        "`2` azure IP(s):\n"
        "`['a', 'e']`' to Slack "
        "channel 'chan': slack "
        "down\n"
        "Unable to push data to "
        "influxDB: write down\n",
        "logs": [
            (20, "Deleted IP address: a"),
            (20, "Deleted IP address: e"),
            (30, "number of Azure IPs deleted: 2"),
            (30, "List of Azure IPs deleted: ['a', 'e']"),
        ],
        "mutations": [("begin_delete", "rg", "a"), ("begin_delete", "rg", "e")],
    },
    ("ip-delete", "plain", False, "on", "api_error"): {
        "result": ("ok",),
        "slack": [
            (
                "chat_postMessage",
                {
                    "channel": "#chan",
                    "text": "Deleted the following `2` azure IP(s):\n`['a', 'e']`",
                },
            )
        ],
        "influx": [("write_api", {"write_options": "SYNCHRONOUS"})],
        "stdout": "Unable to push data to influxDB: api down\n",
        "logs": [
            (20, "Deleted IP address: a"),
            (20, "Deleted IP address: e"),
            (30, "number of Azure IPs deleted: 2"),
            (30, "List of Azure IPs deleted: ['a', 'e']"),
        ],
        "mutations": [("begin_delete", "rg", "a"), ("begin_delete", "rg", "e")],
    },
    ("ip-delete", "plain", False, "no_channel", "on_suffix"): {
        "result": ("ok",),
        "influx": [
            ("write_api", {"write_options": "SYNCHRONOUS"}),
            (
                "write",
                (),
                {"bucket": "bkt", "record": "azure,resource=IP_sfx count=2i,names=\"['a', 'e']\""},
            ),
        ],
        "stdout": "Failed to send message "
        "'Deleted the following "
        "`2` azure IP(s):\n"
        "`['a', 'e']`' to Slack "
        "channel 'None': can "
        "only concatenate str "
        '(not "NoneType") to '
        "str\n",
        "logs": [
            (20, "Deleted IP address: a"),
            (20, "Deleted IP address: e"),
            (30, "number of Azure IPs deleted: 2"),
            (30, "List of Azure IPs deleted: ['a', 'e']"),
        ],
        "mutations": [("begin_delete", "rg", "a"), ("begin_delete", "rg", "e")],
    },
    ("ip-delete", "plain", True, "off", "off"): {
        "result": ("ok",),
        "logs": [(30, "List of Azure IPs (Total: 2) which will be deleted: ['a', 'e']")],
    },
    ("ip-delete", "plain", True, "on", "on"): {
        "result": ("ok",),
        "slack": [
            (
                "chat_postMessage",
                {
                    "channel": "#chan",
                    "text": "`Dry Run`: Will be Deleted: `2` azure IP(s):\n`['a', 'e']`",
                },
            )
        ],
        "influx": [
            ("write_api", {"write_options": "SYNCHRONOUS"}),
            (
                "write",
                (),
                {"bucket": "bkt", "record": "azure,resource=IP count=2i,names=\"['a', 'e']\""},
            ),
        ],
        "logs": [(30, "List of Azure IPs (Total: 2) which will be deleted: ['a', 'e']")],
    },
    ("ip-delete", "plain", True, "post_fail", "write_error"): {
        "result": ("ok",),
        "slack": [
            (
                "chat_postMessage",
                {
                    "channel": "#chan",
                    "text": "`Dry Run`: Will be Deleted: `2` azure IP(s):\n`['a', 'e']`",
                },
            )
        ],
        "influx": [
            ("write_api", {"write_options": "SYNCHRONOUS"}),
            (
                "write",
                (),
                {"bucket": "bkt", "record": "azure,resource=IP count=2i,names=\"['a', 'e']\""},
            ),
        ],
        "stdout": "Failed to send message "
        "'`Dry Run`: Will be "
        "Deleted: `2` azure "
        "IP(s):\n"
        "`['a', 'e']`' to Slack "
        "channel 'chan': slack "
        "down\n"
        "Unable to push data to "
        "influxDB: write down\n",
        "logs": [(30, "List of Azure IPs (Total: 2) which will be deleted: ['a', 'e']")],
    },
    ("ip-delete", "plain", True, "on", "api_error"): {
        "result": ("ok",),
        "slack": [
            (
                "chat_postMessage",
                {
                    "channel": "#chan",
                    "text": "`Dry Run`: Will be Deleted: `2` azure IP(s):\n`['a', 'e']`",
                },
            )
        ],
        "influx": [("write_api", {"write_options": "SYNCHRONOUS"})],
        "stdout": "Unable to push data to influxDB: api down\n",
        "logs": [(30, "List of Azure IPs (Total: 2) which will be deleted: ['a', 'e']")],
    },
    ("ip-delete", "plain", True, "no_channel", "on_suffix"): {
        "result": ("ok",),
        "influx": [
            ("write_api", {"write_options": "SYNCHRONOUS"}),
            (
                "write",
                (),
                {"bucket": "bkt", "record": "azure,resource=IP_sfx count=2i,names=\"['a', 'e']\""},
            ),
        ],
        "stdout": "Failed to send message "
        "'`Dry Run`: Will be "
        "Deleted: `2` azure "
        "IP(s):\n"
        "`['a', 'e']`' to Slack "
        "channel 'None': can only "
        "concatenate str (not "
        '"NoneType") to str\n',
        "logs": [(30, "List of Azure IPs (Total: 2) which will be deleted: ['a', 'e']")],
    },
    ("ip-delete", "delete_error", False, "off", "off"): {
        "result": ("error", "HttpResponseError", "boom"),
        "mutations": [("begin_delete", "rg", "a"), ("begin_delete", "rg", "e")],
    },
    ("ip-delete", "delete_error", False, "on", "on"): {
        "result": ("error", "HttpResponseError", "boom"),
        "mutations": [("begin_delete", "rg", "a"), ("begin_delete", "rg", "e")],
    },
    ("ip-delete", "delete_error", False, "post_fail", "write_error"): {
        "result": ("error", "HttpResponseError", "boom"),
        "mutations": [("begin_delete", "rg", "a"), ("begin_delete", "rg", "e")],
    },
    ("ip-delete", "delete_error", False, "on", "api_error"): {
        "result": ("error", "HttpResponseError", "boom"),
        "mutations": [("begin_delete", "rg", "a"), ("begin_delete", "rg", "e")],
    },
    ("ip-delete", "delete_error", False, "no_channel", "on_suffix"): {
        "result": ("error", "HttpResponseError", "boom"),
        "mutations": [("begin_delete", "rg", "a"), ("begin_delete", "rg", "e")],
    },
    ("ip-delete", "delete_error", True, "off", "off"): {
        "result": ("ok",),
        "logs": [(30, "List of Azure IPs (Total: 2) which will be deleted: ['a', 'e']")],
    },
    ("ip-delete", "delete_error", True, "on", "on"): {
        "result": ("ok",),
        "slack": [
            (
                "chat_postMessage",
                {
                    "channel": "#chan",
                    "text": "`Dry Run`: Will be Deleted: `2` azure IP(s):\n`['a', 'e']`",
                },
            )
        ],
        "influx": [
            ("write_api", {"write_options": "SYNCHRONOUS"}),
            (
                "write",
                (),
                {"bucket": "bkt", "record": "azure,resource=IP count=2i,names=\"['a', 'e']\""},
            ),
        ],
        "logs": [(30, "List of Azure IPs (Total: 2) which will be deleted: ['a', 'e']")],
    },
    ("ip-delete", "delete_error", True, "post_fail", "write_error"): {
        "result": ("ok",),
        "slack": [
            (
                "chat_postMessage",
                {
                    "channel": "#chan",
                    "text": "`Dry Run`: Will be Deleted: `2` azure IP(s):\n`['a', 'e']`",
                },
            )
        ],
        "influx": [
            ("write_api", {"write_options": "SYNCHRONOUS"}),
            (
                "write",
                (),
                {"bucket": "bkt", "record": "azure,resource=IP count=2i,names=\"['a', 'e']\""},
            ),
        ],
        "stdout": "Failed to send "
        "message '`Dry "
        "Run`: Will be "
        "Deleted: `2` "
        "azure IP(s):\n"
        "`['a', 'e']`' to "
        "Slack channel "
        "'chan': slack "
        "down\n"
        "Unable to push "
        "data to "
        "influxDB: write "
        "down\n",
        "logs": [(30, "List of Azure IPs (Total: 2) which will be deleted: ['a', 'e']")],
    },
    ("ip-delete", "delete_error", True, "on", "api_error"): {
        "result": ("ok",),
        "slack": [
            (
                "chat_postMessage",
                {
                    "channel": "#chan",
                    "text": "`Dry Run`: Will be Deleted: `2` azure IP(s):\n`['a', 'e']`",
                },
            )
        ],
        "influx": [("write_api", {"write_options": "SYNCHRONOUS"})],
        "stdout": "Unable to push data to influxDB: api down\n",
        "logs": [(30, "List of Azure IPs (Total: 2) which will be deleted: ['a', 'e']")],
    },
    ("ip-delete", "delete_error", True, "no_channel", "on_suffix"): {
        "result": ("ok",),
        "influx": [
            ("write_api", {"write_options": "SYNCHRONOUS"}),
            (
                "write",
                (),
                {"bucket": "bkt", "record": "azure,resource=IP_sfx count=2i,names=\"['a', 'e']\""},
            ),
        ],
        "stdout": "Failed to send "
        "message '`Dry "
        "Run`: Will be "
        "Deleted: `2` "
        "azure IP(s):\n"
        "`['a', 'e']`' to "
        "Slack channel "
        "'None': can only "
        "concatenate str "
        '(not "NoneType") '
        "to str\n",
        "logs": [(30, "List of Azure IPs (Total: 2) which will be deleted: ['a', 'e']")],
    },
    ("nic-delete", "plain", False, "off", "off"): {
        "logs": [
            (20, "Deleted NIC: nic-a"),
            (30, "Number of Azure NICs deleted: 1"),
            (30, "List of Azure NICs deleted: ['nic-a']"),
        ],
        "mutations": [("begin_delete", "rg", "nic-a")],
    },
    ("nic-delete", "plain", False, "on", "on"): {
        "slack": [
            (
                "chat_postMessage",
                {
                    "channel": "#chan",
                    "text": "Deleted the following `1` azure NIC(s):\n`['nic-a']`",
                },
            )
        ],
        "influx": [
            ("write_api", {"write_options": "SYNCHRONOUS"}),
            (
                "write",
                (),
                {"bucket": "bkt", "record": "azure,resource=NIC count=1i,names=\"['nic-a']\""},
            ),
        ],
        "logs": [
            (20, "Deleted NIC: nic-a"),
            (30, "Number of Azure NICs deleted: 1"),
            (30, "List of Azure NICs deleted: ['nic-a']"),
        ],
        "mutations": [("begin_delete", "rg", "nic-a")],
    },
    ("nic-delete", "plain", False, "post_fail", "write_error"): {
        "slack": [
            (
                "chat_postMessage",
                {
                    "channel": "#chan",
                    "text": "Deleted the following `1` azure NIC(s):\n`['nic-a']`",
                },
            )
        ],
        "influx": [
            ("write_api", {"write_options": "SYNCHRONOUS"}),
            (
                "write",
                (),
                {"bucket": "bkt", "record": "azure,resource=NIC count=1i,names=\"['nic-a']\""},
            ),
        ],
        "stdout": "Failed to send "
        "message 'Deleted the "
        "following `1` azure "
        "NIC(s):\n"
        "`['nic-a']`' to Slack "
        "channel 'chan': slack "
        "down\n"
        "Unable to push data "
        "to influxDB: write "
        "down\n",
        "logs": [
            (20, "Deleted NIC: nic-a"),
            (30, "Number of Azure NICs deleted: 1"),
            (30, "List of Azure NICs deleted: ['nic-a']"),
        ],
        "mutations": [("begin_delete", "rg", "nic-a")],
    },
    ("nic-delete", "plain", False, "on", "api_error"): {
        "slack": [
            (
                "chat_postMessage",
                {
                    "channel": "#chan",
                    "text": "Deleted the following `1` azure NIC(s):\n`['nic-a']`",
                },
            )
        ],
        "logs": [
            (20, "Deleted NIC: nic-a"),
            (30, "Number of Azure NICs deleted: 1"),
            (30, "List of Azure NICs deleted: ['nic-a']"),
        ],
        "mutations": [("begin_delete", "rg", "nic-a")],
    },
    ("nic-delete", "plain", False, "no_channel", "on_suffix"): {
        "influx": [
            ("write_api", {"write_options": "SYNCHRONOUS"}),
            (
                "write",
                (),
                {"bucket": "bkt", "record": "azure,resource=NIC_sfx count=1i,names=\"['nic-a']\""},
            ),
        ],
        "stdout": "Failed to send message "
        "'Deleted the following "
        "`1` azure NIC(s):\n"
        "`['nic-a']`' to Slack "
        "channel 'None': can "
        "only concatenate str "
        '(not "NoneType") to '
        "str\n",
        "logs": [
            (20, "Deleted NIC: nic-a"),
            (30, "Number of Azure NICs deleted: 1"),
            (30, "List of Azure NICs deleted: ['nic-a']"),
        ],
        "mutations": [("begin_delete", "rg", "nic-a")],
    },
    ("nic-delete", "plain", True, "off", "off"): {
        "logs": [(30, "List of Azure NICs (Total: 1) which will be deleted: ['nic-a']")]
    },
    ("nic-delete", "plain", True, "on", "on"): {
        "slack": [
            (
                "chat_postMessage",
                {
                    "channel": "#chan",
                    "text": "`Dry Run`: Will be Deleted: `1` azure NIC(s):\n`['nic-a']`",
                },
            )
        ],
        "influx": [
            ("write_api", {"write_options": "SYNCHRONOUS"}),
            (
                "write",
                (),
                {"bucket": "bkt", "record": "azure,resource=NIC count=1i,names=\"['nic-a']\""},
            ),
        ],
        "logs": [(30, "List of Azure NICs (Total: 1) which will be deleted: ['nic-a']")],
    },
    ("nic-delete", "plain", True, "post_fail", "write_error"): {
        "slack": [
            (
                "chat_postMessage",
                {
                    "channel": "#chan",
                    "text": "`Dry Run`: Will be Deleted: `1` azure NIC(s):\n`['nic-a']`",
                },
            )
        ],
        "influx": [
            ("write_api", {"write_options": "SYNCHRONOUS"}),
            (
                "write",
                (),
                {"bucket": "bkt", "record": "azure,resource=NIC count=1i,names=\"['nic-a']\""},
            ),
        ],
        "stdout": "Failed to send message "
        "'`Dry Run`: Will be "
        "Deleted: `1` azure "
        "NIC(s):\n"
        "`['nic-a']`' to Slack "
        "channel 'chan': slack "
        "down\n"
        "Unable to push data to "
        "influxDB: write down\n",
        "logs": [(30, "List of Azure NICs (Total: 1) which will be deleted: ['nic-a']")],
    },
    ("nic-delete", "plain", True, "on", "api_error"): {
        "slack": [
            (
                "chat_postMessage",
                {
                    "channel": "#chan",
                    "text": "`Dry Run`: Will be Deleted: `1` azure NIC(s):\n`['nic-a']`",
                },
            )
        ],
        "logs": [(30, "List of Azure NICs (Total: 1) which will be deleted: ['nic-a']")],
    },
    ("nic-delete", "plain", True, "no_channel", "on_suffix"): {
        "influx": [
            ("write_api", {"write_options": "SYNCHRONOUS"}),
            (
                "write",
                (),
                {"bucket": "bkt", "record": "azure,resource=NIC_sfx count=1i,names=\"['nic-a']\""},
            ),
        ],
        "stdout": "Failed to send message "
        "'`Dry Run`: Will be "
        "Deleted: `1` azure "
        "NIC(s):\n"
        "`['nic-a']`' to Slack "
        "channel 'None': can "
        "only concatenate str "
        '(not "NoneType") to '
        "str\n",
        "logs": [(30, "List of Azure NICs (Total: 1) which will be deleted: ['nic-a']")],
    },
    ("nic-delete", "delete_error", False, "off", "off"): {
        "logs": [
            (40, "Error occurred while deleting NIC nic-a: boom"),
            (20, "Retrying deletion of NIC nic-a"),
            (40, "Error occurred while deleting NIC nic-a: boom"),
            (20, "Retrying deletion of NIC nic-a"),
            (40, "Error occurred while deleting NIC nic-a: boom"),
            (40, "Failed to delete the NIC - nic-a"),
            (30, "Number of Azure NICs deleted: 0"),
            (30, "List of Azure NICs deleted: []"),
        ],
        "mutations": [
            ("begin_delete", "rg", "nic-a"),
            ("begin_delete", "rg", "nic-a"),
            ("begin_delete", "rg", "nic-a"),
        ],
    },
    ("nic-delete", "delete_error", False, "on", "on"): {
        "logs": [
            (40, "Error occurred while deleting NIC nic-a: boom"),
            (20, "Retrying deletion of NIC nic-a"),
            (40, "Error occurred while deleting NIC nic-a: boom"),
            (20, "Retrying deletion of NIC nic-a"),
            (40, "Error occurred while deleting NIC nic-a: boom"),
            (40, "Failed to delete the NIC - nic-a"),
            (30, "Number of Azure NICs deleted: 0"),
            (30, "List of Azure NICs deleted: []"),
        ],
        "mutations": [
            ("begin_delete", "rg", "nic-a"),
            ("begin_delete", "rg", "nic-a"),
            ("begin_delete", "rg", "nic-a"),
        ],
    },
    ("nic-delete", "delete_error", False, "post_fail", "write_error"): {
        "logs": [
            (40, "Error occurred while deleting NIC nic-a: boom"),
            (20, "Retrying deletion of NIC nic-a"),
            (40, "Error occurred while deleting NIC nic-a: boom"),
            (20, "Retrying deletion of NIC nic-a"),
            (40, "Error occurred while deleting NIC nic-a: boom"),
            (40, "Failed to delete the NIC - nic-a"),
            (30, "Number of Azure NICs deleted: 0"),
            (30, "List of Azure NICs deleted: []"),
        ],
        "mutations": [
            ("begin_delete", "rg", "nic-a"),
            ("begin_delete", "rg", "nic-a"),
            ("begin_delete", "rg", "nic-a"),
        ],
    },
    ("nic-delete", "delete_error", False, "on", "api_error"): {
        "logs": [
            (40, "Error occurred while deleting NIC nic-a: boom"),
            (20, "Retrying deletion of NIC nic-a"),
            (40, "Error occurred while deleting NIC nic-a: boom"),
            (20, "Retrying deletion of NIC nic-a"),
            (40, "Error occurred while deleting NIC nic-a: boom"),
            (40, "Failed to delete the NIC - nic-a"),
            (30, "Number of Azure NICs deleted: 0"),
            (30, "List of Azure NICs deleted: []"),
        ],
        "mutations": [
            ("begin_delete", "rg", "nic-a"),
            ("begin_delete", "rg", "nic-a"),
            ("begin_delete", "rg", "nic-a"),
        ],
    },
    ("nic-delete", "delete_error", False, "no_channel", "on_suffix"): {
        "logs": [
            (40, "Error occurred while deleting NIC nic-a: boom"),
            (20, "Retrying deletion of NIC nic-a"),
            (40, "Error occurred while deleting NIC nic-a: boom"),
            (20, "Retrying deletion of NIC nic-a"),
            (40, "Error occurred while deleting NIC nic-a: boom"),
            (40, "Failed to delete the NIC - nic-a"),
            (30, "Number of Azure NICs deleted: 0"),
            (30, "List of Azure NICs deleted: []"),
        ],
        "mutations": [
            ("begin_delete", "rg", "nic-a"),
            ("begin_delete", "rg", "nic-a"),
            ("begin_delete", "rg", "nic-a"),
        ],
    },
    ("nic-delete", "delete_error", True, "off", "off"): {
        "logs": [(30, "List of Azure NICs (Total: 1) which will be deleted: ['nic-a']")]
    },
    ("nic-delete", "delete_error", True, "on", "on"): {
        "slack": [
            (
                "chat_postMessage",
                {
                    "channel": "#chan",
                    "text": "`Dry Run`: Will be Deleted: `1` azure NIC(s):\n`['nic-a']`",
                },
            )
        ],
        "influx": [
            ("write_api", {"write_options": "SYNCHRONOUS"}),
            (
                "write",
                (),
                {"bucket": "bkt", "record": "azure,resource=NIC count=1i,names=\"['nic-a']\""},
            ),
        ],
        "logs": [(30, "List of Azure NICs (Total: 1) which will be deleted: ['nic-a']")],
    },
    ("nic-delete", "delete_error", True, "post_fail", "write_error"): {
        "slack": [
            (
                "chat_postMessage",
                {
                    "channel": "#chan",
                    "text": "`Dry Run`: Will be Deleted: `1` azure NIC(s):\n`['nic-a']`",
                },
            )
        ],
        "influx": [
            ("write_api", {"write_options": "SYNCHRONOUS"}),
            (
                "write",
                (),
                {"bucket": "bkt", "record": "azure,resource=NIC count=1i,names=\"['nic-a']\""},
            ),
        ],
        "stdout": "Failed to send "
        "message '`Dry "
        "Run`: Will be "
        "Deleted: `1` "
        "azure NIC(s):\n"
        "`['nic-a']`' to "
        "Slack channel "
        "'chan': slack "
        "down\n"
        "Unable to push "
        "data to "
        "influxDB: write "
        "down\n",
        "logs": [(30, "List of Azure NICs (Total: 1) which will be deleted: ['nic-a']")],
    },
    ("nic-delete", "delete_error", True, "on", "api_error"): {
        "slack": [
            (
                "chat_postMessage",
                {
                    "channel": "#chan",
                    "text": "`Dry Run`: Will be Deleted: `1` azure NIC(s):\n`['nic-a']`",
                },
            )
        ],
        "logs": [(30, "List of Azure NICs (Total: 1) which will be deleted: ['nic-a']")],
    },
    ("nic-delete", "delete_error", True, "no_channel", "on_suffix"): {
        "influx": [
            ("write_api", {"write_options": "SYNCHRONOUS"}),
            (
                "write",
                (),
                {"bucket": "bkt", "record": "azure,resource=NIC_sfx count=1i,names=\"['nic-a']\""},
            ),
        ],
        "stdout": "Failed to send "
        "message '`Dry "
        "Run`: Will be "
        "Deleted: `1` "
        "azure NIC(s):\n"
        "`['nic-a']`' to "
        "Slack channel "
        "'None': can only "
        "concatenate str "
        '(not "NoneType") '
        "to str\n",
        "logs": [(30, "List of Azure NICs (Total: 1) which will be deleted: ['nic-a']")],
    },
    ("disk-delete", "delete_error", False, "off", "off"): {
        "result": ("ok",),
        "logs": [
            (20, "Deleted disk: a"),
            (40, "Error occurred while deleting disk d: boom"),
            (30, "number of Azure Disks deleted: 1"),
            (30, "List of Azure Disks deleted: ['a']"),
        ],
    },
    ("disk-delete", "delete_error", False, "on", "on"): {
        "result": ("ok",),
        "logs": [
            (20, "Deleted disk: a"),
            (40, "Error occurred while deleting disk d: boom"),
            (30, "number of Azure Disks deleted: 1"),
            (30, "List of Azure Disks deleted: ['a']"),
        ],
    },
    ("disk-delete", "delete_error", False, "post_fail", "write_error"): {
        "result": ("ok",),
        "logs": [
            (20, "Deleted disk: a"),
            (40, "Error occurred while deleting disk d: boom"),
            (30, "number of Azure Disks deleted: 1"),
            (30, "List of Azure Disks deleted: ['a']"),
        ],
    },
    ("disk-delete", "delete_error", False, "on", "api_error"): {
        "result": ("ok",),
        "logs": [
            (20, "Deleted disk: a"),
            (40, "Error occurred while deleting disk d: boom"),
            (30, "number of Azure Disks deleted: 1"),
            (30, "List of Azure Disks deleted: ['a']"),
        ],
    },
    ("disk-delete", "delete_error", False, "no_channel", "on_suffix"): {
        "result": ("ok",),
        "logs": [
            (20, "Deleted disk: a"),
            (40, "Error occurred while deleting disk d: boom"),
            (30, "number of Azure Disks deleted: 1"),
            (30, "List of Azure Disks deleted: ['a']"),
        ],
    },
}
