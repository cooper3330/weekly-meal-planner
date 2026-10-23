# CLAUDE.md

Weekly meal planner: local, Claude-based app that recommends weekly lunch/dinner options meeting protein/calorie targets, then generates a grocery list and recipes for the user's picks.

## Ground rules
- Spec-driven, with GitHub issues as the source of truth: no code without an approved `type:feature` issue (the spec) that has a `## Technical plan` comment. Specs say *what/why*; plans say *how*.
- Read `docs/constitution.md` before any design or code decision. It wins over convenience.
- Work from the GitHub issue you were assigned. Do not expand scope; file a new issue instead.
- Architecture decisions are recorded as ADRs in `docs/adr/`. There are no spec files in the repo.
- Everything runs locally. No hosted backends; the only network call is to the Claude API.
- Nutrition numbers (calories, protein) are computed and validated in code, never trusted from model prose alone.
- Never commit secrets. API keys come from environment variables; `.env` is gitignored.
- Branches: `issue-<number>-<slug>`. Always create a new branch for each PR; never reuse a branch name. Delete the branch (remote and local) once its PR merges. PRs reference the issue (`Closes #N`). Never push to `main`.

## Workflow
See `docs/workflow.md`. Project stages (the `Stage` field): Backlog -> Spec Review -> Ready -> Architecture -> Plan Review -> Implementation -> In Review -> Done. Epics are milestones.
