# CLAUDE.md

Weekly meal planner: local, Claude-based app for one user that recommends weekly lunch/dinner options matching their preferences and dietary restrictions, showing each option's calories and macros, then generates a grocery list and recipes for the user's picks. It does not plan the user's whole day or enforce calorie/macro targets.

## Ground rules
- Spec-driven, with GitHub issues as the source of truth: no code without an approved `type:feature` issue (the spec) that has a `## Technical plan` comment. Specs say *what/why*; plans say *how*.
- Read `docs/constitution.md` before any design or code decision. It wins over convenience.
- Work from the GitHub issue you were assigned. Do not expand scope; file a new issue instead.
- Architecture decisions are recorded as ADRs in `docs/adr/`. There are no spec files in the repo.
- Everything runs locally. No hosted backends; the only network call is to the Claude API.
- Nutrition numbers (calories, protein, carbohydrates, fat) are computed and validated in code, never trusted from model prose alone.
- Build for one user first; keep user-owned data separable so adding users later is an extension, not a rewrite. Do not build accounts or tenancy now.
- Never commit secrets. API keys come from environment variables; `.env` is gitignored.
- Branches: `issue-<number>-<slug>`. Always create a new branch for each PR; never reuse a branch name. Delete the branch (remote and local) once its PR merges. PRs reference the issue (`Closes #N`). Never push to `main`.

## Workflow
See `docs/workflow.md`. Project stages (the `Stage` field): Backlog -> Spec Review -> Ready -> Architecture -> Plan Review -> Implementation -> In Review -> Done. Epics are milestones.
