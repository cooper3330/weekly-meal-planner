#!/usr/bin/env bash
# Test helpers: run the project's shell scripts offline against stubbed gh, claude and date.
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
new_sandbox() {
  T="$(mktemp -d)"
  STUB_DIR="$T"
  export STUB_DIR
  mkdir -p "$T/bin"
  : > "$T/gh.log"
  : > "$T/claude.calls"
  echo '{"items":[]}' > "$T/items.json"
  echo '{"projects":[],"totalCount":0}' > "$T/projects.json"
  echo '{"fields":[{"id":"PVTF_title","name":"Title","type":"ProjectV2Field"},{"id":"PVTSSF_status","name":"Status","type":"ProjectV2SingleSelectField","options":[{"id":"o_todo","name":"Todo"},{"id":"o_prog","name":"In Progress"},{"id":"o_done","name":"Done"}]}],"totalCount":2}' > "$T/fields.json"
  echo '[]' > "$T/milestones.json"
  echo '{"id":"PVT_abc","number":1,"title":"Weekly Meal Planner"}' > "$T/project.json"

  cat > "$T/bin/gh" <<'STUB'
#!/usr/bin/env bash
echo "gh $*" >> "$STUB_DIR/gh.log"
args=("$@"); jqexpr=""; has_f=""
for ((i = 0; i < ${#args[@]}; i++)); do
  case "${args[$i]}" in
    -q|--jq) jqexpr="${args[$((i + 1))]}" ;;
    -f) has_f=1 ;;
  esac
done
emit() { if [ -n "$jqexpr" ]; then jq -r "$jqexpr" < "$1"; else cat "$1"; fi; }
case "$1" in
  api) if [ -n "$has_f" ]; then exit 0; else emit "$STUB_DIR/milestones.json"; fi; exit 0 ;;
esac
case "$1 $2" in
  "repo view")           echo "${STUB_REPO:-cooper3330/weekly-meal-planner}" ;;
  "project item-list")   emit "$STUB_DIR/items.json" ;;
  "project list")        emit "$STUB_DIR/projects.json" ;;
  "project view")        emit "$STUB_DIR/project.json" ;;
  "project field-list")  emit "$STUB_DIR/fields.json" ;;
  "project create")      echo '{"number":7}' > "$STUB_DIR/created.json"; emit "$STUB_DIR/created.json" ;;
  "project link")        if [ -n "${STUB_LINK_FAIL:-}" ]; then echo "has different owner from '@me'" >&2; exit 1; fi ;;
  *) ;;
esac
exit 0
STUB

  cat > "$T/bin/claude" <<'STUB'
#!/usr/bin/env bash
echo "called" >> "$STUB_DIR/claude.calls"
printf '%s\n' "$@" > "$STUB_DIR/claude.args"
STUB

  cat > "$T/bin/date" <<'STUB'
#!/usr/bin/env bash
for a in "$@"; do
  case "$a" in -I*) echo "date: invalid argument '${a#-I}' for -I" >&2; exit 1 ;; esac
done
exec /bin/date "$@"
STUB
  chmod +x "$T/bin/gh" "$T/bin/claude" "$T/bin/date"
  PATH="$T/bin:$PATH"
}

finish() { echo; echo "  passed=$PASS failed=$FAIL"; [ "$FAIL" -eq 0 ]; }
