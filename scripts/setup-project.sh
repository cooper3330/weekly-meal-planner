#!/usr/bin/env bash
# One-time setup: labels + GitHub Project with a "Stage" field.
# Requires: gh authenticated with the `project` scope (gh auth refresh -s project).
set -euo pipefail
OWNER="${OWNER:-@me}"
REPO="${REPO:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"
TITLE="Weekly Meal Planner"

for l in "type:feature:1d76db" "type:task:0e8a16" "type:architecture:5319e7" "type:bug:d73a4a"; do
  IFS=: read -r a b c <<<"$l"
  gh label create "$a:$b" --color "$c" --repo "$REPO" --force
done

num=$(gh project create --owner "$OWNER" --title "$TITLE" --format json -q .number)
gh project link "$num" --owner "$OWNER" --repo "$REPO"
gh project field-create "$num" --owner "$OWNER" --name "Stage" --data-type SINGLE_SELECT \
  --single-select-options "Backlog,Spec Review,Ready,Architecture,Plan Review,Implementation,In Review,Done,Blocked"

echo "Project #$num created. Add to your environment:  export PROJECT_NUMBER=$num"
echo "Optional: in the project UI, add a board view grouped by Stage and enable the"
echo "'Auto-add to project' workflow for $REPO."
