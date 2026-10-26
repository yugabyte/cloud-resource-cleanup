# Copyright (c) Yugabyte, Inc.

"""Region level error policy and summary lines of the EBS disk cleaner."""

import logging
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager

from botocore.exceptions import ClientError

from crc.aws.connectivity import CONNECTIVITY_ERRORS, log_skipped_region
from crc.core import logger

SKIP_CONTEXT = "EBS disk cleanup"
_UNAUTHORISED_CODES = {
    "AuthFailure",
    "UnauthorizedOperation",
    "OptInRequired",
    "InvalidClientTokenId",
}


@contextmanager
def region_guard(region: str, flag_error: Callable[[], None]) -> Iterator[None]:
    """Region level error policy of `main`: connectivity and unauthorised errors skip the
    region, any other error is logged and flagged through `flag_error`; nothing propagates."""
    try:
        yield
    except CONNECTIVITY_ERRORS as e:
        log_skipped_region(region, SKIP_CONTEXT, e)
        logger.current().region_skipped(region, e)
    except ClientError as e:
        code = (e.response.get("Error") or {}).get("Code")
        if code in _UNAUTHORISED_CODES:
            logging.warning("Region %s: skipped EBS disk cleanup (%s)", region, code)
            logger.current().region_skipped(region, str(code))
            return
        flag_error()
        logging.error("Region %s: ClientError during EBS disk cleanup: %s", region, e)
        logger.current().fail(region, "-", e, logged=True)
    except Exception as e:  # noqa: BLE001 - SAF-003 region guard, error flagged and logged, other regions continue
        flag_error()
        logging.error("Region %s: error during EBS disk cleanup: %s", region, e)
        logger.current().fail(region, "-", e, logged=True)


def log_summary(labels: Sequence[str], *, dry_run: bool, had_errors: bool) -> None:
    """The summary lines `main` logs once all regions are done."""
    if not dry_run:
        logging.warning("number of AWS EBS volumes deleted: %s", len(labels))
        logging.warning("List of AWS EBS volumes deleted: %s", list(labels))
    else:
        logging.warning(
            "List of AWS EBS volumes (Total: %s) which will be deleted: %s",
            len(labels),
            list(labels),
        )
    if had_errors:
        # Log only: raising after the region loop would exit non-zero and fail the Jenkins
        # build without undoing work already done.
        logging.error(
            "AWS EBS disk cleanup did not complete for every volume; "
            "see errors above. Deleted/dry-run list may be incomplete "
            "(count=%s).",
            len(labels),
        )
