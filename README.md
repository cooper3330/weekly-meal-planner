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

### Running the agents

By default the agents run on GitHub Actions (hourly, and on events such as the `ready` label); the owner must first make the one-time settings in "Manual repository settings" in [`docs/workflow.md`](docs/workflow.md). To run them on your machine instead, use `scripts/coordinator.sh` (`--once` for a single pass). Low-risk PRs auto-merge after CI and the reviewer agent; `risk:high` PRs wait for you.

To ask an agent to change its PR or its plan, comment `/revise` (or submit a review as **Request changes**); see the review and revision loop in [`docs/workflow.md`](docs/workflow.md).

Agent identity (docs/adr/0014): in Actions the coordinator requires `AGENT_GH_TOKEN` (a GitHub App token) and fails without it. Locally it is optional: without it, agents post as your own `gh` login and it logs that once. Board moves go only through `scripts/board.sh`, which uses `PROJECT_TOKEN` (a project-only classic PAT) when set and your `gh` login otherwise. Set `AGENT_GIT_NAME` and `AGENT_GIT_EMAIL` together to make agent commits use that identity.

The helper scripts have offline tests (stubbed `gh`, `claude`, and `date`): `bash tests/scripts/run.sh`. `./scripts/setup-project.sh` is safe to re-run. The coordinator runs headless, so it only uses an allowlist of commands; add more with `EXTRA_ALLOWED_TOOLS` (comma-separated) if a run stalls on one. Claude Code stops waiting for background subagents after 10 minutes in headless mode, which cuts off a long architect run, so the coordinator sets `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0` (wait indefinitely) unless you set it yourself, for example to `7200000` to cap runs at two hours.

## Development

The app is Python 3.12+ managed with [uv](https://docs.astral.sh/uv/) (ADR 0004).

1. Install uv: `brew install uv`.
2. Install dependencies: `uv sync` (or `make sync`).
3. Run all checks: `make check`. It runs ruff format, ruff lint, mypy, pytest, then the script tests, and stops at the first failure. Exit 0 means green. `make fmt` applies formatting fixes.
4. Follow the test-first loop (ADR 0010): write the tests named in the task and run `make check` to see them fail for the right reason; commit the failing tests first; then implement until `make check` exits 0. Never weaken, skip, or delete a test to get green.

## CLI bridge

The bridge (`src/bridge/`, ADR 0022) is a small loopback-only server that runs the registered `claude` command with the caller's text on standard input. `python -m bridge` needs `MEALPLANNER_BRIDGE_PORT` (1-65535) and `MEALPLANNER_BRIDGE_TOKEN` (at least 32 characters); `MEALPLANNER_BRIDGE_HOST` defaults to `127.0.0.1` and may only be `127.0.0.1` or `::1`. Anything else prints one line naming the variable and exits with status 2.

- Minimum Claude CLI version: 2.1.248 (it adds `--restricted`). An older CLI rejects the flag and the run fails closed.
- Smoke check: `make bridge-smoke` runs the real CLI once with a tiny prompt, with the bridge's minimal environment, and prints the CLI version, exit code, whether the JSON envelope parsed, `is_error`, and whether `result` was the expected object. It never prints the environment. It is not part of `make check`; run it yourself on your machine.
- If the smoke check fails, the bridge does not ship (#112 AC-13).

### `make run`

`make run` starts `python -m bridge.supervisor`. It always starts the bridge first (whatever `MEALPLANNER_LLM_BACKEND` says; starting the bridge does not need the Claude CLI), polls `GET /healthz` every 100 ms for up to 15 s, then starts the app and prints one line with both process ids.

- The port is a free one chosen per run and the token comes from `secrets.token_urlsafe(32)`. Both reach the bridge and the app only through their environment (`MEALPLANNER_BRIDGE_HOST`, `MEALPLANNER_BRIDGE_PORT`, `MEALPLANNER_BRIDGE_TOKEN`). They are never printed, logged, written to a file or put on a command line. The bridge's environment has `ANTHROPIC_API_KEY` removed.
- Both children run in their own process group. On Ctrl-C, SIGTERM or SIGHUP, or when either child exits, the supervisor sends SIGTERM to each group, waits up to 5 s, then SIGKILL. Exit status: 130 on Ctrl-C, 128 plus the signal number for other signals, otherwise the status of the child that exited first (1 if it exited with 0).
- If the supervisor itself is killed (even with SIGKILL), both children stop: each started with `--lifeline-stdin` reads its standard input until the pipe closes, then terminates itself.
- If the port is taken between being chosen and the bridge binding it, the supervisor exits 1 with one line; run again.
- Supported on macOS and Linux.

Before merging the PR that introduced the supervisor, the owner checks it with the real, signed-in CLI: run `make bridge-smoke`, then `MEALPLANNER_LLM_BACKEND=bridge make run`. If the smoke check fails with the traffic switches off, the bridge does not ship.

## Run it on your tailnet

The server listens on 127.0.0.1 only. `tailscale serve` publishes it over HTTPS to your tailnet and adds your identity (ADR 0012).

1. Copy `.env.example` to `.env`. Set `MEALPLANNER_OWNER_LOGIN` to your Tailscale login (for example `you@example.com`) and `MEALPLANNER_ALLOWED_HOSTS` to this machine's ts.net name (for example `box.tailnet-name.ts.net`). Requests with any other Host header get 400.
2. Start the app: `uv run --env-file .env python -m mealplanner` (with `PYTHONPATH=src`, as `make run` does).
3. Publish it to your tailnet: `tailscale serve --bg 8000`. Open `https://<machine>.<tailnet>.ts.net` on your phone.

Rules:
- **Never use `tailscale funnel`.** It would publish the app to the internet. `tailscale funnel status` must show nothing.
- **Do not set `MEALPLANNER_DEV_LOGIN` while serving.** It lets requests without an identity (for example from tagged devices) in as that login. It is for local development at `http://127.0.0.1:8000` only, and the app logs a warning when it is set.
- **Add a Tailscale ACL** (a second layer) that limits port 443 on this machine to your own devices, for example in the tailnet policy file:

  ```json
  {"action": "accept", "src": ["you@example.com"], "dst": ["tag:mealplanner:443"]}
  ```

  Tag this machine `tag:mealplanner` (or use its name) and make sure no other rule grants access to it.

Anyone whose login is not in the users table gets 403. POST, PUT, PATCH, and DELETE requests from other sites are refused.

Tests run offline with no API key: an autouse fixture blocks internet sockets and removes `ANTHROPIC_API_KEY`. Copy `.env.example` to `.env` for local runs.
