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
# Requires: gh (project scope), jq, claude CLI. Works with macOS (BSD) tools and bash 3.2.
set -euo pipefail

: "${PROJECT_NUMBER:?set PROJECT_NUMBER}"
for cmd in gh jq claude; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "missing required command: $cmd" >&2; exit 1; }
done
cd "$(dirname "$0")/.."

REPO="${REPO:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"
OWNER="${OWNER:-${REPO%%/*}}"
INTERVAL="${INTERVAL:-300}"
MAX_PARALLEL="${MAX_PARALLEL:-1}"

# `date -I` is GNU-only; this format works on macOS and Linux.
ts() { date '+%Y-%m-%dT%H:%M:%S%z'; }

# `claude -p` is headless: nobody can approve a prompt, so anything not listed here is
# denied. These are the commands the coordinator and the agents it dispatches need.
# Keep it narrow: add more with EXTRA_ALLOWED_TOOLS only when a run stalls on one.
ALLOWED_TOOLS=(
  "Bash(gh issue view *)" "Bash(gh issue comment *)" "Bash(gh issue create *)" "Bash(gh issue edit *)"
  "Bash(gh project item-list *)" "Bash(gh project item-edit *)" "Bash(gh project field-list *)"
  "Bash(gh project view *)" "Bash(gh project list *)"
  "Bash(gh pr create *)" "Bash(gh pr view *)" "Bash(gh pr list *)"
  "Bash(gh api repos/$REPO/*)"
  "Bash(git checkout *)" "Bash(git add *)" "Bash(git commit *)" "Bash(git push *)"
  "Bash(git status *)" "Bash(git diff *)" "Bash(git fetch *)"
)
if [ -n "${EXTRA_ALLOWED_TOOLS:-}" ]; then
  IFS=, read -r -a extra <<<"$EXTRA_ALLOWED_TOOLS"
  ALLOWED_TOOLS+=("${extra[@]}")
fi

# The board's built-in single-select Status field must have these options (see docs/workflow.md).
STATUSES=("Backlog" "Spec Review" "Ready" "Architecture" "Plan Review" "Implementation" "In Review" "Done" "Blocked")

# Resolve the board IDs `gh project item-edit` needs (project node ID, Status field ID, and
# the ID of each Status option). Doing it here, not in the agent, keeps the headless run from
# having to discover them and from guessing. Prints one compact JSON object.
board_ids() {
  local project_id fields status missing name
  project_id=$(gh project view "$PROJECT_NUMBER" --owner "$OWNER" --format json -q .id)
  fields=$(gh project field-list "$PROJECT_NUMBER" --owner "$OWNER" --format json)
  status=$(jq -c '[.fields[] | select(.name=="Status")] | first // empty' <<<"$fields")
  if [ -z "$status" ] || [ -z "$project_id" ]; then
    echo "$(ts) project #$PROJECT_NUMBER has no 'Status' field (or could not be read); run scripts/setup-project.sh" >&2
    return 1
  fi
  missing=""
  for name in "${STATUSES[@]}"; do
    if ! jq -e --arg n "$name" '[.options[].name] | index($n)' <<<"$status" >/dev/null; then
      missing="$missing${missing:+, }$name"
    fi
  done
  if [ -n "$missing" ]; then
    echo "$(ts) the Status field is missing options: $missing; set them in the project UI (see docs/workflow.md)" >&2
    return 1
  fi
  jq -c --arg project "$project_id" \
    '{project_id: $project, status_field_id: .id, status_options: ([.options[] | {(.name): .id}] | add)}' <<<"$status"
}

pass() {
  local ready ids
  ready=$(gh project item-list "$PROJECT_NUMBER" --owner "$OWNER" --limit 200 --format json \
    | jq -c '[.items[] | select(.status=="Ready" and .content.number != null)
              | {item_id: .id, number: .content.number, title: .content.title, labels: (.labels // [])}]')
  if [ "$(jq length <<<"$ready")" -eq 0 ]; then
    echo "$(ts) nothing Ready"
    return 0
  fi
  ids=$(board_ids) || return 1
  echo "$(ts) Ready items: $ready"
  # The prompt goes first: --allowedTools takes a list and would swallow a trailing prompt.
  claude -p "Project $PROJECT_NUMBER (owner $OWNER), MAX_PARALLEL=$MAX_PARALLEL. Board IDs: $ids. Items in Ready (item_id is the board item ID): $ready. Run one coordination pass per docs/workflow.md and report what you dispatched. To change an item's Status, run: gh project item-edit --id <item_id> --project-id <project_id> --field-id <status_field_id> --single-select-option-id <id from status_options>. Use only these IDs; never guess one." \
    --agent coordinator --permission-mode acceptEdits \
    --allowedTools "${ALLOWED_TOOLS[@]}"
}

pass
if [ "${1:-}" = "--once" ]; then exit 0; fi
while sleep "$INTERVAL"; do
  pass || echo "$(ts) pass failed, retrying next interval"
done
