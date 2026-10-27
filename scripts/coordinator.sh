#!/usr/bin/env bash
# Coordinator loop: polls the GitHub Project for items in Status=Ready and
# hands each to the coordinator agent (which dispatches the architect/developer).
#
# Usage: PROJECT_NUMBER=1 ./scripts/coordinator.sh [--once]
# Env:   PROJECT_NUMBER       (required) the GitHub Project number
#        OWNER                project owner (default: the repository owner)
#        REPO                 owner/name (default: the current repository)
#        INTERVAL             seconds between passes (default 300)
#        MAX_PARALLEL         agents allowed to run at once (default 1)
#        EXTRA_ALLOWED_TOOLS  comma-separated extra `claude --allowedTools` rules
#        CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS  how long claude -p waits for background subagents
#                             (default 0 = indefinitely; set e.g. 7200000 to cap at two hours)
#        AGENT_GH_TOKEN       token every agent (and this script's own gh calls) acts with: the
#                             GitHub App installation token (docs/adr/0014). Required when
#                             GITHUB_ACTIONS=true; locally, agents post as the local gh user without it.
#        PROJECT_TOKEN        project-only token; used by scripts/board.sh for board calls and by
#                             this script to read the board. Never set as GH_TOKEN for an agent.
#        AGENT_GIT_NAME, AGENT_GIT_EMAIL  when both are set, agents commit as this identity
# Requires: gh (project scope), jq, claude CLI. Works with macOS (BSD) tools and bash 3.2.
set -euo pipefail

: "${PROJECT_NUMBER:?set PROJECT_NUMBER}"
for cmd in gh jq claude; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "missing required command: $cmd" >&2; exit 1; }
done
cd "$(dirname "$0")/.."

# `date -I` is GNU-only; this format works on macOS and Linux.
ts() { date '+%Y-%m-%dT%H:%M:%S%z'; }

# Agent identity. In Actions there is no fallback: acting as another token would post as the wrong
# identity, so a missing AGENT_GH_TOKEN stops the run before anything launches.
if [ "${GITHUB_ACTIONS:-}" = "true" ] && [ -z "${AGENT_GH_TOKEN:-}" ]; then
  echo "$(ts) AGENT_GH_TOKEN is missing or empty: agents must run as the GitHub App in Actions (docs/adr/0014); not launching any agent" >&2
  exit 1
fi
if [ -n "${AGENT_GH_TOKEN:-}" ]; then
  # Every gh call below and every claude launch (which inherits the environment) acts as the App.
  # Board calls switch to PROJECT_TOKEN themselves (gh_project here, scripts/board.sh for agents).
  export GH_TOKEN="$AGENT_GH_TOKEN"
else
  echo "$(ts) AGENT_GH_TOKEN is not set: agents will post as the local gh user"
fi
if [ -n "${AGENT_GIT_NAME:-}" ] && [ -n "${AGENT_GIT_EMAIL:-}" ]; then
  # Environment, not git config: it covers the worktrees created here (merge commits) and the ones
  # agents create, and never touches the user's own git configuration.
  export GIT_AUTHOR_NAME="$AGENT_GIT_NAME" GIT_COMMITTER_NAME="$AGENT_GIT_NAME"
  export GIT_AUTHOR_EMAIL="$AGENT_GIT_EMAIL" GIT_COMMITTER_EMAIL="$AGENT_GIT_EMAIL"
fi

REPO="${REPO:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"
OWNER="${OWNER:-${REPO%%/*}}"
INTERVAL="${INTERVAL:-300}"
MAX_PARALLEL="${MAX_PARALLEL:-1}"
# scripts/board.sh (run by the agents) reads these from the environment.
export PROJECT_NUMBER OWNER REPO

# `gh project` calls need the project token when there is one (an App token cannot read a
# user-owned board); otherwise the ambient login is used.
gh_project() {
  if [ -n "${PROJECT_TOKEN:-}" ]; then GH_TOKEN="$PROJECT_TOKEN" gh project "$@"; else gh project "$@"; fi
}

# The coordinator hands work to subagents (the architect plans for longer than ten minutes).
# `claude -p` stops waiting for background subagents after 10 idle minutes and kills them
# mid-run; 0 waits indefinitely. A value already in the environment wins (a cap such as
# 7200000 for two hours). Every claude launched below inherits it.
export CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS="${CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS:-0}"

