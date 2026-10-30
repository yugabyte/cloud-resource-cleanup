"""Parity: `Service.is_old` of `main` (frozen oracle) against `crc.service.Service.is_old`.

Covers the `MAX_AGE` environment variable (unset, empty, valid, invalid), empty and literal ages
and the quirks of `main`: `True` as 1 day (BUG-024), non-dict `MAX_AGE` raises (BUG-025), unknown
age keys never old (BUG-026), `{}` in `MAX_AGE` (BUG-027).
"""

import datetime
from typing import Any

import pytest

from tests.helpers_crc_service import both, make_oracle_cls, set_max_age
from tests.helpers_log_fix import expected_outcome

CREATED = datetime.datetime(2026, 1, 1, 12, 0, 0)  # noqa: DTZ001 - naive fixture time kept: an aware value is not provably identical


@pytest.fixture
def oracle_cls(monkeypatch: pytest.MonkeyPatch) -> type:
    return make_oracle_cls(monkeypatch)


AGES: list[Any] = [
    0,
    None,
    {},
    1,
    2,
    3,
    True,  # BUG-024
    {"days": 1},
    {"days": 2},
    {"hours": 24},
    {"hours": 48},
    {"hours": 47},
    {"hours": 49},
    {"days": 1, "hours": 12},
    {"days": 1, "hours": 0},
    {"days": 0, "hours": 36},
    {"days": 0},
    {"hours": 0},
    {"days": "1"},
    {"days": 1.9},
    {"weeks": 1},  # BUG-026
    {"days": 1, "weeks": 1},
    {"days": None},
    {"days": float("inf")},
    {"days": -1},
    "x",
    [1],
    1.5,
]
CREATION_OFFSETS: list[datetime.timedelta] = [
    datetime.timedelta(0),
    datetime.timedelta(hours=23, minutes=59),
    datetime.timedelta(days=1),
    datetime.timedelta(days=1, hours=11, minutes=59),
    datetime.timedelta(days=1, hours=12),
    datetime.timedelta(days=2),
    datetime.timedelta(days=2, seconds=1),
    datetime.timedelta(days=3),
    datetime.timedelta(days=-1),
    datetime.timedelta(days=400),
]
MAX_AGES: list[str | None] = [
    None,
    "",
    "{'days': 1}",
    "{'days': 5}",
    "{'hours': 36}",
    "{'days': 1, 'hours': 12}",
    "{}",  # BUG-027
    "{'weeks': 1}",
    "{'days': 2}",
    "5",  # BUG-025
    "not a literal",  # BUG-025
    "[1, 2]",
    "'days'",
    "{'days': 'x'}",
    "{'days': 1e999}",
]


@pytest.mark.parametrize("max_age", MAX_AGES)
@pytest.mark.parametrize("age", AGES, ids=repr)
def test_is_old_parity(
    oracle_cls: type,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    age: Any,
    max_age: str | None,
) -> None:
    set_max_age(monkeypatch, max_age)
    for offset in CREATION_OFFSETS:
        now = CREATED + offset
        old, new = both(oracle_cls, caplog, "is_old", age, now, CREATED)
        assert new == expected_outcome(old, {"BUG-356"})


@pytest.mark.parametrize("max_age", [None, "{'days': 1}", "{'hours': 5}"])
@pytest.mark.parametrize(
    ("now", "created"),
    [
        (datetime.datetime(2026, 1, 3, tzinfo=datetime.UTC), datetime.datetime(2026, 1, 1)),  # noqa: DTZ001 - naive fixture time kept: an aware value is not provably identical
        (datetime.datetime(2026, 1, 3), datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)),  # noqa: DTZ001 - naive fixture time kept: an aware value is not provably identical
        (datetime.datetime(2026, 1, 3, tzinfo=datetime.UTC), None),
        (None, datetime.datetime(2026, 1, 1)),  # noqa: DTZ001 - naive fixture time kept: an aware value is not provably identical
        ("2026-01-03", "2026-01-01"),
        (
            datetime.datetime(2026, 1, 3, tzinfo=datetime.UTC),
            datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        ),
    ],
)
def test_is_old_time_variants_parity(
    oracle_cls: type,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    max_age: str | None,
    now: Any,
    created: Any,
) -> None:
    set_max_age(monkeypatch, max_age)
    old, new = both(oracle_cls, caplog, "is_old", {"days": 1}, now, created)
    assert new == expected_outcome(old, {"BUG-356"})


def test_is_old_empty_age_ignores_max_age_without_parsing(
    oracle_cls: type, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAX_AGE", "not a literal")
    old, new = both(oracle_cls, caplog, "is_old", {}, CREATED, CREATED)
    assert new == expected_outcome(old, {"BUG-356"})
    assert new[0] == "return"
    assert new[1] is True


def test_bug_356_age_lines_differ_from_oracle_and_are_debug(
    oracle_cls: type, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_max_age(monkeypatch, None)
    now = CREATED + datetime.timedelta(days=2)
    old, new = both(oracle_cls, caplog, "is_old", {"days": 1}, now, CREATED)
    assert old[2] != new[2]
    assert new == expected_outcome(old, {"BUG-356"})
    assert [level for level, _ in old[2]] == ["INFO", "INFO"]
    assert [level for level, _ in new[2]] == ["DEBUG", "DEBUG"]
    assert [text for _, text in new[2]] == [text for _, text in old[2]]
