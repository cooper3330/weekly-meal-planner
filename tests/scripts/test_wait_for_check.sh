#!/usr/bin/env bash
# scripts/wait-for-check.sh waits for the head commit's `check` run, and gives up early when no run ever
# appears (#192, owner-approved note 2). Offline: gh is stubbed, and the sleep is replaced by a no-op.
. "$(dirname "$0")/lib.sh"
cd "$ROOT" || exit 1

new_sandbox
# gh stub: answers `gh api .../check-runs --jq FILTER` by running the real jq FILTER on the n-th fixture
# $STUB_DIR/runs_<n>.json (the last one repeats). Every call is counted in $STUB_DIR/calls.
cat > "$T/bin/gh" <<'STUB'
#!/usr/bin/env bash
n=$(( $(cat "$STUB_DIR/calls" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$STUB_DIR/calls"
echo "gh $*" >> "$STUB_DIR/gh.log"
[ -n "${STUB_GH_FAIL:-}" ] && { echo "gh: HTTP 500" >&2; exit 1; }
f="$STUB_DIR/runs_$n.json"
[ -f "$f" ] || f=$(ls "$STUB_DIR"/runs_*.json | sort -t_ -k2 -n | tail -1)
filter=""
while [ $# -gt 0 ]; do [ "$1" = "--jq" ] && filter="$2"; shift; done
jq -r "$filter" < "$f"
STUB
chmod +x "$T/bin/gh"
export REPO=cooper3330/weekly-meal-planner WAIT_SLEEP=true POLL_SECONDS=0
SHA=abc1234def
none='{"check_runs":[]}'
run() { printf '{"check_runs":[{"name":"other","id":9,"status":"completed"},{"name":"check","id":%s,"status":"%s"}]}' "$1" "$2"; }
reset() { rm -f "$T"/runs_*.json "$T/calls" "$T/gh.log"; }
calls() { cat "$T/calls" 2>/dev/null || echo 0; }
wait_out() { bash scripts/wait-for-check.sh "$SHA" 2>&1; }

echo "wait-for-check.sh: usage"
rc=0; bash scripts/wait-for-check.sh >/dev/null 2>&1 || rc=$?
if [ "$rc" -ne 0 ]; then ok "a missing commit is a usage error"; else bad "a missing commit is a usage error"; fi
rc=0; bash scripts/wait-for-check.sh 'abc;rm' >/dev/null 2>&1 || rc=$?
if [ "$rc" -ne 0 ]; then ok "a commit that is not hex is a usage error"; else bad "a commit that is not hex is a usage error"; fi

echo "wait-for-check.sh: a completed check returns at once"
reset; run 1 completed > "$T/runs_1.json"
rc=0; wait_out >/dev/null || rc=$?
assert_eq "$rc" "0" "exits 0"
assert_eq "$(calls)" "1" "after one poll"
assert_count "$T/gh.log" 'repos/cooper3330/weekly-meal-planner/commits/abc1234def/check-runs' 1 "asking for this commit's check runs"

echo "wait-for-check.sh: no check run at all gives up after MISSING_POLLS polls"
reset; echo "$none" > "$T/runs_1.json"
out=$(wait_out)
assert_eq "$(calls)" "6" "six polls by default"
if grep -qi 'no .check. run' <<<"$out"; then ok "and says no check run appeared"; else bad "and says no check run appeared"; fi
reset; echo "$none" > "$T/runs_1.json"
MISSING_POLLS=3 bash scripts/wait-for-check.sh "$SHA" >/dev/null 2>&1
assert_eq "$(calls)" "3" "MISSING_POLLS is honoured"

echo "wait-for-check.sh: a run that appears late keeps the wait going"
reset; echo "$none" > "$T/runs_1.json"; echo "$none" > "$T/runs_2.json"
run 5 queued > "$T/runs_3.json"; run 5 in_progress > "$T/runs_4.json"; run 5 completed > "$T/runs_5.json"
rc=0; wait_out >/dev/null || rc=$?
assert_eq "$rc" "0" "exits 0"
assert_eq "$(calls)" "5" "it polled until the run completed"

echo "wait-for-check.sh: seeing a run resets the missing count"
reset
for i in 1 2 3 4 5; do echo "$none" > "$T/runs_$i.json"; done
run 5 in_progress > "$T/runs_6.json"
for i in 7 8 9 10 11; do echo "$none" > "$T/runs_$i.json"; done
run 5 completed > "$T/runs_12.json"
wait_out >/dev/null
assert_eq "$(calls)" "12" "five missing, one run, five missing, then completed: no early exit"

echo "wait-for-check.sh: a queued or running check is never given up on early"
reset; run 5 in_progress > "$T/runs_1.json"
WAIT_SECONDS=2 POLL_SECONDS=1 WAIT_SLEEP=sleep bash scripts/wait-for-check.sh "$SHA" >/dev/null 2>&1
if [ "$(calls)" -ge 2 ]; then ok "it kept polling while the run was in progress ($(calls) polls)"; else bad "it kept polling while the run was in progress ($(calls) polls)"; fi

echo "wait-for-check.sh: bounded by wall-clock time"
reset; run 5 in_progress > "$T/runs_1.json"
out=$(WAIT_SECONDS=2 POLL_SECONDS=1 WAIT_SLEEP=sleep bash scripts/wait-for-check.sh "$SHA" 2>&1); rc=$?
assert_eq "$rc" "0" "a run that never completes still exits 0 (the reviewer then reports it as pending)"
if grep -q 'did not finish within 12 minutes' <<<"$out"; then ok "with the warning the workflow checks for"; else bad "with the warning the workflow checks for"; fi

echo "wait-for-check.sh: the newest check run counts (a re-run)"
reset
printf '{"check_runs":[{"name":"check","id":1,"status":"completed"},{"name":"check","id":2,"status":"in_progress"}]}' > "$T/runs_1.json"
printf '{"check_runs":[{"name":"check","id":1,"status":"completed"},{"name":"check","id":2,"status":"completed"}]}' > "$T/runs_2.json"
wait_out >/dev/null
assert_eq "$(calls)" "2" "an old completed run does not end the wait while the re-run is still going"

echo "wait-for-check.sh: a failing gh call is not a missing run"
reset; echo "$none" > "$T/runs_1.json"
rc=0; STUB_GH_FAIL=1 WAIT_SECONDS=2 POLL_SECONDS=1 WAIT_SLEEP=sleep bash scripts/wait-for-check.sh "$SHA" >/dev/null 2>&1 || rc=$?
assert_eq "$rc" "0" "it exits 0"
if [ "$(calls)" -ge 2 ]; then ok "and keeps polling instead of giving up early ($(calls) polls)"; else bad "and keeps polling instead of giving up early ($(calls) polls)"; fi

finish
