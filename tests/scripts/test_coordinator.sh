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
assert_grep "$T/claude.args" 'Bash\(scripts/board.sh \*\)' "allows the board wrapper, the only way to set the Status"
assert_no_grep "$T/claude.args" 'gh project item-(edit|add)' "no longer allows raw gh project item-edit or item-add"
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

echo "coordinator: points agents at scripts/board.sh instead of passing Board IDs"
new_sandbox; echo "$READY" > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_no_grep "$T/claude.args" 'Board IDs' "the prompt no longer contains Board IDs"
assert_no_grep "$T/claude.args" 'PVT_abc|PVTSSF_status|o_impl' "and carries no raw project, field or option IDs"
assert_grep "$T/claude.args" 'scripts/board\.sh status <issue-number> <Status name>' "prompt shows the board.sh command shape"
assert_grep "$T/claude.args" '"item_id":"I1"' "each Ready item carries its board item ID"
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
assert_grep "$T/claude.args" 'dispatch the architect, tell it' "the dispatch prompt asks the coordinator to tell the architect to use scripts/board.sh"
assert_grep "$T/claude.args" 'scripts/board\.sh \(add <issue-url>' "and shows add"
assert_grep "$T/claude.args" 'Ready.*Plan Review|Plan Review.*Ready' "and explains Ready versus Plan Review"

echo "revisions: the architect is pointed at scripts/board.sh, other agents do not need it"
new_sandbox; echo "$PRS15" > "$T/prs.json"; echo "$PR_REVISE" > "$T/issue_comments_15.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_grep "$T/claude.args" 'Project 1 \(owner cooper3330\)' "architect revision prompt carries the project number and owner"
assert_grep "$T/claude.args" 'scripts/board\.sh' "and names scripts/board.sh"
assert_no_grep "$T/claude.args" 'Board IDs' "with no Board IDs"
new_sandbox; echo "$PRS15" > "$T/prs.json"; echo "$PR_REVISE" > "$T/issue_comments_15.json"
PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_count "$T/claude.calls" "called" 1 "a revision still runs when the Status field is not set up"

# --- agent identity (docs/adr/0014) -----------------------------------------------------------
echo "identity: in Actions every claude launch runs as the App token, never the project token"
new_sandbox; echo "$READY" > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
echo "$PRS15" > "$T/prs.json"; echo "$PR_REVISE" > "$T/issue_comments_15.json"
GITHUB_ACTIONS=true AGENT_GH_TOKEN=atok PROJECT_TOKEN=ptok GH_TOKEN=ambient PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1; rc=$?
assert_eq "$rc" "0" "exits 0"
assert_count "$T/claude.calls" "called" 2 "a revision and a dispatch were launched"
assert_count "$T/claude.env" '^GH_TOKEN=atok ' 2 "every launch sees GH_TOKEN=atok"
assert_no_grep "$T/claude.env" 'GH_TOKEN=ptok' "no launch sees the project token as GH_TOKEN"
assert_count "$T/claude.env" 'PROJECT_TOKEN=ptok' 2 "PROJECT_TOKEN stays available to scripts/board.sh"
assert_no_grep "$T/out" 'local gh user' "no local-fallback message in Actions"
assert_no_grep "$T/gh.env" '^project item-list GH_TOKEN=(atok|ambient|UNSET)$' "the board is read with the project token"
assert_grep "$T/gh.env" '^project item-list GH_TOKEN=ptok$' "(item-list ran with PROJECT_TOKEN)"
assert_no_grep "$T/gh.env" '^pr (list|comment) GH_TOKEN=(ptok|ambient|UNSET)$' "the script's own PR calls act as the App"

echo "identity: in Actions a missing or empty AGENT_GH_TOKEN stops everything before any launch"
for mode in unset empty; do
  new_sandbox; echo "$READY" > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
  echo "$PRS15" > "$T/prs.json"; echo "$PR_REVISE" > "$T/issue_comments_15.json"
  if [ "$mode" = "empty" ]; then export AGENT_GH_TOKEN=""; else unset AGENT_GH_TOKEN; fi
  GITHUB_ACTIONS=true PROJECT_TOKEN=ptok PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1; rc=$?
  unset AGENT_GH_TOKEN
  if [ "$rc" -ne 0 ]; then ok "$mode: exits non-zero"; else bad "$mode: exits non-zero"; fi
  assert_count "$T/claude.calls" "called" 0 "$mode: no claude launch"
  assert_grep "$T/out" 'AGENT_GH_TOKEN' "$mode: the message names AGENT_GH_TOKEN"
  assert_no_grep "$T/gh.log" 'pr comment' "$mode: nothing was posted"
done

# --- the push probe: a dry-run push is the same handshake the agent's real push makes (#66, #78) -----
actions() { # extra env... -- mode args...: run the coordinator the way the workflow does
  GITHUB_ACTIONS=true AGENT_GH_TOKEN=atok PROJECT_TOKEN=ptok PROJECT_NUMBER=1 "$@" > "$T/out" 2>&1; echo $? > "$T/rc"
}
echo "identity: in Actions a refused push probe stops the run before any launch"
new_sandbox; echo "$READY" > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
STUB_PUSH_DENIED=1 actions bash "$SCRIPT" --once
if [ "$(cat "$T/rc")" -ne 0 ]; then ok "exits non-zero"; else bad "exits non-zero"; fi
assert_count "$T/claude.calls" "called" 0 "no agent is launched"
assert_grep "$T/out" 'Contents' "the message names the Contents permission"
assert_grep "$T/out" 'returned error: 403' "and shows what git said, so the refusal can be read from the log"
assert_count "$T/git.log" 'push --dry-run' 1 "the probe is one dry-run push (it changes nothing on the remote)"

