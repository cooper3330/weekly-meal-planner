# Workflow

GitHub issues are the source of truth for specs, plans, and tasks. The repo holds code, ADRs, and these process docs. There are no spec files.

## Where things live
| Artifact | Location |
|---|---|
| Spec (what/why, acceptance criteria) | Body of a `type:feature` issue |
| Technical plan (how) | A comment headed `## Technical plan` on the feature issue, maintained by the architect (edited in place, not re-posted) |
| Tasks | Sub-issues of the feature issue (`type:task`) |
| Epics / major objectives | Milestones. Assign feature issues to a milestone. Use a parent issue only if an objective needs its own discussion thread |
| Architecture decisions | ADR files in `docs/adr/`, merged by PR. Plans and issues link to them |
| Work state | GitHub Project, built-in `Status` field |

## Project board
A GitHub Project ("Weekly Meal Planner") using the built-in single-select **Status** field, with exactly these nine options in this order (`scripts/setup-project.sh` checks them):

| Status | Meaning | Who moves it |
|---|---|---|
| Backlog | Idea or draft, not yet ready to build | Human |
| Spec Review | Spec written, awaiting human approval | Human |
| **Ready** | Approved; agents may start | Human (specs); the architect, for the tasks it plans |
| Architecture | Architect is producing the plan and tasks | Coordinator |
| Plan Review | The architect has open questions for the owner (only then) | Architect -> Human |
| Implementation | Developer is building a task | Coordinator |
| In Review | PR open | Developer |
| Done | Merged | Built-in project workflow (see below); coordinator for a feature whose sub-issues are all Done |
| Blocked | Needs human input (see comment) | Any agent |

### Board automation
The Status field is the board's built-in field, so GitHub's own project workflows can move cards. In the project, open **Workflows** and enable:

- **Auto-add to project** for this repo (a filter of `is:issue` keeps one card per task; PR cards are not needed because `Closes #N` closes the issue).
- **Item added to project**: set Status to **Backlog**.
- **Item closed**: set Status to **Done**.
- **Pull request merged**: set Status to **Done** (only matters if PR cards are on the board).

Everything else is moved by a human or the agents as in the table above. An item with no Status shows in a "No Status" column; treat it as untriaged. The coordinator only acts on **Ready** and refuses to run if the Status field lacks any of the nine options.

Issue types are set by labels: `type:feature` (a spec), `type:task` (one implementable unit from a plan), `type:architecture` (a design question that produces ADRs), `type:bug`.

## Lifecycle
```
Feature issue (spec) --human: Ready--> Coordinator --> Architect
   Architect posts/updates the `## Technical plan` comment, opens a PR for any ADRs,
   creates a `type:task` sub-issue per task, each with a `Blocked by:` line.
   No open questions: the tasks go to Ready and the feature to Implementation.
   Open questions: one comment with the questions and recommended answers; the tasks and the
   feature go to Plan Review. The owner answers (and moves the tasks to Ready, or comments
   `/revise` so the architect resolves them and promotes the tasks).
Task issue --Ready, all blockers done--> Coordinator --> Developer
   Developer branches `issue-N-slug`, writes the tests first and implements until they pass, opens PR (`Closes #N`), sets In Review.
