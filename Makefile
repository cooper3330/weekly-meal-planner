.PHONY: check fmt sync run bridge-smoke app-up app-down

RUN ?= uv run python
APP_NAME ?= default
SCENARIO ?= profile

# Demo app for agents (#100): `make app-up [APP_NAME=x] [SCENARIO=y]`, then `make app-down`.
app-up:
	PYTHONPATH=src $(RUN) -m agentbrowser up --name $(APP_NAME) --scenario $(SCENARIO)

app-down:
	PYTHONPATH=src $(RUN) -m agentbrowser down --name $(APP_NAME)

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

fmt:
	uv run ruff format .
	uv run ruff check --fix .

sync:
	uv sync
