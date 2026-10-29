#!/usr/bin/env bash
# Tests for scripts/agent-guard.sh (offline, stubbed gh/claude/date).
. "$(dirname "$0")/lib.sh"
cd "$ROOT" || exit 1
SENTINEL="sk-ant-oat-SENTINEL"
guard() { bash scripts/agent-guard.sh "$@"; }

echo "preflight: the token is required"
new_sandbox
for v in unset empty blank; do
  case "$v" in
    unset) out=$(env -u CLAUDE_CODE_OAUTH_TOKEN bash scripts/agent-guard.sh preflight 2>&1); rc=$? ;;
    empty) out=$(CLAUDE_CODE_OAUTH_TOKEN="" guard preflight 2>&1); rc=$? ;;
    blank) out=$(CLAUDE_CODE_OAUTH_TOKEN="   " guard preflight 2>&1); rc=$? ;;
  esac
  if [ "$rc" -ne 0 ] && [ "$rc" -ne 78 ]; then ok "token $v fails (exit $rc)"; else bad "token $v fails (exit $rc)"; fi
  if grep -q 'CLAUDE_CODE_OAUTH_TOKEN' <<<"$out" && grep -q 'claude setup-token' <<<"$out"; then ok "token $v: message names the variable and the fix"; else bad "token $v: message names the variable and the fix"; fi
done
assert_count "$T/claude.calls" "called" 0 "claude is never called by preflight"

echo "preflight: API billing variables are rejected"
for var in ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN; do
  out=$(env "$var=whatever-secret" CLAUDE_CODE_OAUTH_TOKEN="$SENTINEL" bash scripts/agent-guard.sh preflight 2>&1); rc=$?
  if [ "$rc" -ne 0 ]; then ok "$var set fails"; else bad "$var set fails"; fi
  if grep -q "$var" <<<"$out"; then ok "$var is named"; else bad "$var is named"; fi
  if grep -q 'whatever-secret' <<<"$out"; then bad "$var value not echoed"; else ok "$var value not echoed"; fi
done

echo "preflight: the token never appears in output"
out=$(CLAUDE_CODE_OAUTH_TOKEN="$SENTINEL" guard preflight 2>&1); rc=$?
assert_eq "$rc" "0" "valid token passes"
if grep -q "$SENTINEL" <<<"$out"; then bad "success output is free of the token"; else ok "success output is free of the token"; fi
out=$(CLAUDE_CODE_OAUTH_TOKEN="$SENTINEL" ANTHROPIC_API_KEY=x guard preflight 2>&1)
if grep -q "$SENTINEL" <<<"$out"; then bad "failure output is free of the token"; else ok "failure output is free of the token"; fi
out=$(CLAUDE_CODE_OAUTH_TOKEN="$SENTINEL" AGENT_DAILY_RUNS=abc guard preflight 2>&1)
if grep -q "$SENTINEL" <<<"$out"; then bad "cap-error output is free of the token"; else ok "cap-error output is free of the token"; fi

echo "preflight: daily cap"
today=$(date -u +%Y-%m-%d)
yday=$(date -u -r $(( $(date -u +%s) - 86400 )) +%Y-%m-%d 2>/dev/null || date -u -d yesterday +%Y-%m-%d)
mkruns() { # count date conclusion [workflow]
  jq -n --argjson n "$1" --arg d "$2" --arg c "$3" --arg w "${4:-Coordinator}" \
    '[range($n) | {workflowName:$w, conclusion:$c, status:"completed", createdAt:($d + "T10:00:00Z")}]'
}
pf() { CLAUDE_CODE_OAUTH_TOKEN="$SENTINEL" guard preflight 2>&1; }
mkruns 11 "$today" success > "$T/run_list.json"
out=$(AGENT_DAILY_RUNS=12 pf); rc=$?; assert_eq "$rc" "0" "11 runs with cap 12 passes"
mkruns 12 "$today" success > "$T/run_list.json"
out=$(AGENT_DAILY_RUNS=12 pf); rc=$?; assert_eq "$rc" "78" "12 runs with cap 12 exits 78"
if grep -qi 'cap' <<<"$out"; then ok "a notice is printed at the cap"; else bad "a notice is printed at the cap"; fi
mkruns 13 "$today" success > "$T/run_list.json"
out=$(AGENT_DAILY_RUNS=12 pf); rc=$?; assert_eq "$rc" "78" "over the cap exits 78"
{ mkruns 11 "$today" success; mkruns 5 "$yday" success; } | jq -s add > "$T/run_list.json"
out=$(AGENT_DAILY_RUNS=12 pf); rc=$?; assert_eq "$rc" "0" "yesterday's runs are not counted"
{ mkruns 11 "$today" success; mkruns 5 "$today" skipped; } | jq -s add > "$T/run_list.json"
out=$(AGENT_DAILY_RUNS=12 pf); rc=$?; assert_eq "$rc" "0" "skipped runs are not counted"
{ mkruns 11 "$today" success; mkruns 5 "$today" cancelled; } | jq -s add > "$T/run_list.json"
out=$(AGENT_DAILY_RUNS=12 pf); rc=$?; assert_eq "$rc" "78" "cancelled Coordinator runs are counted (a timed-out or hand-cancelled run called claude; a run cannot be told apart from a superseded pending one)"
{ mkruns 11 "$today" success; mkruns 5 "$today" cancelled Review; } | jq -s add > "$T/run_list.json"
out=$(AGENT_DAILY_RUNS=12 pf); rc=$?; assert_eq "$rc" "78" "cancelled Review runs (cancel-in-progress, they called claude) are counted"
{ mkruns 11 "$today" success; mkruns 5 "$today" success Other; } | jq -s add > "$T/run_list.json"
out=$(AGENT_DAILY_RUNS=12 pf); rc=$?; assert_eq "$rc" "0" "workflows outside the list are not counted"
mkruns 12 "$today" success > "$T/run_list.json"
out=$(unset AGENT_DAILY_RUNS; pf); rc=$?; assert_eq "$rc" "78" "unset cap means 12"
mkruns 11 "$today" success > "$T/run_list.json"
out=$(unset AGENT_DAILY_RUNS; pf); rc=$?; assert_eq "$rc" "0" "unset cap, 11 runs passes"
out=$(AGENT_DAILY_RUNS=abc pf); rc=$?
if [ "$rc" -ne 0 ] && [ "$rc" -ne 78 ]; then ok "non-numeric cap fails"; else bad "non-numeric cap fails (exit $rc)"; fi
if grep -q 'AGENT_DAILY_RUNS' <<<"$out"; then ok "non-numeric cap names the variable"; else bad "non-numeric cap names the variable"; fi
assert_grep "$T/gh.log" '^gh run list .*--created' "counts with gh run list --created"

