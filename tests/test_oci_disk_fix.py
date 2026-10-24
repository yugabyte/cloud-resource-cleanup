"""Unknown attachments keep the volumes (BUG-096, BUG-097, BUG-098).

When the volume or boot volume attachments of a region cannot be listed (connectivity or auth
error, availability domain listing failure, or no availability domain), every volume of that
kind in the region is kept and the region is recorded as skipped. Other regions and the other
volume kind are decided as before.
"""

import logging
from collections.abc import Callable, Sequence
from typing import Any

import pytest
from oci.exceptions import ConnectTimeout, RequestException, ServiceError

from crc.core import logger
from crc.core.cleaner import Operation, RunContext
from crc.oci.disk import DiskCleaner, DiskSettings
from crc.oci.disk.api import SdkAttachment, VolumeApi
from tests.helpers_wc_oci_disk import run_disk
from tests.test_oci_disk import MUTATING, NOW, FakeApi, att, vol

FILTER = {"env": ["ci"]}
SETTINGS = DiskSettings(filter_tags=FILTER, age={"hours": 5})
ERRORS: list[Callable[[], Exception]] = [
    lambda: ConnectTimeout(Exception("timed out")),
    lambda: RequestException(Exception("no route")),
    lambda: ServiceError(401, "NotAuthenticated", {}, "denied"),
]


