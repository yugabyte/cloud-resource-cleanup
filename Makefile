PYTHON ?= $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi)
LINT_IMPORTS = $(PYTHON) -c "import sys; from importlinter.cli import lint_imports_command; sys.exit(lint_imports_command())"
SLOW_PARITY = tests/test_cli_main_parity.py tests/test_cli_crc_parity.py tests/test_cli_args_parity.py

.PHONY: check check-fast lint test-file raise-test-floor smoke-offline doctor
check: lint
	$(PYTHON) -m pytest -q -n auto
	$(PYTHON) -m tests.archtest.test_count check

# Everything but the three slow CLI parity files; run `make check` before a commit.
check-fast: lint
	$(PYTHON) -m pytest -q -n auto $(addprefix --ignore=,$(SLOW_PARITY))

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .
	$(PYTHON) -m mypy
	$(LINT_IMPORTS)

# One `python crc.py --dry_run` per (cloud, kind) of the Jenkins commands, SDKs faked, sockets denied.
smoke-offline:
	$(PYTHON) scripts/smoke_offline.py

# Python version, and ruff/mypy/pytest at the versions of constraints.txt; prints the install command when not.
doctor:
	$(PYTHON) scripts/doctor.py

# Usage: make test-file F=tests/test_x.py
test-file:
	@test -n "$(F)" || { echo "usage: make test-file F=tests/test_x.py"; exit 2; }
	$(PYTHON) -m pytest -q $(F)

# Raises tests/archtest/test_count_floor.txt when the test count rose; never lowers it.
raise-test-floor:
	$(PYTHON) -m tests.archtest.test_count raise