# `claude -p` is headless: nobody can approve a prompt, so anything not listed here is
# denied. These are the commands the coordinator and the agents it dispatches need.
# Keep it narrow: add more with EXTRA_ALLOWED_TOOLS only when a run stalls on one.
ALLOWED_TOOLS=(
  "Bash(gh issue view *)" "Bash(gh issue comment *)" "Bash(gh issue create *)" "Bash(gh issue edit *)"
  "Bash(gh project item-list *)" "Bash(gh project field-list *)"
  "Bash(gh project view *)" "Bash(gh project list *)"
  "Bash(scripts/board.sh *)"
  "Bash(gh pr create *)" "Bash(gh pr view *)" "Bash(gh pr list *)"
  "Bash(gh api repos/$REPO/*)"
  "Bash(gh api -X PATCH repos/$REPO/*)" "Bash(gh api -X POST repos/$REPO/*)"
  "Bash(gh api --method PATCH repos/$REPO/*)" "Bash(gh api --method POST repos/$REPO/*)"
  "Bash(git checkout *)" "Bash(git add *)" "Bash(git commit *)" "Bash(git push *)"
  "Bash(git status *)" "Bash(git diff *)" "Bash(git fetch *)" "Bash(git pull *)" "Bash(git log *)"
  "Bash(gh pr comment *)"
  "Bash(make check)" "Bash(make fmt)" "Bash(make sync)" "Bash(uv sync)" "Bash(uv run *)"
  "Bash(bash tests/scripts/*)" "Bash(scripts/pin-action.sh *)"
)
if [ -n "${EXTRA_ALLOWED_TOOLS:-}" ]; then
  IFS=, read -r -a extra <<<"$EXTRA_ALLOWED_TOOLS"
  ALLOWED_TOOLS+=("${extra[@]}")
fi

# Agents change a card's Status only through scripts/board.sh, which resolves the project, field
# and option IDs itself and needs PROJECT_TOKEN. This checks that the board is set up before any
# agent is launched, so the failure is one clear message instead of a stalled run.
board_check() { bash ./scripts/board.sh check; }

# Posts a comment on a PR or an issue.
post_comment() { # kind number body
  if [ "$1" = "pr" ]; then
    gh pr comment "$2" --repo "$REPO" --body "$3" || true
  else
    gh issue comment "$2" --repo "$REPO" --body "$3" || true
  fi
}

# Review and revision loop: find agent PRs and plan issues where a trusted human asked for changes
# (a comment containing /revise, or a "Request changes" review) and dispatch the agent that owns
# them in "revision mode". See docs/workflow.md.
#
# Each run happens in its own detached git worktree, so the user's checkout (and any local branch
# of the same name) is never switched or reset. For a PR, origin/main is merged into the worktree
# first: the branch may predate the current agent definitions, and the agent loads them from the
# worktree. If setup fails, the problem is posted with an agent marker, which marks the feedback
# as handled so a persistent failure is not retried every pass; the reviewer comments /revise
# again after fixing it.
revise_pass() {
  local pending count i item kind number agent branch ack prompt wt fail
  pending=$(REPO="$REPO" ./scripts/pending-revisions.sh) || return 1
  count=$(jq length <<<"$pending")
  i=0
  while [ "$i" -lt "$count" ]; do
    item=$(jq -c ".[$i]" <<<"$pending")
    i=$((i + 1))
    kind=$(jq -r .kind <<<"$item")
    number=$(jq -r .number <<<"$item")
    agent=$(jq -r .agent <<<"$item")
    branch=$(jq -r '.branch // empty' <<<"$item")
    wt=$(mktemp -d "${TMPDIR:-/tmp}/revise-XXXXXX")
    fail=""
    if [ "$kind" = "pr" ]; then
      echo "$(ts) revising PR #$number ($branch) with the $agent agent"
      if ! { git fetch -q origin main "+refs/heads/$branch:refs/remotes/origin/$branch" \
             && git worktree add -q --detach "$wt" "origin/$branch"; }; then
        fail="Could not create a git worktree for $branch (is the network down?). Fix that and comment /revise again."
      elif ! git -C "$wt" merge -q --no-edit origin/main; then
        git -C "$wt" merge --abort >/dev/null 2>&1 || true
        fail="Could not bring $branch up to date with main (merge conflict). Resolve the conflict, then comment /revise again."
      fi
      prompt="Revise after review (revision mode). Repository $REPO, PR #$number, branch $branch; you are in a git worktree with that branch's code, up to date with main. A trusted reviewer left feedback newer than your last reply. Follow the 'Revise after review' section of your agent definition: read all comments, review comments and reviews on the PR yourself, address them with new commits, reply on each thread, and push to $branch (git push origin HEAD:$branch). This prompt deliberately does not repeat the feedback."
    else
      echo "$(ts) revising the plan on issue #$number with the $agent agent"
      if ! { git fetch -q origin main && git worktree add -q --detach "$wt" origin/main; }; then
        fail="Could not create a git worktree for the plan revision (is the network down?). Fix that and comment /revise again."
      fi
      prompt="Revise after review (revision mode). Repository $REPO, plan feedback on issue #$number; you are in a git worktree at origin/main. A trusted reviewer left feedback newer than your last reply. Follow the 'Revise after review' section of your agent definition: read the issue comments yourself, update the Technical plan comment, the ADR PR and the task sub-issues as needed, and reply. This prompt deliberately does not repeat the feedback."
    fi
    if [ "$agent" = "architect" ]; then
      prompt="$prompt Project $PROJECT_NUMBER (owner $OWNER). Change the board only with scripts/board.sh (see your agent definition). When you have resolved the open questions, move the affected task issues from Plan Review to Ready and the feature to Implementation."
    fi
    if [ -n "$fail" ]; then
      echo "$(ts) revision of $kind #$number not started: $fail" >&2
      post_comment "$kind" "$number" "$fail <!-- agent:ack -->"
    else
      ack="Picked up: the $agent agent is revising in response to the latest feedback. If this run fails, comment /revise again. <!-- agent:ack -->"
      post_comment "$kind" "$number" "$ack"
      # The prompt goes first: --allowedTools takes a list and would swallow a trailing prompt.
      if ! (cd "$wt" && claude -p "$prompt" --agent "$agent" --permission-mode acceptEdits \
              --allowedTools "${ALLOWED_TOOLS[@]}"); then
        echo "$(ts) revision of $kind #$number failed (comment /revise again to retry)" >&2
      fi
    fi
    git worktree remove --force "$wt" >/dev/null 2>&1 || true
    rm -rf "$wt"
  done
}