echo "identity: in Actions the permissions the token reports are logged for diagnosis"
new_sandbox; echo "$READY" > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
echo '{"permissions":{"admin":false,"push":false,"pull":true}}' > "$T/repo.json"
actions bash "$SCRIPT" --once
assert_grep "$T/out" 'reported repository permissions' "a log line names them"
assert_grep "$T/out" '"push": false' "and shows the reported value"
assert_eq "$(cat "$T/rc")" "0" "a reported push:false alone does not stop the run (the probe decides)"
assert_count "$T/claude.calls" "called" 1 "so the agent is launched"

echo "identity: an accepted probe proceeds"
new_sandbox; echo "$READY" > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
actions bash "$SCRIPT" --once
assert_eq "$(cat "$T/rc")" "0" "exits 0"
assert_count "$T/claude.calls" "called" 1 "the agent is launched"

echo "identity: a failing repository-permissions call does not block the run"
new_sandbox; echo "$READY" > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
STUB_REPO_API_FAIL=1 actions bash "$SCRIPT" --once
assert_eq "$(cat "$T/rc")" "0" "exits 0 (the probe was accepted, and the diagnostic call is only a log line)"
assert_grep "$T/out" 'reported repository permissions for this token: unavailable' "the log says the permissions were unavailable"
assert_count "$T/claude.calls" "called" 1 "the agent is launched"

echo "identity: a probe that fails for another reason (network) does not block the run"
new_sandbox; echo "$READY" > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
STUB_PUSH_NET=1 actions bash "$SCRIPT" --once
assert_eq "$(cat "$T/rc")" "0" "exits 0"
assert_count "$T/claude.calls" "called" 1 "the agent is launched"
assert_grep "$T/out" 'could not tell' "and the log says the probe was inconclusive"

echo "identity: a missing AGENT_GH_TOKEN is reported before the push probe"
new_sandbox; echo "$READY" > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
env -u AGENT_GH_TOKEN GITHUB_ACTIONS=true STUB_PUSH_DENIED=1 PROJECT_TOKEN=ptok PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_grep "$T/out" 'AGENT_GH_TOKEN is missing' "the missing-token message comes first"
assert_count "$T/git.log" 'push --dry-run' 0 "and the probe is not reached"

# --- a revision that cannot start because the App cannot push says so on the item (#78) -------------
echo "identity: --revise pr with a refused probe comments on the PR (#78)"
new_sandbox; echo '{"permissions":{"admin":false,"push":false,"pull":true}}' > "$T/repo.json"
STUB_PUSH_DENIED=1 actions bash "$SCRIPT" --revise pr 12
if [ "$(cat "$T/rc")" -ne 0 ]; then ok "exits non-zero"; else bad "exits non-zero"; fi
assert_count "$T/claude.calls" "called" 0 "no agent is launched"
assert_count "$T/gh.log" '^gh pr comment 12 ' 1 "one comment on the PR"
body=$(awk '/^gh pr comment 12 /{f=1} f' "$T/gh.log")   # the call's arguments span several lines
for want in 'Contents' 'Pull requests' '/revise' 'returned error: 403' '"push": false' '<!-- agent:permission -->'; do
  if grep -qF -- "$want" <<<"$body"; then ok "the comment has: $want"; else bad "the comment has: $want"; fi
done
if grep -qF 'source=' <<<"$body"; then bad "no source= marker (it never counts toward the revision budget)"; else ok "no source= marker (it never counts toward the revision budget)"; fi

echo "identity: --revise issue comments on the issue (#78)"
new_sandbox
STUB_PUSH_DENIED=1 actions bash "$SCRIPT" --revise issue 12
assert_count "$T/gh.log" '^gh issue comment 12 ' 1 "one comment on the issue"
assert_count "$T/claude.calls" "called" 0 "no agent is launched"

echo "identity: the comment is posted at most once (#78)"
new_sandbox
echo '[{"id":1,"created_at":"2026-10-29T10:00:00Z","body":"please /revise","author_association":"OWNER"},{"id":2,"created_at":"2026-10-29T10:01:00Z","body":"could not start <!-- agent:permission -->","author_association":"NONE"}]' > "$T/issue_comments_12.json"
STUB_PUSH_DENIED=1 actions bash "$SCRIPT" --revise pr 12
if [ "$(cat "$T/rc")" -ne 0 ]; then ok "still exits non-zero"; else bad "still exits non-zero"; fi
assert_count "$T/gh.log" 'comment 12' 0 "nothing more is posted"

echo "identity: the once-only check reads every page of comments, not the last page alone (#78)"
new_sandbox
# Page 1 holds the permission comment (an agent comment); page 2 holds only human comments. gh --paginate --jq
# would run the filter once per page, so a check that reads the last page alone would not see the marker.
echo '[[{"id":2,"created_at":"2026-10-29T10:01:00Z","body":"could not start\n\nmore text <!-- agent:permission -->","author_association":"NONE"}],[{"id":3,"created_at":"2026-10-29T11:00:00Z","body":"/revise again","author_association":"OWNER"}]]' > "$T/issue_comments_12.pages.json"
STUB_PUSH_DENIED=1 actions bash "$SCRIPT" --revise pr 12
if [ "$(cat "$T/rc")" -ne 0 ]; then ok "still exits non-zero"; else bad "still exits non-zero"; fi
assert_count "$T/gh.log" 'comment 12' 0 "nothing more is posted when the marker is on an earlier page"

