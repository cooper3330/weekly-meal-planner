#!/usr/bin/env bash
# Coordinator loop: polls the GitHub Project for items in Status=Ready and
# hands each to the coordinator agent (which dispatches the architect/developer).
#
# Usage: PROJECT_NUMBER=1 ./scripts/coordinator.sh [--once | --ready-label <issue-number>]
#        PROJECT_NUMBER=1 ./scripts/coordinator.sh --revise <pr|issue> <number> [--source human|reviewer]
#   --revise runs one revision for one item and exits (event-driven, from .github/workflows/revise.yml).
#   With --source human (default) the item must be pending according to scripts/pending-revisions.sh;
#   with --source reviewer it must be an open issue-* PR whose latest reviewer review requests changes
#   on the current head. A stale or duplicate request logs and exits 0 without posting.
#   --source ci --run <run-id> (from .github/workflows/ci-fix.yml) revises an issue-* PR whose `check` job
#   failed in that run, on the PR's current head, while main is green and the revision budget (shared with
#   the reviewer source) is not spent. An arch-* PR gets the needs-human label instead.
#        --ready-label N handles one `ready` label event on issue N (sets its card to Ready if it is not
#        yet moving, removes the label), then runs one pass like --once.
# Env:   PROJECT_NUMBER       (required) the GitHub Project number
#        OWNER                project owner (default: the repository owner)
#        REPO                 owner/name (default: the current repository)
#        INTERVAL             seconds between passes (default 300)
#        MAX_PARALLEL         agents allowed to run at once (default 1)
#        ARCHITECT_MAX_TURNS, DEVELOPER_MAX_TURNS  `--max-turns` for architect / developer launches
#                             (defaults 60 / 80; empty means the default). The coordinator's own run gets 30.
#        EXTRA_ALLOWED_TOOLS  comma-separated extra `claude --allowedTools` rules
#        PROTECTED_PATH_RE / WRITABLE_PATH_RE  paths a headless agent cannot write (held for an
#                             interactive session) and the exceptions to them; see below
#        CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS  how long claude -p waits for background subagents
#                             (default 0 = indefinitely; set e.g. 7200000 to cap at two hours)
#        AGENT_GH_TOKEN       token every agent (and this script's own gh calls) acts with: the
#                             GitHub App installation token (docs/adr/0014). Required when
#                             GITHUB_ACTIONS=true; locally, agents post as the local gh user without it.
#        PROJECT_TOKEN        board-scoped token (project + read:org); used by scripts/board.sh for board calls and by
#                             this script to read the board. Never set as GH_TOKEN for an agent.
#        AGENT_GIT_NAME, AGENT_GIT_EMAIL  when both are set, agents commit as this identity
# Requires: gh (project scope), jq, claude CLI. Works with macOS (BSD) tools and bash 3.2.
set -euo pipefail

usage() {
  echo "usage: scripts/coordinator.sh [--once | --ready-label <issue-number> | --revise <pr|issue> <number> [--source human|reviewer | --source ci --run <run-id>]]" >&2
  exit 2
}

# Argument check, before anything touches GitHub. Modes: no flag (loop), --once (one pass), and
# --revise (one revision for one item; used by .github/workflows/revise.yml).
MODE=loop; REV_KIND=""; REV_NUMBER=""; REV_SOURCE=human; REV_RUN=""
# --ready-label N: handle one `ready` label event on issue N, then run one pass (like --once).
# Validated here, so a bad number launches and writes nothing.
READY_LABEL_ISSUE=""
case "${1:-}" in
  "") ;;
  --once) [ $# -eq 1 ] || usage; MODE=once ;;
  --ready-label)
    case "${2:-}" in
      ''|*[!0-9]*|0|0[0-9]*) echo "usage: coordinator.sh --ready-label <issue-number> (a positive integer)" >&2; exit 2 ;;
    esac
    READY_LABEL_ISSUE="$2"; MODE=once ;;
  --revise)
    [ $# -ge 3 ] || usage
    REV_KIND="$2"; REV_NUMBER="$3"; shift 3
    case "$REV_KIND" in pr|issue) ;; *) usage ;; esac
    case "$REV_NUMBER" in ''|*[!0-9]*) usage ;; esac
    while [ $# -gt 0 ]; do
      [ $# -ge 2 ] || usage
      case "$1" in
        --source) REV_SOURCE="$2" ;;
        --run) REV_RUN="$2" ;;
        *) usage ;;
      esac
      shift 2
    done
    case "$REV_SOURCE" in human|reviewer|ci) ;; *) usage ;; esac
    if [ "$REV_SOURCE" != human ] && [ "$REV_KIND" != pr ]; then usage; fi
    # --run is the failed CI run, and goes with --source ci only (a positive integer).
    if [ "$REV_SOURCE" = ci ]; then
      case "$REV_RUN" in ''|*[!0-9]*|0|0[0-9]*) usage ;; esac
    elif [ -n "$REV_RUN" ]; then
      usage
    fi
    MODE=revise ;;
  *) usage ;;
