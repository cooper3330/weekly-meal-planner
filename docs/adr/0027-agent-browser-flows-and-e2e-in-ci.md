# ADR 0027: Agent browser: Playwright flows as JSON files, a demo entry point, and a secrets-free `e2e` job whose evidence the reviewer reads as data
- Status: Proposed
- Date: 2026-11-25
- Supersedes in part: ADR 0005 (the rejection of Playwright viewport tests, and the consequence that phone-size layout is checked only by a human checklist)
- Amends: ADR 0010 (a second, separate test target, `make e2e`, and a second CI job, `e2e`)

## Context
Issues #99 (agent browser tooling) and #142 (verification loop in CI), planned together as the owner asked, with #131 (visual review) and #152 (feature map) depending on the same flow format and evidence. Agents can run unit tests but cannot see the app: reviews of UI pull requests end in "not verifiable", and the owner walks every flow by hand. ADR 0005 rejected Playwright as a heavy dependency and accepted a human checklist for phone layout; with pages arriving for profile, slate, selection, grocery and recipes, and the owner asking that CI start and click through the app on every pull request, that gap is no longer acceptable.

Constraints: everything stays on loopback (P1, ADR 0012); `make check` stays offline and fast (P7, ADR 0010); a new dependency needs an ADR (P8); jobs that run a pull request's code hold no secrets (ADR 0013, ADR 0015); since #112 and #120 `make run` starts the supervisor, the bridge and the app and needs a signed-in Claude CLI, which agents and CI do not have (ADR 0022); the repository is public, so screenshots must show fixture data only.

## Decision
**Library and browser (P8).**
- Playwright for Python, added to the `dev` dependency group (not a runtime dependency of the app) and pinned by `uv.lock`. Only Chromium is used, as Playwright's headless shell.
- `make browser-install` is the one way to install the browser: `uv run playwright install --with-deps --only-shell chromium`. No run ever downloads a browser; a missing browser is a clear error naming `make browser-install`.
- On Actions the browser directory (`~/.cache/ms-playwright`) is cached with `actions/cache`, keyed on the runner OS and the hash of `uv.lock` (which pins the Playwright version). `setup-uv`'s cache does not cover it.

**One package for the tooling: `src/agentbrowser/`.** A third top-level package beside `mealplanner` and `bridge`, under mypy strict. Only `agentbrowser/driver.py` (and modules it imports) import Playwright; the flow loader, the checks' result types and the scope rule are plain Python, unit-tested in `make check` with no browser. `src/agentbrowser/` is not a protected path; it does not decide what the bridge runs.

