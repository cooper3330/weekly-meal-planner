#!/usr/bin/env bash
# Tests for scripts/coordinator.sh --revise pr N --source ci --run R (#56; offline, stubbed gh/claude).
# A failed `check` job on an agent PR starts the developer, within a budget shared with the reviewer.
. "$(dirname "$0")/lib.sh"
SCRIPT="$ROOT/scripts/coordinator.sh"
revise() { PROJECT_NUMBER=1 bash "$SCRIPT" --revise "$@" > "$T/out" 2>&1; }

CI_HEAD=sha-current
GOOD_JOBS='{"jobs":[{"name":"check","conclusion":"failure","html_url":"https://github.com/o/r/actions/runs/900/job/1","steps":[{"name":"Set up job","conclusion":"success"},{"name":"Run make check","conclusion":"failure"}]}]}'
ci_run() { # conclusion head-sha [jobs-json]
  jq -n --arg c "$1" --arg sha "$2" '{head_sha:$sha, conclusion:$c, event:"pull_request", name:"CI"}' > "$T/run_900.json"
  if [ $# -ge 3 ]; then echo "$3" > "$T/run_jobs_900.json"; else echo "$GOOD_JOBS" > "$T/run_jobs_900.json"; fi
}
ci_pr() { # branch [state] [labels-json]
  new_sandbox
  jq -n --arg b "$1" --arg s "${2:-OPEN}" --arg sha "$CI_HEAD" --argjson l "${3:-[]}" \
    '{state:$s, headRefName:$b, headRefOid:$sha, labels:($l | map({name:.}))}' > "$T/pr_view_12.json"
  ci_run failure "$CI_HEAD"
  jq -n '{workflow_runs:[{name:"CI",status:"completed",conclusion:"success"}]}' > "$T/main_runs.json"
}
acks() { # prior comment bodies, one per argument -> issue_comments_12.json
  printf '%s\n' "$@" | jq -R '{body:., created_at:"2026-10-28T10:00:00Z"}' | jq -s . > "$T/issue_comments_12.json"
}
RACK='Picked up. <!-- agent:ack source=reviewer -->'
CACK='Picked up. [run 801] <!-- agent:ack source=ci -->'
ack_list() { # string of R and C characters -> ACK_LIST array
  ACK_LIST=(); local k="$1" i=0
  while [ "$i" -lt "${#k}" ]; do
    case "${k:$i:1}" in R) ACK_LIST+=("$RACK") ;; C) ACK_LIST+=("Picked up. [run 80$i] <!-- agent:ack source=ci -->") ;; esac
    i=$((i + 1))
  done
}

echo "revise: --source ci runs the developer on the failed run"
ci_pr issue-9-feature-x
rc=0; revise pr 12 --source ci --run 900 || rc=$?
assert_eq "$rc" "0" "exits 0"
assert_count "$T/claude.calls" "called" 1 "one launch"
assert_grep "$T/claude.args" '^developer$' "the developer agent"
assert_grep "$T/claude.args" 'Source: CI' "the prompt says the source is CI"
assert_grep "$T/claude.args" 'run 900' "the prompt names the failed run"
assert_count "$T/gh.log" 'pr comment 12 .*<!-- agent:ack source=ci -->' 1 "one acknowledgement carries source=ci"
assert_grep "$T/git.log" 'worktree add .*origin/issue-9-feature-x' "works on the PR branch"
assert_no_grep "$T/gh.log" 'project item-list' "the board is not polled"

echo "revise: --source ci runs once per run"
ci_pr issue-9-feature-x; acks 'Picked up. [run 900] <!-- agent:ack source=ci -->'
revise pr 12 --source ci --run 900
assert_count "$T/claude.calls" "called" 0 "a run that was already acknowledged: no second launch"
assert_count "$T/gh.log" 'pr comment' 0 "and nothing is posted"

