# ADR 0011: Repo skeleton and module boundaries
- Status: Accepted
- Date: 2026-10-24

## Context
Issue #5, decision 8: agree the layout and module boundaries so every feature plan puts code in the same places and dependencies point one way.

## Decision
Layout (a `src/` layout, so tests import the installed package, not the working tree):

```
pyproject.toml  uv.lock  Makefile  .env.example
src/mealplanner/
  config.py          settings from environment variables (no other module reads os.environ)
  app.py             create_app(settings, llm_client=None, ...) -> FastAPI; wiring only
  web/               routes, request/response handling, auth middleware (ADR 0012)
    templates/       Jinja2 templates (ADR 0005)
    static/          app.css and any small JS
  db/                connection factory, migration runner, migrations/NNNN_*.sql (ADR 0006)
  llm/               LLMClient protocol, AnthropicLLMClient, FakeLLMClient, errors (ADR 0008)
  nutrition/         ingredient catalog, unit conversion, nutrition computation (ADR 0007; built in #7)
  <feature>/         one package per feature, created by that feature's tasks:
                     profile/ (#6), planning/ (#7, #8), grocery/ (#9), recipes/ (#10), feedback/ (#11)
tests/
  conftest.py        offline guard, shared fixtures (ADR 0010)
  unit/  web/  fixtures/  scripts/ (existing)
docs/  scripts/  .github/  .claude/   (unchanged)
data/                runtime database and backups (gitignored)
```

Boundaries:
- **Dependency direction:** `web` -> feature packages -> (`db`, `llm`, `nutrition`). Feature and core packages never import `web`, FastAPI, or Jinja2.
- **Feature packages** expose plain functions or small classes that take explicit inputs (`user_id`, a connection, an `LLMClient`) and return pydantic models or dataclasses. This keeps them testable without HTTP, and reusable later behind an MCP server (issue #5, Requirement 4).
- **Each feature package owns its tables** (its migration files), its prompts, and its pydantic schemas.
- **`config.py` is the only reader of environment variables;** `app.py` is the only place implementations are chosen.
- **Packages are created when a task needs them.** The baseline creates only `config`, `app`, `web`, `db`, and `llm`.

## Alternatives considered
- **Flat layout (package at repo root).** Tests can accidentally import uninstalled code; the `src/` layout avoids this at no cost with uv.
- **Layered packages across features** (`models/`, `services/`, `repositories/`). Spreads one feature across many folders and makes vertical-slice tasks touch everything. Feature packages keep a task's changes together.
- **Separate API service and frontend.** Rejected with the SPA in ADR 0005.

## Consequences
- A feature task usually touches one feature package, its templates, a migration, and tests.
- Business logic is reachable without the web layer, which suits tests and a future MCP front end.

## Constitution check
- P7 Tests first: explicit inputs and injected clients make units testable in isolation.
- P8 Simple over clever: a handful of packages, created on demand.
- P10 One user first: `user_id` is an explicit parameter at the feature boundary.
