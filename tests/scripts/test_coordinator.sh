#!/usr/bin/env bash
# Tests for scripts/coordinator.sh (offline, stubbed gh/claude/date).
. "$(dirname "$0")/lib.sh"
SCRIPT="$ROOT/scripts/coordinator.sh"

STATUS_FIELDS='{"fields":[{"id":"PVTF_title","name":"Title","type":"ProjectV2Field"},{"id":"PVTSSF_status","name":"Status","type":"ProjectV2SingleSelectField","options":[{"id":"o_back","name":"Backlog"},{"id":"o_spec","name":"Spec Review"},{"id":"o_ready","name":"Ready"},{"id":"o_arch","name":"Architecture"},{"id":"o_plan","name":"Plan Review"},{"id":"o_impl","name":"Implementation"},{"id":"o_review","name":"In Review"},{"id":"o_done","name":"Done"},{"id":"o_blocked","name":"Blocked"}]}],"totalCount":2}'

READY='{"items":[
 {"id":"I1","status":"Ready","content":{"number":5,"title":"Architecture baseline"},"labels":["type:architecture"]},
 {"id":"I2","status":"Backlog","content":{"number":6,"title":"Profile"},"labels":["type:feature"]},
 {"id":"I3","status":"Ready","content":{"title":"A draft note with no issue number"}}]}'

echo "coordinator: dispatches Ready issues with an explicit tool allowlist"
new_sandbox; echo "$READY" > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1; rc=$?
assert_eq "$rc" "0" "exits 0"
assert_count "$T/claude.calls" "called" 1 "claude is invoked exactly once"
assert_grep "$T/claude.args" '^-p$' "runs headless with -p"
assert_grep "$T/claude.args" '^coordinator$' "uses the coordinator agent"
assert_grep "$T/claude.args" '^--allowedTools$' "passes --allowedTools"
assert_grep "$T/claude.args" 'Bash\(gh issue view \*\)' "allows reading issues"
assert_grep "$T/claude.args" 'Bash\(gh project item-edit \*\)' "allows setting the Status"
assert_grep "$T/claude.args" 'Bash\(gh api repos/cooper3330/weekly-meal-planner/\*\)' "scopes gh api to this repo"
assert_no_grep "$T/claude.args" 'Bash\(gh api \*\)' "does not allow gh api for any repo"
assert_grep "$T/claude.args" '"number":5' "prompt lists the Ready issue"
assert_no_grep "$T/claude.args" '"number":6' "prompt omits Backlog issues"
assert_no_grep "$T/claude.args" 'draft note' "prompt omits draft items with no issue number"
p=$(grep -n 'Items in Ready' "$T/claude.args" | head -1 | cut -d: -f1)
a=$(grep -n '^--allowedTools$' "$T/claude.args" | head -1 | cut -d: -f1)
if [ -n "$p" ] && [ -n "$a" ] && [ "$p" -lt "$a" ]; then ok "prompt comes before --allowedTools so it is not swallowed as a rule"; else bad "prompt comes before --allowedTools so it is not swallowed as a rule"; fi

echo "coordinator: works with BSD (macOS) date"
assert_no_grep "$T/out" "invalid argument" "no date error is printed"
assert_grep "$T/out" '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[+-][0-9]{4} Ready items' "log lines carry an ISO-8601 timestamp"

echo "coordinator: uses the repository owner, not @me"
assert_grep "$T/gh.log" 'project item-list 1 --owner cooper3330' "reads the board as the repo owner"
assert_no_grep "$T/gh.log" '@me' "never uses @me"

echo "coordinator: nothing Ready"
new_sandbox; echo '{"items":[{"id":"I2","status":"Backlog","content":{"number":6,"title":"Profile"},"labels":[]}]}' > "$T/items.json"
PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1; rc=$?
assert_eq "$rc" "0" "exits 0"
assert_count "$T/claude.calls" "called" 0 "claude is not invoked"
assert_grep "$T/out" 'nothing Ready' "reports that nothing is Ready"

echo "coordinator: extra allowed tools"
new_sandbox; echo "$READY" > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
EXTRA_ALLOWED_TOOLS='Bash(npm test),Bash(ls *)' PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_grep "$T/claude.args" '^Bash\(npm test\)$' "EXTRA_ALLOWED_TOOLS adds a rule containing a space"
assert_grep "$T/claude.args" '^Bash\(ls \*\)$' "EXTRA_ALLOWED_TOOLS adds a second rule"

echo "coordinator: requires PROJECT_NUMBER"
new_sandbox
env -u PROJECT_NUMBER bash "$SCRIPT" --once > "$T/out" 2>&1; rc=$?
if [ "$rc" -ne 0 ]; then ok "exits non-zero"; else bad "exits non-zero"; fi
assert_grep "$T/out" 'PROJECT_NUMBER' "names the missing variable"

