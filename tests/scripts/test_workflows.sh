#!/usr/bin/env bash
# Structural tests over .github/workflows/*.yml, using grep/awk only (no YAML dependency).
. "$(dirname "$0")/lib.sh"
cd "$ROOT" || exit 1
FIX=tests/scripts/fixtures

# trigger_branches FILE TRIGGER: prints the `branches:` value under the trigger (empty if none).
trigger_branches() {
  awk -v t="$2" '
    $0 ~ "^  " t ":" { inb = 1; next }
    inb && /^  [^ ]/ { inb = 0 }
    inb && /^    branches:/ { sub(/^    branches:[ ]*/, ""); print; exit }
  ' "$1"
}
# unpinned_uses FILE: prints every `uses:` line not pinned to a 40-hex-character SHA.
unpinned_uses() {
  grep -E '^[[:space:]-]*uses:' "$1" | grep -Ev 'uses:[[:space:]]*[^@[:space:]]+@[0-9a-f]{40}([[:space:]]|$)' || true
}

echo "workflows: SHA-pin checker bites on a fixture using @v4"
if [ -n "$(unpinned_uses "$FIX/unpinned.yml")" ]; then ok "@v4 reported as a violation"; else bad "@v4 reported as a violation"; fi
echo "workflows: trigger checker bites on bad fixtures"
assert_eq "$(trigger_branches "$FIX/unpinned.yml" pull_request)" "" "pull_request without branches filter yields nothing"
assert_eq "$(trigger_branches "$FIX/wrong-branch.yml" pull_request)" "[develop]" "different branch is visible"

