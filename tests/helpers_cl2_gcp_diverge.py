# ruff: noqa: E501 - frozen log lines
"""Where `main`'s frozen CLI and GCP adapters differ from the routed run because of a bug fixed
since (docs/refactor/BUGS.md rows "fixed in ..."): per case, the BUG ids, the outcome fields that
differ, and the values the routed run produces, frozen from the legacy run (not from the
routed run). The test asserts that exactly these fields differ from the oracle and that the routed
run equals the frozen values.

Key: (case, dry_run, slack, influx, scenario) as in `tests/helpers_cl2_gcp_runlog.py`.
"""

from typing import Any

DIVERGENCES: dict[tuple[str, bool, bool, bool, str], tuple[tuple[str, ...], dict[str, Any]]] = {
    ("ip", False, False, False, ""): (
        ("BUG-001",),
        {
            "gcp_calls": [
                ("delete_address", "r1", "a-1"),
                ("delete_address", "r1", "a-2"),
                ("delete_address", "r2", "a-3"),
                ("list_addresses", "r1"),
                ("list_addresses", "r2"),
            ],
        },
    ),
    ("ip", False, False, True, ""): (
        ("BUG-001",),
        {
            "influx": [
                ("b", "gcp", {"resource": "IP"}, {"names": "['a-1', 'a-2', 'a-3']", "count": 3})
            ],
            "gcp_calls": [
                ("delete_address", "r1", "a-1"),
                ("delete_address", "r1", "a-2"),
                ("delete_address", "r2", "a-3"),
                ("list_addresses", "r1"),
                ("list_addresses", "r2"),
            ],
        },
    ),
    ("ip", False, True, False, ""): (
        ("BUG-001",),
        {
            "slack": [
                (
                    "chat_postMessage",
                    {
                        "channel": "#chan",
                        "text": "Deleted the following `3` gcp IP(s):\n`['a-1', 'a-2', 'a-3']`",
                    },
                )
            ],
            "gcp_calls": [
                ("delete_address", "r1", "a-1"),
                ("delete_address", "r1", "a-2"),
                ("delete_address", "r2", "a-3"),
                ("list_addresses", "r1"),
                ("list_addresses", "r2"),
            ],
        },
    ),
    ("ip", False, True, True, ""): (
        ("BUG-001",),
        {
            "slack": [
                (
                    "chat_postMessage",
                    {
                        "channel": "#chan",
                        "text": "Deleted the following `3` gcp IP(s):\n`['a-1', 'a-2', 'a-3']`",
                    },
                )
            ],
            "influx": [
                ("b", "gcp", {"resource": "IP"}, {"names": "['a-1', 'a-2', 'a-3']", "count": 3})
            ],
            "gcp_calls": [
                ("delete_address", "r1", "a-1"),
                ("delete_address", "r1", "a-2"),
                ("delete_address", "r2", "a-3"),
                ("list_addresses", "r1"),
                ("list_addresses", "r2"),
            ],
        },
    ),
    ("ip", True, False, True, ""): (
        ("BUG-001",),
        {
            "influx": [
                ("b", "gcp", {"resource": "IP"}, {"names": "['a-1', 'a-2', 'a-3']", "count": 3})
            ],
        },
    ),
    ("ip", True, True, False, ""): (
        ("BUG-001",),
        {
            "slack": [
                (
                    "chat_postMessage",
                    {
                        "channel": "#chan",
                        "text": "`Dry Run`: Will be Deleted: `3` gcp IP(s):\n`['a-1', 'a-2', 'a-3']`",
                    },
                )
            ],
        },
    ),
    ("ip", True, True, True, ""): (
        ("BUG-001",),
        {
            "slack": [
                (
                    "chat_postMessage",
                    {
                        "channel": "#chan",
                        "text": "`Dry Run`: Will be Deleted: `3` gcp IP(s):\n`['a-1', 'a-2', 'a-3']`",
                    },
                )
            ],
            "influx": [
                ("b", "gcp", {"resource": "IP"}, {"names": "['a-1', 'a-2', 'a-3']", "count": 3})
            ],
        },
    ),
    ("disk", False, False, False, ""): (
        ("BUG-005", "BUG-006"),
        {
            "gcp_calls": [
                ("delete_disk", "z1", "d1"),
                ("delete_disk", "z2", "d4"),
                ("list_disks", "z1"),
                ("list_disks", "z2"),
            ],
        },
    ),
    ("disk", False, False, True, ""): (
        ("BUG-005", "BUG-006"),
        {
            "influx": [("b", "gcp", {"resource": "Disk"}, {"names": "['d1', 'd4']", "count": 2})],
            "gcp_calls": [
                ("delete_disk", "z1", "d1"),
                ("delete_disk", "z2", "d4"),
                ("list_disks", "z1"),
                ("list_disks", "z2"),
            ],
        },
    ),
    ("disk", False, True, False, ""): (
        ("BUG-005", "BUG-006"),
        {
            "slack": [
                (
                    "chat_postMessage",
                    {
                        "channel": "#chan",
                        "text": "Deleted the following `2` gcp Disk(s):\n`['d1', 'd4']`",
                        "link_names": True,
                    },
                )
            ],
            "gcp_calls": [
                ("delete_disk", "z1", "d1"),
                ("delete_disk", "z2", "d4"),
                ("list_disks", "z1"),
                ("list_disks", "z2"),
            ],
        },
    ),
    ("disk", False, True, True, ""): (
        ("BUG-005", "BUG-006"),
        {
            "slack": [
                (
                    "chat_postMessage",
                    {
                        "channel": "#chan",
                        "text": "Deleted the following `2` gcp Disk(s):\n`['d1', 'd4']`",
                        "link_names": True,
                    },
                )
            ],
            "influx": [("b", "gcp", {"resource": "Disk"}, {"names": "['d1', 'd4']", "count": 2})],
            "gcp_calls": [
                ("delete_disk", "z1", "d1"),
                ("delete_disk", "z2", "d4"),
                ("list_disks", "z1"),
                ("list_disks", "z2"),
            ],
        },
    ),
    ("disk", True, False, True, ""): (
        ("BUG-005", "BUG-006"),
        {
            "influx": [("b", "gcp", {"resource": "Disk"}, {"names": "['d1', 'd4']", "count": 2})],
        },
    ),
    ("disk", True, True, False, ""): (
        ("BUG-005", "BUG-006"),
        {
            "slack": [
                (
                    "chat_postMessage",
                    {
                        "channel": "#chan",
                        "text": "`Dry Run`: Will be Deleted: `2` gcp Disk(s):\n`['d1', 'd4']`",
                        "link_names": True,
                    },
                )
            ],
        },
    ),
    ("disk", True, True, True, ""): (
        ("BUG-005", "BUG-006"),
        {
            "slack": [
                (
                    "chat_postMessage",
                    {
                        "channel": "#chan",
                        "text": "`Dry Run`: Will be Deleted: `2` gcp Disk(s):\n`['d1', 'd4']`",
                        "link_names": True,
                    },
                )
            ],
            "influx": [("b", "gcp", {"resource": "Disk"}, {"names": "['d1', 'd4']", "count": 2})],
        },
    ),
    ("disk-owners", False, False, False, ""): (
        ("BUG-005",),
        {
            "gcp_calls": [
                ("delete_disk", "z1", "d1"),
                ("delete_disk", "z2", "d4"),
                ("delete_disk", "z2", "e5"),
                ("list_disks", "z1"),
                ("list_disks", "z2"),
            ],
        },
    ),
    ("disk-owners", False, False, True, ""): (
        ("BUG-005",),
        {
            "influx": [
                (
                    "b",
                    "gcp",
                    {"resource": "Disk"},
                    {
                        "names": "{'alice@yugabyte.com': ['d1', 'd4'], 'not_tagged': ['e5']}",
                        "count": 2,
                    },
                )
            ],
            "gcp_calls": [
                ("delete_disk", "z1", "d1"),
                ("delete_disk", "z2", "d4"),
                ("delete_disk", "z2", "e5"),
                ("list_disks", "z1"),
                ("list_disks", "z2"),
            ],
        },
    ),
    ("disk-owners", False, True, False, ""): (
        ("BUG-005",),
        {
            "slack": [
                ("usergroups_list",),
                ("users_lookupByEmail", "alice@yugabyte.com@yugabyte.com"),
                ("conversations_open", ["U1"]),
                (
                    "chat_postMessage",
                    {
                        "channel": "D1",
                        "text": "Deleted the following`2` gcp Disk(s):\n<@U1> disks `['d1', 'd4']`\n\n",
                        "link_names": True,
                    },
                ),
                ("users_lookupByEmail", "not_tagged@yugabyte.com"),
                ("conversations_open", ["U1"]),
                (
                    "chat_postMessage",
                    {
                        "channel": "D1",
                        "text": "Deleted the following`1` gcp Disk(s):\n<@U1> disks `['e5']`\n\n",
                        "link_names": True,
                    },
                ),
            ],
            "gcp_calls": [
                ("delete_disk", "z1", "d1"),
                ("delete_disk", "z2", "d4"),
                ("delete_disk", "z2", "e5"),
                ("list_disks", "z1"),
                ("list_disks", "z2"),
            ],
        },
    ),
    ("disk-owners", False, True, True, ""): (
        ("BUG-005",),
        {
            "slack": [
                ("usergroups_list",),
                ("users_lookupByEmail", "alice@yugabyte.com@yugabyte.com"),
                ("conversations_open", ["U1"]),
                (
                    "chat_postMessage",
                    {
                        "channel": "D1",
                        "text": "Deleted the following`2` gcp Disk(s):\n<@U1> disks `['d1', 'd4']`\n\n",
                        "link_names": True,
                    },
                ),
                ("users_lookupByEmail", "not_tagged@yugabyte.com"),
                ("conversations_open", ["U1"]),
                (
                    "chat_postMessage",
                    {
                        "channel": "D1",
                        "text": "Deleted the following`1` gcp Disk(s):\n<@U1> disks `['e5']`\n\n",
                        "link_names": True,
                    },
                ),
            ],
            "influx": [
                (
                    "b",
                    "gcp",
                    {"resource": "Disk"},
                    {
                        "names": "{'alice@yugabyte.com': ['d1', 'd4'], 'not_tagged': ['e5']}",
                        "count": 2,
                    },
                )
            ],
            "gcp_calls": [
                ("delete_disk", "z1", "d1"),
                ("delete_disk", "z2", "d4"),
                ("delete_disk", "z2", "e5"),
                ("list_disks", "z1"),
                ("list_disks", "z2"),
            ],
        },
    ),
    ("disk-owners", True, False, True, ""): (
        ("BUG-005",),
        {
            "influx": [
                (
                    "b",
                    "gcp",
                    {"resource": "Disk"},
                    {
                        "names": "{'alice@yugabyte.com': ['d1', 'd4'], 'not_tagged': ['e5']}",
                        "count": 2,
                    },
                )
            ],
        },
    ),
    ("disk-owners", True, True, False, ""): (
        ("BUG-005",),
        {
            "slack": [
                ("usergroups_list",),
                ("users_lookupByEmail", "alice@yugabyte.com@yugabyte.com"),
                ("conversations_open", ["U1"]),
                (
                    "chat_postMessage",
                    {
                        "channel": "D1",
                        "text": "`Dry Run`: Will be Deleted:`2` gcp Disk(s):\n<@U1> disks `['d1', 'd4']`\n\n",
                        "link_names": True,
                    },
                ),
                ("users_lookupByEmail", "not_tagged@yugabyte.com"),
                ("conversations_open", ["U1"]),
                (
                    "chat_postMessage",
                    {
                        "channel": "D1",
                        "text": "`Dry Run`: Will be Deleted:`1` gcp Disk(s):\n<@U1> disks `['e5']`\n\n",
                        "link_names": True,
                    },
                ),
            ],
        },
    ),
    ("disk-owners", True, True, True, ""): (
        ("BUG-005",),
        {
            "slack": [
                ("usergroups_list",),
                ("users_lookupByEmail", "alice@yugabyte.com@yugabyte.com"),
                ("conversations_open", ["U1"]),
                (
                    "chat_postMessage",
                    {
                        "channel": "D1",
                        "text": "`Dry Run`: Will be Deleted:`2` gcp Disk(s):\n<@U1> disks `['d1', 'd4']`\n\n",
                        "link_names": True,
                    },
                ),
                ("users_lookupByEmail", "not_tagged@yugabyte.com"),
                ("conversations_open", ["U1"]),
                (
                    "chat_postMessage",
                    {
                        "channel": "D1",
                        "text": "`Dry Run`: Will be Deleted:`1` gcp Disk(s):\n<@U1> disks `['e5']`\n\n",
                        "link_names": True,
                    },
                ),
            ],
            "influx": [
                (
                    "b",
                    "gcp",
                    {"resource": "Disk"},
                    {
                        "names": "{'alice@yugabyte.com': ['d1', 'd4'], 'not_tagged': ['e5']}",
                        "count": 2,
                    },
                )
            ],
        },
    ),
    ("ip", False, True, True, "fail_delete:a-2"): (
        ("BUG-001",),
        {
            "slack": [
                (
                    "chat_postMessage",
                    {
                        "channel": "#chan",
                        "text": "Deleted the following `2` gcp IP(s):\n`['a-1', 'a-3']`",
                    },
                )
            ],
            "influx": [("b", "gcp", {"resource": "IP"}, {"names": "['a-1', 'a-3']", "count": 2})],
            "gcp_calls": [
                ("delete_address", "r1", "a-1"),
                ("delete_address", "r1", "a-2"),
                ("delete_address", "r2", "a-3"),
                ("list_addresses", "r1"),
                ("list_addresses", "r2"),
            ],
        },
    ),
    ("ip", True, True, True, "fail_delete:a-2"): (
        ("BUG-001",),
        {
            "slack": [
                (
                    "chat_postMessage",
                    {
                        "channel": "#chan",
                        "text": "`Dry Run`: Will be Deleted: `3` gcp IP(s):\n`['a-1', 'a-2', 'a-3']`",
                    },
                )
            ],
            "influx": [
                ("b", "gcp", {"resource": "IP"}, {"names": "['a-1', 'a-2', 'a-3']", "count": 3})
            ],
        },
    ),
    ("disk", False, True, True, "fail_delete:d1"): (
        ("BUG-005", "BUG-006"),
        {
            "slack": [
                (
                    "chat_postMessage",
                    {
                        "channel": "#chan",
                        "text": "Deleted the following `1` gcp Disk(s):\n`['d4']`",
                        "link_names": True,
                    },
                )
            ],
            "influx": [("b", "gcp", {"resource": "Disk"}, {"names": "['d4']", "count": 1})],
            "gcp_calls": [
                ("delete_disk", "z1", "d1"),
                ("delete_disk", "z2", "d4"),
                ("list_disks", "z1"),
                ("list_disks", "z2"),
            ],
        },
    ),
    ("disk", True, True, True, "fail_delete:d1"): (
        ("BUG-005", "BUG-006"),
        {
            "slack": [
                (
                    "chat_postMessage",
                    {
                        "channel": "#chan",
                        "text": "`Dry Run`: Will be Deleted: `2` gcp Disk(s):\n`['d1', 'd4']`",
                        "link_names": True,
                    },
                )
            ],
            "influx": [("b", "gcp", {"resource": "Disk"}, {"names": "['d1', 'd4']", "count": 2})],
        },
    ),
    ("disk", False, True, True, "fail_list_first"): (
        ("BUG-005", "BUG-006", "BUG-127"),
        {
            "slack": [
                (
                    "chat_postMessage",
                    {
                        "channel": "#chan",
                        "text": "Deleted the following `1` gcp Disk(s):\n`['d4']`",
                        "link_names": True,
                    },
                )
            ],
            "influx": [("b", "gcp", {"resource": "Disk"}, {"names": "['d4']", "count": 1})],
            "error": None,
            "gcp_calls": [("delete_disk", "z2", "d4"), ("list_disks", "z1"), ("list_disks", "z2")],
        },
    ),
    ("disk", True, True, True, "fail_list_first"): (
        ("BUG-005", "BUG-006", "BUG-127"),
        {
            "slack": [
                (
                    "chat_postMessage",
                    {
                        "channel": "#chan",
                        "text": "`Dry Run`: Will be Deleted: `1` gcp Disk(s):\n`['d4']`",
                        "link_names": True,
                    },
                )
            ],
            "influx": [("b", "gcp", {"resource": "Disk"}, {"names": "['d4']", "count": 1})],
            "error": None,
            "gcp_calls": [("list_disks", "z1"), ("list_disks", "z2")],
        },
    ),
    ("vm-delete", False, True, True, "fail_list_first"): (
        ("BUG-186",),
        {
            "slack": [
                (
                    "chat_postMessage",
                    {"channel": "#chan", "text": "Deleted the following `1` gcp VM(s):\n`['vm3']`"},
                )
            ],
            "influx": [("b", "gcp", {"resource": "VM"}, {"names": "['vm3']", "count": 1})],
            "error": None,
            "gcp_calls": [
                ("delete_instance", "z2", "vm3"),
                ("list_instances", "z1"),
                ("list_instances", "z2"),
            ],
        },
    ),
    ("vm-delete", True, True, True, "fail_list_first"): (
        ("BUG-186",),
        {
            "slack": [
                (
                    "chat_postMessage",
                    {
                        "channel": "#chan",
                        "text": "`Dry Run`: Will be Deleted: `1` gcp VM(s):\n`['vm3']`",
                    },
                )
            ],
            "influx": [("b", "gcp", {"resource": "VM"}, {"names": "['vm3']", "count": 1})],
            "error": None,
            "gcp_calls": [("list_instances", "z1"), ("list_instances", "z2")],
        },
    ),
    ("disk", False, False, False, "fail_list_last"): (
        ("BUG-005", "BUG-006", "BUG-127"),
        {
            "error": None,
            "gcp_calls": [("delete_disk", "z1", "d1"), ("list_disks", "z1"), ("list_disks", "z2")],
        },
    ),
    ("vm-delete", False, False, False, "fail_list_last"): (
        ("BUG-186",),
        {
            "error": None,
        },
    ),
    ("disk", True, True, True, "fail_close"): (
        ("BUG-005", "BUG-006"),
        {
            "slack": [
                (
                    "chat_postMessage",
                    {
                        "channel": "#chan",
                        "text": "`Dry Run`: Will be Deleted: `2` gcp Disk(s):\n`['d1', 'd4']`",
                        "link_names": True,
                    },
                )
            ],
            "influx": [("b", "gcp", {"resource": "Disk"}, {"names": "['d1', 'd4']", "count": 2})],
        },
    ),
    ("disk", False, True, True, "fail_close_after_delete"): (
        ("BUG-005", "BUG-006"),
        {
            "slack": [
                (
                    "chat_postMessage",
                    {
                        "channel": "#chan",
                        "text": "Deleted the following `2` gcp Disk(s):\n`['d1', 'd4']`",
                        "link_names": True,
                    },
                )
            ],
            "influx": [("b", "gcp", {"resource": "Disk"}, {"names": "['d1', 'd4']", "count": 2})],
            "gcp_calls": [
                ("delete_disk", "z1", "d1"),
                ("delete_disk", "z2", "d4"),
                ("list_disks", "z1"),
                ("list_disks", "z2"),
            ],
        },
    ),
}
