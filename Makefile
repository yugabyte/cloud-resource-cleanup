PYTHON ?= $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi)

.PHONY: check
check:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m mypy
	$(PYTHON) -c "import sys; from importlinter.cli import lint_imports_command; sys.exit(lint_imports_command())"
	$(PYTHON) -m pytest -q
