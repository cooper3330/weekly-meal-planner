#!/usr/bin/env bash
# Coordinator loop: polls the GitHub Project for items in Stage=Ready and
# hands each to the coordinator agent (which dispatches the architect/developer).
# Usage: PROJECT_NUMBER=1 ./scripts/coordinator.sh [--once]
# Requires: gh (project scope), jq, claude CLI.
set -euo pipefail
OWNER="${OWNER:-@me}"
: "${PROJECT_NUMBER:?set PROJECT_NUMBER}"
INTERVAL="${INTERVAL:-300}"
MAX_PARALLEL="${MAX_PARALLEL:-1}"
cd "$(dirname "$0")/.."

pass() {
  ready=$(gh project item-list "$PROJECT_NUMBER" --owner "$OWNER" --limit 200 --format json \
    | jq -c '[.items[] | select(.stage=="Ready") | {number: .content.number, title: .content.title, labels: .labels}]')
  [ "$(jq length <<<"$ready")" -eq 0 ] && { echo "$(date -Is) nothing Ready"; return; }
  echo "$(date -Is) Ready items: $ready"
  claude -p --agent coordinator --permission-mode acceptEdits \
    "Project $PROJECT_NUMBER (owner $OWNER), MAX_PARALLEL=$MAX_PARALLEL. Items in Ready: $ready. \
Run one coordination pass per docs/workflow.md and report what you dispatched."
}

pass
[ "${1:-}" = "--once" ] && exit 0
while sleep "$INTERVAL"; do pass || echo "pass failed, retrying next interval"; done
