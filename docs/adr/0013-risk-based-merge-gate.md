# ADR 0013: Risk-based merge gate: CI runs `make check`, path rules classify risk, a reviewer agent approves, low-risk PRs auto-merge
- Status: Accepted
- Date: 2026-10-26

## Context
Issue #4 (FR-1 to FR-5, FR-7; AC-1 to AC-5, AC-7). Today a human merges every PR and nothing independent verifies agent output. The owner decided that any PR that is not high risk merges automatically, and that the risk classifier is a path-based rule set. Principle 7 says the suite is the definition of done; ADR 0010 makes `make check` that definition.

## Decision
**CI**
- One workflow, `.github/workflows/ci.yml`, with one job named `check`. It runs on `pull_request` and on `push` to `main`, installs uv (`astral-sh/setup-uv`) and runs `make check`. That is exactly the command developers loop on (ADR 0010). It uses no secrets, so it also runs on fork PRs.
- Branch protection on `main` requires the `check` status. It also requires a pull request with 1 approving review and review from Code Owners. These are manual settings, listed in `docs/workflow.md`.

**Risk rules: one list, two consumers**
- `.github/risk-paths` lists one rule per line. A line ending in `/` is a directory prefix; any other line is an exact path. Initial list:
  - process and rules: `docs/constitution.md`, `docs/workflow.md`, `CLAUDE.md`, `docs/adr/`, `.claude/`, `.github/`, `scripts/`
  - test infrastructure: `tests/scripts/`, `tests/conftest.py`
  - build and dependencies: `pyproject.toml`, `uv.lock`, `Makefile`
  - secrets and auth: `.env.example`, `src/mealplanner/config.py`, `src/mealplanner/web/auth.py`
- `scripts/classify-risk.sh` reads changed paths on stdin and prints `high` or `low`. It is pure: no network and no `gh`, so it is tested offline in `tests/scripts/`.
- `.github/CODEOWNERS` assigns every rule in `.github/risk-paths` to `@cooper3330`. A drift test fails if the two lists differ.
- The labeler workflow (`risk-label.yml`) runs on `pull_request_target`. It checks out only the base branch, never the PR's code, so a PR cannot loosen the rules it is judged by. It lists the PR's files through the API and adds `risk:high` when the classifier says `high`. It never removes the label: a `risk:high` added by hand (by the owner, the architect or the reviewer) always sticks, and only a human removes it.

**Reviewer agent (FR-3)**
- `.claude/agents/reviewer.md` runs on Opus 5.5 in a fresh context, separate from the developer. It runs in the workflow, not as a subagent of the coordinator.
- Inputs: the PR diff, the closed task issue, the parent spec's acceptance criteria, the `## Technical plan` comment, `docs/constitution.md`, and the CI result.
- It posts one review with `gh pr review`:
  - a table with one row per AC the task covers: met, not met, or not verifiable, each with evidence (a test name or a file and line)
  - a constitution check
  - `--approve` only when every AC is met, CI is green and no principle is violated; otherwise `--request-changes`
- Its tools are read-only apart from posting that review. It may add `risk:high` with a reason. It never merges.

**Auto-merge (FR-5)**
- A deterministic step in the review workflow handles merging, not the model. After an approving review it:
  1. re-runs the classifier on the PR's current file list,
  2. checks that the `risk:high` label is absent,
  3. checks that the kill switch is off (ADR 0015),
  4. then runs `gh pr merge --auto --squash --delete-branch`.
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
- The owner is the only code owner. A protected-path PR the owner authors (for example from an interactive session) cannot get a code-owner approval, because GitHub forbids self-approval, so it needs the admin bypass. See the open question on #4.
- Adding a protected path means one line in `.github/risk-paths` plus the matching CODEOWNERS line; the drift test enforces the pair.

## Constitution check
- P6/P7: CI re-runs the same `make check` the developer looped on. The reviewer judges against the issue's numbered ACs, and tests are the evidence.
- P8 Simple over clever: one CI job, one list of rules, one bash classifier, and GitHub's native auto-merge. The workflows add no new runtime dependencies.
- P1 Local first governs the app runtime and its data. The delivery tooling runs on GitHub, where the code already lives, and the app's behaviour does not change.
