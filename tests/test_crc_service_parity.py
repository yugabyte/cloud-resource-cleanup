"""Parity: `Service` of `main` (frozen oracle) against `crc.service.Service`.

`tests/legacy/crc_service.txt` is the exact output of `git show main:crc/service.py`. The same
inputs, environment and clock run through both classes; return values, raised errors, log
records and `init_logging` calls must be equal. Quirks of `main` (BUG-004, BUG-016, BUG-024 to
BUG-027, BUG-182, BUG-208, BUG-289) are cases of the tables. BUG-004 and BUG-016 are fixed
for `get_retention_age`: the diverging cases are marked and assert the new outcome.
"""

from typing import Any

import pytest

from crc.core.age import KEEP_AGE
from crc.service import Service
from tests.helpers_crc_service import both, load_oracle, make_oracle_cls
from tests.helpers_log_fix import expected_outcome


@pytest.fixture
def oracle_cls(monkeypatch: pytest.MonkeyPatch) -> type:
    return make_oracle_cls(monkeypatch)


def test_class_attributes_match(oracle_cls: type) -> None:
    for name in ("logs_dir", "logs_file", "AGE_KEYS"):
        assert getattr(Service, name) == getattr(oracle_cls, name)
    old_methods = {n for n in vars(oracle_cls) if not n.startswith("__")}
    assert old_methods <= set(vars(Service))


@pytest.mark.parametrize(
    ("logs_dir", "logs_file"),
    [(None, None), ("other", None), (None, "x.log"), ("/abs/dir", "y.log")],
)
def test_init_calls_init_logging_identically(
    monkeypatch: pytest.MonkeyPatch, logs_dir: str | None, logs_file: str | None
) -> None:
    module = load_oracle()
    old_calls: list[tuple[Any, ...]] = []
    new_calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(module, "init_logging", lambda *a, **k: old_calls.append((a, k)))
    monkeypatch.setattr("crc.service.init_logging", lambda *a, **k: new_calls.append((a, k)))

    def build(base: type) -> None:
        attrs: dict[str, str] = {}
        if logs_dir is not None:
            attrs["logs_dir"] = logs_dir
        if logs_file is not None:
            attrs["logs_file"] = logs_file
        type("Sub", (base,), attrs)()

    build(module.Service)
    build(Service)
    assert old_calls == new_calls
    assert len(new_calls) == 1


REQUIRE_CASES: list[tuple[Any, str]] = [
    (None, "age"),
    (0, "age"),
    ({}, "age"),
    ("", "age"),
    (1, "age"),
    (True, "age"),  # BUG-024
    (False, "age"),
    (-1, "age"),
    ({"days": 1}, "age"),
    ({"hours": 5}, "age"),
    ({"days": 1, "hours": 2}, "custom age tag"),
    ({"days": 0, "hours": 0}, "age"),
    ({"days": 0, "hours": 1}, "age"),
    ({"days": -1, "hours": 30}, "age"),
    ({"days": "2"}, "age"),
    ({"days": "x"}, "age"),
    ({"days": None}, "age"),
    ({"days": 1.9}, "age"),
    ({"days": float("inf")}, "age"),  # BUG-016 / BUG-208 overflow
    ({"days": float("nan")}, "age"),
    ({"weeks": 1}, "age"),
    ({"days": 1, "weeks": 1}, "age"),
    ({1: 1, "days": 1}, "age"),
    ({"minutes": 3}, "label"),
    ("days", "age"),
    ([1], "age"),
    ([], "age"),
    (1.5, "age"),
    ({"days": True}, "age"),
]


@pytest.mark.parametrize(("age", "label"), REQUIRE_CASES)
def test_require_positive_age_parity(
    oracle_cls: type, caplog: pytest.LogCaptureFixture, age: Any, label: str
) -> None:
    old, new = both(oracle_cls, caplog, "require_positive_age", age, label)
    assert new == expected_outcome(old, {"BUG-356"})


def test_require_positive_age_default_label(
    oracle_cls: type, caplog: pytest.LogCaptureFixture
) -> None:
    old, new = both(oracle_cls, caplog, "require_positive_age", 0)
    assert new == expected_outcome(old, {"BUG-356"})
    assert new[0] == "raise"


