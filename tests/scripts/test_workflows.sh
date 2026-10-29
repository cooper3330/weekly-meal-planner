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
  # A workflow that runs claude must not leave the read-only checkout token in git's config: git would
  # push with it instead of the App token (#66).
  if grep -Eq "$RUNS_CLAUDE_RE" <<<"$body"; then
    awk '
      function flush() { if (co && !ok) print "checkout leaves persist-credentials at its default (set persist-credentials: false)"; co = 0; ok = 0 }
      /^[[:space:]]*#/ { next }
      /^[[:space:]]*-[[:space:]]/ { flush() }
      /uses:[[:space:]]*actions\/checkout@/ { co = 1 }
      /persist-credentials:[[:space:]]*false/ { ok = 1 }
      END { flush() }
    ' "$f"
  fi
  # The daily cap is a repository variable (ADR 0015); the guard reads it from its environment, so the step
  # that runs the preflight must pass it (#71). Without it the guard silently uses its default of 12.
  awk '
    function flush() { if (pre && !cap) print "runs the preflight without passing vars.AGENT_DAILY_RUNS in that step"; pre = 0; cap = 0 }
    /^[[:space:]]*#/ { next }
    /^[[:space:]]*-[[:space:]]/ { flush() }
    /agent-guard\.sh[[:space:]]+preflight/ { pre = 1 }
    /AGENT_DAILY_RUNS:[[:space:]]*\$\{\{[[:space:]]*vars\.AGENT_DAILY_RUNS[[:space:]]*\}\}/ { cap = 1 }
    END { flush() }
  ' "$f"
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
            "bad-no-paused:AGENTS_PAUSED" "bad-merge-no-paused:AGENTS_PAUSED" "bad-persist-credentials:persist-credentials" \
            "bad-no-cap-var:AGENT_DAILY_RUNS" "bad-cap-var-other-step:AGENT_DAILY_RUNS"; do
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

# --- git pushes as the GitHub App, not as the read-only checkout token (#66, ADR 0014) ---------
for f in .github/workflows/coordinator.yml .github/workflows/review.yml; do
  assert_grep "$f" 'persist-credentials:[[:space:]]*false' "$f: checkout does not persist the workflow token"
done
CO_=.github/workflows/coordinator.yml
assert_grep "docs/workflow.md" 'Contents: Read & write' "workflow.md lists the App's Contents permission"
assert_grep "docs/workflow.md" 'Pull requests: Read & write' "workflow.md lists the App's pull request permission"
assert_grep "$CO_" 'gh auth setup-git' "coordinator.yml: git authenticates through gh auth setup-git"
mint=$(grep -n 'create-github-app-token' "$CO_" | head -1 | cut -d: -f1)
setup=$(grep -n 'gh auth setup-git' "$CO_" | head -1 | cut -d: -f1)
run=$(grep -n 'run: scripts/coordinator.sh' "$CO_" | head -1 | cut -d: -f1)
if [ -n "$mint" ] && [ -n "$setup" ] && [ -n "$run" ] && [ "$mint" -lt "$setup" ] && [ "$setup" -lt "$run" ]; then ok "setup-git runs after the App token is minted and before the coordinator"; else bad "setup-git runs after the App token is minted and before the coordinator (mint=$mint setup=$setup run=$run)"; fi
step=$(awk '/- name:/ { name = $0 } /gh auth setup-git/ { print name; exit }' "$CO_")
if sed -n "/$(printf '%s' "$step" | sed 's/[][\.*^$/]/\\&/g')/,/gh auth setup-git/p" "$CO_" | grep -Eq 'GH_TOKEN:[[:space:]]*\$\{\{[[:space:]]*steps\.app\.outputs\.token'; then ok "that step's GH_TOKEN is the App token"; else bad "that step's GH_TOKEN is the App token"; fi

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
# At job level, so a run whose job if: is false (an unrelated label, a stranger's issue) never enters
# the group and cannot cancel a pending run (#55). Workflow-level concurrency would apply first.
assert_grep "$CO" '^    concurrency:' "concurrency is set on the job"
assert_no_grep "$CO" '^concurrency:' "and not on the workflow"
assert_grep "$CO" '^      group:[[:space:]]*coordinator[[:space:]]*$' "concurrency group is coordinator"
assert_grep "$CO" '^      cancel-in-progress:[[:space:]]*false' "never cancels a run in progress"
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

