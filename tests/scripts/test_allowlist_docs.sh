#!/usr/bin/env bash
# Drift guard: every gh / git / make / uv / bash / scripts/*.sh command that an agent definition tells an agent to run
# must be covered by the coordinator's headless allowlist. `claude -p` cannot ask for approval, so
# an uncovered command is silently denied (this is how editing the Technical plan comment broke:
# the allowlist only matched `gh api` with the path first).
. "$(dirname "$0")/lib.sh"
cd "$ROOT" || exit 1

REPO_NAME="cooper3330/weekly-meal-planner"
STATUS_FIELDS='{"fields":[{"id":"PVTSSF_status","name":"Status","type":"ProjectV2SingleSelectField","options":[{"id":"1","name":"Backlog"},{"id":"2","name":"Spec Review"},{"id":"3","name":"Ready"},{"id":"4","name":"Architecture"},{"id":"5","name":"Plan Review"},{"id":"6","name":"Implementation"},{"id":"7","name":"In Review"},{"id":"8","name":"Done"},{"id":"9","name":"Blocked"}]}]}'

# The real allowlist: run one dispatch against the stubs and read what was passed to claude.
new_sandbox
echo '{"items":[{"id":"I1","status":"Ready","content":{"number":5,"title":"x"},"labels":["type:architecture"]}]}' > "$T/items.json"
echo "$STATUS_FIELDS" > "$T/fields.json"
PROJECT_NUMBER=1 bash scripts/coordinator.sh --once > /dev/null 2>&1
rules=$(sed -n '/^--allowedTools$/,$p' "$T/claude.args" | tail -n +2)

# allowed COMMAND: succeeds if a Bash(...) rule covers it. `Bash(prefix *)` covers any command that
# starts with "prefix "; `Bash(exact)` covers only that command. Flags before the path do not match
# a rule that expects the path first, which is what this guard exists to catch.
allowed() {
  local cmd="$1 " rule p
  while IFS= read -r rule; do
    case "$rule" in
      "Bash("*"*)") p="${rule#Bash(}"; p="${p%\*)}"; [[ "$cmd" == "$p"* ]] && return 0 ;;
      "Bash("*")")  p="${rule#Bash(}"; p="${p%)}";   [ "$1" = "$p" ] && return 0 ;;
    esac
  done <<<"$rules"
  return 1
}

echo "allowlist: the harness is sane"
[ -n "$rules" ] && ok "found the allowlist passed to claude" || bad "found the allowlist passed to claude"
if allowed "gh issue view 5"; then ok "a covered command is allowed"; else bad "a covered command is allowed"; fi
if allowed "gh api -X DELETE repos/$REPO_NAME/issues/1"; then bad "an uncovered command is rejected"; else ok "an uncovered command is rejected"; fi

echo "allowlist: every command in the agent definitions is covered"
checked=0
while IFS= read -r cmd; do
  [ -n "$cmd" ] || continue
  cmd="${cmd//repos\/R\//repos/$REPO_NAME/}"
  checked=$((checked + 1))
  if allowed "$cmd"; then ok "allowed: $cmd"; else bad "NOT allowed: $cmd"; fi
done < <(grep -ohE '`(gh|git|make|uv|bash|scripts/[a-z-]+\.sh) [^`]*`' .claude/agents/*.md | tr -d '`' | sort -u)
[ "$checked" -gt 5 ] && ok "checked $checked documented commands" || bad "checked enough documented commands (got $checked)"

finish