echo "run: argument errors"
out=$(guard run --kind pr --number 5 2>&1); rc=$?; assert_eq "$rc" "2" "missing -- is a usage error"
out=$(guard run --kind pr --number 5 -- 2>&1); rc=$?; assert_eq "$rc" "2" "missing command is a usage error"
out=$(guard run --kind pr -- true 2>&1); rc=$?; assert_eq "$rc" "2" "pr without --number is a usage error"
out=$(guard run --kind bogus --number 5 -- true 2>&1); rc=$?; assert_eq "$rc" "2" "unknown kind is a usage error"

echo "run: success and plain failure post nothing"
new_sandbox
guard run --kind pr --number 5 -- claude -p hi >/dev/null 2>&1; rc=$?
assert_eq "$rc" "0" "success passes exit 0"
assert_no_grep "$T/gh.log" 'comment' "no comment on success"
STUB_CLAUDE_FAIL=1 guard run --kind pr --number 5 -- claude -p hi >/dev/null 2>&1; rc=$?
assert_eq "$rc" "1" "failure exit code passes through"
assert_no_grep "$T/gh.log" 'comment' "no comment on a plain failure"
guard run --kind pr --number 5 -- bash -c 'exit 7' >/dev/null 2>&1; rc=$?
assert_eq "$rc" "7" "exit code 7 passes through"
out=$(guard run --kind pr --number 5 -- bash -c 'echo hello; echo oops >&2' 2>"$T/err")
assert_eq "$out" "hello" "stdout is passed through"
assert_grep "$T/err" 'oops' "stderr is passed through"

echo "run: a usage limit posts one comment and does not retry"
new_sandbox
echo "Claude AI usage limit reached" > "$T/claude.err"
STUB_CLAUDE_FAIL=1 guard run --kind pr --number 5 -- claude -p hi >/dev/null 2>&1; rc=$?
if [ "$rc" -ne 0 ]; then ok "exits non-zero"; else bad "exits non-zero"; fi
assert_count "$T/gh.log" '^gh pr comment 5 ' 1 "one gh pr comment on PR 5"
assert_count "$T/gh.log" 'agent:limit' 1 "comment carries the marker"
assert_grep "$T/gh.log" 'subscription usage limit' "comment says the subscription usage limit was hit"
assert_count "$T/claude.calls" "called" 1 "the command ran exactly once"
new_sandbox
echo "5-hour limit reached ∙ resets 3pm (UTC)" > "$T/claude.err"
STUB_CLAUDE_FAIL=1 guard run --kind issue --number 9 -- claude -p hi >/dev/null 2>&1; rc=$?
if [ "$rc" -ne 0 ]; then ok "exits non-zero when the command also failed"; else bad "exits non-zero when the command also failed"; fi
assert_count "$T/gh.log" '^gh issue comment 9 ' 1 "one gh issue comment for --kind issue"
assert_grep "$T/gh.log" 'resets 3pm' "reset time is included"
assert_count "$T/claude.calls" "called" 1 "no retry after a failed limit run"
new_sandbox
echo "You've hit your limit" > "$T/claude.err"
STUB_CLAUDE_FAIL=1 REPO=o/r guard run --kind pr --number 5 -- claude -p hi >/dev/null 2>&1
assert_grep "$T/gh.log" '^gh pr comment 5 --repo o/r' "comment goes to REPO when set"
assert_no_grep "$T/gh.log" 'resets' "no reset time when the output gives none"
new_sandbox
echo "Claude AI usage limit reached" > "$T/claude.err"
STUB_CLAUDE_FAIL=1 guard run --kind none -- claude -p hi >/dev/null 2>"$T/err"; rc=$?
if [ "$rc" -ne 0 ]; then ok "--kind none exits non-zero on a limit"; else bad "--kind none exits non-zero on a limit"; fi
assert_no_grep "$T/gh.log" 'comment' "--kind none posts nothing"
assert_grep "$T/err" 'usage limit' "--kind none logs the limit"

