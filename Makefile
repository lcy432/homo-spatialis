# Command surface for homo-spatialis. See CLAUDE.md, 命令约定.
# Override the tool if uv is not on PATH:  make test UV="python -m uv"
UV ?= uv

.PHONY: help setup test lint fmt check-env data freeze demo clean

help:
	@echo "setup      install dependencies and git hooks (uv sync)"
	@echo "test       run the test suite (pytest)"
	@echo "lint       ruff check + ruff format --check"
	@echo "fmt        apply ruff fixes and formatting"
	@echo "check-env  verify toolchain, keys and base-model connectivity"
	@echo "data       M1: fetch and clean source statistics (idempotent)"
	@echo "freeze     M1.5: build and freeze reference populations and quantile tables"
	@echo "demo       run the minimal end-to-end example"

setup:
	$(UV) sync
	$(UV) run pre-commit install

test:
	$(UV) run pytest

lint:
	$(UV) run ruff check .
	$(UV) run ruff format --check .

fmt:
	$(UV) run ruff check --fix .
	$(UV) run ruff format .

check-env:
	$(UV) run python scripts/check_env.py

data:
	$(UV) run python scripts/fetch_data.py

freeze:
	$(UV) run python scripts/freeze_reference.py

demo:
	$(UV) run python scripts/demo.py

clean:
	rm -rf .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