echo "coordinator: resolves the board IDs itself and hands them to the agent"
new_sandbox; echo "$READY" > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_grep "$T/claude.args" 'PVT_abc' "prompt carries the project node ID"
assert_grep "$T/claude.args" 'PVTSSF_status' "prompt carries the Status field ID"
assert_grep "$T/claude.args" '"Implementation":"o_impl"' "prompt carries the Implementation option ID"
assert_grep "$T/claude.args" '"Architecture":"o_arch"' "prompt carries the Architecture option ID"
assert_grep "$T/claude.args" '"item_id":"I1"' "each Ready item carries its board item ID"
assert_grep "$T/claude.args" 'item-edit --id .*--project-id .*--field-id .*--single-select-option-id' "prompt shows the exact item-edit command shape"
assert_grep "$T/claude.args" 'Bash\(gh project view \*\)' "allows gh project view"
assert_grep "$T/claude.args" 'Bash\(gh project list \*\)' "allows gh project list"

echo "coordinator: fails clearly when there is no Status field"
new_sandbox; echo "$READY" > "$T/items.json"
echo '{"fields":[{"id":"PVTF_title","name":"Title","type":"ProjectV2Field"}],"totalCount":1}' > "$T/fields.json"
PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1; rc=$?
if [ "$rc" -ne 0 ]; then ok "exits non-zero"; else bad "exits non-zero"; fi
assert_count "$T/claude.calls" "called" 0 "claude is not invoked"
assert_grep "$T/out" 'Status' "names the missing field"
assert_grep "$T/out" 'setup-project' "points at the setup script"

echo "coordinator: fails clearly when Status lacks the nine options (fresh project defaults)"
new_sandbox; echo "$READY" > "$T/items.json"
PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1; rc=$?
if [ "$rc" -ne 0 ]; then ok "exits non-zero"; else bad "exits non-zero"; fi
assert_count "$T/claude.calls" "called" 0 "claude is not invoked"
assert_grep "$T/out" 'missing options: .*Ready' "lists the missing options, including Ready"
assert_grep "$T/out" 'Spec Review' "lists Spec Review too"
assert_no_grep "$T/out" 'missing options: .*Done' "does not list options that exist (Done)"

echo "coordinator: only reads the Status field, not a legacy Stage key"
new_sandbox; echo '{"items":[{"id":"I9","stage":"Ready","content":{"number":9,"title":"Old"},"labels":[]}]}' > "$T/items.json"
PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_count "$T/claude.calls" "called" 0 "an item with only a legacy stage value is not dispatched"

echo "coordinator: makes no board lookups when nothing is Ready"
new_sandbox; echo '{"items":[]}' > "$T/items.json"
PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_no_grep "$T/gh.log" 'project (view|field-list)' "no project view or field-list calls"

# --- review / revision loop -------------------------------------------------------------
PR_REVISE='[{"id":1,"created_at":"2026-10-26T10:00:00Z","body":"Please change ADR 0004 /revise","author_association":"OWNER"}]'
PRS15='[{"number":15,"headRefName":"arch-5-architecture-baseline"}]'

echo "coordinator: dispatches the architect to revise a PR with feedback"
new_sandbox; echo "$PRS15" > "$T/prs.json"; echo "$PR_REVISE" > "$T/issue_comments_15.json"
PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1; rc=$?
assert_eq "$rc" "0" "exits 0"
assert_count "$T/claude.calls" "called" 1 "claude is invoked once, only for the revision"
assert_grep "$T/claude.args" '^architect$' "uses the architect agent"
assert_grep "$T/claude.args" 'Revise after review' "prompt asks for revision mode"
assert_grep "$T/claude.args" 'PR #15' "prompt names the PR"
assert_grep "$T/claude.args" 'arch-5-architecture-baseline' "prompt names the branch"
assert_no_grep "$T/claude.args" 'Please change ADR 0004' "comment text (untrusted) is not pasted into the prompt"
assert_grep "$T/claude.args" '^--allowedTools$' "revision runs with the same explicit allowlist"
assert_grep "$T/claude.args" 'Bash\(gh pr comment \*\)' "allows replying on the PR"
assert_grep "$T/claude.args" 'Bash\(make check\)' "allows running make check"
assert_grep "$T/gh.log" 'pr comment 15 .*agent:ack' "posts a hidden acknowledgement on the PR first"
assert_grep "$T/git.log" 'worktree add .*arch-5-architecture-baseline' "works in a separate git worktree on the PR branch"
assert_grep "$T/git.log" 'worktree remove' "removes the worktree afterwards"
if [ -s "$T/claude.cwds" ] && ! grep -Fxq "$ROOT" "$T/claude.cwds"; then ok "runs outside the user's own checkout"; else bad "runs outside the user's own checkout"; fi