echo "workflows: every uses: in every workflow is pinned to a SHA"
for f in .github/workflows/*.yml; do
  [ -f "$f" ] || continue
  v=$(unpinned_uses "$f")
  if [ -z "$v" ]; then ok "$f pins its actions"; else bad "$f has unpinned actions: $v"; fi
done

# --- agent auth and kill-switch checks (ADR 0015; AC-8, AC-10 of #4) -------------------------
# A workflow "runs claude" when a non-comment line runs the claude CLI, or a script that launches it.
RUNS_CLAUDE_RE='(^|[[:space:]|&;(])claude[[:space:]]|scripts/(coordinator|review)\.sh|agent-guard\.sh[[:space:]]+run'

# workflow_violations FILE: prints one line per violation (nothing when the workflow is fine).
#  - file level: a workflow that runs claude passes CLAUDE_CODE_OAUTH_TOKEN from the secret; no
#    workflow sets ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / DISABLE_TELEMETRY or uses --bare.
#  - job level: a job that runs claude or enables auto-merge (gh pr merge without --disable-auto)
#    has a job-level if: containing vars.AGENTS_PAUSED != 'true'.
workflow_violations() {
  local f="$1" body
  body=$(grep -Ev '^[[:space:]]*#' "$f")
  if grep -Eq "$RUNS_CLAUDE_RE" <<<"$body" \
     && ! grep -Eq 'CLAUDE_CODE_OAUTH_TOKEN:[[:space:]]*\$\{\{[[:space:]]*secrets\.CLAUDE_CODE_OAUTH_TOKEN[[:space:]]*\}\}' <<<"$body"; then
    echo "runs claude without CLAUDE_CODE_OAUTH_TOKEN from secrets.CLAUDE_CODE_OAUTH_TOKEN"
  fi
  grep -Eq 'ANTHROPIC_API_KEY' <<<"$body" && echo "sets ANTHROPIC_API_KEY"
  grep -Eq 'ANTHROPIC_AUTH_TOKEN' <<<"$body" && echo "sets ANTHROPIC_AUTH_TOKEN"
  grep -Eq -- '--bare' <<<"$body" && echo "uses --bare"
  grep -Eq 'DISABLE_TELEMETRY' <<<"$body" && echo "sets DISABLE_TELEMETRY"
  awk -v runs_re="$RUNS_CLAUDE_RE" '
    function flush() {
      if (job != "" && (runs || merges) && !paused)
        print "job " job " runs claude or enables auto-merge without an AGENTS_PAUSED job-level if"
    }
    /^[[:space:]]*#/ { next }
    /^jobs:/ { injobs = 1; next }
    injobs && /^[^ ]/ { flush(); job = ""; injobs = 0 }
    injobs && /^  [A-Za-z0-9_-]+:[[:space:]]*$/ {
      flush(); job = $1; sub(/:$/, "", job); runs = 0; merges = 0; paused = 0; inif = 0; next
    }
    job == "" { next }
    /^    if:/ { inif = 1; ifbody = $0 }
    inif && /^    [^ ]/ && !/^    if:/ { inif = 0 }
    inif && /^      / { ifbody = ifbody " " $0 }
    inif && ifbody ~ /vars\.AGENTS_PAUSED[[:space:]]*!=[[:space:]]*.true./ { paused = 1 }
    $0 ~ runs_re { runs = 1 }
    /gh pr merge/ && !/--disable-auto/ { merges = 1 }
    END { flush() }
  ' "$f"
}

echo "workflows: the auth and kill-switch checker passes a good fixture"
v=$(workflow_violations "$FIX/workflows/good.yml")
if [ -z "$v" ]; then ok "good.yml has no violations"; else bad "good.yml has no violations: $v"; fi
v=$(workflow_violations "$FIX/workflows/ok-disable-auto-only.yml")
if [ -z "$v" ]; then ok "a job that only runs gh pr merge --disable-auto is exempt"; else bad "disable-auto-only job is exempt: $v"; fi

echo "workflows: the auth and kill-switch checker bites on bad fixtures"
for pair in "bad-api-key:ANTHROPIC_API_KEY" "bad-auth-token:ANTHROPIC_AUTH_TOKEN" "bad-bare:--bare" \
            "bad-telemetry:DISABLE_TELEMETRY" "bad-no-oauth:CLAUDE_CODE_OAUTH_TOKEN" \
            "bad-no-paused:AGENTS_PAUSED" "bad-merge-no-paused:AGENTS_PAUSED"; do
  name="${pair%%:*}"; want="${pair#*:}"
  v=$(workflow_violations "$FIX/workflows/$name.yml")
  if grep -q -- "$want" <<<"$v"; then ok "$name is reported ($want)"; else bad "$name is reported ($want); got: $v"; fi
done

echo "workflows: every real workflow passes the auth and kill-switch checks"
checked=0
claude_files=0
for f in .github/workflows/*.yml; do
  [ -f "$f" ] || continue
  checked=$((checked + 1))
  if grep -Ev '^[[:space:]]*#' "$f" | grep -Eq "$RUNS_CLAUDE_RE"; then claude_files=$((claude_files + 1)); fi
  v=$(workflow_violations "$f")
  if [ -z "$v" ]; then ok "$f passes"; else bad "$f violates: $v"; fi
done
echo "  checked $checked workflows, $claude_files of them run claude"
if [ "$checked" -ge 1 ]; then ok "the checker saw $checked workflows"; else bad "the checker saw at least one workflow"; fi

CI=.github/workflows/ci.yml
echo "workflows: ci.yml"
if [ -f "$CI" ]; then ok "ci.yml exists"; else bad "ci.yml exists"; finish; exit; fi
assert_grep "$CI" '^  check:' "has a job with id check"
assert_eq "$(trigger_branches "$CI" pull_request)" "[main]" "pull_request restricted to main"
assert_eq "$(trigger_branches "$CI" push)" "[main]" "push restricted to main"
assert_grep "$CI" '^[[:space:]]+run:[[:space:]]+make check[[:space:]]*$' "runs make check"
assert_no_grep "$CI" 'secrets\.' "no secrets reference"
assert_grep "$CI" '^permissions:' "declares permissions"
assert_grep "$CI" '^  contents:[[:space:]]+read' "contents: read"
assert_no_grep "$CI" ':[[:space:]]+write' "no write permission"
assert_grep "$CI" 'timeout-minutes:[[:space:]]+10' "sets timeout-minutes"
assert_grep "$CI" 'uses:[[:space:]]+astral-sh/setup-uv@' "installs uv with setup-uv"
assert_grep "$CI" 'python-version:[[:space:]]+"?3\.12"?' "uses Python 3.12"

RL=.github/workflows/risk-label.yml
echo "workflows: risk-label.yml"
assert_eq "$(trigger_branches "$FIX/no-branch-filter.yml" pull_request_target)" "" "fixture without branch filter is visible"
if [ -f "$RL" ]; then ok "risk-label.yml exists"; else bad "risk-label.yml exists"; finish; exit; fi
assert_grep "$RL" '^  pull_request_target:' "uses pull_request_target"
assert_eq "$(trigger_branches "$RL" pull_request_target)" "[main]" "restricted to main"
assert_grep "$RL" 'types:[[:space:]]*\[opened, synchronize, reopened\]' "opened, synchronize, reopened"
assert_no_grep "$RL" 'ref:' "checkout has no ref: (base only)"
assert_no_grep "$RL" 'github\.event\.pull_request\.head' "never references the PR head"
assert_no_grep "$RL" 'remove-label' "never removes the label"
assert_no_grep "$RL" 'contents:[[:space:]]+write' "no contents: write"
assert_grep "$RL" 'pull-requests:[[:space:]]+write' "pull-requests: write"
assert_grep "$RL" 'pulls/.*/files.* --paginate' "lists PR files with pagination"
assert_grep "$RL" 'previous_filename' "includes previous paths of renames"
assert_grep "$RL" 'scripts/classify-risk\.sh' "runs the classifier"
assert_grep "$RL" '--add-label risk:high' "adds risk:high"

