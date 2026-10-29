#!/usr/bin/env bash
# Guards against drift: docs/workflow.md must list every secret and variable the workflows use,
# in its "Manual repository settings" section, plus the settings an agent cannot make.
. "$(dirname "$0")/lib.sh"
cd "$ROOT" || exit 1

DOC="docs/workflow.md"
WF_DIR=".github/workflows"
CLAUDE_MD="CLAUDE.md"

# section FILE: prints the "Manual repository settings" section (heading to the next "## " heading).
section() { awk '/^## Manual repository settings/{f=1; print; next} f && /^## /{exit} f' "$1"; }

# refs DIR KIND: names referenced as KIND.NAME in the workflows. GITHUB_TOKEN is automatic, not a setting.
refs() { grep -rhoE "\b$2\.[A-Z][A-Z0-9_]*" "$1"/*.yml 2>/dev/null | sed "s/^$2\.//" | grep -vx GITHUB_TOKEN | sort -u; }

check_doc() { # doc workflows_dir
  local doc="$1" dir="$2" sec name
  sec=$(section "$doc")
  if [ -z "$sec" ]; then bad "$doc has a 'Manual repository settings' section"; return; fi
  ok "$doc has a 'Manual repository settings' section"
  for name in $(refs "$dir" secrets); do
    if grep -Fq -- "$name" <<<"$sec"; then ok "secret $name is listed"; else bad "secret $name is listed"; fi
  done
  for name in $(refs "$dir" vars); do
    if grep -Fq -- "$name" <<<"$sec"; then ok "variable $name is listed"; else bad "variable $name is listed"; fi
  done
}

echo "docs: every secret and variable the workflows use is in the manual settings section"
check_doc "$DOC" "$WF_DIR"

echo "docs: a new secret or variable in a workflow makes the check fail (fixture)"
new_sandbox
mkdir -p "$T/wf"
cp "$WF_DIR"/*.yml "$T/wf/"
printf 'jobs:\n  x:\n    steps:\n      - run: echo ${{ secrets.NEW_ONE }} ${{ vars.NEW_VAR }}\n' > "$T/wf/fixture.yml"
out=$(check_doc "$DOC" "$T/wf")
if grep -q 'FAIL  secret NEW_ONE' <<<"$out" && grep -q 'FAIL  variable NEW_VAR' <<<"$out"; then
  ok "undocumented secret and variable are reported"
else
  bad "undocumented secret and variable are reported"
fi

echo "docs: the section names the settings the owner must make"
sec=$(section "$DOC")
for phrase in '`check`' 'Require review from Code Owners' 'Allow auto-merge' 'Automatically delete head branches' \
  'Allow GitHub Actions to create and approve pull requests' 'claude setup-token' 'scripts/setup-project.sh' \
  'risk:high' 'needs-human' '60 days' 'Do not allow bypassing'; do
  if grep -Fq -- "$phrase" <<<"$sec"; then ok "mentions $phrase"; else bad "mentions $phrase"; fi
done

echo "docs: no stale 'until #4' wording"
assert_no_grep "$DOC" 'until #4' "workflow.md has no 'until #4'"
assert_no_grep "$DOC" '#4 (lands|will take)' "workflow.md does not say #4 is pending"

echo "docs: the gate is described, and the protected list is not copied"
assert_grep "$DOC" '\.github/risk-paths' "workflow.md points to .github/risk-paths"
assert_grep "$DOC" 'AGENTS_PAUSED' "workflow.md describes the kill switch"
assert_no_grep "$DOC" 'src/mealplanner/config\.py' "workflow.md does not copy the protected list"
assert_grep "$CLAUDE_MD" 'risk:high' "CLAUDE.md mentions risk:high"
assert_grep README.md 'Running the agents' "README.md has a 'Running the agents' note"

finish
