# ADR 0014: Agent identities and credentials: a GitHub App for agents, `GITHUB_TOKEN` for the reviewer, a project-only PAT for the board, the subscription token for Claude
- Status: Accepted
- Date: 2026-10-26

## Context
Issue #4 (FR-5, FR-7, FR-10; open question 2) and task #29. Agents currently post as the owner and are told apart only by the `<!-- agent:NAME -->` marker. That breaks the merge gate in ADR 0013:
- GitHub forbids approving your own PR. The reviewer cannot approve a PR from its own identity, and the owner, as the only code owner, cannot approve agent PRs authored as the owner.
- Pushes and PRs made with the default `GITHUB_TOKEN` do not start other workflows, so CI would never run on them.
- Neither a `GITHUB_TOKEN` nor a GitHub App installation token can write to a **user-owned** Projects v2 board.

The model credential is separate from all of this. The owner decided that agent runs use the Claude subscription token (`CLAUDE_CODE_OAUTH_TOKEN`), not an API key.

## Decision
Four credentials, each used only for its own job:

| Credential | Secret(s) | Identity | Used for |
|---|---|---|---|
| Owner-created GitHub App (for example `meal-planner-agents`), installed on this repo only. Permissions: contents, issues and pull requests (read/write); actions (write), to dispatch workflows | `AGENT_APP_ID`, `AGENT_APP_PRIVATE_KEY` | `<app>[bot]` | Coordinator, architect and developer: branches, pushes, PRs, issues, comments, enabling auto-merge. Tokens are minted per job with `actions/create-github-app-token` and expire after 1 hour. |
| Workflow `GITHUB_TOKEN` | none (built in) | `github-actions[bot]` | The reviewer's review and the risk labeler. The reviewer is a different identity from the PR author, so its approval is valid. Repo setting: "Allow GitHub Actions to create and approve pull requests". |
| Owner's **classic PAT with only the `project` scope** | `PROJECT_TOKEN` | owner | Board reads and writes only, through `scripts/board.sh`, which sets `GH_TOKEN=$PROJECT_TOKEN` for `gh project` calls alone. It has no repo scope, so it cannot push, comment or approve. |
| Claude subscription OAuth token | `CLAUDE_CODE_OAUTH_TOKEN` | n/a (model requests only) | Every `claude` run. No workflow sets `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN`, and none passes `--bare` (ADR 0015). |

- **Interface for board moves.** Agents and the coordinator call `scripts/board.sh` rather than `gh project item-edit`:
  - `scripts/board.sh add <issue-url>`
  - `scripts/board.sh status <issue-number> <Status name>`

  The script looks up the project, field and option IDs itself, so prompts no longer carry Board IDs. Locally, without `PROJECT_TOKEN`, it uses the normal `gh` login.
- **Git identity** for agent commits: `<app-slug>[bot] <<app-user-id>+<app-slug>[bot]@users.noreply.github.com>`.
- **The trust model for the revision loop is unchanged.** App and `github-actions[bot]` comments have author association `NONE`, so they are never trusted feedback. The marker stays, as a fallback for anything still posted as the owner, for example local runs.
- **The owner's identity is never given to an agent** except through the project-only PAT. The owner's approval is what counts as code-owner review.
- **Local runs** (`scripts/coordinator.sh` on the owner's machine) keep working with the owner's `gh` login, as today. They are a fallback, not the main path.

## Alternatives considered
- **A machine user (a second GitHub account).** Its PAT would be long-lived and broad, and it uses up the one machine account GitHub allows per person. An App's tokens are short-lived and scoped.
- **The official Claude GitHub App** (the default identity of `claude-code-action`). We cannot mint its token outside that action, so deterministic steps such as enabling auto-merge could not use it. It would also make the reviewer and the author one identity whenever both run through the action.
- **`GITHUB_TOKEN` for every agent.** Its PRs and pushes would not trigger CI or the reviewer.
- **The owner's PAT for the reviewer.** The reviewer's approvals would count as code-owner approvals and bypass FR-7. Rejected.
- **A fine-grained PAT for the board.** Fine-grained tokens do not cover user-owned Projects v2; a classic token with only `project` is the narrowest that works.

## Consequences
- Agent PRs are authored by the App, so the owner can approve them as code owner and the reviewer can approve them as `github-actions[bot]`.
- App-made pushes and PRs trigger CI and the reviewer. Reviews from the reviewer do not trigger other workflows, so the review workflow does its follow-up steps (auto-merge, revision dispatch) itself (ADR 0015).
- App tokens expire after 1 hour, so agent jobs time out at 55 minutes (ADR 0015).
- The owner must create and install the App, and create the `project`-only PAT. These are manual steps in `docs/workflow.md`.
- Risk: an agent misled by text in an issue or PR could misuse its App token, for example to push to a non-`main` branch or edit issues. `main` is protected, the App is installed on this repo only, and the project PAT cannot touch code.

## Constitution check
- Agents cannot approve their own work or change their own rules unreviewed, which supports principles 6 and 7.
- P8 Simple over clever: one App, the built-in token, one narrow PAT. No per-agent Apps; the role marker identifies the agent.
- No secrets are committed. Every credential is a repository secret, and the subscription token is never logged.
