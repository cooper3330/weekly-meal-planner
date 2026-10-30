.PHONY: check fmt sync run

RUN ?= uv run python

# The project is not installed (package = false), so put src/ on the import path.
run:
	PYTHONPATH=src $(RUN) -m mealplanner

check:
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy
	uv run pytest
	bash tests/scripts/run.sh

fmt:
	uv run ruff format .
	uv run ruff check --fix .

sync:
	uv sync
