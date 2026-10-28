#!/usr/bin/env bash
# Setup for labels, milestones, and a GitHub Project that uses the built-in Status field.
# Safe to re-run: it reuses whatever already exists and only creates what is missing.
#
# Requires: gh authenticated with repo access and the project scope (gh auth refresh -s project), and jq.
# Env:   REPO   owner/name (default: the current repository)
#        OWNER  project owner (default: the repository owner; `gh project link` requires
#               --owner to match the repo owner, so '@me' is not used)
set -euo pipefail

for cmd in gh jq; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "missing required command: $cmd" >&2; exit 1; }
done

REPO="${REPO:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"
OWNER="${OWNER:-${REPO%%/*}}"
TITLE="Weekly Meal Planner"
STATUSES=("Backlog" "Spec Review" "Ready" "Architecture" "Plan Review" "Implementation" "In Review" "Done" "Blocked")

# Labels (--force updates the color of one that already exists)
for l in "type:feature:1d76db" "type:task:0e8a16" "type:architecture:5319e7" "type:bug:d73a4a" "risk:high:b60205" "needs-human:fbca04"; do
  IFS=: read -r a b c <<<"$l"
  gh label create "$a:$b" --color "$c" --repo "$REPO" --force
done

# Milestones act as epics (see docs/workflow.md). Create only the missing ones.
existing_milestones=$(gh api "repos/$REPO/milestones?state=all&per_page=100" --jq '.[].title')
for m in "M1 Foundation" "M2 Weekly planning loop" "M3 Shopping and cooking" "M4 Learning"; do
  if grep -Fxq -- "$m" <<<"$existing_milestones"; then
    echo "milestone exists: $m"
  else
    gh api "repos/$REPO/milestones" -f title="$m" >/dev/null
    echo "milestone created: $m"
  fi
done

# Project: reuse one with this title if it exists.
num=$(gh project list --owner "$OWNER" --limit 100 --format json \
  -q "[.projects[] | select(.title==\"$TITLE\") | .number] | first // empty")
if [ -z "$num" ]; then
  num=$(gh project create --owner "$OWNER" --title "$TITLE" --format json -q .number)
  echo "Created project #$num"
else
  echo "Using existing project #$num"
fi

# Linking is a convenience, so a failure (for example already linked) is not fatal.
if ! gh project link "$num" --owner "$OWNER" --repo "$REPO"; then
  echo "warning: could not link project #$num to $REPO (it may already be linked); continuing" >&2
fi

# Status field: GitHub creates it with Todo / In Progress / Done, and the CLI cannot change a
# field's options, so this only checks them and says what to set in the project UI.
fields=$(gh project field-list "$num" --owner "$OWNER" --format json)
have=$(jq -r '[.fields[] | select(.name=="Status")] | first | .options[]?.name' <<<"$fields")
missing=""
for name in "${STATUSES[@]}"; do
  if ! grep -Fxq -- "$name" <<<"$have"; then missing="$missing${missing:+, }$name"; fi
done
if [ -z "$missing" ]; then
  echo "Status field has all ${#STATUSES[@]} options"
else
  echo "WARNING: the project's Status field is missing these options: $missing" >&2
  echo "In the project UI (Project > Settings > Fields > Status), set the options in this order:" >&2
  (IFS=,; echo "  ${STATUSES[*]}" | sed 's/,/, /g') >&2
fi

echo "Project #$num is ready. Add to your environment:  export PROJECT_NUMBER=$num"
echo "Next, in the project UI: add a board view grouped by Status, then under Workflows enable"
echo "'Auto-add to project' for $REPO plus the built-in 'Item added to project' (Backlog),"
echo "'Item closed' (Done) and 'Pull request merged' (Done). See docs/workflow.md."
