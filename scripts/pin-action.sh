#!/usr/bin/env bash
# Resolves a GitHub Action tag to the commit SHA that workflows must be pinned to, using only
# read-only API calls. Prints one line, ready to paste after `uses: `:
#   actions/checkout@<40-hex sha> # v4
#
# Usage: scripts/pin-action.sh owner/repo [tag]
#   tag defaults to the repository's latest release.
#
# Why a script: the agents run headless with a narrow command allowlist and may not query other
# repositories with `gh api` freely. This one fixed lookup is allowed instead.
# Requires: gh, jq. Works with macOS (BSD) tools and bash 3.2.
set -euo pipefail

usage() { echo "usage: scripts/pin-action.sh owner/repo [tag]" >&2; exit 2; }
for cmd in gh jq; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "missing required command: $cmd" >&2; exit 1; }
done

[ $# -ge 1 ] && [ $# -le 2 ] || usage
slug="$1"
tag="${2:-}"
# Arguments end up in API paths: allow only the characters real owners, repos and tags use.
valid_tag() { grep -Eq '^[A-Za-z0-9_.-]+$' <<<"$1" && [[ "$1" != *..* ]]; }
case "$slug" in
  */*/*|*..*) echo "invalid repository: $slug" >&2; usage ;;
esac
grep -Eq '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$' <<<"$slug" || { echo "invalid repository: $slug" >&2; usage; }
if [ -n "$tag" ]; then
  valid_tag "$tag" || { echo "invalid tag: $tag" >&2; usage; }
else
  # On an API error gh can still print the error body on stdout, so check what came back too.
  if ! tag=$(gh api "repos/$slug/releases/latest" --jq '.tag_name // empty' 2>/dev/null); then tag=""; fi
  [ -n "$tag" ] || { echo "$slug has no latest release; pass a tag" >&2; exit 1; }
  valid_tag "$tag" || { echo "latest release of $slug has an unusable tag name; pass a tag" >&2; exit 1; }
fi

# `gh api` prints "<type> <sha>". An annotated tag points at a tag object, which points at the
# commit; follow it (a few hops at most).
ref=$(gh api "repos/$slug/git/ref/tags/$tag" --jq '"\(.object.type) \(.object.sha)"' 2>/dev/null) \
  || { echo "could not resolve tag $tag of $slug" >&2; exit 1; }
hops=0
while [ "${ref%% *}" = "tag" ] && [ "$hops" -lt 3 ]; do
  ref=$(gh api "repos/$slug/git/tags/${ref#* }" --jq '"\(.object.type) \(.object.sha)"' 2>/dev/null) \
    || { echo "could not resolve tag $tag of $slug" >&2; exit 1; }
  hops=$((hops + 1))
done

type="${ref%% *}"
sha="${ref#* }"
[ "$type" = "commit" ] || { echo "tag $tag of $slug points at a $type, not a commit" >&2; exit 1; }
grep -Eq '^[0-9a-f]{40}$' <<<"$sha" || { echo "tag $tag of $slug did not resolve to a 40-character commit SHA" >&2; exit 1; }
echo "$slug@$sha # $tag"