echo "coordinator: revision worktrees are safe and current"
assert_no_grep "$T/git.log" 'worktree add .* -B ' "never resets a local branch (detached worktree)"
assert_grep "$T/git.log" 'worktree add -q --detach .* origin/arch-5-architecture-baseline' "checks the PR branch out detached"
assert_grep "$T/git.log" 'merge -q --no-edit origin/main' "merges main in first so the agent runs the current protocol"

echo "coordinator: a merge conflict with main is reported, not run"
new_sandbox; echo "$PRS15" > "$T/prs.json"; echo "$PR_REVISE" > "$T/issue_comments_15.json"
STUB_MERGE_FAIL=1 PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1; rc=$?
assert_eq "$rc" "0" "exits 0"
assert_count "$T/claude.calls" "called" 0 "the agent is not started"
assert_grep "$T/git.log" 'merge --abort' "the failed merge is aborted"
assert_grep "$T/gh.log" 'pr comment 15 .*conflict.*agent:ack' "tells the reviewer on the PR, with the marker so it is not retried forever"
assert_grep "$T/git.log" 'worktree remove' "the worktree is cleaned up"

echo "coordinator: a worktree failure is reported, not run in an empty directory"
new_sandbox; echo "$PRS15" > "$T/prs.json"; echo "$PR_REVISE" > "$T/issue_comments_15.json"
STUB_WORKTREE_FAIL=1 PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1; rc=$?
assert_eq "$rc" "0" "exits 0"
assert_count "$T/claude.calls" "called" 0 "the agent is not started"
assert_grep "$T/gh.log" 'pr comment 15 .*Could not (create|start).*agent:ack' "tells the reviewer on the PR"

echo "coordinator: does not re-dispatch feedback that was already acknowledged"
new_sandbox; echo "$PRS15" > "$T/prs.json"
echo '[{"id":1,"created_at":"2026-10-26T10:00:00Z","body":"/revise","author_association":"OWNER"},{"id":2,"created_at":"2026-10-26T10:00:05Z","body":"Picked up. <!-- agent:ack -->","author_association":"OWNER"}]' > "$T/issue_comments_15.json"
PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_count "$T/claude.calls" "called" 0 "an acknowledged trigger is not dispatched again"

echo "coordinator: dispatches the architect to revise a plan from an issue comment"
new_sandbox
echo '[{"number":5,"labels":[{"name":"type:architecture"}]}]' > "$T/issues.json"
echo '[{"id":1,"created_at":"2026-10-26T10:00:00Z","body":"add a task /revise","author_association":"OWNER"}]' > "$T/issue_comments_5.json"
PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_count "$T/claude.calls" "called" 1 "one run"
assert_grep "$T/claude.args" '^architect$' "architect agent"
assert_grep "$T/claude.args" 'issue #5' "prompt names the issue"
assert_grep "$T/gh.log" 'issue comment 5 .*agent:ack' "acknowledges on the issue"
assert_grep "$T/git.log" 'worktree add .*origin/main' "plan revisions start from main"

echo "coordinator: developer revises its own PR"
new_sandbox; echo '[{"number":30,"headRefName":"issue-16-scaffold"}]' > "$T/prs.json"
echo '[{"id":3,"submitted_at":"2026-10-26T10:00:00Z","state":"CHANGES_REQUESTED","body":"","author_association":"OWNER"}]' > "$T/pr_reviews_30.json"
PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_grep "$T/claude.args" '^developer$' "developer agent"

echo "coordinator: revisions run before new Ready work in the same pass"
new_sandbox; echo "$READY" > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
echo "$PRS15" > "$T/prs.json"; echo "$PR_REVISE" > "$T/issue_comments_15.json"
PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_count "$T/claude.calls" "called" 2 "two runs: one revision, one dispatch"
seq=$(grep -A1 '^--agent$' "$T/claude.all" | grep -vE '^(--agent|--)$' | tr '\n' ' ')
assert_eq "$seq" "architect coordinator " "revision first, then the coordinator"

echo "coordinator: a failed revision does not stop the pass"
new_sandbox; echo "$READY" > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
echo "$PRS15" > "$T/prs.json"; echo "$PR_REVISE" > "$T/issue_comments_15.json"
printf '#!/usr/bin/env bash\necho called >> "$STUB_DIR/claude.calls"\nprintf "%%s\\n" "$@" > "$STUB_DIR/claude.args"\n{ echo ---CALL---; printf "%%s\\n" "$@"; } >> "$STUB_DIR/claude.all"\n[ "$2" != "" ] && case "$*" in *"Revise after review"*) exit 1;; esac\n' > "$T/bin/claude"
PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1; rc=$?
assert_eq "$rc" "0" "exits 0"
assert_count "$T/claude.calls" "called" 2 "the Ready dispatch still happens"
assert_grep "$T/out" 'revision (of|for) .*failed|revision failed' "the failure is reported"

finish