CO=.github/workflows/coordinator.yml
echo "workflows: coordinator.yml"
if [ -f "$CO" ]; then ok "coordinator.yml exists"; else bad "coordinator.yml exists"; finish; exit; fi
assert_grep "$CO" '^  schedule:' "has a schedule trigger"
assert_grep "$CO" "cron:[[:space:]]*['\"]17 \* \* \* \*['\"]" "runs hourly at minute 17"
assert_grep "$CO" '^  workflow_dispatch:' "has workflow_dispatch"
assert_grep "$CO" "^    if:[[:space:]]*vars\.AGENTS_PAUSED != 'true'" "job is gated by the AGENTS_PAUSED kill switch"
assert_grep "$CO" '^  group:[[:space:]]*coordinator[[:space:]]*$' "concurrency group is coordinator"
assert_grep "$CO" '^  cancel-in-progress:[[:space:]]*false' "never cancels a run in progress"
tm=$(sed -nE 's/^    timeout-minutes:[[:space:]]*([0-9]+).*/\1/p' "$CO" | head -1)
if [ -n "$tm" ] && [ "$tm" -le 55 ]; then ok "timeout-minutes is $tm (at most 55)"; else bad "timeout-minutes is at most 55 (got '$tm')"; fi
assert_grep "$CO" '^  contents:[[:space:]]+read' "contents: read"
assert_no_grep "$CO" ':[[:space:]]+write' "no write permission (the App token carries the writes)"
assert_grep "$CO" 'fetch-depth:[[:space:]]*0' "checks out full history"
g=$(grep -n 'scripts/agent-guard.sh preflight' "$CO" | head -1 | cut -d: -f1)
c=$(grep -n 'scripts/coordinator.sh --once' "$CO" | head -1 | cut -d: -f1)
m=$(grep -n 'actions/create-github-app-token@' "$CO" | head -1 | cut -d: -f1)
i=$(grep -n '@anthropic-ai/claude-code@[0-9]' "$CO" | head -1 | cut -d: -f1)
if [ -n "$g" ] && [ -n "$c" ] && [ "$g" -lt "$c" ]; then ok "preflight runs before the coordinator"; else bad "preflight runs before the coordinator"; fi
if [ -n "$m" ] && [ -n "$i" ] && [ -n "$g" ] && [ "$m" -lt "$i" ] && [ "$i" -lt "$g" ]; then ok "token minted, claude installed, then preflight"; else bad "token minted, claude installed, then preflight"; fi
assert_grep "$CO" '78' "handles preflight exit code 78"
assert_grep "$CO" '::notice' "78 ends the job with a notice"
assert_grep "$CO" 'GH_TOKEN:[[:space:]]*\$\{\{[[:space:]]*steps\.[A-Za-z_-]+\.outputs\.token[[:space:]]*\}\}' "GH_TOKEN comes from the App-token step output"
assert_grep "$CO" 'AGENT_GH_TOKEN:[[:space:]]*\$\{\{[[:space:]]*steps\.[A-Za-z_-]+\.outputs\.token' "AGENT_GH_TOKEN comes from the App-token step output"
assert_no_grep "$CO" 'GH_TOKEN:[[:space:]]*\$\{\{[[:space:]]*secrets\.PROJECT_TOKEN' "GH_TOKEN is never the project token"
assert_grep "$CO" '^[[:space:]]+PROJECT_TOKEN:[[:space:]]*\$\{\{[[:space:]]*secrets\.PROJECT_TOKEN' "PROJECT_TOKEN comes from secrets"
assert_grep "$CO" 'PROJECT_NUMBER:[[:space:]]*\$\{\{[[:space:]]*vars\.PROJECT_NUMBER[[:space:]]*\|\|[[:space:]]*.1.[[:space:]]*\}\}' "PROJECT_NUMBER defaults to 1"
assert_grep "$CO" 'MAX_PARALLEL:[[:space:]]*\$\{\{[[:space:]]*vars\.MAX_PARALLEL[[:space:]]*\|\|[[:space:]]*.1.[[:space:]]*\}\}' "MAX_PARALLEL defaults to 1"
assert_grep "$CO" 'AGENT_GIT_NAME' "sets AGENT_GIT_NAME"
assert_grep "$CO" 'AGENT_GIT_EMAIL' "sets AGENT_GIT_EMAIL"
assert_no_grep "$CO" 'ANTHROPIC_' "sets no ANTHROPIC_* variable"

