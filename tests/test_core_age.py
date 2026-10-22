"""Table-driven tests for crc.core.age (ported from crc.service.Service)."""

import datetime
import logging
from typing import Any

import pytest

from crc.core import age as core_age
from crc.core.age import (
    AgeSpec,
    get_min_age,
    get_retention_age,
    is_old,
    lookup_custom_age_tag,
    parse_literal,
    require_positive_age,
    validated_retention_age,
)
from crc.service import Service

NOW = datetime.datetime(2026, 1, 10, 12, 0, 0)


def _loose(value: Any) -> Any:
    """Pass deliberately mistyped input to a typed function."""
    return value


def _created(**delta: float) -> datetime.datetime:
    return NOW - datetime.timedelta(**delta)


class _TruthyEmptyDict(dict[str, int]):
    """Dict subclass that is truthy when empty, reaching the AGE_KEYS intersection guard."""

    def __bool__(self) -> bool:
        return True


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (3, {"days": 3}),
        ({"days": 2}, {"days": 2}),
        ({"hours": 5}, {"hours": 5}),
        ({"days": 1, "hours": 2}, {"days": 1, "hours": 2}),
        ({"days": 0, "hours": 1}, {"days": 0, "hours": 1}),
        ({"days": "2"}, {"days": 2}),
        ({"days": 1.9}, {"days": 1}),
        (True, {"days": 1}),
    ],
)
def test_require_positive_age_accepts(age: Any, expected: AgeSpec) -> None:
    assert require_positive_age(age) == expected


