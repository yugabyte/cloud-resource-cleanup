"""Compare a new argparse parser with the oracle parser, allowing only the ADDED table.

`check_additions` asserts the compatibility contract: every action of the oracle exists with the
same option strings, dest, nargs, const, default, type, required, metavar, choices and help, in
the same order; each extra flag, choice or changed help text is listed in
`tests/helpers_ag_cli_added.py`. `strip_additions` then removes the listed additions from a
parser, so the exact parse comparison with the oracle (results, usage and error text) still runs
on every flag and value form the oracle knows.
"""

import argparse
import contextlib
import functools
import runpy
from collections.abc import Iterator, Sequence
from typing import Any

from tests.helpers_ag_cli_added import (
    ADDED_CHOICES,
    ADDED_FLAGS,
    ADDED_HINT,
    ADDED_RESOURCES,
    REMOVED_HINT,
    REMOVED_RESOURCES,
    RESOURCE_HELP,
    AddedFlag,
)
from tests.helpers_oracle import load_legacy_crc

COMPARED_FIELDS = ["option_strings", "dest", "nargs", "const", "default", "type", "required"]
COMPARED_FIELDS += ["metavar"]


def added_choices(option_strings: Sequence[str]) -> list[tuple[str, str | None]]:
    """The (choice, new help text or None for unchanged) pairs ADDED lists for the option."""
    pairs = [(c.choice, c.help) for c in ADDED_CHOICES if c.option in option_strings]
    if "--resource" in option_strings:
        pairs += [(r.kind, RESOURCE_HELP) for r in ADDED_RESOURCES]
    return pairs


def removed_choices(option_strings: Sequence[str]) -> list[str]:
    """The oracle choices REMOVED lists for the option (only `--resource` has any)."""
    if "--resource" in option_strings:
        return [r.kind for r in REMOVED_RESOURCES]
    return []


def _flag_of(action: argparse.Action) -> AddedFlag | None:
    for flag in ADDED_FLAGS:
        if flag.option in action.option_strings:
            return flag
    return None


def _fail_choices(old: argparse.Action, extra: list[Any]) -> str:
    return (
        f"{old.option_strings} has choices {extra} that the oracle crc_main.txt does not; "
        f"{ADDED_HINT}"
    )


def _check_choices(action: argparse.Action, old: argparse.Action) -> None:
    new_choices = list(action.choices) if action.choices is not None else None
    old_choices = list(old.choices) if old.choices is not None else None
    if old_choices is None:
        assert new_choices is None, f"{old.option_strings} gained choices; {ADDED_HINT}"
        assert action.help == old.help
        return
    assert new_choices is not None, f"{old.option_strings} lost its choices"
    missing = [c for c in old_choices if c not in new_choices]
    removed = removed_choices(old.option_strings)
    assert sorted(map(str, missing)) == sorted(removed), (
        f"{old.option_strings} lost the choices {missing} of the oracle, REMOVED lists "
        f"{removed}; {REMOVED_HINT}"
    )
    expected_kept = [c for c in old_choices if c not in removed]
    kept = [c for c in new_choices if c in old_choices]
    assert kept == expected_kept, f"{old.option_strings} reordered the choices of the oracle"
    extra = [c for c in new_choices if c not in old_choices]
    listed = added_choices(old.option_strings)
    assert sorted(map(str, extra)) == sorted(str(c) for c, _ in listed), (
        _fail_choices(old, extra) + f" (listed: {[c for c, _ in listed]})"
    )
    helps = {h for _, h in listed if h is not None}
    assert len(helps) <= 1, f"one new help text per option is expected, got {helps}"
    expected_help = helps.pop() if helps else old.help
    assert action.help == expected_help, (
        f"help of {old.option_strings} differs from the oracle and from ADDED"
    )


def _check_added_flag(action: argparse.Action) -> None:
    flag = _flag_of(action)
    assert flag is not None, (
        f"the parser has a flag {action.option_strings} (dest {action.dest!r}) that the oracle "
        f"crc_main.txt does not; {ADDED_HINT}"
    )
    expected = [flag.option] if flag.short is None else [flag.short, flag.option]
    assert action.option_strings == expected, (action.option_strings, expected)
    assert action.dest == flag.dest
    assert action.default == flag.default, f"default of {flag.option} differs from ADDED"
    assert action.help == flag.help, f"help of {flag.option} differs from ADDED"
    assert action.required is False, f"{flag.option} must be optional (default keeps behaviour)"


