# weekly-meal-planner

A locally hosted, Claude-powered meal planner for one person. It learns your food preferences and each week proposes a slate of lunch and dinner options that fit your tastes and dietary restrictions, showing the calories and macros of each (think Factor's weekly menu). You pick the meals you want; it then produces a consolidated grocery list and a recipe for each pick.

## How the project is built

Development is **spec-driven**, with specs, plans, and tasks living in **GitHub issues**, run through a **GitHub Project** by three Claude agents:

| Agent | File | Job |
|---|---|---|
| Coordinator | `.claude/agents/coordinator.md` | Watches the project for issues in **Ready**, routes each to the right agent, updates status |
| Architect | `.claude/agents/architect.md` | Turns a spec into a technical plan, task breakdown, and ADRs |
| Developer | `.claude/agents/developer.md` | Implements one task from an approved plan, with tests, and opens a PR |

Read [`docs/workflow.md`](docs/workflow.md) for the full lifecycle, [`docs/constitution.md`](docs/constitution.md) for project principles, and [`docs/product-spec.md`](docs/product-spec.md) for the product vision.

## Repo layout

```
.claude/agents/      agent definitions (coordinator, architect, developer)
.github/             issue templates, PR template
docs/                constitution, product spec, workflow, backlog, ADRs (docs/adr/)
scripts/             project setup + coordinator loop
CLAUDE.md            instructions every Claude session loads
```

## Getting started

1. Create the GitHub Project and labels: `./scripts/setup-project.sh` (needs `gh` with the `project` scope).
2. Start the coordinator: `./scripts/coordinator.sh` (polls every few minutes; `--once` for a single pass).
3. Write a feature spec as a GitHub issue using the **Feature spec** template, assign it to a milestone, and move it to **Ready**.

The first issues to file are listed in [`docs/backlog.md`](docs/backlog.md).

To ask an agent to change its PR or its plan, comment `/revise` (or submit a review as **Request changes**); see the review and revision loop in [`docs/workflow.md`](docs/workflow.md).

Agent identity (docs/adr/0014): in Actions the coordinator requires `AGENT_GH_TOKEN` (a GitHub App token) and fails without it. Locally it is optional: without it, agents post as your own `gh` login and it logs that once. Board moves go only through `scripts/board.sh`, which uses `PROJECT_TOKEN` (a project-only classic PAT) when set and your `gh` login otherwise. Set `AGENT_GIT_NAME` and `AGENT_GIT_EMAIL` together to make agent commits use that identity.

The helper scripts have offline tests (stubbed `gh`, `claude`, and `date`): `bash tests/scripts/run.sh`. `./scripts/setup-project.sh` is safe to re-run. The coordinator runs headless, so it only uses an allowlist of commands; add more with `EXTRA_ALLOWED_TOOLS` (comma-separated) if a run stalls on one. Claude Code stops waiting for background subagents after 10 minutes in headless mode, which cuts off a long architect run, so the coordinator sets `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0` (wait indefinitely) unless you set it yourself, for example to `7200000` to cap runs at two hours.

## Development

The app is Python 3.12+ managed with [uv](https://docs.astral.sh/uv/) (ADR 0004).

1. Install uv: `brew install uv`.
2. Install dependencies: `uv sync` (or `make sync`).
3. Run all checks: `make check`. It runs ruff format, ruff lint, mypy, pytest, then the script tests, and stops at the first failure. Exit 0 means green. `make fmt` applies formatting fixes.
4. Follow the test-first loop (ADR 0010): write the tests named in the task and run `make check` to see them fail for the right reason; commit the failing tests first; then implement until `make check` exits 0. Never weaken, skip, or delete a test to get green.

Tests run offline with no API key: an autouse fixture blocks internet sockets and removes `ANTHROPIC_API_KEY`. Copy `.env.example` to `.env` for local runs.