echo "identity: other cases post nothing (#78)"
new_sandbox
actions bash "$SCRIPT" --revise pr 12
assert_count "$T/gh.log" 'comment 12' 0 "an accepted probe: no comment"
new_sandbox
STUB_PUSH_NET=1 actions bash "$SCRIPT" --revise pr 12
assert_count "$T/gh.log" 'comment 12' 0 "an inconclusive probe: no comment"
new_sandbox
STUB_PUSH_DENIED=1 env -u GITHUB_ACTIONS -u AGENT_GH_TOKEN PROJECT_NUMBER=1 bash "$SCRIPT" --revise pr 12 > "$T/out" 2>&1
assert_count "$T/gh.log" 'comment 12' 0 "a local run: no comment"
assert_count "$T/git.log" 'push --dry-run' 0 "and no probe"
new_sandbox; echo "$READY" > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
STUB_PUSH_DENIED=1 actions bash "$SCRIPT" --once
if [ "$(cat "$T/rc")" -ne 0 ]; then ok "--once still exits non-zero"; else bad "--once still exits non-zero"; fi
assert_count "$T/gh.log" ' comment ' 0 "--once has no item to comment on"

echo "identity: locally, without AGENT_GH_TOKEN, it launches as before and logs the fallback once"
new_sandbox; echo "$READY" > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
echo "$PRS15" > "$T/prs.json"; echo "$PR_REVISE" > "$T/issue_comments_15.json"
env -u AGENT_GH_TOKEN -u GITHUB_ACTIONS GH_TOKEN=mine PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1; rc=$?
assert_eq "$rc" "0" "exits 0"
assert_count "$T/claude.calls" "called" 2 "both launches happen"
assert_count "$T/out" 'AGENT_GH_TOKEN is not set.*local gh user' 1 "the fallback is logged exactly once"
assert_count "$T/claude.env" '^GH_TOKEN=mine ' 2 "the launches keep the ambient GH_TOKEN"

echo "identity: AGENT_GIT_NAME and AGENT_GIT_EMAIL set the commit identity for launches"
new_sandbox; echo "$READY" > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
AGENT_GIT_NAME='meal-planner-agents[bot]' AGENT_GIT_EMAIL='1+meal-planner-agents[bot]@users.noreply.github.com' PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_grep "$T/claude.env" 'GIT_AUTHOR_NAME=meal-planner-agents\[bot\] GIT_COMMITTER_NAME=meal-planner-agents\[bot\]' "author and committer name are set"
assert_grep "$T/claude.env" 'GIT_AUTHOR_EMAIL=1\+meal-planner-agents\[bot\]@users.noreply.github.com GIT_COMMITTER_EMAIL=1\+meal-planner-agents\[bot\]@users.noreply.github.com' "author and committer email are set"
new_sandbox; echo "$READY" > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
AGENT_GIT_NAME='only-a-name' PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_grep "$T/claude.env" 'GIT_AUTHOR_NAME=UNSET' "a name without an email changes nothing"


echo "allowlist: gh api writes are allowed with the flag first, for this repo only, never DELETE"
new_sandbox; echo "$READY" > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
for form in 'gh api -X PATCH' 'gh api -X POST' 'gh api --method PATCH' 'gh api --method POST'; do
  assert_grep "$T/claude.args" "^Bash\\($form repos/cooper3330/weekly-meal-planner/\\*\\)\$" "allows: $form repos/<this repo>/*"
done
assert_no_grep "$T/claude.args" 'DELETE' "never allows DELETE"
assert_no_grep "$T/claude.args" 'Bash\(gh api -X (PATCH|POST) \*\)' "no rule that is not scoped to this repo"

echo "coordinator: background subagents may run as long as they need (claude -p stops waiting after 10 minutes)"
new_sandbox; echo "$READY" > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_eq "$(head -1 "$T/claude.bgwait")" "0" "a dispatch run waits indefinitely for background subagents by default"
new_sandbox; echo "$PRS15" > "$T/prs.json"; echo "$PR_REVISE" > "$T/issue_comments_15.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_eq "$(head -1 "$T/claude.bgwait")" "0" "a revision run waits indefinitely too"
new_sandbox; echo "$READY" > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=7200000 PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_eq "$(head -1 "$T/claude.bgwait")" "7200000" "a value already in the environment wins, so a cap can be set"

echo "allowlist: agents can run the shell tests and pin GitHub Actions to a commit SHA"
new_sandbox; echo "$READY" > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_grep "$T/claude.args" '^Bash\(scripts/pin-action\.sh \*\)$' "allows scripts/pin-action.sh (a fixed read-only lookup)"
assert_grep "$T/claude.args" '^Bash\(bash tests/scripts/\*\)$' "allows running the shell tests under tests/scripts"
assert_no_grep "$T/claude.args" 'Bash\((bash|sh|curl|wget) \*\)' "no unrestricted shell, curl or wget"

echo "revisions: a revision of a task's PR is told about the linked issue"
new_sandbox; echo '[{"number":46,"headRefName":"issue-32-ci-workflow-v3"}]' > "$T/prs.json"
echo '[{"id":1,"created_at":"2026-10-28T01:22:58Z","body":"pull main in /revise","author_association":"OWNER"}]' > "$T/issue_comments_32.json"
PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_grep "$T/claude.args" 'PR #46' "the PR is named"
assert_grep "$T/claude.args" 'linked issue #32' "and so is the issue whose comments also count"
assert_grep "$T/claude.args" '^developer$' "the developer handles it"

echo "allowlist: agents can bring main into their branch, and read CI, but not rebase or pull"
new_sandbox; echo "$READY" > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
for rule in 'git merge origin/main' 'git merge --no-edit origin/main' 'git merge --abort' 'git merge-base \*' 'gh pr checks \*' 'gh run view \*' 'gh run list \*'; do
  assert_grep "$T/claude.args" "^Bash\\($rule\\)\$" "allows: $rule"
