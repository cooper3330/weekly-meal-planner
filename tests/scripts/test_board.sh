#!/usr/bin/env bash
# Tests for scripts/board.sh (offline, stubbed gh).
. "$(dirname "$0")/lib.sh"
SCRIPT="$ROOT/scripts/board.sh"
# Hermetic: a PROJECT_TOKEN in the caller's environment must not leak into these tests.
unset PROJECT_TOKEN

STATUS_FIELDS='{"fields":[{"id":"PVTF_title","name":"Title","type":"ProjectV2Field"},{"id":"PVTSSF_status","name":"Status","type":"ProjectV2SingleSelectField","options":[{"id":"o_back","name":"Backlog"},{"id":"o_spec","name":"Spec Review"},{"id":"o_ready","name":"Ready"},{"id":"o_arch","name":"Architecture"},{"id":"o_plan","name":"Plan Review"},{"id":"o_impl","name":"Implementation"},{"id":"o_review","name":"In Review"},{"id":"o_done","name":"Done"},{"id":"o_blocked","name":"Blocked"}]}],"totalCount":2}'
ITEMS='{"items":[{"id":"PVTI_32","status":"Backlog","content":{"number":32,"title":"CI","url":"https://github.com/cooper3330/weekly-meal-planner/issues/32"}}]}'
URL32="https://github.com/cooper3330/weekly-meal-planner/issues/32"
URL40="https://github.com/cooper3330/weekly-meal-planner/issues/40"

# run ARGS...: runs board.sh, leaving stdout in $T/out, stderr in $T/err and the exit code in $T/rc.
run() { PROJECT_NUMBER=1 bash "$SCRIPT" "$@" > "$T/out" 2> "$T/err"; echo $? > "$T/rc"; }
nonzero() { if [ "$(cat "$T/rc")" -ne 0 ]; then ok "$1"; else bad "$1"; fi; }

echo "board: status sets the option ID of the named Status on the issue's card"
new_sandbox; echo "$ITEMS" > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
run status 32 Ready
assert_eq "$(cat "$T/rc")" "0" "exits 0"
assert_grep "$T/gh.log" 'project item-edit --id PVTI_32 --project-id PVT_abc --field-id PVTSSF_status --single-select-option-id o_ready' "edits the card with the project, field and Ready option IDs"
assert_count "$T/gh.log" 'project item-edit' 1 "exactly one edit"
assert_no_grep "$T/gh.log" 'project item-add' "does not add a card that is already on the board"

echo "board: a Status name with a space"
new_sandbox; echo "$ITEMS" > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
run status 32 "In Review"
assert_grep "$T/gh.log" 'single-select-option-id o_review' "In Review resolves to its own option"

echo "board: status adds the card first when the issue is not on the board"
new_sandbox; echo '{"items":[]}' > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
STUB_ITEM_ID=PVTI_new run status 32 Ready
assert_eq "$(cat "$T/rc")" "0" "exits 0"
assert_grep "$T/gh.log" "project item-add 1 --owner cooper3330 --url $URL32" "adds the issue by URL for this repo"
assert_grep "$T/gh.log" 'item-edit --id PVTI_new .*o_ready' "then edits the newly added card"

echo "board: current prints the card's Status name"
new_sandbox; echo "$ITEMS" > "$T/items.json"
run current 32
assert_eq "$(cat "$T/rc")" "0" "exits 0"
assert_eq "$(cat "$T/out")" "Backlog" "prints Backlog for a card in Backlog"
assert_no_grep "$T/gh.log" 'item-edit|item-add' "reads only"

echo "board: current with no card prints nothing and exits non-zero"
new_sandbox; echo "$ITEMS" > "$T/items.json"
run current 99
nonzero "exits non-zero"
assert_eq "$(cat "$T/out")" "" "prints nothing"

echo "board: current rejects a bad number"
new_sandbox; echo "$ITEMS" > "$T/items.json"
run current abc
nonzero "exits non-zero for a non-number"
run current
nonzero "exits non-zero without an argument"

echo "board: current uses PROJECT_TOKEN for the gh project call"
new_sandbox; echo "$ITEMS" > "$T/items.json"
PROJECT_TOKEN=ptok run current 32
assert_grep "$T/gh.env" '^project item-list GH_TOKEN=ptok$' "item-list ran with PROJECT_TOKEN"

echo "board: an unknown Status is rejected before any edit"
new_sandbox; echo "$ITEMS" > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
run status 32 Redy
nonzero "exits non-zero"
assert_grep "$T/err" 'Redy' "names the bad Status"
assert_no_grep "$T/gh.log" 'project item-edit' "makes no edit call"
assert_no_grep "$T/gh.log" 'project item-add' "makes no add call"

echo "board: a board whose Status field lacks an option is rejected, naming the option"
new_sandbox; echo "$ITEMS" > "$T/items.json"
echo '{"fields":[{"id":"PVTSSF_status","name":"Status","options":[{"id":"o_back","name":"Backlog"},{"id":"o_ready","name":"Ready"}]}]}' > "$T/fields.json"
run status 32 Ready
nonzero "exits non-zero"
assert_grep "$T/err" 'missing options: .*Plan Review' "names a missing option"
assert_no_grep "$T/err" 'missing options: .*Ready' "does not name an option that exists"
assert_no_grep "$T/gh.log" 'project item-edit' "makes no edit call"

