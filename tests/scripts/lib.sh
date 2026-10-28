#!/usr/bin/env bash
# Test helpers: run the project's shell scripts offline against stubbed gh, git, claude and date.
# The date stub rejects GNU-only -I flags the way macOS (BSD) date does.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PASS=0
FAIL=0

ok()  { PASS=$((PASS + 1)); echo "  ok    $1"; }
bad() { FAIL=$((FAIL + 1)); echo "  FAIL  $1"; }

assert_grep()    { if grep -Eq -- "$2" "$1"; then ok "$3"; else bad "$3"; fi; }
assert_no_grep() { if grep -Eq -- "$2" "$1"; then bad "$3"; else ok "$3"; fi; }
assert_count()   { # file regex expected message
  local n; n=$(grep -Ec -- "$2" "$1" || true)
  if [ "$n" -eq "$3" ]; then ok "$4"; else bad "$4 (expected $3, got $n)"; fi
}
assert_eq()      { if [ "$1" = "$2" ]; then ok "$3"; else bad "$3 (expected '$2', got '$1')"; fi; }

# new_sandbox: creates $T with stub binaries and empty fixtures; sets STUB_DIR and PATH.
#
# Fixtures (all optional, default to empty) that the gh stub serves:
#   items.json projects.json fields.json project.json milestones.json   (project setup / board)
#   prs.json issues.json                                                (gh pr list / gh issue list)
#   issue_comments_<N>.json pr_comments_<N>.json pr_reviews_<N>.json    (REST comment endpoints)
#   release_latest.json ref_<tag>.json gittag_<sha>.json                (scripts/pin-action.sh lookups)
new_sandbox() {
  # Start from a clean environment: GitHub's runners set GITHUB_ACTIONS=true (the coordinator then
  # demands AGENT_GH_TOKEN), and a developer's shell may export tokens or overrides. Tests that
  # need one of these set it on the command they run.
  unset GITHUB_ACTIONS AGENT_GH_TOKEN PROJECT_TOKEN GH_TOKEN GITHUB_TOKEN \
        AGENT_GIT_NAME AGENT_GIT_EMAIL GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL \
        EXTRA_ALLOWED_TOOLS CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS PROJECT_NUMBER OWNER REPO INTERVAL MAX_PARALLEL
  T="$(mktemp -d)"
  STUB_DIR="$T"
  export STUB_DIR
  mkdir -p "$T/bin"
  : > "$T/gh.log"
  : > "$T/git.log"
  : > "$T/claude.calls"
  : > "$T/claude.all"
  : > "$T/claude.cwds"
  : > "$T/claude.bgwait"
  : > "$T/gh.env"
  : > "$T/claude.env"
  echo '{"items":[]}' > "$T/items.json"
  echo '{"projects":[],"totalCount":0}' > "$T/projects.json"
  echo '{"fields":[{"id":"PVTF_title","name":"Title","type":"ProjectV2Field"},{"id":"PVTSSF_status","name":"Status","type":"ProjectV2SingleSelectField","options":[{"id":"o_todo","name":"Todo"},{"id":"o_prog","name":"In Progress"},{"id":"o_done","name":"Done"}]}],"totalCount":2}' > "$T/fields.json"
  echo '[]' > "$T/milestones.json"
  echo '{"id":"PVT_abc","number":1,"title":"Weekly Meal Planner"}' > "$T/project.json"
  echo '[]' > "$T/prs.json"
  echo '[]' > "$T/issues.json"

  cat > "$T/bin/gh" <<'STUB'
#!/usr/bin/env bash
echo "gh $*" >> "$STUB_DIR/gh.log"
echo "$1 $2 GH_TOKEN=${GH_TOKEN-UNSET}" >> "$STUB_DIR/gh.env"   # which token each call ran with
args=("$@"); jqexpr=""; has_f=""
for ((i = 0; i < ${#args[@]}; i++)); do
  case "${args[$i]}" in
    -q|--jq) jqexpr="${args[$((i + 1))]}" ;;
    -f|-F) has_f=1 ;;
    -X) [ "${args[$((i + 1))]}" != "GET" ] && has_f=1 ;;
  esac
done
# emit FILE: print the fixture (or [] if it does not exist), through --jq/-q if given.
emit() {
  local f="$1"
  if [ ! -f "$f" ]; then f="$STUB_DIR/empty.json"; echo '[]' > "$f"; fi
  if [ -n "$jqexpr" ]; then jq -r "$jqexpr" < "$f"; else cat "$f"; fi
}
if [ "$1" = "api" ]; then
  if [ -n "$has_f" ]; then exit 0; fi          # a write (POST/PATCH): just logged
  path="${2%%\?*}"
  case "$path" in
    repos/*/milestones)        emit "$STUB_DIR/milestones.json" ;;
    repos/*/issues/*/comments) n="${path#repos/*/issues/}"; emit "$STUB_DIR/issue_comments_${n%%/*}.json" ;;
    repos/*/pulls/*/comments)  n="${path#repos/*/pulls/}";  emit "$STUB_DIR/pr_comments_${n%%/*}.json" ;;
    repos/*/pulls/*/reviews)   n="${path#repos/*/pulls/}";  emit "$STUB_DIR/pr_reviews_${n%%/*}.json" ;;
    repos/*/releases/latest)   emit "$STUB_DIR/release_latest.json" ;;
    repos/*/git/ref/tags/*)    t="${path#repos/*/git/ref/tags/}"; emit "$STUB_DIR/ref_${t//\//_}.json" ;;
    repos/*/git/tags/*)        t="${path#repos/*/git/tags/}"; emit "$STUB_DIR/gittag_${t}.json" ;;
    repos/*/issues/*)          # a single issue or PR: state (and pull_request.merged_at for PRs); default open
      n="${path#repos/*/issues/}"; f="$STUB_DIR/issue_state_${n%%/*}.json"
      [ -f "$f" ] || echo '{"state":"open"}' > "$f"
      emit "$f" ;;
    *) echo '[]' ;;
  esac
  exit 0
fi
case "$1 $2" in
  "repo view")           echo "${STUB_REPO:-cooper3330/weekly-meal-planner}" ;;
  "project item-list")   emit "$STUB_DIR/items.json" ;;
  "project list")        emit "$STUB_DIR/projects.json" ;;
  "project view")        emit "$STUB_DIR/project.json" ;;
  "project field-list")  emit "$STUB_DIR/fields.json" ;;
  "project item-add")    echo "${STUB_ITEM_ID:-PVTI_added}" ;;
  "project create")     echo '{"number":7}' > "$STUB_DIR/created.json"; emit "$STUB_DIR/created.json" ;;
  "project link")        if [ -n "${STUB_LINK_FAIL:-}" ]; then echo "has different owner from '@me'" >&2; exit 1; fi ;;
  "pr list")             emit "$STUB_DIR/prs.json" ;;
  "issue list")          emit "$STUB_DIR/issues.json" ;;
  "run list")            emit "$STUB_DIR/run_list.json" ;;   # [{workflowName, conclusion, status, createdAt}]
  "issue view")          f="$STUB_DIR/issue_$3.json"; [ -f "$f" ] || echo '{"body":""}' > "$f"; emit "$f" ;;
  *) ;;                  # label create, pr comment, issue comment, ...: only logged
esac
exit 0
STUB

  cat > "$T/bin/git" <<'STUB'
#!/usr/bin/env bash
# Records every call. `git worktree add` creates the target directory so callers can cd into it.
echo "git $*" >> "$STUB_DIR/git.log"
# STUB_WORKTREE_FAIL / STUB_MERGE_FAIL make those operations fail (merge --abort always works).
case " $* " in
  *" worktree add "*) [ -n "${STUB_WORKTREE_FAIL:-}" ] && { echo "fatal: worktree add failed" >&2; exit 128; } ;;
  *" merge --abort "*) exit 0 ;;
  *" merge "*) [ -n "${STUB_MERGE_FAIL:-}" ] && { echo "CONFLICT (content): merge conflict" >&2; exit 1; } ;;
esac
# STUB_GIT_DIRTY / STUB_GIT_BRANCH / STUB_GIT_UNPUSHED describe the worktree's state;
# STUB_PUSH_FAIL makes pushes fail.
case " $* " in
  *" status --porcelain"*)          [ -n "${STUB_GIT_DIRTY:-}" ] && echo " M tests/scripts/test_workflows.sh" ;;
  *" rev-parse --abbrev-ref HEAD"*) echo "${STUB_GIT_BRANCH:-HEAD}" ;;
  *" rev-list --count"*)            echo "${STUB_GIT_UNPUSHED:-0}" ;;
  *" push "*)                       [ -n "${STUB_PUSH_FAIL:-}" ] && { echo "! [rejected]" >&2; exit 1; } ;;
esac
if [ "${1:-}" = "worktree" ] && [ "${2:-}" = "add" ]; then
  args=("$@"); n=${#args[@]}
  mkdir -p "${args[$((n - 2))]}"
fi
exit 0
STUB

  cat > "$T/bin/claude" <<'STUB'
#!/usr/bin/env bash
echo "called" >> "$STUB_DIR/claude.calls"
pwd >> "$STUB_DIR/claude.cwds"
echo "${CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS-UNSET}" >> "$STUB_DIR/claude.bgwait"
echo "GH_TOKEN=${GH_TOKEN-UNSET} PROJECT_TOKEN=${PROJECT_TOKEN-UNSET} GIT_AUTHOR_NAME=${GIT_AUTHOR_NAME-UNSET} GIT_COMMITTER_NAME=${GIT_COMMITTER_NAME-UNSET} GIT_AUTHOR_EMAIL=${GIT_AUTHOR_EMAIL-UNSET} GIT_COMMITTER_EMAIL=${GIT_COMMITTER_EMAIL-UNSET}" >> "$STUB_DIR/claude.env"
printf '%s\n' "$@" > "$STUB_DIR/claude.args"
{ echo "---CALL---"; printf '%s\n' "$@"; } >> "$STUB_DIR/claude.all"
# claude.out is what the run prints (for example --output-format json); STUB_CLAUDE_FAIL fails it.
[ -f "$STUB_DIR/claude.out" ] && cat "$STUB_DIR/claude.out"
[ -n "${STUB_CLAUDE_FAIL:-}" ] && exit 1
exit 0
STUB

  cat > "$T/bin/date" <<'STUB'
#!/usr/bin/env bash
for a in "$@"; do
  case "$a" in -I*) echo "date: invalid argument '${a#-I}' for -I" >&2; exit 1 ;; esac
done
exec /bin/date "$@"
STUB
  chmod +x "$T/bin/gh" "$T/bin/git" "$T/bin/claude" "$T/bin/date"
  PATH="$T/bin:$PATH"
}

finish() { echo; echo "  passed=$PASS failed=$FAIL"; [ "$FAIL" -eq 0 ]; }
