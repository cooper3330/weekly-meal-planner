# Workflow

## Project board
A GitHub Project ("Weekly Meal Planner") with a single-select **Stage** field:

| Stage | Meaning | Who moves it |
|---|---|---|
| Backlog | Idea or draft | Human |
| Spec Review | Spec written, awaiting human approval | Human |
| **Ready** | Spec approved; agents may start | Human |
| Architecture | Architect is producing plan/tasks | Coordinator |
| Plan Review | Plan awaiting human approval (optional gate) | Architect -> Human |
| Implementation | Developer is building a task | Coordinator |
| In Review | PR open | Developer |
| Done | Merged | Coordinator |
| Blocked | Needs human input (see comment) | Any agent |

Issue types are set by labels: `type:feature` (a spec), `type:task` (one implementable unit from a plan), `type:architecture` (a design question or ADR), `type:bug`.

## Lifecycle
```
Feature spec issue --Ready--> Coordinator --> Architect
   Architect writes specs/NNN/plan.md + tasks.md, opens a PR, creates a `type:task`
   issue per task (linked as sub-issues, each with acceptance criteria), moves feature to Plan Review.
Human approves plan PR, moves task issues to Ready.
Task issue --Ready--> Coordinator --> Developer
   Developer branches `issue-N-slug`, implements with tests, opens PR (`Closes #N`), moves to In Review.
Human reviews/merges. Coordinator moves to Done.
```

## Routing rules (coordinator)
- `type:feature` or `type:architecture` in **Ready** -> Architect.
- `type:task` or `type:bug` in **Ready** -> Developer, only if all blocking issues are Done and the parent plan is merged.
- Anything lacking acceptance criteria -> **Blocked** with a comment asking for them.
- One active item per agent role at a time (avoids merge conflicts early on); raise the limit via `MAX_PARALLEL` in `scripts/coordinator.sh`.

## Spec artifacts (`specs/NNN-name/`)
- `spec.md`: user stories, functional requirements, acceptance criteria, out of scope. No tech choices.
- `plan.md`: architecture, data model, interfaces, risks, constitution check, links to ADRs.
- `tasks.md`: ordered, independently shippable tasks, each mapping to acceptance criteria.
Start from `specs/000-template/`.

## Human gates
Spec approval (moving to Ready) and PR merge are always human. Plan approval is a human gate by default.
