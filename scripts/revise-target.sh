#!/usr/bin/env bash
# Maps a GitHub event to the item that .github/workflows/revise.yml should revise, and prints it as
# `kind=<pr|issue>`, `number=<N>` and `source=<human|reviewer>` lines (the format of $GITHUB_OUTPUT).
# Prints nothing, and exits 0, when the event maps to no item.
#
# The rules (the same scoping as scripts/pending-revisions.sh):
#   - a comment on a PR: kind=pr, the PR, for issue-* and arch-* branches of this repository only;
#   - a comment on a task or bug issue: kind=pr, the open PR on the branch issue-<N>-* (the task's
#     linked PR); none open means nothing;
#   - a comment on a feature or architecture issue: kind=issue, the issue (plan feedback);
#   - a review or a review comment: kind=pr, the PR (issue-* and arch-* branches of this repository);
#   - workflow_dispatch: the kind, number and source inputs, passed through after validation.
# Whether the feedback is from a trusted author and is still unanswered is NOT decided here: the
# workflow's job-level `if:` filters the author, and `coordinator.sh --revise` checks that the item is
# pending. This script only turns an event into a number, and only reads numbers and labels from the
# payload, never free text.
#
# Env: GITHUB_EVENT_NAME, GITHUB_EVENT_PATH (set by Actions); REPO owner/name (default: the current repo)
# Needs: gh (read access), jq. Works with bash 3.2.
set -euo pipefail

: "${GITHUB_EVENT_NAME:?set GITHUB_EVENT_NAME}"
: "${GITHUB_EVENT_PATH:?set GITHUB_EVENT_PATH}"
REPO="${REPO:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"

event() { jq -r "$1" "$GITHUB_EVENT_PATH"; }
numeric() { case "$1" in ''|*[!0-9]*) return 1 ;; esac; }
emit() { printf 'kind=%s\nnumber=%s\nsource=%s\n' "$1" "$2" "${3:-human}"; }
agent_branch() { case "$1" in issue-*|arch-*) return 0 ;; esac; return 1; }

case "$GITHUB_EVENT_NAME" in
  workflow_dispatch)
    kind=$(event '.inputs.kind // ""'); number=$(event '.inputs.number // ""'); source=$(event '.inputs.source // "human"')
    case "$kind" in pr|issue) ;; *) echo "revise-target: kind must be pr or issue, got '$kind'" >&2; exit 2 ;; esac
    numeric "$number" || { echo "revise-target: number must be numeric, got '$number'" >&2; exit 2; }
    case "$source" in human|reviewer) ;; *) echo "revise-target: source must be human or reviewer, got '$source'" >&2; exit 2 ;; esac
    if [ "$source" = reviewer ] && [ "$kind" != pr ]; then echo "revise-target: source reviewer needs kind pr" >&2; exit 2; fi
    emit "$kind" "$number" "$source"
    ;;

  pull_request_review|pull_request_review_comment)
    number=$(event '.pull_request.number // ""'); ref=$(event '.pull_request.head.ref // ""')
    same=$(event '(.pull_request.head.repo.full_name // "") == "'"$REPO"'"')
    numeric "$number" || exit 0
    if [ "$same" = true ] && agent_branch "$ref"; then emit pr "$number"; fi
    ;;

  issue_comment)
    number=$(event '.issue.number // ""')
    numeric "$number" || exit 0
    if [ "$(event '(.issue.pull_request // null) != null')" = true ]; then
      # The payload has no branch for a comment on a PR; ask.
      pr=$(gh pr view "$number" --repo "$REPO" --json headRefName,isCrossRepository)
      if [ "$(jq -r '.isCrossRepository // false' <<<"$pr")" = false ] && agent_branch "$(jq -r '.headRefName // ""' <<<"$pr")"; then
        emit pr "$number"
      fi
      exit 0
    fi
    labels=$(event '[.issue.labels[]?.name] | join(" ")')
    case " $labels " in
      *" type:task "*|*" type:bug "*)
        # The task's PR is the open one on issue-<N>-*. Several would be unusual; take the lowest number.
        pr_number=$(gh pr list --repo "$REPO" --state open --limit 100 --json number,headRefName,isCrossRepository \
          | jq -r --arg p "issue-$number-" '[.[] | select((.isCrossRepository // false | not) and (.headRefName | startswith($p))) | .number] | sort | first // empty')
        if [ -n "$pr_number" ]; then emit pr "$pr_number"; fi
        ;;
      *" type:feature "*|*" type:architecture "*)
        emit issue "$number"
        ;;
    esac
    ;;

  *) echo "revise-target: unsupported event '$GITHUB_EVENT_NAME'" >&2; exit 2 ;;
esac
