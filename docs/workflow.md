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
| Backlog | Idea, draft, or unpromoted task | Human (architect creates tasks here) |
| Spec Review | Spec written, awaiting human approval | Human |
| **Ready** | Approved; agents may start | Human |
| Architecture | Architect is producing the plan and tasks | Coordinator |
| Plan Review | Plan and task sub-issues awaiting human approval | Architect -> Human |
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
   creates a `type:task` sub-issue per task (Status: Backlog), sets the feature to Plan Review.
Human approves the plan (and merges ADR PRs), moves task issues to Ready.
Task issue --Ready--> Coordinator --> Developer
   Developer branches `issue-N-slug`, writes the tests first and implements until they pass, opens PR (`Closes #N`), sets In Review.
Human reviews/merges. The PR's `Closes #N` closes the task, and the built-in project workflows move it to Done; when all sub-issues are Done, the coordinator moves the feature to Done.
```

## Routing rules (coordinator)
- `type:feature` or `type:architecture` in **Ready** -> Architect.
- `type:task` or `type:bug` in **Ready** -> Developer, only if its blockers are Done and (for tasks) the parent feature has a `## Technical plan` comment.
- Anything lacking acceptance criteria -> **Blocked** with a comment asking for them.
- One active item per agent role at a time (avoids merge conflicts early on); raise the limit via `MAX_PARALLEL` in `scripts/coordinator.sh`.

## Writing the artifacts
- **Spec (issue body):** why, user stories, functional requirements, numbered testable acceptance criteria (AC-1...), out of scope, open questions. No tech choices. Use the *Feature spec* form.
- **Plan (comment):** approach, components and interfaces, data model, LLM usage (prompts, JSON schemas, validation), constitution check, risks, links to ADRs, and the task list in order.
- **Task (sub-issue):** goal, the AC numbers it covers, files likely touched, the tests to write first (including edge cases), dependencies. Use the *Task* form.
- Spec changes after approval: edit the issue body and leave a comment saying what changed. The architect re-checks the plan; the coordinator does not re-dispatch work already in progress.

## Human gates
Spec approval (moving to Ready), plan approval (promoting tasks to Ready), and PR merge are always human.

## Models
`.claude/settings.json` pins the executor model to Sonnet 5.5 and enables Opus 5.5 as the [advisor](https://code.claude.com/docs/en/advisor.md) (experimental). Claude consults the advisor at decision points (before committing to an approach, when stuck, before declaring done), so the coordinator, architect, and developer agents all get Opus guidance while keeping Sonnet's cost for routine turns. Each agent in `.claude/agents/` also pins its model in frontmatter so it doesn't depend on how the session was started: the coordinator and developer run on `claude-sonnet-5-5`, and the architect runs on `claude-opus-5-5` (planning is where the strongest model pays off). Opus main with the Opus advisor is an accepted pairing, so the advisor stays attached to all three. Subagents inherit the advisor from settings. The `settings.json` `model` line also makes Sonnet 5.5 the default for interactive sessions in this repo.

- The advisor must rank at or above the main model, so keep models at Opus 5.5 or lower. A session or agent on Fable will not get the Opus advisor.
- Requires the Anthropic API (not Bedrock, Vertex, or Foundry) and feature-flag fetching; `DISABLE_TELEMETRY` keeps the advisor off. `CLAUDE_CODE_DISABLE_ADVISOR_TOOL=1` turns it off entirely.
- Advisor calls bill at Opus rates. There is no setting to force or cap them; ask for one in the task prompt if needed.
- Personal override: `/advisor off` or `claude --advisor <model>` for a session.