echo "board: a board without a Status field is rejected, pointing at the setup script"
new_sandbox; echo "$ITEMS" > "$T/items.json"
echo '{"fields":[{"id":"PVTF_title","name":"Title","type":"ProjectV2Field"}]}' > "$T/fields.json"
run status 32 Ready
nonzero "exits non-zero"
assert_grep "$T/err" 'Status' "names the missing field"
assert_grep "$T/err" 'setup-project' "points at the setup script"

echo "board: check validates the board and edits nothing"
new_sandbox; echo "$STATUS_FIELDS" > "$T/fields.json"
run check
assert_eq "$(cat "$T/rc")" "0" "a complete board passes"
assert_no_grep "$T/gh.log" 'project item-(edit|add)' "no writes"
new_sandbox
run check
nonzero "the default board (no nine options) fails"

echo "board: add prints the new item ID"
new_sandbox; echo '{"items":[]}' > "$T/items.json"
STUB_ITEM_ID=PVTI_new run add "$URL40"
assert_eq "$(cat "$T/rc")" "0" "exits 0"
assert_eq "$(cat "$T/out")" "PVTI_new" "prints the item ID and nothing else"
assert_grep "$T/gh.log" "project item-add 1 --owner cooper3330 --url $URL40" "adds by URL"

echo "board: add is idempotent for an issue already on the board"
new_sandbox; echo "$ITEMS" > "$T/items.json"
run add "$URL32"
assert_eq "$(cat "$T/rc")" "0" "exits 0"
assert_eq "$(cat "$T/out")" "PVTI_32" "returns the existing item ID"

echo "board: usage errors"
new_sandbox
run
nonzero "no subcommand fails"
run frobnicate
nonzero "unknown subcommand fails"
run status 32
nonzero "status without a Status name fails"
run add
nonzero "add without a URL fails"
run status abc Ready
nonzero "a non-numeric issue number fails"
assert_no_grep "$T/gh.log" 'project item-edit' "no edit call on bad usage"

echo "board: PROJECT_NUMBER is required; OWNER overrides the repository owner"
new_sandbox; echo "$ITEMS" > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
env -u PROJECT_NUMBER bash "$SCRIPT" status 32 Ready > "$T/out" 2> "$T/err"; echo $? > "$T/rc"
nonzero "exits non-zero without PROJECT_NUMBER"
assert_grep "$T/err" 'PROJECT_NUMBER' "names the missing variable"
OWNER=someone-else run status 32 Ready
assert_grep "$T/gh.log" 'project view 1 --owner someone-else' "OWNER is used for the project"
assert_no_grep "$T/gh.log" '@me' "never uses @me"

echo "board: PROJECT_TOKEN is used for every gh project call"
new_sandbox; echo '{"items":[]}' > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
GH_TOKEN=agenttok PROJECT_TOKEN=ptok run status 32 Ready
assert_eq "$(cat "$T/rc")" "0" "exits 0"
assert_grep "$T/gh.env" '^project ' "gh project calls happened"
assert_no_grep "$T/gh.env" '^project [a-z-]+ GH_TOKEN=(agenttok|UNSET)$' "no gh project call ran with another token"
assert_count "$T/gh.env" '^project [a-z-]+ GH_TOKEN=ptok$' "$(grep -c '^project ' "$T/gh.env")" "every gh project call saw GH_TOKEN=ptok"

echo "board: without PROJECT_TOKEN, GH_TOKEN passes through unchanged"
new_sandbox; echo "$ITEMS" > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
GH_TOKEN=ambient run status 32 Ready
assert_no_grep "$T/gh.env" '^project [a-z-]+ GH_TOKEN=(ptok|UNSET)$' "the ambient token is kept"
assert_grep "$T/gh.env" '^project item-edit GH_TOKEN=ambient$' "the edit used the ambient token"
new_sandbox; echo "$ITEMS" > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
env -u GH_TOKEN -u PROJECT_TOKEN PROJECT_NUMBER=1 bash "$SCRIPT" status 32 Ready > /dev/null 2>&1
assert_no_grep "$T/gh.env" 'GH_TOKEN=(ptok|ambient)' "with no tokens at all, the ambient gh login is used"

echo "board: PROJECT_TOKEN is never echoed"
new_sandbox; echo "$ITEMS" > "$T/items.json"; echo "$STATUS_FIELDS" > "$T/fields.json"
PROJECT_TOKEN=sentinel-secret-123 run status 32 Ready
assert_no_grep "$T/out" 'sentinel-secret-123' "not in stdout"
assert_no_grep "$T/err" 'sentinel-secret-123' "not in stderr"
assert_no_grep "$T/gh.log" 'sentinel-secret-123' "not on any gh command line"

finish
