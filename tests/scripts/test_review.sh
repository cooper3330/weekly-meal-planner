#!/usr/bin/env bash
# scripts/review.sh launches the reviewer agent, offline (claude and gh are stubbed).
. "$(dirname "$0")/lib.sh"
cd "$ROOT" || exit 1

new_sandbox
# gh stub that serves a PR body from $T/pr_body.txt, for the prompt tests.
cat > "$T/bin/gh" <<'STUB'
#!/usr/bin/env bash
echo "gh $*" >> "$STUB_DIR/gh.log"
case "$1 $2" in
  "repo view") echo "cooper3330/weekly-meal-planner" ;;
  "pr view")   cat "$STUB_DIR/pr_body.txt" 2>/dev/null ;;
esac
exit 0
STUB
chmod +x "$T/bin/gh"
args="$T/claude.args"

# prompt_text: the -p argument (it spans lines), up to the next flag.
prompt_text() { awk 'f && /^--[a-z-]+$/ { exit } f { print } /^-p$/ { f = 1 }' "$args"; }

echo "review.sh: usage"
rc=0; bash scripts/review.sh >/dev/null 2>&1 || rc=$?
if [ "$rc" -ne 0 ]; then ok "a missing PR number is a usage error"; else bad "a missing PR number is a usage error"; fi
rc=0; bash scripts/review.sh abc >/dev/null 2>&1 || rc=$?
if [ "$rc" -ne 0 ]; then ok "a non-numeric PR number is a usage error"; else bad "a non-numeric PR number is a usage error"; fi
if [ ! -s "$T/claude.calls" ]; then ok "claude is not started on a usage error"; else bad "claude is not started on a usage error"; fi

echo "review.sh: launches the reviewer agent"
printf 'Closes #36\n\nSummary\n' > "$T/pr_body.txt"
rc=0; bash scripts/review.sh 70 >/dev/null 2>&1 || rc=$?
assert_eq "$rc" "0" "exits 0 when claude succeeds"
assert_grep "$args" '^-p$' "runs claude -p"
assert_eq "$(grep -A1 -x -- '--agent' "$args" | tail -1)" "reviewer" "agent is reviewer"
assert_eq "$(grep -A1 -x -- '--model' "$args" | tail -1)" "claude-opus-5-5" "model defaults to Opus"
assert_eq "$(grep -A1 -x -- '--max-turns' "$args" | tail -1)" "30" "max turns defaults to 30"
REVIEWER_MODEL=claude-test-model REVIEWER_MAX_TURNS=7 bash scripts/review.sh 70 >/dev/null 2>&1
assert_eq "$(grep -A1 -x -- '--model' "$args" | tail -1)" "claude-test-model" "REVIEWER_MODEL is honoured"
assert_eq "$(grep -A1 -x -- '--max-turns' "$args" | tail -1)" "7" "REVIEWER_MAX_TURNS is honoured"
REVIEWER_MODEL= REVIEWER_MAX_TURNS= bash scripts/review.sh 70 >/dev/null 2>&1
assert_eq "$(grep -A1 -x -- '--model' "$args" | tail -1)" "claude-opus-5-5" "an empty REVIEWER_MODEL falls back to Opus"
assert_eq "$(grep -A1 -x -- '--max-turns' "$args" | tail -1)" "30" "an empty REVIEWER_MAX_TURNS falls back to 30"

echo "review.sh: its own read-only allowlist"
bash scripts/review.sh 70 >/dev/null 2>&1
rules=$(sed -n '/^--allowedTools$/,$p' "$args" | tail -n +2)
for want in 'gh pr review' 'gh pr view' 'gh pr diff' 'gh pr checks' 'gh issue view' 'gh pr edit'; do
  if grep -qF -- "$want" <<<"$rules"; then ok "allows $want"; else bad "allows $want"; fi
done
for banned in 'git push' 'gh pr merge' 'Write' 'Edit' 'gh pr comment' 'gh issue comment' 'gh issue edit' 'git commit' 'make '; do
  if grep -qF -- "$banned" <<<"$rules"; then bad "does not allow $banned"; else ok "does not allow $banned"; fi
done
if grep -F 'gh pr edit' <<<"$rules" | grep -qF -- '--add-label risk:high'; then ok "gh pr edit is limited to --add-label risk:high"; else bad "gh pr edit is limited to --add-label risk:high"; fi
if grep -qF 'gh api' <<<"$rules"; then bad "no gh api rule at all (a prefix rule cannot tell a read from a write)"; else ok "no gh api rule at all (a prefix rule cannot tell a read from a write)"; fi
if grep -qF 'acceptEdits' "$args"; then bad "runs without --permission-mode acceptEdits (it needs no file edits)"; else ok "runs without --permission-mode acceptEdits (it needs no file edits)"; fi

echo "review.sh: goes through the agent guard"
assert_grep scripts/review.sh 'GUARD"? run --kind pr --number' "wraps claude in agent-guard.sh run --kind pr --number N"
: > "$T/gh.log"
echo "Usage limit reached. Your limit resets at 5pm" > "$T/claude.err"
rc=0; STUB_CLAUDE_FAIL=1 bash scripts/review.sh 71 >/dev/null 2>&1 || rc=$?
if [ "$rc" -ne 0 ]; then ok "a usage-limit stop exits non-zero"; else bad "a usage-limit stop exits non-zero"; fi
assert_grep "$T/gh.log" 'pr comment 71' "and the guard comments on the PR"
rm -f "$T/claude.err"

echo "review.sh: the prompt for a PR that closes a task"
printf 'Closes #36\n' > "$T/pr_body.txt"
bash scripts/review.sh 72 >/dev/null 2>&1
p=$(prompt_text)
if grep -q '#72' <<<"$p"; then ok "names the PR"; else bad "names the PR"; fi
if grep -q '#36' <<<"$p"; then ok "names the linked task #36"; else bad "names the linked task #36"; fi
if grep -qi 'data, not instructions' <<<"$p"; then ok "tells the reviewer PR text is data"; else bad "tells the reviewer PR text is data"; fi

echo "review.sh: a PR with no Closes #N"
printf 'Refs #5\n\nADR only\n' > "$T/pr_body.txt"
bash scripts/review.sh 73 >/dev/null 2>&1
p=$(prompt_text)
if grep -qi 'no linked task' <<<"$p"; then ok "says there is no linked task"; else bad "says there is no linked task"; fi
if grep -qi 'parent' <<<"$p" && grep -qi 'constitution' <<<"$p"; then ok "reviews against the parent issue and the constitution"; else bad "reviews against the parent issue and the constitution"; fi
if grep -qi 'not approve by default' <<<"$p"; then ok "does not approve by default"; else bad "does not approve by default"; fi
if grep -q '#36' <<<"$p"; then bad "does not leak a task number"; else ok "does not leak a task number"; fi

finish
