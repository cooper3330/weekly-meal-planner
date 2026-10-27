#!/usr/bin/env bash
# Tests for scripts/pin-action.sh (offline, stubbed gh): turn a GitHub Action tag into the commit SHA
# that workflows must be pinned to, using only read-only API calls.
. "$(dirname "$0")/lib.sh"
SCRIPT="$ROOT/scripts/pin-action.sh"
SHA1=1111111111111111111111111111111111111111
SHA2=2222222222222222222222222222222222222222
TAGSHA=3333333333333333333333333333333333333333

run() { bash "$SCRIPT" "$@" > "$T/out" 2> "$T/err"; rc=$?; }

echo "pin-action: a lightweight tag resolves to its commit"
new_sandbox
echo "{\"object\":{\"type\":\"commit\",\"sha\":\"$SHA1\"}}" > "$T/ref_v6.1.0.json"
run astral-sh/setup-uv v6.1.0
assert_eq "$rc" "0" "exits 0"
assert_eq "$(cat "$T/out")" "astral-sh/setup-uv@$SHA1 # v6.1.0" "prints a line ready to paste after 'uses: '"
assert_no_grep "$T/gh.log" ' -X | --method |-f | -F ' "makes read-only calls only"

echo "pin-action: an annotated tag is followed to the commit it points at"
new_sandbox
echo "{\"object\":{\"type\":\"tag\",\"sha\":\"$TAGSHA\"}}" > "$T/ref_v4.json"
echo "{\"object\":{\"type\":\"commit\",\"sha\":\"$SHA2\"}}" > "$T/gittag_$TAGSHA.json"
run actions/checkout v4
assert_eq "$rc" "0" "exits 0"
assert_eq "$(cat "$T/out")" "actions/checkout@$SHA2 # v4" "prints the commit SHA, not the tag object SHA"

echo "pin-action: without a tag it uses the latest release"
new_sandbox
echo '{"tag_name":"v7.0.1"}' > "$T/release_latest.json"
echo "{\"object\":{\"type\":\"commit\",\"sha\":\"$SHA1\"}}" > "$T/ref_v7.0.1.json"
run astral-sh/setup-uv
assert_eq "$rc" "0" "exits 0"
assert_eq "$(cat "$T/out")" "astral-sh/setup-uv@$SHA1 # v7.0.1" "resolves the latest release tag"

echo "pin-action: failures are loud and print nothing on stdout"
new_sandbox
run astral-sh/setup-uv v9.9.9
if [ "$rc" -ne 0 ]; then ok "an unknown tag exits non-zero"; else bad "an unknown tag exits non-zero"; fi
assert_eq "$(cat "$T/out")" "" "and prints nothing on stdout"
assert_grep "$T/err" 'v9.9.9' "and names the tag it could not resolve"
new_sandbox
echo '{"object":{"type":"commit","sha":"abc123"}}' > "$T/ref_v1.json"
run o/r v1
if [ "$rc" -ne 0 ]; then ok "a SHA that is not 40 hex characters is rejected"; else bad "a SHA that is not 40 hex characters is rejected"; fi
new_sandbox
echo "{\"object\":{\"type\":\"tree\",\"sha\":\"$SHA1\"}}" > "$T/ref_v1.json"
run o/r v1
if [ "$rc" -ne 0 ]; then ok "a tag that points at something other than a commit is rejected"; else bad "a tag that points at something other than a commit is rejected"; fi
new_sandbox
echo '{}' > "$T/release_latest.json"
run astral-sh/setup-uv
if [ "$rc" -ne 0 ]; then ok "a repo with no release and no tag argument fails"; else bad "a repo with no release and no tag argument fails"; fi

new_sandbox
echo '{"tag_name":"v1;rm -rf"}' > "$T/release_latest.json"
run astral-sh/setup-uv
if [ "$rc" -ne 0 ]; then ok "a latest-release tag with unsafe characters is rejected"; else bad "a latest-release tag with unsafe characters is rejected"; fi
assert_count "$T/gh.log" 'git/ref/tags' 0 "and never used in a ref lookup"

echo "pin-action: bad arguments are rejected before any API call"
for args in "" "astral-sh" "a/b/c" "../x" "a/b;rm" "o/r v1;rm" "o/r ../../x"; do
  new_sandbox
  # shellcheck disable=SC2086
  run $args
  if [ "$rc" -ne 0 ]; then ok "rejects: '$args'"; else bad "rejects: '$args'"; fi
  assert_count "$T/gh.log" '^gh api' 0 "no API call for: '$args'"
done

finish
