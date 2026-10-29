#!/usr/bin/env bash
# scripts/after-review.sh enables auto-merge for approved low-risk PRs into main, offline (gh stubbed).
. "$(dirname "$0")/lib.sh"
cd "$ROOT" || exit 1

GIT_MODE=$(git ls-files -s -- scripts/after-review.sh | awk '{print $1}')   # before git is stubbed
SHA=abc123
MARK='<!-- agent:reviewer -->'

# setup: a fresh sandbox with a gh stub that serves fixtures and records its calls.
#   pr.json      baseRefName, headRefOid, labels   (gh pr view --json ... -q)
#   reviews.json REST reviews                      (gh api .../reviews)
#   files.txt    what the paginated files call prints
setup() {
  new_sandbox
  unset AGENTS_PAUSED OWNER
  export REPO=cooper3330/weekly-meal-planner
  set_pr main ""
  echo '[]' > "$T/comments.json"
  approve_on "$SHA"
  echo "src/mealplanner/app.py" > "$T/files.txt"
  cat > "$T/bin/gh" <<'STUB'
#!/usr/bin/env bash
echo "gh $*" >> "$STUB_DIR/gh.log"
args=("$@"); jqexpr=""
for ((i = 0; i < ${#args[@]}; i++)); do
  case "${args[$i]}" in -q|--jq) jqexpr="${args[$((i + 1))]}" ;; esac
done
out() { if [ -n "$jqexpr" ]; then jq -r "$jqexpr" < "$1"; else cat "$1"; fi; }
case "$1 $2" in
  "pr view") out "$STUB_DIR/pr.json" ;;
  "workflow run") [ -n "${STUB_WORKFLOW_FAIL:-}" ] && { echo "HTTP 403" >&2; exit 1; } ;;
  "api repos/"*)
    case "$2" in
      */reviews*) out "$STUB_DIR/reviews.json" ;;
      */issues/*/comments*) out "$STUB_DIR/comments.json" ;;
      */files*)   cat "$STUB_DIR/files.txt" ;;
    esac ;;
esac
exit 0
STUB
  chmod +x "$T/bin/gh"
}
# set_pr BASE LABEL: the PR fixture (LABEL may be empty)
set_pr() { # BASE LABEL [BRANCH]
  jq -n --arg base "$1" --arg sha "$SHA" --arg label "$2" --arg branch "${3:-issue-39-example}" \
    '{baseRefName:$base, headRefName:$branch, headRefOid:$sha, labels:(if $label == "" then [] else [{name:$label}] end)}' > "$T/pr.json"
}
# set_acks REVIEWER HUMAN: the PR's acknowledgement comments (reviewer ones carry source=reviewer)
set_acks() {
  jq -n --argjson r "$1" --argjson h "$2" --argjson c "${3:-0}" \
    '[range($r) | {body: "Picked up. <!-- agent:ack source=reviewer -->"}]
     + [range($c) | {body: "Picked up. [run 1] <!-- agent:ack source=ci -->"}]
     + [range($h) | {body: "Picked up. <!-- agent:ack -->"}]
     + [{body: "an unrelated comment"}]' > "$T/comments.json"
}
# approve_on SHA [STATE] [BODY]: the one review fixture
approve_on() {
  jq -n --arg sha "$1" --arg st "${2:-APPROVED}" --arg body "${3-Looks good. $MARK}" \
    '[{user:{login:"github-actions[bot]"},state:$st,commit_id:$sha,submitted_at:"2026-01-01T00:00:00Z",body:$body}]' > "$T/reviews.json"
}
merges() { grep -c '^gh pr merge' "$T/gh.log" || true; }
gates()  { grep -c 'agent:merge-gate' "$T/gh.log" || true; }
run()    { bash scripts/after-review.sh 70 > "$T/out.txt" 2>&1; }

echo "after-review.sh: usage"
setup
rc=0; bash scripts/after-review.sh >/dev/null 2>&1 || rc=$?
if [ "$rc" -ne 0 ]; then ok "a missing PR number is a usage error"; else bad "a missing PR number is a usage error"; fi
rc=0; bash scripts/after-review.sh abc >/dev/null 2>&1 || rc=$?
if [ "$rc" -ne 0 ]; then ok "a non-numeric PR number is a usage error"; else bad "a non-numeric PR number is a usage error"; fi

echo "after-review.sh: low risk, approved"
setup; run
assert_eq "$(merges)" "1" "exactly one gh pr merge call"
assert_grep "$T/gh.log" '^gh pr merge 70 .*--auto --squash --delete-branch' "it is --auto --squash --delete-branch"
assert_eq "$(gates)" "0" "no merge-gate comment"
assert_no_grep "$T/gh.log" 'add-reviewer' "no review is requested"
assert_grep "$T/out.txt" 'auto-merge' "the decision is printed"

