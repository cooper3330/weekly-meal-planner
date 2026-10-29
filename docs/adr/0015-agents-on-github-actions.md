# ADR 0015: Agents run on GitHub Actions with the `claude` CLI and the existing scripts, behind a kill switch, a preflight, and usage caps
- Status: Accepted
- Date: 2026-10-26
- Amended: 2026-10-29 (task #55, event-driven triggers)

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
| `coordinator.yml` | `schedule` (hourly, at minute 17), `workflow_dispatch`, and the events in "Event-driven triggers" below | `scripts/coordinator.sh --once`, or `--ready-label <issue>` for a `ready` label event |
| `revise.yml` | `issue_comment`, `pull_request_review`, `pull_request_review_comment`; `workflow_dispatch` (inputs `kind`, `number`, `source`) | `scripts/coordinator.sh --revise <pr\|issue> <number>`: one revision run for one item |
| `review.yml` | `pull_request` (`opened`, `synchronize`, `ready_for_review`, `reopened`) with `branches: [main]`, from `issue-*` and `arch-*` branches of this repository | the reviewer agent, then the deterministic auto-merge or revision-dispatch step (ADR 0013) |
| `risk-label.yml` | `pull_request_target` with `branches: [main]`, plus `labeled` | ADR 0013 |
| `ci.yml` | `pull_request` with `branches: [main]`, `push` to `main` | ADR 0013 |

- **Only PRs into `main`.** The owner decided that the merge gate covers only PRs whose base branch is `main` ([comment](https://github.com/cooper3330/weekly-meal-planner/pull/31#discussion_r4385338589)). The `branches` filter of a `pull_request` trigger matches the base branch, so PRs into any other branch start none of these workflows. The revision loop (`revise.yml`) is driven by a human `/revise`, so it is not filtered.

- Project item events are not available for a user-owned project, so a card move cannot start the coordinator. The coordinator is nevertheless event-driven through a `ready` label and a few issue and PR events; see "Event-driven triggers" below (amendment, task #55). The hourly run remains the fallback for anything missed.

**Safety on a public repository.** Each check is a job-level `if:`, so it runs before any step can see a secret:
- **Kill switch:** the repository variable `AGENTS_PAUSED`. Every workflow that runs `claude` or enables auto-merge has `if: vars.AGENTS_PAUSED != 'true'`; when it is `true` the job is skipped and nothing acts.
- **`revise.yml`:**
  - the comment or review author's `author_association` is OWNER, MEMBER or COLLABORATOR;
  - the body does not contain `<!-- agent:`;
  - the body contains `/revise` as a word, or the review state is `changes_requested`.

  The script re-checks this with the `pending-revisions.sh` logic (the source of truth), so a stale or duplicate event is a no-op.
- **`review.yml`:** runs only on PRs into `main`, and only when `github.event.pull_request.head.repo.full_name == github.repository`, so fork PRs never get secrets. No workflow checks out PR code under `pull_request_target`.
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
| Job timeout | 55 min agents, 30 min reviewer, 10 min CI | the App token lives 1 hour; reviewer amended in #61, see the amendment note |
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
- **A `ready` label as the coordinator's trigger.** Rejected at first, because it adds a second source of truth next to Status. Superseded by the amendment below: the label is only a doorbell and the board stays the source of truth.
- **Polling for revisions only.** The owner asked for event-driven revisions.

## Consequences
- The owner's machine is no longer needed. Work is picked up at once when the `ready` label is applied, and otherwise within an hour or on manual dispatch.
- The usage on Pro is bounded by the caps above, but not measured. The owner tunes the variables after the first weeks.
- Every agent workflow shares one preflight, one wrapper and one kill switch. A structural test over `.github/workflows/*.yml` keeps new workflows from skipping them.
- New commands the agents run (`gh pr review`, `gh pr diff`, `gh pr merge`, `scripts/board.sh`, `scripts/agent-guard.sh`) are added to the coordinator allowlist, and `test_allowlist_docs.sh` keeps the two in step.

## Amendment (#61): the reviewer's time budget
The reviewer job timeout is 30 minutes, not 20, and the bounded wait for the `check` job on the head SHA is 12 minutes (720 seconds), not 15. The wait step has its own `timeout-minutes: 12` and the review step 18, so neither phase can use up the other's time. The first two real runs showed the reviewer needs about 3.5 to 8 minutes and CI's own timeout is 10, so a longer wait only covers a stuck runner queue. When the wait runs out, the reviewer still runs and sees `check` as pending; `reviewer.md` treats a missing or red `check` as a reason to request changes, so it never approves on an unfinished check. `REVIEWER_MAX_TURNS`, the daily cap and the reviewer's behaviour are unchanged. Wherever this ADR or #36 says 20 minutes or 15 minutes for the reviewer, read 30 and 12.

## Constitution check
- P7: every script added here is tested offline with stubbed `gh` and `claude`. Workflow files get structural tests.
- P8 Simple over clever: one runner and the existing scripts. Caps and the kill switch are repository variables, not code.
- P1 and CLAUDE.md's "no hosted backends" govern the app. GitHub Actions runs the delivery process, not the app, and holds no user data.

## Amendment (task #55): Event-driven triggers
The owner decided that approving a spec or releasing a task should start the coordinator at once, not on the next hourly run. This reverses the "Project item events are not available…" bullet under **Workflows** and the "`ready` label" alternative above.

**The doorbell.** Applying the `ready` label to an issue starts `coordinator.yml` (`issues: labeled`, only for the label `ready`). The label is a trigger, not state: `scripts/coordinator.sh --ready-label <issue>` sets the issue's card to Ready if it is not yet moving (no card, Backlog, Spec Review, Plan Review or Blocked), never moves a card that is already in Architecture, Implementation, In Review or Done, removes the label, and runs a normal pass. The architect applies the label to the tasks it plans (`.claude/agents/architect.md`); tasks parked in Plan Review get none. A closed issue or a pull request only loses the label.

**Other events.** A pass also starts when:
- a task or bug issue is closed, only if its author is OWNER, MEMBER or COLLABORATOR (a stranger can open and close an issue from the template, and each run counts toward the cap), so a `Blocked by:` line that just cleared is released;
- an `issue-*` PR from this repository is merged into `main`.

**Safety and loop control**
- The same kill switch, preflight, daily cap and pinned actions apply as for the schedule. The job `if:` runs before any step sees a secret, and no event uses `pull_request_target`.
- The pass never applies `ready`, and `unlabeled` is not subscribed, so the coordinator cannot start itself.
- Every pass, scheduled or not, first releases any open issue still labelled `ready` (`sweep_ready_labels`), so an event skipped by the kill switch or the cap, or lost in a burst, is recovered by the next scheduled pass.
- `concurrency: coordinator` (`cancel-in-progress: false`) is set on the job, not the workflow, so an event whose job condition is false never joins the queue or cancels a pending run. A burst of events keeps one pending pass; each pass sweeps every eligible item.
- The daily cap does not count `skipped` runs. It does count `cancelled` runs, because a run cancelled by its timeout or by hand called `claude` and a run superseded while pending cannot be told apart from it. A burst of label events can therefore use several slots; the owner raises `AGENT_DAILY_RUNS` if that bites.