done
assert_no_grep "$T/claude.args" 'Bash\(git pull' "git pull is gone (it can rebase, or pull from any remote)"
assert_no_grep "$T/claude.args" 'Bash\(git merge \*\)' "no open-ended git merge"
assert_no_grep "$T/claude.args" 'git (rebase|reset|cherry-pick)' "no rebase, reset or cherry-pick"

echo "coordinator: a usage-limit stop during a revision is reported once, without a retry"
new_sandbox; echo "$PRS15" > "$T/prs.json"; echo "$PR_REVISE" > "$T/issue_comments_15.json"
echo "Claude AI usage limit reached" > "$T/claude.err"
STUB_CLAUDE_FAIL=1 PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_count "$T/claude.calls" "called" 1 "claude ran once, no retry in the same pass"
assert_count "$T/gh.log" '^gh pr comment 15 .*agent:limit' 1 "the PR gets one limit comment"

echo "coordinator: a limit stop in the ready pass with one dispatched item comments on that item"
new_sandbox
echo '{"items":[{"id":"I1","status":"Ready","content":{"number":5,"title":"x"},"labels":["type:architecture"]}]}' > "$T/items.json"
echo "$STATUS_FIELDS" > "$T/fields.json"
echo "Claude AI usage limit reached" > "$T/claude.err"
STUB_CLAUDE_FAIL=1 PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_count "$T/claude.calls" "called" 1 "claude ran once"
assert_count "$T/gh.log" '^gh issue comment 5 .*agent:limit' 1 "issue #5 gets one limit comment"

echo "coordinator: a limit stop with several Ready items is only logged"
new_sandbox
echo '{"items":[{"id":"I1","status":"Ready","content":{"number":5,"title":"x"},"labels":["type:architecture"]},{"id":"I2","status":"Ready","content":{"number":6,"title":"y"},"labels":["type:architecture"]}]}' > "$T/items.json"
echo "$STATUS_FIELDS" > "$T/fields.json"
echo "Claude AI usage limit reached" > "$T/claude.err"
STUB_CLAUDE_FAIL=1 PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_no_grep "$T/gh.log" 'agent:limit' "no limit comment when the target is ambiguous"
assert_grep "$T/out" 'usage limit' "the limit is logged"

# --- turn caps (FR-11, ADR 0015) ---------------------------------------------------------------
max_turns_of() { grep -A1 '^--max-turns$' "$1" | grep -vE '^(--max-turns|--)$' | head -1; }

echo "turn caps: an architect revision gets ARCHITECT_MAX_TURNS, else 60"
new_sandbox; echo "$PRS15" > "$T/prs.json"; echo "$PR_REVISE" > "$T/issue_comments_15.json"
ARCHITECT_MAX_TURNS=7 PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_eq "$(max_turns_of "$T/claude.args")" "7" "--max-turns follows ARCHITECT_MAX_TURNS"
new_sandbox; echo "$PRS15" > "$T/prs.json"; echo "$PR_REVISE" > "$T/issue_comments_15.json"
PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_eq "$(max_turns_of "$T/claude.args")" "60" "the architect default is 60"
new_sandbox; echo "$PRS15" > "$T/prs.json"; echo "$PR_REVISE" > "$T/issue_comments_15.json"
ARCHITECT_MAX_TURNS= PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_eq "$(max_turns_of "$T/claude.args")" "60" "an empty variable (unset repository variable) falls back to 60"

echo "turn caps: a developer revision gets DEVELOPER_MAX_TURNS, else 80"
new_sandbox; echo '[{"number":30,"headRefName":"issue-16-scaffold"}]' > "$T/prs.json"
echo '[{"id":3,"submitted_at":"2026-10-26T10:00:00Z","state":"CHANGES_REQUESTED","body":"","author_association":"OWNER"}]' > "$T/pr_reviews_30.json"
DEVELOPER_MAX_TURNS=9 PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_eq "$(max_turns_of "$T/claude.args")" "9" "--max-turns follows DEVELOPER_MAX_TURNS"
new_sandbox; echo '[{"number":30,"headRefName":"issue-16-scaffold"}]' > "$T/prs.json"
echo '[{"id":3,"submitted_at":"2026-10-26T10:00:00Z","state":"CHANGES_REQUESTED","body":"","author_association":"OWNER"}]' > "$T/pr_reviews_30.json"
PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_eq "$(max_turns_of "$T/claude.args")" "80" "the developer default is 80"

echo "turn caps: the coordinator's own run gets 30, and its prompt carries the agent caps"
new_sandbox; echo "$READY" > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
ARCHITECT_MAX_TURNS=7 PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_eq "$(max_turns_of "$T/claude.args")" "30" "the coordinator run has --max-turns 30"
assert_grep "$T/claude.args" 'max-turns 7' "the prompt tells the coordinator the architect cap"
assert_grep "$T/claude.args" 'max-turns 80' "and the developer cap"
p=$(grep -n 'Items in Ready' "$T/claude.args" | head -1 | cut -d: -f1)
a=$(grep -n '^--max-turns$' "$T/claude.args" | head -1 | cut -d: -f1)
if [ -n "$p" ] && [ -n "$a" ] && [ "$p" -lt "$a" ]; then ok "--max-turns comes after the prompt"; else bad "--max-turns comes after the prompt"; fi

# --- exactly once (AC-6 of #4) ------------------------------------------------------------------
echo "once: a second pass, after the card moved to Architecture, does not launch claude for it"
new_sandbox; echo "$READY" > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_count "$T/claude.calls" "called" 1 "the first pass dispatches"
echo '{"items":[{"id":"I1","status":"Architecture","content":{"number":5,"title":"Architecture baseline"},"labels":["type:architecture"]}]}' > "$T/items.json"
PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_count "$T/claude.calls" "called" 1 "the second pass launched nothing more"
assert_grep "$T/out" 'nothing Ready' "and says so"