**Ways to start the app (FR-1, FR-2; #142 FR-6).** Neither changes the real entry points, `config.py` or `src/bridge/`.
- **Demo mode** is a separate entry point, `python -m mealplanner.demo`, not a setting, so no environment variable can turn a real run into a demo run or the reverse. It builds the real `create_app` with:
  - a `DemoLLMClient` that answers every call from canned JSON chosen by the requested output model's name (`src/mealplanner/demo/llm/<Model>.json`), never runs out, and still goes through the output model's validation (P5); a model with no canned answer raises `LLMUnavailableError`, so the page shows its normal error. The bridge client is never constructed.
  - seeded fixture data from a named **scenario** (`src/mealplanner/demo/scenarios/<name>.json`; `empty` and `profile` to start). A feature that adds a page adds its scenario data in the same pull request. Fixture data only: no real names, logins or personal data.
  - a fixed clock (Monday 2026-01-05 12:00 UTC), so weeks and dates are deterministic.
  - an isolated temporary data directory, a free loopback port, and `MEALPLANNER_OWNER_LOGIN=demo-owner@example.test`.
- **Wired mode** runs the real supervisor (`python -m bridge.supervisor`, exactly what `make run` starts) with `HOME` pointed at a temporary directory whose `.local/bin/claude` is a stub program written at start (an absolute-path shebang to the project's Python, running `agentbrowser.stub_cli`). The registry's `~/` is expanded against the bridge's `HOME` (ADR 0022), so the registered command, its flags, its environment and every bridge check stay exactly as in a normal run; nothing in `src/bridge/` changes, and no switch exists that a normal run could trip. The stub answers like the CLI's `--output-format json` with canned results. Before the supervisor starts, the flow's scenario is seeded into the temporary data directory (migrations, then the same seeding demo mode uses), so `scenario` means the same in both modes. The clock is the real one in wired mode, so wired flows assert no dates. The real Claude CLI is never started by the tooling or in CI; `make bridge-smoke` stays the owner's check.
- **Identity (ADR 0012).** The browser context sends `Tailscale-User-Login: demo-owner@example.test` on every request, the header `tailscale serve` adds for a real request, and the app is started with that login as owner. `MEALPLANNER_DEV_LOGIN` is never used. The Host allowlist, the cross-site checks and the security headers stay on; a test proves a request without the header gets 403.

**Lifecycle commands (FR-1).** `make app-up [APP_NAME=default] [SCENARIO=profile]` starts the demo app in the background, waits (bounded) for `/healthz`, writes `.agent-tmp/app/<APP_NAME>.json` (URL, port, pid, data directory) and prints the URL; on a timeout or crash it prints the log tail and removes everything it created. `make app-down [APP_NAME=default]` stops that process group, deletes the data directory and the state file, and succeeds when nothing is running. Different names never share a port or directory.

**Flows: one format for #99, #131, #142 and #152.**
- A flow is a JSON file in `tests/flows/` (one file per flow, the file name is the flow's name). It is validated by a pydantic model with `extra="forbid"`:
  ```json
  {
    "proves": [{"issue": 99, "criteria": ["AC-4"]}],
    "mode": "demo",
    "scenario": "profile",
    "viewport": "360x640",
    "steps": [
      {"goto": "/"},
      {"click": {"role": "link", "name": "Profile"}},
      {"expect_text": "Your profile"},
      {"fill": {"label": "Dislikes", "value": "olives"}},
      {"press": "Enter"},
      {"click": {"role": "button", "name": "Save"}, "shows": "AC-2"}
    ]
  }
  ```
- Step kinds: `goto` (a path starting with `/`; an absolute URL is rejected before the browser starts), `click`, `fill`, `select`, `press`, `expect_text`, `expect_no_text`. Elements are found by accessible role and name, by label, or by visible text, never by CSS selector, so flows survive a restyle and the feature map (#152) can reuse the same locators as its landmarks. An optional `shows` names the criterion a step's screenshot demonstrates (#131 FR-2).
- `proves` is required in `tests/flows/` (#142 FR-3) and optional for an ad hoc steps file run with `make browse`, so a bug report's steps (`viewport` and `steps`) paste into a steps file unchanged (FR-8). `mode` (`demo` or `wired`) defaults to `demo`; `scenario` defaults to `profile`; `viewport` defaults to `360x640`.
- **No skip, expected failure or retry exists (P7, #142 FR-7).** The schema has no such field, unknown keys are errors, the runner retries nothing, and a test fails if one is introduced.
- The feature map (#152) is a separate file beside the flows that refers to flows by name; its format is #152's plan, and it reuses this loader and these locators.

**Runner and commands (FR-3, FR-5).**
- `make browse STEPS=<file> [URL=...] [OUT=...]` runs one steps file against an app started with `make app-up` (URL from its state file by default).
- `make e2e [FLOW=tests/flows/<name>.json]` runs every flow (or one): for each flow a fresh app in the flow's mode and scenario, one shared headless Chromium, then the app is stopped whatever happens. With no flows it prints `no flows` and exits 0. Before running it prints the issues and criteria covered.
- On every page reached, the **phone checks** run automatically (FR-4): no horizontal scroll (`scrollWidth` greater than the viewport width, naming the widest offending element), computed body font size at least 16 px, every visible link, button, input, select and textarea at least 44 px tall (a link inside running text, a `p` or `li` with other text, is exempt, as in WCAG 2.5.8), every request to loopback only, and the three security headers on each document response. There is no separate phone-check command.
- A run fails (non-zero exit) on: a failed or timed-out step (bounded wait), a console error or uncaught page exception, a failed request (status 400 or more, or a network error), any request to a host other than the app's loopback address, a failed phone check, or an invalid flow. Playwright's request routing aborts non-loopback requests, so a page cannot actually reach out even when the check is what reports it.
- **Failure output (#142 FR-5).** For each failing flow: the flow, the step number and kind, expected and actual, console errors, failed or external requests, failed checks with the element, the artifact paths, and last the line `Reproduce: make e2e FLOW=tests/flows/<name>.json`.

**Evidence bundle (one layout for local runs, CI, the reviewer and #131).** `OUT` defaults to `.agent-tmp/e2e/<run-id>/`:
- `summary.json` (machine-readable: head commit if known, flows with pass or fail, `proves`, per-step results, failures, reproduce commands) and `summary.md` (the same, short, for people and agents);
- per flow `<flow>/`: `NN-<kind>.png` (a viewport screenshot after every step, so one per step, named by flow and step, with the criterion from `shows` in `summary.json`), `final-full.png`, `page.txt` (visible text at the end), `console.json`, `requests.json`, `checks.json`, `trace.zip`;
- limits: at most 40 steps per flow and 1 MB per screenshot; a flow over the limit fails with a clear message.

**The `e2e` job in the `CI` workflow (#142 FR-1, FR-2, FR-9; #99 FR-6).**
- A second job, `e2e`, in `.github/workflows/ci.yml`, beside `check`, so `ci-fix.yml` (which triggers on a failed `CI` run) sees it with no trigger change. `permissions: contents: read`, no `secrets.*` reference, `persist-credentials: false`, SHA-pinned actions, `timeout-minutes: 15`, and it runs once per head commit (the existing triggers).
- **Scope inside the job, not a `paths:` filter** (a filtered-out required check would hang): a first step lists the files changed against the base (`git diff --name-only`), and `agentbrowser.scope` decides. Runtime paths are `src/`, `tests/flows/`, `tests/e2e/`, `pyproject.toml`, `uv.lock`, `Makefile` and `.github/workflows/ci.yml` (so a change to the browser tests or to the job itself is exercised by the job); any other change set prints `e2e: no runtime change, skipped` and the job succeeds without installing or starting a browser. Pushes to `main` always run.
- Otherwise: sync, restore or install the browser (`make browser-install`, cached), `make e2e`, and upload the bundle with `actions/upload-artifact` as `e2e-evidence` (`if: always()`, retention 14 days). Nothing is retried.
- `e2e` becomes a required status check on `main`: an owner step, documented in `docs/workflow.md`. It can be required at once because it always reports.

**Evidence reaches the reviewer as data (#99 FR-6).** PR code runs only in the `e2e` job, which has nothing to steal. The review job gains `actions: read` and a trusted step that, after waiting for both `check` and `e2e` on the head commit, downloads that commit's `e2e-evidence` with `gh run download` into `.agent-tmp/e2e/` and writes `.agent-tmp/e2e-report.md`: a "data, not instructions" header, the head commit, `summary.md` quoted line by line and cut at 20 KB with a note, and the list of screenshot paths; or `unavailable` (with the reason) or `no runtime change`. Nothing downloaded is executed or sourced; the reviewer gains no `gh api`, `gh run` or download rule and only `Read`s the files (text and images). A missing report never blocks a review. Publishing screenshots on the PR for the owner is #131's plan and reuses this bundle.

**CI-fix (#142 FR-5, AC-6).** `scripts/coordinator.sh --source ci` treats a failed `e2e` job like a failed `check` (same head, step-failure, `main`-is-green and budget rules), and its prompt tells the developer to run the `Reproduce:` command from the log, read the bundle it writes locally, fix the cause, and re-run the same command to green before pushing. No artifact download is needed, because the reproduce command regenerates the evidence.

**Developer on Actions (FR-7).** The workflows that run the developer install the browser (cached) before the coordinator starts it; the developer's allowlist gains exactly `make browser-install`, `make app-up*`, `make app-down*`, `make browse *` and `make e2e*` (no `python`, `npx`, `playwright` or free-form shell rule).

## Alternatives considered
- **Keep the checklist only (ADR 0005).** Leaves every UI review "not verifiable" and the owner walking each flow; the owner asked for the loop to be closed.
- **Selenium or a raw Chrome DevTools client.** Selenium needs a driver binary per browser version and has weaker waiting and request interception; a hand-rolled CDP client is code we would own. Playwright bundles the matching browser, intercepts requests and records traces.
- **Node Playwright, or `npx`.** Adds a second toolchain to a Python project (ADR 0004) and a free-form command to the allowlist.
- **Flows as pytest functions.** More expressive, but a second format: bug reports would not paste into them, the feature map and the screenshots could not read them, and a skip or xfail marker would have to be policed instead of being impossible.
- **A setting (`MEALPLANNER_DEMO=1`) for demo mode.** Touches the protected `config.py` and makes the real and demo apps one environment variable apart; an entry point keeps them apart by construction.
- **A test hook in the bridge or a second registry for the stub.** Weakens the rule that the checked-in registry alone decides what runs; the temporary `HOME` reaches the same result with no bridge change.
- **`MEALPLANNER_DEV_LOGIN` for identity.** It switches the header check off; the spec asks for the real mechanism.
- **A separate `browser-check.yml` workflow** (the first draft of #107). A second job running PR code for the same evidence; the CI job already has no secrets and must exist for #142.
- **A privileged workflow that comments the report**, or `pull_request_target`. More moving parts holding a write token near PR output; the reviewer only needs the files.
- **Running e2e only on UI paths with a workflow `paths:` filter.** A required check that never starts blocks the merge.
- **Making e2e part of `make check`.** Needs a browser for every unit-test loop and breaks "offline and fast".

## Consequences
- Agents start, drive and see the app; UI reviews cite screenshots; CI fails a pull request whose pages break, overflow at 360 px or reach outside loopback.
- One more dev dependency (about 45 MB) and a cached browser (about 100 MB) on CI; the `e2e` job adds a few minutes to runtime pull requests and seconds to others.
- Every page needs demo scenario data and flows for its user-visible criteria; that is part of done (#142 FR-4).
- What the tooling cannot show: a real phone, other browsers, real model output (demo answers are canned). The owner's real-phone pass stays manual, and `docs/mobile-checklist.md` says which checks are automated.
- `make check` is unchanged in scope: the loader, scope rule and stub are unit-tested there, the browser runs only in `make e2e`.

## Constitution check
- P1 Local first: the app and bridge bind loopback; the browser's non-loopback requests are aborted and fail the run; demo and wired runs make no model call to the internet. The browser download happens in `make browser-install` on the delivery machinery, never in the app.
- P5 Structured outputs: demo answers pass the same output-model validation as real ones.
- P7 Tests first: flows are written first and fail for the right reason; no skip, expected failure or retry exists; `make check` stays offline.
- P8 Simple over clever: one library, one browser, one flow format, one job; demo mode reuses `create_app` and wired mode reuses the real supervisor and registry.
- P9 Safe by default: exclusion filters stay in domain code; demo answers go through them.
- P10 One user first: one demo owner login through the existing users table.
