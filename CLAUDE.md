# CLAUDE.md

Weekly meal planner: local, Claude-based app that recommends weekly lunch/dinner options meeting protein/calorie targets, then generates a grocery list and recipes for the user's picks.

## Ground rules
- Spec-driven: no code without an approved `specs/NNN-name/spec.md` and `plan.md`. Specs say *what/why*; plans say *how*.
- Read `docs/constitution.md` before any design or code decision. It wins over convenience.
- Work from the GitHub issue you were assigned. Do not expand scope; file a new issue instead.
- Everything runs locally. No hosted backends; the only network call is to the Claude API.
- Nutrition numbers (calories, protein) are computed and validated in code, never trusted from model prose alone.
- Never commit secrets. API keys come from environment variables; `.env` is gitignored.
- Branches: `issue-<number>-<slug>`. PRs reference the issue (`Closes #N`). Never push to `main`.

## Workflow
See `docs/workflow.md`. Project stages (the `Stage` field): Backlog -> Spec Review -> Ready -> Architecture -> Implementation -> In Review -> Done.
