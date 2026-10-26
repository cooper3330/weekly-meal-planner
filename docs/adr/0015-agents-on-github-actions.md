# ADR 0015: Agents run on GitHub Actions with the `claude` CLI and the existing scripts, behind a kill switch, a preflight, and usage caps
- Status: Accepted
- Date: 2026-10-26

## Context
Issue #4 (FR-3, FR-6, FR-8, FR-10, FR-11; AC-3, AC-6, AC-8, AC-10 to AC-12) and the owner's scope note on #4. The note asks for an event-driven revision loop with loop safety and a revision budget per PR.

Constraints:
- The coordinator must run without the owner's machine.
- The repository is public.
- Agent runs share the owner's **Claude Pro** usage limits with interactive use.
- Project item events are not delivered to Actions for a user-owned project.
- `scripts/coordinator.sh` and `scripts/pending-revisions.sh` already implement routing, blocker gating and revision detection, and have offline tests.

## Decision
**Runner**
- Workflows install the `claude` CLI at a pinned version (`npm install -g @anthropic-ai/claude-code@<version>`) and run the same scripts and agent definitions used locally.
- `anthropics/claude-code-action` is not used. Its value is tag mode and comment UX; using it would mean a second set of prompts, a `claude/` branch prefix that the `arch-*`/`issue-*` detection would not recognise, and an allowlist outside the drift guard.
- Third-party actions are pinned to commit SHAs.

**Workflows**

| Workflow | Trigger | Does |
|---|---|---|
| `coordinator.yml` | `schedule` (hourly, at minute 17) and `workflow_dispatch` | `scripts/coordinator.sh --once` |
| `revise.yml` | `issue_comment`, `pull_request_review`, `pull_request_review_comment`; `workflow_dispatch` (inputs `kind`, `number`, `source`) | `scripts/coordinator.sh --revise <pr\|issue> <number>`: one revision run for one item |
| `review.yml` | `pull_request` (`opened`, `synchronize`, `ready_for_review`, `reopened`) on `issue-*` and `arch-*` branches of this repository | the reviewer agent, then the deterministic auto-merge or revision-dispatch step (ADR 0013) |
| `risk-label.yml` | `pull_request_target`, plus `labeled` | ADR 0013 |
| `ci.yml` | `pull_request`, `push` to `main` | ADR 0013 |

- Project item events are not available, so the coordinator is not event-driven. Moving a card to Ready is picked up on the next hourly run, or at once with "Run workflow".

**Safety on a public repository.** Each check is a job-level `if:`, so it runs before any step can see a secret:
- **Kill switch:** the repository variable `AGENTS_PAUSED`. Every workflow that runs `claude` or enables auto-merge has `if: vars.AGENTS_PAUSED != 'true'`; when it is `true` the job is skipped and nothing acts.
- **`revise.yml`:**
  - the comment or review author's `author_association` is OWNER, MEMBER or COLLABORATOR;
  - the body does not contain `<!-- agent:`;
  - the body contains `/revise` as a word, or the review state is `changes_requested`.

  The script re-checks this with the `pending-revisions.sh` logic (the source of truth), so a stale or duplicate event is a no-op.
- **`review.yml`:** runs only when `github.event.pull_request.head.repo.full_name == github.repository`, so fork PRs never get secrets. No workflow checks out PR code under `pull_request_target`.
- **Exactly one run per trigger:**
  - Each workflow has a `concurrency` group per issue or PR, with `cancel-in-progress: false`.
  - One review submission fires both `pull_request_review` and `pull_request_review_comment`. The second run then finds the acknowledgement the first one posted, and exits.
  - Agent replies carry the marker and are posted by the App, so they never trigger anything.

**Preflight and caps (FR-10, FR-11)**
- `scripts/agent-guard.sh preflight` runs before any `claude` call. It fails, with a message naming `CLAUDE_CODE_OAUTH_TOKEN` and the `claude setup-token` fix, when:
  - the token is missing or empty, or
  - `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` is set.

  It never echoes the token.