# --- event-driven triggers (#55, ADR 0015) ---------------------------------------------------------
# job_if FILE: the whole job-level if: expression (continuation lines joined), one per file.
job_if() {
  awk '/^    if:/ { on = 1; print; next } on && /^    [A-Za-z-]+:/ { exit } on { print }' "$1" | tr '\n' ' '
}
# trigger_types FILE TRIGGER: the `types:` value under the trigger.
trigger_types() {
  awk -v t="$2" '
    $0 ~ "^  " t ":" { inb = 1; next }
    inb && /^  [^ ]/ { inb = 0 }
    inb && /^    types:/ { sub(/^    types:[ ]*/, ""); print; exit }
  ' "$1"
}
# trigger_violations FILE: an issues:labeled trigger needs a job condition naming the label.
trigger_violations() {
  local f="$1" cond
  cond=$(job_if "$f")
  if grep -Eq '^  issues:' "$f" && trigger_types "$f" issues | grep -q labeled \
     && ! grep -Eq "github\.event\.label\.name[[:space:]]*==[[:space:]]*'ready'" <<<"$cond"; then
    echo "issues: labeled has no job condition on the label name"
  fi
  # A closed issue can be opened and closed by any stranger (the issue template labels it type:task),
  # and every run counts toward the daily cap: the closed branch must name a trusted author.
  if grep -Eq '^  issues:' "$f" && trigger_types "$f" issues | grep -q closed \
     && ! grep -Eq "github\.event\.issue\.author_association" <<<"$cond"; then
    echo "issues: closed has no trusted-author condition (author_association)"
  fi
}

echo "workflows: the label-trigger checker bites on a fixture with no label-name condition"
if [ -n "$(trigger_violations "$FIX/workflows/bad-label-no-name.yml")" ]; then ok "reported as a violation"; else bad "reported as a violation"; fi

echo "workflows: the closed-issue checker bites on a fixture with no trusted-author condition"
if trigger_violations "$FIX/workflows/bad-closed-no-actor.yml" | grep -q 'trusted-author'; then ok "reported as a violation"; else bad "reported as a violation"; fi

echo "workflows: coordinator.yml event triggers"
assert_grep "$CO" '^  issues:' "has an issues trigger"
assert_grep "$CO" '^  pull_request:' "has a pull_request trigger"
assert_eq "$(trigger_types "$CO" pull_request)" "[closed]" "pull_request types: closed"
assert_eq "$(trigger_branches "$CO" pull_request)" "[main]" "pull_request restricted to main"
types=$(trigger_types "$CO" issues)
if grep -q labeled <<<"$types" && grep -q closed <<<"$types"; then ok "issues types: labeled and closed"; else bad "issues types: labeled and closed (got $types)"; fi
assert_no_grep "$CO" 'pull_request_target' "never uses pull_request_target"
cond=$(job_if "$CO")
if [ -z "$(trigger_violations "$CO")" ]; then ok "the labeled job condition names the label"; else bad "the labeled job condition names the label"; fi
if [ -z "$(trigger_violations "$CO" | grep trusted-author)" ]; then ok "the closed job condition names a trusted author"; else bad "the closed job condition names a trusted author"; fi
for role in OWNER MEMBER COLLABORATOR; do
  if grep -qF -- "'$role'" <<<"$cond"; then ok "closed issues from a $role start a pass"; else bad "closed issues from a $role start a pass"; fi
done
for want in "github.event.label.name == 'ready'" "type:task" "type:bug" "github.event.pull_request.merged == true" \
            "startsWith(github.event.pull_request.head.ref, 'issue-')" \
            "github.event.pull_request.head.repo.full_name == github.repository" "vars.AGENTS_PAUSED != 'true'"; do
  if grep -qF -- "$want" <<<"$cond"; then ok "job condition has: $want"; else bad "job condition has: $want"; fi