echo "revise: --source ci ignores a stale or non-failing run"
ci_pr issue-9-feature-x; ci_run failure sha-older
rc=0; revise pr 12 --source ci --run 900 || rc=$?
assert_eq "$rc" "0" "older head SHA: exits 0"
assert_count "$T/claude.calls" "called" 0 "older head SHA: no launch"
assert_count "$T/gh.log" 'pr comment' 0 "older head SHA: nothing posted"
ci_pr issue-9-feature-x; ci_run cancelled "$CI_HEAD"
revise pr 12 --source ci --run 900
assert_count "$T/claude.calls" "called" 0 "a cancelled run: no launch"
assert_count "$T/gh.log" 'pr comment' 0 "a cancelled run: nothing posted"
ci_pr issue-9-feature-x; ci_run success "$CI_HEAD"
revise pr 12 --source ci --run 900
assert_count "$T/claude.calls" "called" 0 "a successful run: no launch"
ci_pr issue-9-feature-x; ci_run failure "$CI_HEAD" '{"jobs":[{"name":"check","conclusion":"failure","steps":[]}]}'
revise pr 12 --source ci --run 900
assert_count "$T/claude.calls" "called" 0 "a check job that never ran a step: no launch"
assert_count "$T/gh.log" 'pr comment' 0 "a check job that never ran a step: nothing posted"
ci_pr issue-9-feature-x; ci_run failure "$CI_HEAD" '{"jobs":[{"name":"check","conclusion":"failure","steps":[{"name":"Set up job","conclusion":"failure"}]}]}'
revise pr 12 --source ci --run 900
assert_count "$T/claude.calls" "called" 0 "a check job that failed while setting up the runner: no launch"
ci_pr issue-9-feature-x; ci_run failure "$CI_HEAD" '{"jobs":[{"name":"other","conclusion":"failure","steps":[{"name":"x","conclusion":"failure"}]}]}'
revise pr 12 --source ci --run 900
assert_count "$T/claude.calls" "called" 0 "another job failed, not check: no launch"

echo "revise: --source ci stops when main is red"
ci_pr issue-9-feature-x
jq -n '{workflow_runs:[{name:"CI",status:"completed",conclusion:"failure"}]}' > "$T/main_runs.json"
rc=0; revise pr 12 --source ci --run 900 || rc=$?
assert_eq "$rc" "0" "exits 0"
assert_count "$T/claude.calls" "called" 0 "no launch"
assert_count "$T/gh.log" 'pr comment 12 ' 1 "exactly one comment"
assert_grep "$T/gh.log" 'pr comment 12 .*main.*<!-- agent:ack source=ci -->' "it says main is red, signed source=ci"
ci_pr issue-9-feature-x
jq -n '{workflow_runs:[{name:"CI",status:"completed",conclusion:"cancelled"},{name:"CI",status:"completed",conclusion:"success"}]}' > "$T/main_runs.json"
revise pr 12 --source ci --run 900
assert_count "$T/claude.calls" "called" 1 "a cancelled newest run on main is skipped; main is green"
ci_pr issue-9-feature-x
jq -n '{workflow_runs:[{name:"CI",status:"completed",conclusion:"failure"}]}' > "$T/main_runs.json"
acks 'Main is red. [run 900] <!-- agent:ack source=ci -->'
revise pr 12 --source ci --run 900
assert_count "$T/gh.log" 'pr comment' 0 "a repeated event on a red main posts nothing more"

echo "revise: --source ci refuses the wrong PRs and bad numbers"
ci_pr feature-x
rc=0; revise pr 12 --source ci --run 900 || rc=$?
assert_eq "$rc" "0" "a non-agent branch: exits 0"
assert_count "$T/claude.calls" "called" 0 "a non-agent branch: no launch"
assert_count "$T/gh.log" 'pr comment' 0 "a non-agent branch: nothing posted"
ci_pr issue-9-feature-x CLOSED
revise pr 12 --source ci --run 900
assert_count "$T/claude.calls" "called" 0 "a closed PR: no launch"
assert_count "$T/gh.log" 'pr comment' 0 "a closed PR: nothing posted"
ci_pr issue-9-feature-x
for args in "pr 12 --source ci" "pr 12 --source ci --run" "pr 12 --source ci --run abc" "pr 12 --source ci --run 9x" "pr 12 --source ci --run 0" "pr 12 --source human --run 900" "issue 12 --source ci --run 900"; do
  rc=0; revise $args || rc=$?
  assert_eq "$rc" "2" "usage error (exit 2) for: --revise $args"