- `preflight` also enforces a **daily cap**. It counts today's runs of the agent workflows with `gh run list` and skips with a notice at `AGENT_DAILY_RUNS`.
- `scripts/agent-guard.sh run --kind <pr|issue> --number N -- claude ...` wraps every `claude` call:
  - When the output reports a usage limit, it posts one comment on that item, saying so with the reset time if one is given and signed `<!-- agent:limit -->`, then exits non-zero.
  - Nothing retries automatically. The next scheduled pass, or a human, picks the item up again.
- **Default caps.** Each is a repository variable, so the owner can tune it without a PR:

| Variable | Default | Note |
|---|---|---|
| `MAX_PARALLEL` | 1 | as today |
| `AGENT_DAILY_RUNS` | 12 | agent workflow runs that called `claude`, per UTC day |
| `ARCHITECT_MAX_TURNS` | 60 | `--max-turns` |
| `DEVELOPER_MAX_TURNS` | 80 | `--max-turns` |
| `REVIEWER_MAX_TURNS` | 30 | `--max-turns` |
| Job timeout | 55 min agents, 20 min reviewer, 10 min CI | the App token lives 1 hour |
| `REVIEWER_MODEL` | `claude-opus-5-5` | FR-3; set to `claude-sonnet-5-5` if Pro limits bite |
| `REVISION_BUDGET` | 3 | automatic revision rounds per PR |

- The reviewer runs once per head SHA. Draft PRs are skipped, and a newer push cancels an in-progress review of the same PR.
- `DISABLE_TELEMETRY` is not set, so the Opus advisor keeps working.

**Revision budget (scope note).** This is in scope; the developer's internal retry loop and a circuit breaker stay out of scope (#4 "Out of scope").
- When the reviewer requests changes on an `issue-*` PR, the review workflow dispatches `revise.yml` (`source=reviewer`). `workflow_dispatch` is the one event a `GITHUB_TOKEN` may trigger.
- Automatic rounds are counted from the revision acknowledgements on the PR whose source is the reviewer. Once `REVISION_BUDGET` rounds are used, the workflow does not dispatch. Instead it:
  - labels the PR `needs-human`,
  - comments that the budget is spent,
  - requests the owner's review.
- A human `/revise` always runs and does not count against the budget.

**Local mode** stays: `scripts/coordinator.sh` with no flags still loops on the owner's machine. The docs present Actions as the main path.

## Alternatives considered
- **`claude-code-action` everywhere.** See Runner above. It remains an option for the reviewer later, if its inline-comment UX is wanted.
- **Rewriting the coordinator as a deterministic workflow without a model.** This would save usage, but it means rewriting tested routing. The coordinator only calls `claude` when an item is Ready, so idle hourly runs cost nothing.
- **A `ready` label as the coordinator's trigger.** It would add a second source of truth next to Status. The schedule plus manual dispatch is enough.
- **Polling for revisions only.** The owner asked for event-driven revisions.

## Consequences
- The owner's machine is no longer needed. Work is picked up within an hour, or at once on manual dispatch.
- The usage on Pro is bounded by the caps above, but not measured. The owner tunes the variables after the first weeks.
- Every agent workflow shares one preflight, one wrapper and one kill switch. A structural test over `.github/workflows/*.yml` keeps new workflows from skipping them.
- New commands the agents run (`gh pr review`, `gh pr diff`, `gh pr merge`, `scripts/board.sh`, `scripts/agent-guard.sh`) are added to the coordinator allowlist, and `test_allowlist_docs.sh` keeps the two in step.

## Constitution check
- P7: every script added here is tested offline with stubbed `gh` and `claude`. Workflow files get structural tests.
- P8 Simple over clever: one runner and the existing scripts. Caps and the kill switch are repository variables, not code.
- P1 and CLAUDE.md's "no hosted backends" govern the app. GitHub Actions runs the delivery process, not the app, and holds no user data.