# Echoes each blocker of issue $1 that is not done yet, one number per line. Blockers are the #N
# references on the issue body's "Blocked by:" line (plain, bulleted, or bold). A closed issue is
# done; a PR counts only once it is merged. No such line, or "none", means no blockers.
open_blockers() {
  local body refs ref st
  body=$(gh issue view "$1" --repo "$REPO" --json body -q .body) || return 0
  # Only the first sentence of the line counts: "Blocked by #17. Can run in parallel with #18."
  # blocks on #17 alone.
  refs=$(grep -iE '^[[:space:]]*[-*]*[[:space:]]*\**blocked by' <<<"$body" | sed -E 's/\. .*$//' | grep -oE '#[0-9]+' | tr -d '#' | sort -un || true)
  for ref in $refs; do
    st=$(gh api "repos/$REPO/issues/$ref" --jq 'if .state == "closed" and ((.pull_request // null) == null or .pull_request.merged_at != null) then "done" else "open" end' 2>/dev/null) || st=open
    if [ "$st" != "done" ]; then echo "$ref"; fi
  done
}

# Dispatches Ready work. Tasks and bugs are Ready by default once planned, so the script (not the
# model) holds back any whose blockers are not done; features and architecture issues are not gated.
ready_pass() {
  local all ready count i item n waiting
  all=$(gh_project item-list "$PROJECT_NUMBER" --owner "$OWNER" --limit 200 --format json \
    | jq -c '[.items[] | select(.status=="Ready" and .content.number != null)
              | {item_id: .id, number: .content.number, title: .content.title, labels: (.labels // [])}]')
  count=$(jq length <<<"$all")
  ready="[]"
  i=0
  while [ "$i" -lt "$count" ]; do
    item=$(jq -c ".[$i]" <<<"$all")
    i=$((i + 1))
    n=$(jq -r .number <<<"$item")
    if jq -e '.labels | any(. == "type:task" or . == "type:bug")' <<<"$item" >/dev/null; then
      waiting=$(open_blockers "$n" | sed 's/^/#/' | paste -sd, - | sed 's/,/, /g')
      if [ -n "$waiting" ]; then
        echo "$(ts) #$n waits on $waiting"
        continue
      fi
    fi
    ready=$(jq -c --argjson it "$item" '. + [$it]' <<<"$ready")
  done
  if [ "$(jq length <<<"$ready")" -eq 0 ]; then
    echo "$(ts) nothing Ready"
    return 0
  fi
  board_check || return 1
  echo "$(ts) Ready items: $ready"
  # The prompt goes first: --allowedTools takes a list and would swallow a trailing prompt.
  claude -p "Project $PROJECT_NUMBER (owner $OWNER), MAX_PARALLEL=$MAX_PARALLEL. Items in Ready (item_id is the board item ID): $ready. Only tasks whose Blocked by: blockers are done are listed. Run one coordination pass per docs/workflow.md and report what you dispatched. To change an item's Status, run: scripts/board.sh status <issue-number> <Status name>; it is the only way to touch the board. When you dispatch the architect, tell it the project number and owner and to use scripts/board.sh (add <issue-url>, status <issue-number> <Status name>) to put the task issues it creates on the board: in Ready, or in Plan Review if it has open questions for the owner." \
    --agent coordinator --permission-mode acceptEdits \
    --allowedTools "${ALLOWED_TOOLS[@]}"
}

# Revisions first (finish work already in flight), then new Ready work.
pass() {
  revise_pass || echo "$(ts) revision pass failed" >&2
  ready_pass
}

pass
if [ "${1:-}" = "--once" ]; then exit 0; fi
while sleep "$INTERVAL"; do
  pass || echo "$(ts) pass failed, retrying next interval"
done