done
assert_grep "$CO" 'scripts/coordinator\.sh --ready-label \$\{\{[[:space:]]*github\.event\.issue\.number[[:space:]]*\}\}' "a label event runs --ready-label with the issue number"
assert_grep "$CO" 'scripts/coordinator\.sh --once' "other events run --once"
rl_line=$(grep -n -- '--ready-label' "$CO" | head -1 | cut -d: -f1)
if [ -n "$rl_line" ] && [ -n "$g" ] && [ "$g" -lt "$rl_line" ]; then ok "preflight runs before the label-event pass too"; else bad "preflight runs before the label-event pass too"; fi

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
# review_budget_violations FILE: the reviewer's time budget (#61). The wait loop is bounded to at most
# 720 seconds and its warning says 12 minutes; the job timeout is 30; the wait step has timeout 12 and
# the review step 18; wait step + review step <= job timeout.
review_budget_violations() {
  local f="$1" bound job wstep rstep
  bound=$(sed -nE 's/.*while \[ "\$waited" -lt ([0-9]+) \].*/\1/p' "$f" | head -1)
  job=$(sed -nE 's/^    timeout-minutes:[[:space:]]*([0-9]+).*/\1/p' "$f" | head -1)
  wstep=$(awk '/^      - name:/ { n = $0 } /^        timeout-minutes:/ && n ~ /Wait for the check/ { print $2; exit }' "$f")
  rstep=$(awk '/^      - name:/ { n = $0 } /^        timeout-minutes:/ && n ~ /Run the reviewer/ { print $2; exit }' "$f")
  if [ ! -r "$f" ] || [ -z "$bound" ] || [ -z "$job" ]; then echo "could not read the time budget from $f"; return; fi
  if [ "$bound" -gt 720 ]; then echo "wait bound is '$bound' seconds, want at most 720"; fi
  # The loop is bounded by wall-clock time, and a timed-out wait step must not skip the reviewer.
  { grep -q 'start=\$SECONDS' "$f" && grep -q 'waited=\$((SECONDS - start))' "$f"; } || echo "wait loop is not bounded by wall-clock time (SECONDS)"
  awk '/^      - name:/ { n = $0 } /^        continue-on-error:[[:space:]]*true/ && n ~ /Wait for the check/ { f = 1 } END { exit !f }' "$f" || echo "wait step is not continue-on-error, so a timeout would skip the reviewer"
  grep -q 'did not finish within 12 minutes' "$f" || echo "warning text does not say 12 minutes"
  [ "$job" = "30" ] || echo "job timeout-minutes is '$job', want 30"
  [ "$wstep" = "12" ] || echo "wait step timeout-minutes is '$wstep', want 12"
  [ "$rstep" = "18" ] || echo "review step timeout-minutes is '$rstep', want 18"
  # The wait step plus the review step must fit in the job timeout (12 + 18 = 30).
  if [ -n "$wstep" ] && [ -n "$job" ] && [ -n "$rstep" ] && [ $((wstep + rstep)) -gt "$job" ]; then
    echo "wait step ($wstep) + review step ($rstep) exceeds the job timeout ($job)"
  fi
  if [ -n "$job" ] && { [ "${wstep:-0}" -gt "$job" ] || [ "${rstep:-0}" -gt "$job" ]; }; then
    echo "a step timeout exceeds the job timeout ($job)"
  fi
}
echo "workflows: the time-budget checker bites on fixtures"
if review_budget_violations "$FIX/review-wait900.yml" | grep -q 'wait bound'; then ok "a 900-second wait is reported"; else bad "a 900-second wait is reported"; fi
if review_budget_violations "$FIX/review-timeout20.yml" | grep -q 'job timeout-minutes'; then ok "a 20-minute job timeout is reported"; else bad "a 20-minute job timeout is reported"; fi
if [ "$(review_budget_violations "$FIX/review-timeout20.yml" | grep -c 'wait bound\|job timeout-minutes')" = 2 ]; then ok "a 20-minute job with a 900-second wait is reported for both"; else bad "a 20-minute job with a 900-second wait is reported for both"; fi
rbv=$(review_budget_violations "$RV")
if [ -z "$rbv" ]; then ok "review.yml: wait <= 720 s (12 minutes), job 30, wait step 12, review step 18"; else bad "review.yml time budget: $rbv"; fi
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
