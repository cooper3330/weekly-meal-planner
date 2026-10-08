# weekly-meal-planner

A locally hosted, Claude-powered meal planner. It learns your food preferences and each week proposes a slate of lunch and dinner options that hit your protein and calorie targets (think Factor's weekly menu). You pick the meals you want; it then produces a consolidated grocery list and a recipe for each pick.

## How the project is built

Development is **spec-driven** and run through a **GitHub Project** by three Claude agents:

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
docs/                constitution, product spec, workflow, ADRs
specs/NNN-name/      one folder per feature: spec.md -> plan.md -> tasks.md
scripts/             project setup + coordinator loop
CLAUDE.md            instructions every Claude session loads
```

## Getting started

1. Create the GitHub Project and labels: `./scripts/setup-project.sh` (needs `gh` with the `project` scope).
2. Start the coordinator: `./scripts/coordinator.sh` (polls every few minutes; `--once` for a single pass).
3. Write a feature spec as a GitHub issue using the **Feature spec** template and move it to **Ready**.

The first issues to file are listed in [`docs/backlog.md`](docs/backlog.md).