@pytest.mark.parametrize(
    ("age", "message"),
    [
        (None, "age is required and must be greater than zero"),
        (0, "age is required and must be greater than zero"),
        ({}, "age is required and must be greater than zero"),
        ("", "age is required and must be greater than zero"),
        ("3", "age must be an int or dict of days/hours"),
        (1.5, "age must be an int or dict of days/hours"),
        ([1], "age must be an int or dict of days/hours"),
        ({"weeks": 1}, r"age has unsupported keys \['weeks'\]; use days and/or hours"),
        ({"days": 1, "weeks": 1}, "age has unsupported keys"),
        ({1: 1}, r"age has unsupported keys \[1\]"),
        (_TruthyEmptyDict(), "age must include days and/or hours"),
        ({"days": "abc"}, "age days must be an integer"),
        ({"days": None}, "age days must be an integer"),
        ({"hours": [1]}, "age hours must be an integer"),
        ({"days": 1e999}, "age days must be an integer"),
        ({"days": float("nan")}, "age days must be an integer"),
        ({"days": -1}, "age days cannot be negative"),
        ({"days": 1, "hours": -1}, "age hours cannot be negative"),
        (-3, "age days cannot be negative"),
        ({"days": 0}, "age must be greater than zero"),
        ({"hours": 0}, "age must be greater than zero"),
        ({"days": 0, "hours": 0}, "age must be greater than zero"),
    ],
)
def test_require_positive_age_rejects(age: Any, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        require_positive_age(age)


def test_require_positive_age_label_and_chaining() -> None:
    with pytest.raises(ValueError, match="detach_age is required") as empty:
        require_positive_age(None, "detach_age")
    assert empty.value.__cause__ is None
    with pytest.raises(ValueError, match="detach_age days must be an integer") as bad:
        require_positive_age(_loose({"days": 1e999}), "detach_age")
    assert isinstance(bad.value.__cause__, OverflowError)
    with pytest.raises(ValueError, match="x days must be an integer") as typed:
        require_positive_age(_loose({"days": None}), "x")
    assert isinstance(typed.value.__cause__, TypeError)


def test_require_positive_age_does_not_mutate_input() -> None:
    original = _loose({"days": "2"})
    assert require_positive_age(original) == {"days": 2}
    assert original == {"days": "2"}


AWS_TAGS = [{"Key": "Name", "Value": "x"}, {"Key": "age", "Value": "{'days': 2}"}]


@pytest.mark.parametrize(
    ("tags", "key", "expected"),
    [
        (AWS_TAGS, "age", (True, "{'days': 2}")),
        (AWS_TAGS, "missing", (False, None)),
        ([{"Key": "age"}], "age", (True, None)),
        ([{"Key": "age", "Value": ""}], "age", (True, "")),
        ([{"Key": "age", "Value": ""}, {"Key": "age", "Value": "5"}], "age", (True, "")),
        ({"age": "{'hours': 3}"}, "age", (True, "{'hours': 3}")),
        ({"age": ""}, "age", (True, "")),
        ({"other": "1"}, "age", (False, None)),
        ({}, "age", (False, None)),
        ([], "age", (False, None)),
        (AWS_TAGS, "", (False, None)),
        ({"": "1"}, "", (False, None)),
    ],
)
def test_lookup_custom_age_tag(tags: Any, key: str, expected: tuple[bool, str | None]) -> None:
    assert lookup_custom_age_tag(tags, key) == expected


@pytest.mark.parametrize(
    ("tags", "key", "expected"),
    [
        (AWS_TAGS, "age", {"days": 2}),
        ({"age": "{'hours': 3}"}, "age", {"hours": 3}),
        ({"age": "{'days': 1, 'hours': 2}"}, "age", {"days": 1, "hours": 2}),
        ({"age": "4"}, "age", {"days": 4}),
        ({"age": " 4 "}, "age", {"days": 4}),
        ({"other": "1"}, "age", None),
        ({}, "age", None),
        ([], "age", None),
        (AWS_TAGS, "missing", None),
        (AWS_TAGS, "", None),
        ({"": "{'days': 1}"}, "", None),
    ],
)
def test_validated_retention_age_valid_or_absent(
    tags: Any, key: str, expected: AgeSpec | None
) -> None:
    assert validated_retention_age(tags, key) == expected


@pytest.mark.parametrize(
    ("tags", "message"),
    [
        ({"age": ""}, "custom age tag 'age' is present but empty"),
        ({"age": "   "}, "custom age tag 'age' is present but empty"),
        ([{"Key": "age", "Value": ""}], "is present but empty"),
        ([{"Key": "age"}], "is present but empty"),
        ({"age": "not a literal"}, "is present but unparseable: 'not a literal'"),
        ({"age": "{'days': "}, "is present but unparseable"),
        ({"age": "[x for x in y]"}, "is present but unparseable"),
        ({"age": "{'days': 0}"}, "custom age tag must be greater than zero"),
        ({"age": "0"}, "custom age tag is required and must be greater than zero"),
        ({"age": "{}"}, "custom age tag is required"),
        ({"age": "{'days': -1}"}, "custom age tag days cannot be negative"),
        ({"age": "{'weeks': 1}"}, "custom age tag has unsupported keys"),
        ({"age": "{'days': 1e999}"}, "custom age tag days must be an integer"),
        ({"age": "'abc'"}, "custom age tag must be an int or dict of days/hours"),
        ({"age": "{'days': 'x'}"}, "custom age tag days must be an integer"),
    ],
)
def test_validated_retention_age_present_but_bad_raises(
    tags: Any, message: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING), pytest.raises(ValueError, match=message):
        validated_retention_age(tags, "age")
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_validated_retention_age_log_messages(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING), pytest.raises(ValueError):
        validated_retention_age({"age": ""}, "age")
    assert caplog.messages == ["Custom age tag 'age' is present but empty (skipping resource)"]

    caplog.clear()
    with caplog.at_level(logging.WARNING), pytest.raises(ValueError) as unparseable:
        validated_retention_age({"age": "zzz"}, "age")
    assert isinstance(unparseable.value.__cause__, ValueError)
    assert len(caplog.messages) == 1
    assert caplog.messages[0].startswith(
        "Custom age tag 'age'='zzz' is unparseable (skipping resource): "
    )

    caplog.clear()
    with caplog.at_level(logging.WARNING), pytest.raises(ValueError):
        validated_retention_age({"age": "{'days': 0}"}, "age")
    assert caplog.messages == [
        "Invalid custom age tag {'days': 0} for key 'age' (skipping resource): "
        "custom age tag must be greater than zero"
    ]


def test_validated_retention_age_typeerror_becomes_valueerror() -> None:
    with pytest.raises(ValueError, match="unparseable") as err:
        validated_retention_age({"age": "{[]: 1}"}, "age")
    assert isinstance(err.value.__cause__, TypeError)


def test_validated_retention_age_no_log_when_absent(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        assert validated_retention_age({}, "age") is None
        assert validated_retention_age({"a": "1"}, "") is None
    assert caplog.messages == []


@pytest.mark.parametrize(
    ("tags", "key", "expected"),
    [
        (AWS_TAGS, "age", {"days": 2}),
        ({"age": "{'hours': 3}"}, "age", {"hours": 3}),
        ({"age": "7"}, "age", 7),
        ({"age": "{'days': 0}"}, "age", {"days": 0}),
        ({"age": "{'weeks': 1}"}, "age", {"weeks": 1}),
        ({"age": "{'days': 1e999}"}, "age", {"days": float("inf")}),
        ({"age": "not a literal"}, "age", None),
        ({"age": "{'days': "}, "age", None),
        ({"age": ""}, "age", None),
        ({"age": "{[]: 1}"}, "age", None),
        ({"other": "1"}, "age", None),
        ({}, "age", None),
        ([], "age", None),
        (None, "age", None),
        (AWS_TAGS, "missing", None),
        (AWS_TAGS, "", None),
        ([{"Key": "age"}], "age", None),
        ([{"Key": "age", "Value": ""}], "age", None),
        ([{"Key": "age", "Value": ""}, {"Key": "age", "Value": "5"}], "age", 5),
        ([{"Key": "age", "Value": "zzz"}, {"Key": "age", "Value": "5"}], "age", None),
        (["not-a-dict"], "age", None),
    ],
)
def test_get_retention_age(tags: Any, key: str, expected: Any) -> None:
    assert get_retention_age(tags, key) == expected


def test_get_retention_age_logs(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        get_retention_age({"a": "1"}, "")
        get_retention_age({}, "a")
        get_retention_age({"a": "1"}, "a")
        get_retention_age({"a": "zzz"}, "a")
        get_retention_age(_loose(["bad"]), "a")
    messages = caplog.messages
    assert messages[:2] == [
        "No custom_age_tag_key provided to search for.",
        "No tags provided to search for custom_age_tag_key.",
    ]
    assert messages[2:5] == [
        "Searching for custom_age_tag_key: a",
        "Found 'a' tag: 1",
        "Searching for custom_age_tag_key: a",
    ]
    assert messages[5] == "Found 'a' tag: zzz"
    assert messages[6].startswith("Error parsing 'a' tag value 'zzz': ")
    assert messages[7] == "Searching for custom_age_tag_key: a"
    assert messages[8] == "Error retrieving 'a' tag: 'str' object has no attribute 'get'"
    levels = {r.getMessage(): r.levelname for r in caplog.records}
    assert levels["No custom_age_tag_key provided to search for."] == "DEBUG"
    assert levels["No tags provided to search for custom_age_tag_key."] == "DEBUG"  # BUG-356
    assert levels["Searching for custom_age_tag_key: a"] == "DEBUG"  # BUG-356
    assert levels["Found 'a' tag: 1"] == "INFO"
    assert levels[messages[6]] == "ERROR"
    assert levels[messages[8]] == "ERROR"


def test_parse_literal(caplog: pytest.LogCaptureFixture) -> None:
    assert parse_literal("{'days': 1}", "k") == {"days": 1}
    with caplog.at_level(logging.ERROR):
        assert parse_literal("zzz", "k") is None
        assert parse_literal("{'days': ", "k") is None
    assert [r.levelname for r in caplog.records] == ["ERROR", "ERROR"]
    assert caplog.messages[0].startswith("Error parsing 'k' tag value 'zzz': ")
    with pytest.raises(TypeError):
        parse_literal("{[]: 1}", "k")


@pytest.mark.parametrize(
    ("dict1", "dict2", "expected"),
    [
        ({"days": 3, "hours": 5}, {"days": 2, "hours": 3}, {"days": 2, "hours": 3}),
        ({"days": 2, "hours": 3}, {"days": 3, "hours": 5}, {"days": 2, "hours": 3}),
        ({"days": 1}, {"hours": 24}, {"hours": 24}),
        ({"hours": 24}, {"days": 1}, {"days": 1}),
        ({"days": 1}, {"days": 1}, {"days": 1}),
        ({"hours": 5}, {"hours": 6}, {"hours": 5}),
        ({"hours": 7}, {"hours": 6}, {"hours": 6}),
        ({"days": 1}, {"hours": 25}, {"days": 1}),
        ({}, {"days": 1}, {}),
        ({"days": 1}, {}, {}),
        ({}, {}, {}),
    ],
)
def test_get_min_age(dict1: AgeSpec, dict2: AgeSpec, expected: AgeSpec) -> None:
    assert get_min_age(dict1, dict2) == expected


def test_get_min_age_tie_returns_second_object() -> None:
    first: AgeSpec = {"days": 1}
    second: AgeSpec = {"hours": 24}
    assert get_min_age(first, second) is second
    assert get_min_age(second, first) is first


@pytest.mark.parametrize(
    ("age", "delta", "expected"),
    [
        (3, {"days": 3}, True),
        (3, {"days": 2, "hours": 23}, False),
        ({"days": 3}, {"days": 4}, True),
        ({"days": 3}, {"days": 3}, True),
        ({"days": 3}, {"days": 2, "hours": 23}, False),
        ({"days": 1}, {"hours": 47}, True),
        ({"hours": 5}, {"hours": 5}, True),
        ({"hours": 5}, {"hours": 4, "minutes": 59}, False),
        ({"hours": 30}, {"days": 1, "hours": 6}, True),
        ({"hours": 30}, {"days": 1, "hours": 5}, False),
        ({"days": 1, "hours": 12}, {"days": 1, "hours": 12}, True),
        ({"days": 1, "hours": 12}, {"days": 1, "hours": 11, "minutes": 59}, False),
        ({"days": 1, "hours": 12}, {"days": 3}, True),
        ({"days": 0, "hours": 0}, {"hours": 0}, True),
        ({"days": "2"}, {"days": 2}, True),
        ({"unknown": 1}, {"days": 100}, False),
        ({"days": 3, "weeks": 9}, {"days": 3}, True),
        ({"days": 3, "weeks": 9}, {"days": 2}, False),
        ({"hours": 1, "weeks": 9}, {"hours": 1}, True),
        ({"days": 1}, {"hours": -5}, False),
    ],
)
def test_is_old_thresholds(
    age: Any, delta: dict[str, float], expected: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MAX_AGE", raising=False)
    assert is_old(age, NOW, _created(**delta)) is expected


@pytest.mark.parametrize("age", [{}, 0, None])
def test_is_old_empty_age_matches_all(
    age: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("MAX_AGE", "{'days': 9999}")
    with caplog.at_level(logging.DEBUG):
        assert is_old(age, NOW, NOW) is True
        assert is_old(age, NOW, _created(days=1000)) is True
    assert caplog.messages == ["Age is not specified. Ignoring age threshold check"] * 2


def test_is_old_log_messages(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("MAX_AGE", raising=False)
    with caplog.at_level(logging.INFO):
        assert is_old({"days": 1}, NOW, _created(days=2)) is True
    assert caplog.messages == []  # BUG-356: the age-check lines are DEBUG, not INFO
    with caplog.at_level(logging.DEBUG):
        assert is_old({"days": 1}, NOW, _created(days=2)) is True
        assert is_old({"hours": 2}, NOW, _created(hours=3)) is True
        assert is_old({"days": 1, "hours": 12}, NOW, _created(days=2)) is True
        assert is_old({"days": 5}, NOW, _created(days=1)) is False
    assert {r.levelno for r in caplog.records} == {logging.DEBUG}
    assert caplog.messages == [
        "Validating resource age with threshold: {'days': 1}",
        "The resource is 2 days old and older than the threshold of 1 days.",
        "Validating resource age with threshold: {'hours': 2}",
        "The resource is 3.0 hours old and older than the threshold of 2 hours.",
        "Validating resource age with threshold: {'days': 1, 'hours': 12}",
        "The resource is 48.0 hours old and older than the threshold of 36.0 hours.",
        "Validating resource age with threshold: {'days': 5}",
        "The resource is not older than the threshold specified in the age argument",
    ]


@pytest.mark.parametrize(
    ("age", "max_age", "delta", "outcome"),
    [
        ({"days": 5}, "{'days': 1}", {"days": 2}, (True, True)),
        ({"days": 5}, "{'days': 1}", {"hours": 23}, (False, True)),
        ({"days": 5}, "{'hours': 1}", {"hours": 2}, (True, True)),
        ({"days": 5}, "{'days': 0}", {"hours": 1}, (True, True)),
        (5, "{'days': 1}", {"days": 2}, (True, True)),
        ({"days": 1}, "{'days': 5}", {"days": 2}, (True, False)),
        ({"days": 5}, "{'days': 9}", {"days": 2}, (False, False)),
        ({"hours": 5}, "{'days': 1}", {"hours": 6}, (True, False)),
        ({"days": 1}, "{'hours': 24}", {"hours": 24}, (True, True)),
        ({"days": 5}, "{}", {"days": 99}, (False, True)),
        ({"days": 5}, "", {"days": 2}, (False, False)),
    ],
)
def test_is_old_max_age_override(
    age: Any,
    max_age: str,
    delta: dict[str, float],
    outcome: tuple[bool, bool],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    expected, overwritten = outcome
    monkeypatch.setenv("MAX_AGE", max_age)
    with caplog.at_level(logging.INFO):
        assert is_old(age, NOW, _created(**delta)) is expected
    marker = "Overwriting resource age: Setting age to the maximum threshold of"
    assert any(marker in m for m in caplog.messages) is overwritten


def test_is_old_max_age_unset_has_no_effect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAX_AGE", raising=False)
    assert is_old({"days": 5}, NOW, _created(days=2)) is False


def test_is_old_max_age_message_text(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("MAX_AGE", "{'hours': 1}")
    with caplog.at_level(logging.DEBUG):
        assert is_old({"days": 5}, NOW, _created(hours=2)) is True
    assert caplog.messages[0] == (
        "Overwriting resource age: Setting age to the maximum threshold of {'hours': 1}."
    )
    assert caplog.messages[1] == "Validating resource age with threshold: {'hours': 1}"


def test_is_old_invalid_max_age_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_AGE", "not a literal")
    with pytest.raises(SyntaxError):
        is_old({"days": 1}, NOW, _created(days=2))


def test_is_old_int_max_age_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_AGE", "3")
    with pytest.raises(AttributeError):
        is_old({"days": 1}, NOW, _created(days=2))


def test_is_old_non_numeric_threshold_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAX_AGE", raising=False)
    with pytest.raises(ValueError, match="invalid literal"):
        is_old(_loose({"days": "abc"}), NOW, _created(days=2))


def test_is_old_future_creation_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAX_AGE", raising=False)
    assert is_old({"days": 1}, NOW, NOW + datetime.timedelta(days=1)) is False


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("require_positive_age", ({"days": 2}, "lbl")),
        ("validated_retention_age", ({"a": "{'days': 2}"}, "a")),
        ("validated_retention_age", ({"a": "{'days': 2}"}, "b")),
        ("get_retention_age", ({"a": "{'days': 2}"}, "a")),
        ("get_retention_age", ({"a": "zzz"}, "a")),
        ("get_min_age", ({"days": 1}, {"hours": 5})),
        ("_lookup_custom_age_tag", (AWS_TAGS, "age")),
        ("_parse_literal", ("{'days': 1}", "k")),
    ],
)
def test_service_delegates_to_core(method: str, args: tuple[Any, ...]) -> None:
    service = Service.__new__(Service)
    core_name = {
        "_lookup_custom_age_tag": "lookup_custom_age_tag",
        "_parse_literal": "parse_literal",
    }
    expected = getattr(core_age, core_name.get(method, method))(*args)
    assert getattr(service, method)(*args) == expected


def test_service_is_old_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_AGE", "{'hours': 1}")
    service = Service.__new__(Service)
    created = _created(hours=2)
    assert service.is_old({"days": 5}, NOW, created) is is_old({"days": 5}, NOW, created) is True
    assert Service.AGE_KEYS == core_age.AGE_KEYS
