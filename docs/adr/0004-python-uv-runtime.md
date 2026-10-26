# ADR 0004: Python 3.12 with uv; a short, justified dependency list
- Status: Accepted
- Date: 2026-10-24

## Context
Issue #5, decision 1: pick the language, runtime, and dependency management. The app is a small local web server that calls the Claude API, validates structured output, computes nutrition, and stores data in a local file. Constitution principle 8 asks for the fewest dependencies, each justified in an ADR.

## Decision
- **Language and runtime:** Python 3.12 or newer, CPython.
- **Package and environment management:** [uv](https://docs.astral.sh/uv/). One `pyproject.toml` declares dependencies; `uv.lock` is committed; `uv sync` creates `.venv/` (gitignored). `uv run --env-file .env ...` loads local secrets without an extra library.
- **Runtime dependencies (complete list for the baseline):**

  | Package | Why |
  |---|---|
  | `fastapi` | HTTP routing, form handling, dependency injection for tests (ADR 0005) |
  | `uvicorn` | The ASGI server that serves the app on loopback (ADR 0012) |
  | `jinja2` | Server-side HTML templates (ADR 0005) |
  | `python-multipart` | Required by FastAPI to parse HTML form posts |
  | `pydantic` | Schema validation of model output and user input (ADR 0008). Pulled in by FastAPI anyway; declared explicitly because we depend on it directly |
  | `anthropic` | Official Claude API client (ADR 0008) |

- **Dev dependencies:** `pytest`, `httpx` (required by FastAPI's `TestClient`), `ruff`, `mypy` (ADR 0010).
- SQLite comes from the standard library (`sqlite3`); no ORM (ADR 0006).
- Any new dependency, runtime or dev, needs a line in an ADR (a new one, or an amendment to this table via ADR) saying why the standard library or an existing dependency is not enough.

## Alternatives considered
- **TypeScript on Node (for example Next.js or Express + React).** Strong for rich UIs, but brings a bundler, a larger dependency tree, and a separate validation library. The UI here is forms and lists (ADR 0005), so the cost buys little.
- **Python with pip + venv + requirements.txt.** Works, but no lockfile by default and slower, less reproducible installs. uv gives a lockfile and one tool for Python versions, venvs, and running commands.
- **Python with Poetry.** Comparable, but heavier and slower than uv for no benefit here.
- **Flask instead of FastAPI.** Equally simple, but FastAPI's dependency injection makes swapping in the fake LLM client and a temp database in tests trivial, and it already uses pydantic.

## Consequences
- One language for the server, domain logic, and tests; agents work in a single toolchain.
- Contributors need uv installed (`brew install uv`).
- Changes to `pyproject.toml` or `uv.lock` are dependency changes and are treated as high risk by the delivery loop (#4).

## Constitution check
- P1 Local first: everything runs on the user's machine; `anthropic` is the only library that makes network calls.
- P8 Simple over clever: six runtime dependencies, each justified above; standard-library SQLite.
- No conflict with other principles.