esac

: "${PROJECT_NUMBER:?set PROJECT_NUMBER}"
for cmd in gh jq claude; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "missing required command: $cmd" >&2; exit 1; }
done
cd "$(dirname "$0")/.."

# Every claude launch goes through the guard (usage-limit reporting, no retry; docs/adr/0015).
GUARD="$PWD/scripts/agent-guard.sh"

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

# In Actions, stop early (with the fix named) if the App cannot push; agents would otherwise run to the
# end of a task and fail at `git push`. The probe is a dry-run push of a throwaway branch name: git does
# the same authenticated handshake as a real push and changes nothing on the remote. It stops the run
# only on a clear refusal (403, permission denied); any other failure (network, an odd error) is logged
# and lets the run continue. The permissions the token reports are logged for diagnosis only: that field
# is not a reliable answer for an App installation token, so it never decides.
if [ "${GITHUB_ACTIONS:-}" = "true" ]; then
  reported=$(gh api "repos/$REPO" --jq .permissions 2>/dev/null || true)
  echo "$(ts) reported repository permissions for this token: ${reported:-unavailable}"
  probe_rc=0
  probe=$(git push --dry-run origin "HEAD:refs/heads/agent-push-probe" 2>&1) || probe_rc=$?
  if [ "$probe_rc" -ne 0 ]; then
    if grep -qiE '(returned error: )?403|permission to .* denied|permission denied|not permitted|write access .*not granted' <<<"$probe"; then
      echo "$(ts) the GitHub App cannot push to $REPO: give the App Contents: Read & write and accept it on the installation (docs/adr/0014); not launching any agent" >&2
      echo "$(ts) git said: $probe" >&2
      # A revision has an item to tell: say so there, once, so the owner does not have to open the Actions log.
      if [ "$MODE" = revise ]; then
        # --paginate with --jq would run the filter once per page; slurp every page, then take the newest agent comment.
        last=$(gh api "repos/$REPO/issues/$REV_NUMBER/comments?per_page=100" --paginate 2>/dev/null | jq -rs '[.[][] | select((.body // "") | contains("<!-- agent:"))] | last | (.body // "")' 2>/dev/null || true)
        if ! grep -qF '<!-- agent:permission -->' <<<"$last"; then
          msg="This revision could not start: a test push from the GitHub App that runs the agents was refused. In the App's settings give it Contents: Read and write (and Pull requests: Read and write), accept the new permissions on the App's installation for this repository, then comment /revise again."$'\n\n'"git said:"$'\n\n'"${probe}"$'\n\n'"The token reported these repository permissions: ${reported:-unavailable}"$'\n\n'"<!-- agent:permission -->"
          if [ "$REV_KIND" = pr ]; then
            gh pr comment "$REV_NUMBER" --repo "$REPO" --body "$msg" || true
          else
            gh issue comment "$REV_NUMBER" --repo "$REPO" --body "$msg" || true
          fi
        fi
      fi
      exit 1
    fi
    echo "$(ts) could not tell whether the App can push (the dry-run push failed for another reason); continuing: $probe" >&2
  fi
