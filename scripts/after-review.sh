#!/usr/bin/env bash
# The deterministic merge gate (ADR 0013). Runs as the last step of review.yml, after review.sh.
# Reads the latest review that carries the reviewer marker on the PR's current head SHA. If it is an
# approval, it checks, in order: the base branch is main; scripts/classify-risk.sh prints `low` for the
# PR's files; the PR has no risk:high label; AGENTS_PAUSED is not true. All pass: it enables squash
# auto-merge. Any fails: it requests the owner's review and posts one merge-gate comment.
# If the latest reviewer review is CHANGES_REQUESTED on an issue-* PR, it dispatches a revision
# (revise.yml, source=reviewer) while fewer than REVISION_BUDGET reviewer rounds have been acknowledged;
# at the budget it labels the PR needs-human and asks the owner instead.
#
# Usage: scripts/after-review.sh <pr-number>
# Env:   REPO             owner/name (default: the current repository)
#        OWNER            who to ask for a review (default: the repository owner)
#        AGENTS_PAUSED    the kill switch; `true` blocks auto-merge and revision dispatch
#        REVISION_BUDGET  reviewer-requested revision rounds per PR (default 3)
# Needs: gh (the App token, so the merge's push to main triggers CI; it also needs Actions: write to
#        dispatch revise.yml, docs/adr/0014), jq. Works with bash 3.2.
set -euo pipefail

usage() { echo "usage: scripts/after-review.sh <pr-number>" >&2; exit 2; }
[ $# -eq 1 ] || usage
PR="$1"
case "$PR" in ''|*[!0-9]*) usage ;; esac

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="${REPO:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"
OWNER="${OWNER:-${REPO%%/*}}"
MARKER='<!-- agent:reviewer -->'

log() { echo "after-review: PR #$PR: $*"; }

# The reviewer asked for changes. On an issue-* PR the developer is dispatched to revise (through
# .github/workflows/revise.yml), up to REVISION_BUDGET rounds; every round leaves an
# `<!-- agent:ack source=reviewer -->` comment, which is what is counted (human /revise rounds do not
# count). At the budget the PR goes to a human instead. ADR PRs (arch-*) are revised only on human
# feedback, so nothing happens for them here.
ACK_MARKER='<!-- agent:ack source=reviewer -->'
request_revision() {
  local branch budget rounds labels
  branch=$(gh pr view "$PR" --repo "$REPO" --json headRefName -q .headRefName)
  case "$branch" in
    issue-*) ;;
    *) log "branch '$branch' is not issue-*; revisions of other PRs start only on human feedback"; return 0 ;;
  esac
  if [ "${AGENTS_PAUSED:-}" = true ]; then log "AGENTS_PAUSED is true; not dispatching a revision"; return 0; fi

  budget="${REVISION_BUDGET:-3}"   # an unset repository variable arrives as an empty string
  case "$budget" in ''|*[!0-9]*) budget=3 ;; esac
  rounds=$(gh api "repos/$REPO/issues/$PR/comments?per_page=100" --paginate \
    | jq -s --arg m "$ACK_MARKER" '[.[] | (if type == "array" then .[] else . end) | select((.body // "") | contains($m))] | length')

  if [ "$rounds" -lt "$budget" ]; then
    log "revision round $((rounds + 1)) of $budget: dispatching the developer"
    gh workflow run revise.yml --repo "$REPO" -f kind=pr -f number="$PR" -f source=reviewer
    return 0
  fi

  labels=$(gh pr view "$PR" --repo "$REPO" --json labels -q '.labels[].name')
  if printf '%s\n' "$labels" | grep -qx 'needs-human'; then
    log "revision budget ($budget) is spent and the PR is already with a human; nothing to do"
    return 0
  fi
  log "revision budget ($budget) is spent after $rounds rounds; handing the PR to $OWNER"
  gh label create needs-human --repo "$REPO" --color D93F0B \
    --description "An agent run stopped and a human must decide" >/dev/null 2>&1 || true
  gh pr edit "$PR" --repo "$REPO" --add-label needs-human >/dev/null
  gh pr edit "$PR" --repo "$REPO" --add-reviewer "$OWNER" >/dev/null 2>&1 \
    || log "could not request a review from $OWNER (they may be the PR author)"
  gh pr comment "$PR" --repo "$REPO" --body "The revision budget is spent: the developer has already revised this PR $rounds times in response to the reviewer (budget $budget, set by REVISION_BUDGET). It is handed to a human: the owner's review is requested. Comment /revise to run another revision; that does not count against the budget.

