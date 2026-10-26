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

# --- tasks go to Ready by default, so the script gates them on their "Blocked by:" line -------
task_items() { # numbers... -> a board with those Ready type:task cards
  local out='{"items":[' sep=""
  for n in "$@"; do
    out="$out$sep{\"id\":\"T$n\",\"status\":\"Ready\",\"content\":{\"number\":$n,\"title\":\"Task $n\"},\"labels\":[\"type:task\"]}"; sep=","
  done
  echo "$out]}"
}
body() { jq -nc --arg b "$2" '{body: $b}' > "$T/issue_$1.json"; }   # body ISSUE TEXT
run_gate() { PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1; }

echo "gating: a task waits while a blocker is open"
new_sandbox; task_items 16 > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
body 16 $'Goal: scaffold\n\nBlocked by: #15'
run_gate
assert_count "$T/claude.calls" "called" 0 "not dispatched"
assert_grep "$T/out" '#16 waits on #15' "says what it is waiting on"
assert_grep "$T/out" 'nothing Ready' "reports that nothing can be dispatched"

echo "gating: a closed blocker releases the task"
new_sandbox; task_items 16 > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
body 16 'Blocked by: #15'; echo '{"state":"closed"}' > "$T/issue_state_15.json"
run_gate
assert_count "$T/claude.calls" "called" 1 "dispatched"
assert_grep "$T/claude.args" '"number":16' "task 16 is in the prompt"

echo "gating: a PR blocker must be merged, not just closed"
new_sandbox; task_items 16 > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
body 16 'Blocked by: #15'; echo '{"state":"closed","pull_request":{"merged_at":null}}' > "$T/issue_state_15.json"
run_gate
assert_count "$T/claude.calls" "called" 0 "a closed-but-unmerged PR does not release the task"
new_sandbox; task_items 16 > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
body 16 'Blocked by: #15'; echo '{"state":"closed","pull_request":{"merged_at":"2026-10-26T01:00:00Z"}}' > "$T/issue_state_15.json"
run_gate
assert_count "$T/claude.calls" "called" 1 "a merged PR releases the task"

echo "gating: every listed blocker must be done"
new_sandbox; task_items 16 > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
body 16 'Blocked by: #14, #15'; echo '{"state":"closed"}' > "$T/issue_state_14.json"
run_gate
assert_count "$T/claude.calls" "called" 0 "one open blocker is enough to wait"
assert_grep "$T/out" '#16 waits on #15' "only the open blocker is named"
assert_no_grep "$T/out" 'waits on .*#14' "the closed blocker is not named"

echo "gating: no blockers"
for text in 'Blocked by: none' $'Goal only, no dependency line' $'## Dependencies / blocked by\nSee #15 for context'; do
  new_sandbox; task_items 16 > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
  body 16 "$text"
  run_gate
  assert_count "$T/claude.calls" "called" 1 "dispatched for: $(echo "$text" | head -1)"
done

echo "gating: markdown and bullet forms of the line are understood"
for text in '**Blocked by:** #15' '- Blocked by: #15' 'blocked by #15'; do
  new_sandbox; task_items 16 > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
  body 16 "$text"
  run_gate
  assert_count "$T/claude.calls" "called" 0 "waits for: $text"
done

echo "gating: the architect's existing wording (#17-#20) is understood"
new_sandbox; task_items 20 > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
body 20 $'**Dependencies / blocked by**\nBlocked by #17. Can run in parallel with #18 and #19.'; echo '{"state":"closed"}' > "$T/issue_state_17.json"
run_gate
assert_count "$T/claude.calls" "called" 1 "refs after the first sentence are not blockers (parallel-with note)"
new_sandbox; task_items 19 > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
body 19 $'**Dependencies / blocked by**\nBlocked by #17 and #18.'; echo '{"state":"closed"}' > "$T/issue_state_17.json"
run_gate
assert_count "$T/claude.calls" "called" 0 "'#17 and #18' means both are required"
assert_grep "$T/out" '#19 waits on #18' "and the open one is named"

echo "gating: only the unblocked tasks reach the coordinator"
new_sandbox; task_items 16 17 > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
body 16 'Blocked by: #15'; body 17 'Blocked by: none'
run_gate
assert_grep "$T/claude.args" '"number":17' "the free task is dispatched"
assert_no_grep "$T/claude.args" '"number":16' "the blocked task is held back"

echo "gating: features and architecture issues are never gated"
new_sandbox; echo '{"items":[{"id":"F5","status":"Ready","content":{"number":5,"title":"Arch"},"labels":["type:architecture"]}]}' > "$T/items.json"
echo "$STATUS_FIELDS" > "$T/fields.json"; body 5 'Blocked by: #99'
run_gate
assert_count "$T/claude.calls" "called" 1 "dispatched regardless of any Blocked by line"

echo "coordinator: tells the architect how to set task Status"
assert_grep "$T/claude.args" 'dispatch the architect, pass it' "the dispatch prompt asks the coordinator to forward the Board IDs to the architect"
assert_grep "$T/claude.args" 'Ready.*Plan Review|Plan Review.*Ready' "and explains Ready versus Plan Review"
assert_grep "$T/claude.args" 'Bash\(gh project item-add \*\)' "item-add is allowed so the architect can put new tasks on the board"

echo "revisions: the architect gets the Board IDs, other agents do not need them"
new_sandbox; echo "$PRS15" > "$T/prs.json"; echo "$PR_REVISE" > "$T/issue_comments_15.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_grep "$T/claude.args" 'PVT_abc' "architect revision prompt carries the project node ID"
assert_grep "$T/claude.args" 'Project 1 \(owner cooper3330\)' "and the project number and owner"
new_sandbox; echo "$PRS15" > "$T/prs.json"; echo "$PR_REVISE" > "$T/issue_comments_15.json"
PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_count "$T/claude.calls" "called" 1 "a revision still runs when the Status field is not set up"
assert_no_grep "$T/claude.args" 'PVT_abc' "without Board IDs in the prompt"

finish
