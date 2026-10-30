# Copyright (c) Yugabyte, Inc.

# Entry point of the Slack digest poster: `python crc_digest.py --dir <dir>`. main() lives in
# crc/cli/digest.py; the Jenkins triggers job runs it once after the cleanup stages.

from crc.cli.digest import main

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
