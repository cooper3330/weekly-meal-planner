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
Human reviews/merges. The PR's `Closes #N` closes the task, and the built-in project workflows move it to Done; when all sub-issues are Done, the coordinator moves the feature to Done.
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
Moving every planned task to Ready by hand was a chore, not a decision, so the architect does it. When it has no open questions it puts each task in Ready itself; open questions (conflicting requirements, a security concern, a trade-off the spec and constitution do not settle) are the only reason a plan waits in Plan Review. Dependencies are explicit: a task is dispatched only once everything on its `Blocked by:` line is done. Tasks that rely on the architect's ADRs list the ADR PR there, so development starts after you accept the decisions by merging that PR (until the reviewer agent and risk-based auto-merge in #4 take that over).

## Releasing work with the `ready` label
Approving a spec or releasing a task is one action: add the `ready` label to the issue. A GitHub Actions workflow cannot trigger on a card move in a user-owned project (ADR 0015), so the label is the doorbell. Within seconds the coordinator workflow sets the card to Ready (unless it is already moving), removes the label, and runs a pass. The architect applies the label to the tasks it plans, so they are dispatched at once; tasks parked in Plan Review get no label.

- The board stays the source of truth. The label only triggers a pass, so a repeated or stale label is harmless. A card already in Architecture, Implementation, In Review or Done is never moved backwards.
- Moving the card to Ready by hand still works; the hourly pass picks it up. Every pass (hourly included) first releases any open issue still labelled `ready`, so an event skipped by `AGENTS_PAUSED`, the daily cap, a cancelled queued run or a failed read is recovered by the next scheduled pass.
- Closing a task or bug issue filed by the owner, a member or a collaborator (not a stranger: each run counts toward the daily cap), or merging an `issue-*` PR into `main`, also starts a pass, so a task whose `Blocked by:` line just cleared is released without waiting.
- A burst of events queues one pass behind the running one; GitHub cancels the older pending runs, and cancelled and skipped runs do not count toward `AGENT_DAILY_RUNS`. Each pass sweeps every eligible item, so nothing is lost. The concurrency group is on the job, so an unrelated event (another label, a stranger's issue) never joins the queue.
- Re-run `scripts/setup-project.sh` once (it is safe to re-run) to create the `ready` label. `AGENTS_PAUSED`, the preflight and the daily cap apply to these triggers like any other.

## Human gates
- Spec approval: moving a spec to Ready.
- Answering the architect's open questions, when it has any (Plan Review).
- Accepting architecture decisions by merging the ADR PR, and merging PRs generally, until #4 automates low-risk merges.
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
1. Each coordinator pass (`scripts/coordinator.sh`, or the Actions coordinator once #4 lands) runs `scripts/pending-revisions.sh`. It lists work whose newest trusted feedback is newer than the newest agent comment.
2. For each item the coordinator posts a short acknowledgement, creates a throwaway detached git worktree on the PR branch (or on `origin/main` for plan feedback) so your own checkout and local branches are untouched, merges `origin/main` into the PR worktree first (so the agent runs the current protocol; a conflict is reported on the PR instead), and runs the owning agent in revision mode: the architect for `arch-*` branches and plan issues, the developer for `issue-*` branches. The prompt names the PR or issue but not the feedback text; the agent reads the comments itself and ignores untrusted authors.
3. The agent adds new commits (never amends or force-pushes), replies on every thread, and pushes. You re-review, resolve the threads, and merge.

**The marker.** Agents post with the owner's own GitHub account, so every agent comment ends with a hidden marker, `<!-- agent:NAME -->`. The marker, not the author, is how the loop tells agent comments from yours. It also means an agent's reply, even one that quotes `/revise`, is never treated as feedback.

**Limits**
- It runs while the coordinator runs: loop mode polls every `INTERVAL` seconds, `--once` does a single pass.
- A failed run is reported in the coordinator's output. Comment `/revise` again to retry.
- You still resolve threads and merge. The reviewer agent and risk-based auto-merge in #4 will take that over.