echo "after-review.sh: a failed check means no merge, a review request and one comment"
check_fails() { # name regex-for-the-comment
  assert_eq "$(merges)" "0" "$1: no gh pr merge call"
  assert_grep "$T/gh.log" '^gh pr edit 70 .*--add-reviewer cooper3330' "$1: the owner's review is requested"
  assert_eq "$(grep -c '^gh pr comment' "$T/gh.log" || true)" "1" "$1: exactly one comment"
  assert_grep "$T/gh.log" 'agent:merge-gate' "$1: the comment carries the merge-gate marker"
  assert_grep "$T/gh.log" "$2" "$1: the comment names the failed check"
}
setup; set_pr release-x ""; run
check_fails "base branch" 'base branch'
setup; echo "scripts/x.sh" > "$T/files.txt"; run
check_fails "protected file" 'classif'
setup; set_pr main risk:high; run
check_fails "risk:high label" 'risk:high label'
setup; AGENTS_PAUSED=true run
check_fails "paused" 'AGENTS_PAUSED'
setup; echo "scripts/x.sh" > "$T/files.txt"; OWNER=someone bash scripts/after-review.sh 70 >/dev/null 2>&1
assert_grep "$T/gh.log" 'add-reviewer someone' "OWNER overrides the reviewer to request"

echo "after-review.sh: reviews that do not count"
setup; approve_on old000; run
assert_eq "$(merges)" "0" "stale approval (older head SHA): nothing merged"
assert_eq "$(gates)" "0" "stale approval: no comment"
setup; approve_on "$SHA" CHANGES_REQUESTED; run
assert_eq "$(merges)" "0" "changes requested: no merge"
assert_eq "$(gates)" "0" "changes requested: no merge-gate comment"
assert_no_grep "$T/gh.log" 'add-reviewer' "changes requested: no review requested"
setup; approve_on "$SHA" APPROVED "Approved by some other bot"; run
assert_eq "$(merges)" "0" "approval without the reviewer marker is ignored"
assert_eq "$(gates)" "0" "unmarked approval: no comment"
setup; echo '[]' > "$T/reviews.json"; run
assert_eq "$(merges)" "0" "no reviews: nothing merged"
setup
jq -n --arg sha "$SHA" --arg m "$MARK" \
  '[{state:"APPROVED",commit_id:$sha,submitted_at:"2026-01-01T00:00:00Z",body:("a " + $m)},
    {state:"CHANGES_REQUESTED",commit_id:$sha,submitted_at:"2026-01-02T00:00:00Z",body:("b " + $m)}]' > "$T/reviews.json"
run
assert_eq "$(merges)" "0" "an approval later followed by changes requested does not merge"
setup
jq -n --arg sha "$SHA" --arg m "$MARK" \
  '[{state:"CHANGES_REQUESTED",commit_id:$sha,submitted_at:"2026-01-01T00:00:00Z",body:("a " + $m)},
    {state:"APPROVED",commit_id:$sha,submitted_at:"2026-01-02T00:00:00Z",body:("b " + $m)}]' > "$T/reviews.json"
run
assert_eq "$(merges)" "1" "changes requested, then a later approval, merges"

echo "after-review.sh: fails closed"
setup
mkdir -p "$T/repo/scripts" "$T/repo/.github"   # a copy of the scripts with no rules file
cp scripts/after-review.sh scripts/classify-risk.sh "$T/repo/scripts/"
(cd "$T/repo" && bash scripts/after-review.sh 70 > "$T/out.txt" 2>&1) || true
assert_eq "$(merges)" "0" "rules file missing: no merge"
assert_eq "$(gates)" "1" "rules file missing: the owner is told (one merge-gate comment)"

echo "after-review.sh: more than 100 changed files"
setup; printf 'src/a.py\nsrc/b.py\nscripts/second-page.sh\n' > "$T/files.txt"; run
assert_grep "$T/gh.log" '^gh api repos/[^ ]+/pulls/70/files .*--paginate' "lists files with --paginate"
assert_eq "$(merges)" "0" "a protected path on a later page gives high: no merge"
setup; printf 'src/a.py\nsrc/b.py\n' > "$T/files.txt"; run
assert_eq "$(merges)" "1" "many low-risk files still merge"

# --- changes requested: dispatch a revision within REVISION_BUDGET, else hand over (#39) ------------
dispatches() { grep -c '^gh workflow run' "$T/gh.log" || true; }
cr_setup() { setup; approve_on "$SHA" CHANGES_REQUESTED; }   # BRANCH via set_pr after, if needed

echo "after-review.sh: changes requested, within the budget, dispatches one revision"
for prior in 0 1 2; do
  cr_setup; set_acks "$prior" 0; run
  assert_eq "$(dispatches)" "1" "$prior prior reviewer acknowledgements: one dispatch"
  assert_grep "$T/gh.log" '^gh workflow run revise\.yml .*-f kind=pr .*-f number=70 .*-f source=reviewer' "$prior: revise.yml with kind=pr, the number and source=reviewer"
  assert_no_grep "$T/gh.log" 'needs-human|add-reviewer' "$prior: no hand-over"
  assert_eq "$(merges)" "0" "$prior: no merge"
