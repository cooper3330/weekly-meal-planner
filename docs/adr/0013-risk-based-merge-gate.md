# ADR 0013: Risk-based merge gate: CI runs `make check`, path rules classify risk, a reviewer agent approves, low-risk PRs auto-merge
- Status: Accepted
- Date: 2026-10-26

## Context
Issue #4 (FR-1 to FR-5, FR-7; AC-1 to AC-5, AC-7). Today a human merges every PR and nothing independent verifies agent output. The owner decided that any PR that is not high risk merges automatically, and that the risk classifier is a path-based rule set.

In the review of PR #31 the owner refined both decisions:
- **Only PRs into `main` matter** ([comment](https://github.com/cooper3330/weekly-meal-planner/pull/31#discussion_r4385338589)). The merge gate covers only PRs whose base branch is `main`. PRs into other branches are ignored.
- **What is protected** ([comment](https://github.com/cooper3330/weekly-meal-planner/pull/31#discussion_r4385372917)). The human defines how agents work, and only the files that hold those definitions stay human-reviewed:
  - agent process;
  - environment variables and secrets;
  - ADRs;
  - Claude, GitHub and Git configuration;
  - the workflow doc;
  - the constitution.

  Application code, its tests, and its build and dependency files may auto-merge, because the app has very few users. This replaces the "at minimum" list in FR-4 of #4, which also named dependency manifests, lockfiles and auth code.

Principle 7 says the suite is the definition of done; ADR 0010 makes `make check` that definition.

## Decision
**CI**
- One workflow, `.github/workflows/ci.yml`, with one job named `check`. It runs on `pull_request` with `branches: [main]` (PRs into `main` only) and on `push` to `main`, installs uv (`astral-sh/setup-uv`) and runs `make check`. That is exactly the command developers loop on (ADR 0010). It uses no secrets, so it also runs on fork PRs.
- Branch protection on `main` requires the `check` status. It also requires a pull request with 1 approving review and review from Code Owners. These are manual settings, listed in `docs/workflow.md`.

**Risk rules: one list, two consumers**
- `.github/risk-paths` lists one rule per line. A line ending in `/` is a directory prefix; any other line is an exact path. The initial list, and nothing else:
  - **Constitution and workflow doc:** `docs/constitution.md`, `docs/workflow.md`
  - **Architecture decisions:** `docs/adr/`
  - **Agent process and Claude configuration:** `CLAUDE.md`, `.claude/`, `scripts/`
  - **GitHub and Git configuration:**
    - `.github/`, which holds the workflows, CODEOWNERS, `risk-paths` and the templates;
    - `.gitignore`, which keeps `.env` out of commits.
  - **Environment variables and secrets:**
    - `.env.example`;
    - `src/mealplanner/config.py`, the one module that reads environment variables (ADR 0011).
- Everything else is not protected, so it auto-merges when low risk:
  - all other application code under `src/`, including auth;
  - `tests/`, including `tests/scripts/` and `tests/conftest.py`;
  - `pyproject.toml`, `uv.lock` and `Makefile`;
  - `README.md` and `docs/product-spec.md`.
- `scripts/classify-risk.sh` reads changed paths on stdin and prints `high` or `low`. It is pure: no network and no `gh`, so it is tested offline in `tests/scripts/`.
- `.github/CODEOWNERS` assigns every rule in `.github/risk-paths` to `@cooper3330`. A drift test fails if the two lists differ.
- The labeler workflow (`risk-label.yml`) runs on `pull_request_target` with `branches: [main]`. It checks out only the base branch, never the PR's code, so a PR cannot loosen the rules it is judged by. It lists the PR's files through the API and adds `risk:high` when the classifier says `high`. It never removes the label: a `risk:high` added by hand (by the owner, the architect or the reviewer) always sticks, and only a human removes it.

**Reviewer agent (FR-3)**
- `.claude/agents/reviewer.md` runs on Opus 5.5 in a fresh context, separate from the developer. It runs in the workflow, not as a subagent of the coordinator.
- Inputs: the PR diff, the closed task issue, the parent spec's acceptance criteria, the `## Technical plan` comment, `docs/constitution.md`, and the CI result.
- It posts one review with `gh pr review`:
  - a table with one row per AC the task covers: met, not met, or not verifiable, each with evidence (a test name or a file and line)
  - a constitution check
  - `--approve` only when every AC is met, CI is green and no principle is violated; otherwise `--request-changes`
- Its tools are read-only apart from posting that review. It may add `risk:high` with a reason. It never merges.
- The path rules no longer cover tests or the build files, so the reviewer guards them. It adds `risk:high` when a PR weakens the gate without touching a protected path. That covers a PR that:
  - deletes, skips or loosens tests (principle 7 forbids this);
  - removes a step from `make check` in the `Makefile`;
  - relaxes the lint or type-check settings in `pyproject.toml`.
- It runs only on PRs whose base branch is `main` (ADR 0015).

**Auto-merge (FR-5)**
- A deterministic step in the review workflow handles merging, not the model. After an approving review it:
  1. checks that the PR's base branch is `main`, a second guard behind the workflow's branch filter,
  2. re-runs the classifier on the PR's current file list,
  3. checks that the `risk:high` label is absent,
  4. checks that the kill switch is off (ADR 0015),
  5. then runs `gh pr merge --auto --squash --delete-branch`.
- GitHub then merges once the required `check` status and the approval are in place.
- If any of these checks fails, the step requests the owner's review and does not enable auto-merge.
- When `risk:high` is added at any time, a `labeled` handler runs `gh pr merge --disable-auto`. This covers a label added by hand after auto-merge was enabled.
- `risk:high` PRs are merged only by a human. On protected paths, branch protection also demands code-owner approval.

## Alternatives considered
- **A required `risk-gate` status check that fails while `risk:high` is set.** It would also block the human merge, unless an admin bypass is used every time. Disabling auto-merge on the label is simpler, and CODEOWNERS already enforces the protected paths.
- **The classifier as a GitHub ruleset or the `actions/labeler` action.** A ruleset cannot express "auto-merge only if low risk". `actions/labeler` adds a dependency, and its glob config would be a third copy of the list. A 30-line script is testable offline.
- **Classifying in a `pull_request` workflow.** On fork PRs the token is read-only, so the label could not be applied. `pull_request_target` without checking out the PR's code is the safe form.
- **The reviewer as a coordinator subagent.** That would not be a fresh context, and its approval would come from the same identity that authored the PR (see ADR 0014).

## Consequences
- Low-risk PRs need no human: CI is green, the reviewer approves, the PR squash-merges and its branch is deleted.
- Every PR that implements this feature touches protected paths, so all of them are `risk:high` and human-merged. That is intended: agents cannot loosen their own rules.
- The owner is the only code owner, and GitHub forbids self-approval. So a protected-path PR the owner authors, for example from an interactive session, cannot get a code-owner approval.
  - The owner merges such a PR with the admin bypass. Branch protection leaves "Do not allow bypassing the above settings" off, and `docs/workflow.md` says so.
  - Agent PRs are authored by the App (ADR 0014), so they never need the bypass.
- PRs into branches other than `main` get no CI, no reviewer, no label and no auto-merge. The owner merges them by hand or leaves them.
- Application code, tests, the `Makefile` and dependency changes can merge without a human. Two risks follow, and the owner accepted them because the app has few users:
  - **Dependencies.** A dependency bump the reviewer approves is installed on the owner's machine at the next `uv sync`.
  - **A weakened gate.** A PR could weaken `make check` or the tests without touching a protected path. Three things stand in the way:
    - CI runs the PR's own `make check`, so a weakened suite still has to pass what is left of it;
    - the reviewer adds `risk:high` when it sees tests or checks weakened;
    - the owner can add the label at any time.
- Adding a protected path means one line in `.github/risk-paths` plus the matching CODEOWNERS line; the drift test enforces the pair.

## Constitution check
- P6/P7: CI re-runs the same `make check` the developer looped on. The reviewer judges against the issue's numbered ACs, and tests are the evidence.
- P7 (tests are never weakened): `tests/` is no longer a protected path, so the reviewer enforces this and labels any weakening `risk:high`.
- CLAUDE.md's "never commit secrets": `.env.example`, `.gitignore` and the one module that reads environment variables stay human-reviewed.
- P8 Simple over clever: one CI job, one list of rules, one bash classifier, and GitHub's native auto-merge. The workflows add no new runtime dependencies.
- P1 Local first governs the app runtime and its data. The delivery tooling runs on GitHub, where the code already lives, and the app's behaviour does not change.
