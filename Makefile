.PHONY: check fmt sync

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
