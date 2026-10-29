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
assert_grep "$RL" 'types:[[:space:]]*\[opened, synchronize, reopened, labeled\]' "opened, synchronize, reopened, labeled"
# job_block FILE JOB: the lines of one job
job_block() { awk -v j="  $2:" '$0 == j { on = 1; print; next } on && /^  [A-Za-z0-9_-]+:/ { exit } on { print }' "$1"; }
label_job=$(job_block "$RL" label)
if grep -q "github.event.action != 'labeled'" <<<"$label_job"; then ok "the labeling job skips the labeled event"; else bad "the labeling job skips the labeled event"; fi
da_job=$(job_block "$RL" disable-auto-merge)
if grep -q 'gh pr merge .*--disable-auto' <<<"$da_job"; then ok "a job runs gh pr merge --disable-auto"; else bad "a job runs gh pr merge --disable-auto"; fi
if grep -q "github.event.label.name == 'risk:high'" <<<"$da_job" && grep -q "github.event.action == 'labeled'" <<<"$da_job"; then ok "the disable job runs only when the label added is risk:high"; else bad "the disable job runs only when the label added is risk:high"; fi
if grep -q 'AGENTS_PAUSED' <<<"$da_job"; then bad "the disable job has no AGENTS_PAUSED condition"; else ok "the disable job has no AGENTS_PAUSED condition"; fi
if grep -q 'actions/checkout' <<<"$da_job"; then bad "the disable job has no checkout step"; else ok "the disable job has no checkout step"; fi
if grep -q 'create-github-app-token@' <<<"$da_job" && grep -Eq 'GH_TOKEN:[[:space:]]*\$\{\{[[:space:]]*steps\.app\.outputs\.token' <<<"$da_job"; then ok "the disable job uses the App token"; else bad "the disable job uses the App token"; fi
if grep -Eq 'github\.token|secrets\.GITHUB_TOKEN' <<<"$da_job"; then bad "the disable job does not use the workflow token"; else ok "the disable job does not use the workflow token"; fi
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
assert_no_grep "$RV" 'AGENT_GH_TOKEN|PROJECT_TOKEN' "uses neither AGENT_GH_TOKEN nor PROJECT_TOKEN"
# The reviewer's own steps use the workflow token (never the PR author); only the merge gate uses the App token.
rv_review_step=$(awk '/- name: Run the reviewer/ { on = 1; print; next } on && /^      - / { exit } on { print }' "$RV")
if grep -Eq 'GH_TOKEN:[[:space:]]*\$\{\{[[:space:]]*github\.token' <<<"$rv_review_step" && ! grep -q 'steps\.app' <<<"$rv_review_step"; then ok "the reviewer step uses github.token, not the App token"; else bad "the reviewer step uses github.token, not the App token"; fi
rv_merge_step=$(awk '/- name: Enable auto-merge/ { on = 1; print; next } on && /^      - / { exit } on { print }' "$RV")
if grep -Eq 'GH_TOKEN:[[:space:]]*\$\{\{[[:space:]]*steps\.app\.outputs\.token' <<<"$rv_merge_step"; then ok "the merge step uses the App token"; else bad "the merge step uses the App token"; fi
if grep -Eq 'github\.token|secrets\.GITHUB_TOKEN' <<<"$rv_merge_step"; then bad "the merge step does not use github.token"; else ok "the merge step does not use github.token"; fi
assert_grep "$RV" 'actions/create-github-app-token@' "mints the App token"
assert_grep "$RV" 'path:[[:space:]]*trusted' "the merge gate comes from a separate base-branch checkout"
assert_grep "$RV" 'trusted/scripts/after-review\.sh' "runs after-review.sh from the base-branch checkout"
r=$(grep -n 'scripts/review\.sh "' "$RV" | head -1 | cut -d: -f1)
a=$(grep -n 'scripts/after-review\.sh "' "$RV" | head -1 | cut -d: -f1)
if [ -n "$r" ] && [ -n "$a" ] && [ "$r" -lt "$a" ]; then ok "after-review.sh runs after review.sh"; else bad "after-review.sh runs after review.sh"; fi
# The merge gate is its own job on a fresh runner, so no review-job step can alter its env or PATH.
rv_mg=$(awk '/^  merge-gate:/ { on = 1; print; next } on && /^  [a-z-]+:/ { exit } on { print }' "$RV")
rv_rj=$(awk '/^  review:/ { on = 1; print; next } on && /^  [a-z-]+:/ { exit } on { print }' "$RV")
if [ -n "$rv_mg" ]; then ok "there is a separate merge-gate job"; else bad "there is a separate merge-gate job"; fi
if grep -Eq '^    needs:[[:space:]]*review[[:space:]]*$' <<<"$rv_mg"; then ok "merge-gate needs the review job"; else bad "merge-gate needs the review job"; fi
if grep -q "vars\.AGENTS_PAUSED != 'true'" <<<"$rv_mg" && grep -q 'head\.repo\.full_name == github\.repository' <<<"$rv_mg"; then ok "merge-gate keeps the AGENTS_PAUSED and same-repository conditions"; else bad "merge-gate keeps the AGENTS_PAUSED and same-repository conditions"; fi
if [ "$(grep -c 'actions/checkout@' <<<"$rv_mg")" = 1 ] && grep -Eq 'ref:[[:space:]]*\$\{\{[[:space:]]*github\.event\.pull_request\.base\.ref' <<<"$rv_mg" && ! grep -q 'head\.sha\|head\.ref' <<<"$rv_mg"; then ok "merge-gate checks out only the base ref"; else bad "merge-gate checks out only the base ref"; fi
if [ "$(grep -E '^[[:space:]]*run:' <<<"$rv_mg" | grep -vc 'trusted/scripts/')" = 0 ]; then ok "merge-gate runs no script from the PR checkout"; else bad "merge-gate runs no script from the PR checkout"; fi
if grep -q 'create-github-app-token\|steps\.app' <<<"$rv_rj"; then bad "the review job never sees the App token"; else ok "the review job never sees the App token"; fi
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