echo "once: with two Ready tasks and MAX_PARALLEL=1 the coordinator is told the limit"
new_sandbox; task_items 16 17 > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
body 16 'Blocked by: none'; body 17 'Blocked by: none'
MAX_PARALLEL=1 PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_count "$T/claude.calls" "called" 1 "one coordinator run, not one per task"
assert_grep "$T/claude.args" 'MAX_PARALLEL=1' "the prompt carries MAX_PARALLEL=1"

echo "once: an empty PROJECT_NUMBER fails fast with the existing message"
new_sandbox
PROJECT_NUMBER= bash "$SCRIPT" --once > "$T/out" 2>&1; rc=$?
if [ "$rc" -ne 0 ]; then ok "exits non-zero"; else bad "exits non-zero"; fi
assert_grep "$T/out" 'set PROJECT_NUMBER' "names PROJECT_NUMBER"
assert_count "$T/gh.log" '.' 0 "no gh call was made"

echo "allowlist: the repo scripts agents run are allowed with or without a bash prefix"
new_sandbox; echo "$READY" > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
for rule in 'scripts/board.sh \*' 'bash scripts/board.sh \*' 'scripts/pin-action.sh \*' 'bash scripts/pin-action.sh \*'; do
  assert_grep "$T/claude.args" "^Bash\\($rule\\)\$" "allows: $rule"
done
assert_no_grep "$T/claude.args" 'Bash\(bash scripts/\*\)' "but not every script under scripts/"
assert_no_grep "$T/claude.args" 'Bash\(bash \*\)' "and no unrestricted bash"

# --- coordinator.sh --revise: one revision for one item (event-driven, #39) ------------------------
PRS12='[{"number":12,"headRefName":"issue-9-feature-x"}]'
ARCH12='[{"number":12,"headRefName":"arch-4-plan"}]'
HUMAN_REVISE='[{"id":1,"created_at":"2026-10-28T10:00:00Z","body":"please fix /revise","author_association":"OWNER"}]'
revise() { PROJECT_NUMBER=1 bash "$SCRIPT" --revise "$@" > "$T/out" 2>&1; }

echo "revise: usage errors launch nothing"
new_sandbox; echo "$PRS12" > "$T/prs.json"; echo "$HUMAN_REVISE" > "$T/issue_comments_12.json"
for args in "" "pr" "pr abc" "pr 12x" "pr -3" "repo 12" "pr 12 --source nobody" "pr 12 --source" "pr 12 extra"; do
  rc=0; revise $args || rc=$?
  assert_eq "$rc" "2" "usage error (exit 2) for: --revise $args"
done
assert_count "$T/claude.calls" "called" 0 "claude is never launched on a usage error"
assert_count "$T/gh.log" 'pr comment' 0 "and nothing is posted"

echo "revise: a trusted /revise on a developer PR starts exactly one developer run"
new_sandbox; echo "$PRS12" > "$T/prs.json"; echo "$HUMAN_REVISE" > "$T/issue_comments_12.json"
rc=0; revise pr 12 || rc=$?
assert_eq "$rc" "0" "exits 0"
assert_count "$T/claude.calls" "called" 1 "one claude launch"
assert_grep "$T/claude.args" '^developer$' "the developer agent"
assert_grep "$T/claude.args" 'Revise after review' "in revision mode"
assert_grep "$T/claude.args" 'PR #12' "naming the PR"
assert_no_grep "$T/claude.args" 'please fix' "comment text is not pasted into the prompt"
assert_no_grep "$T/claude.args" 'source=reviewer|Source: reviewer' "the human source does not claim the reviewer as the source"
assert_count "$T/gh.log" 'pr comment 12 .*agent:ack' 1 "one acknowledgement"
assert_no_grep "$T/gh.log" 'source=reviewer' "which does not carry source=reviewer"
assert_no_grep "$T/gh.log" 'project item-list' "the board is not polled"
assert_grep "$T/git.log" 'worktree add .*origin/issue-9-feature-x' "works on the PR branch in a worktree"
assert_grep "$T/git.log" 'merge -q --no-edit origin/main' "with main merged in"
assert_eq "$(max_turns_of "$T/claude.args")" "80" "and the developer turn cap"

echo "revise: running it twice for the same feedback launches once"
echo '[{"id":1,"created_at":"2026-10-28T10:00:00Z","body":"please fix /revise","author_association":"OWNER"},{"id":2,"created_at":"2026-10-28T10:00:05Z","body":"Picked up. <!-- agent:ack -->","author_association":"OWNER"}]' > "$T/issue_comments_12.json"
rc=0; revise pr 12 || rc=$?
assert_eq "$rc" "0" "the duplicate exits 0"
assert_count "$T/claude.calls" "called" 1 "no second launch"
assert_count "$T/gh.log" 'pr comment' 1 "and no second acknowledgement"
assert_grep "$T/out" 'not pending' "the log says it is not pending"

echo "revise: an architect PR goes to the architect"
new_sandbox; echo "$ARCH12" > "$T/prs.json"; echo "$HUMAN_REVISE" > "$T/issue_comments_12.json"
revise pr 12
assert_count "$T/claude.calls" "called" 1 "one launch"
assert_grep "$T/claude.args" '^architect$' "the architect agent"
assert_eq "$(max_turns_of "$T/claude.args")" "60" "and the architect turn cap"

echo "revise: a plan on a feature issue is revised by the architect"
new_sandbox; echo '[{"number":5,"labels":[{"name":"type:feature"}]}]' > "$T/issues.json"
echo "$HUMAN_REVISE" > "$T/issue_comments_5.json"
revise issue 5 --source human
assert_count "$T/claude.calls" "called" 1 "one launch"
assert_grep "$T/claude.args" '^architect$' "the architect agent"
assert_grep "$T/claude.args" 'issue #5' "naming the issue"
assert_grep "$T/gh.log" 'issue comment 5 .*agent:ack' "acknowledged on the issue"

