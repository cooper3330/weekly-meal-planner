#!/usr/bin/env bash
# Tests for how scripts/coordinator.sh dispatches Ready work (offline, stubbed gh/git/claude):
# a fresh worktree per pass, salvage of unfinished work, protected-path tasks held for an
# interactive session, and the commands agents were denied reported to the owner.
. "$(dirname "$0")/lib.sh"
SCRIPT="$ROOT/scripts/coordinator.sh"

STATUS_FIELDS='{"fields":[{"id":"PVTSSF_status","name":"Status","type":"ProjectV2SingleSelectField","options":[{"id":"o_back","name":"Backlog"},{"id":"o_spec","name":"Spec Review"},{"id":"o_ready","name":"Ready"},{"id":"o_arch","name":"Architecture"},{"id":"o_plan","name":"Plan Review"},{"id":"o_impl","name":"Implementation"},{"id":"o_review","name":"In Review"},{"id":"o_done","name":"Done"},{"id":"o_blocked","name":"Blocked"}]}],"totalCount":1}'
ITEMS32='{"items":[{"id":"I32","status":"Ready","content":{"number":32,"title":"CI workflow"},"labels":["type:task"]}]}'

# sandbox ITEMS_JSON: a sandbox with the board set up and these Ready items.
sandbox() { new_sandbox; echo "$1" > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"; }
# issue_body N TEXT: the body `gh issue view N` returns.
issue_body() { jq -n --arg b "$2" '{body: $b}' > "$T/issue_$1.json"; }
run() { PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1; echo $? > "$T/rc"; }
nonzero() { if [ "$(cat "$T/rc")" -ne 0 ]; then ok "$1"; else bad "$1"; fi; }

SAFE=$'### Files likely touched\n- `scripts/x.sh`\n- `tests/scripts/test_x.sh`\n\n### Dependencies\nBlocked by: none'

echo "dispatch: each pass runs in a fresh worktree of origin/main, never in the owner's checkout"
sandbox "$ITEMS32"; issue_body 32 "$SAFE"
run
assert_count "$T/claude.calls" "called" 1 "claude is launched once"
assert_grep "$T/git.log" 'worktree add -q --detach .*dispatch-.* origin/main' "a detached worktree of origin/main is created"
assert_grep "$T/claude.cwds" 'dispatch-' "claude runs inside that worktree"
if grep -qx "$ROOT" "$T/claude.cwds"; then bad "claude does not run in the owner's checkout"; else ok "claude does not run in the owner's checkout"; fi
assert_grep "$T/git.log" 'worktree remove --force .*dispatch-' "the worktree is removed afterwards"
assert_no_grep "$T/git.log" 'git (checkout|switch|reset|stash)' "the script never switches or resets a branch itself"
assert_grep "$T/claude.args" '^--output-format$' "asks claude for structured output"

echo "dispatch: a worktree that cannot be created launches nothing"
sandbox "$ITEMS32"; issue_body 32 "$SAFE"
STUB_WORKTREE_FAIL=1 run
nonzero "exits non-zero"
assert_count "$T/claude.calls" "called" 0 "claude is not launched"
assert_grep "$T/out" 'worktree' "says why"

echo "dispatch: unfinished work is saved to a wip branch, never onto an agent's own branch"
sandbox "$ITEMS32"; issue_body 32 "$SAFE"
STUB_GIT_DIRTY=1 STUB_GIT_BRANCH=issue-32-ci run
assert_grep "$T/git.log" 'add -A' "uncommitted changes are staged"
assert_grep "$T/git.log" 'commit .*WIP' "and committed as a WIP commit"
assert_grep "$T/git.log" 'push .*HEAD:refs/heads/wip-issue-32-ci-' "and pushed to wip-<branch>-<time>"
assert_no_grep "$T/git.log" 'HEAD:refs/heads/issue-32-ci( |$)' "not to the branch the agent was using"
assert_grep "$T/gh.log" 'issue comment 32 ' "the issue is told where the work is"
assert_grep "$T/out" 'wip-issue-32-ci-' "and the output names the branch"
assert_grep "$T/git.log" 'worktree remove --force' "the worktree is removed once the work is safe"

echo "dispatch: commits that were never pushed are saved too"
sandbox "$ITEMS32"; issue_body 32 "$SAFE"
STUB_GIT_UNPUSHED=2 STUB_GIT_BRANCH=issue-32-ci run
assert_grep "$T/git.log" 'push .*HEAD:refs/heads/wip-issue-32-ci-' "they are pushed to a wip branch"
assert_no_grep "$T/git.log" 'commit ' "with no extra commit when the tree is clean"

echo "dispatch: nothing to save means no commit, push or comment"
sandbox "$ITEMS32"; issue_body 32 "$SAFE"
STUB_GIT_BRANCH=issue-32-ci run
assert_no_grep "$T/git.log" 'commit |push ' "no commit or push"
assert_no_grep "$T/gh.log" 'issue comment' "no comment"

echo "dispatch: work from a detached worktree is saved under a generic name"
sandbox "$ITEMS32"; issue_body 32 "$SAFE"
STUB_GIT_DIRTY=1 run
assert_grep "$T/git.log" 'push .*HEAD:refs/heads/wip-dispatch-' "pushed to wip-dispatch-<time>"
assert_no_grep "$T/gh.log" 'issue comment 32 .*wip' "no issue is named when the branch does not say which"

echo "dispatch: salvage runs even when claude fails"
sandbox "$ITEMS32"; issue_body 32 "$SAFE"
STUB_CLAUDE_FAIL=1 STUB_GIT_DIRTY=1 STUB_GIT_BRANCH=issue-32-ci run
nonzero "the pass reports the failure"
assert_grep "$T/git.log" 'push .*HEAD:refs/heads/wip-issue-32-ci-' "the unfinished work is still saved"

echo "dispatch: if the work cannot be saved, the worktree is kept"
sandbox "$ITEMS32"; issue_body 32 "$SAFE"
STUB_PUSH_FAIL=1 STUB_GIT_DIRTY=1 STUB_GIT_BRANCH=issue-32-ci run
nonzero "exits non-zero"
assert_no_grep "$T/git.log" 'worktree remove' "the worktree is not deleted"
assert_grep "$T/out" 'kept' "and the output says it was kept"
assert_grep "$T/out" 'dispatch-' "and where"

echo "dispatch: commands the agents were denied are reported with the result"
sandbox "$ITEMS32"; issue_body 32 "$SAFE"
echo '{"result":"All done.","permission_denials":[{"tool_name":"Bash","tool_input":{"command":"git worktree add ../x"}},{"tool_name":"Edit","tool_input":{"file_path":".claude/agents/architect.md"}},{"tool_name":"Bash","tool_input":{"command":"git worktree add ../x"}}]}' > "$T/claude.out"
run
assert_grep "$T/out" 'All done\.' "the agent's result is still printed"
assert_grep "$T/out" 'denied' "denials are announced"
assert_grep "$T/out" 'git worktree add \.\./x' "with the exact denied command"
assert_grep "$T/out" '\.claude/agents/architect\.md' "and the denied file edit"
assert_count "$T/out" 'git worktree add \.\./x' 1 "each denial once"
echo '{"result":"fine","permission_denials":[]}' > "$T/claude.out"
run
assert_no_grep "$T/out" 'denied' "no denials, no announcement"
echo 'plain text, not json' > "$T/claude.out"
run
assert_grep "$T/out" 'plain text, not json' "output that is not JSON is passed through"
assert_eq "$(cat "$T/rc")" "0" "and is not an error"

echo "preflight: a task that edits protected paths is held for an interactive session"
PROT=$'### Files likely touched\n- `.claude/settings.json`\n- `scripts/board.sh`\n\n### Dependencies\nBlocked by: none'
sandbox '{"items":[{"id":"I29","status":"Ready","content":{"number":29,"title":"Identity","url":"https://github.com/cooper3330/weekly-meal-planner/issues/29"},"labels":["type:task"]}]}'; issue_body 29 "$PROT"
run
assert_count "$T/claude.calls" "called" 0 "it is not dispatched"
assert_grep "$T/gh.log" 'issue comment 29 ' "the issue gets a comment"
assert_grep "$T/gh.log" 'interactive' "that says an interactive session is needed"
assert_grep "$T/gh.log" 'claude --agent developer' "with the command to run"
assert_grep "$T/gh.log" '\.claude/settings\.json' "and the protected path that caused it"
assert_grep "$T/gh.log" 'project item-edit --id I29 .*o_blocked' "and its card moves to Blocked"
assert_grep "$T/gh.log" 'agent:coordinator' "the comment carries the coordinator marker"

echo "preflight: other tasks in the same pass are still dispatched"
sandbox '{"items":[{"id":"I29","status":"Ready","content":{"number":29,"title":"Identity","url":"https://github.com/cooper3330/weekly-meal-planner/issues/29"},"labels":["type:task"]},{"id":"I33","status":"Ready","content":{"number":33,"title":"Risk"},"labels":["type:task"]}]}'
issue_body 29 "$PROT"; issue_body 33 "$SAFE"
run
assert_count "$T/claude.calls" "called" 1 "claude is launched once"
assert_grep "$T/claude.args" '"number":33' "for the safe task"
assert_no_grep "$T/claude.args" '"number":29' "and not the held one"

echo "preflight: only the Files likely touched section counts, in either heading style"
sandbox "$ITEMS32"; issue_body 32 $'### Background\nThe old design edited `.claude/settings.json`.\n\n### Files likely touched\n- `scripts/x.sh`\n'
run
assert_count "$T/claude.calls" "called" 1 "a protected path mentioned elsewhere does not hold the task"
sandbox "$ITEMS32"; issue_body 32 $'**Goal**\nx\n\n**Files likely touched**\n`.claude/settings.json`, `README.md`\n\n**Tests to write first**\nnothing\n'
run
assert_count "$T/claude.calls" "called" 0 "a bold-style section is read too"
sandbox "$ITEMS32"; issue_body 32 $'### Files likely touched\n- `./.git/hooks/pre-commit`\n'
run
assert_count "$T/claude.calls" "called" 0 "./-prefixed and .git paths are protected"
sandbox "$ITEMS32"; issue_body 32 $'### Files likely touched\n- `.github/workflows/ci.yml`\n- `docs/claude/notes.md`\n- `src/.claude-like/x.py`\n'
run
assert_count "$T/claude.calls" "called" 1 ".github and look-alike paths are not protected"

echo "preflight: features and architecture issues are never held"
sandbox '{"items":[{"id":"I4","status":"Ready","content":{"number":4,"title":"Delivery loop"},"labels":["type:feature"]}]}'; issue_body 4 "$PROT"
run
assert_count "$T/claude.calls" "called" 1 "a feature is dispatched to the architect as usual"

echo "preflight: agent definitions, commands and skills under .claude/ can be written headless (tested on Claude Code 2.1.312)"
for path in '.claude/agents/reviewer.md' '.claude/commands/fix.md' '.claude/skills/s/SKILL.md'; do
  sandbox "$ITEMS32"; issue_body 32 "$(printf '### Files likely touched\n- `%s`\n- `scripts/x.sh`\n' "$path")"
  run
  assert_count "$T/claude.calls" "called" 1 "not held: $path"
done

echo "preflight: other .claude/ paths and the editor and git directories are still held"
for path in '.claude/settings.json' '.claude/settings.local.json' '.claude/notes.md' '.claude/hooks/pre.sh' '.git/hooks/pre-commit' '.vscode/settings.json' '.mcp.json'; do
  sandbox "$ITEMS32"; issue_body 32 "$(printf '### Files likely touched\n- `%s`\n' "$path")"
  run
  assert_count "$T/claude.calls" "called" 0 "held: $path"
done

echo "preflight: a task listing an agent file and settings.json is held, and names only the protected one"
sandbox "$ITEMS32"; issue_body 32 $'### Files likely touched\n- `.claude/agents/reviewer.md`\n- `.claude/settings.json`\n'
run
assert_count "$T/claude.calls" "called" 0 "held"
assert_grep "$T/gh.log" 'protected paths \(\.claude/settings\.json\)' "the comment names only .claude/settings.json"

echo "preflight: WRITABLE_PATH_RE can restore the strict behaviour for a machine that denies agent files"
sandbox "$ITEMS32"; issue_body 32 $'### Files likely touched\n- `.claude/agents/reviewer.md`\n'
WRITABLE_PATH_RE='^$' run
assert_count "$T/claude.calls" "called" 0 "an agent file is held when WRITABLE_PATH_RE matches nothing"

finish