fi
OWNER="${OWNER:-${REPO%%/*}}"
INTERVAL="${INTERVAL:-300}"
MAX_PARALLEL="${MAX_PARALLEL:-1}"
# Turn caps per launch (docs/adr/0015). An empty value (an unset repository variable) means the default.
ARCHITECT_MAX_TURNS="${ARCHITECT_MAX_TURNS:-60}"
DEVELOPER_MAX_TURNS="${DEVELOPER_MAX_TURNS:-80}"
COORDINATOR_MAX_TURNS=30
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
  "Bash(scripts/board.sh *)" "Bash(bash scripts/board.sh *)"
  "Bash(gh pr create *)" "Bash(gh pr view *)" "Bash(gh pr list *)"
  "Bash(gh api repos/$REPO/*)"
  "Bash(gh api -X PATCH repos/$REPO/*)" "Bash(gh api -X POST repos/$REPO/*)"
  "Bash(gh api --method PATCH repos/$REPO/*)" "Bash(gh api --method POST repos/$REPO/*)"
  "Bash(git checkout *)" "Bash(git add *)" "Bash(git commit *)" "Bash(git push *)"
  "Bash(git status *)" "Bash(git diff *)" "Bash(git fetch *)" "Bash(git log *)"
  "Bash(git merge origin/main)" "Bash(git merge --no-edit origin/main)" "Bash(git merge --abort)"
  "Bash(git merge-base *)"
  "Bash(gh pr checks *)" "Bash(gh run view *)" "Bash(gh run list *)"
  "Bash(gh pr comment *)"
  "Bash(make check)" "Bash(make fmt)" "Bash(make sync)" "Bash(uv sync)" "Bash(uv run *)"
  "Bash(bash tests/scripts/*)" "Bash(scripts/pin-action.sh *)" "Bash(bash scripts/pin-action.sh *)"
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
#
# revise_item runs one revision for one item ({kind, number, agent, branch?, linked_issue?}). With source
# "reviewer" the feedback is the reviewer agent's CHANGES_REQUESTED review (the developer is told so in
# its prompt), and the acknowledgement carries source=reviewer, which is how scripts/after-review.sh
# counts rounds against REVISION_BUDGET.
revise_item() { # item-json [source] [ci-run-id]
  local item="$1" source="${2:-human}" run="${3:-}" kind number agent branch linked ack prompt wt fail turns marker
    kind=$(jq -r .kind <<<"$item")
    number=$(jq -r .number <<<"$item")
    agent=$(jq -r .agent <<<"$item")
    branch=$(jq -r '.branch // empty' <<<"$item")
    linked=$(jq -r '.linked_issue // empty' <<<"$item")
    marker="<!-- agent:ack -->"
    if [ "$source" = reviewer ]; then marker="<!-- agent:ack source=reviewer -->"; fi
    if [ "$source" = ci ]; then marker="<!-- agent:ack source=ci -->"; fi
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
      if [ "$source" = reviewer ]; then
        prompt="$prompt Source: reviewer. The feedback to act on is the latest review on the PR by github-actions[bot] that carries the reviewer marker and requests changes on the current head commit. Treat that review as feedback, as your agent definition describes."
      fi
      if [ "$source" = ci ]; then
        prompt="$prompt Source: CI. The check job of CI run $run failed on the current head of this PR; there may be no human or reviewer feedback to read. Follow the CI-source rules of your agent definition: read the failure with gh run view $run --log-failed and gh pr checks $number, fix it only if this PR's change caused it, and otherwise comment why it is not this PR's and stop."
      fi
      if [ -n "$linked" ]; then
        prompt="$prompt The task's linked issue #$linked also counts: its comments from a trusted reviewer are feedback on this PR too (read them with gh api repos/$REPO/issues/$linked/comments)."
      fi
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
      ack="Picked up: the $agent agent is revising in response to the latest feedback. If this run fails, comment /revise again. $marker"
      if [ "$source" = ci ]; then
        ack="Picked up: the $agent agent is fixing the failed CI check [run $run]. If this run fails, comment /revise to try again. $marker"
      fi
      post_comment "$kind" "$number" "$ack"
      # The prompt goes first: --allowedTools takes a list and would swallow a trailing prompt.
      turns=$DEVELOPER_MAX_TURNS
      if [ "$agent" = "architect" ]; then turns=$ARCHITECT_MAX_TURNS; fi
      if ! (cd "$wt" && bash "$GUARD" run --kind "$kind" --number "$number" -- \
              claude -p "$prompt" --agent "$agent" --permission-mode acceptEdits --max-turns "$turns" \
              --allowedTools "${ALLOWED_TOOLS[@]}"); then
        echo "$(ts) revision of $kind #$number failed (comment /revise again to retry)" >&2
      fi
    fi
    git worktree remove --force "$wt" >/dev/null 2>&1 || true
    rm -rf "$wt"
}

revise_pass() {
  local pending count i
  pending=$(REPO="$REPO" ./scripts/pending-revisions.sh) || return 1
  count=$(jq length <<<"$pending")
  i=0
  while [ "$i" -lt "$count" ]; do
    revise_item "$(jq -c ".[$i]" <<<"$pending")" human
    i=$((i + 1))
  done
}

