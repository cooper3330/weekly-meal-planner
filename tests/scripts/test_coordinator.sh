#!/usr/bin/env bash
# Tests for scripts/coordinator.sh (offline, stubbed gh/claude/date).
. "$(dirname "$0")/lib.sh"
SCRIPT="$ROOT/scripts/coordinator.sh"

STAGE_FIELDS='{"fields":[{"id":"PVTF_title","name":"Title","type":"ProjectV2Field"},{"id":"PVTSSF_stage","name":"Stage","type":"ProjectV2SingleSelectField","options":[{"id":"o_back","name":"Backlog"},{"id":"o_ready","name":"Ready"},{"id":"o_arch","name":"Architecture"},{"id":"o_impl","name":"Implementation"},{"id":"o_blocked","name":"Blocked"}]}],"totalCount":2}'

READY='{"items":[
 {"id":"I1","stage":"Ready","content":{"number":5,"title":"Architecture baseline"},"labels":["type:architecture"]},
 {"id":"I2","stage":"Backlog","content":{"number":6,"title":"Profile"},"labels":["type:feature"]},
 {"id":"I3","stage":"Ready","content":{"title":"A draft note with no issue number"}}]}'

echo "coordinator: dispatches Ready issues with an explicit tool allowlist"
new_sandbox; echo "$READY" > "$T/items.json"; echo "$STAGE_FIELDS" > "$T/fields.json"
PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1; rc=$?
assert_eq "$rc" "0" "exits 0"
assert_count "$T/claude.calls" "called" 1 "claude is invoked exactly once"
assert_grep "$T/claude.args" '^-p$' "runs headless with -p"
assert_grep "$T/claude.args" '^coordinator$' "uses the coordinator agent"
assert_grep "$T/claude.args" '^--allowedTools$' "passes --allowedTools"
assert_grep "$T/claude.args" 'Bash\(gh issue view \*\)' "allows reading issues"
assert_grep "$T/claude.args" 'Bash\(gh project item-edit \*\)' "allows setting the Stage"
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
new_sandbox; echo '{"items":[{"id":"I2","stage":"Backlog","content":{"number":6,"title":"Profile"},"labels":[]}]}' > "$T/items.json"
PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1; rc=$?
assert_eq "$rc" "0" "exits 0"
assert_count "$T/claude.calls" "called" 0 "claude is not invoked"
assert_grep "$T/out" 'nothing Ready' "reports that nothing is Ready"

echo "coordinator: extra allowed tools"
new_sandbox; echo "$READY" > "$T/items.json"; echo "$STAGE_FIELDS" > "$T/fields.json"
EXTRA_ALLOWED_TOOLS='Bash(npm test),Bash(ls *)' PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_grep "$T/claude.args" '^Bash\(npm test\)$' "EXTRA_ALLOWED_TOOLS adds a rule containing a space"
assert_grep "$T/claude.args" '^Bash\(ls \*\)$' "EXTRA_ALLOWED_TOOLS adds a second rule"

echo "coordinator: requires PROJECT_NUMBER"
new_sandbox
env -u PROJECT_NUMBER bash "$SCRIPT" --once > "$T/out" 2>&1; rc=$?
if [ "$rc" -ne 0 ]; then ok "exits non-zero"; else bad "exits non-zero"; fi
assert_grep "$T/out" 'PROJECT_NUMBER' "names the missing variable"

echo "coordinator: resolves the board IDs itself and hands them to the agent"
new_sandbox; echo "$READY" > "$T/items.json"; echo "$STAGE_FIELDS" > "$T/fields.json"
PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_grep "$T/claude.args" 'PVT_abc' "prompt carries the project node ID"
assert_grep "$T/claude.args" 'PVTSSF_stage' "prompt carries the Stage field ID"
assert_grep "$T/claude.args" '"Implementation":"o_impl"' "prompt carries the Implementation option ID"
assert_grep "$T/claude.args" '"Architecture":"o_arch"' "prompt carries the Architecture option ID"
assert_grep "$T/claude.args" '"item_id":"I1"' "each Ready item carries its board item ID"
assert_grep "$T/claude.args" 'item-edit --id .*--project-id .*--field-id .*--single-select-option-id' "prompt shows the exact item-edit command shape"
assert_grep "$T/claude.args" 'Bash\(gh project view \*\)' "allows gh project view"
assert_grep "$T/claude.args" 'Bash\(gh project list \*\)' "allows gh project list"

echo "coordinator: fails clearly when the Stage field is missing"
new_sandbox; echo "$READY" > "$T/items.json"
PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1; rc=$?
if [ "$rc" -ne 0 ]; then ok "exits non-zero"; else bad "exits non-zero"; fi
assert_count "$T/claude.calls" "called" 0 "claude is not invoked"
assert_grep "$T/out" 'Stage' "names the missing field"
assert_grep "$T/out" 'setup-project' "points at the setup script"

echo "coordinator: makes no board lookups when nothing is Ready"
new_sandbox; echo '{"items":[]}' > "$T/items.json"
PROJECT_NUMBER=1 bash "$SCRIPT" --once > "$T/out" 2>&1
assert_no_grep "$T/gh.log" 'project (view|field-list)' "no project view or field-list calls"

finish