AWS_TAGS: list[dict[str, str]] = [
    {"Key": "Name", "Value": "n"},
    {"Key": "retain", "Value": "{'days': 2}"},
]
TAG_CASES: list[tuple[Any, str]] = [
    (AWS_TAGS, "retain"),
    (AWS_TAGS, "missing"),
    (AWS_TAGS, ""),
    (AWS_TAGS, "Name"),
    ([], "retain"),
    ({}, "retain"),
    (None, "retain"),
    ({"retain": "{'days': 2}"}, "retain"),
    ({"retain": ""}, "retain"),
    ({"retain": None}, "retain"),
    ({"other": "x"}, "retain"),
    ({"retain": "{'days': 2}"}, ""),
    ([{"Key": "retain"}], "retain"),
    ([{"Key": "retain", "Value": ""}], "retain"),
    (
        [{"Key": "retain", "Value": ""}, {"Key": "retain", "Value": "{'days': 3}"}],
        "retain",
    ),  # BUG-289
    (
        [{"Key": "retain", "Value": "{'days': 3}"}, {"Key": "retain", "Value": "{'days': 9}"}],
        "retain",
    ),
    ([{"Key": "retain", "Value": "{'days': 1e999}"}], "retain"),  # BUG-208
    ([{"Key": "retain", "Value": "{'days': 0}"}], "retain"),
    ([{"Key": "retain", "Value": "{'days': -1}"}], "retain"),
    ([{"Key": "retain", "Value": "{'days': True}"}], "retain"),
    ([{"Key": "retain", "Value": "{'days': 1, 'hours': 2}"}], "retain"),
    ([{"Key": "retain", "Value": "{'weeks': 1}"}], "retain"),
    ([{"Key": "retain", "Value": "not a literal"}], "retain"),
    ([{"Key": "retain", "Value": "{'days': "}], "retain"),
    ([{"Key": "retain", "Value": "1.5"}], "retain"),  # BUG-182
    ([{"Key": "retain", "Value": "True"}], "retain"),  # BUG-182
    ([{"Key": "retain", "Value": "'days'"}], "retain"),  # BUG-182
    ([{"Key": "retain", "Value": "[1, 2]"}], "retain"),
    ([{"Key": "retain", "Value": "5"}], "retain"),
    ([{"Key": "retain", "Value": "0"}], "retain"),
    ([{"Key": "retain", "Value": "   "}], "retain"),
    ([{"Key": "retain", "Value": "__import__('os')"}], "retain"),
    ([{"Key": "retain", "Value": "1 +"}], "retain"),
    ([{"Key": "retain", "Value": "[" * 200}], "retain"),
    ([{"Value": "{'days': 2}"}], "retain"),
    ([{"Key": "retain", "Value": 5}], "retain"),
    ([{"Key": "retain", "Value": ["x"]}], "retain"),
    (["bad"], "retain"),
    ([None], "retain"),
    ({"retain": 5}, "retain"),
    ({"retain": "{'hours': 12}"}, "retain"),
    ({"retain": "{'days': 1e999}"}, "retain"),
    ({"retain": "garbage"}, "retain"),
    ({"retain": "{'days': 2}"}, "other"),
    ("retain", "retain"),
    ("abc", "retain"),
    (7, "retain"),
]


# TAG_CASES indexes where `get_retention_age` diverges from main on purpose (BUG-004, BUG-016):
# a custom age tag that is present but empty, unparseable, not finite, zero, negative, not a
# valid age or not a string is now a keep marker instead of None / an unchecked literal.
BUG_004_CASES = frozenset(
    {3, 8, 9, 12, 13, 14, 16, 17, 18, 21, 22, 23, 24, 26, 27, 29, 30, 31, 32, 33, 35, 36}
    | {39, 41, 42}
)


TAG_METHOD_PARITY = [
    pytest.param(method, tags, key, id=f"{method}-case{i}")
    for method in ("_lookup_custom_age_tag", "validated_retention_age", "get_retention_age")
    for i, (tags, key) in enumerate(TAG_CASES)
    if not (method == "get_retention_age" and i in BUG_004_CASES)
]


@pytest.mark.parametrize(("method", "tags", "key"), TAG_METHOD_PARITY)
def test_tag_methods_parity(
    oracle_cls: type, caplog: pytest.LogCaptureFixture, method: str, tags: Any, key: str
) -> None:
    old, new = both(oracle_cls, caplog, method, tags, key)
    assert new == expected_outcome(old, {"BUG-356"})