hand_to_human() { # pr-number: label needs-human (creating the label if needed); ask the owner for a review
  gh label create needs-human --repo "$REPO" --color D93F0B \
    --description "An agent run stopped and a human must decide" >/dev/null 2>&1 || true
  gh pr edit "$1" --repo "$REPO" --add-label needs-human >/dev/null || true
  gh pr edit "$1" --repo "$REPO" --add-reviewer "$OWNER" >/dev/null 2>&1 \
    || echo "$(ts) could not request a review from $OWNER (they may be the PR author)"
}

# --source ci: the `check` job failed on an agent PR (.github/workflows/ci-fix.yml). Everything here is
# a deterministic check; the model only runs at the end. Stale, duplicate or unfixable events log why
# and return 0 without posting; a PR that needs a human gets one comment and the needs-human label.
revise_ci() { # pr-number run-id
  local number="$1" run="$2" job_json pr state branch head labels run_json jobs_json job_url rounds budget comments main_state item
  pr=$(gh pr view "$number" --repo "$REPO" --json state,headRefName,headRefOid,labels) || return 1
  state=$(jq -r '.state // ""' <<<"$pr")
  branch=$(jq -r '.headRefName // ""' <<<"$pr")
  head=$(jq -r '.headRefOid // ""' <<<"$pr")
  labels=$(jq -r '(.labels // [])[].name' <<<"$pr")
  case "$branch" in
    issue-*|arch-*) ;;
    *) echo "$(ts) PR #$number: branch '$branch' is neither issue-* nor arch-*; nothing to do"; return 0 ;;
  esac
  if [ "$state" != OPEN ]; then echo "$(ts) PR #$number is $state; nothing to do"; return 0; fi

  # The failed run must be for the PR's current head, and its `check` job must have failed in a step
  # (not been cancelled, or died while setting up the runner before any step ran).
  run_json=$(gh api "repos/$REPO/actions/runs/$run") || return 1
  if [ -z "$head" ] || [ "$(jq -r '.head_sha // ""' <<<"$run_json")" != "$head" ]; then
    echo "$(ts) PR #$number: CI run $run is not for the current head ${head:0:7} (a newer push makes it stale); nothing to do"
    return 0
  fi
  if [ "$(jq -r '.conclusion // ""' <<<"$run_json")" != failure ]; then
    echo "$(ts) PR #$number: CI run $run did not fail (conclusion '$(jq -r '.conclusion // ""' <<<"$run_json")'); nothing to do"
    return 0
  fi
  jobs_json=$(gh api "repos/$REPO/actions/runs/$run/jobs?per_page=100") || return 1
  job_json=$(jq -c '[(.jobs // [])[] | select(.name == "check" and .conclusion == "failure"
              and ([(.steps // [])[] | select(.conclusion == "failure" and .name != "Set up job")] | length) > 0)]
            | first // empty' <<<"$jobs_json")
  job_url=$(jq -r '.html_url // "the failed run"' <<<"${job_json:-null}")
  if [ -z "$job_json" ]; then
    echo "$(ts) PR #$number: the check job of run $run did not fail in a step (cancelled, or a runner or setup failure); nothing to do"
    return 0
  fi

  if [[ "$branch" == arch-* ]]; then
    if printf '%s\n' "$labels" | grep -qx 'needs-human'; then
      echo "$(ts) PR #$number: ADR PR with a failed check is already with a human; nothing to do"
      return 0
    fi
    echo "$(ts) PR #$number: ADR PRs are not fixed automatically; handing it to $OWNER"
    hand_to_human "$number"
    post_comment pr "$number" "The \`check\` job failed on this ADR PR ($job_url). ADR PRs are not fixed automatically: the needs-human label is set and the owner's review is requested. Comment /revise after fixing or to ask the architect for a revision.

<!-- agent:merge-gate -->"
    return 0
  fi

  comments=$(gh api "repos/$REPO/issues/$number/comments?per_page=100" --paginate \
    | jq -sc '[.[] | (if type == "array" then .[] else . end)]')
  # Once per run: a CI acknowledgement that names this run means it was taken (a re-delivered event).
  if jq -e --arg m "[run $run]" '[.[] | select((.body // "") | contains("<!-- agent:ack source=ci -->") and contains($m))] | length > 0' <<<"$comments" >/dev/null; then
    echo "$(ts) PR #$number: CI run $run was already picked up; nothing to do"
    return 0
  fi

  # If main is red, the cause is not this PR. The latest finished CI run on main, cancelled and skipped
  # runs aside, is what counts.
  main_state=$(gh api "repos/$REPO/actions/runs?branch=main&event=push&status=completed&per_page=20" \
    | jq -r '[(.workflow_runs // [])[] | select(.name == "CI" and (.conclusion == "success" or .conclusion == "failure"))] | (first // {}) | .conclusion // ""') || return 1
  if [ "$main_state" = failure ]; then
    echo "$(ts) PR #$number: the latest CI run on main failed; not this PR's to fix"
    post_comment pr "$number" "The \`check\` job failed on this PR [run $run], but CI on \`main\` is failing too, so the cause is probably not this PR. No automatic fix is started. Fix \`main\` first; then comment /revise if this PR still needs a fix. <!-- agent:ack source=ci -->"
    return 0
  fi

  # The budget is shared with the reviewer: both kinds of acknowledgement are counted. Human /revise
  # rounds (an acknowledgement without source=) never count.
  budget="${REVISION_BUDGET:-3}"
  case "$budget" in ''|*[!0-9]*) budget=3 ;; esac
  rounds=$(jq -r '[.[] | select((.body // "") | contains("<!-- agent:ack source=reviewer -->") or contains("<!-- agent:ack source=ci -->"))] | length' <<<"$comments")
  if [ "$rounds" -ge "$budget" ]; then
    if printf '%s\n' "$labels" | grep -qx 'needs-human'; then
      echo "$(ts) PR #$number: revision budget ($budget) is spent and the PR is already with a human; nothing to do"
      return 0
    fi
    echo "$(ts) PR #$number: revision budget ($budget) is spent after $rounds rounds; handing it to $OWNER"
    hand_to_human "$number"
    post_comment pr "$number" "The revision budget is spent: the developer has already revised this PR $rounds times in response to the reviewer or to failing CI (budget $budget, set by REVISION_BUDGET). The \`check\` job failed again ($job_url). It is handed to a human: the owner's review is requested. Comment /revise to run another revision; that does not count against the budget.

<!-- agent:merge-gate -->"
    return 0
  fi

  item=$(jq -nc --argjson n "$number" --arg b "$branch" --arg l "$(sed -nE 's/^issue-([0-9]+)-.*/\1/p' <<<"$branch")" \
    '{kind: "pr", number: $n, agent: "developer", branch: $b, linked_issue: (if $l == "" then null else ($l | tonumber) end)}')
  revise_item "$item" ci "$run"
}

# --revise <kind> <number> [--source human|reviewer|ci]: one revision for one item, then exit. Events can
# be stale or duplicated (the second event of one review submission, a re-run), so every run first
# checks that the item still needs a revision and otherwise logs and exits 0 without posting.
revise_one() { # kind number source
  local kind="$1" number="$2" source="$3" pending item
  if [ "$source" = human ]; then
    # scripts/pending-revisions.sh stays the source of truth: trusted author, no agent marker,
    # newer than the last agent comment, linked task issues included.
    pending=$(REPO="$REPO" ./scripts/pending-revisions.sh) || return 1
    item=$(jq -c --arg k "$kind" --argjson n "$number" '[.[] | select(.kind == $k and .number == $n)] | first // empty' <<<"$pending")
    if [ -z "$item" ]; then
      echo "$(ts) $kind #$number is not pending a revision (stale or duplicate event); nothing to do"
      return 0
    fi
    revise_item "$item" human
    return 0
  fi

  if [ "$source" = ci ]; then
    revise_ci "$number" "$REV_RUN"
    return $?
  fi

  # Reviewer source: the reviewer is github-actions[bot], which pending-revisions never trusts, so
  # the checks are made here. Developer PRs (issue-*) only: ADR PRs are revised on human feedback.
  local pr state branch head review review_state review_time acked
  pr=$(gh pr view "$number" --repo "$REPO" --json state,headRefName,headRefOid) || return 1
  state=$(jq -r '.state // ""' <<<"$pr")
  branch=$(jq -r '.headRefName // ""' <<<"$pr")
  head=$(jq -r '.headRefOid // ""' <<<"$pr")
  case "$branch" in
    issue-*) ;;
    *) echo "$(ts) refusing a reviewer-requested revision of PR #$number: its branch '$branch' is not issue-* (ADR PRs are revised on human feedback only)" >&2; return 1 ;;
  esac
  if [ "$state" != OPEN ]; then
    echo "$(ts) PR #$number is $state; nothing to do"
    return 0
  fi
  # The latest review by the reviewer bot with the reviewer marker, on any head: it must be a change
  # request, and on the current head (an older one is stale, the developer has pushed since).
  review=$(gh api "repos/$REPO/pulls/$number/reviews?per_page=100" --paginate \
    | jq -sc '[.[] | (if type == "array" then .[] else . end)
               | select(.user.login == "github-actions[bot]" and ((.body // "") | contains("<!-- agent:reviewer -->")))]
              | sort_by(.submitted_at) | last // {}')
  review_state=$(jq -r '.state // ""' <<<"$review")
  review_time=$(jq -r '.submitted_at // ""' <<<"$review")
  if [ "$review_state" != CHANGES_REQUESTED ] || [ "$(jq -r '.commit_id // ""' <<<"$review")" != "$head" ] || [ -z "$head" ]; then
    echo "$(ts) PR #$number: the latest reviewer review is '${review_state:-none}' on ${head:0:7} or on an older head; nothing to do"
    return 0
  fi
  # Once per review: an acknowledgement for the reviewer source newer than the review means it was taken.
  acked=$(gh api "repos/$REPO/issues/$number/comments?per_page=100" --paginate \
    | jq -s --arg t "$review_time" '[.[] | (if type == "array" then .[] else . end)
          | select((.body // "") | contains("<!-- agent:ack source=reviewer -->")) | select(.created_at > $t)] | length')
  if [ "${acked:-0}" -gt 0 ]; then
    echo "$(ts) PR #$number: this review was already picked up; nothing to do"
    return 0
  fi
  item=$(jq -nc --argjson n "$number" --arg b "$branch" --arg l "$(sed -nE 's/^issue-([0-9]+)-.*/\1/p' <<<"$branch")" \
    '{kind: "pr", number: $n, agent: "developer", branch: $b, linked_issue: (if $l == "" then null else ($l | tonumber) end)}')
  revise_item "$item" reviewer
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

# Claude Code does not let a headless run write to its protected paths (.git, editor folders, and
# most of .claude/, such as settings.json) and nobody can approve the prompt, so a task that edits
# them cannot be done by a dispatched agent. Matched against the paths a task lists under "Files
# likely touched".
#
# Exception, tested on Claude Code 2.1.312 with --permission-mode acceptEdits: files under
# .claude/agents/, .claude/commands/ and .claude/skills/ ARE writable headless (a subagent can write
# them too), so tasks that only edit agent definitions are dispatched. If a machine's CLI denies them,
# the denial report names the command, and WRITABLE_PATH_RE='^$' holds them again.
PROTECTED_PATH_RE="${PROTECTED_PATH_RE:-^(\.claude|\.git|\.vscode|\.idea|\.husky|\.devcontainer)/|^(\.mcp\.json|\.claude\.json)\$}"
WRITABLE_PATH_RE="${WRITABLE_PATH_RE:-^\.claude/(agents|commands|skills)/}"

# Echoes the protected paths that issue $1 lists in its "Files likely touched" section (written as
# a "###" heading or a bold line), one per line. Paths mentioned elsewhere in the body do not count.
protected_paths() {
  local body
  body=$(gh issue view "$1" --repo "$REPO" --json body -q .body) || return 0
  awk 'tolower($0) ~ /files likely touched/ { on = 1; next } on && /^(###|\*\*)/ { exit } on { print }' <<<"$body" \
    | { grep -oE '`[^`]+`' || true; } | tr -d '`' | sed 's|^\./||' \
    | { grep -E "$PROTECTED_PATH_RE" || true; } | { grep -Ev "$WRITABLE_PATH_RE" || true; }
}

# Holds task $1 (protected paths in $2) for an interactive session: says so on the issue and moves
# the card to Blocked, which also stops the same comment being posted on every pass.
hold_for_interactive() {
  local n="$1" paths list
  list=$(paste -sd, - <<<"$2" | sed 's/,/, /g')
  echo "$(ts) #$n edits protected paths ($list): needs an interactive session, not dispatching"
  post_comment issue "$n" "Held for an interactive session: this task edits protected paths ($list). Claude Code does not let a headless run write there and nobody can approve it, so a dispatched agent would stop (agent definitions under .claude/agents, commands and skills are not affected). Run it yourself: \`cd <your checkout> && claude --agent developer\`, ask it to implement #$n, and approve the protected edits when it asks. Or make those edits by hand. <!-- agent:coordinator -->"
  bash ./scripts/board.sh status "$n" Blocked || echo "$(ts) could not move #$n to Blocked" >&2
}

# Saves whatever a run left behind in worktree $1: uncommitted changes and commits that exist on no
# remote. They go to a NEW branch, wip-<branch>-<time>, never onto the agent's own branch (that may
# have an open PR). Returns non-zero when they could not be saved, so the caller keeps the worktree.
salvage_worktree() {
  local wt="$1" dirty unpushed base wip n
  dirty=$(git -C "$wt" status --porcelain 2>/dev/null || true)
  unpushed=$(git -C "$wt" rev-list --count HEAD --not --remotes 2>/dev/null || echo 0)
  if [ -z "$dirty" ] && [ "${unpushed:-0}" -eq 0 ]; then return 0; fi
  base=$(git -C "$wt" rev-parse --abbrev-ref HEAD 2>/dev/null || echo HEAD)
  case "$base" in HEAD|main|"") base="dispatch" ;; esac
  wip="wip-$base-$(date +%Y%m%d%H%M%S)"
  if [ -n "$dirty" ]; then
    git -C "$wt" add -A && git -C "$wt" commit -q -m "WIP: unfinished agent work saved by the coordinator" \
      || { echo "$(ts) could not commit the unfinished work in $wt" >&2; return 1; }
  fi
  if ! git -C "$wt" push -q origin "HEAD:refs/heads/$wip"; then
    echo "$(ts) could not push the unfinished work from $wt" >&2
    return 1
  fi
  echo "$(ts) unfinished agent work saved to branch $wip"
  n=$(sed -nE 's/^issue-([0-9]+)-.*/\1/p' <<<"$base")
  if [ -n "$n" ]; then
    post_comment issue "$n" "A headless run stopped with unfinished work. It was saved to branch \`$wip\` (not the PR branch). Continue from it, or restart the task. <!-- agent:coordinator -->"
  fi
  return 0
}

# Prints a run's result and announces the commands and edits it was denied. \`claude -p\` cannot ask
# for approval, so a denial silently stops the agent; listing them is how the owner knows exactly
# what to allow. Output that is not JSON is passed through untouched.
report_run() {
  local out="$1" denied
  if jq -e . >/dev/null 2>&1 <<<"$out"; then
    jq -r '.result // empty' <<<"$out"
    denied=$(jq -r '[.permission_denials[]? | "\(.tool_name): \(.tool_input.command // .tool_input.file_path // (.tool_input | tostring))"] | unique | .[]' <<<"$out" 2>/dev/null || true)
    if [ -n "$denied" ]; then
      echo "$(ts) denied during this run (allow them in scripts/coordinator.sh, or run the task interactively):" >&2
      sed 's/^/  /' <<<"$denied" >&2
    fi
  else
    printf '%s\n' "$out"
  fi
}

# Dispatches Ready work. Tasks and bugs are Ready by default once planned, so the script (not the
# model) holds back any whose blockers are not done, and any that edit protected paths; features
# and architecture issues are not gated.
#
# The coordinator and the agents it starts run in their own detached worktree of origin/main, so
# the owner's checkout (and whatever branch or uncommitted work it has) is never touched. Whatever
# the run leaves unfinished is saved by salvage_worktree before the worktree is deleted.
ready_pass() {
  local all ready count i item n waiting prot wt out rc salvaged guard_kind guard_number
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
      prot=$(protected_paths "$n")
      if [ -n "$prot" ]; then
        hold_for_interactive "$n" "$prot"
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
  wt=$(mktemp -d "${TMPDIR:-/tmp}/dispatch-XXXXXX")
  if ! { git fetch -q origin main && git worktree add -q --detach "$wt" origin/main; }; then
    echo "$(ts) could not create a git worktree for this pass (is the network down?); not dispatching" >&2
    rm -rf "$wt"
    return 1
  fi
  rc=0
  # The prompt goes first: --allowedTools takes a list and would swallow a trailing prompt.
  # A usage-limit stop is reported on the item when exactly one was dispatched, else only logged.
  guard_kind=none; guard_number=()
  if [ "$(jq length <<<"$ready")" -eq 1 ]; then
    guard_kind=issue; guard_number=(--number "$(jq -r '.[0].number' <<<"$ready")")
  fi
  out=$(cd "$wt" && bash "$GUARD" run --kind "$guard_kind" ${guard_number[@]+"${guard_number[@]}"} -- claude -p "Project $PROJECT_NUMBER (owner $OWNER), MAX_PARALLEL=$MAX_PARALLEL. Items in Ready (item_id is the board item ID): $ready. Only tasks whose Blocked by: blockers are done are listed. Run one coordination pass per docs/workflow.md and report what you dispatched. You are in a fresh git worktree at origin/main; the agents you start work in it too. To change an item's Status, run: scripts/board.sh status <issue-number> <Status name>; it is the only way to touch the board. When you dispatch the architect, tell it the project number and owner and to use scripts/board.sh (add <issue-url>, status <issue-number> <Status name>) to put the task issues it creates on the board: in Ready, or in Plan Review if it has open questions for the owner. Turn caps: when you start the architect, pass --max-turns $ARCHITECT_MAX_TURNS; when you start the developer, pass --max-turns $DEVELOPER_MAX_TURNS." \
    --agent coordinator --permission-mode acceptEdits --output-format json --max-turns "$COORDINATOR_MAX_TURNS" \
    --allowedTools "${ALLOWED_TOOLS[@]}") || rc=$?
  report_run "$out"
  salvaged=0
  salvage_worktree "$wt" || salvaged=1
  if [ "$salvaged" -eq 0 ]; then
    git worktree remove --force "$wt" >/dev/null 2>&1 || true
    rm -rf "$wt"
  else
    echo "$(ts) worktree kept at $wt because its unfinished work could not be saved; look there before deleting it" >&2
    rc=1
  fi
  return "$rc"
}

# Revisions first (finish work already in flight), then new Ready work. Labelled issues are swept
# before the board is read, so a `ready` label whose event was skipped (AGENTS_PAUSED, the daily cap,
# a cancelled queued run, a failed read) is still released by the next scheduled pass.
pass() {
  revise_pass || echo "$(ts) revision pass failed" >&2
  sweep_ready_labels || echo "$(ts) ready-label sweep failed" >&2
  ready_pass
}

# Handles one `ready` label event (the doorbell, docs/workflow.md), then the caller runs one pass.
# The board stays the source of truth: the label only triggers a pass. A card that is not yet
# approved is set to Ready; one that is already moving or finished is never moved backwards. The
# label is removed after the card is set (never before), so a repeated or stale label is harmless and
# a missed event is caught by the sweep at the start of every pass.
handle_ready_label() {
  local n="$1" info state kind status rc
  info=$(gh api "repos/$REPO/issues/$n" --jq '[.state, (if .pull_request then "pr" else "issue" end)] | join(" ")') || {
    echo "$(ts) could not read #$n; leaving the label for the next pass" >&2
    return 1
  }
  state=${info%% *}; kind=${info##* }
  if [ "$state" != "open" ] || [ "$kind" = "pr" ]; then
    echo "$(ts) #$n is a $state $kind: removing the ready label and doing nothing else"
    gh issue edit "$n" --repo "$REPO" --remove-label ready || true
    return 10   # nothing else to do, not even a pass
  fi
  rc=0
  status=$(bash ./scripts/board.sh current "$n" 2>/dev/null) || rc=$?
  if [ "$rc" -ne 0 ] && [ "$rc" -ne 3 ]; then
    echo "$(ts) could not read the card for #$n; changing nothing" >&2
    return 1
  fi
  case "$rc:$status" in
    3:*|0:|0:Backlog|0:"Spec Review"|0:"Plan Review"|0:Blocked)
      echo "$(ts) #${n}: ${status:-no card} -> Ready"
      bash ./scripts/board.sh status "$n" Ready || { echo "$(ts) could not set #$n to Ready" >&2; return 1; }
      ;;
    0:Ready) echo "$(ts) #$n is already Ready" ;;
    *) echo "$(ts) #$n is in $status: not moving it, not dispatching it" ;;
  esac
  gh issue edit "$n" --repo "$REPO" --remove-label ready || true
}

# Releases every open issue that still carries the `ready` label (the doorbell's fallback).
sweep_ready_labels() {
  local n labelled
  labelled=$(gh issue list --repo "$REPO" --label ready --state open --json number --jq '.[].number') || return 1
  for n in $labelled; do
    handle_ready_label "$n" || true
  done
}

if [ "$MODE" = revise ]; then
  revise_one "$REV_KIND" "$REV_NUMBER" "$REV_SOURCE"
  exit $?
fi

if [ -n "$READY_LABEL_ISSUE" ]; then
  # A failed label event must not stop the pass: the hourly schedule would otherwise be the only retry.
  rc=0
  handle_ready_label "$READY_LABEL_ISSUE" || rc=$?
  # 10: a closed issue or a pull request; the label is gone and there is nothing else to do.
  if [ "$rc" -eq 10 ]; then exit 0; fi
  if [ "$rc" -ne 0 ]; then echo "$(ts) ready-label handling failed" >&2; fi
  pass
  exit 0
fi

pass
if [ "$MODE" = once ]; then exit 0; fi
while sleep "$INTERVAL"; do
  pass || echo "$(ts) pass failed, retrying next interval"
done