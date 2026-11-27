.PHONY: check fmt sync run bridge-smoke browser-install app-up app-down browse e2e

RUN ?= uv run python
APP_NAME ?= default
SCENARIO ?= profile

# Demo app for agents (#100): `make app-up [APP_NAME=x] [SCENARIO=y]`, then `make app-down`.
app-up:
	PYTHONPATH=src $(RUN) -m agentbrowser up --name $(APP_NAME) --scenario $(SCENARIO)

app-down:
	PYTHONPATH=src $(RUN) -m agentbrowser down --name $(APP_NAME)

# Drive the app in headless Chromium (#102): `make browse STEPS=flow.json [URL=...] [OUT=...]`.
# URL defaults to the app from `make app-up APP_NAME=...`; OUT to .agent-tmp/e2e/<run-id>/.
browse:
	@test -n "$(STEPS)" || { echo "Usage: make browse STEPS=<file> [URL=...] [OUT=...] [APP_NAME=default]" >&2; exit 2; }
	PYTHONPATH=src $(RUN) -m agentbrowser browse --steps "$(STEPS)" --name $(APP_NAME) $(if $(URL),--url "$(URL)") $(if $(OUT),--out "$(OUT)")

# Run every flow in tests/flows/ (or one: FLOW=tests/flows/<name>.json) against a fresh demo app
# each, then walk every feature in tests/e2e/feature-map.json (or one: FEATURE=<name>) from the
# home page, then the browser tests in tests/e2e/ (only without FLOW or FEATURE). Needs
# `make browser-install`.
e2e:
	@rc=0; \
	PYTHONPATH=src $(RUN) -m agentbrowser e2e $(if $(FLOW),--flow "$(FLOW)") $(if $(FEATURE),--feature "$(FEATURE)") $(if $(OUT),--out "$(OUT)") || rc=1; \
	$(if $(or $(FLOW),$(FEATURE)),,uv run pytest tests/e2e -o addopts= || rc=1;) \
	exit $$rc

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