done

echo "after-review.sh: at the budget, the PR is handed to a human"
cr_setup; set_acks 3 0; run
assert_eq "$(dispatches)" "0" "3 prior acknowledgements: no dispatch"
assert_grep "$T/gh.log" '^gh pr edit 70 .*--add-label needs-human' "the PR is labelled needs-human"
assert_grep "$T/gh.log" '^gh pr edit 70 .*--add-reviewer cooper3330' "the owner's review is requested"
assert_eq "$(gates)" "1" "exactly one comment, with the merge-gate marker"
assert_grep "$T/gh.log" 'budget' "the comment says the budget is spent"
assert_eq "$(merges)" "0" "no merge"
cr_setup; set_acks 5 0; run
assert_eq "$(dispatches)" "0" "over the budget: no dispatch"
assert_eq "$(gates)" "1" "over the budget: one comment"

echo "after-review.sh: REVISION_BUDGET is respected"
cr_setup; set_acks 1 0; REVISION_BUDGET=1 run
assert_eq "$(dispatches)" "0" "REVISION_BUDGET=1 with 1 prior: no dispatch"
assert_grep "$T/gh.log" 'needs-human' "REVISION_BUDGET=1 with 1 prior: handed over"
cr_setup; set_acks 0 0; REVISION_BUDGET=1 run
assert_eq "$(dispatches)" "1" "REVISION_BUDGET=1 with 0 prior: dispatched"
cr_setup; set_acks 3 0; REVISION_BUDGET=5 run
assert_eq "$(dispatches)" "1" "REVISION_BUDGET=5 with 3 prior: dispatched"
cr_setup; set_acks 3 0; REVISION_BUDGET= run
assert_eq "$(dispatches)" "0" "an empty REVISION_BUDGET (unset variable) means the default of 3"
cr_setup; set_acks 0 0; REVISION_BUDGET=abc run
assert_eq "$(dispatches)" "1" "a non-numeric REVISION_BUDGET falls back to the default"

echo "after-review.sh: human acknowledgements do not count"
cr_setup; set_acks 2 7; run
assert_eq "$(dispatches)" "1" "2 reviewer + 7 human acknowledgements: still dispatches"
cr_setup; set_acks 0 9; run
assert_eq "$(dispatches)" "1" "only human acknowledgements: dispatches"

echo "after-review.sh: CI acknowledgements share the budget with the reviewer's"
cr_setup; set_acks 1 0 1; run
assert_eq "$(dispatches)" "1" "1 reviewer + 1 CI round: dispatches"
cr_setup; set_acks 2 0 1; run
assert_eq "$(dispatches)" "0" "2 reviewer + 1 CI round: the budget is spent, no dispatch"
assert_grep "$T/gh.log" 'needs-human' "2 reviewer + 1 CI round: handed to a human"
cr_setup; set_acks 0 0 3; run
assert_eq "$(dispatches)" "0" "3 CI rounds: no dispatch"

echo "after-review.sh: changes requested never dispatches on an ADR PR, a stale review or while paused"
cr_setup; set_pr main "" arch-4-plan; set_acks 0 0; run
assert_eq "$(dispatches)" "0" "arch-* PR: no dispatch"
assert_no_grep "$T/gh.log" 'needs-human' "arch-* PR: not labelled either"
cr_setup; approve_on old000 CHANGES_REQUESTED; run
assert_eq "$(dispatches)" "0" "a change request on an older head: no dispatch"
cr_setup; approve_on "$SHA" CHANGES_REQUESTED "no marker"; run
assert_eq "$(dispatches)" "0" "an unmarked change request: no dispatch"
cr_setup; AGENTS_PAUSED=true run
assert_eq "$(dispatches)" "0" "paused: no dispatch"

echo "after-review.sh: a hand-over already made is not repeated"
cr_setup; set_acks 3 0; set_pr main needs-human; run
assert_eq "$(gates)" "0" "already labelled needs-human: no second comment"
assert_eq "$(dispatches)" "0" "and no dispatch"

echo "after-review.sh: a failed dispatch is reported, not hidden"
cr_setup; set_acks 0 0
rc=0; STUB_WORKFLOW_FAIL=1 run || rc=$?
if [ "$rc" -ne 0 ]; then ok "a failing gh workflow run makes the step fail"; else bad "a failing gh workflow run makes the step fail"; fi

echo "after-review.sh: structure"
assert_grep scripts/after-review.sh 'previous_filename' "includes the previous path of renames"
if [ "$GIT_MODE" = 100755 ]; then ok "after-review.sh is executable"; else bad "after-review.sh is executable"; fi

finish
