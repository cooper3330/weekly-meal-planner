#!/usr/bin/env bash
# Tests for scripts/setup-project.sh (offline, stubbed gh).
. "$(dirname "$0")/lib.sh"
SCRIPT="$ROOT/scripts/setup-project.sh"
ALL_MILESTONES='[{"title":"M1 Foundation"},{"title":"M2 Weekly planning loop"},{"title":"M3 Shopping and cooking"},{"title":"M4 Learning"}]'

echo "setup: fresh repository"
new_sandbox
bash "$SCRIPT" > "$T/out" 2>&1; rc=$?
assert_eq "$rc" "0" "exits 0"
assert_count "$T/gh.log" 'label create' 4 "creates the four type labels"
assert_count "$T/gh.log" 'milestones -f title=' 4 "creates four milestones"
assert_count "$T/gh.log" 'project create' 1 "creates one project"
assert_grep "$T/gh.log" 'project create --owner cooper3330' "creates the project as the repo owner"
assert_grep "$T/gh.log" 'project link 7 --owner cooper3330 --repo cooper3330/weekly-meal-planner' "links with an owner that matches the repo owner"
assert_count "$T/gh.log" 'project field-create 7 .*--name Stage' 1 "creates the Stage field"
assert_no_grep "$T/gh.log" '@me' "never uses @me"
assert_grep "$T/out" 'PROJECT_NUMBER=7' "prints the project number to export"

echo "setup: safe to re-run"
new_sandbox
echo '{"projects":[{"number":3,"title":"Weekly Meal Planner"},{"number":4,"title":"Other"}],"totalCount":2}' > "$T/projects.json"
echo '{"fields":[{"name":"Title"},{"name":"Status"},{"name":"Stage"}],"totalCount":3}' > "$T/fields.json"
echo "$ALL_MILESTONES" > "$T/milestones.json"
bash "$SCRIPT" > "$T/out" 2>&1; rc=$?
assert_eq "$rc" "0" "exits 0"
assert_count "$T/gh.log" 'project create' 0 "does not create a second project"
assert_count "$T/gh.log" 'field-create' 0 "does not create a second Stage field"
assert_count "$T/gh.log" 'milestones -f title=' 0 "does not recreate milestones"
assert_grep "$T/gh.log" 'project link 3 ' "reuses the existing project number"
assert_grep "$T/out" 'PROJECT_NUMBER=3' "prints the existing project number"

echo "setup: partial milestones"
new_sandbox
echo '[{"title":"M1 Foundation"}]' > "$T/milestones.json"
bash "$SCRIPT" > "$T/out" 2>&1
assert_count "$T/gh.log" 'milestones -f title=' 3 "creates only the missing milestones"

echo "setup: link failure is not fatal"
new_sandbox
STUB_LINK_FAIL=1 bash "$SCRIPT" > "$T/out" 2>&1; rc=$?
assert_eq "$rc" "0" "exits 0"
assert_grep "$T/out" 'could not link' "warns that linking failed"
assert_count "$T/gh.log" 'field-create' 1 "still creates the Stage field"

echo "setup: owner and repo overrides"
new_sandbox
OWNER=someone REPO=someone/other bash "$SCRIPT" > "$T/out" 2>&1
assert_grep "$T/gh.log" 'project create --owner someone' "honors OWNER"
assert_grep "$T/gh.log" 'label create .* --repo someone/other' "honors REPO"

finish
