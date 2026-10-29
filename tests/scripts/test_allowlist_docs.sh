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
done < <(grep -ohE '`(gh|git|make|uv|scripts/board\.sh) [^`]*`' $(ls .claude/agents/*.md | grep -v 'reviewer\.md') | tr -d '`' | sort -u)
# The board is changed only through the wrapper: agents are told to use it, with a covered example.
if grep -qF '`scripts/board.sh status 12 Ready`' .claude/agents/architect.md; then ok "architect.md shows scripts/board.sh status 12 Ready"; else bad "architect.md shows scripts/board.sh status 12 Ready"; fi
if allowed "scripts/board.sh status 12 Ready"; then ok "scripts/board.sh status 12 Ready is allowed"; else bad "scripts/board.sh status 12 Ready is allowed"; fi
if allowed "gh project item-edit --id X"; then bad "raw gh project item-edit is no longer allowed"; else ok "raw gh project item-edit is no longer allowed"; fi
if grep -ohE '`gh project item-(edit|add)[^`]*`' .claude/agents/*.md | grep -q .; then bad "agent definitions do not tell agents to run raw gh project item-edit/item-add"; else ok "agent definitions do not tell agents to run raw gh project item-edit/item-add"; fi
# The ready-label doorbell (#55): the architect's two commands must be covered by the allowlist.
for c in "gh issue edit 12 --add-label ready" "scripts/board.sh status 12 Ready"; do
  if allowed "$c"; then ok "allowed: $c"; else bad "NOT allowed: $c"; fi
done
if grep -qF '`gh issue edit <issue number> --add-label ready`' .claude/agents/architect.md; then ok "architect.md names the add-label command (checked above as a documented command)"; else bad "architect.md names the add-label command"; fi
[ "$checked" -gt 5 ] && ok "checked $checked documented commands" || bad "checked enough documented commands (got $checked)"

# --- the reviewer has its own, narrower allowlist (REVIEWER_ALLOWED_TOOLS in scripts/review.sh) ---
echo "allowlist: every command in reviewer.md is covered by the reviewer allowlist"
new_sandbox
printf 'Closes #36\n' > "$T/pr_body.txt"
REPO=$REPO_NAME bash scripts/review.sh 5 > /dev/null 2>&1
rev_rules=$(sed -n '/^--allowedTools$/,$p' "$T/claude.args" | tail -n +2)
[ -n "$rev_rules" ] && ok "found the reviewer allowlist passed to claude" || bad "found the reviewer allowlist passed to claude"

# rev_allowed COMMAND: succeeds if a Bash(...) rule, read as a glob over the whole command, covers it.
rev_allowed() {
  local rule p
  while IFS= read -r rule; do
    case "$rule" in
      "Bash("*")") p="${rule#Bash(}"; p="${p%)}"; [[ "$1" == $p ]] && return 0 ;;
    esac
  done <<<"$rev_rules"
  return 1
}
if rev_allowed "gh pr view 5"; then ok "a covered reviewer command is allowed"; else bad "a covered reviewer command is allowed"; fi
if rev_allowed "gh pr merge 5 --squash"; then bad "an uncovered reviewer command is rejected"; else ok "an uncovered reviewer command is rejected"; fi
if rev_allowed "gh pr edit 5 --add-label risk:low"; then bad "a different label is rejected"; else ok "a different label is rejected"; fi
# The reviewer may read and post its one review, nothing else. `gh api` takes -f/-F/--input (which make
# it a POST) and -X/--method after the path, and a prefix glob over the path cannot tell reads from
# writes, so the reviewer has no gh api rule: it reads issues and PRs with gh issue view and gh pr view.
echo "allowlist: the reviewer cannot write through gh api"
for w in "gh api repos/$REPO_NAME/issues/5/comments -f body=x" \
         "gh api repos/$REPO_NAME/pulls/5/reviews -X POST" \
         "gh api repos/$REPO_NAME/issues/5/comments --method DELETE" \
         "gh api repos/$REPO_NAME/pulls/5/comments --input f.json" \
         "gh api repos/$REPO_NAME/issues/5 -X POST -f body=/comments" \
         "gh api repos/$REPO_NAME/issues/comments/9 -X PATCH -F body=@x.md"; do
  if rev_allowed "$w"; then bad "rejected: $w"; else ok "rejected: $w"; fi
done
for r in "gh issue view 5 --comments" "gh pr view 5 --comments" "gh pr diff 5" "gh pr checks 5"; do
  if rev_allowed "$r"; then ok "still allowed: $r"; else bad "still allowed: $r"; fi
done

rchecked=0
while IFS= read -r cmd; do
  [ -n "$cmd" ] || continue
  cmd="${cmd//repos\/R\//repos/$REPO_NAME/}"
  cmd="${cmd//N/5}"
  rchecked=$((rchecked + 1))
  if rev_allowed "$cmd"; then ok "reviewer allowed: $cmd"; else bad "reviewer NOT allowed: $cmd"; fi
done < <(grep -ohE '`(gh|git|make|uv|scripts/board\.sh) [^`]*`' .claude/agents/reviewer.md | tr -d '`' | sort -u)
[ "$rchecked" -gt 3 ] && ok "checked $rchecked reviewer commands" || bad "checked enough reviewer commands (got $rchecked)"

finish