@pytest.mark.parametrize("index", sorted(BUG_004_CASES))
def test_get_retention_age_bad_tag_diverges_and_keeps(
    oracle_cls: type, caplog: pytest.LogCaptureFixture, index: int
) -> None:
    """BUG-004 / BUG-016: the oracle falls back (None or an unchecked literal); the fix keeps."""
    tags, key = TAG_CASES[index]
    old, new = both(oracle_cls, caplog, "get_retention_age", tags, key)
    assert new != expected_outcome(old, {"BUG-356"})
    assert new[0] == "return"
    assert new[1] == KEEP_AGE
    assert new[1] is not KEEP_AGE
    assert {level for level, _ in new[2]} <= {"DEBUG"}
    if old[0] == "return":
        assert old[1] != KEEP_AGE


@pytest.mark.parametrize(
    "key",
    [None, 0, b"retain"],
)
def test_tag_methods_non_string_key_parity(
    oracle_cls: type, caplog: pytest.LogCaptureFixture, key: Any
) -> None:
    for method in ("_lookup_custom_age_tag", "validated_retention_age", "get_retention_age"):
        old, new = both(oracle_cls, caplog, method, AWS_TAGS, key)
        assert new == expected_outcome(old, {"BUG-356"})


@pytest.mark.parametrize(
    "value", ["{'days': 2}", "5", "x y", "{", "", "None", "[1]", "{'a': 1e999}"]
)
def test_parse_literal_parity(
    oracle_cls: type, caplog: pytest.LogCaptureFixture, value: str
) -> None:
    old, new = both(oracle_cls, caplog, "_parse_literal", value, "retain")
    assert new == expected_outcome(old, {"BUG-356"})


@pytest.mark.parametrize("value", [None, 5, b"x", "__import__('os')", "(" * 300])
def test_parse_literal_odd_values_parity(
    oracle_cls: type, caplog: pytest.LogCaptureFixture, value: Any
) -> None:
    old, new = both(oracle_cls, caplog, "_parse_literal", value, "retain")
    assert new == expected_outcome(old, {"BUG-356"})


MIN_AGE_CASES: list[tuple[Any, Any]] = [
    ({"days": 1}, {"days": 2}),
    ({"days": 2}, {"days": 1}),
    ({"days": 1}, {"days": 1}),
    ({"days": 1}, {"hours": 24}),
    ({"hours": 24}, {"days": 1}),
    ({"days": 3, "hours": 5}, {"days": 2, "hours": 3}),
    ({"days": 2, "hours": 3}, {"days": 3, "hours": 5}),
    ({}, {}),
    ({}, {"days": 1}),
    ({"days": 1}, {}),
    ({"hours": 1}, {"days": 0}),
    ({"minutes": 1}, {"seconds": 5}),
    ({"days": "1"}, {"days": 2}),
    ({"days": None}, {"days": 2}),
    (None, {"days": 2}),
    ({"days": 2}, None),
    ({"days": 1.5}, {"hours": 36}),
    ({"days": 1}, "x"),
]


@pytest.mark.parametrize(("first", "second"), MIN_AGE_CASES)
def test_get_min_age_parity(
    oracle_cls: type, caplog: pytest.LogCaptureFixture, first: Any, second: Any
) -> None:
    old, new = both(oracle_cls, caplog, "get_min_age", first, second)
    assert new == expected_outcome(old, {"BUG-356"})
    if new[0] == "return" and first is not None and second is not None:
        assert new[1] is first or new[1] is second


def test_bug_356_retention_age_lines_differ_from_oracle_and_are_debug(
    oracle_cls: type, caplog: pytest.LogCaptureFixture
) -> None:
    for tags, expected_old, expected_new in (
        (AWS_TAGS, ["INFO", "INFO"], ["DEBUG", "INFO"]),
        ({}, ["WARNING"], ["DEBUG"]),
    ):
        old, new = both(oracle_cls, caplog, "get_retention_age", tags, "retain")
        assert old[2] != new[2]
        assert new == expected_outcome(old, {"BUG-356"})
        assert [level for level, _ in old[2]] == expected_old
        assert [level for level, _ in new[2]] == expected_new
        assert [text for _, text in new[2]] == [text for _, text in old[2]]