RV=.github/workflows/review.yml
echo "workflows: review.yml"
assert_eq "$(trigger_branches "$FIX/unpinned.yml" pull_request)" "" "a fixture without the branches filter is visible as a violation"
if [ -f "$RV" ]; then ok "review.yml exists"; else bad "review.yml exists"; finish; exit; fi
assert_grep "$RV" '^name:[[:space:]]*Review[[:space:]]*$' "is named Review (the daily cap counts that name)"
assert_grep "$RV" '^  pull_request:' "uses pull_request"
assert_no_grep "$RV" 'pull_request_target' "never uses pull_request_target"
assert_eq "$(trigger_branches "$RV" pull_request)" "[main]" "pull_request restricted to main"
assert_grep "$RV" 'types:[[:space:]]*\[opened, synchronize, ready_for_review, reopened\]' "opened, synchronize, ready_for_review, reopened"
rv_if=$(awk '/^    if:/ { on = 1 } on && /^    [a-z-]+:/ && !/^    if:/ { exit } on { print }' "$RV")
if grep -q 'github\.event\.pull_request\.head\.repo\.full_name == github\.repository' <<<"$rv_if"; then ok "job runs only for PRs from this repository"; else bad "job runs only for PRs from this repository"; fi
if grep -q 'github\.event\.pull_request\.draft == false\|!github\.event\.pull_request\.draft' <<<"$rv_if"; then ok "drafts are excluded"; else bad "drafts are excluded"; fi
if grep -q "startsWith(github.event.pull_request.head.ref, 'issue-')" <<<"$rv_if" && grep -q "startsWith(github.event.pull_request.head.ref, 'arch-')" <<<"$rv_if"; then ok "head branch must match issue-* or arch-*"; else bad "head branch must match issue-* or arch-*"; fi
if grep -q "vars\.AGENTS_PAUSED != 'true'" <<<"$rv_if"; then ok "job is gated by the AGENTS_PAUSED kill switch"; else bad "job is gated by the AGENTS_PAUSED kill switch"; fi
assert_grep "$RV" '^  group:[[:space:]]*review-\$\{\{[[:space:]]*github\.event\.pull_request\.number[[:space:]]*\}\}' "concurrency group is keyed by PR number"
assert_grep "$RV" '^  cancel-in-progress:[[:space:]]*true' "a newer push supersedes the review"
tm=$(sed -nE 's/^    timeout-minutes:[[:space:]]*([0-9]+).*/\1/p' "$RV" | head -1)
if [ -n "$tm" ] && [ "$tm" -le 20 ]; then ok "timeout-minutes is $tm (at most 20)"; else bad "timeout-minutes is at most 20 (got '$tm')"; fi
assert_grep "$RV" '^  contents:[[:space:]]+read' "contents: read"
assert_grep "$RV" '^  pull-requests:[[:space:]]+write' "pull-requests: write"
assert_grep "$RV" '^  issues:[[:space:]]+read' "issues: read"
assert_grep "$RV" '^  checks:[[:space:]]+read' "checks: read"
assert_no_grep "$RV" 'contents:[[:space:]]+write' "no contents: write"
assert_grep "$RV" 'GH_TOKEN:[[:space:]]*\$\{\{[[:space:]]*github\.token[[:space:]]*\}\}' "GH_TOKEN is github.token"
assert_no_grep "$RV" 'create-github-app-token|AGENT_APP|AGENT_GH_TOKEN|PROJECT_TOKEN' "does not use the App token (the reviewer is never the PR author)"
assert_grep "$RV" 'CLAUDE_CODE_OAUTH_TOKEN:[[:space:]]*\$\{\{[[:space:]]*secrets\.CLAUDE_CODE_OAUTH_TOKEN' "CLAUDE_CODE_OAUTH_TOKEN comes from secrets"
assert_no_grep "$RV" 'ANTHROPIC_' "sets no ANTHROPIC_* variable"
assert_grep "$RV" 'check-runs|gh pr checks' "waits for the check status on the head SHA"
assert_grep "$RV" 'github\.event\.pull_request\.head\.sha' "looks at the head SHA"
assert_grep "$RV" '(900|15 \* 60|15)' "the wait is bounded to 15 minutes"
w=$(grep -n 'check-runs\|gh pr checks' "$RV" | head -1 | cut -d: -f1)
g=$(grep -n 'scripts/agent-guard.sh preflight' "$RV" | head -1 | cut -d: -f1)
c=$(grep -n 'scripts/review.sh' "$RV" | head -1 | cut -d: -f1)
if [ -n "$w" ] && [ -n "$g" ] && [ -n "$c" ] && [ "$w" -lt "$g" ] && [ "$g" -lt "$c" ]; then ok "wait for check, then preflight, then review.sh"; else bad "wait for check, then preflight, then review.sh"; fi
assert_grep "$RV" '78' "handles preflight exit code 78"

finish
