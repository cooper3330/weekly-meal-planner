#!/usr/bin/env bash
# Guards against drift: the nine board statuses must appear in the docs and in both scripts,
# and nothing in the repo may still refer to the retired custom "Stage" field.
. "$(dirname "$0")/lib.sh"
cd "$ROOT" || exit 1

STATUSES=("Backlog" "Spec Review" "Ready" "Architecture" "Plan Review" "Implementation" "In Review" "Done" "Blocked")

echo "statuses: every status is defined in the docs and both scripts"
for s in "${STATUSES[@]}"; do
  for f in docs/workflow.md scripts/board.sh scripts/setup-project.sh; do
    if grep -Fq -- "$s" "$f"; then ok "$f mentions $s"; else bad "$f mentions $s"; fi
  done
done

echo "statuses: no remaining references to the retired Stage field"
hits=$(grep -rnE '\b[Ss]tages?\b' CLAUDE.md README.md docs scripts .claude .github 2>/dev/null || true)
if [ -z "$hits" ]; then ok "no 'Stage' references in docs, scripts, agents, or templates"; else bad "found Stage references:"; echo "$hits"; fi

finish