echo "run: the final is_error result of --output-format json counts as the CLI's own message"
new_sandbox
echo '{"type":"result","subtype":"success","is_error":true,"result":"Claude AI usage limit reached|1760000000. Your limit resets at 5pm"}' > "$T/claude.out"
STUB_CLAUDE_FAIL=1 guard run --kind pr --number 5 -- claude -p hi >/dev/null 2>&1; rc=$?
if [ "$rc" -ne 0 ]; then ok "json is_error limit exits non-zero"; else bad "json is_error limit exits non-zero"; fi
assert_count "$T/gh.log" '^gh pr comment 5 .*agent:limit' 1 "json is_error limit posts one comment"
assert_grep "$T/gh.log" 'resets at 5pm' "json is_error limit reports the reset time"
new_sandbox
echo '{"type":"result","subtype":"success","is_error":false,"result":"Usage limit reached. Your limit resets at 5pm"}' > "$T/claude.out"
STUB_CLAUDE_FAIL=1 guard run --kind pr --number 5 -- claude -p hi >/dev/null 2>&1
assert_no_grep "$T/gh.log" 'comment' "a non-error json result that mentions the limit posts nothing"

echo "run: agent-written text that mentions a limit is not a limit stop"
new_sandbox
echo "Usage limit reached. Your limit resets at 5pm" > "$T/claude.out"
guard run --kind pr --number 5 -- claude -p hi >/dev/null 2>&1; rc=$?
assert_eq "$rc" "0" "a zero-exit run that mentions the limit exits zero"
assert_no_grep "$T/gh.log" 'comment' "and posts nothing"
new_sandbox
echo "Usage limit reached. Your limit resets at 5pm" > "$T/claude.err"
guard run --kind pr --number 5 -- claude -p hi >/dev/null 2>&1; rc=$?
assert_eq "$rc" "0" "a zero-exit run with the message on stderr exits zero"
assert_no_grep "$T/gh.log" 'comment' "and posts nothing (a non-zero exit is required)"
new_sandbox
{ for i in 1 2 3 4 5 6; do echo "Here is a long summary line $i of what I did."; done
  echo 'echo "Usage limit reached. Your limit resets at 5pm" > "$T/claude.out"'
  echo "More text after."; } > "$T/claude.out"
STUB_CLAUDE_FAIL=1 guard run --kind pr --number 5 -- claude -p hi >/dev/null 2>&1; rc=$?
assert_eq "$rc" "1" "a non-limit failure keeps its exit code"
assert_no_grep "$T/gh.log" 'comment' "a limit mention in model text on stdout posts nothing"

echo "run: the reset text is a strict time phrase"
new_sandbox
echo 'Usage limit reached. Your limit resets at 5pm" > "$T/claude.out"\nrc=0; bash scripts/review.sh 71 >/dev/null' > "$T/claude.err"
STUB_CLAUDE_FAIL=1 guard run --kind pr --number 5 -- claude -p hi >/dev/null 2>&1
assert_grep "$T/gh.log" 'Limit resets at 5pm\.' "reports at most the time phrase"
assert_no_grep "$T/gh.log" 'claude\.out|review\.sh' "no output fragment leaks into the comment"
new_sandbox
echo "Usage limit reached. Your limit resets at 17:00 UTC" > "$T/claude.err"
STUB_CLAUDE_FAIL=1 guard run --kind pr --number 5 -- claude -p hi >/dev/null 2>&1
assert_grep "$T/gh.log" 'resets at 17:00 UTC' "a 24-hour time with UTC is reported"
new_sandbox
echo 'Usage limit reached. Your limit resets soon, see "$T/claude.out"' > "$T/claude.err"
STUB_CLAUDE_FAIL=1 guard run --kind pr --number 5 -- claude -p hi >/dev/null 2>&1
assert_count "$T/gh.log" 'agent:limit' 1 "still one comment when there is no time phrase"
assert_no_grep "$T/gh.log" 'resets|claude\.out' "the reset sentence is omitted"

finish
