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

finish