The reviewer agent reviews the PR; the merge gate (below) decides whether it merges by itself or waits for the owner. The PR's `Closes #N` closes the task, and the built-in project workflows move it to Done; when all sub-issues are Done, the coordinator moves the feature to Done.
```

## Routing rules (coordinator)
- `type:feature` or `type:architecture` in **Ready** -> Architect.
- `type:task` or `type:bug` in **Ready** -> Developer, only if every blocker on its `Blocked by:` line is done (an issue is closed, a PR is merged) and (for tasks) the parent feature has a `## Technical plan` comment. The coordinator script checks the blockers itself and logs `#N waits on #M` for a held-back task.
- Anything lacking acceptance criteria -> **Blocked** with a comment asking for them.
- **Where dispatched agents run.** Each coordinator pass creates a fresh detached git worktree of `origin/main` and runs the coordinator, and the agents it starts, inside it. The owner's checkout, its branch and any uncommitted work are never touched. Whatever a run leaves unfinished (uncommitted changes, commits on no remote) is saved before the worktree is deleted, to a new `wip-<branch>-<time>` branch (never the agent's own branch, which may have a PR), and the issue gets a comment naming it. If the work cannot be saved, the worktree is kept and its path is printed.
- **Tasks that need an interactive session.** Claude Code does not let a headless run write to its protected paths (`.git/`, editor folders, and most of `.claude/`, such as `settings.json`) and nobody can approve the prompt. Agent definitions, commands and skills (`.claude/agents/`, `.claude/commands/`, `.claude/skills/`) are writable headless, as tested on Claude Code 2.1.312, so tasks that only edit them are dispatched; set `WRITABLE_PATH_RE='^$'` for the coordinator if your CLI denies them. A task whose "Files likely touched" lists such a path is not dispatched: the coordinator comments with the paths and the command (`claude --agent developer` in your checkout), and moves it to Blocked. Do it interactively, approving the protected edits, or edit those files by hand. The architect should keep protected-path edits in their own small task.
- **Denied commands are reported.** The coordinator asks for structured output and prints every command or edit an agent was denied, so the allowlist change (or the interactive session) is obvious.
- One active item per agent role at a time (avoids merge conflicts early on); raise the limit via `MAX_PARALLEL` in `scripts/coordinator.sh`.

## Writing the artifacts
- **Spec (issue body):** why, user stories, functional requirements, numbered testable acceptance criteria (AC-1...), out of scope, open questions. No tech choices. Use the *Feature spec* form.
- **Plan (comment):** approach, components and interfaces, data model, LLM usage (prompts, JSON schemas, validation), constitution check, risks, links to ADRs, and the task list in order.
- **Task (sub-issue):** goal, the AC numbers it covers, files likely touched, the tests to write first (including edge cases), and a machine-readable line `Blocked by: #12, #15` (or `Blocked by: none`) naming the tasks and the ADR PR it waits on. Use the *Task* form.
- Spec changes after approval: edit the issue body and leave a comment saying what changed. The architect re-checks the plan; the coordinator does not re-dispatch work already in progress.

## Planned work flows to Ready
Moving every planned task to Ready by hand was a chore, not a decision, so the architect does it. When it has no open questions it puts each task in Ready itself; open questions (conflicting requirements, a security concern, a trade-off the spec and constitution do not settle) are the only reason a plan waits in Plan Review. Dependencies are explicit: a task is dispatched only once everything on its `Blocked by:` line is done. Tasks that rely on the architect's ADRs list the ADR PR there, so development starts after the ADR PR merges. ADR PRs touch `docs/adr/`, a protected path, so they are `risk:high` and you accept the decisions by merging them.

## The agents' GitHub App
Agents act as an owner-created GitHub App installed on this repository only (docs/adr/0014). Give it these repository permissions, and no others:

- Contents: Read & write (agents push their `issue-*` branches; git authenticates with the App token through `gh auth setup-git`)
- Issues: Read & write
- Pull requests: Read & write
- Actions: Write (to dispatch workflows)

Store its ID and private key as the repository secrets `AGENT_APP_ID` and `AGENT_APP_PRIVATE_KEY`. If a push from a workflow fails with `403`, check that the App has Contents write and that the installation has accepted it. Before it launches any agent, the coordinator makes a test push (`git push --dry-run` of a throwaway branch name, which changes nothing on the remote) with the App's token. A clear refusal (403, permission denied) stops the run with a message naming the fix; any other failure of the probe is logged and the run continues. The run log also prints the permissions the token reports, for diagnosis only (that field is not a reliable answer for an App token, so it never decides). When a refusal stops a revision (`--revise`), it also leaves one comment on the PR or issue, ending with `<!-- agent:permission -->`: it says the revision could not start, names the permission to give the App and to accept on the installation, and shows what git said. Fix the App, then comment `/revise` again. The comment is never counted toward the revision budget, and it is posted once, not on every retry.