done
assert_count "$T/claude.calls" "called" 0 "usage errors launch nothing"
assert_count "$T/gh.log" 'pr comment' 0 "usage errors post nothing"

echo "revise: --source ci on an architect PR hands it to a human"
ci_pr arch-4-plan
rc=0; revise pr 12 --source ci --run 900 || rc=$?
assert_eq "$rc" "0" "exits 0"
assert_count "$T/claude.calls" "called" 0 "no launch"
assert_grep "$T/gh.log" 'pr edit 12 .*--add-label needs-human' "labels the PR needs-human"
assert_count "$T/gh.log" 'pr comment 12 ' 1 "one comment"
assert_grep "$T/gh.log" 'pr comment 12 .*actions/runs/900/job/1' "the comment links the failing job"
ci_pr arch-4-plan OPEN '["needs-human"]'
revise pr 12 --source ci --run 900
assert_count "$T/gh.log" 'pr comment' 0 "an arch PR already with a human: no second comment"
ci_pr arch-4-plan; ci_run failure sha-older
revise pr 12 --source ci --run 900
assert_count "$T/gh.log" 'pr edit' 0 "a stale failure on an arch PR: no label"

echo "revise: the shared budget counts reviewer and CI rounds together"
for kinds in "" R C RC CC RR; do
  ci_pr issue-9-feature-x
  ack_list "$kinds"
  if [ "${#ACK_LIST[@]}" -gt 0 ]; then acks "${ACK_LIST[@]}"; fi
  revise pr 12 --source ci --run 900
  assert_count "$T/claude.calls" "called" 1 "with prior rounds '${kinds:-none}': dispatches"
done
for kinds in RRC RCC CCC RRR; do
  ci_pr issue-9-feature-x
  ack_list "$kinds"; acks "${ACK_LIST[@]}"
  revise pr 12 --source ci --run 900
  assert_count "$T/claude.calls" "called" 0 "with 3 prior rounds ($kinds): no launch"
  assert_grep "$T/gh.log" 'pr edit 12 .*--add-label needs-human' "$kinds: labels needs-human"
  assert_grep "$T/gh.log" 'pr edit 12 .*--add-reviewer cooper3330' "$kinds: requests the owner's review"
  assert_count "$T/gh.log" 'pr comment 12 .*budget is spent' 1 "$kinds: one budget comment"
  assert_count "$T/gh.log" '^<!-- agent:merge-gate -->$' 1 "$kinds: signed merge-gate"
  assert_count "$T/gh.log" 'pr comment 12 .*agent:ack' 0 "$kinds: no acknowledgement"
done
ci_pr issue-9-feature-x OPEN '["needs-human"]'; acks "$RACK" "$RACK" "$CACK"
revise pr 12 --source ci --run 900
assert_count "$T/gh.log" 'pr comment' 0 "budget spent and already with a human: no repeated comment"
ci_pr issue-9-feature-x; acks "$RACK"
REVISION_BUDGET=1 revise pr 12 --source ci --run 900
assert_count "$T/claude.calls" "called" 0 "REVISION_BUDGET=1 is respected"
assert_grep "$T/gh.log" 'pr edit 12 .*--add-label needs-human' "REVISION_BUDGET=1: handed to a human"
ci_pr issue-9-feature-x; acks 'a <!-- agent:ack -->' 'b <!-- agent:ack -->' 'c <!-- agent:ack -->' 'd <!-- agent:ack -->'
revise pr 12 --source ci --run 900
assert_count "$T/claude.calls" "called" 1 "human acknowledgements (no source=) are not counted"

finish