# A workflow step that runs a script directly (scripts/x.sh, not "bash scripts/x.sh") needs the git
# executable bit, or the runner fails with "Permission denied" (this broke the coordinator and review
# workflows). direct_script_calls FILE prints each repo script a workflow runs directly.
direct_script_calls() {
  grep -E '(^|[[:space:]])(\./)?(scripts|tests/scripts/fixtures)/[A-Za-z0-9_.-]+\.sh' "$1" \
    | grep -vE '^[[:space:]]*#' \
    | grep -vE '(bash|sh)[[:space:]]+(\./)?(scripts|tests/scripts/fixtures)/' \
    | grep -oE '(scripts|tests/scripts/fixtures)/[A-Za-z0-9_.-]+\.sh' | sort -u
}
non_executable() { # path: succeeds when git records the file without the executable bit
  local mode; mode=$(git ls-files -s -- "$1" | awk '{print $1}')
  [ -n "$mode" ] && [ "$mode" != "100755" ]
}

echo "workflows: scripts that a workflow runs directly are executable in git"
if [ -n "$(direct_script_calls "$FIX/direct-call.yml" | while read -r sc; do non_executable "$sc" && echo "$sc"; done)" ]; then
  ok "a fixture that runs a non-executable script directly is reported"
else
  bad "a fixture that runs a non-executable script directly is reported"
fi
for f in .github/workflows/*.yml; do
  [ -f "$f" ] || continue
  for sc in $(direct_script_calls "$f"); do
    if non_executable "$sc"; then bad "$f runs $sc directly but it is not executable (mode $(git ls-files -s -- "$sc" | awk '{print $1}'))"; else ok "$f runs $sc directly and it is executable"; fi
  done
done

# The claude CLI that workflows install must be new enough for the API to accept it (the first review run
# failed with "Claude Code 2.1.0 does not support this model; version 2.1.280 or newer is required"),
# and every workflow must install the same version.
MIN_CLAUDE=2.1.280
claude_pins() { grep -ohE '@anthropic-ai/claude-code@[0-9][0-9.]*' "$1" | sed 's/.*@//' | sort -u; }
too_old() { # version: succeeds when it is older than MIN_CLAUDE
  [ "$(printf '%s\n%s\n' "$1" "$MIN_CLAUDE" | sort -V | head -1)" != "$MIN_CLAUDE" ]
}

echo "workflows: the pinned claude CLI is recent enough, and the same everywhere"
if too_old "$(claude_pins "$FIX/old-claude.yml")"; then ok "a fixture pinned to 2.1.0 is reported as too old"; else bad "a fixture pinned to 2.1.0 is reported as too old"; fi
all_pins=""
for f in .github/workflows/*.yml; do
  [ -f "$f" ] || continue
  for v in $(claude_pins "$f"); do
    all_pins="$all_pins $v"
    if too_old "$v"; then bad "$f installs claude-code@$v, older than $MIN_CLAUDE"; else ok "$f installs claude-code@$v (at least $MIN_CLAUDE)"; fi
  done
done
distinct=$(printf '%s\n' $all_pins | sort -u | grep -c . || true)
if [ "$distinct" -le 1 ]; then ok "all workflows install the same claude-code version"; else bad "all workflows install the same claude-code version (found:$(printf ' %s' $all_pins | tr ' ' '\n' | sort -u | tr '\n' ' '))"; fi

finish
