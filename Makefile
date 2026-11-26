.PHONY: check fmt sync run bridge-smoke browser-install

RUN ?= uv run python

# The project is not installed (package = false), so put src/ on the import path.
run:
	PYTHONPATH=src $(RUN) -m bridge.supervisor

check:
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy
	uv run pytest
	bash tests/scripts/run.sh

# Owner-run check against the real Claude CLI (#112 AC-13). Not part of `check`.
bridge-smoke:
	PYTHONPATH=src $(RUN) -m bridge.smoke

# Installs the Chromium that the agent-browser commands drive (ADR 0028). Not part of `check`.
browser-install:
	uv run playwright install --with-deps --only-shell chromium

fmt:
	uv run ruff format .
	uv run ruff check --fix .

sync:
	uv sync
