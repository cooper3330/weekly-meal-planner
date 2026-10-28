#!/usr/bin/env bash
# Tests for scripts/classify-risk.sh, .github/risk-paths and .github/CODEOWNERS (offline).
. "$(dirname "$0")/lib.sh"
cd "$ROOT" || exit 1
SCRIPT=scripts/classify-risk.sh
RULES=.github/risk-paths
CO=.github/CODEOWNERS
FIX=tests/scripts/fixtures

# classify PATH...: prints the verdict for the given paths (one per line).
classify() { printf '%s\n' "$@" | bash "$SCRIPT"; }
expect() { # verdict path...
  local want="$1"; shift
  assert_eq "$(classify "$@")" "$want" "$want: $*"
}

echo "classify: low cases"
for p in src/mealplanner/planning/x.py tests/unit/test_x.py docs/product-spec.md README.md \
         pyproject.toml uv.lock Makefile tests/scripts/lib.sh tests/conftest.py src/mealplanner/web/auth.py; do
  expect low "$p"
done

echo "classify: high, one per rule"
for p in docs/adr/0099-x.md .claude/agents/developer.md .github/workflows/ci.yml scripts/coordinator.sh \
         CLAUDE.md src/mealplanner/CLAUDE.md docs/constitution.md docs/workflow.md \
         .gitignore src/.gitignore src/mealplanner/config.py; do
  expect high "$p"
done

echo "classify: mixed, empty, line endings, spaces, renames"
expect high README.md src/a.py scripts/x.sh tests/y.py
assert_eq "$(printf '' | bash "$SCRIPT")" "low" "empty input is low"
assert_eq "$(printf 'scripts/x.sh' | bash "$SCRIPT")" "high" "no trailing newline"
assert_eq "$(printf 'scripts/x.sh\r\n' | bash "$SCRIPT")" "high" "CRLF still matches"
assert_eq "$(printf 'src/a.py\r\nREADME.md\r\n' | bash "$SCRIPT")" "low" "CRLF low"
expect high "docs/adr/my note.md"
expect low "docs/my notes.md"
expect high "scripts/old-name.sh" "src/new.py"
expect high "src/old.py" "scripts/new-name.sh"

echo "classify: boundaries"
expect low scripts-old/x docs/adr-notes.md .github-x
expect low src/mealplanner/config.py.bak .env.example src/.env.example
expect high docs/adr/sub/dir/x.md
expect low x.gitignore CLAUDE.md.bak src/NOTCLAUDE.md
expect low tests/scripts/lib.sh src/scripts/x.py

echo "classify: rules file"
new_sandbox
printf '# comment\n\n  \nfoo/\n**/BAR.md\nexact.txt\n' > "$T/rules"
r() { printf '%s\n' "$@" | bash "$SCRIPT" --rules "$T/rules"; }
assert_eq "$(r foo/x)" "high" "--rules dir prefix"
assert_eq "$(r a/b/BAR.md)" "high" "--rules any depth"
assert_eq "$(r exact.txt)" "high" "--rules exact"
assert_eq "$(r scripts/x.sh)" "low" "--rules overrides the default list"
assert_eq "$(r '# comment')" "low" "comment lines are not rules"
assert_eq "$(r '')" "low" "blank lines are not rules"
printf 'x\n' | bash "$SCRIPT" --rules "$T/missing" > "$T/out" 2> "$T/err"; rc=$?
if [ "$rc" -ne 0 ]; then ok "missing rules file exits non-zero"; else bad "missing rules file exits non-zero"; fi
assert_no_grep "$T/out" 'low' "missing rules file does not print low"
assert_grep "$T/err" 'rules file' "missing rules file explains itself"
: > "$T/empty"
printf 'x\n' | bash "$SCRIPT" --rules "$T/empty" > "$T/out" 2> "$T/err"; rc=$?
if [ "$rc" -ne 0 ]; then ok "rules file with no rules fails closed"; else bad "rules file with no rules fails closed"; fi

echo "classify: no network"
grep -Ev '^[[:space:]]*#' "$SCRIPT" > "$T/code"
assert_no_grep "$T/code" '(^|[^[:alnum:]_-])(gh|curl|wget)[[:space:]]' "script calls no gh/curl/wget"

echo "rules: exact initial list"
want='**/.gitignore
**/CLAUDE.md
.claude/
.github/
docs/adr/
docs/constitution.md
docs/workflow.md
scripts/
src/mealplanner/config.py'
got=$(grep -Ev '^[[:space:]]*(#|$)' "$RULES" | tr -d '\r' | LC_ALL=C sort)
assert_eq "$got" "$(printf '%s\n' "$want" | LC_ALL=C sort)" "risk-paths holds exactly the ADR 0013 list"

# co_patterns FILE: the path pattern of each non-comment CODEOWNERS line.
co_patterns() { grep -Ev '^[[:space:]]*(#|$)' "$1" | awk '{print $1}'; }
# co_violations FILE: patterns that are neither root-anchored nor a **/ rule.
co_violations() { co_patterns "$1" | grep -Ev '^(/|\*\*/)' || true; }

echo "codeowners: drift"
rules_list=$(grep -Ev '^[[:space:]]*(#|$)' "$RULES" | tr -d '\r' | LC_ALL=C sort)
co_list=$(co_patterns "$CO" | sed 's#^/##' | LC_ALL=C sort)
assert_eq "$co_list" "$rules_list" "CODEOWNERS paths equal the rules list (leading / stripped)"
bad_owner=$(grep -Ev '^[[:space:]]*(#|$)' "$CO" | awk '$2 != "@cooper3330" || NF != 2')
assert_eq "$bad_owner" "" "every CODEOWNERS line is owned by @cooper3330"
assert_eq "$(co_violations "$CO")" "" "every pattern is root-anchored or **/"
if [ -n "$(co_violations "$FIX/codeowners-bare.txt")" ]; then ok "bare scripts/ fixture is a violation"; else bad "bare scripts/ fixture is a violation"; fi

finish
