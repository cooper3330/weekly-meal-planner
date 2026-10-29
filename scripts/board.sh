#!/usr/bin/env bash
# The only path agents and the coordinator use to change the GitHub Project board.
#
# A GitHub App token cannot write to a user-owned Projects v2 board, so board calls use a
# classic PAT with project + read:org scopes (PROJECT_TOKEN, see docs/adr/0014). Everything else the agents do
# runs as the App. Keeping the board calls in this one script keeps that token out of every
# other command. Locally, without PROJECT_TOKEN, it uses the normal `gh` login.
#
# Usage: scripts/board.sh add <issue-url>              print the board item ID (adds it if needed)
#        scripts/board.sh status <issue-number> <Status name>
#                                                       set the Status, adding the card if needed
#        scripts/board.sh current <issue-number>        print the card's Status name; no output and
#                                                       exit 3 when the issue has no card (1 on error)
#        scripts/board.sh check                         verify the board has the Status options
# Env:   PROJECT_NUMBER  (required) the GitHub Project number
#        OWNER           project owner (default: the repository owner)
#        REPO            owner/name (default: the current repository)
#        PROJECT_TOKEN   token for `gh project` calls (default: the ambient gh login)
# Requires: gh (project scope), jq. Works with macOS (BSD) tools and bash 3.2.
set -euo pipefail

: "${PROJECT_NUMBER:?set PROJECT_NUMBER}"
for cmd in gh jq; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "missing required command: $cmd" >&2; exit 1; }
done

# The board's built-in single-select Status field must have these options (see docs/workflow.md).
STATUSES=("Backlog" "Spec Review" "Ready" "Architecture" "Plan Review" "Implementation" "In Review" "Done" "Blocked")

ts() { date '+%Y-%m-%dT%H:%M:%S%z'; }
die() { echo "$(ts) board.sh: $*" >&2; exit 1; }

# Every `gh project` call goes through here, so PROJECT_TOKEN reaches nothing else.
gp() {
  if [ -n "${PROJECT_TOKEN:-}" ]; then
    GH_TOKEN="$PROJECT_TOKEN" gh project "$@"
  else
    gh project "$@"
  fi
}

# Resolves REPO and OWNER (the owner defaults to the repository owner) only when needed.
resolve_repo() {
  REPO="${REPO:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"
  OWNER="${OWNER:-${REPO%%/*}}"
}

# Resolve the IDs `gh project item-edit` needs (project node ID, Status field ID, and the ID of
# each Status option) and check that the Status field has every option. Prints one compact JSON
# object.
board_ids() {
  local project_id fields status missing name
  project_id=$(gp view "$PROJECT_NUMBER" --owner "$OWNER" --format json -q .id)
  fields=$(gp field-list "$PROJECT_NUMBER" --owner "$OWNER" --format json)
  status=$(jq -c '[.fields[] | select(.name=="Status")] | first // empty' <<<"$fields")
  if [ -z "$status" ] || [ -z "$project_id" ]; then
    die "project #$PROJECT_NUMBER has no 'Status' field (or could not be read); run scripts/setup-project.sh"
  fi
  missing=""
  for name in "${STATUSES[@]}"; do
    if ! jq -e --arg n "$name" '[.options[].name] | index($n)' <<<"$status" >/dev/null; then
      missing="$missing${missing:+, }$name"
    fi
  done
  if [ -n "$missing" ]; then
    die "the Status field is missing options: $missing; set them in the project UI (see docs/workflow.md)"
  fi
  jq -c --arg project "$project_id" \
    '{project_id: $project, status_field_id: .id, status_options: ([.options[] | {(.name): .id}] | add)}' <<<"$status"
}

# Prints the item ID of the card for an issue URL, adding the card when it is not on the board.
# Adding an issue that is already there returns the existing item, so this is idempotent.
item_id_for_url() {
  local url="$1" id
  id=$(gp item-list "$PROJECT_NUMBER" --owner "$OWNER" --limit 200 --format json \
    | jq -r --arg u "$url" '[.items[] | select(.content.url == $u) | .id] | first // empty')
  if [ -z "$id" ]; then
    id=$(gp item-add "$PROJECT_NUMBER" --owner "$OWNER" --url "$url" --format json -q .id)
  fi
  [ -n "$id" ] || die "could not find or add $url on project #$PROJECT_NUMBER"
  echo "$id"
}

cmd="${1:-}"
case "$cmd" in
  add)
    [ $# -eq 2 ] || die "usage: board.sh add <issue-url>"
    resolve_repo
    item_id_for_url "$2"
    ;;
  status)
    [ $# -eq 3 ] || die "usage: board.sh status <issue-number> <Status name>"
    number="$2"; name="$3"
    case "$number" in ''|*[!0-9]*) die "issue number must be numeric, got '$number'" ;; esac
    known=""
    for s in "${STATUSES[@]}"; do [ "$s" = "$name" ] && known=1; done
    [ -n "$known" ] || die "unknown Status '$name' (expected one of: $(IFS=,; echo "${STATUSES[*]}" | sed 's/,/, /g'))"
    resolve_repo
    ids=$(board_ids)
    option_id=$(jq -r --arg n "$name" '.status_options[$n]' <<<"$ids")
    item_id=$(item_id_for_url "https://github.com/$REPO/issues/$number")
    gp item-edit --id "$item_id" --project-id "$(jq -r .project_id <<<"$ids")" \
      --field-id "$(jq -r .status_field_id <<<"$ids")" --single-select-option-id "$option_id" >/dev/null
    echo "$(ts) #$number -> $name"
    ;;
  current)
    [ $# -eq 2 ] || die "usage: board.sh current <issue-number>"
    number="$2"
    case "$number" in ''|*[!0-9]*) die "issue number must be numeric, got '$number'" ;; esac
    resolve_repo
    items=$(gp item-list "$PROJECT_NUMBER" --owner "$OWNER" --limit 200 --format json) \
      || die "could not read project #$PROJECT_NUMBER"
    # A card in the list prints its Status name (empty if it has none). Exit 3 means "no card", so a
    # caller can tell that apart from a failed read (exit 1).
    found=$(jq -r --argjson n "$number" --arg repo "$REPO" \
      '[.items[] | select(.content.number == $n and ((.content.repository // $repo) == $repo))] | first // empty | "card\t\(.status // "")"' <<<"$items")
    [ -n "$found" ] || exit 3
    printf '%s\n' "${found#card	}"
    ;;
  check)
    [ $# -eq 1 ] || die "usage: board.sh check"
    resolve_repo
    board_ids >/dev/null
    ;;
  *)
    die "usage: board.sh add <issue-url> | status <issue-number> <Status name> | current <issue-number> | check"
    ;;
esac