echo "revise: untrusted or agent feedback launches nothing"
new_sandbox; echo "$PRS12" > "$T/prs.json"
echo '[{"id":1,"created_at":"2026-10-28T10:00:00Z","body":"/revise","author_association":"NONE"}]' > "$T/issue_comments_12.json"
rc=0; revise pr 12 || rc=$?
assert_eq "$rc" "0" "association NONE: exits 0"
assert_count "$T/claude.calls" "called" 0 "association NONE: no launch"
assert_count "$T/gh.log" 'pr comment' 0 "association NONE: nothing posted"
new_sandbox; echo "$PRS12" > "$T/prs.json"
echo '[{"id":1,"created_at":"2026-10-28T10:00:00Z","body":"/revise <!-- agent:architect -->","author_association":"OWNER"}]' > "$T/issue_comments_12.json"
revise pr 12
assert_count "$T/claude.calls" "called" 0 "a /revise with an agent marker: no launch"
new_sandbox; echo "$PRS12" > "$T/prs.json"
revise pr 12
assert_count "$T/claude.calls" "called" 0 "no feedback at all: no launch"

echo "revise: a task-issue /revise counts for the linked PR"
new_sandbox; echo "$PRS12" > "$T/prs.json"; echo "$HUMAN_REVISE" > "$T/issue_comments_9.json"
revise pr 12
assert_count "$T/claude.calls" "called" 1 "one launch"
assert_grep "$T/claude.args" 'linked issue #9' "and the prompt names the linked issue"

echo "revise: a worktree failure is reported, with no launch"
new_sandbox; echo "$PRS12" > "$T/prs.json"; echo "$HUMAN_REVISE" > "$T/issue_comments_12.json"
STUB_WORKTREE_FAIL=1 revise pr 12
assert_count "$T/claude.calls" "called" 0 "not launched"
assert_grep "$T/gh.log" 'pr comment 12 .*Could not (create|start).*agent:ack' "reported on the PR"

echo "revise: needs the Actions identity like the rest of the coordinator"
new_sandbox; echo "$PRS12" > "$T/prs.json"; echo "$HUMAN_REVISE" > "$T/issue_comments_12.json"
GITHUB_ACTIONS=true PROJECT_TOKEN=ptok AGENT_GH_TOKEN=atok revise pr 12
assert_count "$T/claude.env" '^GH_TOKEN=atok ' 1 "the launch acts as the App"

# reviewer source
HEAD_SHA=sha-current
set_review() { # state sha [body] [login]
  jq -n --arg st "$1" --arg sha "$2" --arg body "${3-Please fix X. <!-- agent:reviewer -->}" --arg login "${4:-github-actions[bot]}" \
    '[{user:{login:$login},state:$st,commit_id:$sha,submitted_at:"2026-10-28T10:00:00Z",body:$body,author_association:"NONE"}]' > "$T/pr_reviews_12.json"
}
reviewer_pr() { # branch [state]
  new_sandbox
  jq -n --arg b "$1" --arg s "${2:-OPEN}" --arg sha "$HEAD_SHA" '{state:$s, headRefName:$b, headRefOid:$sha}' > "$T/pr_view_12.json"
  set_review CHANGES_REQUESTED "$HEAD_SHA"
}

echo "revise: --source reviewer runs the developer and counts the round"
reviewer_pr issue-9-feature-x
rc=0; revise pr 12 --source reviewer || rc=$?
assert_eq "$rc" "0" "exits 0"
assert_count "$T/claude.calls" "called" 1 "one launch"
assert_grep "$T/claude.args" '^developer$' "the developer agent"
assert_grep "$T/claude.args" 'Source: reviewer' "the prompt says the source is the reviewer"
assert_no_grep "$T/claude.args" 'Please fix X' "the review text is not pasted into the prompt"
assert_count "$T/gh.log" 'pr comment 12 .*<!-- agent:ack source=reviewer -->' 1 "the acknowledgement carries source=reviewer"
assert_grep "$T/git.log" 'worktree add .*origin/issue-9-feature-x' "works on the PR branch"
assert_no_grep "$T/gh.log" 'project item-list' "the board is not polled"

echo "revise: --source reviewer ignores what is not a current reviewer request"
reviewer_pr issue-9-feature-x; set_review CHANGES_REQUESTED sha-older
revise pr 12 --source reviewer; rc=$?
assert_eq "$rc" "0" "older head SHA: exits 0"
assert_count "$T/claude.calls" "called" 0 "older head SHA: no launch"
assert_count "$T/gh.log" 'pr comment' 0 "older head SHA: nothing posted"
reviewer_pr issue-9-feature-x; set_review APPROVED "$HEAD_SHA"
revise pr 12 --source reviewer
assert_count "$T/claude.calls" "called" 0 "an approval: no launch"
reviewer_pr issue-9-feature-x; set_review CHANGES_REQUESTED "$HEAD_SHA" "Fix it, no marker"
revise pr 12 --source reviewer
assert_count "$T/claude.calls" "called" 0 "a review without the reviewer marker: no launch"
reviewer_pr issue-9-feature-x; set_review CHANGES_REQUESTED "$HEAD_SHA" "Fix it. <!-- agent:reviewer -->" "someone-else"
revise pr 12 --source reviewer
assert_count "$T/claude.calls" "called" 0 "a marked review by anyone but github-actions[bot]: no launch"
reviewer_pr issue-9-feature-x CLOSED
revise pr 12 --source reviewer
assert_count "$T/claude.calls" "called" 0 "a closed PR: no launch"
reviewer_pr issue-9-feature-x
jq -n --arg sha "$HEAD_SHA" '[{user:{login:"github-actions[bot]"},state:"CHANGES_REQUESTED",commit_id:$sha,submitted_at:"2026-10-28T10:00:00Z",body:"a <!-- agent:reviewer -->"},{user:{login:"github-actions[bot]"},state:"APPROVED",commit_id:$sha,submitted_at:"2026-10-28T11:00:00Z",body:"b <!-- agent:reviewer -->"}]' > "$T/pr_reviews_12.json"
revise pr 12 --source reviewer
assert_count "$T/claude.calls" "called" 0 "changes requested, then approved: no launch"

