"""BUG-004 and BUG-016, a bad custom age tag keeps the resource (fail closed)."""

import datetime
import logging
from typing import Any

import pytest

from crc.core import logger
from crc.core.age import KEEP_AGE, get_retention_age, is_old
from crc.service import Service

CREATED = datetime.datetime(2020, 1, 1)
NOW = datetime.datetime(2026, 1, 1)
KEY = "max_retention_age"
BAD_TAG_COUNT = 3

BAD_VALUES = [
    "",
    "   ",
    "not a literal",
    "{'days': ",
    "{[]: 1}",
    "{}",
    "0",
    "{'days': 0}",
    "{'days': 0, 'hours': 0}",
    "{'days': -1}",
    "{'days': 1e999}",
    "{'days': float('nan')}",
    "{'weeks': 1}",
    "1.5",
    "[1, 2]",
    "'days'",
]


@pytest.mark.parametrize("value", BAD_VALUES)
@pytest.mark.parametrize("as_list", [True, False])
def test_bad_tag_returns_keep_age_and_keeps_resource(value: str, as_list: bool) -> None:
    tags: Any = [{"Key": KEY, "Value": value}] if as_list else {KEY: value}
    age = get_retention_age(tags, KEY)
    assert age == KEEP_AGE
    assert age is not KEEP_AGE
    assert is_old(age, NOW, CREATED) is False


@pytest.mark.parametrize("max_age", ["{'days': 21}", "{'hours': 1}", "{'days': 0}"])
def test_keep_age_ignores_max_age(monkeypatch: pytest.MonkeyPatch, max_age: str) -> None:
    monkeypatch.setenv("MAX_AGE", max_age)
    assert is_old(KEEP_AGE, NOW, CREATED) is False


def test_none_value_and_missing_value_keep() -> None:
    assert get_retention_age({KEY: None}, KEY) == KEEP_AGE  # type: ignore[dict-item]
    assert get_retention_age([{"Key": KEY}], KEY) == KEEP_AGE


@pytest.mark.parametrize(
    ("tags", "key", "expected"),
    [
        ({"other": "x"}, KEY, None),
        ([{"Key": "other", "Value": "x"}], KEY, None),
        ({KEY: "{'days': 2}"}, KEY, {"days": 2}),
        ([{"Key": KEY, "Value": "{'hours': 5}"}], KEY, {"hours": 5}),
        ({KEY: "{'days': 1, 'hours': 2}"}, KEY, {"days": 1, "hours": 2}),
        ({KEY: "7"}, KEY, 7),
        ({KEY: "bad"}, "", None),
        ({}, KEY, None),
    ],
)
def test_absent_or_valid_tag_unchanged(tags: Any, key: str, expected: Any) -> None:
    assert get_retention_age(tags, key) == expected


def test_valid_tag_is_still_judged_by_its_own_age() -> None:
    assert is_old(get_retention_age({KEY: "{'days': 2}"}, KEY), NOW, CREATED) is True
    assert is_old(get_retention_age({KEY: "{'days': 2}"}, KEY), NOW, NOW) is False


def test_bad_tag_counts_skip_and_logs_at_debug_only(caplog: pytest.LogCaptureFixture) -> None:
    run = logger.begin("aws", "vm", "delete", True, {})
    try:
        with caplog.at_level(logging.DEBUG):
            get_retention_age({KEY: "zzz"}, KEY)
            get_retention_age({KEY: ""}, KEY)
            get_retention_age({KEY: "{'days': 0}"}, KEY)
            get_retention_age({KEY: "{'days': 2}"}, KEY)
        assert run._skips == {"bad_age_tag": BAD_TAG_COUNT}
    finally:
        logger.finish()
    bad = [
        r
        for r in caplog.records
        if "ustom age tag" in r.getMessage() and "skipping" in r.getMessage()
    ]
    assert len(bad) == BAD_TAG_COUNT
    assert {r.levelname for r in bad} == {"DEBUG"}
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING and "age" in r.name]


def test_service_delegates_bad_tag() -> None:
    service = Service()
    assert service.get_retention_age({KEY: ""}, KEY) == KEEP_AGE
    assert service.get_retention_age({"x": "1"}, KEY) is None
