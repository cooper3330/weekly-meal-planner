#!/usr/bin/env bash
# scripts/revise-target.sh maps a GitHub event to the item revise.yml revises (offline, gh stubbed).
. "$(dirname "$0")/lib.sh"
cd "$ROOT" || exit 1
SCRIPT="$ROOT/scripts/revise-target.sh"
MODE=$(git ls-files -s -- scripts/revise-target.sh | awk '{print $1}')

# target NAME PAYLOAD-JSON: runs the script for an event; sets OUT and RC.
target() {
  printf '%s' "$2" > "$T/event.json"
  RC=0
  OUT=$(GITHUB_EVENT_NAME="$1" GITHUB_EVENT_PATH="$T/event.json" REPO=cooper3330/weekly-meal-planner bash "$SCRIPT" 2>"$T/err") || RC=$?
}
expect() { local got; got=$(tr '\n' ' ' <<<"$OUT"); assert_eq "${got% }" "${1% }" "$2"; }

echo "revise-target: a comment on a PR"
new_sandbox; echo '{"state":"OPEN","headRefName":"issue-9-x"}' > "$T/pr_view_12.json"
target issue_comment '{"issue":{"number":12,"pull_request":{"url":"x"},"labels":[]}}'
expect "kind=pr number=12 source=human " "a developer PR maps to itself"
echo '{"headRefName":"arch-4-plan"}' > "$T/pr_view_12.json"
target issue_comment '{"issue":{"number":12,"pull_request":{"url":"x"},"labels":[]}}'
expect "kind=pr number=12 source=human " "an architect PR maps to itself"
echo '{"headRefName":"dependabot/uv/x"}' > "$T/pr_view_12.json"
target issue_comment '{"issue":{"number":12,"pull_request":{"url":"x"},"labels":[]}}'
expect "" "any other branch maps to nothing"
echo '{"headRefName":"issue-9-x","isCrossRepository":true}' > "$T/pr_view_12.json"
target issue_comment '{"issue":{"number":12,"pull_request":{"url":"x"},"labels":[]}}'
expect "" "a fork PR maps to nothing"

echo "revise-target: a comment on a task or bug issue goes to its open PR"
new_sandbox; echo '[{"number":30,"headRefName":"issue-16-scaffold"},{"number":31,"headRefName":"issue-160-other"},{"number":32,"headRefName":"arch-1-x"}]' > "$T/prs.json"
target issue_comment '{"issue":{"number":16,"labels":[{"name":"type:task"}]}}'
expect "kind=pr number=30 source=human " "a task maps to the PR on issue-16-*, not issue-160-*"
target issue_comment '{"issue":{"number":16,"labels":[{"name":"type:bug"},{"name":"x"}]}}'
expect "kind=pr number=30 source=human " "a bug maps to its PR too"
target issue_comment '{"issue":{"number":17,"labels":[{"name":"type:task"}]}}'
expect "" "a task with no open PR launches nothing"
echo "[]" > "$T/prs.json"
target issue_comment '{"issue":{"number":16,"labels":[{"name":"type:task"}]}}'
expect "" "no open PRs at all: nothing"
echo '[{"number":30,"headRefName":"issue-16-scaffold","isCrossRepository":true}]' > "$T/prs.json"
target issue_comment '{"issue":{"number":16,"labels":[{"name":"type:task"}]}}'
expect "" "a fork's PR does not count"

echo "revise-target: a comment on a feature or architecture issue revises the plan"
new_sandbox
target issue_comment '{"issue":{"number":4,"labels":[{"name":"type:feature"}]}}'
expect "kind=issue number=4 source=human " "a feature maps to itself as an issue"
target issue_comment '{"issue":{"number":5,"labels":[{"name":"type:architecture"}]}}'
expect "kind=issue number=5 source=human " "an architecture issue too"
target issue_comment '{"issue":{"number":6,"labels":[{"name":"question"}]}}'
expect "" "an issue of another type maps to nothing"
target issue_comment '{"issue":{"number":7,"labels":[]}}'
expect "" "an unlabelled issue maps to nothing"

echo "revise-target: a review or a review comment goes to its PR"
new_sandbox
for ev in pull_request_review pull_request_review_comment; do
  target "$ev" '{"pull_request":{"number":12,"head":{"ref":"issue-9-x","repo":{"full_name":"cooper3330/weekly-meal-planner"}}}}'
  expect "kind=pr number=12 source=human " "$ev on a developer PR"
  target "$ev" '{"pull_request":{"number":13,"head":{"ref":"arch-4-x","repo":{"full_name":"cooper3330/weekly-meal-planner"}}}}'
  expect "kind=pr number=13 source=human " "$ev on an architect PR"
  target "$ev" '{"pull_request":{"number":14,"head":{"ref":"feature/x","repo":{"full_name":"cooper3330/weekly-meal-planner"}}}}'
  expect "" "$ev on another branch: nothing"
  target "$ev" '{"pull_request":{"number":15,"head":{"ref":"issue-9-x","repo":{"full_name":"someone/fork"}}}}'
  expect "" "$ev on a fork: nothing"
done

echo "revise-target: workflow_dispatch passes validated inputs through"
new_sandbox
target workflow_dispatch '{"inputs":{"kind":"pr","number":"12","source":"reviewer"}}'
expect "kind=pr number=12 source=reviewer " "pr 12 from the reviewer"
target workflow_dispatch '{"inputs":{"kind":"issue","number":"5"}}'
expect "kind=issue number=5 source=human " "the source defaults to human"
for bad in '{"inputs":{"kind":"repo","number":"5"}}' '{"inputs":{"kind":"pr","number":"5; rm"}}' '{"inputs":{"kind":"pr","number":""}}' \
           '{"inputs":{"kind":"pr","number":"5","source":"bot"}}' '{"inputs":{"kind":"issue","number":"5","source":"reviewer"}}'; do
  target workflow_dispatch "$bad"
  expect "" "invalid input prints nothing: $bad"
  assert_eq "$RC" "2" "and is a usage error"
done

echo "revise-target: other events are refused"
new_sandbox
target push '{}'
assert_eq "$RC" "2" "an unsupported event is an error"

echo "revise-target: structure"
if [ "$MODE" = 100755 ]; then ok "revise-target.sh is executable"; else bad "revise-target.sh is executable"; fi

finish