class FlakyApi(FakeApi):
    def __init__(
        self,
        *,
        attachments_error: Exception | None = None,
        domains: Sequence[str] = ("AD-1",),
        domains_error: Exception | None = None,
        boot_errors: dict[str, Exception] | None = None,
        boot_by_domain: dict[str, list[Any]] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.attachments_error = attachments_error
        self.domains = list(domains)
        self.domains_error = domains_error
        self.boot_errors = boot_errors or {}
        self.boot_by_domain = boot_by_domain or {}

    def list_volume_attachments(self) -> Sequence[SdkAttachment]:
        if self.attachments_error is not None:
            self.calls.append(("list_volume_attachments",))
            raise self.attachments_error
        return super().list_volume_attachments()

    def list_availability_domains(self) -> Sequence[str]:
        self.calls.append(("list_availability_domains",))
        if self.domains_error is not None:
            raise self.domains_error
        return self.domains

    def list_boot_volume_attachments(self, availability_domain: str) -> Sequence[SdkAttachment]:
        self.calls.append(("list_boot_volume_attachments", availability_domain))
        if availability_domain in self.boot_errors:
            raise self.boot_errors[availability_domain]
        found: Sequence[SdkAttachment] = self.boot_by_domain.get(
            availability_domain, self.boot_attachments
        )
        return found


def context(*, dry_run: bool = False) -> RunContext:
    return RunContext(dry_run=dry_run, operation=Operation.DELETE, options={}, now=NOW)


def cleaner(apis: dict[str, VolumeApi]) -> DiskCleaner:
    return DiskCleaner(lambda region: apis[region], lambda: list(apis), SETTINGS)


def mutations(api: FakeApi) -> list[tuple[str, ...]]:
    return [call for call in api.calls if call[0] in MUTATING]


@pytest.mark.parametrize("make_error", ERRORS)
def test_volume_attachment_error_keeps_every_block_volume(
    make_error: Callable[[], Exception], caplog: pytest.LogCaptureFixture
) -> None:
    api = FlakyApi(
        volumes=[vol("a"), vol("b")], boot_volumes=[vol("c")], attachments_error=make_error()
    )
    subject = cleaner({"r1": api})
    plan = subject.plan(context())
    assert [c.id for c in plan.candidates] == ["c"]
    assert [(s.id, s.reason) for s in plan.skipped] == [
        ("a", "block volume attachments unknown"),
        ("b", "block volume attachments unknown"),
    ]
    result = subject.apply(plan, context())
    assert result.succeeded == ("name-c",)
    assert mutations(api) == [("delete_boot_volume", "c")]
    assert "Region r1: keeping every disk" in caplog.text


@pytest.mark.parametrize("make_error", ERRORS)
def test_availability_domain_error_keeps_every_boot_volume(
    make_error: Callable[[], Exception], caplog: pytest.LogCaptureFixture
) -> None:
    api = FlakyApi(
        volumes=[vol("a")], boot_volumes=[vol("b"), vol("c")], domains_error=make_error()
    )
    subject = cleaner({"r1": api})
    plan = subject.plan(context())
    assert [c.id for c in plan.candidates] == ["a"]
    assert [(s.id, s.reason) for s in plan.skipped] == [
        ("b", "boot volume attachments unknown"),
        ("c", "boot volume attachments unknown"),
    ]
    subject.apply(plan, context())
    assert mutations(api) == [("delete_volume", "a")]
    assert "Region r1: keeping every boot disk" in caplog.text


@pytest.mark.parametrize("make_error", ERRORS)
def test_boot_attachment_error_keeps_every_boot_volume(
    make_error: Callable[[], Exception],
) -> None:
    api = FlakyApi(boot_volumes=[vol("a")], boot_errors={"AD-1": make_error()})
    plan = cleaner({"r1": api}).plan(context())
    assert plan.candidates == ()
    assert [s.id for s in plan.skipped] == ["a"]


def test_error_in_a_later_domain_discards_the_attachments_found_so_far() -> None:
    api = FlakyApi(
        boot_volumes=[vol("a"), vol("b")],
        domains=["AD-1", "AD-2"],
        boot_by_domain={"AD-1": [att("a")]},
        boot_errors={"AD-2": ConnectTimeout(Exception("timed out"))},
    )
    plan = cleaner({"r1": api}).plan(context())
    assert plan.candidates == ()
    assert sorted(s.id for s in plan.skipped) == ["a", "b"]


def test_region_without_availability_domains_keeps_every_boot_volume(
    caplog: pytest.LogCaptureFixture,
) -> None:
    api = FlakyApi(volumes=[vol("a")], boot_volumes=[vol("b")], domains=[])
    subject = cleaner({"r1": api})
    plan = subject.plan(context())
    assert [c.id for c in plan.candidates] == ["a"]
    assert [(s.id, s.reason) for s in plan.skipped] == [("b", "boot volume attachments unknown")]
    assert ("list_boot_volume_attachments", "AD-1") not in api.calls
    assert "Region r1: keeping every boot disk" in caplog.text


def test_known_empty_attachments_still_delete() -> None:
    api = FakeApi(volumes=[vol("a")], boot_volumes=[vol("b")])
    plan = cleaner({"r1": api}).plan(context())
    assert [c.id for c in plan.candidates] == ["a", "b"]
    assert plan.skipped == ()


def test_other_regions_are_decided_as_before() -> None:
    broken = FlakyApi(volumes=[vol("a")], attachments_error=ConnectTimeout(Exception("x")))
    healthy = FakeApi(volumes=[vol("b")], attachments=[att("c")])
    plan = cleaner({"r1": broken, "r2": healthy}).plan(context())
    assert [(c.region, c.id) for c in plan.candidates] == [("r2", "b")]


def test_non_available_volume_keeps_its_state_reason() -> None:
    api = FlakyApi(volumes=[vol("a", state="TERMINATED")], attachments_error=RequestException("x"))
    plan = cleaner({"r1": api}).plan(context())
    assert [s.reason for s in plan.skipped] == ["state is not AVAILABLE"]


def test_unexpected_attachment_error_still_propagates() -> None:
    api = FlakyApi(volumes=[vol("a")], attachments_error=ValueError("boom"))
    with pytest.raises(ValueError, match="boom"):
        cleaner({"r1": api}).plan(context())


def test_plan_in_dry_run_lists_only_kept_unknown_as_not_deleted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    api = FlakyApi(volumes=[vol("a")], attachments_error=ConnectTimeout(Exception("x")))
    cleaner({"r1": api}).plan(context(dry_run=True))
    assert "List of OCI disk (Total: 0) which will be deleted: []" in caplog.text
    assert mutations(api) == []


def run_with_log(action: Callable[[], object]) -> logger.RunLog:
    run = logger.begin("oci", "disk", "delete", False, {})
    try:
        action()
    finally:
        logger.finish()
    return run


def test_run_log_counts_one_skipped_region_and_each_kept_volume() -> None:
    api = FlakyApi(
        volumes=[vol("a"), vol("b")],
        boot_volumes=[vol("c")],
        attachments_error=ConnectTimeout(Exception("x")),
        domains_error=ConnectTimeout(Exception("y")),
    )
    subject = cleaner({"r1": api, "r2": FakeApi()})
    run = run_with_log(lambda: subject.plan(context()))
    assert run._regions_skipped == 1
    assert run._skips == {"attachments_unknown": 3}


def test_engine_keeps_volumes_with_unknown_attachments() -> None:
    api = FlakyApi(
        volumes=[vol("a")],
        boot_volumes=[vol("b")],
        attachments_error=ConnectTimeout(Exception("x")),
        domains=[],
    )
    outcome = run_disk({"r1": api}, dry_run=False, settings=SETTINGS, now=NOW)
    assert outcome.result is not None
    assert outcome.result.succeeded == ()
    assert mutations(api) == []