## Releasing work with the `ready` label
Approving a spec or releasing a task is one action: add the `ready` label to the issue. A GitHub Actions workflow cannot trigger on a card move in a user-owned project (ADR 0015), so the label is the doorbell. Within seconds the coordinator workflow sets the card to Ready (unless it is already moving), removes the label, and runs a pass. The architect applies the label to the tasks it plans, so they are dispatched at once; tasks parked in Plan Review get no label.

- The board stays the source of truth. The label only triggers a pass, so a repeated or stale label is harmless. A card already in Architecture, Implementation, In Review or Done is never moved backwards.
- Moving the card to Ready by hand still works; the hourly pass picks it up. Every pass (hourly included) first releases any open issue still labelled `ready`, so an event skipped by `AGENTS_PAUSED`, the daily cap, a cancelled queued run or a failed read is recovered by the next scheduled pass.
- Closing a task or bug issue filed by the owner, a member or a collaborator (not a stranger: each run counts toward the daily cap), or merging an `issue-*` PR into `main`, also starts a pass, so a task whose `Blocked by:` line just cleared is released without waiting.
- A burst of events queues one pass behind the running one; GitHub cancels the older pending runs, and skipped runs do not count toward `AGENT_DAILY_RUNS`, but cancelled runs do (a run cancelled by timeout or by hand called Claude, and a superseded pending run cannot be told apart from it), so a burst of label events can use several slots. Raise `AGENT_DAILY_RUNS` if the cap is reached too early. Each pass sweeps every eligible item, so nothing is lost. The concurrency group is on the job, so an unrelated event (another label, a stranger's issue) never joins the queue.
- Re-run `scripts/setup-project.sh` once (it is safe to re-run) to create the `ready` label. `AGENTS_PAUSED`, the preflight and the daily cap apply to these triggers like any other.

### The daily cap
- `AGENT_DAILY_RUNS` is a repository variable (Settings > Secrets and variables > Actions > Variables). Each workflow passes it to `scripts/agent-guard.sh preflight` in the `env:` of its Preflight step (`${{ vars.AGENT_DAILY_RUNS }}`); unset or empty means 12. The guard counts today's (UTC) runs of Coordinator, Revise and Review.
- A review skipped by the cap is visible: the Review job posts one comment on the PR, marked `<!-- agent:review-skipped sha=... -->`, giving the count and the cap. It says to raise `AGENT_DAILY_RUNS` or wait for 00:00 UTC, then re-run the Review job. A re-run on the same head SHA posts no second comment. Until then any earlier verdict on the PR (for example CHANGES_REQUESTED) is stale.
- A run the cap skipped does not count toward the cap, whichever workflow it is. Such a run concludes `success` without calling Claude: a Review run has its "Run the reviewer" step skipped, and a Coordinator or Revise run has run its "Daily cap skip" step (which runs only on the cap-skip path). The guard leaves those runs out (one `gh run view` per successful run today; unreadable jobs mean the run is counted). Cancelled runs are still counted.

## The merge gate
A risk-based gate replaces "a human merges every PR" (ADR 0013).

- **Only PRs into `main` are gated.** CI, the labeler, the reviewer and auto-merge all filter on base branch `main`. A PR into any other branch starts none of them.
- **CI's `check` job is required.** It runs `make check`, the same command developers loop on.
- **The reviewer agent approves or requests changes.** It checks the PR against the task's acceptance criteria, the plan, the ADRs and the constitution, once per head commit.
- **Low-risk PRs auto-merge.** After an approval, `scripts/after-review.sh` (code, not the model) enables GitHub's squash auto-merge when the base is `main`, the classifier says `low`, there is no `risk:high` label, and agents are not paused. GitHub merges once the required `check` and the approval are in.
- **`risk:high` PRs need the owner.** The label is added by `.github/workflows/risk-label.yml` and is never removed by an agent. Adding it to a PR also turns auto-merge off.

**Protected paths.** `.github/risk-paths` is the one list (do not copy it into docs); `.github/CODEOWNERS` mirrors it and a test fails if the two drift. They are the files that define the process and its guardrails: the constitution, this workflow, the ADRs, agent and Claude configuration, the scripts, the workflows, ignore files and the files holding secrets and configuration. An agent that could change them could loosen its own gate, so a PR touching any of them is `risk:high` and waits for you. `scripts/classify-risk.sh` applies the list and fails closed if it is missing.

**Weakened tests.** The reviewer labels any PR that weakens tests or `make check` (skipped, deleted or loosened tests, a relaxed check) `risk:high`, because `tests/`, the `Makefile` and the dependency files are not protected paths.

**Owner-authored protected PRs.** GitHub forbids approving your own PR, so you merge your own protected-path PRs with the admin bypass, which is why "Do not allow bypassing" stays off. Agent PRs come from the App and do not need it.

## Agents on GitHub Actions (ADR 0015)
The agents run on Actions by default, using the pinned `claude` CLI and the same scripts as local mode.

| Workflow | Trigger | Does |
|---|---|---|
| `ci.yml` | PRs into `main`, pushes to `main` | the `check` job: `make check` (no secrets) |
| `risk-label.yml` | `pull_request_target` into `main`, and `labeled` | adds `risk:high`; on that label, turns auto-merge off |
| `coordinator.yml` | hourly (minute 17), "Run workflow", the `ready` label, a closed task or merged `issue-*` PR | `scripts/coordinator.sh --once`: dispatches the architect and developer |
| `review.yml` | PRs into `main` from `issue-*` and `arch-*` branches of this repository | the reviewer agent, then `scripts/after-review.sh` (auto-merge or a revision) |
| `revise.yml` | trusted comments and reviews, "Run workflow" | `scripts/coordinator.sh --revise`: one revision run |
| `ci-fix.yml` | failed `check` on an `issue-*` PR | a developer revision (see above) |

- **Kill switch.** Set the repository variable `AGENTS_PAUSED` to `true`: every job that runs `claude` or enables auto-merge is skipped. The labeler's disable-auto-merge step still runs, so a `risk:high` label stops a merge even while paused.
- **Default caps** (repository variables; unset means the default):

| Variable | Default |
|---|---|
| `MAX_PARALLEL` | 1 |
| `AGENT_DAILY_RUNS` | 12 runs that called `claude`, per UTC day |
| `ARCHITECT_MAX_TURNS` | 60 |
| `DEVELOPER_MAX_TURNS` | 80 |
| `REVIEWER_MAX_TURNS` | 30 |
| `REVIEWER_MODEL` | `claude-opus-5-5` |
| `REVISION_BUDGET` | 3 rounds per PR |

  Job timeouts are 55 minutes for the agents, 30 for the reviewer and 10 for CI.
- **Revision budget.** At `REVISION_BUDGET` the PR gets the `needs-human` label (see "Review and revision loop").
- **Local mode is the fallback.** `scripts/coordinator.sh` still runs on your machine (loop mode, or `--once`) when Actions is paused, out of quota or unavailable. Local mode does not run the reviewer or CI-fix workflows, so you review and merge by hand.

## Human gates
- Spec approval: moving a spec to Ready.
- Answering the architect's open questions, when it has any (Plan Review).
- Accepting architecture decisions by merging the ADR PR (it is `risk:high`).
- Merging any other `risk:high` PR. Low-risk PRs merge without you (see "The merge gate").
- Plan approval is no longer a routine gate.

## Models
`.claude/settings.json` pins the executor model to Sonnet 5.5 and enables Opus 5.5 as the [advisor](https://code.claude.com/docs/en/advisor.md) (experimental). Claude consults the advisor at decision points (before committing to an approach, when stuck, before declaring done), so the coordinator, architect, and developer agents all get Opus guidance while keeping Sonnet's cost for routine turns. Each agent in `.claude/agents/` also pins its model in frontmatter so it doesn't depend on how the session was started: the coordinator and developer run on `claude-sonnet-5-5`, and the architect runs on `claude-opus-5-5` (planning is where the strongest model pays off). Opus main with the Opus advisor is an accepted pairing, so the advisor stays attached to all three. Subagents inherit the advisor from settings. The `settings.json` `model` line also makes Sonnet 5.5 the default for interactive sessions in this repo.

- The advisor must rank at or above the main model, so keep models at Opus 5.5 or lower. A session or agent on Fable will not get the Opus advisor.
- Requires the Anthropic API (not Bedrock, Vertex, or Foundry) and feature-flag fetching; `DISABLE_TELEMETRY` keeps the advisor off. `CLAUDE_CODE_DISABLE_ADVISOR_TOOL=1` turns it off entirely.
- Advisor calls bill at Opus rates. There is no setting to force or cap them; ask for one in the task prompt if needed.
- Personal override: `/advisor off` or `claude --advisor <model>` for a session.


## Review and revision loop
You review agent PRs and plan issues in GitHub as usual. The coordinator picks up your feedback and sends it to the agent that owns the work.

**How to ask for changes**
- On a PR (an `arch-*` ADR PR or an `issue-*` code PR): leave inline comments and submit the review as **Request changes**, or add any comment containing the word `/revise`.
- On a feature or architecture issue (feedback on the plan): comment with `/revise`.
- On a task issue whose PR is open: a `/revise` comment on the issue counts as feedback on its PR (the PR on the `issue-<N>-*` branch), so either place works. The agent is told the linked issue and reads both.
- Only comments from the repo's OWNER, MEMBER, or COLLABORATOR count. Ordinary comments without `/revise` are ignored, so you can discuss freely.

**What happens**
1. Each coordinator pass (`scripts/coordinator.sh` locally, or the Actions workflows `coordinator.yml` and `revise.yml`) runs `scripts/pending-revisions.sh`. It lists work whose newest trusted feedback is newer than the newest agent comment.
2. For each item the coordinator posts a short acknowledgement, creates a throwaway detached git worktree on the PR branch (or on `origin/main` for plan feedback) so your own checkout and local branches are untouched, merges `origin/main` into the PR worktree first (so the agent runs the current protocol; a conflict is reported on the PR instead), and runs the owning agent in revision mode: the architect for `arch-*` branches and plan issues, the developer for `issue-*` branches. The prompt names the PR or issue but not the feedback text; the agent reads the comments itself and ignores untrusted authors.
3. The agent adds new commits (never amends or force-pushes), replies on every thread, and pushes. You re-review, resolve the threads, and merge.

**The marker.** On Actions, agents now post as the GitHub App (the reviewer as `github-actions[bot]`); in local mode without `AGENT_GH_TOKEN` they still post as your own account. Either way every agent comment ends with a hidden marker, `<!-- agent:NAME -->`, and the marker stays: it, not the author, is how the loop tells agent comments from yours. It also means an agent's reply, even one that quotes `/revise`, is never treated as feedback.

**When CI fails on an agent PR (`.github/workflows/ci-fix.yml`)**
When the `check` job of `CI` fails on an `issue-*` PR from this repository, the developer is started automatically, in revision mode, instead of the PR waiting for you to comment `/revise`. The workflow only triggers; `scripts/coordinator.sh --revise pr N --source ci --run R` decides. It starts the developer only if the failed run is for the PR's current head, the `check` job really failed in a step (not cancelled, not a runner failure), and CI on `main` is not red (otherwise it posts one comment that the cause is probably not the PR). The reviewer agent is not involved. Each round is acknowledged with `<!-- agent:ack source=ci -->`.
- **Shared budget.** CI rounds and reviewer-requested rounds count together against `REVISION_BUDGET` (default 3). At the budget the PR gets the `needs-human` label, one comment, and your review request. Your own `/revise` rounds never count.
- **ADR PRs** (`arch-*`) are not fixed automatically: a red `check` gets the `needs-human` label and one comment with the failing job's link.
- The developer's push restarts CI; a second failure is another round until the budget is spent. A green run triggers nothing. If the failure is not the PR's (infrastructure, or a test that also fails on `main`), the developer says so on the PR and stops.
- Local mode does not react to CI.

**Limits**
- It runs while the coordinator runs: loop mode polls every `INTERVAL` seconds, `--once` does a single pass.
- A failed run is reported in the coordinator's output. Comment `/revise` again to retry.
- You resolve threads. You merge only `risk:high` PRs; the reviewer and the merge gate handle the rest.
- **Revision budget and `needs-human`.** Reviewer-requested and CI-fix rounds count together against `REVISION_BUDGET` (default 3). When it is spent, the PR gets the `needs-human` label and one comment asking for you. Your own `/revise` rounds never count.

## Manual repository settings
Agents cannot make these. Do each once as the repository owner. `tests/scripts/test_docs.sh` fails if a workflow uses a secret or variable that is not named here.

- [ ] **Branch protection on `main`** (Settings, Branches):
  - Require a pull request before merging, with 1 approval.
  - Require review from Code Owners.
  - Require status checks to pass, and select the `check` status.
  - Leave "Do not allow bypassing the above settings" off, so you can merge your own protected-path PRs (ADR 0013).
- [ ] **Repository settings** (Settings, General, and Actions):
  - Allow auto-merge.
  - Automatically delete head branches.
  - Allow GitHub Actions to create and approve pull requests (Settings, Actions, General, Workflow permissions); the reviewer needs it to approve.
- [ ] **The GitHub App** (ADR 0014): create it with the permissions in "The agents' GitHub App", install it on this repository only, and add the secrets `AGENT_APP_ID` and `AGENT_APP_PRIVATE_KEY`.
- [ ] **Board token.** Create a classic personal access token with only the `project` scope and add it as the secret `PROJECT_TOKEN`.
- [ ] **Claude token.**
  - Run `claude setup-token` on your Pro login and add the printed value as the secret `CLAUDE_CODE_OAUTH_TOKEN`.
  - The token lasts one year. Record the creation date and the expiry date here when you create it: created `YYYY-MM-DD`, expires `YYYY-MM-DD` (one year later). Set a calendar reminder a few weeks before.
  - To renew: run `claude setup-token` again, replace the secret `CLAUDE_CODE_OAUTH_TOKEN`, update the dates, and reset the reminder.
  - Do not add `ANTHROPIC_API_KEY` for the delivery loop; the preflight fails if it is set. Switching to an API key is a documented fallback: replace the secret and change the preflight in `scripts/agent-guard.sh`.
- [ ] **Repository variables** (Settings, Secrets and variables, Actions, Variables). Unset means the default:

  | Variable | Default | Purpose |
  |---|---|---|
  | `PROJECT_NUMBER` | none (required) | the project's number |
  | `MAX_PARALLEL` | 1 | agents running at once |
  | `AGENTS_PAUSED` | unset (not paused) | `true` stops all agent runs |
  | `AGENT_DAILY_RUNS` | 12 | daily cap on runs that call `claude` |
  | `ARCHITECT_MAX_TURNS` | 60 | architect turn cap |
  | `DEVELOPER_MAX_TURNS` | 80 | developer turn cap |
  | `REVIEWER_MAX_TURNS` | 30 | reviewer turn cap |
  | `REVIEWER_MODEL` | `claude-opus-5-5` | reviewer model |
  | `REVISION_BUDGET` | 3 | automatic revision rounds per PR |
- [ ] **Labels and project.** Run `scripts/setup-project.sh` (safe to re-run). It creates the labels `risk:high`, `needs-human` and `ready`, and checks the board's Status options.
- [ ] **The 60-day schedule limit.** On a public repository GitHub disables a scheduled workflow after 60 days without repository activity. If the hourly coordinator stops, re-enable it from the Actions tab.