echo "revise: --source reviewer runs once per review"
reviewer_pr issue-9-feature-x
echo '[{"id":7,"created_at":"2026-10-28T10:05:00Z","body":"Picked up. <!-- agent:ack source=reviewer -->","author_association":"OWNER"}]' > "$T/issue_comments_12.json"
revise pr 12 --source reviewer
assert_count "$T/claude.calls" "called" 0 "an acknowledgement newer than the review: no second launch"

echo "revise: --source reviewer is refused on an architect PR"
reviewer_pr arch-4-plan
rc=0; revise pr 12 --source reviewer || rc=$?
if [ "$rc" -ne 0 ]; then ok "exits non-zero"; else bad "exits non-zero"; fi
assert_count "$T/claude.calls" "called" 0 "no launch"
assert_grep "$T/out" 'issue-\*' "says only issue-* PRs"
reviewer_pr issue-9-feature-x
rc=0; revise issue 12 --source reviewer || rc=$?
assert_eq "$rc" "2" "--source reviewer with kind issue is a usage error"

echo "revise: local mode is unchanged (no flags still polls and dispatches)"
new_sandbox; echo "$READY" > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_count "$T/claude.calls" "called" 1 "--once still dispatches Ready work"
assert_grep "$T/gh.log" 'project item-list' "and reads the board"

# --- the `ready` label doorbell (#55, ADR 0015) -------------------------------------------------
card() { # id number status -> a one-card board, labelled so the coordinator dispatches it
  echo "{\"items\":[{\"id\":\"$1\",\"status\":\"$3\",\"content\":{\"number\":$2,\"title\":\"x\",\"url\":\"https://github.com/cooper3330/weekly-meal-planner/issues/$2\"},\"labels\":[\"type:architecture\"]}]}"
}
rl() { PROJECT_NUMBER=1 bash "$SCRIPT" --ready-label "$@" > "$T/out" 2>&1; echo $? > "$T/rc"; }

for st in "Backlog" "Spec Review" "Plan Review" "Blocked"; do
  echo "ready-label: a card in $st is set to Ready, the label removed, then a normal pass runs"
  new_sandbox; card I5 5 "$st" > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
  rl 5
  assert_eq "$(cat "$T/rc")" "0" "exits 0"
  assert_count "$T/gh.log" 'project item-edit .*--single-select-option-id o_ready' 1 "one item-edit, to the Ready option"
  assert_count "$T/gh.log" '^gh issue edit 5 .*--remove-label ready' 1 "the ready label is removed"
  assert_count "$T/claude.calls" "called" 1 "the pass dispatches the now-Ready item"
done

echo "ready-label: the card is set to Ready before the label is removed"
e=$(grep -n 'project item-edit' "$T/gh.log" | head -1 | cut -d: -f1)
r=$(grep -n 'remove-label ready' "$T/gh.log" | head -1 | cut -d: -f1)
if [ -n "$e" ] && [ -n "$r" ] && [ "$e" -lt "$r" ]; then ok "edit first, then remove the label"; else bad "edit first, then remove the label"; fi

echo "ready-label: a card already Ready is not edited"
new_sandbox; card I5 5 Ready > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
rl 5
assert_count "$T/gh.log" 'project item-edit' 0 "no item-edit"
assert_count "$T/gh.log" '^gh issue edit 5 .*--remove-label ready' 1 "label removed"
assert_count "$T/claude.calls" "called" 1 "the pass runs and dispatches it"

for st in "Architecture" "Implementation" "In Review" "Done"; do
  echo "ready-label: a card in $st is not moved backwards or dispatched"
  new_sandbox; card I5 5 "$st" > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
  rl 5
  assert_eq "$(cat "$T/rc")" "0" "exits 0"
  assert_count "$T/gh.log" 'project item-edit' 0 "no item-edit"
  assert_count "$T/gh.log" '^gh issue edit 5 .*--remove-label ready' 1 "label removed"
  assert_count "$T/claude.calls" "called" 0 "that item is not dispatched"
done

echo "ready-label: the pass still runs for other Ready work when the labelled card is in progress"
new_sandbox
echo '{"items":[{"id":"I5","status":"Implementation","content":{"number":5,"title":"x"},"labels":["type:architecture"]},{"id":"I6","status":"Ready","content":{"number":6,"title":"y"},"labels":["type:architecture"]}]}' > "$T/items.json"
echo "$STATUS_FIELDS" > "$T/fields.json"
rl 5
assert_count "$T/claude.calls" "called" 1 "one launch, for the other item"
assert_grep "$T/claude.args" '"number":6' "#6 is in the prompt"
assert_no_grep "$T/claude.args" '"number":5' "#5 is not"

echo "ready-label: an issue with no card gets the card added and set to Ready"
new_sandbox; echo '{"items":[]}' > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
rl 5
assert_count "$T/gh.log" 'project item-add 1 --owner cooper3330 --url .*/issues/5' 1 "the card is added"
assert_count "$T/gh.log" 'project item-edit .*o_ready' 1 "and set to Ready"
assert_count "$T/gh.log" '^gh issue edit 5 .*--remove-label ready' 1 "label removed"

