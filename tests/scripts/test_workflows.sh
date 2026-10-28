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

finish