<!-- agent:merge-gate -->" >/dev/null
}

head_sha=$(gh pr view "$PR" --repo "$REPO" --json headRefOid -q .headRefOid)

# The latest marked review on the current head. Reviews on an older head, or without the marker
# (for example from another bot), do not count. The review body is data, only searched for the marker.
state=$(gh api "repos/$REPO/pulls/$PR/reviews" --paginate \
  | jq -rs --arg sha "$head_sha" --arg marker "$MARKER" \
    '[.[] | (if type == "array" then .[] else . end)
      | select(.commit_id == $sha and ((.body // "") | contains($marker)))]
     | sort_by(.submitted_at) | (last // {}) | .state // ""')

if [ "$state" = "CHANGES_REQUESTED" ]; then
  log "reviewer requested changes on head ${head_sha:0:7}"
  request_revision
  exit 0
fi

if [ "$state" != "APPROVED" ]; then
  log "no reviewer approval on head ${head_sha:0:7} (latest marked review: ${state:-none}); nothing to do"
  exit 0
fi
log "reviewer approved head ${head_sha:0:7}; running the merge-gate checks"

fail() { # check-name detail: ask the owner and say which check failed; never enable auto-merge
  log "merge gate FAILED: $1: $2; auto-merge not enabled, requesting review from $OWNER"
  gh pr edit "$PR" --repo "$REPO" --add-reviewer "$OWNER" >/dev/null 2>&1 \
    || log "could not request a review from $OWNER (they may be the PR author)"
  gh pr comment "$PR" --repo "$REPO" --body "Auto-merge is not enabled: the merge-gate check \"$1\" failed ($2). The owner's review is requested.

<!-- agent:merge-gate -->" >/dev/null
  exit 0
}

# 1. base branch
base=$(gh pr view "$PR" --repo "$REPO" --json baseRefName -q .baseRefName)
[ "$base" = main ] || fail "base branch" "the base is \`$base\`, not \`main\`"
log "check 1 ok: base branch is main"

# 2. risk classifier on the PR's current file list (renames include the previous path)
if ! files=$(gh api "repos/$REPO/pulls/$PR/files" --paginate \
    --jq '.[] | .filename, (.previous_filename // empty)'); then
  fail "risk classifier" "could not list the PR's files"
fi
if ! risk=$(printf '%s\n' "$files" | bash "$HERE/classify-risk.sh") || [ -z "$risk" ]; then
  fail "risk classifier" "scripts/classify-risk.sh failed, so the PR is treated as high risk"
fi
[ "$risk" = low ] || fail "risk classifier" "the changed files classify as \`$risk\` risk"
log "check 2 ok: classifier says low"

# 3. label
labels=$(gh pr view "$PR" --repo "$REPO" --json labels -q '.labels[].name')
if printf '%s\n' "$labels" | grep -qx 'risk:high'; then
  fail "risk:high label" "the PR has the risk:high label"
fi
log "check 3 ok: no risk:high label"

# 4. kill switch
[ "${AGENTS_PAUSED:-}" != true ] || fail "AGENTS_PAUSED" "agents are paused"
log "check 4 ok: agents are not paused"

log "all checks passed; enabling squash auto-merge"
gh pr merge "$PR" --repo "$REPO" --auto --squash --delete-branch