def check_additions(new: argparse.ArgumentParser, old: argparse.ArgumentParser) -> None:
    old_by_dest = {a.dest: a for a in old._actions}
    for action in new._actions:
        if action.dest in old_by_dest:
            continue
        _check_added_flag(action)
    listed = {f.dest for f in ADDED_FLAGS}
    present = {a.dest for a in new._actions}
    assert listed <= present, f"ADDED lists flags the parser lacks: {sorted(listed - present)}"
    new_oracle_order = [a.dest for a in new._actions if a.dest in old_by_dest]
    assert new_oracle_order == [a.dest for a in old._actions], "flag order of the oracle changed"
    for action in new._actions:
        previous = old_by_dest.get(action.dest)
        if previous is None:
            continue
        assert type(action) is type(previous), (action.dest, type(action), type(previous))
        for field in COMPARED_FIELDS:
            assert getattr(action, field) == getattr(previous, field), (action.dest, field)
        _check_choices(action, previous)
    assert len(new._mutually_exclusive_groups) == len(old._mutually_exclusive_groups)
    assert new._defaults == old._defaults
    assert len(new._actions) == len(old._actions) + len(ADDED_FLAGS)
    assert not [r for r in ADDED_RESOURCES if r.kind not in _resource_choices(new)], (
        "ADDED_RESOURCES lists a kind that is not a --resource choice"
    )


def _resource_choices(parser: argparse.ArgumentParser) -> list[str]:
    for action in parser._actions:
        if "--resource" in action.option_strings:
            return list(action.choices or [])
    return []


def strip_additions(new: argparse.ArgumentParser, old: argparse.ArgumentParser) -> None:
    """Remove the flags and choices listed in ADDED from `new`; put back the REMOVED ones."""
    old_by_dest = {a.dest: a for a in old._actions}
    for action in list(new._actions):
        if _flag_of(action) is not None and action.dest not in old_by_dest:
            for group in new._action_groups:
                if action in group._group_actions:
                    group._group_actions.remove(action)
            new._actions.remove(action)
            for option in action.option_strings:
                del new._option_string_actions[option]
            continue
        previous = old_by_dest.get(action.dest)
        listed = added_choices(action.option_strings)
        if previous is not None and listed and action.choices is not None:
            extra = {c for c, _ in listed}
            action.choices = [c for c in action.choices if c not in extra]
            helps = {h for _, h in listed if h is not None}
            if len(helps) == 1 and action.help == next(iter(helps)):
                action.help = previous.help
        if previous is not None and removed_choices(action.option_strings):
            action.choices = list(previous.choices or [])


def abbreviation_problems(new: argparse.ArgumentParser, old: argparse.ArgumentParser) -> list[str]:
    """Prefixes of the oracle's long flags that no longer resolve as they did (allow_abbrev)."""
    problems: list[str] = []
    old_longs = [o for o in old._option_string_actions if o.startswith("--")]
    new_longs = [o for o in new._option_string_actions if o.startswith("--")]
    for option in old_longs:
        for end in range(3, len(option) + 1):
            prefix = option[:end]
            before = {old._option_string_actions[o].dest for o in old_longs if o.startswith(prefix)}
            after = {new._option_string_actions[o].dest for o in new_longs if o.startswith(prefix)}
            if len(before) == 1 and after != before:
                problems.append(
                    f"{prefix} meant {sorted(before)}, now {sorted(after)}: a new long flag must "
                    f"not start with {prefix!r}; pick another name"
                )
    return problems


class _BuiltError(Exception):
    def __init__(self, parser: argparse.ArgumentParser) -> None:
        super().__init__()
        self.parser = parser


@functools.cache
def oracle_parser() -> argparse.ArgumentParser:
    """The parser the oracle `get_argparser` builds (parse_args is stopped before it parses)."""
    original = argparse.ArgumentParser.parse_args

    def stop(self: argparse.ArgumentParser, *_: Any, **__: Any) -> Any:
        raise _BuiltError(self)

    argparse.ArgumentParser.parse_args = stop  # type: ignore[method-assign]
    try:
        load_legacy_crc("ag_cli_oracle_parser").get_argparser()
    except _BuiltError as built:
        return built.parser
    finally:
        argparse.ArgumentParser.parse_args = original  # type: ignore[method-assign]
    raise AssertionError("the oracle get_argparser did not reach parse_args")


@contextlib.contextmanager
def stripping(parser: argparse.ArgumentParser | None = None) -> Iterator[None]:
    """While active, every parser parses without the flags and choices listed as ADDED."""
    oracle = parser or oracle_parser()
    original = argparse.ArgumentParser.parse_args

    def parse_args(self: argparse.ArgumentParser, *args: Any, **kwargs: Any) -> Any:
        strip_additions(self, oracle)
        return original(self, *args, **kwargs)

    argparse.ArgumentParser.parse_args = parse_args  # type: ignore[method-assign]
    try:
        yield
    finally:
        argparse.ArgumentParser.parse_args = original  # type: ignore[method-assign]


def has_additions() -> bool:
    return bool(ADDED_FLAGS or ADDED_CHOICES or ADDED_RESOURCES)


def run_script_without_additions(script: str) -> None:
    """Subprocess entry: run `script` as `__main__` with the ADDED flags and choices stripped."""
    with stripping():
        runpy.run_path(script, run_name="__main__")
