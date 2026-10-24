# ADR 0010: ruff, mypy, pytest, and one `make check` command as the test-first loop's exit condition
- Status: Accepted
- Date: 2026-10-24

## Context
Issue #5, decision 7 and AC-5, building on constitution principle 7 and ADR 0003. Tests are written first; the suite must be self-validating so an agent can loop until it passes; LLM calls are mocked and tests run offline with no API key. The CI work in #4 runs the same checks.

## Decision
- **Tools (dev dependencies, ADR 0004):**
  - `ruff` for linting and formatting
  - `mypy` in strict mode on `src/`
  - `pytest` for tests, with `httpx` for FastAPI's `TestClient`
  
  Configuration lives in `pyproject.toml`.
- **One command:** `make check` runs, in order and failing fast:
  `uv run ruff format --check .` -> `uv run ruff check .` -> `uv run mypy` -> `uv run pytest` -> `bash tests/scripts/run.sh` (the existing offline script tests).
  **`make check` exiting 0 is the definition of green.** Developers and agents loop on it, and CI (#4) calls exactly this target. `make fmt` applies formatting fixes.
- **Offline by construction:**
  - An autouse fixture in `tests/conftest.py` patches `socket.socket.connect` (and `connect_ex`) to raise for internet (`AF_INET`/`AF_INET6`) addresses, and removes `ANTHROPIC_API_KEY` from the environment for every test.
  - A test proves the guard works (a connection attempt fails).
  - A test that needs the network fails loudly rather than silently passing.
- **How LLM calls are mocked:**
  - Feature code receives an `LLMClient` (ADR 0008).
  - Tests pass a `FakeLLMClient` with scripted responses, through the app factory or FastAPI dependency overrides. The fake runs the real validation path, so retry and rejection behavior is tested too.
  - Scripted responses live as JSON fixtures in `tests/fixtures/llm/`.
  - The real `AnthropicLLMClient` is unit-tested against a stubbed SDK client object, never the network.
- **How macro and allergen validation are tested:**
  - **Nutrition:** expected values are hand-computed from specific catalog rows and asserted exactly (before display rounding), plus rounding tests at the display layer.
  - **Catalog integrity tests** (ADR 0007).
  - **Adversarial fixtures** that must be rejected or filtered, never displayed:
    - an excluded ingredient by id
    - an allergen arriving through a composite ingredient (for example soy sauce for a wheat exclusion)
    - an unknown ingredient id
    - a unit with no conversion
    - zero, negative, or absurd quantities
    - model-supplied nutrition fields (rejected by `extra="forbid"`)
    - missing or mistyped fields
    - an empty or short slate
  - Each feature that renders options adds tests asserting that every displayed number equals the code-computed value.
- **Real dependencies where cheap:** SQLite tests use a real database file in `tmp_path`, with no mocking of the storage layer. HTTP tests use `TestClient` against the real app factory.
- **Layout:** `tests/unit/` mirrors `src/mealplanner/`; `tests/web/` holds HTTP-level flow tests; `tests/fixtures/` holds data. Tests are deterministic: no wall-clock or random dependence without injection.
- **Test-first workflow (the agent loop):**
  1. The architect writes "Tests to write first" in each task issue, including edge cases.
  2. The developer writes those tests and runs `make check`. It must fail, and fail for the right reason (an assertion or a missing behavior, not a syntax or import error in the test itself). The failing tests are committed first in their own commit.
  3. The developer implements and reruns `make check` until it exits 0. It may loop without human input.
  4. Tests are never weakened, skipped, `xfail`ed, or deleted to get green. A test believed to be wrong is called out in the PR for human review.
  5. The PR template's checklist confirms these steps; CI (#4) re-runs `make check`.
- **No coverage threshold.** Coverage targets invite low-value tests. Review against the task's listed tests instead.

## Alternatives considered
- **pyright instead of mypy.** Comparable; mypy is the more common default and runs from `uv run` with no Node dependency.
- **black + isort + flake8.** Three tools where ruff is one.
- **pytest-socket for the network guard.** Works, but it is a dependency for what is a ten-line fixture.
- **Live API smoke tests in the suite.** Non-deterministic, cost money, and need a key. Out of `make check`; a manual smoke command can be proposed later.
- **A shell script (`./check`) instead of make.** Equivalent. `make` ships with macOS and gives named targets (`check`, `fmt`, `run`).

## Consequences
- An agent knows when it is done without human judgment: `make check` is green.
- The suite stays fast and free to run, since it needs no network and no API key.
- CI in #4 has a single, already-defined entry point.

## Constitution check
- P7 Tests first: the workflow, offline guard, and fake client implement it directly.
- P2/P9: nutrition and allergen handling have mandated exact-value and adversarial tests.
- P5 Structured outputs: schema-violation fixtures exercise retry and rejection.
- P8 Simple over clever: three dev tools plus `httpx`, with no plugins.
