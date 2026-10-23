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
| Work state | GitHub Project, `Stage` field |

## Project board
A GitHub Project ("Weekly Meal Planner") with a single-select **Stage** field:

| Stage | Meaning | Who moves it |
|---|---|---|
| Backlog | Idea, draft, or unpromoted task | Human (architect creates tasks here) |
| Spec Review | Spec written, awaiting human approval | Human |
| **Ready** | Approved; agents may start | Human |
| Architecture | Architect is producing the plan and tasks | Coordinator |
| Plan Review | Plan and task sub-issues awaiting human approval | Architect -> Human |
| Implementation | Developer is building a task | Coordinator |
| In Review | PR open | Developer |
| Done | Merged | Coordinator |
| Blocked | Needs human input (see comment) | Any agent |

Issue types are set by labels: `type:feature` (a spec), `type:task` (one implementable unit from a plan), `type:architecture` (a design question that produces ADRs), `type:bug`.

## Lifecycle
```
Feature issue (spec) --human: Ready--> Coordinator --> Architect
   Architect posts/updates the `## Technical plan` comment, opens a PR for any ADRs,
   creates a `type:task` sub-issue per task (Stage: Backlog), sets the feature to Plan Review.
Human approves the plan (and merges ADR PRs), moves task issues to Ready.
Task issue --Ready--> Coordinator --> Developer
   Developer branches `issue-N-slug`, implements with tests, opens PR (`Closes #N`), sets In Review.
Human reviews/merges. Coordinator moves the task to Done; when all sub-issues are Done, the feature is Done.
```

## Routing rules (coordinator)
- `type:feature` or `type:architecture` in **Ready** -> Architect.
- `type:task` or `type:bug` in **Ready** -> Developer, only if its blockers are Done and (for tasks) the parent feature has a `## Technical plan` comment.
- Anything lacking acceptance criteria -> **Blocked** with a comment asking for them.
- One active item per agent role at a time (avoids merge conflicts early on); raise the limit via `MAX_PARALLEL` in `scripts/coordinator.sh`.

## Writing the artifacts
- **Spec (issue body):** why, user stories, functional requirements, numbered testable acceptance criteria (AC-1...), out of scope, open questions. No tech choices. Use the *Feature spec* form.
- **Plan (comment):** approach, components and interfaces, data model, LLM usage (prompts, JSON schemas, validation), constitution check, risks, links to ADRs, and the task list in order.
- **Task (sub-issue):** goal, the AC numbers it covers, files likely touched, test expectations, dependencies. Use the *Task* form.
- Spec changes after approval: edit the issue body and leave a comment saying what changed. The architect re-checks the plan; the coordinator does not re-dispatch work already in progress.

## Human gates
Spec approval (moving to Ready), plan approval (promoting tasks to Ready), and PR merge are always human.
