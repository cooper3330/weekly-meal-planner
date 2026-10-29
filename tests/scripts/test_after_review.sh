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
  "api repos/"*)
    case "$2" in
      */reviews*) out "$STUB_DIR/reviews.json" ;;
      */files*)   cat "$STUB_DIR/files.txt" ;;
    esac ;;
esac
exit 0
STUB
  chmod +x "$T/bin/gh"
}
# set_pr BASE LABEL: the PR fixture (LABEL may be empty)
set_pr() {
  jq -n --arg base "$1" --arg sha "$SHA" --arg label "$2" \
    '{baseRefName:$base, headRefOid:$sha, labels:(if $label == "" then [] else [{name:$label}] end)}' > "$T/pr.json"
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

echo "after-review.sh: structure"
assert_grep scripts/after-review.sh 'previous_filename' "includes the previous path of renames"
if [ "$GIT_MODE" = 100755 ]; then ok "after-review.sh is executable"; else bad "after-review.sh is executable"; fi

finish