echo "ready-label: a closed issue only loses the label"
new_sandbox; card I5 5 Backlog > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
echo '{"state":"closed"}' > "$T/issue_state_5.json"
rl 5
assert_eq "$(cat "$T/rc")" "0" "exits 0"
assert_count "$T/gh.log" '^gh issue edit 5 .*--remove-label ready' 1 "label removed"
assert_count "$T/gh.log" 'project ' 0 "no board call"
assert_count "$T/claude.calls" "called" 0 "no claude launch"

echo "ready-label: a pull request only loses the label"
new_sandbox; card I5 5 Backlog > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
echo '{"state":"open","pull_request":{"merged_at":null}}' > "$T/issue_state_5.json"
rl 5
assert_count "$T/gh.log" '^gh issue edit 5 .*--remove-label ready' 1 "label removed"
assert_count "$T/gh.log" 'project ' 0 "no board call"
assert_count "$T/claude.calls" "called" 0 "no claude launch"

echo "ready-label sweep: every pass releases an open issue still labelled ready (a missed event is recovered)"
new_sandbox; card I5 5 Backlog > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
echo '[{"number":5}]' > "$T/issues.json"
PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_count "$T/gh.log" 'project item-edit .*--single-select-option-id o_ready' 1 "one item-edit, to the Ready option"
assert_count "$T/gh.log" '^gh issue edit 5 .*--remove-label ready' 1 "the ready label is removed"
assert_count "$T/claude.calls" "called" 1 "the item is dispatched in the same pass"

echo "ready-label sweep: a closed issue still labelled ready only loses the label"
new_sandbox; card I5 5 Backlog > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
echo '[{"number":5}]' > "$T/issues.json"; echo '{"state":"closed"}' > "$T/issue_state_5.json"
PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_count "$T/gh.log" '^gh issue edit 5 .*--remove-label ready' 1 "label removed"
assert_count "$T/gh.log" 'project item-edit' 0 "no item-edit"
assert_count "$T/claude.calls" "called" 0 "no claude launch"

echo "ready-label sweep: a card already moving keeps its Status; the label goes"
new_sandbox; card I5 5 Implementation > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
echo '[{"number":5}]' > "$T/issues.json"
PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_count "$T/gh.log" 'project item-edit' 0 "no item-edit"
assert_count "$T/gh.log" '^gh issue edit 5 .*--remove-label ready' 1 "label removed"
assert_count "$T/claude.calls" "called" 0 "not dispatched"

echo "ready-label sweep: a labelled issue with no card is added alongside other Ready work in one run"
new_sandbox
echo '{"items":[{"id":"I6","status":"Ready","content":{"number":6,"title":"y"},"labels":["type:architecture"]}]}' > "$T/items.json"
echo "$STATUS_FIELDS" > "$T/fields.json"; echo '[{"number":5}]' > "$T/issues.json"
PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_count "$T/gh.log" 'project item-add 1 --owner cooper3330 --url .*/issues/5' 1 "the labelled issue gets a card"
assert_count "$T/claude.calls" "called" 1 "one coordinator run covers both"

echo "ready-label sweep: with no labelled issues nothing is edited"
new_sandbox; echo "$READY" > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_count "$T/gh.log" 'remove-label ready' 0 "no label is touched"

echo "ready-label: a bad number is a usage error that launches and writes nothing"
for bad_arg in abc 0 "" "-3" "5x"; do
  new_sandbox; card I5 5 Backlog > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
  rl "$bad_arg"
  if [ "$(cat "$T/rc")" -ne 0 ]; then ok "'$bad_arg': exits non-zero"; else bad "'$bad_arg': exits non-zero"; fi
  assert_grep "$T/out" 'usage' "'$bad_arg': prints usage"
  assert_count "$T/gh.log" 'issue edit|item-edit|item-add|comment' 0 "'$bad_arg': no gh write call"
  assert_count "$T/claude.calls" "called" 0 "'$bad_arg': no claude launch"
done
new_sandbox
PROJECT_NUMBER=1 bash "$SCRIPT" --ready-label > "$T/out" 2>&1; rc=$?
if [ "$rc" -ne 0 ]; then ok "no argument at all: exits non-zero"; else bad "no argument at all: exits non-zero"; fi
assert_count "$T/claude.calls" "called" 0 "no argument: no claude launch"

echo "ready-label: running it twice dispatches once"
new_sandbox; card I5 5 Backlog > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
rl 5
assert_count "$T/claude.calls" "called" 1 "the first run dispatches"
card I5 5 Architecture > "$T/items.json"   # what the dispatched architect did to the card
rl 5
assert_count "$T/claude.calls" "called" 1 "the second run launches nothing more"
assert_count "$T/gh.log" 'project item-edit' 1 "and edits nothing"

echo "ready-label: a failed lookup of the card changes nothing (no edit, no launch)"
new_sandbox; card I5 5 Backlog > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
printf '#!/usr/bin/env bash\necho "gh $*" >> "$STUB_DIR/gh.log"\ncase "$1 $2" in "project item-list") exit 1;; "repo view") echo cooper3330/weekly-meal-planner;; "api"*) echo "open issue";; esac\nexit 0\n' > "$T/bin/gh"
rl 5
# The exit code is deliberately not asserted: --ready-label carries on into the pass, which fails on the same stubbed item-list.
assert_grep "$T/out" 'could not read the card for #5; changing nothing' "the failure is reported, naming the issue"
assert_count "$T/gh.log" 'item-edit' 0 "no card edit"
assert_count "$T/gh.log" '--remove-label ready' 0 "the label is kept, so the next pass's sweep retries it"
assert_count "$T/claude.calls" "called" 0 "no launch"

finish
